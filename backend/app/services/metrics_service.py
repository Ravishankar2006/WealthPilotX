"""In-process metrics (§16.4).

§16.4 asks for four things: request latency, error rate, ingestion job success rate,
and model prediction latency. Three of them are counted here as they happen; the
fourth is queried from `ingestion_runs`, which already records every run.

**Scope, stated plainly because it is easy to over-read a metrics endpoint.** These
counters live in one process's memory. §16.4 also requires a stateless API layer so
instances can sit behind a load balancer — which means a scraper polling through
that balancer gets *one* instance's numbers, and a restart resets them. That is the
correct trade for this milestone: the alternative is a metrics backend, which is
infrastructure this project does not have and cannot honestly claim to operate. The
snapshot says which process it came from and how long it has been counting, so a
reader is not left to assume it is cluster-wide.

**What is deliberately not recorded.** No user identifiers, no request bodies, no
query strings, and no raw paths — only the *route template* (`/market/{symbol}`, not
`/market/AAPL`). Raw paths would put a chosen symbol, and eventually an id, into a
surface designed to be scraped and retained.
"""

import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.enums import IngestionStatus
from app.models.ingestion_run import IngestionRun

# Per-series ring size. 512 samples is enough for a stable p95 and bounds memory at
# a few tens of kilobytes per route no matter how long the process runs.
SAMPLE_WINDOW = 512

# Distinct route templates tracked. FastAPI's own route table is finite, so this is
# a guard against an unrouted path family rather than an expected limit.
MAX_SERIES = 200

# How far back the ingestion success rate looks. Seven days spans a full weekly
# cycle, so a Sunday with no trading data does not read as a failure rate.
INGESTION_WINDOW_DAYS = 7


def _percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile. No interpolation — with a few hundred samples the
    interpolated value implies a precision the sample does not have."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * len(ordered)) - 1))
    return round(ordered[index], 2)


@dataclass
class _Series:
    count: int = 0
    errors: int = 0
    client_errors: int = 0
    samples: deque[float] = field(default_factory=lambda: deque(maxlen=SAMPLE_WINDOW))

    def summary(self) -> dict[str, Any]:
        values = list(self.samples)
        return {
            "count": self.count,
            "errors": self.errors,
            "client_errors": self.client_errors,
            "error_rate": round(self.errors / self.count, 4) if self.count else 0.0,
            "latency_ms": {
                "mean": round(sum(values) / len(values), 2) if values else 0.0,
                "p50": _percentile(values, 0.50),
                "p95": _percentile(values, 0.95),
                "p99": _percentile(values, 0.99),
                "sampled": len(values),
            },
        }


class MetricsRegistry:
    """Thread-safe counters.

    A lock rather than atomics: the operations are a handful of integer increments
    and a deque append per request, and correctness under Starlette's threadpool
    matters more than the nanoseconds. `deque.append` with a `maxlen` is already
    atomic; the counters are not.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._routes: dict[str, _Series] = defaultdict(_Series)
        self._total = _Series()
        self._timers: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=SAMPLE_WINDOW))
        self._started = time.monotonic()

    def record_request(self, route: str, status: int, duration_ms: float) -> None:
        with self._lock:
            series = self._total
            series.count += 1
            series.samples.append(duration_ms)
            if status >= 500:
                series.errors += 1
            elif status >= 400:
                series.client_errors += 1

            if route not in self._routes and len(self._routes) >= MAX_SERIES:
                return
            route_series = self._routes[route]
            route_series.count += 1
            route_series.samples.append(duration_ms)
            if status >= 500:
                route_series.errors += 1
            elif status >= 400:
                route_series.client_errors += 1

    def observe(self, name: str, duration_ms: float) -> None:
        """Record a named duration — model inference, mostly."""
        with self._lock:
            self._timers[name].append(duration_ms)

    def reset(self) -> None:
        """For tests. Counters that carry across tests make assertions order-dependent."""
        with self._lock:
            self._routes.clear()
            self._timers.clear()
            self._total = _Series()
            self._started = time.monotonic()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            routes = {name: series.summary() for name, series in self._routes.items()}
            timers = {
                name: {
                    "count": len(values),
                    "mean_ms": round(sum(values) / len(values), 2) if values else 0.0,
                    "p95_ms": _percentile(list(values), 0.95),
                }
                for name, values in self._timers.items()
            }
            uptime = round(time.monotonic() - self._started, 1)
            total = self._total.summary()

        return {
            "process_id": os.getpid(),
            "uptime_seconds": uptime,
            "requests": total,
            "routes": routes,
            "timers": timers,
        }


metrics = MetricsRegistry()


class timed:  # noqa: N801  (used as a context manager, reads as a verb at the call site)
    """Record how long a block took under `name`.

    Records on the way out whether or not the block raised: a model call that fails
    after four seconds is exactly the latency worth knowing about, and only counting
    successes would make a degrading model look fast.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._start = 0.0

    def __enter__(self) -> "timed":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *_exc: object) -> None:
        metrics.observe(self.name, (time.perf_counter() - self._start) * 1000)


def ingestion_success_rate(db: Session, *, days: int = INGESTION_WINDOW_DAYS) -> dict[str, Any]:
    """§16.4's ingestion job success rate, from the run table rather than memory.

    PARTIAL counts as a failure. FR-04 is explicit that a provider outage must
    surface "rather than silently skipping the day", and a success rate that scores
    a run where half the symbols failed as a success is precisely that silence.
    """
    since = datetime.now(UTC) - timedelta(days=days)
    rows = db.execute(
        select(IngestionRun.job, IngestionRun.status, func.count())
        .where(IngestionRun.started_at >= since)
        .group_by(IngestionRun.job, IngestionRun.status)
    ).all()

    per_job: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "succeeded": 0})
    for job, status, count in rows:
        per_job[job]["total"] += count
        if status is IngestionStatus.SUCCESS:
            per_job[job]["succeeded"] += count

    return {
        "window_days": days,
        "jobs": {
            job: {
                **counts,
                "success_rate": round(counts["succeeded"] / counts["total"], 4)
                if counts["total"]
                else None,
            }
            for job, counts in sorted(per_job.items())
        },
    }


def build_snapshot(db: Session) -> dict[str, Any]:
    return {**metrics.snapshot(), "ingestion": ingestion_success_rate(db)}
