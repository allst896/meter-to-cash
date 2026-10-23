"""
Metering diagnostics, in the order the spec prescribes.

Count before cleaning: deduplicating first destroys the evidence.

  1. Expected-interval calendar (from time-zone rules, not data)
  2. Per-day counts before deduplication: raw rows and unique local labels
  3. Values on repeated timestamps: DST repeated hour vs true duplicates
  4. Totals against register reads: which source is off
  5. Gap pattern: why it is off

Then estimation: the initial version (Source B, gaps interpolated and flagged)
and the final version (Source A), kept side by side, never overwritten.

Everything here works from the input files alone. Nothing reads the generator's
answer key.
"""
from datetime import timedelta

import pandas as pd

from m2c.calendar import NOMINAL_PER_DAY, daily_counts, expected_intervals

INTERVAL = timedelta(minutes=15)
REGISTER_TOLERANCE_PCT = 0.001   # 0.1% of register usage (placeholder; open decision in spec)
OUTAGE_MIN_INTERVALS = 4          # a contiguous run of >= 1 hour reads as a communication outage

DST_REPEAT = "DST repeated hour"
TRUE_DUP = "True duplicate"
CONFLICT = "Conflict (unexplained)"


# ------------------------------------------------------------------ load
def load(data_dir):
    a = pd.read_csv(f"{data_dir}/source_a.csv")
    a["interval_end_utc"] = pd.to_datetime(a["interval_end_utc"], utc=True)
    b = pd.read_csv(f"{data_dir}/source_b.csv")
    b["row"] = range(len(b))                      # arrival order in the stream
    b["label"] = pd.to_datetime(b["timestamp_local"])
    b["operating_date"] = (b["label"] - INTERVAL).dt.date   # interval-ending → starts 15 min earlier
    reads = pd.read_csv(f"{data_dir}/register_reads.csv")
    return a, b, reads


# ------------------------------------------------------------------ step 1
def calendar(year, month):
    cal = expected_intervals(year, month)
    cal["interval_end_utc"] = pd.to_datetime(cal["interval_end_utc"], utc=True)
    counts = daily_counts(cal)
    repeated_labels = set(cal.loc[cal["label_local"].duplicated(keep=False), "label_local"])
    return cal, counts, repeated_labels


# ------------------------------------------------------------------ step 2
def per_day_counts(b, cal_counts, meters):
    raw = b.groupby(["meter_id", "operating_date"]).size().rename("raw_rows")
    uniq = b.groupby(["meter_id", "operating_date"])["timestamp_local"].nunique().rename("unique_labels")
    grid = pd.MultiIndex.from_product([meters, cal_counts["operating_date"]],
                                      names=["meter_id", "operating_date"])
    out = (pd.DataFrame(index=grid).join(raw).join(uniq).fillna(0).astype(int).reset_index()
           .merge(cal_counts[["operating_date", "expected"]], on="operating_date"))

    def reading(r):
        if r.raw_rows == r.expected and r.unique_labels == r.expected:
            return "Clean"
        parts = []
        if r.raw_rows > r.unique_labels:
            parts.append(f"{r.raw_rows - r.unique_labels} repeated labels")
        if r.raw_rows < r.expected:
            parts.append(f"{r.expected - r.raw_rows} short")
        if r.unique_labels < r.expected and r.raw_rows >= r.expected:
            parts.append("unique labels below expected")
        return "; ".join(parts) or "Check"
    out["reading"] = out.apply(reading, axis=1)
    return out


def month_counts_naive(b):
    """What a month-level check that assumes 96/day sees."""
    days = b["operating_date"].nunique()
    return b.groupby("meter_id").size().rename("raw_rows").to_frame().assign(
        assumed=days * NOMINAL_PER_DAY)


# ------------------------------------------------------------------ step 3
def repeated_timestamps(b, repeated_labels):
    g = b.groupby(["meter_id", "timestamp_local"])
    stats = g.agg(rows=("kwh", "size"), distinct_values=("kwh", "nunique"),
                  values=("kwh", lambda v: list(v))).reset_index()
    rep = stats[stats["rows"] > 1].copy()

    def classify(r):
        if r.distinct_values == 1:
            return TRUE_DUP
        if r.timestamp_local in repeated_labels and r.distinct_values == 2:
            return DST_REPEAT
        return CONFLICT
    rep["classification"] = rep.apply(classify, axis=1)
    return rep


