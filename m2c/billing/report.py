"""HTML report for the billing stage."""
import json
from datetime import date, timedelta

import pandas as pd

from m2c.billing import system as S
from m2c.metering.report import esc, table
from m2c.rating.report import CSS as RATING_CSS, money, status

CSS = RATING_CSS + """
.balchart{position:relative;max-width:720px;margin:0 0 8px}
.balchart svg{width:100%;height:auto;display:block}
.balchart .band{fill:var(--band)}
.balchart .grid{stroke:var(--rule);stroke-width:1}
.balchart .tick{fill:var(--muted);font-size:11px;font-family:"IBM Plex Sans",sans-serif}
.balchart .lbl{font-size:11px;font-family:"IBM Plex Sans",sans-serif;fill:var(--ink2)}
.balchart .line{fill:none;stroke-width:2;stroke-linejoin:round}
.balchart .line.s1{stroke:var(--s2);stroke-dasharray:5 3}.balchart .line.s2{stroke:var(--s1)}
.balchart .dot{stroke:var(--bg);stroke-width:2}.balchart .dot.s1{fill:var(--s2)}.balchart .dot.s2{fill:var(--s1)}
.balchart .cross{stroke:var(--muted);stroke-width:1}
.statement td.total{font-weight:600;color:var(--ink);border-top:2px solid var(--ink)}
"""

LINE_LABELS = [("PRIOR", "Prior balance (Dec 1 statement)"), ("PAYMENT", "Payments"), ("CREDIT", "Credits"),
               ("INTEREST", "Interest"), ("CURRENT", "Current charges, December"),
               ("TRUE_UP", "True-up, November"), ("AMOUNT_DUE", "Amount due")]

CHART_JS = r"""
(function(){
  const d = JSON.parse(document.getElementById('bal-data').textContent);
  const NS='http://www.w3.org/2000/svg', host=document.querySelector('.balchart');
  const el=(t,a)=>{const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);return e;};
  const W=720,H=250,L=64,R=110,T=14,B=30,n=d.days.length;
  const ymax=Math.ceil(Math.max(...d.billed)/500)*500;
  const x=i=>L+(W-L-R)*i/(n-1), y=v=>T+(H-T-B)*(1-v/ymax);
  const svg=el('svg',{viewBox:`0 0 ${W} ${H}`,role:'img','aria-label':'Unpaid balance by day, as billed and as received'});
  svg.appendChild(el('rect',{x:x(d.window[0]),y:T,width:x(d.window[1])-x(d.window[0]),height:H-T-B,class:'band'}));
  const wl=el('text',{x:(x(d.window[0])+x(d.window[1]))/2,y:T+12,class:'lbl','text-anchor':'middle'});wl.textContent='Interest accrues';svg.appendChild(wl);
  for(let v=0;v<=ymax;v+=500){svg.appendChild(el('line',{x1:L,x2:W-R,y1:y(v),y2:y(v),class:'grid'}));
    const t=el('text',{x:L-8,y:y(v)+4,class:'tick','text-anchor':'end'});t.textContent='$'+v.toLocaleString();svg.appendChild(t);}
  d.days.forEach((lb,i)=>{if(i%2)return;const t=el('text',{x:x(i),y:H-10,class:'tick','text-anchor':'middle'});t.textContent=lb;svg.appendChild(t);});
  const step=a=>{let s='';a.forEach((v,i)=>{s+=(i?`H${x(i).toFixed(1)}V${y(v).toFixed(1)}`:`M${x(0).toFixed(1)} ${y(v).toFixed(1)}`);});return s;};
  svg.appendChild(el('path',{d:step(d.actual),class:'line s2'}));
  svg.appendChild(el('path',{d:step(d.billed),class:'line s1'}));
  [['billed','s1','As billed (ledger)'],['actual','s2','As received (bank)']].forEach(([k,c,t])=>{
    const v=d[k][n-1], lab=el('text',{x:x(n-1)+8,y:y(v)+(k==='billed'?-6:14),class:'lbl'});lab.textContent=t;svg.appendChild(lab);});
  const cross=el('line',{y1:T,y2:H-B,class:'cross',visibility:'hidden'});svg.appendChild(cross);
  const d1=el('circle',{r:4,class:'dot s1',visibility:'hidden'}),d2=el('circle',{r:4,class:'dot s2',visibility:'hidden'});svg.appendChild(d2);svg.appendChild(d1);
  const hit=el('rect',{x:L,y:T,width:W-L-R,height:H-T-B,fill:'transparent'});svg.appendChild(hit);
  const tip=document.createElement('div');tip.className='tip';tip.hidden=true;host.appendChild(svg);host.appendChild(tip);
  const f=v=>'$'+v.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
  hit.addEventListener('pointermove',ev=>{const pt=svg.createSVGPoint();pt.x=ev.clientX;pt.y=ev.clientY;
    const p=pt.matrixTransform(svg.getScreenCTM().inverse());const i=Math.max(0,Math.min(n-1,Math.round((p.x-L)/(W-L-R)*(n-1))));
    cross.setAttribute('x1',x(i));cross.setAttribute('x2',x(i));cross.setAttribute('visibility','visible');
    d1.setAttribute('cx',x(i));d1.setAttribute('cy',y(d.billed[i]));d1.setAttribute('visibility','visible');
    d2.setAttribute('cx',x(i));d2.setAttribute('cy',y(d.actual[i]));d2.setAttribute('visibility','visible');
    const inw=i>=d.window[0]&&i<d.window[1];
    tip.innerHTML=`<b>${d.days[i]}</b>${inw?' <span class="muted">(interest accrues)</span>':''}<br><span class="sw s2" style="background:var(--s2)"></span>As billed ${f(d.billed[i])}<br><span class="sw s1"></span>As received ${f(d.actual[i])}`;
    tip.hidden=false;const hb=host.getBoundingClientRect(),sx=hb.width/W;let left=x(i)*sx+12;if(left>hb.width-200)left=x(i)*sx-210;tip.style.left=left+'px';tip.style.top='8px';});
  hit.addEventListener('pointerleave',()=>{tip.hidden=true;[cross,d1,d2].forEach(e=>e.setAttribute('visibility','hidden'));});
})();
"""


