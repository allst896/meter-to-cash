"""
Run the rating stage. Reads the metering stage's output, so run that first.

    python run_metering.py
    python run_rating.py
"""
import argparse
import os

import pandas as pd

from m2c.rating import checks, engine, inputs, report

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    data, out = os.path.join(HERE, "data", "rating"), os.path.join(HERE, "output")
    usage = os.path.join(out, "interval_usage.csv")
    if not os.path.exists(usage):
        raise SystemExit("output/interval_usage.csv not found: run `python run_metering.py` first.")
    os.makedirs(data, exist_ok=True)

    inputs.build(data, seed=args.seed)
    lines, rates, usage_by_month = engine.run_all(data, usage)
    r = checks.run(lines, rates, usage_by_month, data)
    r["data_dir"] = data

    lines.to_csv(os.path.join(out, "rated_charges.csv"), index=False)
    ex = []
    for x in r["volume"][~r["volume"]["passes"]].itertuples():
        ex.append({"check": "Volume conservation", "engine": x.engine, "run": x.run, "meter_id": x.meter_id,
                   "charge_code": "DELIVERY", "finding": f"{x.unrated_delivery_kwh} kWh unpriced; zone key "
                   f"'{x.zone_key}' not in rate table (closest '{x.closest_key}')", "amount": None})
    for x in r["delta"][r["delta"]["flagged"]].itertuples():
        ex.append({"check": "Delta from normal", "engine": "old", "run": "Nov final", "meter_id": x.meter_id,
                   "charge_code": x.charge, "finding": x.reading, "amount": None})
    cmp = r["comparison"]
    for x in cmp[cmp["classification"] == checks.MISSING_NEW].itertuples():
        ex.append({"check": "System comparison", "engine": "new", "run": x.run, "meter_id": x.meter_id,
                   "charge_code": x.charge_code, "finding": x.classification, "amount": x.difference})
    for x in r["effective"].itertuples():
        ex.append({"check": "Effective-date check", "engine": "new", "run": x.run, "meter_id": x.meter_id,
                   "charge_code": x.charge_code, "finding": f"Rate differs: new used {x.new_rate_id}, "
                   f"{x.rate_for_service_period} effective for service period; {x.correct_engine} engine correct",
                   "amount": x.difference})
    pd.DataFrame(ex).to_csv(os.path.join(out, "rating_exceptions.csv"), index=False)

    with open(os.path.join(out, "rating_report.html"), "w", encoding="utf-8") as f:
        f.write(report.render(r, args.seed))
    print(r["by_run"].to_string(index=False))
    print(f"\nReport: {os.path.join(out, 'rating_report.html')}")


if __name__ == "__main__":
    main()
