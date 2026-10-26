"""HTML report for the collection stage."""
import os
from datetime import date

from m2c.metering.report import esc, table
from m2c.rating.report import CSS as RATING_CSS, money, status

CSS = RATING_CSS + """
.chain{list-style:none;padding:0;margin:0 0 18px;counter-reset:c}
.chain li{position:relative;padding:0 0 16px 34px;border-left:2px solid var(--rule);margin-left:11px}
.chain li:last-child{border-left-color:transparent}
.chain li::before{counter-increment:c;content:counter(c);position:absolute;left:-12px;top:-2px;width:22px;height:22px;
  border-radius:50%;background:var(--panel);border:1px solid var(--rule);font-size:.78rem;text-align:center;line-height:21px;color:var(--ink2)}
.chain b{color:var(--ink)}
.chain .where{font-size:.82rem;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;display:block}
.evidence{border:1px solid var(--rule);border-radius:6px;padding:18px 20px;margin:0 0 18px}
.evidence dl{display:grid;grid-template-columns:minmax(120px,190px) 1fr;gap:8px 18px;margin:0}
.evidence dt{color:var(--muted);font-size:.9rem}
.evidence dd{margin:0}
main a{color:var(--ink)}
.reissue{display:grid;grid-template-columns:minmax(160px,220px) 1fr;gap:20px;align-items:start;margin:0 0 18px}
.reissue a.thumb{display:block;border:1px solid var(--rule);border-radius:4px;overflow:hidden;background:#fff}
.reissue img{width:100%;height:auto;display:block}
@media (max-width:560px){.evidence dl,.reissue{grid-template-columns:1fr}.evidence dt{margin-top:6px}}
"""

D = date.fromisoformat


def _d(s):
    x = D(s[:10])
    return f"{x.strftime('%b')} {x.day}"


def _dt(s):
    x = D(s[:10])
    return f"{x.strftime('%b')} {x.day}, {s[11:16]}"


