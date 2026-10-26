"""HTML report for the metering stage: one self-contained page."""
import html
import json
from datetime import timedelta

import pandas as pd

from m2c.metering.diagnose import (CONFLICT, DST_REPEAT, OUTAGE_MIN_INTERVALS,
                                   REGISTER_TOLERANCE_PCT, TRUE_DUP)

esc = lambda s: html.escape(str(s))


def kwh(x, places=1, signed=False):
    if abs(x) < 0.5 * 10 ** -places:
        x = 0.0
    s = f"{abs(x):,.{places}f}"
    if signed:
        return ("+" if x > 0 else "−" if x < 0 else "") + s
    return ("−" if x < 0 else "") + s


def chip(ok, yes="Within tolerance", no="Outside tolerance"):
    cls, mark, text = ("ok", "✓", yes) if ok else ("bad", "✗", no)
    return f'<span class="chip {cls}"><span aria-hidden="true">{mark}</span> {esc(text)}</span>'


def table(headers, rows, num=(), cls=""):
    th = "".join(f'<th class="{"n" if i in num else ""}" scope="col">{esc(h)}</th>' for i, h in enumerate(headers))
    tb = "".join("<tr>" + "".join(f'<td class="{"n" if i in num else ""}">{c}</td>' for i, c in enumerate(r))
                 + "</tr>" for r in rows)
    return f'<div class="scroll"><table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{tb}</tbody></table></div>'


def day_label(d):
    return pd.Timestamp(d).strftime("%b %-d")


# ------------------------------------------------------------------ chart data
def chart_data(r):
    """Nov 18 per meter: actual (final) and the interpolated estimate (initial) across the gap."""
    v = r["versions"]
    gap_day = pd.Timestamp(r["gaps"]["operating_dates"].iloc[0][0]).date()
    out = []
    for m in r["meters"]:
        sel = v[(v["meter_id"] == m) & (v["operating_date"] == gap_day)]
        fin = sel[sel["version"] == "final"].sort_values("interval_end_utc")
        ini = sel[sel["version"] == "initial"].sort_values("interval_end_utc")
        est_mask = (ini["quality"] == "estimated").values
        # Estimate line runs from the last actual before the gap to the first actual after it
        idx = [i for i, e in enumerate(est_mask) if e]
        lo, hi = max(idx[0] - 1, 0), min(idx[-1] + 1, len(ini) - 1)
        out.append({
            "meter": m,
            "labels": [l[-5:] for l in fin["label_local"]],
            "actual": [round(x, 3) for x in fin["kwh"]],
            "estimate": [round(x, 3) if lo <= i <= hi else None for i, x in enumerate(ini["kwh"])],
            "gap": [idx[0], idx[-1]],
        })
    return gap_day, out


