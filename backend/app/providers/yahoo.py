"""Yahoo Finance market data (FR-04 initial source).

**This is the only module in the codebase permitted to import yfinance** (CLAUDE.md,
PRD §7.3). Yahoo's endpoint is unofficial: it can change shape, rate-limit, or vanish
without notice. Confining it here means that when it does, the blast radius is one
file — and that a licensed provider can be substituted without any downstream change.

`yfinance` is imported lazily inside the method rather than at module scope so that
importing the provider registry never drags pandas into a process that only needs the
fixture provider (the API, and the whole test suite).
"""

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.logging import get_logger, safe_extra
from app.providers.base import (
    PriceBar,
    ProviderUnavailableError,
    SymbolNotFoundError,
)

logger = get_logger(__name__)

_REQUIRED = ("Open", "High", "Low", "Close", "Volume")


def _decimal(value: Any) -> Decimal | None:
    """Provider floats → Decimal, via `str` so the shortest repr is preserved.

    `Decimal(0.1)` captures the binary error; `Decimal(str(0.1))` gives `0.1`.
    """
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return None if not result.is_finite() else result


class YahooMarketDataProvider:
    """Adapts `yfinance.Ticker.history` to the §7.3 interface."""

    name = "yahoo"

    def __init__(self, timeout: int = 30) -> None:
        self._timeout = timeout

    def fetch_daily_bars(self, symbol: str, start: date, end: date) -> list[PriceBar]:
        import yfinance  # noqa: PLC0415 — deliberately lazy; see the module docstring

        try:
            frame = yfinance.Ticker(symbol).history(
                start=start.isoformat(),
                # yfinance treats `end` as exclusive; the interface contract says
                # [start, end] inclusive, so add a day rather than lose the last bar.
                end=(end + timedelta(days=1)).isoformat(),
                interval="1d",
                auto_adjust=False,
                actions=False,
                raise_errors=True,
                timeout=self._timeout,
            )
        except Exception as exc:  # noqa: BLE001 — vendor raises an open-ended set
            # yfinance raises bare Exceptions with free-text messages rather than a
            # typed hierarchy, so the message is the only signal available for
            # distinguishing "no such ticker" from "Yahoo is down".
            message = str(exc).lower()
            if "no data found" in message or "delisted" in message or "not found" in message:
                raise SymbolNotFoundError(f"Yahoo has no data for {symbol!r}.") from exc
            raise ProviderUnavailableError(f"Yahoo request for {symbol!r} failed.") from exc

        if frame is None or frame.empty:
            # Ambiguous by construction: an empty frame is returned both for a bad
            # symbol and for a window with no trading days. The interface says an
            # empty window is not an error, so treat it as the benign case and let
            # the run's per-symbol counters show if a symbol never yields anything.
            return []

        return self._to_bars(symbol, frame)

    def _to_bars(self, symbol: str, frame: Any) -> list[PriceBar]:
        missing = [column for column in _REQUIRED if column not in frame.columns]
        if missing:
            # The shape changed underneath us — exactly the §7.3 risk. Fail loudly.
            raise ProviderUnavailableError(
                f"Yahoo response for {symbol!r} is missing columns: {', '.join(missing)}"
            )

        # `auto_adjust=False` yields an "Adj Close"; older builds have dropped it.
        # Falling back to Close is correct for an unadjusted series and beats
        # discarding the whole fetch.
        adj_column = "Adj Close" if "Adj Close" in frame.columns else "Close"

        bars: list[PriceBar] = []
        skipped = 0
        for index, row in frame.iterrows():
            values = {
                "open": _decimal(row["Open"]),
                "high": _decimal(row["High"]),
                "low": _decimal(row["Low"]),
                "close": _decimal(row["Close"]),
                "adj_close": _decimal(row[adj_column]),
            }
            if any(value is None for value in values.values()):
                # Yahoo emits NaN rows for halted sessions. Cleaning would reject
                # these anyway; dropping here keeps NaN out of the Decimal path.
                skipped += 1
                continue

            volume = row["Volume"]
            bars.append(
                PriceBar(
                    date=index.date() if hasattr(index, "date") else index,
                    volume=int(volume) if volume == volume and volume is not None else 0,
                    **values,  # type: ignore[arg-type]
                )
            )

        if skipped:
            logger.warning(
                "provider_rows_unusable",
                extra=safe_extra(provider=self.name, symbol=symbol, rows=skipped),
            )

        bars.sort(key=lambda bar: bar.date)
        return bars