def render(r, seed, out_dir):
    cash, sp, bal, pairs, reads = r["cash"], r["short_pays"], r["balances"], r["pairs"], r["reads"]
    t, log, intr, aging = r["traces"][0], r["log"][0], r["interest"], r["aging"]
    s0, p0 = sp.iloc[0], pairs.iloc[0]
    late = reads[reads["side"] == "Neither (timing)"].iloc[0]
    paid = cash[cash["account_id"] == s0.account_id].iloc[0]
    rh = r["reissue"]["header"]
    reissue_ok = r["reissue"]["references"]["passes"].all()

    # ---- cash application
    cash_tbl = table(["Received", "Reference", "Payer", "Amount", "Remittance says", "Matched to", "Matched by", "Result"],
                     [[_d(x.received_date), esc(x.reference), esc(x.payer), money(x.amount),
                       esc(x.invoice_ref or f"Account {x.account_ref}"), esc(x.invoice_no or "—"), esc(x.matched_by),
                       status("caught", f"{x.result}, {money(-x.difference)}") if x.result != "Exact match"
                       else status("na", x.result)] for x in cash.itertuples()], num=(3,))

    # ---- matrix
    na = lambda s=None: status("na", s) if s else "—"
    matrix = table(["", "1. Match", "2. Classify", "3. Vs lines", "4. Pair", "5. Reads",
                    "6. Trace"], [
        [f"Short-pay ({esc(s0.account_id)})", na("Invoice no."), status("caught", "Short-pay"),
         status("caught", "One line"), na("Not flagged"), na("Read matches"), status("caught", "Invoicing")],
        [f"Misapplied ({esc(p0.belongs_to)} → {esc(p0.posted_to)})", na("Account + amount"),
         status("missed", "Exact"), na(), status("caught", "Paired"), na(), na()],
        [f"Late read ({esc(late.account_id)})", na("Account + amount"), status("missed", "Exact"), na(),
         na(), status("caught", "Timing gap"), na()],
    ], cls="matrix")

    # ---- 3: lines on the short-paid invoice
    inv_lines = r.get("inv_lines")
    lines_tbl = table(["Printed line", "Section", "Amount", "Equals the shortfall?"],
                      [[esc(x["label"]), esc(x["section"].title()), money(x["amount"], True),
                        status("caught", "Yes") if abs(x["amount"] - s0.shortfall) < 0.005 else ""]
                       for x in inv_lines], num=(2,)) if inv_lines else ""

    # ---- 4
    bal_tbl = table(["Account", "Invoiced", "Posted in AR", "Received per bank", "Balance as posted", "Unexpected"],
                    [[esc(x.account_id), money(x.invoiced), money(x.posted), money(x.received), money(x.balance_posted),
                      (status("missed", f"Unpaid {money(x.unexpected)}") if x.unexpected > 0.004 else
                       status("missed", f"Credit {money(-x.unexpected)}") if x.unexpected < -0.004 else "")]
                     for x in bal.itertuples()], num=(1, 2, 3, 4))
    corr_tbl = table(["Posted", "Account", "Amount", "Reference", "Note"],
                     [[_d(x.posted_date), esc(x.account_id), money(x.amount, True), esc(x.reference), esc(x.note)]
                      for x in r["corrections"].itertuples()], num=(2,))
    i2 = intr.set_index("account_id")
    intr_tbl = table(["Account", "Feb 1 interest as posted", "Correct", "Difference", "Why"],
                     [[esc(a), money(x.as_posted), money(x.correct), money(x.difference),
                       esc(x.why.capitalize()) if x.why else ""]
                      for a, x in i2.iterrows() if x.as_posted or x.correct], num=(1, 2, 3))

    # ---- 5
    reads_tbl = table(["Account", "Meter", "November on invoices", "Invoiced kWh", "Read kWh (submitted)", "Read",
                       "Submitted", "Result"],
                      [[esc(x.account_id), esc(x.meter_id), esc(x.basis), f"{x.invoiced_kwh:,.3f}", f"{x.read_kwh:,.0f}",
                        esc(x.read_type), _dt(x.submitted_at),
                        status("na", "Matches") if x.side == "Neither" else status("caught", "Timing gap")
                        if x.side == "Neither (timing)" else status("missed", x.side)]
                       for x in reads.itertuples()], num=(3, 4))

    # ---- 6: trace
    ch = t["rating"]["charges"]
    names = {"ENERGY_ON": "Energy, on-peak", "ENERGY_OFF": "Energy, off-peak", "DELIVERY": "Delivery"}
    rating_tbl = table(["Charge", "Initial kWh", "Final kWh", "Initial", "Final", "Difference"],
                       [[names[c["charge"]], f"{c['initial_kwh']:,.3f}", f"{c['final_kwh']:,.3f}", money(c["initial"]),
                         money(c["final"]), money(c["difference"], True)] for c in ch]
                       + [["<b>True-up</b>", "", "", "", "", f"<b>{money(t['rating']['total'], True)}</b>"]],
                       num=(1, 2, 3, 4, 5))
    m, sub = t["metering"], t["submission"]
    ties = " ".join(status("caught" if ok else "missed", k) for k, ok in t["ties"].items())

    # ---- dispute + reissue
    thumb_png = f"invoices/{rh['invoice_no']}.png"
    thumb = (f'<img src="{thumb_png}" alt="Page 1 of reissued sample invoice {esc(rh["invoice_no"])}">'
             if os.path.exists(os.path.join(out_dir, thumb_png)) else esc(rh["invoice_no"]))

    # ---- aging
    views = list(dict.fromkeys(aging["view"]))
    dates = {v: aging[aging["view"] == v]["as_of"].iloc[0] for v in views}

    rows = []
    for a in aging["account_id"].unique():
        row = [esc(a)]
        for v in views:
            x = aging[(aging["view"] == v) & (aging["account_id"] == a)].rename(
                columns={"1–30": "b1", "31–60": "b2", "61–90": "b3", "90+": "b4"}).iloc[0]
            if x.credit:
                row.append(f"{money(x.credit)} credit")
            elif x.balance <= 0.004:
                row.append(money(0))
            else:
                b = "Current" if x.Current else "1–30" if x.b1 else "31–60" if x.b2 else "61–90" if x.b3 else "90+"
                row.append(money(x.balance) + ("" if b == "Current" else f", {b} days")
                           + (" (in dispute)" if x.in_dispute else ""))
        rows.append(row)
    aging_tbl = table(["Account", *[f"{v} ({_d(dates[v])})" for v in views]], rows)

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Collection Diagnostics</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body><main>