CHART_JS = r"""
(function(){
  const data = JSON.parse(document.getElementById('gap-data').textContent);
  const NS='http://www.w3.org/2000/svg';
  const el=(t,a)=>{const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);return e;};
  document.querySelectorAll('.gapchart').forEach((host,ci)=>{
    const d=data[ci], W=320, H=190, L=40, R=18, T=12, B=26;
    const n=d.actual.length, vals=d.actual.concat(d.estimate.filter(v=>v!==null));
    const ymax=Math.max(...vals)*1.12;
    const x=i=>L+(W-L-R)*i/(n-1), y=v=>T+(H-T-B)*(1-v/ymax);
    const svg=el('svg',{viewBox:`0 0 ${W} ${H}`,role:'img','aria-label':`${d.meter}: actual vs estimated usage on the outage day`});
    // outage band
    svg.appendChild(el('rect',{x:x(d.gap[0]-0.5),y:T,width:x(d.gap[1]+0.5)-x(d.gap[0]-0.5),height:H-T-B,class:'band'}));
    // grid + y ticks
    const step=niceStep(ymax/3);
    for(let v=0;v<=ymax;v+=step){
      svg.appendChild(el('line',{x1:L,x2:W-R,y1:y(v),y2:y(v),class:'grid'}));
      const t=el('text',{x:L-6,y:y(v)+3,class:'tick','text-anchor':'end'});t.textContent=fmt(v);svg.appendChild(t);
    }
    ['00:15','06:00','12:00','18:00','00:00'].forEach(lb=>{
      const i=d.labels.indexOf(lb); if(i<0)return;
      const t=el('text',{x:x(i),y:H-8,class:'tick','text-anchor':'middle'});t.textContent=lb==='00:15'?'00:00':lb;svg.appendChild(t);
    });
    svg.appendChild(el('path',{d:path(d.actual),class:'line s1'}));
    svg.appendChild(el('path',{d:path(d.estimate),class:'line s2'}));
    // hover
    const cross=el('line',{y1:T,y2:H-B,class:'cross',visibility:'hidden'});svg.appendChild(cross);
    const dot1=el('circle',{r:4,class:'dot s1',visibility:'hidden'}), dot2=el('circle',{r:4,class:'dot s2',visibility:'hidden'});
    svg.appendChild(dot1);svg.appendChild(dot2);
    const hit=el('rect',{x:L,y:T,width:W-L-R,height:H-T-B,fill:'transparent'});svg.appendChild(hit);
    const tip=document.createElement('div');tip.className='tip';tip.hidden=true;
    host.appendChild(svg);host.appendChild(tip);
    hit.addEventListener('pointermove',ev=>{
      const pt=svg.createSVGPoint();pt.x=ev.clientX;pt.y=ev.clientY;
      const p=pt.matrixTransform(svg.getScreenCTM().inverse());
      const i=Math.max(0,Math.min(n-1,Math.round((p.x-L)/(W-L-R)*(n-1))));
      cross.setAttribute('x1',x(i));cross.setAttribute('x2',x(i));cross.setAttribute('visibility','visible');
      dot1.setAttribute('cx',x(i));dot1.setAttribute('cy',y(d.actual[i]));dot1.setAttribute('visibility','visible');
      const e=d.estimate[i];
      if(e!==null){dot2.setAttribute('cx',x(i));dot2.setAttribute('cy',y(e));dot2.setAttribute('visibility','visible');}
      else dot2.setAttribute('visibility','hidden');
      const inGap=i>=d.gap[0]&&i<=d.gap[1];
      tip.innerHTML=`<b>${d.labels[i]}</b>${inGap?' <span class="muted">(missing in Source B)</span>':''}<br>`+
        `<span class="sw s1"></span>Actual ${d.actual[i].toFixed(3)} kWh`+
        (e!==null&&inGap?`<br><span class="sw s2"></span>Estimate ${e.toFixed(3)} kWh`:'');
      tip.hidden=false;
      const hb=host.getBoundingClientRect(), sx=hb.width/W;
      let left=x(i)*sx+10; if(left>hb.width-170)left=x(i)*sx-180;
      tip.style.left=left+'px';tip.style.top='8px';
    });
    hit.addEventListener('pointerleave',()=>{tip.hidden=true;[cross,dot1,dot2].forEach(e=>e.setAttribute('visibility','hidden'));});
    function path(arr){let s='',pen=false;arr.forEach((v,i)=>{if(v===null){pen=false;return;}s+=(pen?'L':'M')+x(i).toFixed(1)+' '+y(v).toFixed(1);pen=true;});return s;}
  });
  function niceStep(r){const p=Math.pow(10,Math.floor(Math.log10(r)));const f=r/p;return (f<1.5?1:f<3?2:f<7?5:10)*p;}
  function fmt(v){return v<10?v.toFixed(v%1?1:0):v.toFixed(0);}
})();
"""


