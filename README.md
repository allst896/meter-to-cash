# Meter-to-Cash Demo

A walkthrough of how billing numbers go wrong between the meter and the payment, and how to prove where.
Each stage plants realistic problems in synthetic data, then finds them with checks a reviewer can follow.

| Stage | Question it answers | Status |
| --- | --- | --- |
| 1. Metering | Which usage feed is wrong, and why? | Built |
| 2. Rating | Is every unit priced, in the right category, at the right rate? | Built |
| 3. Billing | Does the statement roll forward, and is the interest right? | Built |
| 4. Invoicing | Can the customer check the invoice themselves? | Built |
| 5. Collection | Does each payment equal its invoice, and if not, why? | Built |

## Stage 1: Metering

Three meters, November 2026, 15-minute interval-ending data in America/Chicago time.

- **Source A**, the monthly meter read: complete, UTC timestamps.
- **Source B**, the streamed feed: local timestamps with no offset, and three planted problems:
  - the repeated 01:00 hour when DST ends on Nov 1 (real intervals, different values)
  - 20 re-sent records per meter (identical values)
  - a 6-hour collector outage on Nov 18 (24 missing intervals)
- **Register reads** are the source of record.

Source B has exactly 2,880 rows per meter, which is 30 × 96, so a month-level count passes.
The checks run in this order, counting before any cleaning:

1. Build the expected-interval calendar from time-zone rules (2,884 intervals; 100 on Nov 1)
2. Count per day, raw rows and unique labels, before deduplication
3. Compare values on repeated timestamps: DST hour (keep) vs re-sends (drop)
4. Compare totals to register reads: which source is off
5. Read the gap pattern: why it is off

Then the initial version (Source B, outage interpolated and flagged) and the final version (Source A)
are kept side by side, and the difference is the true-up.

## Stage 2: Rating

November's usage is rated by the production engine and, in parallel, by an upgraded engine that will
replace it. Three charges: a customer charge keyed on category, time-of-use energy, and delivery keyed
on load zone. Rates are effective-dated; on-peak energy rises from $0.12 to $0.13/kWh on Dec 1.

Planted issues, each from real settlement work:

- **Wrong zone:** an account update on Nov 1 put M3 in LZ_WEST instead of LZ_HOUSTON, in both engines
- **Unpriced usage:** the migration loaded M2's zone as `NORTH`, which the rate table doesn't know
- **Effective dating:** the new engine looks up rates by run date, not service period

The four checks, in order: volume conservation, delta from normal (each meter against its own prior
three months), system comparison (old vs new, line by line), and the effective-date check. No single
check catches all three issues; the report shows which catches what.

## Stage 3: Billing

The January 1 statements for three accounts: the December 1 balance, payments, the rating stage's zone
correction as a credit, interest, December charges, and the November true-up from metering. Terms are
18% a year after a 5-day grace period; credits apply before interest; no interest on the true-up.

The true-up only runs for meters whose December 1 register read was submitted by the December 28 cutoff.
M2's read needed a field verification and was keyed December 29, so A-1002's true-up waits for February.

Planted issue: a $1,300 partial payment received Dec 18 was keyed into the ledger as Dec 28. The amount is
right, so the statement rolls forward and nothing looks wrong, but the interest is $1.28 too high.

The four checks: balance roll-forward, an independent interest recalculation from the bank remittance,
interest by balance period (which puts the difference on Dec 28), and ledger against bank remittance.

## Stage 4: Invoicing

Six sample invoices as PDF from a fictional utility: the December 1 invoices (billed on estimated usage,
with a disclosure of actual vs estimated kWh) and the January 1 invoices built from the corrected statements.
Every invoice prints enough for the customer to recompute it: quantities, rates, interest periods, and billed
vs actual amounts behind each credit and true-up.

Planted issues, both from how the invoicing system builds the page, not from wrong numbers:

- **Mislabeled line:** the commercial template maps the November true-up into current charges as "Energy"
- **Missing reference:** the true-up's original-invoice lookup only searches ISSUED invoices; the zone credit
  marked A-1003's December invoice ADJUSTED, so its true-up prints as a bare "Adjustment"

The five checks: statement-to-invoice tie-out (passes on every invoice), classification, required fields
and disclosures, adjustment references, and a customer-view recompute using only what is printed.

