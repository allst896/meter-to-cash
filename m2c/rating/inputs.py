"""
Synthetic inputs for the rating stage.

Rate table        effective-dated; on-peak energy rises from $0.12 to $0.13/kWh on Dec 1, 2026
Meter master      production setup, effective-dated. M3's account update on Nov 1 picked the
                  wrong load zone (LZ_WEST instead of LZ_HOUSTON): a configuration goof.
New-system master the same records migrated into the upgraded rating engine, except M2's zone
                  came across as "NORTH" instead of "LZ_NORTH".
Billing history   what production billed for Aug-Oct 2026 (the baseline for delta from normal)
October usage     interval data for October, used for the parallel run on Nov 2

The November interval usage comes from the metering stage (output/interval_usage.csv).
"""
from datetime import date, timedelta

import numpy as np
import pandas as pd

from m2c.calendar import TZ, expected_intervals
from m2c.metering.generate import METERS, _kw

RATES = [
    # rate_id, charge_code, key, rate, unit, effective_from, effective_to
    ("CUST-RES-1", "CUSTOMER", "RESIDENTIAL", 10.00, "$/month", "2026-01-01", ""),
    ("CUST-COM-1", "CUSTOMER", "COMMERCIAL", 45.00, "$/month", "2026-01-01", ""),
    ("CUST-IND-1", "CUSTOMER", "INDUSTRIAL", 250.00, "$/month", "2026-01-01", ""),
    ("EON-1", "ENERGY_ON", "ALL", 0.120, "$/kWh", "2026-01-01", "2026-11-30"),
    ("EON-2", "ENERGY_ON", "ALL", 0.130, "$/kWh", "2026-12-01", ""),
    ("EOFF-1", "ENERGY_OFF", "ALL", 0.070, "$/kWh", "2026-01-01", ""),
    ("DEL-NORTH-1", "DELIVERY", "LZ_NORTH", 0.031, "$/kWh", "2026-01-01", ""),
    ("DEL-HOUSTON-1", "DELIVERY", "LZ_HOUSTON", 0.036, "$/kWh", "2026-01-01", ""),
    ("DEL-SOUTH-1", "DELIVERY", "LZ_SOUTH", 0.033, "$/kWh", "2026-01-01", ""),
    ("DEL-WEST-1", "DELIVERY", "LZ_WEST", 0.044, "$/kWh", "2026-01-01", ""),
]

MASTER_PRODUCTION = [
    # meter_id, category, zone, effective_from, effective_to, change_note
    ("M1", "COMMERCIAL", "LZ_NORTH", "2024-03-01", "", "New service"),
    ("M2", "RESIDENTIAL", "LZ_NORTH", "2025-06-15", "", "New service"),
    ("M3", "INDUSTRIAL", "LZ_HOUSTON", "2023-09-01", "2026-10-31", "New service"),
    ("M3", "INDUSTRIAL", "LZ_WEST", "2026-11-01", "", "Account update: service upgrade"),
]

HISTORY_MONTHS = [(2026, 8), (2026, 9), (2026, 10)]

# Answer key: used by tests and the report's "which check catches what" view only.
PLANTED = {
    "wrong_zone": {"meter_id": "M3", "charge_code": "DELIVERY",
                   "label": "Wrong zone (configuration goof)"},
    "unpriced": {"meter_id": "M2", "charge_code": "DELIVERY",
                 "label": "Unpriced usage (key mismatch)"},
    "effective_dating": {"charge_code": "ENERGY_ON",
                         "label": "Effective dating (system upgrade)"},
}


def month_intervals(year, month, rng):
    cal = expected_intervals(year, month)
    start_utc = cal["interval_end_utc"] - timedelta(minutes=15)
    frames = []
    for meter, (profile, _) in METERS.items():
        kwh = [round(_kw(profile, s.astimezone(TZ), rng) * 0.25, 3) for s in start_utc]
        frames.append(pd.DataFrame({
            "meter_id": meter,
            "version": "final",
            "interval_end_utc": cal["interval_end_utc"].dt.strftime("%Y-%m-%dT%H:%MZ"),
            "label_local": cal["label_local"],
            "utc_offset": cal["utc_offset"],
            "operating_date": cal["operating_date"],
            "kwh": kwh,
            "quality": "actual",
        }))
    return pd.concat(frames, ignore_index=True)


def build(data_dir, seed=7):
    from m2c.rating import engine   # avoid a circular import at module load

    rng = np.random.default_rng(seed + 1000)
    rates = pd.DataFrame(RATES, columns=["rate_id", "charge_code", "key", "rate", "unit",
                                         "effective_from", "effective_to"])
    prod = pd.DataFrame(MASTER_PRODUCTION, columns=["meter_id", "category", "zone",
                                                    "effective_from", "effective_to", "change_note"])
    new = prod.copy()
    new.loc[new["meter_id"] == "M2", "zone"] = "NORTH"
    new["change_note"] = new["change_note"] + " (migrated)"

    history_lines, october = [], None
    for y, m in HISTORY_MONTHS:
        usage = month_intervals(y, m, rng)
        first = date(y, m, 1)
        last = (pd.Timestamp(first) + pd.offsets.MonthEnd(0)).date()
        run_date = last + timedelta(days=2)
        lines = engine.rate(usage, prod, rates, period=(first, last), run_date=run_date,
                            rate_date="service", engine_name="old", run_name=f"{y}-{m:02d} final")
        history_lines.append(lines)
        if (y, m) == HISTORY_MONTHS[-1]:
            october = usage

    hist = pd.concat(history_lines, ignore_index=True)
    rates.to_csv(f"{data_dir}/rates.csv", index=False)
    prod.to_csv(f"{data_dir}/meter_master_production.csv", index=False)
    new.to_csv(f"{data_dir}/meter_master_new_system.csv", index=False)
    hist.to_csv(f"{data_dir}/billing_history.csv", index=False)
    october.to_csv(f"{data_dir}/october_usage.csv", index=False)