# ------------------------------------------------------------------ page
def render(r, seed):
    meters = r["meters"]
    s = r["summary"].set_index("meter_id")
    reg = r["register"].set_index("meter_id")
    days = r["days"]
    rep = r["repeats"]
    cal = r["cal"]
    dst_day = r["dst_day"]
    gaps = r["gaps"]
    acc = r["accuracy"]
    gap_day, cdata = chart_data(r)

    # Decomposition: Source B's shortfall explained interval by interval, using Source A's values
    a = r["a"]
    missing = acc.groupby("meter_id")["kwh_actual"].sum()
    naive_dropped = (r["b"].loc[~r["b"].index.isin(r["b_naive"].index)])
    dst_dropped = (naive_dropped[naive_dropped["timestamp_local"].isin(r["repeated_labels"])]
                   .groupby("meter_id")["kwh"].sum().reindex(meters, fill_value=0))

    # Lede numbers
    short_total = sum(-reg.loc[m, "source_b_clean_var"] for m in meters)
    g0 = gaps.iloc[0]

    # Step 1
    rep_labels = sorted(r["repeated_labels"])
    rep_rows = cal[cal["label_local"].isin(rep_labels)].sort_values("interval_end_utc")
    step1 = table(["Label (local)", "UTC offset", "Interval ends (UTC)"],
                  [[esc(x.label_local[-5:]), esc(x.utc_offset[:3] + ":" + x.utc_offset[3:]),
                    esc(x.interval_end_utc.strftime("%H:%M"))] for x in rep_rows.itertuples()], num=())
    cc = r["cal_counts"]
    n_dst = int((cc["expected"] != 96).sum())

    # Step 2: month-level vs per-day
    mn = r["month_naive"]
    month_rows = [[esc(m), f"{int(mn.loc[m,'raw_rows']):,}", f"{int(mn.loc[m,'assumed']):,}",
                   '<span class="chip warn"><span aria-hidden="true">!</span> Passes, but shouldn\'t</span>', f"{len(cal):,}"] for m in meters]
    month_tbl = table(["Meter", "Source B rows", "30 days × 96", "Month-level check", "Actually expected"],
                      month_rows, num=(1, 2, 4))

    def daytype(row):
        if row.operating_date == dst_day:
            return "dst"
        if row.raw_rows < row.expected:
            return "short"
        if row.raw_rows > row.unique_labels:
            return "dups"
        return "clean"
    days = days.assign(kind=days.apply(daytype, axis=1))
    per_day_rows = []
    for kind, title, reading in [
        ("dst", day_label(dst_day), "Repeats with different values: DST hour, keep all"),
        ("short", day_label(gap_day), "Contiguous missing: outage"),
        ("dups", "Days with re-sent rows", "Identical repeats: drop"),
        ("clean", "All other days", "Clean"),
    ]:
        cells = [esc(title)]
        for m in meters:
            dm = days[(days["meter_id"] == m) & (days["kind"] == kind)]
            if kind in ("dst", "short"):
                x = dm.iloc[0]
                cells.append(f"{x.expected} / {x.raw_rows} / {x.unique_labels}")
            elif kind == "dups":
                cells.append(f"{len(dm)} days, +{int((dm.raw_rows - dm.expected).sum())} rows")
            else:
                cells.append(f"{len(dm)} days")
        cells.append(esc(reading))
        per_day_rows.append(cells)
    per_day_tbl = table(["Day", *[f"{m}: expected / raw / unique" for m in meters], "Reading"],
                        per_day_rows)

    # Step 3
    cls_rows = []
    for m in meters:
        rm = rep[rep["meter_id"] == m]
        cls_rows.append([esc(m), str(int((rm["classification"] == DST_REPEAT).sum())),
                         str(int((rm["classification"] == TRUE_DUP).sum())),
                         str(int((rm["classification"] == CONFLICT).sum()))])
    cls_tbl = table(["Meter", "DST repeated hour (keep both)", "True duplicates (drop copy)", "Unexplained conflicts"],
                    cls_rows, num=(1, 2, 3))
    m1 = meters[0]
    dst_detail = rep[(rep["meter_id"] == m1) & (rep["classification"] == DST_REPEAT)].sort_values("timestamp_local")
    dst_tbl = table(["Label", "First arrival (CDT, −05:00)", "Second arrival (CST, −06:00)"],
                    [[esc(x.timestamp_local[-5:]), f"{x.values[0]:.3f} kWh", f"{x.values[1]:.3f} kWh"]
                     for x in dst_detail.itertuples()], num=(1, 2))
    dup_example = rep[(rep["meter_id"] == m1) & (rep["classification"] == TRUE_DUP)].iloc[0]

    # Both orders
    o = r["orders"]
    orders_tbl = table(
        ["Meter", f"{day_label(dst_day)} expected", "Dedup first: count", "Dedup first: evidence left",
         "Count first: raw / unique", "Count first: repeats with different values"],
        [[esc(x.meter_id), str(x.expected), str(x.dedup_first_count), "None (4 short, no clue why)",
          f"{x.count_first_raw} / {x.count_first_unique}", f"{x.count_first_repeats_diff_values} labels: DST hour"]
         for x in o.itertuples()], num=(1, 2, 4, 5))

    counts_tbl = table(["Count per meter", *meters, "Arithmetic"], [
        ["Expected, November 2026", *[f"{int(s.loc[m,'expected']):,}" for m in meters], "30 × 96 + 4 (DST day)"],
        ["Source A", *[f"{int(s.loc[m,'source_a_received']):,}" for m in meters], "Complete"],
        ["Source B, raw rows", *[f"{int(s.loc[m,'source_b_raw']):,}" for m in meters], "2,884 − 24 + 20"],
        ["Source B, correctly cleaned", *[f"{int(s.loc[m,'source_b_clean']):,}" for m in meters], "2,880 − 20 true duplicates"],
        ["Source B, naive dedup on local time", *[f"{int(s.loc[m,'source_b_naive']):,}" for m in meters], "2,880 − 20 − 4 DST intervals"],
    ], num=tuple(range(1, len(meters) + 1)))

    # Step 4
    reg_rows = []
    for m in meters:
        x = reg.loc[m]
        reg_rows.append([esc(m), kwh(x.register_usage, 0), kwh(x.tolerance, 2),
                         f"{kwh(x.source_a_var, 2, True)}<br>{chip(x.source_a_ok)}",
                         f"{kwh(x.source_b_clean_var, 2, True)}<br>{chip(x.source_b_clean_ok)}",
                         f"{kwh(x.source_b_naive_var, 2, True)}<br>{chip(x.source_b_naive_ok)}"])
    reg_tbl = table(["Meter", "Register usage (kWh)", f"Tolerance ({REGISTER_TOLERANCE_PCT:.1%})",
                     "Source A vs register", "Source B cleaned vs register", "Source B naive vs register"],
                    reg_rows, num=(1, 2, 3, 4, 5))
    dec_rows = []
    for m in meters:
        x = reg.loc[m]
        explained_clean = x.source_a_var - missing[m]
        explained_naive = explained_clean - dst_dropped[m]
        dec_rows.append([esc(m), kwh(-missing[m], 2, True), kwh(-dst_dropped[m], 2, True),
                         f"{kwh(explained_clean - x.source_b_clean_var, 3, True)} / "
                         f"{kwh(explained_naive - x.source_b_naive_var, 3, True)}"])
    dec_tbl = table(["Meter", f"Outage intervals ({day_label(gap_day)}), at Source A values",
                     "Second DST hour dropped by naive dedup", "Unexplained (cleaned / naive)"],
                    dec_rows, num=(1, 2, 3))

    # Step 5
    gap_tbl = table(["Meter", "First missing label", "Last missing label", "Intervals", "Hours", "Pattern"],
                    [[esc(x.meter_id), esc(x.first_label), esc(x.last_label), str(x.intervals),
                      f"{x.hours:g}", esc(x.pattern)] for x in gaps.itertuples()], num=(3, 4))

    # Estimation and true-up
    est_rows = []
    for m in meters:
        am = acc[acc["meter_id"] == m]
        est_rows.append([esc(m), str(int(s.loc[m, "estimated_intervals"])),
                         kwh(am["kwh_estimated"].sum(), 2), kwh(am["kwh_actual"].sum(), 2),
                         kwh(s.loc[m, "initial_total"], 2), kwh(s.loc[m, "final_total"], 2),
                         kwh(s.loc[m, "true_up_kwh"], 2, True)])
    est_tbl = table(["Meter", "Estimated intervals", "Estimated kWh", "Actual kWh (Source A)",
                     "Initial month total", "Final month total", "True-up (kWh)"],
                    est_rows, num=(1, 2, 3, 4, 5, 6))
    big = min(meters, key=lambda m: s.loc[m, "true_up_kwh"])

    charts = "".join(f'<figure class="gapfig"><figcaption>{esc(d["meter"])}</figcaption>'
                     f'<div class="gapchart"></div></figure>' for d in cdata)
    chart_table = table(["Meter", "Label", "Actual (kWh)", "Estimate (kWh)"],
                        [[esc(d["meter"]), esc(d["labels"][i]), f'{d["actual"][i]:.3f}', f'{d["estimate"][i]:.3f}']
                         for d in cdata for i in range(d["gap"][0], d["gap"][1] + 1)], num=(2, 3))

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Metering Diagnostics</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body><main>

