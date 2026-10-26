"""
Collection checks, in the spec's order. Run as of January 22, 2027, the day after the invoices fell due.

  1. Match payments to invoices      invoice number on the remittance, then account and amount, then amount and date
  2. Classify each result            exact, short-pay, overpayment, unapplied
  3. Short-pays against invoice lines   a shortfall equal to one printed line points to a dispute of that line
  4. Pair unexpected balances        an unexpected unpaid balance and an unexpected credit of the same amount;
                                     the bank remittance says whose payment it was
  5. Invoice against submitted reads   invoiced November kWh against the register reads as submitted, and when
  6. Trace back                      the disputed line upstream: invoice, statement, rating, metering

Then the resolution the spec settles on: repost the misapplied payment, reissue the disputed invoice with
its reference, and the customer pays. No interest on an amount in good-faith dispute while it is open.
"""
from datetime import date, datetime, timedelta

import pandas as pd

from m2c.billing import system as billing
from m2c.collection.inputs import REISSUE_NO

RUN_DATE = date(2027, 1, 22)
FEB1 = date(2027, 2, 1)
REISSUE_DATE, REISSUE_DUE = date(2027, 1, 27), date(2027, 2, 16)
REGISTER_RESOLUTION = 1.0        # kWh; register reads are whole kWh at both ends
NOV = ("2026-11-01 00:00", "2026-12-01 00:00")
D = date.fromisoformat


def _md(x):
    return f"{x.strftime('%b')} {x.day}, {x.year}"


# ------------------------------------------------------------------ 1
def match(d, as_of=RUN_DATE):
    oi = d["open_items"]
    rows = []
    for r in d["bank"][d["bank"]["received_date"].map(D) <= as_of].itertuples():
        inv, how = None, "Unmatched"
        ref = r.invoice_ref[:-3] if r.invoice_ref.endswith("-R1") else r.invoice_ref     # a reissue pays its original
        if ref and ref in set(oi["invoice_no"]):
            inv, how = ref, "Invoice number on remittance"
        else:
            by_acct = oi[(oi["account_id"] == r.account_ref) & ((oi["amount"] - r.amount).abs() < 0.005)]
            if len(by_acct) == 1:
                inv, how = by_acct["invoice_no"].iloc[0], "Account and amount"
            else:
                rec = D(r.received_date)
                by_amt = oi[((oi["amount"] - r.amount).abs() < 0.005)
                            & oi["invoice_date"].map(D).le(rec) & oi["due_date"].map(lambda x: D(x) + timedelta(30)).ge(rec)]
                if len(by_amt) == 1:
                    inv, how = by_amt["invoice_no"].iloc[0], "Amount and date"
        acct = oi.set_index("invoice_no").loc[inv, "account_id"] if inv else ""
        rows.append({"reference": r.reference, "received_date": r.received_date, "payer": r.payer, "amount": r.amount,
                     "invoice_ref": r.invoice_ref, "account_ref": r.account_ref, "invoice_no": inv or "",
                     "account_id": acct, "matched_by": how})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ 2
def classify(d, cash):
    amt = d["open_items"].set_index("invoice_no")["amount"]
    out = cash.copy()
    out["invoice_amount"] = out["invoice_no"].map(lambda i: float(amt[i]) if i else float("nan"))
    out["difference"] = (out["amount"] - out["invoice_amount"]).round(2)

    def kind(r):
        if not r.invoice_no:
            return "Unapplied cash"
        if abs(r.difference) < 0.005:
            return "Exact match"
        return "Short-pay" if r.difference < 0 else "Overpayment"
    out["result"] = [kind(r) for r in out.itertuples()]
    return out


# ------------------------------------------------------------------ 3
def short_pays(classified, lines):
    rows = []
    for r in classified[classified["result"] == "Short-pay"].itertuples():
        short = round(-r.difference, 2)
        L = lines[(lines["invoice_no"] == r.invoice_no) & (lines["block"] == "detail")]
        hit = L[(L["amount"] - short).abs() < 0.005]
        x = hit.iloc[0] if len(hit) == 1 else None
        rows.append({"invoice_no": r.invoice_no, "account_id": r.account_id, "reference": r.reference,
                     "shortfall": short, "lines_checked": len(L), "matching_line": x["label"] if x is not None else "",
                     "section": x["section"] if x is not None else "",
                     "line_has_reference": bool(x is not None and x["ref_invoice"]),
                     "points_to_dispute": x is not None})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ 4
def _transposed(a, b):
    return a != b and sorted(a) == sorted(b)