def _chart_data(r, account):
    d = r["data"]
    dec1 = d["dec1"].set_index("account_id")
    prior, due = float(dec1.loc[account, "amount_due"]), date.fromisoformat(dec1.loc[account, "due_date"])
    led = d["ledger"][d["ledger"]["account_id"] == account]
    bank = d["bank"][d["bank"]["account_id"] == account]
    start, end = date(2026, 12, 15), S.STATEMENT_DATE
    days, billed, actual, i, win = [], [], [], 0, [None, None]
    dd = start
    while dd <= end:
        days.append(f"{dd.strftime('%b')} {dd.day}")
        billed.append(round(prior - sum(float(p.amount) for p in led.itertuples() if date.fromisoformat(p.payment_date) <= dd), 2))
        actual.append(round(prior - sum(float(p.amount) for p in bank.itertuples() if date.fromisoformat(p.received_date) <= dd), 2))
        if dd == due + timedelta(days=S.GRACE_DAYS):
            win[0] = i
        if dd == end:
            win[1] = i
        dd += timedelta(days=1)
        i += 1
    return {"days": days, "billed": billed, "actual": actual, "window": win}


def _fmt_date(s):
    d = s if isinstance(s, date) else date.fromisoformat(s)
    return f"{d.strftime('%b')} {d.day}"