<header>
<nav class="stagenav" aria-label="Stages"><span>1 Metering</span><a href="rating_report.html">2 Rating</a><a href="billing_report.html">3 Billing</a></nav>
<p class="kicker">Meter-to-cash demo · Stage 1 of 5: Metering</p>
<h1>Which feed is wrong, and why</h1>
<p class="lede">Two feeds report the same three meters for November 2026. Source B passes a month-level count
({int(s.loc[m1,'source_b_raw']):,} rows, exactly 30&nbsp;×&nbsp;96), yet it comes up
<strong>{kwh(short_total, 1)} kWh short</strong> of the register reads across the three meters. Counting intervals per day,
before any cleaning, finds the cause: a {g0.hours:g}-hour collector outage on {day_label(gap_day)}. Along the way it keeps the
repeated DST hour on {day_label(dst_day)} that a naive cleanup would have deleted.</p>
</header>

<nav class="steps" aria-label="Steps">
<a href="#s1">1 Calendar</a><a href="#s2">2 Count per day</a><a href="#s3">3 Repeated timestamps</a>
<a href="#s4">4 Register reads</a><a href="#s5">5 Gap pattern</a><a href="#est">Estimate &amp; true-up</a>
</nav>

<section id="s1"><h2><span class="num">1</span>Build the expected-interval calendar</h2>
<p>Derived from America/Chicago time-zone rules, not from the data: {len(cal):,} intervals in November, 96 per day except
{day_label(dst_day)}, which has {int(cc.loc[cc['operating_date']==dst_day,'expected'].iloc[0])} because clocks fall back at 02:00.
Labels are interval-ending, and each interval counts toward the day it starts. On {day_label(dst_day)}, four labels occur twice;
only the UTC offset tells them apart.</p>
{step1}</section>

