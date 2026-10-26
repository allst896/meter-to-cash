"""
Run the collection stage. Reads the earlier stages' output, so run them first.

    python run_metering.py
    python run_rating.py
    python run_billing.py
    python run_invoicing.py
    python run_collection.py
"""
import argparse
import json
import os
import shutil
import subprocess

import pandas as pd

from m2c.billing import system as billing
from m2c.collection import checks, inputs, report
from m2c.collection.reissue import reissue
from m2c.invoicing import pdf
from m2c.invoicing import system as invoicing

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    out = os.path.join(HERE, "output")
    if not os.path.exists(os.path.join(out, "invoicing_report.html")):
        raise SystemExit("Run the metering, rating, billing and invoicing stages first.")
    cdir = os.path.join(HERE, "data", "collection")
    os.makedirs(cdir, exist_ok=True)

    bd = billing.load(os.path.join(HERE, "data", "billing"))
    statements = pd.read_csv(os.path.join(out, "statements.csv"), keep_default_na=False)
    usage = pd.read_csv(os.path.join(out, "interval_usage.csv"))
    register = pd.read_csv(os.path.join(HERE, "data", "register_reads.csv"))
    headers, lines, _ = invoicing.build(bd, statements, usage)

    inputs.build(cdir, headers, lines)
    d = inputs.load(cdir)
    r = checks.run(d, headers, lines, bd, statements, usage, register)

    hdr, rlines, refs = reissue(bd, statements, usage, r["cash"])
    path = os.path.join(out, "invoices", f"{hdr['invoice_no']}.pdf")
    pdf.render(hdr, rlines, path)
    if shutil.which("pdftoppm"):
        subprocess.run(["pdftoppm", "-r", "60", "-png", "-f", "1", "-l", "1", "-singlefile", path,
                        os.path.join(out, "invoices", hdr["invoice_no"])], check=False)
    r["reissue"] = dict(header=hdr, references=refs)

    r["cash"].to_csv(os.path.join(out, "cash_application.csv"), index=False)
    r["reads"].to_csv(os.path.join(out, "read_check.csv"), index=False)
    pd.concat([r["corrections"], r["later"]]).to_csv(os.path.join(out, "ar_corrections.csv"), index=False)
    r["aging"].to_csv(os.path.join(out, "aging.csv"), index=False)
    log = pd.DataFrame([{**{k: v for k, v in x.items() if k != "trace"}, "evidence": json.dumps(x["trace"])}
                        for x in r["log"]])
    log.to_csv(os.path.join(out, "dispute_log.csv"), index=False)

    with open(os.path.join(out, "collection_report.html"), "w", encoding="utf-8") as f:
        f.write(report.render(r, args.seed, out))
    print(r["cash"][["reference", "account_id", "amount", "matched_by", "result"]].to_string(index=False))
    print(f"\nReport: {os.path.join(out, 'collection_report.html')}")


if __name__ == "__main__":
    main()
