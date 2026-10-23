"""
Run the metering stage end to end.

    python run_metering.py            # regenerate data, diagnose, write outputs
    python run_metering.py --seed 11  # a different synthetic month, same planted issues
"""
import argparse
import os

from m2c.metering import diagnose, generate, report

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    data, out = os.path.join(HERE, "data"), os.path.join(HERE, "output")
    os.makedirs(data, exist_ok=True)
    os.makedirs(out, exist_ok=True)

    generate.write(data, seed=args.seed)
    r = diagnose.run(data)

    v = r["versions"].copy()
    v["interval_end_utc"] = v["interval_end_utc"].dt.strftime("%Y-%m-%dT%H:%MZ")
    v.to_csv(os.path.join(out, "interval_usage.csv"), index=False)
    r["summary"].to_csv(os.path.join(out, "validation_summary.csv"), index=False)
    with open(os.path.join(out, "metering_report.html"), "w", encoding="utf-8") as f:
        f.write(report.render(r, args.seed))

    print(r["summary"].set_index("meter_id").T.to_string())
    print(f"\nReport: {os.path.join(out, 'metering_report.html')}")


if __name__ == "__main__":
    main()
