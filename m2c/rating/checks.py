"""
Rating checks, in the spec's order.

  1. Volume conservation   per meter, rated kWh must equal metered kWh
  2. Delta from normal     each meter's effective $/kWh per charge vs its own prior three months
  3. System comparison     old vs new engine, same usage, line by line
  4. Effective-date check  for each disputed line, which rate was effective for the service period

The checks read the engines' output and the input files. They do not read the answer key.
"""
import difflib
from datetime import date

import pandas as pd

from m2c.rating.engine import ENGINES, RUNS, effective, lookup_rate

DELTA_THRESHOLD = 0.10      # flag effective $/kWh more than 10% away from the prior-3-month average
USAGE_NORMAL = 0.25         # usage within 25% of its average reads as normal
ENERGY = ["ENERGY_ON", "ENERGY_OFF"]

MATCH, MISSING_NEW, MISSING_OLD, RATE_DIFF, QTY_DIFF = (
    "Match", "Missing in new engine", "Missing in old engine", "Rate differs", "Quantity differs")


# ------------------------------------------------------------------ 1. volume conservation
def volume_conservation(lines, usage, rates, data_dir):
    masters = {e: pd.read_csv(f"{data_dir}/{cfg['master']}", keep_default_na=False)
               for e, cfg in ENGINES.items()}
    valid_keys = sorted(rates.loc[rates["charge_code"] == "DELIVERY", "key"].unique())
    rows = []
    for engine in ENGINES:
        for run_name, src, version, period, _ in RUNS:
            u = usage[src]
            u = u[u["version"] == version]
            ln = lines[(lines["engine"] == engine) & (lines["run"] == run_name)]
            for meter, mu in u.groupby("meter_id"):
                lm = ln[ln["meter_id"] == meter]
                metered = round(mu["kwh"].sum(), 3)
                energy = round(lm.loc[lm["charge_code"].isin(ENERGY), "quantity"].sum(), 3)
                delivery = round(lm.loc[lm["charge_code"] == "DELIVERY", "quantity"].sum(), 3)
                zone = effective(masters[engine][masters[engine]["meter_id"] == meter], period[0]).iloc[0]["zone"]
                key_known = zone in valid_keys
                rows.append({
                    "engine": engine, "run": run_name, "meter_id": meter, "metered_kwh": metered,
                    "energy_kwh": energy, "delivery_kwh": delivery,
                    "unrated_energy_kwh": round(metered - energy, 3),
                    "unrated_delivery_kwh": round(metered - delivery, 3),
                    "zone_key": zone, "key_in_rate_table": key_known,
                    "closest_key": "" if key_known else
                    (difflib.get_close_matches(zone, valid_keys, n=1, cutoff=0) or [""])[0],
                })
    vc = pd.DataFrame(rows)
    vc["passes"] = (vc["unrated_energy_kwh"].abs() < 1e-6) & (vc["unrated_delivery_kwh"].abs() < 1e-6)
    return vc, valid_keys


# ------------------------------------------------------------------ 2. delta from normal
def _effective_rates(lines):
    g = lines.copy()
    g["group"] = g["charge_code"].where(~g["charge_code"].isin(ENERGY), "ENERGY")
    agg = g.groupby(["meter_id", "group"]).agg(kwh=("quantity", "sum"), amount=("amount", "sum"),
                                               key=("key", "first")).reset_index()
    return agg


def delta_from_normal(lines, history):
    """Production (old engine) November final run against its own Aug-Oct billing."""
    cur = _effective_rates(lines[(lines["engine"] == "old") & (lines["run"] == "Nov final")])
    hist = history.copy()
    hist["month"] = hist["service_from"].str[:7]
    months = sorted(hist["month"].unique())
    per_month = []
    for m in months:
        e = _effective_rates(hist[hist["month"] == m])
        e["month"] = m
        per_month.append(e)
    pm = pd.concat(per_month, ignore_index=True)
    rows = []
    for meter in sorted(cur["meter_id"].unique()):
        for group in ["ENERGY", "DELIVERY"]:
            c = cur[(cur["meter_id"] == meter) & (cur["group"] == group)]
            h = pm[(pm["meter_id"] == meter) & (pm["group"] == group)].set_index("month")
            hist_rate = (h["amount"] / h["kwh"])
            row = {"meter_id": meter, "charge": group}
            for m in months:
                row[f"rate_{m}"] = hist_rate.get(m)
                row[f"key_{m}"] = h["key"].get(m)
            row["prior_avg_rate"] = hist_rate.mean()
            row["prior_avg_kwh"] = h["kwh"].mean()
            if c.empty:
                row.update(nov_rate=None, nov_kwh=0.0, nov_key="", rate_change=None, kwh_change=-1.0)
            else:
                c = c.iloc[0]
                row.update(nov_rate=c["amount"] / c["kwh"], nov_kwh=c["kwh"], nov_key=c["key"])
                row["rate_change"] = row["nov_rate"] / row["prior_avg_rate"] - 1
                row["kwh_change"] = row["nov_kwh"] / row["prior_avg_kwh"] - 1
            row["flagged"] = row["rate_change"] is None or abs(row["rate_change"]) > DELTA_THRESHOLD
            row["usage_normal"] = abs(row["kwh_change"]) <= USAGE_NORMAL
            row["key_changed"] = bool(row["nov_key"]) and row["nov_key"] != row[f"key_{months[-1]}"]
            if row["flagged"] and row["usage_normal"]:
                row["reading"] = ("Usage normal, price per kWh not: check grouping and category setup"
                                  + (f" (key changed {row[f'key_{months[-1]}']} → {row['nov_key']})"
                                     if row["key_changed"] else ""))
            elif row["flagged"]:
                row["reading"] = "Price and usage both moved: check usage first"
            else:
                row["reading"] = "Within normal range"
            rows.append(row)
    return pd.DataFrame(rows), months


