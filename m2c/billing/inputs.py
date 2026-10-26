"""
Synthetic inputs for the billing stage: the January 1, 2027 statements.

From the earlier stages
  Dec 1 bills        November's initial rating as production billed it (M3 still in the wrong zone)
  Zone credit        the rating fix for M3, issued Dec 15 as a credit on the November charges
  November true-up   corrected final minus corrected initial (metering's estimate replaced by actuals)
  December charges   December usage rated on the corrected setup (on-peak at the new $0.13 rate)

Read submissions
  When each meter's Dec 1 register read was submitted. The true-up only uses finals whose register read
  was in by the cutoff. Planted: M2's AMI read came in flagged, so a field verification read was ordered;
  the tech confirmed it Dec 28 and it was keyed Dec 29, after the Dec 28 17:00 cutoff. Nothing is wrong
  with the number; it arrived too late for the January 1 true-up.

Cash
  Bank remittance    what actually arrived, and when (the source of record)
  Payment ledger     what was keyed into billing. Planted: A-1001's partial payment received Dec 18
                     was keyed as Dec 28, a one-digit slip. Amount and reference are right.
"""
from datetime import date
import math

import numpy as np
import pandas as pd

from m2c.rating import engine
from m2c.rating.inputs import month_intervals

ACCOUNTS = [
    # account_id, meter_id, description
    ("A-1001", "M1", "Commercial"),
    ("A-1002", "M2", "Residential"),
    ("A-1003", "M3", "Industrial"),
]
NOV = (date(2026, 11, 1), date(2026, 11, 30))
DEC = (date(2026, 12, 1), date(2026, 12, 31))
DEC1, DUE = date(2026, 12, 1), date(2026, 12, 21)
CREDIT_DATE = date(2026, 12, 15)


def _corrected_master(prod):
    fix = prod.copy()
    sel = (fix["meter_id"] == "M3") & (fix["effective_from"] == "2026-11-01")
    fix.loc[sel, "zone"] = "LZ_HOUSTON"
    fix.loc[sel, "change_note"] = "Account update: service upgrade (zone corrected Dec 15)"
    return fix


def build(data_dir, rating_dir, usage_path, seed=7):
    rates = pd.read_csv(f"{rating_dir}/rates.csv", keep_default_na=False)
    rates["rate"] = rates["rate"].astype(float)
    prod = pd.read_csv(f"{rating_dir}/meter_master_production.csv", keep_default_na=False)
    fixed = _corrected_master(prod)
    usage = pd.read_csv(usage_path)
    ini, fin = usage[usage["version"] == "initial"], usage[usage["version"] == "final"]

    rate = engine.rate
    billed_initial = rate(ini, prod, rates, NOV, DEC1, "service", "production", "Nov initial (as billed)")
    corrected_initial = rate(ini, fixed, rates, NOV, DEC1, "service", "production", "Nov initial (corrected)")
    corrected_final = rate(fin, fixed, rates, NOV, date(2026, 12, 10), "service", "production", "Nov final (corrected)")
    dec_usage = month_intervals(2026, 12, np.random.default_rng(seed + 2000))
    dec_usage["version"] = "initial"
    december = rate(dec_usage, fixed, rates, DEC, date(2027, 1, 1), "service", "production", "Dec initial")
    lines = pd.concat([billed_initial, corrected_initial, corrected_final, december], ignore_index=True)

    acct = pd.DataFrame(ACCOUNTS, columns=["account_id", "meter_id", "description"])
    meter_of = acct.set_index("account_id")["meter_id"]
    bill = billed_initial.groupby("meter_id")["amount"].sum().round(2)
    dec1 = pd.DataFrame({
        "account_id": acct["account_id"], "statement_date": DEC1.isoformat(), "due_date": DUE.isoformat(),
        "amount_due": [bill[m] for m in acct["meter_id"]],
    })

    # Credit for the rating fix: what was billed minus what should have been billed, November initial
    over = (billed_initial.groupby("meter_id")["amount"].sum()
            - corrected_initial.groupby("meter_id")["amount"].sum()).round(2)
    credits = pd.DataFrame([{
        "credit_id": f"CR-{i + 1:04d}", "account_id": a, "effective_date": CREDIT_DATE.isoformat(),
        "amount": over[m], "reason": "Delivery zone corrected LZ_WEST to LZ_HOUSTON, November service",
    } for i, (a, m) in enumerate(zip(acct["account_id"], acct["meter_id"])) if over[m] > 0.004])

    a1_partial = math.floor(0.6 * bill[meter_of["A-1001"]] / 100) * 100
    bank = pd.DataFrame([
        ("2026-12-18", "A-1001", float(a1_partial), "ACH-20261218-0417"),
        ("2026-12-20", "A-1003", bill[meter_of["A-1003"]], "ACH-20261220-0552"),
        ("2026-12-29", "A-1002", bill[meter_of["A-1002"]], "CHK-20261229-1188"),
    ], columns=["received_date", "account_id", "amount", "reference"])

    ledger = bank.rename(columns={"received_date": "payment_date"}).copy()
    ledger["entered_on"] = ledger["payment_date"]
    ledger["entered_by"] = ["jlopez", "jlopez", "mchen"]
    ledger.loc[ledger["reference"] == "ACH-20261218-0417", "payment_date"] = "2026-12-28"   # the slip
    ledger = ledger[["account_id", "payment_date", "amount", "reference", "entered_on", "entered_by"]]

    reads = pd.DataFrame([
        ("M1", "2026-12-01 00:00", "AMI", "2026-12-01 00:00", "2026-12-01 06:10", ""),
        ("M2", "2026-12-01 00:00", "Field verification", "2026-12-28 14:40", "2026-12-29 09:14",
         "AMI read flagged (communication error); field verification ordered Dec 4"),
        ("M3", "2026-12-01 00:00", "AMI", "2026-12-01 00:00", "2026-12-01 06:10", ""),
    ], columns=["meter_id", "read_for", "read_type", "read_taken", "submitted_at", "note"])

    acct.to_csv(f"{data_dir}/accounts.csv", index=False)
    reads.to_csv(f"{data_dir}/read_submissions.csv", index=False)
    dec1.to_csv(f"{data_dir}/dec1_bills.csv", index=False)
    lines.to_csv(f"{data_dir}/rated_lines.csv", index=False)
    credits.to_csv(f"{data_dir}/credits.csv", index=False)
    bank.to_csv(f"{data_dir}/bank_remittance.csv", index=False)
    ledger.to_csv(f"{data_dir}/payment_ledger.csv", index=False)
    fixed.to_csv(f"{data_dir}/meter_master_corrected.csv", index=False)