<header>
<nav class="stagenav" aria-label="Stages"><a href="metering_report.html">1 Metering</a><a href="rating_report.html">2 Rating</a><a href="billing_report.html">3 Billing</a><a href="invoicing_report.html">4 Invoicing</a><span>5 Collection</span></nav>
<p class="kicker">Meter-to-cash demo · Stage 5 of 5: Collection</p>
<h1>The short-pay was a question</h1>
<p class="lede">{esc(s0.account_id)} paid {money(paid.amount)} against a {money(paid.invoice_amount)} invoice. The
{money(s0.shortfall)} it held back is exactly the bare “Adjustment” line from invoicing, so this isn't a cash-flow problem;
it's a question the invoice never answered. Two other accounts look wrong for different reasons: {esc(p0.belongs_to)} paid on time
but shows unpaid because its check was keyed to {esc(p0.posted_to)}, and {esc(late.account_id)}'s November is still on
estimated usage because its meter read came in after the cutoff. Six checks sort out which is which.</p>
</header>

<section><h2>January cash</h2>
<p>Everything the bank received through January 22, the day after the invoices fell due, matched to an invoice.</p>
{cash_tbl}
</section>

<section><h2>Which check catches what</h2>
{matrix}
<p>Matching and classifying only see the cash. Two of the three problems arrive as exact payments; they turn up
when the ledger is compared to the bank, and the invoice to the reads.</p>
</section>

<nav class="steps" aria-label="Steps"><a href="#s1">1–2 Match and classify</a><a href="#s3">3 Short-pay vs lines</a>
<a href="#s4">4 Pair balances</a><a href="#s5">5 Submitted reads</a><a href="#s6">6 Trace back</a>
<a href="#dispute">Dispute log</a><a href="#aging">Aging</a></nav>

<section id="s1"><h2><span class="num">1–2</span>Match and classify</h2>
<p>Each receipt is matched by the invoice number on the remittance; failing that, by account and amount; failing that,
by amount and date. Then it is classified: exact match, short-pay, overpayment, or unapplied cash. Three of the four
are exact. {esc(p0.belongs_to)}'s check carried no invoice number, only “{esc(p0.remittance_says)}” on the memo, so it
matched on account and amount. The bank knows whose payment it is; the question is whether AR does.</p>
</section>

<section id="s3"><h2><span class="num">3</span>Short-pay against invoice lines</h2>
<p>The shortfall on {esc(s0.invoice_no)} is {money(s0.shortfall)}. Of the {s0.lines_checked} printed lines, one matches
it exactly: the adjustment with no reference.</p>
{lines_tbl}
<p class="callout">A shortfall that equals one line points to a dispute of that line. A customer short on cash pays a
round number or nothing; a customer who doesn't recognize a charge pays everything else. A dispute is opened the same
day, and interest on the {money(s0.shortfall)} is suspended while it is open.</p>
</section>

