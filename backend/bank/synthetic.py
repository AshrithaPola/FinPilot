"""Seeded synthetic customer data for the fictional bank, and its loader.

    cd backend
    python -m bank.synthetic --database-url OWNER_URL [--seed 7] [--customers 50]

generate() is pure and deterministic: the same seed gives the same rows.
Twelve months (2025-07-01 to 2026-06-30) straddle the fee-schedule change
on 2026-02-01, so "why was this fee different?" has a real answer. Fee
amounts and rules come from FEE_SCHEDULES, the same numbers as the bank's
fee-schedule documents (data/bank/). Every customer gets one transaction
whose description carries a canary naming them, for leakage and red-team
tests.

load() writes as the database owner and never relies on bypassing RLS:
reference data (securities, prices) has no RLS, and each customer's rows
are written in one transaction bound to that customer (app.customer_id), so
the policies' WITH CHECK passes for any owner, Neon's included. Re-running
replaces each customer's rows: the customer row is upserted (users may
reference it) and its children are deleted and re-inserted.
"""
import argparse
import hashlib
import os
import random
import sys
from dataclasses import dataclass
from datetime import date, timedelta

from config import sqlalchemy_url

BANK_ORG = "finpilot-bank"
BANK_NAME = "FinPilot Bank"
START, END = date(2025, 7, 1), date(2026, 6, 30)
SCHEDULE_CHANGE = date(2026, 2, 1)


@dataclass(frozen=True)
class FeeSchedule:
    """Amounts in cents. Mirrors data/bank/fee-schedule-2025.md and -2026.md."""
    overdraft: int
    overdraft_max_per_day: int
    overdraft_cushion: int  # no overdraft fee while the balance stays above -cushion
    nsf: int
    maintenance: int
    maintenance_waiver_deposits: int  # direct deposits in the month that waive it
    atm_out_of_network: int
    foreign_transaction_bps: int
    late_payment: int


FEE_SCHEDULES = {
    date(2025, 1, 1): FeeSchedule(overdraft=3400, overdraft_max_per_day=3, overdraft_cushion=0,
                                  nsf=3400, maintenance=1200, maintenance_waiver_deposits=50000,
                                  atm_out_of_network=300, foreign_transaction_bps=300,
                                  late_payment=3900),
    SCHEDULE_CHANGE: FeeSchedule(overdraft=1500, overdraft_max_per_day=2, overdraft_cushion=5000,
                                 nsf=0, maintenance=1000, maintenance_waiver_deposits=25000,
                                 atm_out_of_network=350, foreign_transaction_bps=300,
                                 late_payment=4000),
}


def schedule_on(day: date) -> FeeSchedule:
    return FEE_SCHEDULES[max(d for d in FEE_SCHEDULES if d <= day)]


SECURITIES = [  # symbol, name, type, expense ratio; prices are real (bank/market.py, Alpha Vantage)
    ("VTI", "Vanguard Total Stock Market ETF", "etf", "0.0003"),
    ("VXUS", "Vanguard Total International Stock ETF", "etf", "0.0005"),
    ("BND", "Vanguard Total Bond Market ETF", "etf", "0.0003"),
    ("SCHD", "Schwab U.S. Dividend Equity ETF", "etf", "0.0006"),
]
# Each holding's purchase month (seeded per holding) and its cost basis: shares x that
# month's real Alpha Vantage close, read from the database at load time, so no
# Alpha Vantage price is committed (data/SOURCES.md). Price return only: no dividends.
PURCHASE_MONTHS = [date(y, m, 1) for y in range(2020, 2026) for m in range(1, 13) if date(y, m, 1) <= date(2025, 6, 1)]
# The monthly series isn't split-adjusted. SCHD's 3-for-1 split, confirmed by the data:
# month-end close $84.53 in Sep 2024, about $28 in Oct 2024.
SPLITS = {"SCHD": (date(2024, 10, 11), 3)}


