"""
Run the billing stage. Reads the metering and rating stages' outputs, so run those first.

    python run_metering.py
    python run_rating.py
    python run_billing.py
"""
import argparse
import os

import pandas as pd

from m2c.billing import checks, inputs, report, system

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    out = os.path.join(HERE, "output")
    data, rating = os.path.join(HERE, "data", "billing"), os.path.join(HERE, "data", "rating")
    usage = os.path.join(out, "interval_usage.csv")
    if not (os.path.exists(usage) and os.path.exists(os.path.join(rating, "rates.csv"))):
        raise SystemExit("Run `python run_metering.py` and `python run_rating.py` first.")
    os.makedirs(data, exist_ok=True)

    inputs.build(data, rating, usage, seed=args.seed)
    r = checks.run(system.load(data))

    pd.concat([r["billed"], r["corrected"]]).to_csv(os.path.join(out, "statements.csv"), index=False)
    chk = r["roll_forward"].merge(r["recalc"][["account_id", "billed_interest", "recalculated", "difference", "matches"]],
                                  on="account_id")
    lb = r["ledger_bank"][["account_id_ledger", "reference", "payment_date", "received_date", "date_matches"]]
    chk = chk.merge(lb.rename(columns={"account_id_ledger": "account_id"}), on="account_id", how="left")
    chk.to_csv(os.path.join(out, "billing_checks.csv"), index=False)
    with open(os.path.join(out, "billing_report.html"), "w", encoding="utf-8") as f:
        f.write(report.render(r, args.seed))
    print(r["recalc"][["account_id", "billed_interest", "recalculated", "difference"]].to_string(index=False))
    print(f"\nReport: {os.path.join(out, 'billing_report.html')}")


if __name__ == "__main__":
    main()