def render(r, seed):
    d, billed, corrected = r["data"], r["billed"], r["corrected"]
    accts = list(d["accounts"]["account_id"])
    desc = d["accounts"].set_index("account_id")["description"]
    rc = r["recalc"].set_index("account_id")
    bad = rc[~rc["matches"]].index[0]
    lb = r["ledger_bank"]
    slip = lb[~lb["date_matches"]].iloc[0]
    over = rc.loc[bad, "difference"]

    def stmt_table(st):
        rows = []
        for code, label in LINE_LABELS:
            cells = [esc(label)]
            for a in accts:
                g = st[(st["account_id"] == a) & (st["line_type"] == code)]
                cells.append(money(g["amount"].sum(), code not in ("PRIOR", "AMOUNT_DUE")) if len(g) else "—")
            if code == "AMOUNT_DUE":
                cells = [f"<b>{c}</b>" for c in cells]
            rows.append(cells)
        return table(["", *[f"{a} ({desc[a].lower()})" for a in accts]], rows, num=(1, 2, 3), cls="statement")

    ch = r["chain"]
    chain_tbl = table(["Account", "Dec 1 bill (Nov, as billed)", "Zone credit", "True-up", "Net for November",
                       "Corrected final rating", ""],
                      [[esc(x.account_id), money(x.dec1_billed), money(-x.credit, True), money(x.true_up, True),
                        money(x.net_november), money(x.corrected_final),
                        status("caught", "Ties") if x.ties else status("missed", "Off")] for x in ch.itertuples()],
                      num=(1, 2, 3, 4, 5))

    rf = r["roll_forward"]
    rf_tbl = table(["Account", "Sum of lines", "Amount due printed", "Prior = Dec 1 bill", "Payments = ledger", "Result"],
                   [[esc(x.account_id), money(x.lines_total), money(x.amount_due),
                     "Yes" if x.prior_matches_dec1 else "No", "Yes" if x.payments_match_ledger else "No",
                     status("na", "Ties") if x.ties and x.prior_matches_dec1 and x.payments_match_ledger
                     else status("caught", "Does not tie")] for x in rf.itertuples()], num=(1, 2))

    rc_tbl = table(["Account", "Billed interest", "Recalculated (bank dates)", "Difference", "Result"],
                   [[esc(a), money(x.billed_interest), money(x.recalculated), money(x.difference, True),
                     status("caught", "Mismatch") if not x.matches else status("na", "Matches")]
                    for a, x in rc.iterrows()], num=(1, 2, 3))

    def seg_tbl(segs):
        rows = [[f"{_fmt_date(s['from'])} to {_fmt_date(s['to'])}", money(s["balance"]), str(s["days"]),
                 f"${s['interest']:.4f}"] for s in segs]
        rows.append(["<b>Total</b>", "", str(sum(s["days"] for s in segs)),
                     f"<b>{money(round(sum(s['interest'] for s in segs), 2))}</b>"])
        return table(["Period", "Unpaid balance", "Days", "Interest"], rows, num=(1, 2, 3))
    per = r["periods"][bad]

    lb_tbl = table(["Reference", "Account", "Bank: received", "Ledger: payment date", "Ledger: entered on",
                    "Amount (both)", "Result"],
                   [[f"<code>{esc(x.reference)}</code>", esc(x.account_id_ledger), esc(x.received_date),
                     esc(x.payment_date), esc(x.entered_on), money(float(x.amount_bank)),
                     status("caught", f"Date off by {x.days_off} days") if not x.date_matches else status("na", "Matches")]
                    for x in lb.itertuples()], num=(5,))

    due_b = billed[(billed["account_id"] == bad) & (billed["line_type"] == "AMOUNT_DUE")]["amount"].iloc[0]
    due_c = corrected[(corrected["account_id"] == bad) & (corrected["line_type"] == "AMOUNT_DUE")]["amount"].iloc[0]
    cred = d["credits"].iloc[0]
    a2 = rc.loc["A-1002"]
    chart = _chart_data(r, bad)

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Billing Diagnostics</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body><main>

<header>
<nav class="stagenav" aria-label="Stages"><a href="metering_report.html">1 Metering</a><a href="rating_report.html">2 Rating</a><span>3 Billing</span><a href="invoicing_report.html">4 Invoicing</a></nav>
<p class="kicker">Meter-to-cash demo · Stage 3 of 5: Billing</p>
<h1>The balance ties. The interest doesn't.</h1>
<p class="lede">All three January 1 statements roll forward to the cent. Yet {esc(bad)} is charged
<strong>{money(over)} too much interest</strong>, because a {money(float(slip.amount_bank))} payment that arrived
{_fmt_date(slip.received_date)} was keyed as {_fmt_date(slip.payment_date)}. The amount was right, so nothing else looked wrong.
Only recalculating the interest independently, then checking the payment against the bank, shows it.</p>
</header>

