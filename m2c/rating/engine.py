"""
Rating engines: interval usage in, rated charge lines out.

The old (production) engine and the new (upgraded) engine share this code and differ only in
configuration, the way two real systems differ:

  rate_date="service"  look up the rate effective for the service period   (old engine)
  rate_date="run"      look up the rate effective on the run date          (new engine: the bug)

and in which meter master they read. A line whose key has no rate is silently not produced,
as many engines do; only an independent volume check notices.
"""
from datetime import date, timedelta

import pandas as pd

from m2c.calendar import TZ

ON_PEAK_START, ON_PEAK_END = 7, 19     # weekdays, interval start 07:00-19:00 local


def tou_period(interval_end_utc):
    start = (pd.to_datetime(interval_end_utc, utc=True) - timedelta(minutes=15)).dt.tz_convert(TZ)
    on = (start.dt.weekday < 5) & (start.dt.hour >= ON_PEAK_START) & (start.dt.hour < ON_PEAK_END)
    return on.map({True: "ENERGY_ON", False: "ENERGY_OFF"})


def _d(s):
    return date.fromisoformat(s) if isinstance(s, str) and s else None


def effective(rows, on_date):
    """Rows (rates or master records) effective on a date."""
    f = rows["effective_from"].map(_d)
    t = rows["effective_to"].map(_d)
    ok = (f <= on_date) & t.map(lambda x: x is None or on_date <= x)
    return rows[ok.values]


def lookup_rate(rates, charge_code, key, on_date):
    r = effective(rates[(rates["charge_code"] == charge_code) & (rates["key"] == key)], on_date)
    return None if r.empty else r.iloc[0]


def rate(usage, master, rates, period, run_date, rate_date, engine_name, run_name):
    first, last = period
    lookup_date = first if rate_date == "service" else run_date
    u = usage.copy()
    u["tou"] = tou_period(u["interval_end_utc"])
    lines = []

    def add(meter, charge, key, quality, qty, unit, r):
        lines.append({
            "engine": engine_name, "run": run_name, "run_date": run_date.isoformat(),
            "service_from": first.isoformat(), "service_to": last.isoformat(),
            "source_version": u["version"].iloc[0], "meter_id": meter, "charge_code": charge,
            "key": key, "quality": quality, "quantity": round(qty, 3), "unit": unit,
            "rate": r["rate"], "rate_id": r["rate_id"],
            "rate_effective_from": r["effective_from"], "rate_effective_to": r["effective_to"] or "",
            "amount": round(qty * r["rate"], 2),
        })

    for meter, mu in u.groupby("meter_id"):
        m = effective(master[master["meter_id"] == meter], first)
        if m.empty:
            continue
        m = m.iloc[0]
        r = lookup_rate(rates, "CUSTOMER", m["category"], lookup_date)
        if r is not None:
            add(meter, "CUSTOMER", m["category"], "actual", 1, "month", r)
        for (tou, quality), g in mu.groupby(["tou", "quality"]):
            r = lookup_rate(rates, tou, "ALL", lookup_date)
            if r is not None:
                add(meter, tou, "ALL", quality, g["kwh"].sum(), "kWh", r)
        for quality, g in mu.groupby("quality"):
            r = lookup_rate(rates, "DELIVERY", m["zone"], lookup_date)
            if r is not None:
                add(meter, "DELIVERY", m["zone"], quality, g["kwh"].sum(), "kWh", r)
    return pd.DataFrame(lines)


# ---------------------------------------------------------------------------------------------
ENGINES = {
    "old": {"master": "meter_master_production.csv", "rate_date": "service"},
    "new": {"master": "meter_master_new_system.csv", "rate_date": "run"},
}

RUNS = [
    # run name, usage source, version, service period, run date
    ("Oct final", "october", "final", (date(2026, 10, 1), date(2026, 10, 31)), date(2026, 11, 2)),
    ("Nov initial", "november", "initial", (date(2026, 11, 1), date(2026, 11, 30)), date(2026, 12, 1)),
    ("Nov final", "november", "final", (date(2026, 11, 1), date(2026, 11, 30)), date(2026, 12, 10)),
]


def run_all(data_dir, usage_path):
    rates = pd.read_csv(f"{data_dir}/rates.csv", keep_default_na=False)
    rates["rate"] = rates["rate"].astype(float)
    nov = pd.read_csv(usage_path)
    oct_ = pd.read_csv(f"{data_dir}/october_usage.csv")
    usage = {"november": nov, "october": oct_}
    out = []
    for name, cfg in ENGINES.items():
        master = pd.read_csv(f"{data_dir}/{cfg['master']}", keep_default_na=False)
        for run_name, src, version, period, run_date in RUNS:
            u = usage[src]
            u = u[u["version"] == version]
            out.append(rate(u, master, rates, period, run_date, cfg["rate_date"], name, run_name))
    return pd.concat(out, ignore_index=True), rates, usage
