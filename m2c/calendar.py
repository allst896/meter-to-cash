"""
Expected-interval calendar (metering step 1).

Answers "how many intervals should exist, and which ones?" from time-zone rules
alone, before looking at any meter data.

Conventions:
  - Time zone: America/Chicago
  - 15-minute intervals, labeled interval-ending (00:00-00:15 is labeled 00:15)
  - Each interval counts toward the local date it STARTS on, so the interval
    labeled 00:00 belongs to the previous day
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

TZ = ZoneInfo("America/Chicago")
INTERVAL = timedelta(minutes=15)
NOMINAL_PER_DAY = timedelta(days=1) // INTERVAL  # 96


def month_bounds_utc(year, month, tz=TZ):
    start_local = datetime(year, month, 1, tzinfo=tz)
    ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
    end_local = datetime(ny, nm, 1, tzinfo=tz)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def expected_intervals(year, month, tz=TZ, interval=INTERVAL):
    """Every expected interval in the local month, one row per interval."""
    t, end_utc = month_bounds_utc(year, month, tz)
    rows = []
    # Step through UTC: it has no repeated or skipped hours, so the list is
    # complete by construction.
    while t < end_utc:
        begin_local = t.astimezone(tz)
        end_local = (t + interval).astimezone(tz)
        rows.append({
            "interval_end_utc": t + interval,
            "label_local": end_local.strftime("%Y-%m-%d %H:%M"),  # as a meter shows it
            "utc_offset": end_local.strftime("%z"),               # tells repeated labels apart
            "operating_date": begin_local.date(),
        })
        t += interval
    return pd.DataFrame(rows)


def daily_counts(intervals):
    """Expected intervals per operating date, with a DST-transition flag."""
    counts = (intervals.groupby("operating_date").size()
              .rename("expected").reset_index())
    counts["dst_transition"] = counts["expected"] != NOMINAL_PER_DAY
    return counts