<section><h2>The January 1 statements, as billed</h2>
<p>Terms: {S.RATE:.0%} a year, actual days ÷ {S.BASIS}, accrued daily on the unpaid December 1 balance after a
{S.GRACE_DAYS}-day grace period (from Dec 26). Payments and credits count from their dates; credits apply before interest.
The true-up carries no interest because it was not billed before January 1.</p>
{stmt_table(billed)}
</section>

<nav class="steps" aria-label="Steps"><a href="#chain">From rating</a><a href="#s1">1 Roll-forward</a>
<a href="#s2">2 Interest recalculation</a><a href="#s3">3 Balance periods</a><a href="#s4">4 Ledger vs bank</a></nav>

<section id="chain"><h2>What billing picks up from rating</h2>
<p>The December 1 bill was November's initial rating as production billed it, with M3 still in the wrong zone. Two
adjustments arrive on January 1: the zone fix as credit {esc(cred.credit_id)} ({money(float(cred.amount))}, effective
{_fmt_date(cred.effective_date)}), and the true-up, which replaces metering's estimate with actuals. Together they bring every
account to November's corrected final rating.</p>
{chain_tbl}
</section>

<section id="s1"><h2><span class="num">1</span>Balance roll-forward</h2>
<p>Every statement's lines add up to its printed amount due, the prior balance matches the December 1 statement, and the
payments match the ledger. That rules out a missing, duplicated or wrong-amount payment, and it is why nothing looked wrong.</p>
{rf_tbl}
</section>

<section id="s2"><h2><span class="num">2</span>Independent interest recalculation</h2>
<p>Recompute interest outside the billing system, from the terms and the cash the bank actually received.
{esc(bad)} doesn't match. {esc("A-1002")}'s late payment does carry interest ({money(a2.billed_interest)}), and the
recalculation agrees, so the formula itself is fine.</p>
{rc_tbl}
</section>

<section id="s3"><h2><span class="num">3</span>Interest by balance period</h2>
<p>Breaking {esc(bad)}'s interest into periods of constant balance puts the difference in one place: the billing system
has the balance dropping on {_fmt_date(slip.payment_date)}, while the money arrived {_fmt_date(slip.received_date)}, before the
grace period even ended. That date is the lead.</p>
<div class="legend"><span><span class="sw s2" style="background:repeating-linear-gradient(90deg,var(--s2) 0 5px,transparent 5px 8px)"></span>As billed (ledger date)</span>
<span><span class="sw s1"></span>As received (bank date)</span><span><span class="sw band"></span>Interest accrues</span></div>
<div class="balchart"></div>
<div class="gapcharts" style="grid-template-columns:repeat(auto-fit,minmax(300px,1fr))">
<div><h3>As billed</h3>{seg_tbl(per['billed'])}</div>
<div><h3>Recalculated</h3>{seg_tbl(per['recalc'])}</div>
</div>
<p class="note">{money(float(slip.amount_bank))} × {S.RATE:.0%} ÷ {S.BASIS} × 2 days = {money(over)}: two days of interest on
money that had already been paid.</p>
</section>

<section id="s4"><h2><span class="num">4</span>Ledger against bank remittance</h2>
<p>Match each ledger entry to the bank on its reference. One record disagrees on the date only.</p>
{lb_tbl}
<p class="callout">The bank received <code>{esc(slip.reference)}</code> on {_fmt_date(slip.received_date)}, and the reference
itself carries that date. The ledger entry was keyed on {_fmt_date(slip.entered_on)} with a payment date of
{_fmt_date(slip.payment_date)}, {int(slip.days_off)} days after the day it was entered, which is impossible. The payment record is wrong, not the
interest formula. Fix: correct the date, recalculate, and the amount due falls from {money(due_b)} to {money(due_c)}.</p>
</section>

<section><h2>Handed to invoicing</h2>
<ul class="files">
<li><code>output/statements.csv</code> — every statement line per account, as billed and corrected.</li>
<li><code>output/billing_checks.csv</code> — roll-forward, interest recalculation and ledger-vs-bank results per account.</li>
</ul></section>

<footer><p>Synthetic data (seed {seed}); November and December charges from the rating stage. Scenario design from real
settlement work by Archer Opitz; built with AI assistance.</p></footer>
</main>
<script type="application/json" id="bal-data">{json.dumps(chart)}</script>
<script>{CHART_JS}</script>
</body></html>"""
