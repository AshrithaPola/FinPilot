"""The calculator: deterministic arithmetic for answers that need numbers
(can I afford this, how much interest, how much did my holding gain), so
the language model never does arithmetic. Inputs come from the other
tools (cents, prices, quantities, rates); every result shows its formula,
for the "how I got this" panel.

Decimal throughout, money rounded half-up to the cent; inputs are bounded
so nothing overflows or runs long. No database, no caller data: the planner
passes it numbers it already has.
"""
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal

from pydantic import Field

from tools.common import Args, Tool, validate

Cents = Annotated[int, Field(ge=-10**13, le=10**13)]  # up to $100 billion either way
Positive = Annotated[Decimal, Field(gt=0, le=Decimal(10**9), max_digits=20, decimal_places=8)]
CENT = Decimal("1")


def _cents(value: Decimal) -> int:
    return int(value.quantize(CENT, rounding=ROUND_HALF_UP))


def _pct(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


class AffordArgs(Args):
    balance_cents: Cents
    amount_cents: Annotated[int, Field(ge=0, le=10**13)]
    keep_at_least_cents: Cents = 0


class TotalArgs(Args):
    amounts_cents: Annotated[list[Cents], Field(min_length=1, max_length=1000)]


class DifferenceArgs(Args):
    a_cents: Cents
    b_cents: Cents


class InterestArgs(Args):
    principal_cents: Annotated[int, Field(ge=0, le=10**13)]
    annual_rate_pct: Annotated[Decimal, Field(ge=0, le=100, max_digits=10, decimal_places=6)]
    days: Annotated[int, Field(ge=1, le=36_500)]
    compounding: Literal["simple", "daily"] = "daily"


class ChangeArgs(Args):
    old: Annotated[Decimal, Field(max_digits=24, decimal_places=8)]
    new: Annotated[Decimal, Field(max_digits=24, decimal_places=8)]


class ValueArgs(Args):
    quantity: Positive
    price: Positive


class GainArgs(Args):
    cost_basis_cents: Annotated[int, Field(gt=0, le=10**13)]
    value_cents: Annotated[int, Field(ge=0, le=10**13)]


def _afford(args: AffordArgs) -> dict:
    remaining = args.balance_cents - args.amount_cents
    return {"remaining_cents": remaining, "affordable": remaining >= args.keep_at_least_cents,
            "shortfall_cents": max(0, args.keep_at_least_cents - remaining),
            "formula": "remaining = balance - amount; affordable if remaining >= keep_at_least"}


def _difference(args: DifferenceArgs) -> dict:
    difference = args.a_cents - args.b_cents
    return {"difference_cents": difference, "absolute_cents": abs(difference),
            "formula": "difference = a - b; absolute = |a - b| (two money-out totals: absolute is how much more)"}


def _total(args: TotalArgs) -> dict:
    return {"total_cents": sum(args.amounts_cents), "count": len(args.amounts_cents),
            "formula": "total = sum of amounts"}


def _interest(args: InterestArgs) -> dict:
    principal, rate = Decimal(args.principal_cents), args.annual_rate_pct / 100
    if args.compounding == "simple":
        interest = principal * rate * args.days / 365
        formula = "interest = principal x rate x days / 365"
    else:
        interest = principal * ((1 + rate / 365) ** args.days - 1)
        formula = "interest = principal x ((1 + rate / 365) ^ days - 1)"
    return {"interest_cents": _cents(interest), "ending_cents": _cents(principal + interest), "formula": formula}


def _change(args: ChangeArgs) -> dict:
    if args.old == 0:
        return {"percent_change": None, "formula": "undefined: the old value is 0"}
    return {"percent_change": _pct((args.new - args.old) / abs(args.old) * 100),
            "formula": "percent_change = (new - old) / |old| x 100"}


def _value(args: ValueArgs) -> dict:
    return {"value_cents": _cents(args.quantity * args.price * 100), "formula": "value = quantity x price"}


def _gain(args: GainArgs) -> dict:
    gain = args.value_cents - args.cost_basis_cents
    return {"gain_cents": gain, "return_pct": _pct(Decimal(gain) / args.cost_basis_cents * 100),
            "formula": "gain = value - cost_basis; return = gain / cost_basis x 100"}


TOOLS = {t.name: t for t in (
    Tool("afford", "Whether a purchase leaves at least keep_at_least_cents in the account: "
         "remaining balance, yes or no, and the shortfall.", AffordArgs, _afford),
    Tool("total", "Sum of amounts in cents (e.g. transactions from list_transactions).", TotalArgs, _total),
    Tool("difference", "a minus b in cents, and its absolute value (e.g. April's total minus May's: for two "
         "money-out totals, the absolute value is how much more).", DifferenceArgs, _difference),
    Tool("interest", "Interest on a principal at an annual rate over a number of days, simple or "
         "compounded daily.", InterestArgs, _interest),
    Tool("percent_change", "Percent change from an old value to a new one.", ChangeArgs, _change),
    Tool("market_value", "Value in cents of a quantity of a security at a price.", ValueArgs, _value),
    Tool("gain", "Gain and return of a holding from its cost basis and current value.", GainArgs, _gain),
)}


def calculate(name: str, arguments: dict | None = None) -> dict:
    tool, args = validate(TOOLS, name, arguments)
    return tool.run(args)