# ------------------------------------------------------------------ cleaning
def clean_correct(b, cal, repeated_labels):
    """Keep the DST repeated hour, drop identical re-sends, attach offsets."""
    offset_of = (cal[~cal["label_local"].isin(repeated_labels)]
                 .set_index("label_local")["utc_offset"].to_dict())
    rows = []
    for (meter, label), grp in b.sort_values("row").groupby(["meter_id", "timestamp_local"], sort=False):
        distinct = list(dict.fromkeys(grp["kwh"]))           # values in arrival order
        if label in repeated_labels:
            # First distinct value arrived first: daylight time (-0500), then standard (-0600)
            for value, off in zip(distinct, ["-0500", "-0600"]):
                rows.append((meter, label, off, value))
        else:
            rows.append((meter, label, offset_of[label], distinct[0]))
    out = pd.DataFrame(rows, columns=["meter_id", "label_local", "utc_offset", "kwh"])
    return out.merge(cal[["label_local", "utc_offset", "interval_end_utc", "operating_date"]],
                     on=["label_local", "utc_offset"], how="left")


def clean_naive(b):
    """The common mistake: deduplicate on the local timestamp alone."""
    return b.drop_duplicates(subset=["meter_id", "timestamp_local"], keep="first")


# ------------------------------------------------------------------ step 4
def register_comparison(a, b_clean, b_naive, reads):
    r = reads.pivot(index="meter_id", columns="read_local", values="register_kwh")
    usage = (r.iloc[:, 1] - r.iloc[:, 0]).rename("register_usage")
    out = pd.concat([
        usage,
        a.groupby("meter_id")["kwh"].sum().rename("source_a"),
        b_clean.groupby("meter_id")["kwh"].sum().rename("source_b_clean"),
        b_naive.groupby("meter_id")["kwh"].sum().rename("source_b_naive"),
    ], axis=1)
    out["tolerance"] = out["register_usage"] * REGISTER_TOLERANCE_PCT
    for col in ["source_a", "source_b_clean", "source_b_naive"]:
        out[f"{col}_var"] = out[col] - out["register_usage"]
        out[f"{col}_ok"] = out[f"{col}_var"].abs() <= out["tolerance"]
    return out.reset_index()


# ------------------------------------------------------------------ step 5
def gap_pattern(cal, b_clean, meters):
    runs = []
    expected = cal[["interval_end_utc", "label_local", "operating_date"]]
    for meter in meters:
        have = set(b_clean.loc[b_clean["meter_id"] == meter, "interval_end_utc"])
        miss = expected[~expected["interval_end_utc"].isin(have)].sort_values("interval_end_utc")
        if miss.empty:
            continue
        run_id = (miss["interval_end_utc"].diff() != INTERVAL).cumsum()
        for _, grp in miss.groupby(run_id):
            n = len(grp)
            runs.append({
                "meter_id": meter,
                "first_label": grp["label_local"].iloc[0],
                "last_label": grp["label_local"].iloc[-1],
                "intervals": n,
                "hours": n * INTERVAL.total_seconds() / 3600,
                "operating_dates": sorted(set(grp["operating_date"])),
                "pattern": ("Contiguous run: communication outage" if n >= OUTAGE_MIN_INTERVALS
                            else "Scattered: device or sampling problem"),
            })
    return pd.DataFrame(runs)


# ------------------------------------------------------------------ estimation and versions
def build_versions(cal, a, b_clean, meters):
    frames = []
    base = cal[["interval_end_utc", "label_local", "utc_offset", "operating_date"]]
    for meter in meters:
        # Initial: Source B, missing intervals interpolated in time and flagged
        b_m = b_clean.loc[b_clean["meter_id"] == meter, ["interval_end_utc", "kwh"]]
        init = base.merge(b_m, on="interval_end_utc", how="left").sort_values("interval_end_utc")
        init["quality"] = init["kwh"].isna().map({True: "estimated", False: "actual"})
        init["kwh"] = (init.set_index("interval_end_utc")["kwh"]
                       .interpolate(method="time", limit_direction="both").round(3).values)
        init["version"] = "initial"

        # Final: Source A (the monthly meter read), all actual
        a_m = a.loc[a["meter_id"] == meter, ["interval_end_utc", "kwh"]]
        fin = base.merge(a_m, on="interval_end_utc", how="left")
        fin["quality"] = "actual"
        fin["version"] = "final"
        for f in (init, fin):
            f["meter_id"] = meter
            frames.append(f)
    out = pd.concat(frames, ignore_index=True)
    cols = ["meter_id", "version", "interval_end_utc", "label_local", "utc_offset",
            "operating_date", "kwh", "quality"]
    return out[cols]


