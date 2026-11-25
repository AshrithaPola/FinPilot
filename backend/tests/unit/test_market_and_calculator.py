"""The calculator's arithmetic, the market data parsers, and market tool
arguments refused before any SQL; no database, no network."""
from datetime import date

import pytest

from bank.market import parse_alpha_vantage, parse_company_facts, parse_fred
from tools.calculator import calculate
from tools.common import ToolError
from tools.market import MarketTools

USER = object()  # any signed-in caller


@pytest.mark.parametrize("name, arguments, expected", [
    ("afford", {"balance_cents": 150_000, "amount_cents": 120_000, "keep_at_least_cents": 50_000},
     {"remaining_cents": 30_000, "affordable": False, "shortfall_cents": 20_000}),
    ("afford", {"balance_cents": 200_000, "amount_cents": 120_000, "keep_at_least_cents": 50_000},
     {"remaining_cents": 80_000, "affordable": True, "shortfall_cents": 0}),
    ("total", {"amounts_cents": [-1_299, -4_550, 10_000]}, {"total_cents": 4_151, "count": 3}),
    # The deposit agreement's own example (section 9.5): $10,000 at 3.54%, 30 days, daily -> $29.14.
    ("interest", {"principal_cents": 1_000_000, "annual_rate_pct": "3.54", "days": 30},
     {"interest_cents": 2_914, "ending_cents": 1_002_914}),
    ("interest", {"principal_cents": 100_000, "annual_rate_pct": "20", "days": 365, "compounding": "simple"},
     {"interest_cents": 20_000}),
    ("percent_change", {"old": "290", "new": "301.27"}, {"percent_change": "3.89"}),
    ("percent_change", {"old": "0", "new": "5"}, {"percent_change": None}),
    ("market_value", {"quantity": "12.5", "price": "301.27"}, {"value_cents": 376_588}),
    ("gain", {"cost_basis_cents": 100_000, "value_cents": 112_345}, {"gain_cents": 12_345, "return_pct": "12.35"}),
])
def test_the_calculator_is_exact(name, arguments, expected):
    result = calculate(name, arguments)
    assert {k: result[k] for k in expected} == expected and result["formula"]


@pytest.mark.parametrize("name, arguments", [
    ("interest", {"principal_cents": 100, "annual_rate_pct": "101", "days": 30}),
    ("interest", {"principal_cents": 100, "annual_rate_pct": "5", "days": 0}),
    ("total", {"amounts_cents": []}),
    ("afford", {"balance_cents": 1, "amount_cents": 1, "customer_id": "cust-0002"}),
    ("market_value", {"quantity": "-1", "price": "10"}),
    ("eval", {"expression": "__import__('os').system('rm -rf /')"}),
])
def test_the_calculator_refuses_what_it_does_not_define(name, arguments):
    with pytest.raises(ToolError):
        calculate(name, arguments)


def _entry(val, end, filed, form="10-K", fp="FY", start=None, accn="0000000000-26-000001"):
    e = {"val": val, "end": end, "filed": filed, "form": form, "fp": fp, "accn": accn}
    return e | ({"start": start} if start else {})


def test_company_facts_keep_annual_10k_figures_from_the_latest_filing():
    facts = {"facts": {"us-gaap": {
        "Revenues": {"label": "Revenues", "units": {"USD": [
            _entry(100, "2024-12-31", "2025-02-01", start="2024-01-01"),
            _entry(105, "2024-12-31", "2026-02-01", start="2024-01-01", accn="later"),  # restated comparative
            _entry(120, "2025-12-31", "2026-02-01", start="2025-01-01"),
            _entry(30, "2025-09-30", "2025-11-01", form="10-Q", fp="Q3", start="2025-07-01"),
            _entry(31, "2025-12-31", "2026-02-01", start="2025-10-01"),  # a quarter inside a 10-K
        ]}},
        "Assets": {"label": "Assets", "units": {"USD": [_entry(900, "2025-12-31", "2026-02-01")]}},
    }}}
    found = {(r["metric"], r["fiscal_year"]): r for r in parse_company_facts("KO", 21344, facts)}
    assert {k: r["value"] for k, r in found.items()} == {
        ("revenue", 2024): 105, ("revenue", 2025): 120, ("total_assets", 2025): 900}
    assert found[("revenue", 2024)]["accession"] == "later" and found[("revenue", 2025)]["concept"] == "Revenues"

    # On a day before the later 10-K was filed, only the earlier filing exists.
    from datetime import date

    before = {(r["metric"], r["fiscal_year"]): r
              for r in parse_company_facts("KO", 21344, facts, as_of=date(2025, 6, 30))}
    assert {k: r["value"] for k, r in before.items()} == {("revenue", 2024): 100}
    assert all(r["filed"] <= date(2025, 6, 30) for r in before.values())


