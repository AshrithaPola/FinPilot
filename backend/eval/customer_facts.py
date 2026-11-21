"""The demo customers' fact sheet (eval/customer_facts.md), an independent check.

    backend/.venv/Scripts/python scripts/neon.py run -- python -m eval.customer_facts

Plain SQL as the database owner (MIGRATION_DATABASE_URL), filtered by
customer id in the query itself: none of the app's tools, its access
layer or its balance code, so a number taken from here checks the app
rather than repeating it. Read-only. Data up to DEMO_TODAY.
"""
import os
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import sqlalchemy as sa

DEMO_CUSTOMERS = ("cust-0036", "cust-0019", "cust-0011")
OUT = Path(__file__).with_name("customer_facts.md")
MONTHS = 3  # spending by category for the last three months
LARGEST = 10  # largest money-out transactions listed


def usd(cents) -> str:
    cents = int(Decimal(str(cents)).quantize(Decimal(1), ROUND_HALF_UP))  # fractional cents (prices) round half up
    return f"{'-' if cents < 0 else ''}${Decimal(abs(cents)) / 100:,.2f}"


def _table(head: list[str], rows: list[list]) -> list[str]:
    if not rows:
        return ["None.", ""]
    return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head),
            *("| " + " | ".join(str(c) for c in r) + " |" for r in rows), ""]


def _months(today) -> list[str]:
    """The last MONTHS calendar months up to today, oldest first, as YYYY-MM."""
    y, m, out = today.year, today.month, []
    for _ in range(MONTHS):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out[::-1]