## Stage 5: Collection

January cash against the January 1 invoices, checked as of January 22, the day after they fell due.

Planted issues:

- **Short-pay:** A-1003 pays everything except the $12.97 bare "Adjustment" from invoicing
- **Misapplied payment:** A-1002's check is keyed to A-1020 (transposed digits), so one account shows
  unpaid and another an unexplained credit of the same amount
- **Submission cutoff:** M2's read arrived after the true-up cutoff, so A-1002 is still billed on estimates

The six checks: match payments to invoices, classify, compare short-pays to invoice lines, pair unexpected
balances, compare invoiced kWh to submitted reads and the cutoff, and trace the disputed line upstream to
metering. The dispute log carries the evidence package; the invoice is reissued with its reference
(no interest while disputed), the customer pays, and aging clears.

## Run it

```
pip install -r requirements.txt
python run_metering.py          # writes data/ and output/
python run_rating.py            # reads output/interval_usage.csv
python run_billing.py           # reads metering and rating outputs
python run_invoicing.py         # reads billing outputs; writes PDFs to output/invoices/
python run_collection.py        # reads invoicing; writes the reissued invoice too
python -m pytest -q             # the spec's numbers as tests, across three random months
```

Outputs:

- `output/metering_report.html`, `output/rating_report.html`, `output/billing_report.html`, `output/invoicing_report.html`, `output/collection_report.html` — the walkthroughs
- `output/interval_usage.csv` — every interval per meter and version, with UTC end time, local label, offset and quality flag
- `output/validation_summary.csv` — metering counts and variances per meter
- `output/rated_charges.csv` — every rated line from both engines and all runs
- `output/rating_exceptions.csv` — the rating findings
- `output/statements.csv` — January 1 statement lines per account, as billed and corrected
- `output/billing_checks.csv` — billing check results per account
- `output/invoices/` — the six sample invoices (PDF, plus PNG previews when `pdftoppm` is installed)
- `output/invoice_lines.csv` — every printed invoice line, with section, source statement line and references
- `output/invoicing_checks.csv` — invoicing check results per invoice
- `output/cash_application.csv` — each receipt, how it matched, and its classification
- `output/dispute_log.csv` — disputes with their evidence packages
- `output/read_check.csv` — invoiced kWh against submitted reads and the cutoff
- `output/ar_corrections.csv`, `output/aging.csv` — the reposting, and aging before and after

## Layout

```
m2c/calendar.py              expected-interval calendar
m2c/metering/generate.py     synthetic Source A, Source B, register reads
m2c/metering/diagnose.py     the five checks, cleaning, estimation, versions
m2c/metering/report.py       HTML report
m2c/rating/inputs.py         rate table, meter masters, billing history
m2c/rating/engine.py         old and new rating engines
m2c/rating/checks.py         the four rating checks
m2c/rating/report.py         HTML report
m2c/billing/inputs.py        Dec 1 bills, credit, December charges, ledger, bank remittance
m2c/billing/system.py        the billing system: statements and daily interest
m2c/billing/checks.py        the four billing checks and the tie-out to rating
m2c/billing/report.py        HTML report
m2c/invoicing/system.py      the invoicing system: templates, lines, invoice register
m2c/invoicing/pdf.py         invoice PDFs
m2c/invoicing/checks.py      the five invoicing checks
m2c/invoicing/report.py      HTML report
m2c/collection/inputs.py     open items, bank remittance, AR postings
m2c/collection/checks.py     the six collection checks, dispute log, aging
m2c/collection/reissue.py    the fixed lookup and the reissued invoice
m2c/collection/report.py     HTML report
tests/                       spec numbers as tests
```

## About

Scenarios and diagnostic methods come from real settlement work by Archer Opitz (Opitz Consulting LLC):
the DST fall-back case was found in production by counting intervals per day against the calendar,
grouping mistakes by delta-from-normal checks, effective-dating errors by comparing systems during upgrades,
and fat-fingered payment dates by recalculating interest independently.
Unexplained invoice adjustments, and the disputes they cause, are drawn from the same work, as is checking
invoices against submitted data, where mismatches traced either to what was submitted or to the counterparty's
pickup timing.
The code was built with AI assistance.