def estimate_accuracy(versions):
    est = versions[(versions["version"] == "initial") & (versions["quality"] == "estimated")]
    key = ["meter_id", "interval_end_utc"]
    fin = versions[versions["version"] == "final"][key + ["kwh"]]
    m = est[key + ["label_local", "kwh"]].merge(fin, on=key, suffixes=("_estimated", "_actual"))
    return m


# ------------------------------------------------------------------ both orders, side by side
def order_comparison(b, b_naive, cal_counts, rep):
    day = cal_counts.set_index("operating_date")["expected"]
    dst_day = day[day > NOMINAL_PER_DAY].index[0]
    rows = []
    for meter in sorted(b["meter_id"].unique()):
        raw_day = b[(b["meter_id"] == meter) & (b["operating_date"] == dst_day)]
        naive_day = b_naive[(b_naive["meter_id"] == meter) & (b_naive["operating_date"] == dst_day)]
        dst_found = rep[(rep["meter_id"] == meter) & (rep["classification"] == DST_REPEAT)]
        rows.append({
            "meter_id": meter, "expected": int(day[dst_day]),
            "dedup_first_count": len(naive_day),
            "dedup_first_repeats_visible": 0,
            "count_first_raw": len(raw_day),
            "count_first_unique": raw_day["timestamp_local"].nunique(),
            "count_first_repeats_diff_values": len(dst_found),
        })
    return dst_day, pd.DataFrame(rows)


# ------------------------------------------------------------------ run everything
def run(data_dir, year=2026, month=11):
    a, b, reads = load(data_dir)
    meters = sorted(a["meter_id"].unique())
    cal, cal_counts, repeated_labels = calendar(year, month)

    days = per_day_counts(b, cal_counts, meters)
    rep = repeated_timestamps(b, repeated_labels)
    b_clean = clean_correct(b, cal, repeated_labels)
    b_naive = clean_naive(b)
    reg = register_comparison(a, b_clean, b_naive, reads)
    gaps = gap_pattern(cal, b_clean, meters)
    versions = build_versions(cal, a, b_clean, meters)
    acc = estimate_accuracy(versions)
    dst_day, orders = order_comparison(b, b_naive, cal_counts, rep)

    # Validation summary per meter (handed to rating)
    summ = []
    for m in meters:
        v = versions[versions["meter_id"] == m]
        rg = reg.set_index("meter_id").loc[m]
        r = rep[rep["meter_id"] == m]
        init_total = v.loc[v["version"] == "initial", "kwh"].sum()
        final_total = v.loc[v["version"] == "final", "kwh"].sum()
        summ.append({
            "meter_id": m,
            "expected": len(cal),
            "source_a_received": int((a["meter_id"] == m).sum()),
            "source_b_raw": int((b["meter_id"] == m).sum()),
            "true_duplicates_removed": int((r.loc[r["classification"] == TRUE_DUP, "rows"] - 1).sum()),
            "dst_repeated_labels_kept": int((r["classification"] == DST_REPEAT).sum()),
            "conflicts": int((r["classification"] == CONFLICT).sum()),
            "source_b_clean": int((b_clean["meter_id"] == m).sum()),
            "source_b_naive": int((b_naive["meter_id"] == m).sum()),
            "estimated_intervals": int(((v["version"] == "initial") & (v["quality"] == "estimated")).sum()),
            "register_usage": float(rg["register_usage"]),
            "source_a_var": float(rg["source_a_var"]),
            "source_b_clean_var": float(rg["source_b_clean_var"]),
            "source_b_naive_var": float(rg["source_b_naive_var"]),
            "initial_total": round(init_total, 3),
            "final_total": round(final_total, 3),
            "true_up_kwh": round(final_total - init_total, 3),
        })
    summary = pd.DataFrame(summ)

    return dict(a=a, b=b, reads=reads, meters=meters, cal=cal, cal_counts=cal_counts,
                repeated_labels=repeated_labels, days=days, month_naive=month_counts_naive(b),
                repeats=rep, b_clean=b_clean, b_naive=b_naive, register=reg, gaps=gaps,
                versions=versions, accuracy=acc, dst_day=dst_day, orders=orders, summary=summary)