def balances(d, cash, as_of=RUN_DATE, postings=None):
    """Per account: balance as posted in AR, and balance as the bank says it should be."""
    p = d["postings"] if postings is None else postings
    p = p[p["posted_date"].map(D) <= as_of]
    oi = d["open_items"]
    rows = []
    for a in d["accounts"]["account_id"]:
        billed = oi[oi["account_id"] == a]["amount"].sum()
        posted = p[p["account_id"] == a]["amount"].sum()
        received = cash[cash["account_id"] == a]["amount"].sum()
        rows.append({"account_id": a, "invoiced": round(billed, 2), "posted": round(posted, 2),
                     "received": round(received, 2), "balance_posted": round(billed - posted, 2) + 0.0,
                     "balance_bank": round(billed - received, 2) + 0.0,
                     "unexpected": round(received - posted, 2) + 0.0})       # + unpaid that shouldn't be, - credit that shouldn't be
    return pd.DataFrame(rows)


def pair(d, cash, bal):
    rows = []
    owe = bal[bal["unexpected"] > 0.004]
    cred = bal[bal["unexpected"] < -0.004]
    for o in owe.itertuples():
        for c in cred.itertuples():
            if abs(o.unexpected + c.unexpected) > 0.005:
                continue
            p = d["postings"][(d["postings"]["account_id"] == c.account_id)
                              & ((d["postings"]["amount"] - o.unexpected).abs() < 0.005)]
            for x in p.itertuples():
                b = cash[cash["reference"] == x.reference]
                if b.empty or b["account_id"].iloc[0] != o.account_id:
                    continue
                b = b.iloc[0]
                cause = "transposed digits" if _transposed(o.account_id.split("-")[1], c.account_id.split("-")[1]) \
                    else "keyed to another account"
                rows.append({"amount": o.unexpected, "reference": x.reference, "received_date": b["received_date"],
                             "posted_date": x.posted_date, "posted_to": c.account_id, "belongs_to": o.account_id,
                             "remittance_says": b["account_ref"], "matched_by": b["matched_by"],
                             "entered_by": x.entered_by, "cause": cause})
    return pd.DataFrame(rows, columns=["amount", "reference", "received_date", "posted_date", "posted_to", "belongs_to",
                                       "remittance_says", "matched_by", "entered_by", "cause"])


# ------------------------------------------------------------------ 5
def submitted_reads(bdata, statements, register):
    rated = bdata["lines"]
    reads = bdata["reads"].set_index("meter_id")
    reg = register.set_index(["meter_id", "read_local"])["register_kwh"]
    st = statements[statements["version"] == "corrected"]
    rows = []
    for a, m in bdata["accounts"][["account_id", "meter_id"]].itertuples(index=False):
        q = lambda run: float(rated[(rated["run"] == run) & (rated["meter_id"] == m)
                                    & (rated["charge_code"] == "DELIVERY")]["quantity"].astype(float).sum())
        amt = lambda run: float(rated[(rated["run"] == run) & (rated["meter_id"] == m)]["amount"].astype(float).sum())
        applied = ((st["account_id"] == a) & (st["line_type"] == "TRUE_UP")).any()
        invoiced = q("Nov final (corrected)") if applied else q("Nov initial (as billed)")
        read_kwh = float(reg[(m, NOV[1])] - reg[(m, NOV[0])])
        r = reads.loc[m]
        submitted = datetime.fromisoformat(r["submitted_at"])
        on_time = submitted <= billing.TRUEUP_CUTOFF
        agrees = abs(invoiced - read_kwh) <= REGISTER_RESOLUTION and applied
        if agrees:
            verdict, side = "Invoice matches the submitted read", "Neither"
        elif not applied and not on_time:
            verdict, side = "Timing gap: read submitted after the cutoff; picked up on the February 1 invoice", "Neither (timing)"
        elif not applied and on_time:
            verdict, side = "Read was in by the cutoff but not used", "Ours (pickup)"
        else:
            verdict, side = "Invoiced quantity differs from the submitted read", "Investigate"
        rows.append({"account_id": a, "meter_id": m, "basis": "Final (true-up applied)" if applied else "Estimated (no true-up yet)",
                     "invoiced_kwh": round(invoiced, 3), "read_kwh": read_kwh, "difference_kwh": round(invoiced - read_kwh, 3),
                     "read_type": r["read_type"], "submitted_at": r["submitted_at"],
                     "cutoff": billing.TRUEUP_CUTOFF.strftime("%Y-%m-%d %H:%M"), "on_time": on_time,
                     "pending_true_up": 0.0 if applied else round(amt("Nov final (corrected)") - amt("Nov initial (corrected)"), 2),
                     "verdict": verdict, "side": side})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ 6