<section id="s2"><h2><span class="num">2</span>Count per day, before deduplication</h2>
<p>A month-level check that assumes 96 intervals a day passes every meter. It shouldn't.</p>
{month_tbl}
<p>Per-day counts, raw rows and unique labels side by side, isolate every problem day. On {day_label(dst_day)} the raw count
matches the calendar but the unique count doesn't, so both counts are needed.</p>
{per_day_tbl}</section>

<section id="s3"><h2><span class="num">3</span>Compare values on repeated timestamps</h2>
<p>Repeated labels inside the calendar's repeated hour that carry different values are real intervals. Repeats with identical
values anywhere else are re-sent records.</p>
{cls_tbl}
<p>{esc(m1)}, {day_label(dst_day)}, the repeated hour. The first arrival is daylight time and the second is standard time:</p>
{dst_tbl}
<p class="note">Example of a true duplicate: {esc(m1)} at {esc(dup_example.timestamp_local)}, received twice at
{dup_example['values'][0]:.3f} kWh.</p>
<h3>Why the order matters</h3>
<p>Deduplicating first leaves {day_label(dst_day)} four intervals short with nothing left to explain it. Counting first keeps the evidence.</p>
{orders_tbl}
{counts_tbl}</section>

<section id="s4"><h2><span class="num">4</span>Compare totals to register reads</h2>
<p>Register reads are the source of record. Source A reconciles on every meter; Source B does not, cleaned or not.
This answers <em>which</em> source is off.</p>
{reg_tbl}
<p>The shortfall is fully explained by specific intervals: the outage, plus the second DST hour if deduplicated naively.</p>
{dec_tbl}</section>

