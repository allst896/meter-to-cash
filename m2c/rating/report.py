"""HTML report for the rating stage: one self-contained page, same look as metering."""
import pandas as pd

from m2c.metering.report import CSS as BASE_CSS, esc, table
from m2c.rating import checks as C
from m2c.rating.inputs import PLANTED

CSS = BASE_CSS + """
.chip.na{color:var(--muted);background:var(--panel)}
header{margin-bottom:40px}
table.matrix td{padding:12px}
table.matrix td:first-child{font-weight:500;color:var(--ink)}
.callout{border-left:3px solid var(--rule);padding:2px 0 2px 16px;margin:0 0 18px;max-width:72ch;color:var(--ink2)}
"""


def money(x, signed=False):
    if abs(x) < 0.005:
        x = 0.0
    s = f"${abs(x):,.2f}"
    if signed:
        return ("+" if x > 0 else "−" if x < 0 else "") + s
    return ("−" if x < 0 else "") + s


def rate_fmt(x, places=4):
    return "—" if x is None or pd.isna(x) else f"${x:.{places}f}"


def pct(x):
    if x is None or pd.isna(x):
        return "—"
    if abs(x) < 0.0005:
        return "0.0%"
    return f"{x:+.1%}".replace("-", "−")


def period(sf, st):
    a, b = pd.Timestamp(sf), pd.Timestamp(st)
    return f"{a.strftime('%b')} {a.day}–{b.day}, {b.year}"


def status(kind, text):
    mark = {"caught": "✓", "missed": "✗", "na": "–", "decides": "✓"}[kind]
    cls = {"caught": "ok", "missed": "bad", "na": "na", "decides": "ok"}[kind]
    return f'<span class="chip {cls}"><span aria-hidden="true">{mark}</span> {esc(text)}</span>'


def matrix(r):
    vc, delta, cmp, eff = r["volume"], r["delta"], r["comparison"], r["effective"]
    wz, up, ed = PLANTED["wrong_zone"], PLANTED["unpriced"], PLANTED["effective_dating"]

    def vc_cell(meter=None, charge=None):
        sel = vc if meter is None else vc[vc["meter_id"] == meter]
        col = "unrated_delivery_kwh" if charge == "DELIVERY" else "unrated_energy_kwh"
        bad = sel[sel[col].abs() > 1e-6]
        return status("caught", "Caught") if len(bad) else status("missed", "Passes")

    def cmp_cell(meter=None, charge=None):
        sel = cmp
        if meter:
            sel = sel[sel["meter_id"] == meter]
        if charge:
            sel = sel[sel["charge_code"] == charge]
        return status("caught", "Caught") if (sel["classification"] != C.MATCH).any() else \
            status("missed", "Both engines agree")

    d = delta.set_index(["meter_id", "charge"])
    wz_delta = status("caught", "Caught") if d.loc[(wz["meter_id"], "DELIVERY"), "flagged"] else status("missed", "Missed")
    rows = [
        [esc(wz["label"]), vc_cell(wz["meter_id"], "DELIVERY"), wz_delta,
         cmp_cell(wz["meter_id"], "DELIVERY"), status("na", "No disputed rate")],
        [esc(up["label"]), vc_cell(up["meter_id"], "DELIVERY"), status("na", "Production only"),
         cmp_cell(up["meter_id"], "DELIVERY"), status("na", "No disputed rate")],
        [esc(ed["label"]), vc_cell(None, "ENERGY"), status("na", "Production only"),
         cmp_cell(None, ed["charge_code"]),
         status("decides", "Old engine is right") if (eff["correct_engine"] == "old").all() else status("missed", "Unresolved")],
    ]
    return table(["Planted issue", "1. Volume conservation", "2. Delta from normal",
                  "3. System comparison", "4. Effective-date check"], rows, cls="matrix")