def split_adjusted(symbol: str, price_date: date, close: float) -> float:
    """A close in today's shares: divided by the ratio of each later split."""
    for day, ratio in [SPLITS[symbol]] if symbol in SPLITS else []:
        if price_date < day:
            close /= ratio
    return close


def purchase_month(seed: int, customer_id: str, symbol: str) -> date:
    """Its own random stream, so the main one (every other row) is unchanged."""
    return random.Random(f"{seed}:{customer_id}:{symbol}:purchase").choice(PURCHASE_MONTHS)

# Fictional merchants: (name, category, low, high) in dollars.
MERCHANTS = [
    ("Corner Grocer", "groceries", 12, 140), ("FreshFields Market", "groceries", 20, 180),
    ("Bean & Barrel Coffee", "dining", 4, 15), ("Harbor Noodle House", "dining", 14, 60),
    ("QuickFuel", "transport", 25, 70), ("MetroRide Transit", "transport", 3, 40),
    ("Brightline Pharmacy", "health", 8, 90), ("Northside Hardware", "home", 15, 220),
    ("Paperleaf Books", "shopping", 9, 60), ("Atlas Outfitters", "shopping", 30, 250),
]
AVERAGE_PURCHASE = sum((low + high) / 2 for _, _, low, high in MERCHANTS) / len(MERCHANTS) * 100  # cents
SPEND_SHARE = 0.75
# Monthly subscriptions: (name, low, high) in dollars. Each customer has 1-3,
# each charged the same amount on the same day every month.
SUBSCRIPTIONS = [("StreamBox", 10, 18), ("CloudLocker Storage", 3, 10), ("TuneWave Music", 11, 17),
                 ("FitLoop Gym", 25, 55)]
FOREIGN_MERCHANTS = [("Café Lumière Paris", "travel", 15, 120), ("Kyoto Rail Pass", "travel", 60, 300)]
CARD_PURCHASE_ODDS = 0.15  # a card purchase on about 1 day in 7 (about 55 a year)
SEGMENTS = (("retail", 0.7), ("premier", 0.15), ("student", 0.15))
FIRST = ["Avery", "Jordan", "Riley", "Morgan", "Casey", "Quinn", "Rowan", "Sasha", "Devi", "Mateo",
         "Amara", "Leon", "Priya", "Tomas", "Nia", "Kenji"]
LAST = ["Okafor", "Lindqvist", "Moreau", "Tanaka", "Haddad", "Novak", "Reyes", "Abara",
        "Castillo", "Byrne", "Iyer", "Weller"]


def canary(customer_id: str) -> str:
    return "CANARY-" + hashlib.sha256(customer_id.encode()).hexdigest()[:12]


def _days(start: date, end: date):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def _cents(rng: random.Random, low: float, high: float) -> int:
    return int(round(rng.uniform(low, high) * 100))


def generate(seed: int = 7, customers: int = 50) -> dict[str, list[dict]]:
    """All rows, by table. Customer-owned rows carry customer_id."""
    rng = random.Random(seed)
    out: dict[str, list[dict]] = {t: [] for t in (
        "securities", "prices", "customers", "accounts", "cards", "transactions", "fees",
        "holdings")}
    out["securities"] = [{"symbol": s, "name": n, "type": t, "expense_ratio": e}
                         for s, n, t, e in SECURITIES]
    # Prices are real now (bank/market.py). The random draws that made the old
    # synthetic prices stay, so every customer's data after them is unchanged.
    for day in _days(START, END):
        if day.weekday() < 5:
            for _ in SECURITIES:
                rng.gauss(0.0003, 0.009)

    for n in range(1, customers + 1):
        cid = f"cust-{n:04d}"
        segment = rng.choices([s for s, _ in SEGMENTS], [w for _, w in SEGMENTS])[0]
        out["customers"].append({
            "customer_id": cid, "org_id": BANK_ORG, "segment": segment,
            "full_name": f"{rng.choice(FIRST)} {rng.choice(LAST)}",
            "joined_on": START - timedelta(days=rng.randint(30, 3000))})
        _customer_rows(rng, cid, segment, out, random.Random(f"{seed}:{cid}:start"), seed)
    return out


