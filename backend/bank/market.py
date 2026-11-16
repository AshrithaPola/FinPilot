"""Market data for the market tools, fetched into the database (as the owner;
the app role only reads it). Never committed: the repo holds the code and
the lists below, not the values (data/SOURCES.md).

    python -m bank.market fetch sec      # annual figures from SEC company facts (SEC_USER_AGENT)
    python -m bank.market fetch prices   # daily closes of the demo ETFs (ALPHA_VANTAGE_API_KEY)
    python -m bank.market fetch rates    # FRED series (FRED_API_KEY)

Each writes to MIGRATION_DATABASE_URL (the owner), replacing what it
fetched before; run through scripts/neon.py for Neon.
"""
import argparse
import os
import sys
import time
from datetime import date

import sqlalchemy as sa

COMPANIES = {"AAPL": 320193, "MSFT": 789019, "KO": 21344}  # the 10-Ks in data/public/sources.yaml
ETFS = ("VTI", "VXUS", "BND", "SCHD")  # the demo holdings (bank/synthetic.py SECURITIES)
SERIES = {  # FRED series: title, as the tools show it
    "DPRIME": "Bank prime loan rate (%)",
    "DFF": "Federal funds effective rate (%)",
    "DGS10": "10-year Treasury constant maturity rate (%)",
}
# Metric -> the us-gaap concepts that report it, in order of preference
# (companies differ: Apple and Microsoft report revenue as
# RevenueFromContractWithCustomerExcludingAssessedTax, older filings as Revenues).
METRICS = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"),
    "net_income": ("NetIncomeLoss",),
    "operating_income": ("OperatingIncomeLoss",),
    "eps_diluted": ("EarningsPerShareDiluted",),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "total_assets": ("Assets",),
    "stockholders_equity": ("StockholdersEquity",),
}
YEARS = 5
SEC_PAUSE, AV_PAUSE = 0.25, 13.0  # SEC allows 10 requests a second; Alpha Vantage's free tier 5 a minute


# --- parsing (pure, tested without the network) -----------------------------------

def parse_company_facts(ticker: str, cik: int, facts: dict, years: int = YEARS) -> list[dict]:
    """Annual 10-K figures: for each metric, the first concept the company
    reports; one row per fiscal year (the year its period ends), from the
    latest filing that reported it (later 10-Ks repeat earlier years as
    comparatives). Durations must be about a year, so quarters don't slip in."""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    out = []
    for metric, concepts in METRICS.items():
        concept = next((c for c in concepts if c in gaap), None)
        if concept is None:
            continue
        for unit, entries in gaap[concept].get("units", {}).items():
            by_year: dict[int, dict] = {}
            for e in entries:
                if e.get("form") != "10-K" or e.get("fp") != "FY":
                    continue
                end = date.fromisoformat(e["end"])
                if "start" in e and not 330 <= (end - date.fromisoformat(e["start"])).days <= 400:
                    continue
                filed = date.fromisoformat(e["filed"])
                current = by_year.get(end.year)
                if current is None or filed > current["filed"]:
                    by_year[end.year] = {"ticker": ticker, "cik": cik, "metric": metric, "concept": concept,
                                         "label": gaap[concept].get("label") or concept, "unit": unit,
                                         "fiscal_year": end.year, "period_end": end, "value": e["val"],
                                         "form": e["form"], "filed": filed,
                                         "accession": e["accn"]}
            out += [by_year[y] for y in sorted(by_year)[-years:]]
    return out


def parse_alpha_vantage(symbol: str, payload: dict) -> list[dict]:
    series = payload.get("Time Series (Daily)")
    if series is None:  # a rate-limit note or an error message instead of data
        raise RuntimeError(f"Alpha Vantage gave no prices for {symbol}: "
                           f"{payload.get('Note') or payload.get('Information') or payload.get('Error Message')}")
    return [{"symbol": symbol, "price_date": date.fromisoformat(day), "close": values["4. close"],
             "source": "alphavantage"} for day, values in series.items()]


def parse_fred(series_id: str, payload: dict) -> list[dict]:
    return [{"series_id": series_id, "obs_date": date.fromisoformat(o["date"]), "value": o["value"]}
            for o in payload.get("observations", []) if o.get("value") not in (None, ".")]


# --- fetching and storing -------------------------------------------------------

def _get(session, url: str, **kwargs) -> dict:
    response = session.get(url, timeout=60, **kwargs)
    response.raise_for_status()
    return response.json()


def fetch_sec(user_agent: str) -> list[dict]:
    import requests

    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})
    rows = []
    for ticker, cik in COMPANIES.items():
        rows += parse_company_facts(ticker, cik, _get(
            session, f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"))
        time.sleep(SEC_PAUSE)
    return rows


def fetch_prices(api_key: str) -> list[dict]:
    import requests

    session, rows = requests.Session(), []
    for i, symbol in enumerate(ETFS):
        if i:
            time.sleep(AV_PAUSE)
        rows += parse_alpha_vantage(symbol, _get(session, "https://www.alphavantage.co/query", params={
            "function": "TIME_SERIES_DAILY", "symbol": symbol, "outputsize": "compact", "apikey": api_key}))
    return rows


def fetch_rates(api_key: str, start: str = "2025-01-01") -> list[dict]:
    import requests

    session, rows = requests.Session(), []
    for series_id in SERIES:
        rows += parse_fred(series_id, _get(session, "https://api.stlouisfed.org/fred/series/observations", params={
            "series_id": series_id, "api_key": api_key, "file_type": "json", "observation_start": start}))
    return rows


def store(owner_url: str, kind: str, rows: list[dict]) -> int:
    """Replace what this kind of fetch wrote before (one transaction)."""
    from config import sqlalchemy_url

    table, key, scope = {"sec": ("company_facts", "ticker", COMPANIES),
                         "prices": ("prices", "symbol", ETFS),
                         "rates": ("rate_observations", "series_id", SERIES)}[kind]
    engine = sa.create_engine(sqlalchemy_url(owner_url))
    try:
        with engine.begin() as conn:
            where = " AND source = 'alphavantage'" if kind == "prices" else ""
            conn.execute(sa.text(f"DELETE FROM {table} WHERE {key} = ANY(:keys){where}"), {"keys": list(scope)})
            if rows:
                conn.execute(sa.insert(sa.table(table, *map(sa.column, rows[0]))), rows)
    finally:
        engine.dispose()
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch market data into the database.")
    parser.add_argument("command", choices=["fetch"])
    parser.add_argument("what", choices=["sec", "prices", "rates"])
    args = parser.parse_args()
    from config import get_settings

    settings = get_settings()
    owner = os.environ.get("MIGRATION_DATABASE_URL")
    if not owner:
        print("Set MIGRATION_DATABASE_URL (the owner), e.g. through scripts/neon.py run.", file=sys.stderr)
        return 2
    key = {"sec": settings.sec_user_agent, "prices": settings.alpha_vantage_api_key,
           "rates": settings.fred_api_key}[args.what]
    if not key:
        name = {"sec": "SEC_USER_AGENT", "prices": "ALPHA_VANTAGE_API_KEY", "rates": "FRED_API_KEY"}[args.what]
        print(f"{name} is not set in .env; nothing fetched.", file=sys.stderr)
        return 2
    rows = {"sec": fetch_sec, "prices": fetch_prices, "rates": fetch_rates}[args.what](key)
    print(f"{args.what}: {store(owner, args.what, rows)} rows stored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
