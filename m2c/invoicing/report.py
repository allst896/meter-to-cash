"""HTML report for the invoicing stage."""
import os

from m2c.metering.report import esc, table
from m2c.rating.report import CSS as RATING_CSS, money, status

CSS = RATING_CSS + """
.gallery{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin:0 0 14px}
.gallery figure{margin:0}
.gallery a{display:block;border:1px solid var(--rule);border-radius:4px;overflow:hidden;background:#fff}
.gallery img{width:100%;height:auto;display:block}
.gallery figcaption{font-size:.88rem;color:var(--ink2);margin-top:6px}
.links{font-size:.92rem;color:var(--ink2)}
"""


def _row_status(df, inv, col="passes"):
    sub = df[df["invoice_no"] == inv]
    if sub.empty:
        return status("na", "Nothing to check")
    return status("missed", "Passes") if sub[col].all() else status("caught", "Caught")


def render(r, seed, out_dir):
    h, L = r["headers"], r["lines"]
    jan = h[h["invoice_date"] == "2027-01-01"]
    dec = h[h["invoice_date"] == "2026-12-01"]
    cls, ref, rc = r["classification"], r["references"], r["recompute"]
    mis = cls[~cls["passes"]].iloc[0]
    bare = ref[~ref["passes"]].iloc[0]
    clean = [i for i in h["invoice_no"] if i not in (mis.invoice_no, bare.invoice_no)]

    def thumb(inv, cap):
        png = f"invoices/{inv}.png"
        img = f'<img src="{png}" alt="Page 1 of sample invoice {esc(inv)}" loading="lazy">' \
            if os.path.exists(os.path.join(out_dir, png)) else esc(inv)
        return f'<figure><a href="invoices/{esc(inv)}.pdf">{img}</a><figcaption>{cap}</figcaption></figure>'

    caps = {mis.invoice_no: f"{esc(mis.invoice_no)}: true-up printed as December energy",
            bare.invoice_no: f"{esc(bare.invoice_no)}: bare “Adjustment”"}
    gallery = "".join(thumb(i, caps.get(i, f"{esc(i)}: correct")) for i in jan["invoice_no"])
    dec_links = " · ".join(f'<a href="invoices/{esc(i)}.pdf">{esc(i)}</a>' for i in dec["invoice_no"])

    clean_status = lambda df: status("na", "Passes") if df[df["invoice_no"].isin(clean)]["passes"].all() \
        else status("caught", "Flagged")
    tie = r["tie_out"]
    matrix = table(["", "1. Tie-out", "2. Classification", "3. Required fields", "4. Adjustment references",
                    "5. Customer recompute"], [
        [f"Mislabeled line ({esc(mis.invoice_no)})", _row_status(tie, mis.invoice_no), _row_status(cls, mis.invoice_no),
         _row_status(r["required"], mis.invoice_no), status("na", "Not printed as an adjustment"),
         _row_status(rc, mis.invoice_no)],
        [f"Missing reference ({esc(bare.invoice_no)})", _row_status(tie, bare.invoice_no),
         _row_status(cls, bare.invoice_no), _row_status(r["required"], bare.invoice_no),
         _row_status(ref, bare.invoice_no), _row_status(rc, bare.invoice_no)],
        [f"The other {len(clean)} invoices", clean_status(tie), clean_status(cls), clean_status(r["required"]),
         clean_status(ref) if ref["invoice_no"].isin(clean).any() else status("na", "—"), clean_status(rc)],
    ])

    tie_tbl = table(["Invoice", "Statement lines", "Carried on invoice", "Amount due (invoice)", "Amount due (statement)", "Result"],
                    [[esc(x.invoice_no), str(x.statement_lines), str(x.carried), money(x.amount_due),
                      money(x.statement_amount_due), status("na", "Ties") if x.passes else status("caught", "Off")]
                     for x in tie.itertuples()], num=(1, 2, 3, 4))

    cur = L[(L["invoice_no"] == mis.invoice_no) & (L["section"] == "CURRENT") & (L["block"] == "detail")]
    real_energy = cur[(cur["source"] == "CURRENT")]["amount"].sum()
    printed_current = cur["amount"].sum()
    cls_tbl = table(["Invoice", "Printed as", "Amount", "Statement line type", "Belongs in", "Result"],
                    [[esc(x.invoice_no), f"{esc(x.section.title())}: “{esc(x.label)}”", money(x.amount), esc(x.source),
                      esc(x.expected_section.title()), status("caught", "Wrong section")]
                     for x in cls[~cls["passes"]].itertuples()], num=(2,))

    req = r["required"]
    req_tbl = table(["Invoice", "Required fields", "Estimated usage", "Disclosure", "Result"],
                    [[esc(x.invoice_no), "All present" if not x.missing_fields else esc(", ".join(x.missing_fields)),
                      "Yes" if x.estimated_usage else "No", esc(x.disclosure or "Not needed"),
                      status("na", "Passes") if x.passes else status("caught", "Fails")] for x in req.itertuples()])

    ref_tbl = table(["Invoice", "Printed label", "Amount", "Original invoice", "Service period", "Result"],
                    [[esc(x.invoice_no), esc(x.label), money(x.amount, True), esc(x.ref_invoice or "—"),
                      esc(x.ref_period or "—"),
                      status("na", "Complete") if x.passes else status("caught", "; ".join(x.problems).capitalize())]
                     for x in ref.itertuples()], num=(2,))
    reg = r["register"]
    reg_tbl = table(["Invoice", "Account", "Service", "Status"],
                    [[esc(x.invoice_no), esc(x.account_id), f"{esc(x.service_from)} to {esc(x.service_to)}", esc(x.status)]
                     for x in reg[reg["invoice_date"] == "2026-12-01"].itertuples()])

    rc_sum = rc.groupby("invoice_no")["passes"].agg(["size", "sum"]).reset_index()
    rc_tbl = table(["Invoice", "Amounts checked", "Recomputed from the page", "Result"],
                   [[esc(x.invoice_no), str(x.size), str(x.sum),
                     status("na", "All recompute") if x.size == x.sum else status("caught", f"{x.size - x.sum} cannot be checked")]
                    for x in rc_sum.itertuples()], num=(1, 2))
    rc_fail = table(["Invoice", "Section", "Printed", "Amount", "Why"],
                    [[esc(x.invoice_no), esc(x.section.title()), esc(x.label), money(x.amount), esc(x.how)]
                     for x in rc[~rc["passes"]].itertuples()], num=(3,))

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Invoicing Diagnostics</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body><main>