def _statement(rng, due: date, amount: int, late_payer: bool) -> dict:
    """A card statement: what's due, when, the minimum payment (the greater of
    $25.00 or 1%, never more than the balance) and whether it's paid late."""
    late = late_payer and rng.random() < 0.35
    return {"due": due, "amount": amount, "minimum": min(amount, max(2500, amount // 100)), "late": late,
            "pay_on": due + timedelta(days=rng.randint(3, 12)) if late else due}


def _customer_rows(rng, cid, segment, out, start_rng, seed: int) -> None:
    """One customer's rows. Starting balances (on START) come from
    `start_rng`, a stream of their own, so adding them changed no other row
    (and none of the receipts drawn from these transactions)."""
    opened = START - timedelta(days=rng.randint(30, 900))
    checking, savings, credit = f"{cid}-chk", f"{cid}-sav", f"{cid}-cc"
    accounts = [(checking, "checking", "Everyday Checking"), (savings, "savings", "High-Yield Savings"),
                (credit, "credit", "Cash Back Card")]
    if segment == "premier":
        accounts.append((f"{cid}-brk", "brokerage", "Self-Directed Investing"))
    savings_range = {"retail": (500, 8000), "premier": (10000, 60000), "student": (50, 1500)}[segment]
    starting = {savings: _cents(start_rng, *savings_range),
                credit: -_cents(start_rng, 0, 900),  # owed on the card
                f"{cid}-brk": 0}  # cash; the holdings are valued by the market tools
    rows = {}
    for account_id, kind, product in accounts:
        rows[account_id] = {"account_id": account_id, "customer_id": cid, "type": kind, "product": product,
                            "opened_on": opened, "status": "open",
                            "starting_balance_cents": starting.get(account_id, 0), "starting_balance_on": START}
        out["accounts"].append(rows[account_id])
    debit_card, credit_card = f"{cid}-dc", f"{cid}-cc1"
    for card_id, account_id, network in ((debit_card, checking, "visa"),
                                         (credit_card, credit, "mastercard")):
        out["cards"].append({"card_id": card_id, "account_id": account_id, "customer_id": cid,
                             "last4": f"{rng.randint(0, 9999):04d}", "network": network,
                             "status": "active", "expires_on": date(2029, rng.randint(1, 12), 28)})

    seq = iter(range(1, 10**6))

    def txn(day, account, amount, category, description, merchant=None, card=None) -> dict:
        row = {"txn_id": f"{cid}-t{next(seq):05d}", "account_id": account, "customer_id": cid,
               "card_id": card, "posted_on": day, "amount_cents": amount, "merchant": merchant,
               "category": category, "description": description}
        out["transactions"].append(row)
        return row

    def fee(day, account, fee_type, amount, txn_id=None, waived=False) -> None:
        out["fees"].append({"fee_id": f"{cid}-f{next(seq):05d}", "account_id": account,
                            "customer_id": cid, "txn_id": txn_id, "fee_type": fee_type,
                            "amount_cents": amount, "assessed_on": day, "waived": waived})

    direct_deposit = segment != "student" or rng.random() < 0.3
    pay = {"retail": (1400, 2600), "premier": (3500, 6500), "student": (300, 700)}[segment]
    paycheck = _cents(rng, *pay)
    rent = int(paycheck * rng.uniform(0.6, 0.9))
    balance = _cents(rng, 200, 3000) if segment != "premier" else _cents(rng, 4000, 15000)
    rows[checking]["starting_balance_cents"] = balance  # what the simulation below starts from
    # Card spending is a share of what pay leaves after rent, so most months end
    # ahead and overdrafts are occasional (a big week before payday), not chronic.
    spend_share = {"retail": SPEND_SHARE, "premier": SPEND_SHARE - 0.15, "student": SPEND_SHARE + 0.05}[segment]
    per_day = max(0, paycheck * 26 / 12 - rent) * spend_share / 30 / AVERAGE_PURCHASE
    per_day = max(0.0, per_day - CARD_PURCHASE_ODDS)  # card purchases come out of the same budget
    purchase_odds = min(1.0, per_day / 3)  # up to 3 purchases a day
    deposits_this_month, overdrafts_today, day_seen = 0, 0, None
    cushion = int(paycheck * rng.uniform(0.6, 1.2))  # kept in checking above next month's rent
    subscriptions = [(name, _cents(rng, low, high), rng.randint(1, 28))
                     for name, low, high in rng.sample(SUBSCRIPTIONS, rng.randint(1, 3))]
    txn(START, checking, 100, "interest", f"Statement memo ref {canary(cid)}")
    # The card (cardmember agreement): a cycle closes at month end, payment is
    # due on the 25th of the next month, and autopay pays the statement
    # balance from checking. About one customer in seven sometimes misses
    # autopay and pays by hand 3 to 12 days late, and is charged a late fee
    # under the schedule in force (never more than the minimum payment due).
    late_payer = rng.random() < 0.15
    card_owed = -rows[credit]["starting_balance_cents"]  # the June statement, due July 25
    statement = _statement(rng, START.replace(day=25), card_owed, late_payer) if card_owed else None

    for day in _days(START, END):
        sched = schedule_on(day)
        if day != day_seen:
            overdrafts_today, day_seen = 0, day
        if day.weekday() == 4 and (day - START).days // 7 % 2 == 0:  # every other Friday
            if direct_deposit:
                txn(day, checking, paycheck, "income", "Direct deposit PAYROLL", "Employer Payroll")
                deposits_this_month += paycheck
            else:  # paid by check: same pay, but it doesn't waive the maintenance fee
                txn(day, checking, paycheck, "income", "Mobile check deposit")
            balance += paycheck
        debits = []
        if day.day == 1:
            debits.append((rent, "housing", "Rent payment", "Maple Court Apartments"))
        debits += [(amount, "subscriptions", f"Recurring payment {name}", name)
                   for name, amount, on_day in subscriptions if day.day == on_day]
        if statement and day == statement["pay_on"]:
            debits.append((statement["amount"], "card_payment",
                           "Cash Back Card payment" if statement["late"] else "Cash Back Card autopay", None))
        if statement and statement["late"] and day == statement["due"] + timedelta(days=1):
            late_fee = min(sched.late_payment, statement["minimum"])
            fee(day, credit, "late_payment", late_fee)
            card_owed += late_fee
        # A debit card is declined once the account is $200 overdrawn.
        purchases = sum(rng.random() < purchase_odds for _ in range(3))
        for _ in range(purchases if balance > -20000 else 0):
            name, category, low, high = rng.choice(MERCHANTS)
            debits.append((_cents(rng, low, high), category, f"Card purchase {name}", name))
        for amount, category, description, merchant in debits:
            row = txn(day, checking, -amount, category, description, merchant,
                      debit_card if category not in ("housing", "card_payment") else None)
            balance -= amount
            if category == "card_payment":
                txn(day, credit, amount, "payment", "Payment, thank you")
                card_owed -= amount
                statement = None
            # One fee per item that leaves the account overdrawn past the
            # cushion, up to the daily cap.
            if balance < -sched.overdraft_cushion and overdrafts_today < sched.overdraft_max_per_day:
                fee(day, checking, "overdraft", sched.overdraft, row["txn_id"])
                balance -= sched.overdraft
                overdrafts_today += 1
        if rng.random() < 0.012:  # out-of-network ATM
            amount = rng.choice([2000, 4000, 6000])
            row = txn(day, checking, -amount, "cash", "ATM withdrawal (out of network)",
                      "Riverside ATM", debit_card)
            balance -= amount
            fee(day, checking, "atm", sched.atm_out_of_network, row["txn_id"])
            balance -= sched.atm_out_of_network
        if rng.random() < CARD_PURCHASE_ODDS:  # credit card purchase, sometimes abroad
            name, category, low, high = rng.choice(FOREIGN_MERCHANTS if rng.random() < 0.05 else MERCHANTS)
            amount = _cents(rng, low, high)
            row = txn(day, credit, -amount, category, f"Card purchase {name}", name, credit_card)
            card_owed += amount
            if (name, category, low, high) in FOREIGN_MERCHANTS:
                foreign = max(1, amount * sched.foreign_transaction_bps // 10_000)
                fee(day, credit, "foreign_transaction", foreign, row["txn_id"])
                card_owed += foreign
        next_day = day + timedelta(days=1)
        if next_day.month != day.month:  # month end
            # Sweep what's above next month's rent plus a cushion to savings, so
            # checking stays near the same level all year (and so does the
            # chance of an overdraft, under either fee schedule).
            if balance > rent + cushion and rng.random() < 0.6:
                amount = balance - rent - cushion
                txn(day, checking, -amount, "transfer", "Transfer to savings")
                txn(day, savings, amount, "transfer", "Transfer from checking")
                balance -= amount
            if deposits_this_month < sched.maintenance_waiver_deposits and segment != "student":
                fee(day, checking, "monthly_maintenance", sched.maintenance)
                balance -= sched.maintenance
            elif segment == "student":
                fee(day, checking, "monthly_maintenance", sched.maintenance, waived=True)
            if card_owed > 0 and statement is None:  # the cycle closes: next due on the 25th
                statement = _statement(rng, next_day.replace(day=25), card_owed, late_payer)
            deposits_this_month = 0

    if segment == "premier":
        for symbol in rng.sample([s for s, *_ in SECURITIES], rng.randint(1, 3)):
            quantity = round(rng.uniform(5, 120), 3)
            rng.uniform(0.8, 1.05)  # the old cost-basis factor: unused, drawn so later rows stay the same
            # cost_basis_cents and purchased_on are set by load(), from the real close.
            out["holdings"].append({"account_id": f"{cid}-brk", "customer_id": cid,
                                    "symbol": symbol, "quantity": quantity,
                                    "purchase_month": purchase_month(seed, cid, symbol)})


# --- loading ----------------------------------------------------------------------

CHILDREN = ("accounts", "cards", "transactions", "fees", "holdings")  # insert order


def _insert(conn, table: str, rows: list[dict]) -> None:
    """Multi-row INSERTs (a Core insert() runs as batches of VALUES rows), not
    text(), which psycopg2 runs as one round trip per row: over the network to
    Neon that was nearly the whole load time."""
    import sqlalchemy as sa

    if rows:
        conn.execute(sa.insert(sa.table(table, *map(sa.column, rows[0]))), rows)


def priced_holdings(holdings: list[dict], closes: dict[tuple[str, date], tuple[date, float]]) -> list[dict]:
    """Each holding with purchased_on (its month's last close date) and
    cost_basis_cents = shares x that close, split-adjusted. `closes` maps
    (symbol, first of month) to (price_date, close)."""
    out, missing = [], sorted({(h["symbol"], h["purchase_month"]) for h in holdings} - set(closes))
    if missing:
        raise RuntimeError("No Alpha Vantage close for " + ", ".join(f"{s} {m:%Y-%m}" for s, m in missing)
                           + ": fetch prices first (python -m bank.market fetch prices).")
    for h in holdings:
        day, close = closes[(h["symbol"], h["purchase_month"])]
        row = {k: v for k, v in h.items() if k != "purchase_month"}
        out.append({**row, "purchased_on": day,
                    "cost_basis_cents": round(h["quantity"] * split_adjusted(h["symbol"], day, close) * 100)})
    return out


def _purchase_closes(conn) -> dict[tuple[str, date], tuple[date, float]]:
    import sqlalchemy as sa

    return {(s, d.replace(day=1)): (d, float(c)) for s, d, c in conn.execute(sa.text(
        "SELECT symbol, price_date, close FROM prices WHERE source = 'alphavantage' "
        "AND price_date >= :a AND price_date < :b"), {"a": PURCHASE_MONTHS[0], "b": date(2025, 7, 1)})}


def load(owner_url: str, data: dict[str, list[dict]]) -> dict[str, int]:
    """Write `data` (from generate) as the owner. Returns rows written per table."""
    import sqlalchemy as sa

    engine = sa.create_engine(sqlalchemy_url(owner_url))
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("INSERT INTO orgs VALUES (:o, :n) ON CONFLICT (org_id) DO NOTHING"),
                         {"o": BANK_ORG, "n": BANK_NAME})
            conn.execute(sa.text("""
                INSERT INTO securities VALUES (:symbol, :name, :type, CAST(:expense_ratio AS numeric))
                ON CONFLICT (symbol) DO UPDATE SET name = EXCLUDED.name, type = EXCLUDED.type,
                    expense_ratio = EXCLUDED.expense_ratio
            """), data["securities"])
            # Removes the synthetic prices older loads wrote; fetched ones (Alpha Vantage) stay.
            conn.execute(sa.text("DELETE FROM prices WHERE source = 'synthetic'"))
            _insert(conn, "prices", data["prices"])
            holdings = priced_holdings(data["holdings"], _purchase_closes(conn))
        by_customer: dict[str, dict[str, list[dict]]] = {
            c["customer_id"]: {t: [] for t in CHILDREN} for c in data["customers"]}
        for table in CHILDREN:
            for row in (holdings if table == "holdings" else data[table]):
                by_customer[row["customer_id"]][table].append(row)
        for customer in data["customers"]:
            cid = customer["customer_id"]
            with engine.begin() as conn:
                conn.execute(sa.text("SELECT set_config('app.customer_id', :c, true)"), {"c": cid})
                conn.execute(sa.text("""
                    INSERT INTO customers VALUES (:customer_id, :org_id, :full_name, :segment,
                                                  :joined_on)
                    ON CONFLICT (customer_id) DO UPDATE SET full_name = EXCLUDED.full_name,
                        segment = EXCLUDED.segment, joined_on = EXCLUDED.joined_on
                """), customer)
                # Receipt extractions describe this customer's old transactions;
                # reloaded ones may differ, so they go too (re-extract after).
                conn.execute(sa.text("DELETE FROM receipt_images WHERE customer_id = :c"), {"c": cid})
                for table in reversed(CHILDREN):
                    # Explicit filter: an owner with BYPASSRLS (or a superuser)
                    # isn't limited by the policy.
                    conn.execute(sa.text(f"DELETE FROM {table} WHERE customer_id = :c"), {"c": cid})
                for table in CHILDREN:
                    _insert(conn, table, by_customer[cid][table])
    finally:
        engine.dispose()
    return {t: len(rows) for t, rows in data.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate and load the synthetic customer data.")
    parser.add_argument("--database-url", default=os.environ.get("MIGRATION_DATABASE_URL"),
                        help="Owner Postgres URL (default: MIGRATION_DATABASE_URL)")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--customers", type=int, default=50)
    args = parser.parse_args()
    if not args.database_url:
        print("Pass --database-url (the owner) or set MIGRATION_DATABASE_URL.", file=sys.stderr)
        return 2
    counts = load(args.database_url, generate(args.seed, args.customers))
    print(", ".join(f"{t} {n}" for t, n in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