def render(r, seed):
    lines, rates, vc, delta, months = r["lines"], r["rates"], r["volume"], r["delta"], r["months"]
    cmp, by_run, eff, tu = r["comparison"], r["by_run"], r["effective"], r["true_up"]
    old_final = lines[(lines["engine"] == "old") & (lines["run"] == "Nov final")]

    # impacts
    m3_del = old_final[(old_final["meter_id"] == "M3") & (old_final["charge_code"] == "DELIVERY")]
    m3_kwh = m3_del["quantity"].sum()
    west = rates.set_index("key").loc["LZ_WEST", "rate"]
    houston = rates.set_index("key").loc["LZ_HOUSTON", "rate"]
    wz_impact = m3_kwh * (west - houston)
    up_impact = -cmp[(cmp["run"] == "Nov final") & (cmp["classification"] == C.MISSING_NEW)]["difference"].sum()
    ed_impact = eff[eff["run"] == "Nov final"]["difference"].sum()

    # rate table
    rate_tbl = table(["Rate", "Charge", "Key", "Rate", "Effective from", "Effective to"],
                     [[esc(x.rate_id), esc(x.charge_code), esc(x.key),
                       f"${x.rate:,.2f} / month" if x.unit == "$/month" else f"${x.rate:.3f} / kWh",
                       esc(x.effective_from), esc(x.effective_to or "open")] for x in rates.itertuples()],
                     num=(3,))
    prod = pd.read_csv(r["data_dir"] + "/meter_master_production.csv", keep_default_na=False)
    new = pd.read_csv(r["data_dir"] + "/meter_master_new_system.csv", keep_default_na=False)
    master_tbl = table(["Meter", "Category", "Zone (production)", "Zone (new system)", "Effective", "Change note"],
                       [[esc(p.meter_id), esc(p.category.title()), esc(p.zone), esc(n.zone),
                         f"{esc(p.effective_from)} to {esc(p.effective_to or 'open')}", esc(p.change_note)]
                        for p, n in zip(prod.itertuples(), new.itertuples())])
    engines_tbl = table(["", "Old engine (production)", "New engine (upgrade, in parallel)"], [
        ["Meter master", "Production setup", "Migrated copy of production"],
        ["Rate lookup date", "Service period", "Run date"],
        ["Runs compared", "Oct final (Nov 2), Nov initial (Dec 1), Nov final (Dec 10)", "Same runs, same usage"],
    ])

    # 1. volume conservation
    meters = sorted(vc["meter_id"].unique())
    vc_rows = []
    for (eng, run), g in vc.groupby(["engine", "run"], sort=False):
        cells = [esc(eng.title()), esc(run)]
        for m in meters:
            x = g[g["meter_id"] == m].iloc[0]
            if x.unrated_energy_kwh + x.unrated_delivery_kwh > 1e-6:
                cells.append(status("caught", f"{x.unrated_delivery_kwh:,.1f} delivery kWh unpriced"))
            else:
                cells.append(status("na", f"{x.metered_kwh:,.1f} kWh, all priced"))
        vc_rows.append(cells)
    vc_tbl = table(["Engine", "Run", *meters], vc_rows)
    bad = vc[~vc["passes"]].iloc[0]
    keys = ", ".join(r["valid_keys"])

    # 2. delta from normal
    mlabels = [pd.Timestamp(m + "-01").strftime("%b") for m in months]
    d_rows = []
    for x in delta.to_dict("records"):
        cells = [esc(x["meter_id"]), esc(x["charge"].title())]
        cells += [rate_fmt(x[f"rate_{m}"]) for m in months]
        cells += [rate_fmt(x["nov_rate"]), pct(x["rate_change"]), pct(x["kwh_change"]),
                  status("caught", "Flagged") if x["flagged"] else status("na", "Normal")]
        d_rows.append(cells)
    delta_tbl = table(["Meter", "Charge", *[f"{m} $/kWh" for m in mlabels], "Nov $/kWh",
                       "Rate vs prior avg", "Usage vs prior avg", "Result"],
                      d_rows, num=tuple(range(2, 5 + len(months))))
    flagged = delta[delta["flagged"]].iloc[0]
    hist_keys = " → ".join(f"{pd.Timestamp(m + '-01').strftime('%b')} {flagged[f'key_{m}']}" for m in months)

    # 3. system comparison
    run_rows = []
    for x in by_run.itertuples():
        diffs = []
        if x.missing_new:
            diffs.append(f"{x.missing_new} line{'s' if x.missing_new != 1 else ''} missing in new")
        if x.rate_diffs:
            per = ", ".join(f"${p:.3f}" for p in x.rate_diff_per_unit)
            diffs.append(f"{x.rate_diffs} {x.rate_diff_charges} lines, rate +{per}/kWh")
        run_rows.append([esc(x.run), esc(x.run_date), str(x.lines), str(x.matches),
                         esc("; ".join(diffs) or "None"), money(x.net_difference, True)])
    run_tbl = table(["Run", "Run date", "Lines", "Match", "Differences", "New − old"], run_rows, num=(2, 3, 5))
    fin = cmp[(cmp["run"] == "Nov final") & (cmp["classification"] != C.MATCH)]
    fin_tbl = table(["Meter", "Charge", "Quantity", "Old rate", "New rate", "Old amount", "New amount",
                     "Difference", "Classified as"],
                    [[esc(x.meter_id), esc(x.charge_code), f"{(x.quantity_old if pd.notna(x.quantity_old) else x.quantity_new):,.1f}",
                      rate_fmt(x.rate_old, 3), rate_fmt(x.rate_new, 3),
                      money(x.amount_old) if pd.notna(x.amount_old) else "—",
                      money(x.amount_new) if pd.notna(x.amount_new) else "—",
                      money(x.difference, True), esc(x.classification)] for x in fin.itertuples()],
                    num=(2, 3, 4, 5, 6, 7))

    # 4. effective-date check
    e = eff[eff["run"] == "Nov final"]
    eff_tbl = table(["Meter", "Service period", "Old engine used", "New engine used", "Effective for service period", "Verdict"],
                    [[esc(x.meter_id), esc(period(*x.service_period.split(" to "))), f"{esc(x.old_rate_id)} (${x.old_rate:.3f})",
                      f"{esc(x.new_rate_id)} (${x.new_rate:.3f}, from {esc(x.new_rate_effective_from)})",
                      esc(x.rate_for_service_period),
                      status("caught", "Old engine correct") if x.correct_engine == "old" else status("missed", "Check")]
                     for x in e.itertuples()])
    tu_p = tu.pivot(index="meter_id", columns="engine", values="true_up")
    tu_tbl = table(["Meter", "On-peak true-up, old engine", "On-peak true-up, new engine", "Difference"],
                   [[esc(m), money(tu_p.loc[m, "old"], True), money(tu_p.loc[m, "new"], True),
                     money(tu_p.loc[m, "new"] - tu_p.loc[m, "old"], True)] for m in tu_p.index], num=(1, 2, 3))

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rating Diagnostics</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body><main>

