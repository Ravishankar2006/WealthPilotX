"""Drift monitoring for the market-prediction model (§10.5).

§10.5 asks for two things: "track the distribution of key input features and the
rolling prediction error; alert if either shifts beyond a defined threshold."

**The thresholds were written down before any measurement was taken.** They are the
conventional PSI bands and a 1.5× error multiple, recorded in
`Docs/PLAN/PHASE-6-HARDENING.md` §2.2 at planning time. A drift monitor whose
thresholds are set after seeing the first run's numbers reports that everything is
fine, permanently, by construction.

**What an alert does: nothing automatic.** It writes a row and logs at WARNING.
§10.5 requires promotion to be "reviewed manually before promotion in the MVP", and
a monitor that retrains or demotes on its own is that review removed. The operator
decides; this tells them there is something to decide.

**Where the reference distribution comes from.** Not the artifact — artifacts
deliberately carry fitted parameters only, never training rows (§11.2). The registry
row records the training date range, so the reference window is recomputed from
stored market data over exactly those dates. That is also the more honest source:
it reflects what the data says now about the period the model was fitted on.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger, safe_extra
from app.ml import artifacts, registry
from app.ml.features import market as market_features
from app.ml.prediction.model import PredictionArtifact
from app.models.asset import Asset
from app.models.enums import DriftCheck, DriftVerdict
from app.models.model_monitoring import ModelMonitoring
from app.models.model_record import PREDICTION_MODEL, ModelRecord
from app.models.prediction import Prediction

logger = get_logger(__name__)

# The conventional Population Stability Index bands. Below 0.10 a shift is not
# meaningfully different from sampling noise; above 0.25 the input distribution has
# moved enough that a model fitted on the old one is extrapolating.
PSI_WATCH = 0.10
PSI_ALERT = 0.25

# Rolling RMSE relative to the RMSE recorded at training time. 1.5× is generous on
# purpose: this target is close to noise (the market predictor did not beat its
# naive baseline), so a tight multiple would alert on ordinary month-to-month
# variation and the alerts would be ignored within a fortnight.
ERROR_WATCH_MULTIPLE = 1.2
ERROR_ALERT_MULTIPLE = 1.5

# Deciles. Ten bins over a few thousand rows keeps roughly 10% expected mass per
# bin, which is where the PSI bands above were calibrated.
PSI_BINS = 10

# Below this, a PSI is dominated by which side of a boundary a handful of points
# landed on, and reporting it as a measurement would be false precision.
MIN_SAMPLES = 100

# Distinct values required in *each* window for PSI to mean anything.
#
# This one was learned from the first real run, and it matters. The macro features
# are monthly series forward-filled onto trading days: `inflation` has 11 distinct
# values across a two-year training window and **2** across the recent 90 days. All
# of the recent mass therefore lands in one or two reference deciles, and PSI comes
# out above 10 — forty times the alert threshold — whether or not anything drifted.
# The monitor's first run produced three ALERTs, all three artefacts, and it would
# have produced the same three every night in perpetuity. Alerts that always fire
# are alerts nobody reads.
#
# You cannot populate ten deciles from fewer than ten distinct values, so that is
# the floor. Below it the check reports INSUFFICIENT_DATA and records both windows'
# ranges, which is the comparison that *is* meaningful for a slow-moving level
# series — and leaves the reading to a person rather than inventing a statistic.
MIN_DISTINCT_VALUES = PSI_BINS

# How much recent history counts as "now" for the stability check.
RECENT_WINDOW_DAYS = 90

# Laplace-style floor so an empty bin does not send PSI to infinity. Small enough
# not to mask a real shift, large enough that one empty decile is not an alert on
# its own.
EPSILON = 1e-6


@dataclass(frozen=True, slots=True)
class Observation:
    """One measurement, before it becomes a row."""

    check: DriftCheck
    subject: str
    value: float | None
    verdict: DriftVerdict
    reference_start: date | None = None
    reference_end: date | None = None
    window_start: date | None = None
    window_end: date | None = None
    details: dict[str, object] | None = None


def population_stability_index(reference: np.ndarray, recent: np.ndarray) -> float:
    """PSI between two samples of one feature, binned on the reference's deciles.

    Binning on the *reference* rather than on the pooled sample is the point: the
    question is how much of the recent data now falls outside where the model was
    fitted. Pooled bins would move with the drift and understate it.
    """
    reference = reference[np.isfinite(reference)]
    recent = recent[np.isfinite(recent)]
    if len(reference) < MIN_SAMPLES or len(recent) < MIN_SAMPLES:
        return float("nan")

    if (
        len(np.unique(reference)) < MIN_DISTINCT_VALUES
        or len(np.unique(recent)) < MIN_DISTINCT_VALUES
    ):
        return float("nan")

    quantiles = np.quantile(reference, np.linspace(0, 1, PSI_BINS + 1))
    # A feature with heavy ties (a rounded macro series, say) produces duplicate
    # edges, which `np.histogram` rejects. Collapsing to the distinct edges keeps
    # the comparison valid over however many real bins exist.
    edges = np.unique(quantiles)
    if len(edges) < 3:
        return float("nan")
    edges[0], edges[-1] = -np.inf, np.inf

    reference_share = np.histogram(reference, bins=edges)[0] / len(reference)
    recent_share = np.histogram(recent, bins=edges)[0] / len(recent)

    reference_share = np.maximum(reference_share, EPSILON)
    recent_share = np.maximum(recent_share, EPSILON)

    return float(np.sum((recent_share - reference_share) * np.log(recent_share / reference_share)))


def psi_verdict(value: float) -> DriftVerdict:
    if not np.isfinite(value):
        return DriftVerdict.INSUFFICIENT_DATA
    if value >= PSI_ALERT:
        return DriftVerdict.ALERT
    if value >= PSI_WATCH:
        return DriftVerdict.WATCH
    return DriftVerdict.STABLE


def error_verdict(ratio: float) -> DriftVerdict:
    if not np.isfinite(ratio):
        return DriftVerdict.INSUFFICIENT_DATA
    if ratio >= ERROR_ALERT_MULTIPLE:
        return DriftVerdict.ALERT
    if ratio >= ERROR_WATCH_MULTIPLE:
        return DriftVerdict.WATCH
    return DriftVerdict.STABLE


def _pooled_features(db: Session, columns: tuple[str, ...], start: date, end: date) -> pd.DataFrame:
    """Features for every tracked asset over a date window, stacked.

    Pooled across assets because the model is: it is fitted on one matrix spanning
    the universe, so per-asset stability would answer a question the model does not
    ask.
    """
    frames: list[pd.DataFrame] = []
    for symbol in db.scalars(select(Asset.symbol).where(Asset.is_active.is_(True))):
        matrix = market_features.build_training_matrix(db, symbol)
        if matrix.frame.empty:
            continue
        window = matrix.frame.loc[
            (matrix.frame.index >= pd.Timestamp(start)) & (matrix.frame.index <= pd.Timestamp(end))
        ]
        available = [column for column in columns if column in window.columns]
        if not window.empty and available:
            frames.append(window[available])

    return pd.concat(frames) if frames else pd.DataFrame(columns=list(columns))


def _stability_details(
    reference: pd.Series, recent: pd.Series, *, verdict: DriftVerdict
) -> dict[str, object]:
    """Row counts, thresholds, and — when the check could not run — why, plus the
    comparison that is still meaningful.

    The ranges are always recorded, not only on failure. A slow-moving level series
    that PSI cannot score is exactly the case where an operator needs to see that
    inflation moved from [326.8, 329.2] to [330.5, 331.1] with their own eyes.
    """
    reference = reference.dropna()
    recent = recent.dropna()

    details: dict[str, object] = {
        "reference_rows": int(len(reference)),
        "recent_rows": int(len(recent)),
        "reference_distinct": int(reference.nunique()),
        "recent_distinct": int(recent.nunique()),
        "reference_range": (
            [round(float(reference.min()), 6), round(float(reference.max()), 6)]
            if len(reference)
            else None
        ),
        "recent_range": (
            [round(float(recent.min()), 6), round(float(recent.max()), 6)] if len(recent) else None
        ),
        "watch_threshold": PSI_WATCH,
        "alert_threshold": PSI_ALERT,
    }

    if verdict is DriftVerdict.INSUFFICIENT_DATA:
        if len(reference) < MIN_SAMPLES or len(recent) < MIN_SAMPLES:
            details["reason"] = f"fewer than {MIN_SAMPLES} usable rows in a window"
        else:
            details["reason"] = (
                f"fewer than {MIN_DISTINCT_VALUES} distinct values in a window "
                f"({details['reference_distinct']} reference, {details['recent_distinct']} "
                "recent) — too few to compare as distributions. Compare the ranges above."
            )

    return details


def feature_stability(
    db: Session, record: ModelRecord, artifact: PredictionArtifact, *, as_of: date | None = None
) -> list[Observation]:
    """PSI per fitted feature, training window vs the last `RECENT_WINDOW_DAYS`."""
    today = as_of or date.today()
    window_start = today - timedelta(days=RECENT_WINDOW_DAYS)

    if record.training_start is None or record.training_end is None:
        return [
            Observation(
                check=DriftCheck.FEATURE_STABILITY,
                subject=column,
                value=None,
                verdict=DriftVerdict.INSUFFICIENT_DATA,
                details={"reason": "the model record has no training date range"},
            )
            for column in artifact.feature_columns
        ]

    reference = _pooled_features(
        db, artifact.feature_columns, record.training_start, record.training_end
    )
    recent = _pooled_features(db, artifact.feature_columns, window_start, today)

    observations: list[Observation] = []
    for column in artifact.feature_columns:
        if column not in reference.columns or column not in recent.columns:
            observations.append(
                Observation(
                    check=DriftCheck.FEATURE_STABILITY,
                    subject=column,
                    value=None,
                    verdict=DriftVerdict.INSUFFICIENT_DATA,
                    reference_start=record.training_start,
                    reference_end=record.training_end,
                    window_start=window_start,
                    window_end=today,
                    details={"reason": "the feature is absent from one of the windows"},
                )
            )
            continue

        value = population_stability_index(
            reference[column].to_numpy(dtype=float), recent[column].to_numpy(dtype=float)
        )
        verdict = psi_verdict(value)
        observations.append(
            Observation(
                check=DriftCheck.FEATURE_STABILITY,
                subject=column,
                value=None if not np.isfinite(value) else round(value, 6),
                verdict=verdict,
                reference_start=record.training_start,
                reference_end=record.training_end,
                window_start=window_start,
                window_end=today,
                details=_stability_details(reference[column], recent[column], verdict=verdict),
            )
        )
    return observations


def _realised_returns(db: Session, record: ModelRecord, *, as_of: date) -> pd.DataFrame:
    """Stored predictions whose horizon has elapsed, joined to what actually happened.

    A prediction made ten days ago on a twenty-day horizon has no realised outcome
    yet, and scoring it against the price today would compare a one-month forecast
    against a ten-day move. Those rows are excluded rather than partially credited.
    """
    rows = db.execute(
        select(Prediction, Asset.symbol)
        .join(Asset, Asset.id == Prediction.asset_id)
        .where(Prediction.model_version == record.version)
        .order_by(Prediction.prediction_date)
    ).all()

    records: list[dict[str, object]] = []
    price_cache: dict[str, pd.DataFrame] = {}

    for prediction, symbol in rows:
        horizon_end = prediction.prediction_date + timedelta(days=prediction.horizon_days * 7 // 5)
        if horizon_end > as_of:
            continue

        if symbol not in price_cache:
            price_cache[symbol] = market_features.load_prices(db, symbol)
        prices = price_cache[symbol]
        if prices.empty:
            continue

        start_rows = prices.loc[prices.index <= pd.Timestamp(prediction.prediction_date)]
        end_rows = prices.loc[prices.index <= pd.Timestamp(horizon_end)]
        if start_rows.empty or len(end_rows) <= len(start_rows):
            continue

        start_price = float(start_rows["adj_close"].iloc[-1])
        end_price = float(end_rows["adj_close"].iloc[-1])
        if start_price <= 0 or end_price <= 0:
            continue

        records.append(
            {
                "symbol": symbol,
                "date": prediction.prediction_date,
                "predicted": float(prediction.predicted_return),
                # The target is a log return, so the realised value must be one too.
                "realised": float(np.log(end_price / start_price)),
            }
        )

    return pd.DataFrame(records)


def prediction_error(db: Session, record: ModelRecord, *, as_of: date | None = None) -> Observation:
    """Rolling RMSE on realised horizons, against the RMSE recorded at training."""
    today = as_of or date.today()
    training_rmse = (record.metrics or {}).get("rmse")

    def insufficient(reason: str) -> Observation:
        return Observation(
            check=DriftCheck.PREDICTION_ERROR,
            subject=record.name,
            value=None,
            verdict=DriftVerdict.INSUFFICIENT_DATA,
            details={"reason": reason},
        )

    if not isinstance(training_rmse, (int, float)) or training_rmse <= 0:
        return insufficient("the model record carries no training RMSE to compare against")

    realised = _realised_returns(db, record, as_of=today)
    if len(realised) < 2:
        # Two is not a statistic; it is enough to say the pipeline works and to keep
        # the row from claiming a measurement. The verdict stays INSUFFICIENT_DATA.
        return insufficient("no prediction has reached the end of its horizon yet, or too few have")

    errors = realised["predicted"] - realised["realised"]
    rolling_rmse = float(np.sqrt(np.mean(np.square(errors))))
    ratio = rolling_rmse / float(training_rmse)

    return Observation(
        check=DriftCheck.PREDICTION_ERROR,
        subject=record.name,
        value=round(ratio, 6),
        verdict=error_verdict(ratio),
        window_start=realised["date"].min(),
        window_end=realised["date"].max(),
        details={
            "rolling_rmse": round(rolling_rmse, 6),
            "training_rmse": round(float(training_rmse), 6),
            "observations": len(realised),
            "watch_threshold": ERROR_WATCH_MULTIPLE,
            "alert_threshold": ERROR_ALERT_MULTIPLE,
        },
    )


def run(db: Session, *, as_of: date | None = None) -> list[ModelMonitoring]:
    """Run both checks against the production prediction model and store the results.

    Returns an empty list when nothing is promoted — an unmonitored system, but not
    a broken one, and not something to raise about on a scheduler's nightly tick.
    """
    record = registry.production_record(db, PREDICTION_MODEL)
    if record is None:
        logger.info("monitoring_skipped", extra=safe_extra(reason="no production model"))
        return []

    payload = artifacts.load(record.artifact_path, record.artifact_checksum)
    if not isinstance(payload, PredictionArtifact):
        logger.warning(
            "monitoring_skipped",
            extra=safe_extra(reason="production artifact is not a prediction model"),
        )
        return []

    observations = [
        *feature_stability(db, record, payload, as_of=as_of),
        prediction_error(db, record, as_of=as_of),
    ]

    stored = [
        ModelMonitoring(
            model_name=record.name,
            model_version=record.version,
            check=observation.check,
            subject=observation.subject,
            value=observation.value,
            verdict=observation.verdict,
            reference_start=observation.reference_start,
            reference_end=observation.reference_end,
            window_start=observation.window_start,
            window_end=observation.window_end,
            details=observation.details,
        )
        for observation in observations
    ]
    db.add_all(stored)
    db.commit()

    for observation in observations:
        if observation.verdict in (DriftVerdict.ALERT, DriftVerdict.WATCH):
            # WARNING for both: a WATCH is the signal that gives an operator time to
            # act before an ALERT, and logging it at INFO would bury it among the
            # request lines it shares a stream with.
            logger.warning(
                "model_drift_detected",
                extra=safe_extra(
                    model=record.name,
                    model_version=record.version,
                    check=observation.check.value,
                    subject=observation.subject,
                    value=observation.value,
                    verdict=observation.verdict.value,
                ),
            )

    return stored


def latest(db: Session, name: str = PREDICTION_MODEL, limit: int = 50) -> list[ModelMonitoring]:
    return list(
        db.scalars(
            select(ModelMonitoring)
            .where(ModelMonitoring.model_name == name)
            .order_by(ModelMonitoring.created_at.desc())
            .limit(limit)
        )
    )
