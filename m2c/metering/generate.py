"""
Synthetic inputs for the metering stage.

Source A  monthly meter read: the meter's stored intervals, UTC timestamps, complete
Source B  unofficial streamed feed: local timestamps with no offset, three planted issues
            - DST fall-back: 01:00-01:45 on Nov 1 appear twice (real, different values)
            - true duplicates: rows re-sent with identical values (never Nov 1 or Nov 18)
            - collector outage: Nov 18, labels 02:15-08:00 missing (24 intervals)
Register reads  start and end cumulative totals per meter (source of record)
"""
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from m2c.calendar import TZ, expected_intervals

YEAR, MONTH = 2026, 11
METERS = {
    # meter_id: (profile, starting register read in kWh)
    "M1": ("commercial", 182_450),
    "M2": ("residential", 23_118),
    "M3": ("industrial", 96_205),
}
DST_DAY = date(2026, 11, 1)
OUTAGE_DAY = date(2026, 11, 18)
OUTAGE_FIRST, OUTAGE_LAST = "2026-11-18 02:15", "2026-11-18 08:00"
TRUE_DUPLICATES_PER_METER = 20


def _kw(profile, local_start, rng):
    """Average demand (kW) for the interval starting at local_start."""
    h = local_start.hour + local_start.minute / 60
    weekday = local_start.weekday() < 5
    if profile == "commercial":
        if weekday and 7 <= h < 19:
            ramp = min(1.0, (h - 7) / 1.5, (19 - h) / 1.5)
            kw = 12 + 33 * ramp
        else:
            kw = 15 if not weekday and 9 <= h < 17 else 12
    elif profile == "residential":
        kw = 0.6
        if 6.5 <= h < 9:
            kw += 0.9
        if 17 <= h < 22:
            kw += 1.9
    else:  # industrial, two shifts
        kw = 24 if 6 <= h < 22 else 17
    return kw * rng.normal(1.0, 0.05)


def build(seed=7):
    rng = np.random.default_rng(seed)
    cal = expected_intervals(YEAR, MONTH)
    start_utc = cal["interval_end_utc"] - timedelta(minutes=15)

    a_rows, b_rows, reads = [], [], []
    for meter, (profile, start_read) in METERS.items():
        kwh = np.array([round(_kw(profile, s.astimezone(TZ), rng) * 0.25, 3) for s in start_utc])
        a = pd.DataFrame({
            "meter_id": meter,
            "interval_end_utc": cal["interval_end_utc"].dt.strftime("%Y-%m-%dT%H:%MZ"),
            "kwh": kwh,
        })
        a_rows.append(a)

        # Source B: same meter data, streamed with local labels and no offset
        b = pd.DataFrame({"meter_id": meter, "timestamp_local": cal["label_local"],
                          "kwh": kwh, "operating_date": cal["operating_date"]})
        outage = (b["timestamp_local"] >= OUTAGE_FIRST) & (b["timestamp_local"] <= OUTAGE_LAST)
        b = b[~outage].reset_index(drop=True)

        eligible = b.index[~b["operating_date"].isin([DST_DAY, OUTAGE_DAY])]
        picks = set(rng.choice(eligible, TRUE_DUPLICATES_PER_METER, replace=False).tolist())
        rows = []
        for i, r in enumerate(b.itertuples(index=False)):
            rows.append(r)
            if i in picks:          # the feed re-sends the same record
                rows.append(r)
        b = pd.DataFrame(rows, columns=b.columns).drop(columns="operating_date")
        b_rows.append(b)

        end_read = start_read + int(round(kwh.sum()))
        reads += [
            {"meter_id": meter, "read_local": "2026-11-01 00:00", "register_kwh": start_read},
            {"meter_id": meter, "read_local": "2026-12-01 00:00", "register_kwh": end_read},
        ]

    return (pd.concat(a_rows, ignore_index=True),
            pd.concat(b_rows, ignore_index=True),
            pd.DataFrame(reads))


def write(data_dir, seed=7):
    a, b, reads = build(seed)
    a.to_csv(f"{data_dir}/source_a.csv", index=False)
    b.to_csv(f"{data_dir}/source_b.csv", index=False)
    reads.to_csv(f"{data_dir}/register_reads.csv", index=False)
    return a, b, reads
