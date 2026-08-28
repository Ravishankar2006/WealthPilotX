"""FRED macroeconomic data (FR-05 initial source).

FRED is an official, documented, rate-limited-but-stable API — the low-risk half of
§7.3. It still goes behind the provider interface, because the point of the seam is
that *no* application code knows where a number came from.
"""

from datetime import date
from decimal import Decimal, InvalidOperation

import httpx

from app.models.enums import EconomicSeries
from app.providers.base import (
    ProviderConfigurationError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
    SeriesPoint,
    SymbolNotFoundError,
)

BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

# FR-05 names five concepts; FRED publishes series IDs. This mapping is the whole
# translation, kept in one place so switching (say) the interest-rate proxy from the
# fed funds rate to the 10-year is a one-line, reviewable change.
SERIES_IDS: dict[EconomicSeries, str] = {
    EconomicSeries.INFLATION: "CPIAUCSL",  # CPI, all urban consumers, seasonally adj.
    EconomicSeries.INTEREST_RATE: "FEDFUNDS",  # Effective federal funds rate
    EconomicSeries.GDP: "GDPC1",  # Real GDP, chained 2017 dollars
    EconomicSeries.UNEMPLOYMENT: "UNRATE",  # Civilian unemployment rate
    EconomicSeries.FX_RATE: "DTWEXBGS",  # Trade-weighted USD index, broad
}


class FredEconomicDataProvider:
    name = "fred"

    def __init__(self, api_key: str | None, timeout: float = 30.0) -> None:
        self._api_key = api_key
        self._timeout = timeout

    def fetch_series(self, series: EconomicSeries, start: date, end: date) -> list[SeriesPoint]:
        if not self._api_key:
            # Configuration errors are not retryable — surfacing this as a distinct
            # type stops the retry loop from spending 4 attempts on a missing key.
            raise ProviderConfigurationError(
                "FRED_API_KEY is not configured; cannot fetch economic data."
            )

        params = {
            "series_id": SERIES_IDS[series],
            "api_key": self._api_key,
            "file_type": "json",
            "observation_start": start.isoformat(),
            "observation_end": end.isoformat(),
        }

        try:
            response = httpx.get(BASE_URL, params=params, timeout=self._timeout)
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"FRED request for {series} failed.") from exc

        if response.status_code == 429:
            raise ProviderRateLimitedError(f"FRED rate-limited the request for {series}.")
        if response.status_code == 400:
            # FRED answers an unknown series ID with a 400, which is permanent —
            # our mapping is wrong and no retry will fix it.
            raise SymbolNotFoundError(f"FRED rejected series {SERIES_IDS[series]!r}.")
        if response.status_code >= 500:
            raise ProviderUnavailableError(f"FRED returned {response.status_code} for {series}.")
        if response.status_code != 200:
            raise ProviderUnavailableError(
                f"FRED returned an unexpected {response.status_code} for {series}."
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderUnavailableError("FRED returned a non-JSON body.") from exc

        return parse_observations(payload)


def parse_observations(payload: dict[str, object]) -> list[SeriesPoint]:
    """Extract observations from a FRED payload.

    Split out from the HTTP call so the parsing rules — notably FRED's "." for a
    value that does not exist for that date — are unit-testable without a network.
    """
    raw = payload.get("observations")
    if not isinstance(raw, list):
        raise ProviderUnavailableError("FRED payload has no observations array.")

    points: list[SeriesPoint] = []
    for observation in raw:
        if not isinstance(observation, dict):
            continue
        value = observation.get("value")
        # FRED uses "." for a missing observation. It is not a zero, and storing it
        # as one would put a fabricated data point in front of an M3 model.
        if value in (None, ".", ""):
            continue
        try:
            points.append(
                SeriesPoint(
                    date=date.fromisoformat(str(observation["date"])),
                    value=Decimal(str(value)),
                )
            )
        except (KeyError, ValueError, InvalidOperation):
            continue

    points.sort(key=lambda point: point.date)
    return points
