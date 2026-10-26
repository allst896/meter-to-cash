"""
The billing system: builds each account's January 1 statement.

Terms: 18% a year, actual days / 365, accrued daily on the unpaid December 1 balance after a 5-day
grace period (from Dec 26). Payments and credits reduce the balance from their dates (credits as of
their effective date, before interest). No interest on the true-up: it was not billed before Jan 1.

The system takes payment dates from the payment ledger, as keyed.
"""
from datetime import date, timedelta

import pandas as pd

RATE, BASIS, GRACE_DAYS = 0.18, 365, 5
STATEMENT_DATE = date(2027, 1, 1)
NEW_DUE = date(2027, 1, 21)


def interest_daily(prior, due, reductions, statement_date=STATEMENT_DATE):
    """Accrue day by day on the balance left after reductions dated on or before that day."""
    start = due + timedelta(days=GRACE_DAYS)
    total, d = 0.0, start
    while d < statement_date:
        bal = prior - sum(amt for dt, amt in reductions if dt <= d)
        if bal > 0:
            total += bal * RATE / BASIS
        d += timedelta(days=1)
    return round(total, 2)


def load(data_dir):
    rd = lambda f: pd.read_csv(f"{data_dir}/{f}", keep_default_na=False)
    acct, dec1, lines = rd("accounts.csv"), rd("dec1_bills.csv"), rd("rated_lines.csv")
    credits = rd("credits.csv") if open(f"{data_dir}/credits.csv").read().strip() else pd.DataFrame(
        columns=["credit_id", "account_id", "effective_date", "amount", "reason"])
    return dict(accounts=acct, dec1=dec1, lines=lines, credits=credits,
                bank=rd("bank_remittance.csv"), ledger=rd("payment_ledger.csv"))


def statements(d, payments=None, version="as billed"):
    """Statement lines per account. `payments` defaults to the ledger (account, payment_date, amount, reference)."""
    payments = d["ledger"] if payments is None else payments
    lines, out = d["lines"], []
    meter_of = d["accounts"].set_index("account_id")["meter_id"]
    for a in d["accounts"]["account_id"]:
        m = meter_of[a]
        b = d["dec1"].set_index("account_id").loc[a]
        prior, due = float(b["amount_due"]), date.fromisoformat(b["due_date"])
        pays = payments[payments["account_id"] == a]
        creds = d["credits"][d["credits"]["account_id"] == a]
        red = ([(date.fromisoformat(p.payment_date), float(p.amount)) for p in pays.itertuples()]
               + [(date.fromisoformat(c.effective_date), float(c.amount)) for c in creds.itertuples()])
        interest = interest_daily(prior, due, red)
        amt = lambda run: lines[(lines["run"] == run) & (lines["meter_id"] == m)]["amount"].sum()
        current = round(amt("Dec initial"), 2)
        true_up = round(amt("Nov final (corrected)") - amt("Nov initial (corrected)"), 2)

        rows = [("PRIOR", "Prior balance (Dec 1 statement)", "", prior)]
        rows += [("PAYMENT", f"Payment received {p.payment_date} ({p.reference})", p.payment_date, -float(p.amount))
                 for p in pays.itertuples()]
        rows += [("CREDIT", f"Credit {c.credit_id}: {c.reason}", c.effective_date, -float(c.amount))
                 for c in creds.itertuples()]
        rows += [("INTEREST", f"Interest at {RATE:.0%}/yr after {GRACE_DAYS}-day grace", "", interest),
                 ("CURRENT", "Current charges, December service", "", current),
                 ("TRUE_UP", "True-up, November service (prior-period adjustment)", "", true_up)]
        total = round(sum(r[3] for r in rows), 2)
        rows.append(("AMOUNT_DUE", f"Amount due by {NEW_DUE.isoformat()}", "", total))
        for i, (code, desc, dt, amount) in enumerate(rows):
            out.append({"version": version, "account_id": a, "meter_id": m,
                        "statement_date": STATEMENT_DATE.isoformat(), "line": i + 1, "line_type": code,
                        "description": desc, "line_date": dt, "amount": round(amount, 2)})
    return pd.DataFrame(out)
