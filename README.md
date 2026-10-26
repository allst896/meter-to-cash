# Meter-to-Cash Demo

A walkthrough of how billing numbers go wrong between the meter and the payment, and how to prove where.
Each stage plants realistic problems in synthetic data, then finds them with checks a reviewer can follow.

| Stage | Question it answers | Status |
| --- | --- | --- |
| 1. Metering | Which usage feed is wrong, and why? | Built |
| 2. Rating | Is every unit priced, in the right category, at the right rate? | Built |
| 3. Billing | Does the statement roll forward, and is the interest right? | Spec |
| 4. Invoicing | Can the customer check the invoice themselves? | Spec |
| 5. Collection | Does each payment equal its invoice, and if not, why? | Spec |

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

## Run it

```
pip install -r requirements.txt
python run_metering.py          # writes data/ and output/
python run_rating.py            # reads output/interval_usage.csv
python -m pytest -q             # the spec's numbers as tests, across three random months
```

Outputs:

- `output/metering_report.html` and `output/rating_report.html` — the walkthroughs
- `output/interval_usage.csv` — every interval per meter and version, with UTC end time, local label, offset and quality flag
- `output/validation_summary.csv` — metering counts and variances per meter
- `output/rated_charges.csv` — every rated line from both engines and all runs
- `output/rating_exceptions.csv` — the rating findings

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
tests/                       spec numbers as tests
```

## About

Scenarios and diagnostic methods come from real settlement work by Archer Opitz (Opitz Consulting LLC):
the DST fall-back case was found in production by counting intervals per day against the calendar,
grouping mistakes by delta-from-normal checks, and effective-dating errors by comparing systems during upgrades.
The code was built with AI assistance.