<section id="s5"><h2><span class="num">5</span>Read the gap pattern</h2>
<p>Every interval missing from cleaned Source B falls in one contiguous run per meter, on the same day and at the same
time for all three meters. A run of {OUTAGE_MIN_INTERVALS} or more intervals points to a communication outage rather than a
device fault, and a shared run across meters points to the collector. This answers <em>why</em>.</p>
{gap_tbl}</section>

<section id="est"><h2>Estimate, initial bill and true-up</h2>
<p>The December 1 bill uses Source B with the {int(s.loc[m1,'estimated_intervals'])} outage intervals estimated by
interpolation and flagged. When the meter is read in December, Source A replaces Source B, and the difference becomes the
true-up on the January 1 bill. Both versions are kept.</p>
<div class="legend"><span><span class="sw s1"></span>Actual (Source A)</span><span><span class="sw s2"></span>Interpolated estimate</span>
<span><span class="sw band"></span>Missing in Source B</span></div>
<div class="gapcharts">{charts}</div>
<details><summary>Table view of the estimated intervals</summary>{chart_table}</details>
{est_tbl}
<p>Interpolation draws a straight line across the gap. For {esc(big)}, demand ramps up near the end of the outage, so the
straight line overestimates and the true-up is a credit of {kwh(-s.loc[big,'true_up_kwh'], 1)} kWh. Every final total reconciles
to the register reads within tolerance.</p></section>

<section><h2>Handed to rating</h2>
<ul class="files">
<li><code>output/interval_usage.csv</code> — every interval per meter and version (initial, final), with UTC end time, local label and offset, and a quality flag.</li>
<li><code>output/validation_summary.csv</code> — per meter: expected vs received counts, duplicates removed, DST labels kept, estimated intervals, and variances to the register reads.</li>
</ul></section>

