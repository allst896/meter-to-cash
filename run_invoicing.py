"""
Run the invoicing stage. Reads the billing stage's output, so run the earlier stages first.

    python run_metering.py
    python run_rating.py
    python run_billing.py
    python run_invoicing.py
"""
import argparse
import json
import os
import shutil
import subprocess

import pandas as pd

from m2c.billing import system as billing
from m2c.invoicing import checks, pdf, report, system

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    out = os.path.join(HERE, "output")
    bdir = os.path.join(HERE, "data", "billing")
    if not os.path.exists(os.path.join(out, "statements.csv")):
        raise SystemExit("Run the metering, rating and billing stages first.")
    inv_dir = os.path.join(out, "invoices")
    os.makedirs(inv_dir, exist_ok=True)

    bd = billing.load(bdir)
    statements = pd.read_csv(os.path.join(out, "statements.csv"), keep_default_na=False)
    usage = pd.read_csv(os.path.join(out, "interval_usage.csv"))
    headers, lines, register = system.build(bd, statements, usage)

    for h in headers.to_dict("records"):
        path = os.path.join(inv_dir, f"{h['invoice_no']}.pdf")
        pdf.render(h, lines, path)
        if shutil.which("pdftoppm"):        # page-1 preview for the report (optional)
            subprocess.run(["pdftoppm", "-r", "60", "-png", "-f", "1", "-l", "1", "-singlefile", path,
                            os.path.join(inv_dir, h["invoice_no"])], check=False)

    r = checks.run(headers, lines, register, statements, bd["dec1"])
    out_lines = lines.copy()
    out_lines["detail"] = out_lines["detail"].map(lambda x: json.dumps(x) if x else "")
    out_lines.to_csv(os.path.join(out, "invoice_lines.csv"), index=False)
    summary = r["tie_out"][["invoice_no", "account_id", "passes"]].rename(columns={"passes": "tie_out"})
    for name, df in [("classification", r["classification"]), ("required_fields", r["required"]),
                     ("adjustment_references", r["references"]), ("customer_recompute", r["recompute"])]:
        agg = df.groupby("invoice_no")["passes"].all().rename(name)
        summary = summary.merge(agg, on="invoice_no", how="left")
    summary.fillna({"adjustment_references": True}).to_csv(os.path.join(out, "invoicing_checks.csv"), index=False)

    with open(os.path.join(out, "invoicing_report.html"), "w", encoding="utf-8") as f:
        f.write(report.render(r, args.seed, out))
    print(summary.to_string(index=False))
    print(f"\nReport: {os.path.join(out, 'invoicing_report.html')}")


if __name__ == "__main__":
    main()