def trace(dispute, lines, statements, bdata, usage, reads_check):
    a, inv = dispute["account_id"], dispute["invoice_no"]
    m = bdata["accounts"].set_index("account_id").loc[a, "meter_id"]
    line = lines[(lines["invoice_no"] == inv) & (lines["block"] == "detail") & (lines["label"] == dispute["matching_line"])].iloc[0]
    st = statements[(statements["version"] == "corrected") & (statements["account_id"] == a)].set_index("line_type")
    rated = bdata["lines"].copy()
    rated[["quantity", "amount"]] = rated[["quantity", "amount"]].astype(float)
    r = rated[rated["meter_id"] == m]
    ini = r[r["run"] == "Nov initial (corrected)"].groupby("charge_code")[["quantity", "amount"]].sum()
    fin = r[r["run"] == "Nov final (corrected)"].groupby("charge_code")[["quantity", "amount"]].sum()
    charges = []
    for code in ["ENERGY_ON", "ENERGY_OFF", "DELIVERY"]:
        charges.append({"charge": code, "initial_kwh": round(ini.loc[code, "quantity"], 3),
                        "final_kwh": round(fin.loc[code, "quantity"], 3),
                        "initial": round(ini.loc[code, "amount"], 2), "final": round(fin.loc[code, "amount"], 2),
                        "difference": round(fin.loc[code, "amount"] - ini.loc[code, "amount"], 2)})
    u = usage[usage["meter_id"] == m]
    est = u[(u["version"] == "initial") & (u["quality"] == "estimated")].sort_values("interval_end_utc")
    act = u[(u["version"] == "final") & u["interval_end_utc"].isin(est["interval_end_utc"])]
    rc = reads_check.set_index("account_id").loc[a]
    rating_total = round(sum(c["difference"] for c in charges), 2)
    kwh_delta = round(act["kwh"].sum() - est["kwh"].sum(), 3)
    return {
        "invoice": {"invoice_no": inv, "label": line["label"], "amount": float(line["amount"]),
                    "ref_invoice": line["ref_invoice"], "ref_period": line["ref_period"]},
        "statement": {"line_type": "TRUE_UP", "description": st.loc["TRUE_UP", "description"],
                      "amount": float(st.loc["TRUE_UP", "amount"])},
        "rating": {"charges": charges, "total": rating_total},
        "metering": {"meter_id": m, "intervals": len(est), "first": est["label_local"].iloc[0],
                     "last": est["label_local"].iloc[-1], "estimated_kwh": round(est["kwh"].sum(), 3),
                     "actual_kwh": round(act["kwh"].sum(), 3), "difference_kwh": kwh_delta},
        "submission": {"read_type": rc["read_type"], "submitted_at": rc["submitted_at"], "cutoff": rc["cutoff"],
                       "on_time": bool(rc["on_time"]), "invoiced_kwh": rc["invoiced_kwh"], "read_kwh": rc["read_kwh"]},
        "ties": {
            "invoice = statement": bool(abs(float(line["amount"]) - float(st.loc["TRUE_UP", "amount"])) < 0.005),
            "statement = rating": bool(abs(float(st.loc["TRUE_UP", "amount"]) - rating_total) < 0.005),
            "rating kWh = metering kWh": bool(abs(charges[2]["final_kwh"] - charges[2]["initial_kwh"] - kwh_delta) < 0.002),
        },
        "began_at": "Invoicing: the original-invoice lookup searches ISSUED invoices only; credit CR-0003 had marked "
                    "the December invoice ADJUSTED",
    }


# ------------------------------------------------------------------ interest and aging
def feb1_interest(d, cash, disputed):
    """February 1 interest under billing's terms, as AR is posted vs as it should be."""
    oi = d["open_items"]
    rows = []
    for a in d["accounts"]["account_id"]:
        o = oi[oi["account_id"] == a]
        prior, due = float(o["amount"].sum()), D(o["due_date"].iloc[0])
        p = d["postings"][(d["postings"]["account_id"] == a) & (d["postings"]["posted_date"].map(D) <= RUN_DATE)]
        as_posted = billing.interest_daily(prior, due, [(D(x.posted_date), x.amount) for x in p.itertuples()], FEB1)
        c = cash[cash["account_id"] == a]
        hold = disputed.get(a, 0.0)
        correct = billing.interest_daily(prior - hold, due, [(D(x.received_date), x.amount) for x in c.itertuples()], FEB1)
        rows.append({"account_id": a, "as_posted": as_posted, "correct": correct,
                     "difference": round(as_posted - correct, 2),
                     "why": ("cash posted to the wrong account" if as_posted > correct and not hold else
                             "amount in dispute; no interest while open" if as_posted > correct else "")})
    return pd.DataFrame(rows)


BUCKETS = ["Current", "1–30", "31–60", "61–90", "90+"]