<footer><p>Synthetic data (seed {seed}): three meters, November 2026, 15-minute interval-ending data in America/Chicago time.
Scenario design from real settlement work by Archer Opitz; built with AI assistance.</p></footer>
</main>
<script type="application/json" id="gap-data">{json.dumps(cdata)}</script>
<script>{CHART_JS}</script>
</body></html>"""


CSS = """
:root{--bg:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#6f6e69;--rule:#dcdad4;--panel:#f2f1ed;
--s1:#2a78d6;--s2:#eb6834;--band:#efe9dc;--ok:#1d6b45;--ok-bg:#e1f0e7;--warn:#7a5200;--warn-bg:#f6ead0;--bad:#a3312b;--bad-bg:#f7e4e2}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){--bg:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;
--muted:#9c9b92;--rule:#34332f;--panel:#232321;--s1:#3987e5;--s2:#d95926;--band:#2c2a24;--ok:#7cc89c;--ok-bg:#1b3326;--warn:#e3b460;--warn-bg:#33281a;
--bad:#f08c84;--bad-bg:#3a1d1b}}
:root[data-theme="dark"]{--bg:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#9c9b92;--rule:#34332f;--panel:#232321;
--s1:#3987e5;--s2:#d95926;--band:#2c2a24;--ok:#7cc89c;--ok-bg:#1b3326;--warn:#e3b460;--warn-bg:#33281a;--bad:#f08c84;--bad-bg:#3a1d1b}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 "IBM Plex Sans",system-ui,sans-serif;font-variant-numeric:tabular-nums}
main{max-width:1040px;margin:0 auto;padding:48px 16px 64px}
.kicker{color:var(--muted);margin:0 0 6px;font-weight:500}
.stagenav{display:flex;gap:18px;font-size:.92rem;margin:0 0 6px}
.stagenav a{color:var(--ink2)}.stagenav span{color:var(--ink);font-weight:500}
h1{font-size:clamp(1.9rem,4.5vw,2.8rem);line-height:1.12;margin:0 0 16px;font-weight:600;letter-spacing:-.015em}
.lede{font-size:1.15rem;max-width:66ch;margin:0;color:var(--ink2)}
.lede strong{color:var(--ink)}
.steps{display:flex;flex-wrap:wrap;gap:6px 18px;margin:28px 0 40px;padding:12px 0;border-top:1px solid var(--rule);border-bottom:1px solid var(--rule)}
.steps a{color:var(--ink2);text-decoration:none;font-size:.92rem}.steps a:hover{color:var(--ink);text-decoration:underline}
section{margin:0 0 52px}
h2{font-size:1.35rem;font-weight:600;margin:0 0 10px;display:flex;align-items:baseline;gap:12px}
h2 .num{font-family:"IBM Plex Mono",monospace;font-weight:400;font-size:1rem;color:var(--muted)}
h3{font-size:1.05rem;font-weight:600;margin:28px 0 6px}
section p{max-width:72ch;color:var(--ink2);margin:0 0 14px}
.note{font-size:.92rem}
.scroll{overflow-x:auto;margin:0 0 18px}
table{border-collapse:collapse;width:100%;font-size:.93rem}
th,td{text-align:left;padding:8px 12px;border-bottom:1px solid var(--rule);vertical-align:top}
th{font-weight:500;color:var(--ink2);border-bottom:2px solid var(--ink)}
td.n,th.n{text-align:right}
.chip{display:inline-block;margin-top:3px;padding:1px 8px;border-radius:4px;font-size:.82rem;font-weight:500;white-space:nowrap}
.chip.ok{color:var(--ok);background:var(--ok-bg)}.chip.warn{color:var(--warn);background:var(--warn-bg)}.chip.bad{color:var(--bad);background:var(--bad-bg)}
.legend{display:flex;flex-wrap:wrap;gap:6px 20px;font-size:.9rem;color:var(--ink2);margin:6px 0 10px}
.sw{display:inline-block;width:14px;height:3px;border-radius:2px;vertical-align:middle;margin-right:6px}
.sw.s1{background:var(--s1)}.sw.s2{background:repeating-linear-gradient(90deg,var(--s2) 0 5px,transparent 5px 8px)}.sw.band{background:var(--band);height:12px;border:1px solid var(--rule)}
.gapcharts{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px;margin:0 0 12px}
.gapfig{margin:0;border:1px solid var(--rule);border-radius:6px;padding:10px 10px 4px;background:var(--bg)}
.gapfig figcaption{font-weight:600;font-size:.95rem;margin:0 0 2px 4px}
.gapchart{position:relative}
.gapchart svg{width:100%;height:auto;display:block}
.gapchart .band{fill:var(--band)}
.gapchart .grid{stroke:var(--rule);stroke-width:1}
.gapchart .tick{fill:var(--muted);font-size:10px;font-family:"IBM Plex Sans",sans-serif}
.gapchart .line{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.gapchart .line.s1{stroke:var(--s1)}.gapchart .line.s2{stroke:var(--s2);stroke-dasharray:5 3}
.gapchart .dot{stroke:var(--bg);stroke-width:2}.gapchart .dot.s1{fill:var(--s1)}.gapchart .dot.s2{fill:var(--s2)}
.gapchart .cross{stroke:var(--muted);stroke-width:1}
.tip{position:absolute;pointer-events:none;background:var(--bg);border:1px solid var(--rule);border-radius:6px;
padding:6px 9px;font-size:.82rem;line-height:1.45;box-shadow:0 2px 8px rgba(0,0,0,.12);min-width:160px;color:var(--ink)}
.tip .muted{color:var(--muted)}
details{margin:0 0 18px;color:var(--ink2);font-size:.92rem}summary{cursor:pointer;margin-bottom:8px}
.files{color:var(--ink2);max-width:72ch;padding-left:20px}.files li{margin:0 0 8px}
code{font-family:"IBM Plex Mono",monospace;font-size:.88em;background:var(--panel);padding:1px 5px;border-radius:3px}
footer p{color:var(--muted);font-size:.86rem;border-top:1px solid var(--rule);padding-top:16px;margin:0}
"""