<header>
<nav class="stagenav" aria-label="Stages"><a href="metering_report.html">1 Metering</a><a href="rating_report.html">2 Rating</a><a href="billing_report.html">3 Billing</a><span>4 Invoicing</span></nav>
<p class="kicker">Meter-to-cash demo · Stage 4 of 5: Invoicing</p>
<h1>Right numbers, wrong page</h1>
<p class="lede">Every invoice ties to its statement to the cent. Two of them still fail the customer. On
{esc(mis.invoice_no)} the November true-up is printed as December energy, and on {esc(bare.invoice_no)} it is a bare
{money(bare.amount)} “Adjustment” with nothing to say what it is for. Neither is a wrong number. Both come from
how the invoicing system builds the page, so a tie-out can't see them, but a customer checking the bill would.</p>
</header>

<section><h2>The January 1 invoices</h2>
<p>Sample PDFs, generated from the corrected billing statements. Click to open.</p>
<div class="gallery">{gallery}</div>
<p class="links">December 1 invoices, with the estimated-usage disclosure: {dec_links}</p>
</section>

<section><h2>Which check catches what</h2>
{matrix}
</section>

<nav class="steps" aria-label="Steps"><a href="#s1">1 Tie-out</a><a href="#s2">2 Classification</a>
<a href="#s3">3 Required fields</a><a href="#s4">4 Adjustment references</a><a href="#s5">5 Customer recompute</a></nav>

<section id="s1"><h2><span class="num">1</span>Statement-to-invoice tie-out</h2>
<p>Every statement line is carried onto its invoice once, with the same amount, and the amount due matches. This passes
on all six invoices, including both problem ones.</p>
{tie_tbl}
</section>

<section id="s2"><h2><span class="num">2</span>Classification</h2>
<p>Each printed line must sit in the section its statement line belongs in. One doesn't.</p>
{cls_tbl}
<p class="callout">The commercial invoice template maps true-ups into current charges as “Energy.” {esc(mis.invoice_no)}
shows December current charges of {money(printed_current)} when December's actual charges were {money(real_energy)}, so the
customer is told they used less than they did, and the November correction disappears from view. Residential and
industrial templates map true-ups correctly; one bad mapping mislabels every invoice that uses it. Fix the template.</p>
</section>

<section id="s3"><h2><span class="num">3</span>Required fields</h2>
<p>Account, bill-to, invoice number and date, service period, due date and remit-to, plus the estimated-usage
disclosure on any invoice that bills estimated usage, with actual and estimated kWh adding up to the kWh billed.</p>
{req_tbl}
</section>

<section id="s4"><h2><span class="num">4</span>Adjustment references</h2>
<p>A printed prior-period adjustment must say which invoice it adjusts, for what service period, and the billed and actual
quantities behind it.</p>
{ref_tbl}
<p class="callout">The true-up looks up its original invoice in the register, but only among invoices with status ISSUED.
Credit CR-0003 marked {esc(bare.account_id)}'s December invoice ADJUSTED, so the lookup came back empty and the template fell
back to a bare “Adjustment.” The amount is right and the invoice ties out; the customer just can't tell what
{money(bare.amount)} is for. Fix the lookup to include adjusted invoices, then reissue.</p>
{reg_tbl}
</section>

<section id="s5"><h2><span class="num">5</span>Customer-view recompute</h2>
<p>Using only what is printed, recompute every amount: quantity × rate, the interest periods, the credit and true-up
detail, each subtotal, and the amount due. Anything that can't be recomputed from the page is something the customer
needs and doesn't have.</p>
{rc_tbl}
{rc_fail}
<p>The customer who received the bare adjustment is the one to watch: an unexplained charge tends to go unpaid. That is
where collection picks up.</p>
</section>

<section><h2>Handed to collection</h2>
<ul class="files">
<li><code>output/invoices/</code>: the six sample invoices as PDF.</li>
<li><code>output/invoice_lines.csv</code>: every printed line, with its section, source statement line and references.</li>
<li><code>output/invoicing_checks.csv</code>: results of the five checks per invoice.</li>
</ul></section>

<footer><p>Synthetic data (seed {seed}); statements from the billing stage. Company, customers and addresses are fictional.
Scenario design from real settlement work by Archer Opitz; built with AI assistance.</p></footer>
</main></body></html>"""