<section id="s4"><h2><span class="num">4</span>Pair unexpected balances</h2>
<p>For each account, what AR has posted against what the bank says was received.</p>
{bal_tbl}
<p class="callout">{esc(p0.belongs_to)} shows {money(p0.amount)} unpaid that the bank says was received on {_d(p0.received_date)},
and {esc(p0.posted_to)} shows a {money(p0.amount)} credit posted the same day. Same amount, same date, opposite signs: one
payment on the wrong account. The remittance memo said {esc(p0.remittance_says)}; it was keyed as {esc(p0.posted_to)}
({esc(p0.cause)}). {esc(p0.posted_to)} had already paid its own invoice by autopay, so the credit had no explanation.</p>
<p>The correction reverses the posting and reposts it to {esc(p0.belongs_to)}, effective the date the bank received it:</p>
{corr_tbl}
<p>Left alone, the February 1 bill would have charged interest that isn't owed:</p>
{intr_tbl}
</section>

<section id="s5"><h2><span class="num">5</span>Invoice against submitted reads</h2>
<p>For each account, November's kWh as invoiced so far against the register read as it was submitted, and when it was
submitted against the true-up cutoff ({_dt(late.cutoff)}).</p>
{reads_tbl}
<p class="callout">{esc(late.account_id)}'s meter read came in flagged, so a field verification was ordered. The tech confirmed
the read December 28 and it was keyed {_dt(late.submitted_at)}, after the cutoff. The January 1 true-up ran without it, so
November is still billed on estimated usage. Nothing is wrong with either number: the read arrived too late to be picked up.
The February 1 invoice carries the true-up ({money(late.pending_true_up, True)}).</p>
<p>From the payer's side of a counterparty invoice, every mismatch traced to one of two causes: the data submitted wasn't
what we believed we had submitted, or the counterparty didn't pick it up, usually because of timing. When an invoice and
your records disagree, check both before deciding which side is wrong. For {esc(s0.account_id)} this check does the opposite
job: the invoiced kWh match the read, submitted on time, so the disputed charge is right.</p>
</section>

<section id="s6"><h2><span class="num">6</span>Trace back</h2>
<p>The disputed line, followed upstream to where it came from.</p>
<ol class="chain">
<li><span class="where">Invoicing</span><b>{esc(t['invoice']['invoice_no'])}: “{esc(t['invoice']['label'])}” {money(t['invoice']['amount'], True)}</b>,
no original invoice, no service period, no quantities.</li>
<li><span class="where">Billing</span><b>{esc(t['statement']['description'])}: {money(t['statement']['amount'], True)}</b> on the
January 1 statement.</li>
<li><span class="where">Rating</span><b>November final minus November initial, on the corrected setup.</b>{rating_tbl}</li>
<li><span class="where">Metering</span><b>{m['intervals']} estimated intervals on {esc(m['meter_id'])}</b>, labeled
Nov {int(m['first'][8:10])}, {esc(m['first'][11:])} to {esc(m['last'][11:])}, interpolated at {m['estimated_kwh']:,.3f} kWh. The meter read says
{m['actual_kwh']:,.3f} kWh: the gap covered an overnight furnace run, and a straight line can't see it. Difference
{m['difference_kwh']:+,.3f} kWh.</li>
<li><span class="where">Submission</span><b>{esc(sub['read_type'])} read submitted {_dt(sub['submitted_at'])}</b>, well before
the cutoff; invoiced kWh {sub['invoiced_kwh']:,.3f} against a read of {sub['read_kwh']:,.0f}.</li>
</ol>
<p>{ties}</p>
<p class="callout">The amount started in metering, and that part is the process working: estimate, then true up. The problem
started in invoicing, where the true-up lost its reference. The fix goes where the problem began, not where it surfaced:
the invoice lookup now includes adjusted invoices, and the invoice is reissued with the reference.</p>
</section>

