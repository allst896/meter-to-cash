"""
Billing checks, in the spec's order.

  1. Balance roll-forward             statement lines must tie to the amount due
  2. Independent interest recalc      outside the billing system, from the terms and the cash actually
                                      received (bank remittance), by balance periods
  3. Interest by balance period       billed vs recalculated, side by side: where do the periods differ?
  4. Ledger against bank remittance   which record is wrong?

Plus two tie-outs that connect the stages: the December 1 bill, the zone credit and the true-up add up
to November's corrected final rating, and the interest base excludes the true-up.
"""
from datetime import date, timedelta

import pandas as pd

from m2c.billing import system as S


# ------------------------------------------------------------------ 1
def roll_forward(st, d):
    rows = []
    for a, g in st.groupby("account_id"):
        body = g[g["line_type"] != "AMOUNT_DUE"]["amount"].sum()
        printed = g[g["line_type"] == "AMOUNT_DUE"]["amount"].iloc[0]
        prior_ok = abs(g[g["line_type"] == "PRIOR"]["amount"].iloc[0]
                       - float(d["dec1"].set_index("account_id").loc[a, "amount_due"])) < 0.005
        pays = -g[g["line_type"] == "PAYMENT"]["amount"].sum()
        ledger = d["ledger"][d["ledger"]["account_id"] == a]["amount"].astype(float).sum()
        rows.append({"account_id": a, "lines_total": round(body, 2), "amount_due": printed,
                     "ties": abs(body - printed) < 0.005, "prior_matches_dec1": prior_ok,
                     "payments_match_ledger": abs(pays - ledger) < 0.005})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ 2 and 3
def balance_periods(prior, due, reductions, statement_date=S.STATEMENT_DATE):
    """Segments of constant balance inside the interest window."""
    start = due + timedelta(days=S.GRACE_DAYS)
    cuts = sorted({start, statement_date} | {dt for dt, _ in reductions if start < dt < statement_date})
    segs = []
    for a, b in zip(cuts[:-1], cuts[1:]):
        bal = prior - sum(amt for dt, amt in reductions if dt <= a)
        days = (b - a).days
        segs.append({"from": a, "to": b, "balance": round(bal, 2), "days": days,
                     "interest": max(bal, 0) * S.RATE / S.BASIS * days})
    return segs


def _reductions(d, a, cash, date_col):
    p = cash[cash["account_id"] == a]
    c = d["credits"][d["credits"]["account_id"] == a]
    return ([(date.fromisoformat(x[date_col]), float(x["amount"])) for _, x in p.iterrows()]
            + [(date.fromisoformat(x["effective_date"]), float(x["amount"])) for _, x in c.iterrows()])


def interest_recalc(st, d):
    rows, periods = [], {}
    dec1 = d["dec1"].set_index("account_id")
    for a in d["accounts"]["account_id"]:
        prior, due = float(dec1.loc[a, "amount_due"]), date.fromisoformat(dec1.loc[a, "due_date"])
        billed_segs = balance_periods(prior, due, _reductions(d, a, d["ledger"], "payment_date"))
        recalc_segs = balance_periods(prior, due, _reductions(d, a, d["bank"], "received_date"))
        billed = st[(st["account_id"] == a) & (st["line_type"] == "INTEREST")]["amount"].iloc[0]
        recalc = round(sum(s["interest"] for s in recalc_segs), 2)
        rows.append({"account_id": a, "billed_interest": billed, "recalculated": recalc,
                     "difference": round(billed - recalc, 2), "matches": abs(billed - recalc) < 0.005,
                     "billed_breakpoints": [s["from"] for s in billed_segs[1:]],
                     "recalc_breakpoints": [s["from"] for s in recalc_segs[1:]]})
        periods[a] = {"billed": billed_segs, "recalc": recalc_segs}
    return pd.DataFrame(rows), periods


# ------------------------------------------------------------------ 4
def ledger_vs_bank(d):
    m = d["ledger"].merge(d["bank"], on="reference", how="outer", suffixes=("_ledger", "_bank"))
    m["amount_matches"] = (m["amount_ledger"].astype(float) - m["amount_bank"].astype(float)).abs() < 0.005
    m["date_matches"] = m["payment_date"] == m["received_date"]
    m["dated_after_entry"] = m["payment_date"] > m["entered_on"]
    m["days_off"] = (pd.to_datetime(m["payment_date"]) - pd.to_datetime(m["received_date"])).dt.days
    return m


# ------------------------------------------------------------------ tie-outs
def chain_tie_out(d):
    lines, meter_of = d["lines"], d["accounts"].set_index("account_id")["meter_id"]
    rows = []
    for a in d["accounts"]["account_id"]:
        m = meter_of[a]
        amt = lambda run: round(lines[(lines["run"] == run) & (lines["meter_id"] == m)]["amount"].sum(), 2)
        billed, fin = amt("Nov initial (as billed)"), amt("Nov final (corrected)")
        credit = float(d["credits"][d["credits"]["account_id"] == a]["amount"].astype(float).sum())
        true_up = round(fin - amt("Nov initial (corrected)"), 2)
        net = round(billed - credit + true_up, 2)
        rows.append({"account_id": a, "dec1_billed": billed, "credit": credit, "true_up": true_up,
                     "net_november": net, "corrected_final": fin, "ties": abs(net - fin) <= 0.011})
    return pd.DataFrame(rows)


def run(d):
    billed = S.statements(d)
    rf = roll_forward(billed, d)
    recalc, periods = interest_recalc(billed, d)
    lb = ledger_vs_bank(d)
    # Fix: take payment dates from the bank remittance for the records that disagree
    fixed_payments = d["ledger"].merge(d["bank"][["reference", "received_date"]], on="reference")
    fixed_payments["payment_date"] = fixed_payments["received_date"]
    corrected = S.statements(d, fixed_payments, version="corrected")
    chain = chain_tie_out(d)
    rf_corrected = roll_forward(corrected, d)
    return dict(billed=billed, corrected=corrected, roll_forward=rf, roll_forward_corrected=rf_corrected,
                recalc=recalc, periods=periods, ledger_bank=lb, chain=chain, data=d)