<header>
<nav class="stagenav" aria-label="Stages"><a href="metering_report.html">1 Metering</a><span>2 Rating</span><a href="billing_report.html">3 Billing</a></nav>
<p class="kicker">Meter-to-cash demo · Stage 2 of 5: Rating</p>
<h1>Every unit priced, in the right group, at the right rate</h1>
<p class="lede">November's usage is rated by the production engine and, in parallel, by the upgraded engine that will replace it.
Three issues are planted. Two of them leave every kilowatt-hour priced, so a volume check passes, and one of those is
configured identically in both engines, so a system comparison passes too. No single check catches all three.</p>
</header>

<section><h2>Which check catches what</h2>
<p>Rows are the planted issues; columns are the four checks, run in order. Delta from normal runs on production billing,
where the history lives.</p>
{matrix(r)}
<p>November impact: the wrong zone overbills M3 by {money(wz_impact)}; the key mismatch would leave {money(up_impact)} of
M2 delivery unbilled in the new system; run-date pricing would overbill on-peak energy by {money(ed_impact)} across the three meters.</p>
</section>

<nav class="steps" aria-label="Steps">
<a href="#setup">Setup</a><a href="#s1">1 Volume conservation</a><a href="#s2">2 Delta from normal</a>
<a href="#s3">3 System comparison</a><a href="#s4">4 Effective-date check</a>
</nav>