<section id="dispute"><h2>Dispute log</h2>
<div class="evidence"><dl>
<dt>Dispute</dt><dd>{esc(log['dispute_id'])}, {esc(log['account_id'])}, opened {_d(log['opened'])}</dd>
<dt>Disputed line</dt><dd>{esc(log['invoice_no'])}, “{esc(log['line'])}”, {money(log['amount'])}</dd>
<dt>Customer said</dt><dd>{esc(log['customer_said'])}</dd>
<dt>What was submitted, and when</dt><dd>{esc(sub['read_type'])} register read, {sub['read_kwh']:,.0f} kWh for November, submitted
{_dt(sub['submitted_at'])}</dd>
<dt>Calculation cutoff</dt><dd>{_dt(sub['cutoff'])}; read was in on time</dd>
<dt>Upstream trace</dt><dd>{m['intervals']} estimated intervals, {m['difference_kwh']:+,.3f} kWh → true-up
{money(t['rating']['total'], True)} → statement → invoice line with no reference</dd>
<dt>Finding</dt><dd>{esc(log['finding'])}</dd>
<dt>Interest</dt><dd>{esc(log['interest'])}</dd>
<dt>Resolution</dt><dd>{esc(log['resolution'])}</dd>
<dt>Closed</dt><dd>{_d(log['closed'])}: {esc(log['closed_by'])}</dd>
</dl></div>
<p>With the evidence in one place, the dispute is settled in one exchange: here is the invoice it adjusts, the service period,
the intervals that were estimated, and the read that replaced them.</p>
<div class="reissue"><a class="thumb" href="invoices/{esc(rh['invoice_no'])}.pdf">{thumb}</a>
<div><p><b>{esc(rh['invoice_no'])}</b>, reissued {_d(rh['invoice_date'])}. Same charges; the adjustment now shows the
invoice it adjusts, the service period, and billed vs actual quantities per charge. Payment received is applied, leaving
{money(rh['reissue']['balance'])} due {_d(rh['reissue']['due'])}.</p>
<p>Invoicing's adjustment-reference check on the reissued invoice: {status("caught", "Passes") if reissue_ok else status("missed", "Fails")}</p>
<p><a href="invoices/{esc(rh['invoice_no'])}.pdf">Open the reissued invoice (PDF)</a></p></div></div>
</section>

<section id="aging"><h2>Aging</h2>
{aging_tbl}
<p>As posted, AR shows two past-due balances and a credit nobody can explain. After reposting, only the disputed
{money(s0.shortfall)} is open, and it accrues nothing. After the reissue and payment, every account is at zero.</p>
</section>

<section><h2>The whole chain</h2>
<ol class="chain">
<li><span class="where"><a href="metering_report.html">Metering</a></span>A six-hour collector outage on November 18 is
filled by interpolation; the initial bill uses the estimate.</li>
<li><span class="where"><a href="rating_report.html">Rating</a></span>The final run on actual reads prices the difference.</li>
<li><span class="where"><a href="billing_report.html">Billing</a></span>The January 1 statement carries it as a
{money(t['statement']['amount'], True)} true-up, no interest.</li>
<li><span class="where"><a href="invoicing_report.html">Invoicing</a></span>The lookup misses the adjusted December invoice,
and the true-up prints as a bare “Adjustment.”</li>
<li><span class="where">Collection</span>The customer pays everything else. The shortfall matches the line, the trace finds
the cause, the invoice is reissued, and the customer pays.</li>
</ol>
</section>

<section><h2>Outputs</h2>
<ul class="files">
<li><code>output/cash_application.csv</code>: each receipt, how it matched, and its classification.</li>
<li><code>output/dispute_log.csv</code>: the dispute, with its evidence package.</li>
<li><code>output/read_check.csv</code>: invoiced kWh against submitted reads and the cutoff.</li>
<li><code>output/ar_corrections.csv</code>: the reposting and the payment after reissue.</li>
<li><code>output/aging.csv</code>: aging as posted, after reposting, and after resolution.</li>
<li><code>output/invoices/{esc(rh['invoice_no'])}.pdf</code>: the reissued invoice.</li>
</ul></section>

<footer><p>Synthetic data (seed {seed}). Company, customers and addresses are fictional.
Scenario design from real settlement work by Archer Opitz; built with AI assistance.</p></footer>
</main></body></html>"""