def aging(d, as_of, postings, disputed_open):
    oi = d["open_items"]
    p = postings[postings["posted_date"].map(D) <= as_of]
    rows = []
    for a in d["accounts"]["account_id"]:
        o = oi[oi["account_id"] == a]
        bal = round(o["amount"].sum() - p[p["account_id"] == a]["amount"].sum(), 2) + 0.0
        row = {"as_of": as_of.isoformat(), "account_id": a, "balance": bal, **{b: 0.0 for b in BUCKETS},
               "credit": 0.0, "in_dispute": 0.0}
        if bal < -0.004:
            row["credit"] = bal
        elif bal > 0.004:
            days = (as_of - D(o["due_date"].iloc[0])).days
            b = BUCKETS[0] if days <= 0 else BUCKETS[min(4, 1 + (days - 1) // 30)]
            row[b] = bal
            row["in_dispute"] = min(bal, disputed_open.get(a, 0.0))
        rows.append(row)
    return pd.DataFrame(rows)


# ------------------------------------------------------------------
def run(d, headers, lines, bdata, statements, usage, register):
    cash = match(d)
    cls = classify(d, cash)
    sp = short_pays(cls, lines)
    bal = balances(d, cash)
    pairs = pair(d, cash, bal)
    reads = submitted_reads(bdata, statements, register)
    disputes = sp[sp["points_to_dispute"]]
    traces = [trace(x, lines, statements, bdata, usage, reads) for x in disputes.to_dict("records")]
    disputed = {x["account_id"]: x["shortfall"] for x in disputes.to_dict("records")}
    interest = feb1_interest(d, cash, disputed)

    # ---- resolution
    fixes = []
    for x in pairs.itertuples():
        fixes += [{"posted_date": RUN_DATE.isoformat(), "account_id": x.posted_to, "invoice_no": "",
                   "amount": -x.amount, "reference": x.reference, "entered_by": "correction",
                   "note": f"Reverse: belongs to {x.belongs_to} ({x.cause})"},
                  {"posted_date": RUN_DATE.isoformat(), "account_id": x.belongs_to,
                   "invoice_no": cash.set_index("reference").loc[x.reference, "invoice_no"], "amount": x.amount,
                   "reference": x.reference, "entered_by": "correction",
                   "note": f"Repost, effective {x.received_date} (date received)"}]
    later = d["bank"][d["bank"]["received_date"].map(D) > RUN_DATE]
    pay_after = [{"posted_date": r.received_date, "account_id": r.account_ref, "invoice_no": r.invoice_ref,
                  "amount": r.amount, "reference": r.reference, "entered_by": "jlopez", "note": "Paid after reissue"}
                 for r in later.itertuples()]
    base = d["postings"].assign(note="")
    corrected = pd.concat([base, pd.DataFrame(fixes)], ignore_index=True)
    resolved = pd.concat([corrected, pd.DataFrame(pay_after)], ignore_index=True)

    log = []
    for i, (x, t) in enumerate(zip(disputes.to_dict("records"), traces)):
        paid = [p for p in pay_after if p["account_id"] == x["account_id"]]
        log.append({
            "dispute_id": f"DSP-{i + 1:04d}", "account_id": x["account_id"], "invoice_no": x["invoice_no"],
            "line": x["matching_line"], "amount": x["shortfall"], "opened": RUN_DATE.isoformat(),
            "raised_by": "Collection (short-pay equal to one line); customer confirmed by phone",
            "customer_said": f"Paid everything except the ${x['shortfall']:,.2f} “Adjustment”; "
                             "the invoice doesn't say what it is for.",
            "interest": "Suspended while open",
            "finding": "Charge is correct; the invoice left off its reference",
            "resolution": f"Reissued as {REISSUE_NO} on {_md(REISSUE_DATE)} with original invoice, "
                          f"service period, and billed vs actual quantities; balance due {_md(REISSUE_DUE)}",
            "closed": paid[0]["posted_date"] if paid else "",
            "closed_by": f"Payment {paid[0]['reference']} for ${paid[0]['amount']:,.2f}" if paid else "",
            "trace": t,
        })
    open_disputes = {x["account_id"]: x["amount"] for x in log}
    aging_tbl = pd.concat([
        aging(d, RUN_DATE, d["postings"], open_disputes).assign(view="As posted"),
        aging(d, date(2027, 1, 31), corrected, open_disputes).assign(view="After reposting"),
        aging(d, date(2027, 2, 28), resolved, {}).assign(view="After reissue and payment"),
    ], ignore_index=True)

    sp_lines = lines[lines["invoice_no"].isin(sp["invoice_no"]) & (lines["block"] == "detail")]
    return dict(cash=cls, short_pays=sp, inv_lines=sp_lines[["label", "section", "amount"]].to_dict("records"), balances=bal, pairs=pairs, reads=reads, traces=traces, interest=interest,
                corrections=pd.DataFrame(fixes), later=pd.DataFrame(pay_after), log=log, aging=aging_tbl,
                resolved=balances(d, match(d, date(2027, 2, 28)), date(2027, 2, 28), resolved))
