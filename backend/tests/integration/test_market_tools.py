"""Market tools against real Postgres: latest price on or before a date,
with its source; SEC figures newest first, with the filing; FRED values with
the notice; and the app role can't write the market tables. Skipped without
TEST_POSTGRES_URL."""
import pytest
import sqlalchemy as sa

from bank import synthetic
from db.access import Access
from tests.integration.test_corpus_load import _store
from tools.market import FRED_NOTICE, MarketTools

USER = Access(org_id=synthetic.BANK_ORG, clearance=0, customer_id="cust-0001")


@pytest.fixture
def tools(pg_url, app_url):
    _store(pg_url, app_url)
    synthetic.load(pg_url, synthetic.generate(customers=1))  # securities and synthetic prices
    owner = sa.create_engine(pg_url)
    with owner.begin() as conn:
        conn.execute(sa.text("INSERT INTO prices VALUES ('VTI', '2026-11-13', 301.27, 'alphavantage')"))
        conn.execute(sa.text("""
            INSERT INTO company_facts VALUES
              ('KO', 21344, 'revenue', 'Revenues', 'Revenues', 'USD', 2024, '2024-12-31', 47061000000,
               '10-K', '2025-02-20', '0000021344-25-000010'),
              ('KO', 21344, 'revenue', 'Revenues', 'Revenues', 'USD', 2025, '2025-12-31', 48300000000,
               '10-K', '2026-02-20', '0001628280-26-010047')"""))
        conn.execute(sa.text("INSERT INTO rate_observations VALUES ('DPRIME', '2026-11-10', 7.50), "
                             "('DPRIME', '2026-11-12', 7.25)"))
    owner.dispose()
    return MarketTools(sa.create_engine(app_url))


def test_prices_are_the_latest_on_or_before_the_date_with_their_source(tools):
    [latest] = tools.call(USER, "get_price", {"symbol": "VTI"})
    assert (latest["price_date"], latest["close"], latest["attribution"]) == (
        "2026-11-13", "301.2700", "Prices: Alpha Vantage")
    [old] = tools.call(USER, "get_price", {"symbol": "VTI", "as_of": "2026-01-04"})  # a Sunday
    assert old["price_date"] <= "2026-01-04" and old["source"] == "synthetic"
    history = tools.call(USER, "get_price_history", {"symbol": "VTI", "limit": 3})
    assert [h["price_date"] for h in history] == sorted((h["price_date"] for h in history), reverse=True)
    assert tools.call(USER, "get_price", {"symbol": "ZZZZ"}) == []


def test_financials_and_rates_cite_their_source(tools):
    years = tools.call(USER, "get_company_financials", {"ticker": "KO", "metric": "revenue"})
    assert [y["fiscal_year"] for y in years] == [2025, 2024]
    assert years[0]["attribution"] == "SEC EDGAR, 10-K filed 2026-02-20 (accession 0001628280-26-010047)"
    [rate] = tools.call(USER, "get_rate", {"series_id": "DPRIME", "as_of": "2026-11-11"})
    assert (rate["obs_date"], rate["value"]) == ("2026-11-10", "7.50") and FRED_NOTICE in rate["attribution"]


def test_the_app_role_cannot_write_market_data(tools, app_url):
    for statement in ("UPDATE prices SET close = 1", "DELETE FROM company_facts",
                      "INSERT INTO rate_observations VALUES ('DFF', '2026-01-01', 99)"):
        with pytest.raises(sa.exc.DBAPIError, match="permission denied"):
            with sa.create_engine(app_url).begin() as conn:
                conn.execute(sa.text(statement))