def test_market_tools_never_read_past_the_demo_today():
    from datetime import date

    from engine.executor import AS_OF_TODAY, capped

    today = date(2026, 6, 30)
    assert capped({"symbol": "VTI"}, "as_of", today) == {"symbol": "VTI", "as_of": "2026-06-30"}
    assert capped({"as_of": "2026-12-31"}, "as_of", today) == {"as_of": "2026-06-30"}  # later: today
    assert capped({"as_of": "2026-01-02"}, "as_of", today) == {"as_of": "2026-01-02"}  # earlier: kept
    assert capped({"end": "soon"}, "end", today) == {"end": "soon"}  # the tool's validation refuses it
    assert AS_OF_TODAY == {"get_price": "as_of", "get_rate": "as_of", "get_company_financials": "as_of",
                           "get_price_history": "end"}  # every market tool that reads dated rows


def test_price_and_rate_parsers():
    payload = {"Time Series (Daily)": {"2026-11-13": {"4. close": "301.27"}}}
    assert parse_alpha_vantage("VTI", payload) == [
        {"symbol": "VTI", "price_date": date(2026, 11, 13), "close": "301.27", "source": "alphavantage"}]
    with pytest.raises(RuntimeError, match="no prices for VTI"):
        parse_alpha_vantage("VTI", {"Note": "Thank you for using Alpha Vantage! ... 25 requests per day."})
    observations = {"observations": [{"date": "2026-11-12", "value": "7.50"}, {"date": "2026-11-11", "value": "."}]}
    assert parse_fred("DPRIME", observations) == [{"series_id": "DPRIME", "obs_date": date(2026, 11, 12), "value": "7.50"}]


@pytest.mark.parametrize("name, arguments", [
    ("get_price", {"symbol": "vti"}),
    ("get_price", {"symbol": "VTI'; DROP TABLE prices; --"}),
    ("get_price_history", {"symbol": "VTI", "limit": 1000}),
    ("get_company_financials", {"ticker": "AAPL", "metric": "salaries_of_everyone"}),
    ("get_rate", {"series_id": "SOMETHING"}),
    ("get_rate", {"series_id": "DPRIME", "customer_id": "cust-0002"}),
])
def test_market_tool_arguments_are_refused_before_sql(name, arguments):
    with pytest.raises(ToolError, match="Invalid arguments"):
        MarketTools(engine=None).call(USER, name, arguments)
    with pytest.raises(ToolError, match="signed-in"):
        MarketTools(engine=None).call(None, "get_price", {"symbol": "VTI"})


def test_the_difference_tool_subtracts_and_gives_the_size():
    from tools.calculator import calculate

    out = calculate("difference", {"a_cents": -54940, "b_cents": -25998})  # April's and May's groceries
    assert out["difference_cents"] == -28942 and out["absolute_cents"] == 28942


def test_the_planner_sees_each_arguments_allowed_values_and_fee_aliases_are_accepted():
    from engine.plan import planner_prompt
    from tools.accounts import FeeArgs

    prompt = planner_prompt("2025-07-01", "2026-06-30", "2026-06-30")
    assert "fee_type?: overdraft | nsf | monthly_maintenance | atm | foreign_transaction | wire | late_payment" in prompt
    assert "account_type?: checking | savings | credit | brokerage" in prompt and "- difference(" in prompt
    for alias, name in (("late", "late_payment"), ("late_fee", "late_payment"), ("Late Fee", "late_payment"),
                        ("maintenance", "monthly_maintenance"), ("atm-fee", "atm"), ("fx", "foreign_transaction")):
        assert FeeArgs.model_validate({"fee_type": alias}).fee_type == name
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        FeeArgs.model_validate({"fee_type": "bogus"})  # anything else is still refused


@pytest.mark.parametrize("status", [None, 500])
def test_a_failed_request_never_shows_the_api_key(status):
    import requests
    from bank.market import _get

    key = "SECRET-KEY-123"

    class Session:
        def get(self, url, **kwargs):
            full = requests.Request("GET", url, params=kwargs.get("params")).prepare().url
            if status is None:
                raise requests.ConnectionError(f"Max retries exceeded with url: {full}")
            response = requests.Response()
            response.status_code, response.url, response.reason = status, full, "Server Error"
            return response

    with pytest.raises(RuntimeError) as caught:
        _get(Session(), "https://www.alphavantage.co/query", params={"symbol": "VTI", "apikey": key})
    shown = str(caught.value) + repr(caught.value.__cause__) + repr(caught.value.__context__)
    assert key not in shown and "www.alphavantage.co" in shown
    assert caught.value.__context__ is None and caught.value.__cause__ is None  # nothing attached
