"""ML job implementations (§10.5).

Kept out of `__main__.py` because these import the ML stack, and the CLI's other
commands — `seed-assets`, `ingest-market` — have no business paying that import cost
just to parse arguments.

Training and promotion are separate commands on purpose. §10.5 requires manual
review before promotion in the MVP, and a training script that promotes what it just
built is how an unreviewed model reaches users.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger, safe_extra
from app.ml import backtest, monitoring, registry
from app.ml.prediction import dataset as prediction_dataset
from app.ml.prediction import model as prediction_model
from app.ml.risk import model as risk_model
from app.models.asset import Asset
from app.models.enums import DriftVerdict
from app.models.model_monitoring import ModelMonitoring
from app.models.model_record import PREDICTION_MODEL, RISK_MODEL, ModelRecord
from app.models.prediction import Prediction
from app.services import backtest_service, prediction_service

logger = get_logger("app.jobs.ml")


def _summarise(record: ModelRecord) -> None:
    metrics = record.metrics or {}
    headline = {
        key: value
        for key, value in metrics.items()
        if isinstance(value, (int, float, bool)) and key != "n_samples"
    }
    print(f"{record.name}:{record.version} registered as {record.status}")  # noqa: T201
    for key, value in sorted(headline.items()):
        print(f"  {key}: {value}")  # noqa: T201
    if not metrics.get("beats_baseline", True):
        print("  ⚠ does not beat the naive baseline — promotion will be refused")  # noqa: T201


def train_risk(db: Session) -> ModelRecord:
    """Train the FR-03 classifier on the rubric-labelled synthetic population."""
    settings = get_settings()
    artifact, metrics = risk_model.train(population=settings.risk_training_population)

    record = registry.register(
        db,
        name=RISK_MODEL,
        payload=artifact,
        metrics=metrics,
        # Synthetic data has no calendar range — the population is drawn, not
        # observed, so leaving these null is the honest answer.
        training_start=None,
        training_end=None,
    )
    _summarise(record)
    print(  # noqa: T201
        "  note: metrics measure fidelity to app/ml/risk/rubric.py, not correctness "
        "about real people"
    )
    return record


def train_prediction(db: Session, *, holdout_days: int = 0) -> ModelRecord:
    """Train the FR-08 regressor on stored market history.

    `holdout_days` reserves the most recent period so §19's backtest has genuinely
    out-of-sample data. Zero trains on everything, which maximises the model but
    leaves nothing to evaluate a portfolio against.
    """
    data = prediction_dataset.build_training_data(db, holdout_days=holdout_days)
    artifact, metrics = prediction_model.train(data)

    record = registry.register(
        db,
        name=PREDICTION_MODEL,
        payload=artifact,
        metrics=metrics,
        training_start=data.train_start.date(),
        training_end=data.train_end.date(),
    )
    _summarise(record)
    if holdout_days:
        print(f"  reserved the last {holdout_days} days for out-of-sample backtesting (§19)")  # noqa: T201
    return record


def generate_predictions(db: Session) -> int:
    """Write a prediction row per tracked asset (FR-08).

    Idempotent on `(asset_id, prediction_date, model_version)`, like ingestion — so
    a re-run after a partial failure is safe rather than duplicating rows.
    """
    loaded = registry.resolve_production(db, PREDICTION_MODEL)
    artifact: prediction_model.PredictionArtifact = loaded.payload

    assets = list(db.scalars(select(Asset).where(Asset.is_active.is_(True)).order_by(Asset.symbol)))
    rows: list[dict[str, object]] = []
    skipped: list[str] = []

    for asset in assets:
        prediction = prediction_service.generate_for_asset(db, asset, artifact, loaded.version)
        if prediction is None:
            # Too little history for the 60-day indicators. FR-09 requires this to be
            # reported rather than filled in.
            skipped.append(asset.symbol)
            continue
        rows.append(
            {
                "asset_id": prediction.asset_id,
                "model_version": prediction.model_version,
                "prediction_date": prediction.prediction_date,
                "predicted_return": prediction.predicted_return,
                "trend": prediction.trend,
                "confidence": prediction.confidence,
                "horizon_days": prediction.horizon_days,
                "created_at": datetime.now(UTC),
            }
        )

    written = 0
    if rows:
        statement = insert(Prediction).values(rows)
        statement = statement.on_conflict_do_update(
            constraint="uq_prediction_asset_date_model",
            set_={
                "predicted_return": statement.excluded.predicted_return,
                "trend": statement.excluded.trend,
                "confidence": statement.excluded.confidence,
                "created_at": statement.excluded.created_at,
            },
        )
        # RETURNING rather than rowcount — psycopg reports -1 for a multi-row INSERT,
        # the same trap that made M2's ingestion report negative row counts.
        written = len(db.execute(statement.returning(Prediction.id)).all())
        db.commit()

    logger.info(
        "predictions_generated",
        extra=safe_extra(
            model_version=loaded.version, rows=written, skipped=len(skipped), assets=len(assets)
        ),
    )
    print(  # noqa: T201
        f"predict: {written} predictions from {loaded.record.name}:{loaded.version}"
        + (
            f"; skipped {len(skipped)} with insufficient history: {', '.join(skipped)}"
            if skipped
            else ""
        )
    )
    return written


def promote(db: Session, name: str, version: str, *, force: bool = False) -> ModelRecord:
    record = registry.promote(db, name, version, force=force)
    print(f"promote: {record.name}:{record.version} is now {record.status}")  # noqa: T201
    return record


def list_models(db: Session, name: str | None = None) -> list[ModelRecord]:
    statement = select(ModelRecord).order_by(ModelRecord.name, ModelRecord.created_at)
    if name:
        statement = statement.where(ModelRecord.name == name)
    records = list(db.scalars(statement))

    for record in records:
        metric_name, _ = registry.PROMOTION_METRIC.get(record.name, ("", True))
        value = (record.metrics or {}).get(metric_name)
        rendered = f"{value:.6f}" if isinstance(value, (int, float)) else "—"
        print(  # noqa: T201
            f"{record.name:20} {record.version:6} {str(record.status):11} {metric_name}={rendered}"
        )
    if not records:
        print("no models registered")  # noqa: T201
    return records


def backtest_portfolio(
    db: Session,
    *,
    user_email: str | None = None,
    months: int = backtest_service.DEFAULT_MONTHS,
    cost_bps: float = backtest.DEFAULT_TRANSACTION_COST_BPS,
) -> backtest.BacktestResult | None:
    """§19 — backtest the most recent portfolio against a benchmark.

    The computation lives in `services/backtest_service` because the API serves it
    too, and a backtest that gave different answers depending on whether the CLI or
    the endpoint asked would be worse than no backtest. This wrapper only picks the
    portfolio and prints the result.
    """
    from app.models.portfolio import Portfolio
    from app.models.user import User

    statement = select(Portfolio).order_by(Portfolio.created_at.desc()).limit(1)
    if user_email:
        statement = statement.join(User, User.id == Portfolio.user_id).where(
            User.email == user_email
        )

    portfolio = db.scalar(statement)
    if portfolio is None:
        print("backtest: no portfolio found — generate one first")  # noqa: T201
        return None

    try:
        run = backtest_service.run_for_portfolio(db, portfolio, months=months, cost_bps=cost_bps)
    except backtest_service.BacktestUnavailableError as exc:
        print(f"backtest: {exc}")  # noqa: T201
        return None

    _print_backtest(run.result)
    return run.result


def _print_backtest(result: backtest.BacktestResult) -> None:
    print(f"\nBacktest {result.start} → {result.end}  ({result.rebalances} rebalances)")  # noqa: T201
    print(f"{'metric':<22}{'portfolio':>14}{'benchmark (' + result.benchmark_symbol + ')':>22}")  # noqa: T201
    for label, key in [
        ("total return", "total_return"),
        ("annualised return", "annualised_return"),
        ("volatility", "volatility"),
        ("Sharpe ratio", "sharpe_ratio"),
        ("max drawdown", "max_drawdown"),
    ]:
        mine = result.portfolio.as_dict()[key]
        theirs = result.benchmark.as_dict()[key]
        fmt = "{:>13.2%}" if key != "sharpe_ratio" else "{:>13.3f}"
        print(f"{label:<22}{fmt.format(mine)} {fmt.format(theirs)}")  # noqa: T201

    # §19 requires the assumption to be reported, not merely applied.
    print(  # noqa: T201
        f"\nTransaction costs: {result.transaction_cost_bps:.0f} bps per side on turnover, "
        f"rebalanced every {backtest.REBALANCE_DAYS} trading days. "
        f"Total cost drag: {result.total_costs:.2%}."
    )
    print(  # noqa: T201
        "Past performance does not guarantee future results. This is a historical "
        "simulation, not a record of realised returns."
    )


def evaluate_recommendations(db: Session, k: int = 10) -> dict[str, object] | None:
    """§18's recommendation metrics across every risk-class and goal combination.

    Reported per combination rather than as one headline number: an average over
    combinations would hide a ranker that works for GROWTH and fails for RETIREMENT.
    """
    from app.ml.recommendation import evaluation as rank_eval
    from app.ml.recommendation import scoring as scoring_module
    from app.models.enums import InvestmentGoal, RiskCategory
    from app.services import portfolio_service

    features = portfolio_service._asset_features(db)
    if len(features) < 2:
        print("evaluate-recommendations: not enough assets with price history")  # noqa: T201
        return None

    print(f"\nRecommendation ranking metrics (§18), K={k}, {len(features)} assets")  # noqa: T201
    print(f"{'risk':<8}{'goal':<18}{'P@K':>8}{'R@K':>8}{'NDCG':>8}{'relevant':>10}")  # noqa: T201

    results: dict[str, object] = {}
    for risk in RiskCategory:
        for goal in InvestmentGoal:
            ranked = scoring_module.score_assets(features, risk_category=risk, goal=goal)
            metrics = rank_eval.evaluate_ranking(ranked, risk_category=risk, goal=goal, k=k)
            results[f"{risk}/{goal}"] = metrics.as_dict()
            print(  # noqa: T201
                f"{str(risk):<8}{str(goal):<18}"
                f"{metrics.precision_at_k:>8.3f}{metrics.recall_at_k:>8.3f}"
                f"{metrics.ndcg_at_k:>8.3f}{metrics.relevant_total:>10}"
            )

    # §18's reported-metrics discipline. The caveat travels with the numbers.
    print(  # noqa: T201
        "\nRelevance is rule-derived, not observed: no user has been advised, acted, and "
        "had an outcome recorded. These measure agreement between the ranker and a "
        "second rule in this repository — internal consistency, not correctness. "
        "See Docs/MODELS/portfolio-optimizer.md."
    )
    return results


def monitor(db: Session) -> list[ModelMonitoring]:
    """§10.5's drift check. Prints a table; the alerts are in the log stream."""
    observations = monitoring.run(db)

    if not observations:
        print("monitor: no production prediction model — nothing to monitor")  # noqa: T201
        return observations

    ordering = {
        DriftVerdict.ALERT: 0,
        DriftVerdict.WATCH: 1,
        DriftVerdict.INSUFFICIENT_DATA: 2,
        DriftVerdict.STABLE: 3,
    }
    # Worst first. An operator reading a scheduler's output sees the line that
    # needs them before the thirty that do not.
    for row in sorted(observations, key=lambda r: ordering[r.verdict]):
        value = f"{row.value:.4f}" if row.value is not None else "—"
        print(f"{str(row.verdict):18} {row.subject:26} {value}")  # noqa: T201

    alerts = [r for r in observations if r.verdict is DriftVerdict.ALERT]
    if alerts:
        print(  # noqa: T201
            f"\nmonitor: {len(alerts)} alert(s). §10.5 leaves the response to a human — "
            "review before retraining or promoting anything."
        )
    return observations
