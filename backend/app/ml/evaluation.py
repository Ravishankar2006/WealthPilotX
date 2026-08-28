"""Evaluation metrics (PRD §18).

§18 also sets a discipline, not just a metric list: the source report quoted
R² = 0.95 and 94.8% risk accuracy as example values, and those are to be treated as
targets until reproduced on this project's own data. Everything here therefore
reports what was measured, including when the answer is unflattering — a 20-day
equity return is mostly noise, and an honest near-zero R² is the expected result,
not a bug to tune away.
"""

from typing import Any

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    mean_absolute_error,
    precision_score,
    r2_score,
    recall_score,
)


def regression_metrics(y_true: Any, y_pred: Any) -> dict[str, float]:
    """MAE, RMSE and R² (§18, market prediction)."""
    true = np.asarray(y_true, dtype=float)
    pred = np.asarray(y_pred, dtype=float)

    mae = float(mean_absolute_error(true, pred))
    rmse = float(np.sqrt(np.mean((true - pred) ** 2)))
    # R² is undefined for a single sample and unstable for a handful; report NaN
    # rather than a number that looks meaningful.
    r2 = float(r2_score(true, pred)) if true.size > 1 else float("nan")

    return {"mae": mae, "rmse": rmse, "r2": r2, "n_samples": int(true.size)}


def classification_metrics(y_true: Any, y_pred: Any) -> dict[str, Any]:
    """Accuracy, precision, recall and F1 (§18, risk classification).

    Macro-averaged: the rubric's thresholds do not produce three equal classes, and a
    weighted average would let the majority class hide poor performance on the
    others. The per-class breakdown is reported alongside for the same reason.
    """
    labels = sorted(set(np.asarray(y_true).tolist()) | set(np.asarray(y_pred).tolist()))

    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "precision_macro": float(precision_score(y_true, y_pred, average="macro", zero_division=0)),
        "recall_macro": float(recall_score(y_true, y_pred, average="macro", zero_division=0)),
        "f1_macro": float(f1_score(y_true, y_pred, average="macro", zero_division=0)),
        "per_class": {
            label: {
                "precision": float(
                    precision_score(
                        y_true, y_pred, labels=[label], average="micro", zero_division=0
                    )
                ),
                "recall": float(
                    recall_score(y_true, y_pred, labels=[label], average="micro", zero_division=0)
                ),
            }
            for label in labels
        },
        "n_samples": int(np.asarray(y_true).size),
    }


def naive_baseline_prediction(y_train: Any, n: int) -> np.ndarray:
    """The baseline a first market-prediction model must beat (§10.5).

    The training mean, repeated. Unexciting by design: it is what you get for
    predicting nothing at all, and a model that cannot beat it has learned nothing
    worth serving.
    """
    return np.full(n, float(np.mean(np.asarray(y_train, dtype=float))))


def majority_class_prediction(y_train: Any, n: int) -> np.ndarray:
    """The classification counterpart: always answer the most common class."""
    values, counts = np.unique(np.asarray(y_train), return_counts=True)
    return np.full(n, values[int(np.argmax(counts))])