def sheet(conn, customer: str, today) -> list[str]:
    q = lambda sql, **p: conn.execute(sa.text(sql), {"c": customer, "today": today, **p}).mappings().all()  # noqa: E731
    [who] = q("SELECT full_name, segment, joined_on FROM customers WHERE customer_id = :c")
    out = [f"## {customer}", "", f"Name: {who['full_name']}. Segment: {who['segment']}. Joined: {who['joined_on']}.", ""]

    out += ["### Accounts and balances", ""]
    accounts = q("""
        SELECT a.account_id, a.type, a.product, a.status, a.starting_balance_cents, a.starting_balance_on,
               a.starting_balance_cents
                 + coalesce((SELECT sum(t.amount_cents) FROM transactions t
                             WHERE t.account_id = a.account_id AND t.customer_id = :c AND t.posted_on <= :today), 0)
                 - coalesce((SELECT sum(f.amount_cents) FROM fees f
                             WHERE f.account_id = a.account_id AND f.customer_id = :c AND NOT f.waived
                               AND f.assessed_on <= :today), 0) AS balance
        FROM accounts a WHERE a.customer_id = :c ORDER BY a.account_id""")
    out += _table(["Account", "Type", "Product", "Status", "Starting balance", "Balance on " + str(today)],
                  [[a["account_id"], a["type"], a["product"], a["status"],
                    f"{usd(a['starting_balance_cents'])} on {a['starting_balance_on']}", usd(a["balance"])]
                   for a in accounts])

    out += ["### Fees charged", ""]
    fees = q("""
        SELECT f.assessed_on, f.amount_cents, f.fee_type, f.waived, f.account_id,
               t.txn_id, t.posted_on, t.amount_cents AS txn_amount, coalesce(t.merchant, t.description) AS txn_what
        FROM fees f LEFT JOIN transactions t ON t.txn_id = f.txn_id AND t.customer_id = :c
        WHERE f.customer_id = :c AND f.assessed_on <= :today ORDER BY f.assessed_on, f.fee_id""")
    out += _table(["Date", "Amount", "Type", "Waived", "Account", "Caused by"],
                  [[f["assessed_on"], usd(f["amount_cents"]), f["fee_type"], "yes" if f["waived"] else "no",
                    f["account_id"],
                    f"{f['txn_id']}, {f['posted_on']}, {usd(f['txn_amount'])}, {f['txn_what']}" if f["txn_id"] else "-"]
                   for f in fees])

    months = _months(today)
    out += [f"### Money out by category, {months[0]} to {months[-1]}", ""]
    spend = q("""
        SELECT category, to_char(posted_on, 'YYYY-MM') AS month, -sum(amount_cents) AS out_cents
        FROM transactions
        WHERE customer_id = :c AND amount_cents < 0 AND posted_on <= :today
          AND to_char(posted_on, 'YYYY-MM') = ANY(:months)
        GROUP BY category, month""", months=months)
    by = {}
    for r in spend:
        by.setdefault(r["category"], {})[r["month"]] = r["out_cents"]
    out += _table(["Category", *months, "Total"],
                  [[cat, *(usd(by[cat].get(m, 0)) for m in months), usd(sum(by[cat].values()))]
                   for cat in sorted(by)])

    out += [f"### Largest money-out transactions (top {LARGEST})", ""]
    largest = q(f"""
        SELECT txn_id, posted_on, amount_cents, account_id, category, coalesce(merchant, description) AS what
        FROM transactions WHERE customer_id = :c AND amount_cents < 0 AND posted_on <= :today
        ORDER BY amount_cents, posted_on, txn_id LIMIT {LARGEST}""")
    out += _table(["Date", "Amount", "Account", "Category", "What", "Transaction"],
                  [[t["posted_on"], usd(t["amount_cents"]), t["account_id"], t["category"], t["what"], t["txn_id"]]
                   for t in largest])

    out += ["### Card payments and late fees", ""]
    payments = q("""
        SELECT posted_on, amount_cents, account_id, description FROM transactions
        WHERE customer_id = :c AND category = 'card_payment' AND posted_on <= :today ORDER BY posted_on""")
    out += _table(["Date", "Amount", "From account", "Description"],
                  [[p["posted_on"], usd(p["amount_cents"]), p["account_id"], p["description"]] for p in payments])
    late = q("""
        SELECT assessed_on, amount_cents, account_id, waived FROM fees
        WHERE customer_id = :c AND fee_type = 'late_payment' AND assessed_on <= :today ORDER BY assessed_on""")
    out += _table(["Late fee date", "Amount", "Account", "Waived"],
                  [[f["assessed_on"], usd(f["amount_cents"]), f["account_id"], "yes" if f["waived"] else "no"]
                   for f in late])

    out += [f"### Holdings, priced on {today}", ""]
    holdings = q("""
        SELECT h.account_id, h.symbol, s.name, h.quantity, h.cost_basis_cents, p.price_date, p.close, p.source
        FROM holdings h JOIN securities s USING (symbol)
        LEFT JOIN LATERAL (SELECT price_date, close, source FROM prices
                           WHERE symbol = h.symbol AND price_date <= :today
                           ORDER BY price_date DESC LIMIT 1) p ON true
        WHERE h.customer_id = :c ORDER BY h.symbol""")
    rows = []
    for h in holdings:
        value = (Decimal(str(h["quantity"])) * Decimal(str(h["close"])) * 100).quantize(Decimal(1), ROUND_HALF_UP) if h["close"] else None
        rows.append([h["symbol"], h["name"], h["quantity"], usd(h["cost_basis_cents"]),
                     f"{usd(Decimal(str(h['close'])) * 100)} on {h['price_date']} ({h['source']})" if h["close"] else "-",
                     usd(value) if value is not None else "-", h["account_id"]])
    out += _table(["Symbol", "Name", "Quantity", "Cost basis", "Close", "Market value", "Account"], rows)
    return out


def main() -> int:
    from config import get_settings, sqlalchemy_url

    owner = os.environ.get("MIGRATION_DATABASE_URL")
    if not owner:
        print("Set MIGRATION_DATABASE_URL (the owner), e.g. through scripts/neon.py run.", file=sys.stderr)
        return 2
    today = get_settings().demo_today
    engine = sa.create_engine(sqlalchemy_url(owner))
    lines = ["# Demo customers: fact sheet", "",
             f"Generated by `python -m eval.customer_facts` with plain SQL as the database owner. Data up to {today}.",
             "Amounts in dollars; negative is money out (or owed on a card).", ""]
    try:
        with engine.connect() as conn:
            conn.execute(sa.text("SET TRANSACTION READ ONLY"))
            for customer in DEMO_CUSTOMERS:
                lines += sheet(conn, customer, today)
    finally:
        engine.dispose()
    OUT.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"wrote {OUT.name}: {len(lines)} lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