# ------------------------------------------------------------------ 3. system comparison
def system_comparison(lines):
    key = ["run", "meter_id", "charge_code", "quality"]
    cols = key + ["key", "quantity", "rate", "rate_id", "rate_effective_from", "rate_effective_to",
                  "amount", "service_from", "service_to", "run_date"]
    old = lines.loc[lines["engine"] == "old", cols]
    new = lines.loc[lines["engine"] == "new", cols]
    m = old.merge(new, on=key, how="outer", suffixes=("_old", "_new"))
    m["run_date"] = m["run_date_old"].fillna(m["run_date_new"])
    m["service_from"] = m["service_from_old"].fillna(m["service_from_new"])
    m["service_to"] = m["service_to_old"].fillna(m["service_to_new"])
    m["difference"] = (m["amount_new"].fillna(0) - m["amount_old"].fillna(0)).round(2)

    def classify(r):
        if pd.isna(r.amount_new):
            return MISSING_NEW
        if pd.isna(r.amount_old):
            return MISSING_OLD
        if abs(r.quantity_old - r.quantity_new) > 1e-9:
            return QTY_DIFF
        if abs(r.rate_old - r.rate_new) > 1e-12:
            return RATE_DIFF
        return MATCH
    m["classification"] = m.apply(classify, axis=1)
    order = {name: i for i, (name, *_rest) in enumerate(RUNS)}
    return m.sort_values(["run", "meter_id", "charge_code", "quality"],
                         key=lambda s: s.map(order) if s.name == "run" else s).reset_index(drop=True)


def comparison_by_run(cmp):
    out = []
    for run_name, *_ in RUNS:
        c = cmp[cmp["run"] == run_name]
        rd = c[c["classification"] == RATE_DIFF]
        out.append({
            "run": run_name, "run_date": c["run_date"].iloc[0], "lines": len(c),
            "matches": int((c["classification"] == MATCH).sum()),
            "missing_new": int((c["classification"] == MISSING_NEW).sum()),
            "rate_diffs": len(rd),
            "rate_diff_charges": ", ".join(sorted(rd["charge_code"].unique())),
            "rate_diff_per_unit": sorted(set((rd["rate_new"] - rd["rate_old"]).round(4))),
            "net_difference": round(c["difference"].sum(), 2),
        })
    return pd.DataFrame(out)


# ------------------------------------------------------------------ 4. effective-date check
def effective_date_check(cmp, rates):
    rd = cmp[cmp["classification"] == RATE_DIFF].copy()
    rows = []
    for r in rd.itertuples():
        sf, st = date.fromisoformat(r.service_from), date.fromisoformat(r.service_to)
        at_start = lookup_rate(rates, r.charge_code, r.key_old, sf)
        at_end = lookup_rate(rates, r.charge_code, r.key_old, st)
        correct = at_start["rate_id"] if at_start is not None and at_end is not None and \
            at_start["rate_id"] == at_end["rate_id"] else None
        new_from = date.fromisoformat(r.rate_effective_from_new)
        rows.append({
            "run": r.run, "run_date": r.run_date, "meter_id": r.meter_id, "charge_code": r.charge_code,
            "quality": r.quality, "service_period": f"{r.service_from} to {r.service_to}",
            "old_rate_id": r.rate_id_old, "old_rate": r.rate_old,
            "new_rate_id": r.rate_id_new, "new_rate": r.rate_new,
            "new_rate_effective_from": r.rate_effective_from_new,
            "rate_for_service_period": correct,
            "correct_engine": "old" if correct == r.rate_id_old else "new" if correct == r.rate_id_new else "neither",
            "new_rate_starts_after_service": new_from > st,
            "difference": r.difference,
        })
    return pd.DataFrame(rows)


def true_up_exposure(lines, charge_code="ENERGY_ON"):
    """True-up = November final minus November initial, per engine, for one charge."""
    rows = []
    for engine in ENGINES:
        e = lines[(lines["engine"] == engine) & (lines["charge_code"] == charge_code)]
        ini = e[e["run"] == "Nov initial"].groupby("meter_id")["amount"].sum()
        fin = e[e["run"] == "Nov final"].groupby("meter_id")["amount"].sum()
        for m in fin.index:
            rows.append({"engine": engine, "meter_id": m, "initial": ini.get(m, 0.0),
                         "final": fin[m], "true_up": round(fin[m] - ini.get(m, 0.0), 2)})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ run everything
def run(lines, rates, usage, data_dir):
    history = pd.read_csv(f"{data_dir}/billing_history.csv", keep_default_na=False)
    vc, valid_keys = volume_conservation(lines, usage, rates, data_dir)
    delta, months = delta_from_normal(lines, history)
    cmp = system_comparison(lines)
    by_run = comparison_by_run(cmp)
    eff = effective_date_check(cmp, rates)
    tu = true_up_exposure(lines)

    # Internal consistency: line differences sum to the engines' total difference per run
    for run_name, *_ in RUNS:
        tot = (lines[(lines["engine"] == "new") & (lines["run"] == run_name)]["amount"].sum()
               - lines[(lines["engine"] == "old") & (lines["run"] == run_name)]["amount"].sum())
        assert abs(tot - cmp.loc[cmp["run"] == run_name, "difference"].sum()) < 0.005, run_name
    return dict(lines=lines, rates=rates, history=history, volume=vc, valid_keys=valid_keys,
                delta=delta, months=months, comparison=cmp, by_run=by_run, effective=eff,
                true_up=tu)