<section id="setup"><h2>Setup</h2>
<p>Three charges, each keyed on a different attribute: the customer charge on category, energy on time of use
(on-peak is weekdays 07:00–19:00, interval start, Central time), and delivery on load zone. Rates carry effective dates;
on-peak energy rises on December 1.</p>
<h3>Rate table</h3>
{rate_tbl}
<h3>Meter master</h3>
{master_tbl}
<h3>The two engines</h3>
{engines_tbl}
</section>

<section id="s1"><h2><span class="num">1</span>Volume conservation</h2>
<p>Per meter and run, rated kWh must equal metered kWh, for energy and for delivery separately. One gap appears, in the
new engine only, every run.</p>
{vc_tbl}
<p class="callout">Grouped by attribute, the unpriced delivery kWh all carry zone key <code>{esc(bad.zone_key)}</code>.
The rate table only knows {esc(keys)}; the closest is <code>{esc(bad.closest_key)}</code>. The migration dropped the
prefix, so the new engine found no delivery rate and silently produced no line.</p>
</section>

<section id="s2"><h2><span class="num">2</span>Delta from normal</h2>
<p>Each meter's effective $/kWh per charge, compared to its own prior three months of production billing. Flag at
±{C.DELTA_THRESHOLD:.0%}; usage within ±{C.USAGE_NORMAL:.0%} reads as normal.</p>
{delta_tbl}
<p class="callout">{esc(flagged.meter_id)} delivery moved {pct(flagged.rate_change)} while its usage moved {pct(flagged.kwh_change)}.
Same consumption, different price: a grouping or category change, not a usage change. The zone history reads
{esc(hist_keys)}, then {esc(flagged.nov_key)} in November, from an account update on Nov 1 with no move or service
transfer behind it. Volume conservation passes because every kWh is priced, and the system comparison passes because the
wrong zone was migrated into the new engine too. Fix: correct the account setup and rebill
({money(wz_impact)} back to the customer for November).</p>
</section>

<section id="s3"><h2><span class="num">3</span>System comparison</h2>
<p>Same usage, both engines, matched line by line on meter, charge and data quality.</p>
{run_tbl}
<p>The missing delivery line shows up in every run. The on-peak energy difference does not: October's run on Nov 2
matches, and every run after the December 1 rate change differs by exactly the size of that change, on on-peak energy only.
That pattern points to effective dating.</p>
<h3>November final run (Dec 10), every difference</h3>
{fin_tbl}
</section>

<section id="s4"><h2><span class="num">4</span>Effective-date check</h2>
<p>For each disputed line, which rate was effective for the service period? That decides which engine is right.</p>
{eff_tbl}
<p class="callout">The new engine priced November usage with a rate that starts after November ends: it looks up rates by
run date. Fix the new engine to rate by service period before cutover. The same error reaches the January true-up, which is
recalculated after the cutoff; here it is small because both November runs fall after December 1 and carry the same wrong rate, so only the
on-peak kWh that changed between the initial and final data is mispriced.</p>
{tu_tbl}
</section>

<section><h2>Handed to billing</h2>
<ul class="files">
<li><code>output/rated_charges.csv</code> — every rated line from both engines and all runs: quantity, rate, amount, rate ID and effective dates, source version and quality flag.</li>
<li><code>output/rating_exceptions.csv</code> — the findings: unpriced usage, delta-from-normal flags, and engine differences with the effective-date verdict.</li>
</ul></section>

<footer><p>Synthetic data (seed {seed}); November usage from the metering stage. Scenario design from real settlement work by
Archer Opitz; built with AI assistance.</p></footer>
</main></body></html>"""
