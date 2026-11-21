"""Market tools: prices of the demo securities, annual company figures from
SEC filings, and FRED interest rates, read from the database (bank/market.py
fetches them; nothing here calls an outside service at question time).

Public reference data, the same for everyone: any signed-in caller may ask,
and the transaction is READ ONLY. Every row says where it came from and as
of when, so an answer can cite it: Alpha Vantage prices with their date
("Prices: Alpha Vantage"), the synthetic demo prices marked as such, SEC
figures with the filing's form, date and accession number, FRED values with
the notice FRED's terms require.
"""
from datetime import date
from typing import Annotated, Literal

import sqlalchemy as sa
from pydantic import Field, model_validator

from bank.market import METRICS, SERIES
from tools.common import AccessRefused, Args, Tool, ToolError, read_only, rows, validate

Symbol = Annotated[str, Field(pattern=r"^[A-Z]{1,5}$")]
FRED_NOTICE = "This product uses the FRED® API but is not endorsed or certified by the Federal Reserve Bank of St. Louis."
PRICE_SOURCES = {"alphavantage": "Prices: Alpha Vantage", "synthetic": "Synthetic demo price (not a market price)"}


class PriceArgs(Args):
    symbol: Symbol
    as_of: date | None = None


class HistoryArgs(Args):
    symbol: Symbol
    start: date | None = None
    end: date | None = None
    limit: Annotated[int, Field(ge=1, le=200)] = 30

    @model_validator(mode="after")
    def _ordered(self):
        if self.start and self.end and self.start > self.end:
            raise ValueError("start must be on or before end")
        return self


class FinancialsArgs(Args):
    ticker: Symbol
    metric: Literal[tuple(METRICS)]  # type: ignore[valid-type]
    years: Annotated[int, Field(ge=1, le=10)] = 5
    as_of: date | None = None  # only figures filed on or before it


class RateArgs(Args):
    series_id: Literal[tuple(SERIES)]  # type: ignore[valid-type]
    as_of: date | None = None


def _attributed(price_rows: list[dict]) -> list[dict]:
    return [{**r, "attribution": PRICE_SOURCES.get(r["source"], r["source"])} for r in price_rows]


def _price(conn, args: PriceArgs) -> list[dict]:
    """The close on as_of, or the last one before it (markets close on weekends)."""
    return _attributed(rows(conn, """
        SELECT symbol, price_date, close, source FROM prices
        WHERE symbol = :symbol AND (CAST(:as_of AS date) IS NULL OR price_date <= :as_of)
        ORDER BY price_date DESC LIMIT 1
    """, args.model_dump()))


def _history(conn, args: HistoryArgs) -> list[dict]:
    return _attributed(rows(conn, """
        SELECT symbol, price_date, close, source FROM prices
        WHERE symbol = :symbol
          AND (CAST(:start AS date) IS NULL OR price_date >= :start)
          AND (CAST(:end AS date) IS NULL OR price_date <= :end)
        ORDER BY price_date DESC LIMIT :limit
    """, args.model_dump()))


def _financials(conn, args: FinancialsArgs) -> list[dict]:
    found = rows(conn, """
        SELECT ticker, metric, label, unit, fiscal_year, period_end, value, form, filed, accession
        FROM company_facts WHERE ticker = :ticker AND metric = :metric
          AND (CAST(:as_of AS date) IS NULL OR filed <= :as_of)
        ORDER BY fiscal_year DESC LIMIT :years
    """, args.model_dump())
    return [{**r, "attribution": f"SEC EDGAR, {r['form']} filed {r['filed']} (accession {r['accession']})"}
            for r in found]


def _rate(conn, args: RateArgs) -> list[dict]:
    found = rows(conn, """
        SELECT series_id, obs_date, value FROM rate_observations
        WHERE series_id = :series_id AND (CAST(:as_of AS date) IS NULL OR obs_date <= :as_of)
        ORDER BY obs_date DESC LIMIT 1
    """, args.model_dump())
    return [{**r, "title": SERIES[r["series_id"]], "attribution": f"FRED, series {r['series_id']}. {FRED_NOTICE}"}
            for r in found]


TOOLS = {t.name: t for t in (
    Tool("get_price", "Closing price of a security (e.g. VTI) on a date, or the latest before it; "
         "with its date and source.", PriceArgs, _price),
    Tool("get_price_history", "Closing prices of a security, newest first; optional date range and "
         "limit (at most 200).", HistoryArgs, _history),
    Tool("get_company_financials", f"Annual figures from a company's 10-K filings (AAPL, MSFT, KO): metric "
         f"one of {', '.join(METRICS)}; newest year first.", FinancialsArgs, _financials),
    Tool("get_rate", f"Latest value of an interest-rate series on or before a date: "
         f"{'; '.join(f'{k} = {v}' for k, v in SERIES.items())}.", RateArgs, _rate),
)}


class MarketTools:
    def __init__(self, engine: sa.Engine):
        self._engine = engine

    def call(self, principal, name: str, arguments: dict | None = None) -> list[dict]:
        if principal is None:
            raise AccessRefused("Market tools are for signed-in users.")
        tool, args = validate(TOOLS, name, arguments)
        with read_only(self._engine) as conn:  # public data: no access context needed
            return tool.run(conn, args)
