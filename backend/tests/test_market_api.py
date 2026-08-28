"""§13.2 market endpoints, and the §13.1 conventions they inherit.

Pagination is the bulk of this file because keyset paging is easy to get subtly
wrong in ways no single-page test notices — a dropped row at a page boundary, or a
`next_cursor` that points at an empty page.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.pagination import encode_cursor
from app.models.asset import Asset
from app.models.enums import AssetClass, AssetType
from app.models.market_data import MarketData

BASE = "/api/v1/market"


@pytest.fixture
def asset(db: Session) -> Asset:
    row = Asset(
        symbol="SPY",
        name="SPDR S&P 500 ETF Trust",
        asset_type=AssetType.ETF,
        asset_class=AssetClass.EQUITY,
        currency="USD",
        exchange="NYSEARCA",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture
def bars(db: Session, asset: Asset) -> list[MarketData]:
    """Ten consecutive sessions, close rising by 1 a day so order is checkable."""
    rows = [
        MarketData(
            asset_id=asset.id,
            date=date(2026, 1, 1) + timedelta(days=n),
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("90"),
            close=Decimal(100 + n),
            adj_close=Decimal(100 + n),
            volume=1_000 + n,
            source="test",
        )
        for n in range(10)
    ]
    db.add_all(rows)
    db.commit()
    return rows


class TestAuthentication:
    """§13.1 — bearer JWT on everything except register and login. The data is
    public, but the convention is not conditional on the payload."""

    @pytest.mark.parametrize("path", ["/assets", "/SPY"])
    def test_unauthenticated_requests_are_rejected(self, client: TestClient, path: str) -> None:
        response = client.get(f"{BASE}{path}")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"

    def test_an_invalid_token_is_rejected(self, client: TestClient) -> None:
        response = client.get(
            f"{BASE}/assets", headers={"Authorization": "Bearer not-a-real-token"}
        )
        assert response.status_code == 401


class TestListAssets:
    def test_it_returns_the_list_envelope(
        self, client: TestClient, auth_headers: dict[str, str], asset: Asset
    ) -> None:
        body = client.get(f"{BASE}/assets", headers=auth_headers).json()
        assert set(body) == {"data", "next_cursor"}
        assert body["data"][0]["symbol"] == "SPY"
        assert body["next_cursor"] is None

    def test_it_exposes_the_asset_class_fr_11_needs(
        self, client: TestClient, auth_headers: dict[str, str], asset: Asset
    ) -> None:
        row = client.get(f"{BASE}/assets", headers=auth_headers).json()["data"][0]
        assert row["asset_class"] == "EQUITY"
        assert row["asset_type"] == "ETF"

    def test_the_full_universe_is_reachable(
        self, client: TestClient, auth_headers: dict[str, str], seeded_assets: list[Asset]
    ) -> None:
        body = client.get(f"{BASE}/assets?limit=500", headers=auth_headers).json()
        assert len(body["data"]) == len(seeded_assets)

    def test_paging_visits_every_asset_exactly_once(
        self, client: TestClient, auth_headers: dict[str, str], seeded_assets: list[Asset]
    ) -> None:
        """The bug keyset pagination invites: a row dropped at a page boundary."""
        seen: list[str] = []
        cursor: str | None = None
        for _ in range(20):  # bounded, so a broken cursor cannot hang the suite
            url = f"{BASE}/assets?limit=7" + (f"&cursor={cursor}" if cursor else "")
            body = client.get(url, headers=auth_headers).json()
            seen.extend(row["symbol"] for row in body["data"])
            cursor = body["next_cursor"]
            if cursor is None:
                break

        assert cursor is None
        assert seen == sorted(asset.symbol for asset in seeded_assets)
        assert len(seen) == len(set(seen))

    def test_the_last_page_reports_no_next_cursor(
        self, client: TestClient, auth_headers: dict[str, str], seeded_assets: list[Asset]
    ) -> None:
        """A cursor that leads to an empty page makes a paging client loop forever."""
        body = client.get(f"{BASE}/assets?limit=500", headers=auth_headers).json()
        assert body["next_cursor"] is None

    def test_filters_narrow_the_result(
        self, client: TestClient, auth_headers: dict[str, str], seeded_assets: list[Asset]
    ) -> None:
        bonds = client.get(
            f"{BASE}/assets?asset_class=BOND&limit=500", headers=auth_headers
        ).json()["data"]
        assert bonds
        assert {row["asset_class"] for row in bonds} == {"BOND"}

        equities = client.get(
            f"{BASE}/assets?asset_type=EQUITY&limit=500", headers=auth_headers
        ).json()["data"]
        assert {row["asset_type"] for row in equities} == {"EQUITY"}

    def test_inactive_assets_are_hidden(
        self, client: TestClient, auth_headers: dict[str, str], asset: Asset, db: Session
    ) -> None:
        asset.is_active = False
        db.commit()
        assert client.get(f"{BASE}/assets", headers=auth_headers).json()["data"] == []

    @pytest.mark.parametrize("limit", [0, -1, 501])
    def test_an_out_of_range_limit_is_rejected(
        self, client: TestClient, auth_headers: dict[str, str], limit: int
    ) -> None:
        response = client.get(f"{BASE}/assets?limit={limit}", headers=auth_headers)
        assert response.status_code == 422
        assert "limit" in response.json()["error"]["fields"]

    def test_a_malformed_cursor_is_a_400_not_a_500(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """And never a silent reset to page one, which loops a paging client."""
        response = client.get(f"{BASE}/assets?cursor=%FF%FF%FF", headers=auth_headers)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_cursor"


class TestMarketHistory:
    def test_it_returns_bars_newest_first_with_the_asset(
        self, client: TestClient, auth_headers: dict[str, str], bars: list[MarketData]
    ) -> None:
        body = client.get(f"{BASE}/SPY", headers=auth_headers).json()
        assert body["asset"]["symbol"] == "SPY"
        assert len(body["data"]) == 10
        dates = [row["date"] for row in body["data"]]
        assert dates == sorted(dates, reverse=True)

    def test_the_symbol_is_case_insensitive(
        self, client: TestClient, auth_headers: dict[str, str], bars: list[MarketData]
    ) -> None:
        assert client.get(f"{BASE}/spy", headers=auth_headers).status_code == 200

    def test_an_unknown_symbol_is_a_404_in_the_standard_envelope(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.get(f"{BASE}/NOTREAL", headers=auth_headers)
        assert response.status_code == 404
        body = response.json()
        assert set(body["error"]) == {"code", "message", "fields"}
        assert body["error"]["code"] == "asset_not_found"

    def test_an_asset_with_no_bars_returns_an_empty_page_not_a_404(
        self, client: TestClient, auth_headers: dict[str, str], asset: Asset
    ) -> None:
        """A tracked asset awaiting its first ingestion exists; it just has no data."""
        body = client.get(f"{BASE}/SPY", headers=auth_headers).json()
        assert body["data"] == []
        assert body["next_cursor"] is None

    def test_the_date_window_is_inclusive(
        self, client: TestClient, auth_headers: dict[str, str], bars: list[MarketData]
    ) -> None:
        body = client.get(
            f"{BASE}/SPY?start=2026-01-03&end=2026-01-05", headers=auth_headers
        ).json()
        assert [row["date"] for row in body["data"]] == [
            "2026-01-05",
            "2026-01-04",
            "2026-01-03",
        ]

    def test_paging_visits_every_bar_exactly_once(
        self, client: TestClient, auth_headers: dict[str, str], bars: list[MarketData]
    ) -> None:
        seen: list[str] = []
        cursor: str | None = None
        for _ in range(20):
            url = f"{BASE}/SPY?limit=3" + (f"&cursor={cursor}" if cursor else "")
            body = client.get(url, headers=auth_headers).json()
            seen.extend(row["date"] for row in body["data"])
            cursor = body["next_cursor"]
            if cursor is None:
                break

        assert cursor is None
        assert len(seen) == 10
        assert len(set(seen)) == 10
        assert seen == sorted(seen, reverse=True)

    def test_a_reversed_date_range_is_rejected(
        self, client: TestClient, auth_headers: dict[str, str], asset: Asset
    ) -> None:
        response = client.get(f"{BASE}/SPY?start=2026-06-01&end=2026-01-01", headers=auth_headers)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_date_range"

    def test_a_non_date_bound_is_a_422(
        self, client: TestClient, auth_headers: dict[str, str], asset: Asset
    ) -> None:
        response = client.get(f"{BASE}/SPY?start=yesterday", headers=auth_headers)
        assert response.status_code == 422

    def test_a_cursor_that_is_not_a_date_is_a_400(
        self, client: TestClient, auth_headers: dict[str, str], asset: Asset
    ) -> None:
        response = client.get(
            f"{BASE}/SPY?cursor={encode_cursor('not-a-date')}", headers=auth_headers
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_cursor"

    def test_prices_survive_the_round_trip_as_exact_decimals(
        self, client: TestClient, auth_headers: dict[str, str], bars: list[MarketData]
    ) -> None:
        """Decimal end to end (§4 of the phase plan): a float round-trip would show
        up here as 100.00000000000001."""
        row = client.get(f"{BASE}/SPY?limit=1", headers=auth_headers).json()["data"][0]
        assert Decimal(str(row["close"])) == Decimal("109")
        assert Decimal(str(row["open"])) == Decimal("100")
