"""
Invoicing checks, in the spec's order.

  1. Statement-to-invoice tie-out   every statement line carried, amounts and totals match
  2. Classification                 each line printed in the section its statement line type belongs in
  3. Required fields                header fields present; estimated-usage disclosure present and consistent
  4. Adjustment references          every printed prior-period adjustment cites its original invoice,
                                    service period, and billed vs actual quantities
  5. Customer-view recompute        using only what is printed, recompute every amount

The checks read the invoices, the statements and the invoice register; not the answer key.
"""
import pandas as pd

from m2c.billing import system as billing
from m2c.invoicing.system import EXPECTED_SECTION

REQUIRED = ["account_id", "bill_to", "invoice_no", "invoice_date", "service_period", "due_date", "remit_to"]
CENT = 0.01 + 1e-9          # a printed amount may differ from quantity x rate by rounding only


def _has(v):
    return v is not None and v == v and str(v) != ""


# ------------------------------------------------------------------ 1
def tie_out(headers, lines, statements, dec1):
    st = statements[statements["version"] == "corrected"]
    rows = []
    for h in headers.itertuples():
        L = lines[lines["invoice_no"] == h.invoice_no]
        carried = L[L["source"] != ""]
        if h.invoice_date == "2027-01-01":
            s = st[st["account_id"] == h.account_id]
            expected = s[s["line_type"] != "AMOUNT_DUE"].groupby("line_type")["amount"].sum().round(2).to_dict()
            due = s[s["line_type"] == "AMOUNT_DUE"]["amount"].iloc[0]
        else:
            due = float(dec1.set_index("account_id").loc[h.account_id, "amount_due"])
            expected = {"CURRENT": due}
        got = carried.groupby("source")["amount"].sum().round(2).to_dict()
        missing = [k for k in expected if k not in got]
        off = [k for k in expected if k in got and abs(got[k] - expected[k]) > 0.005]
        extra = [k for k in got if k not in expected]
        ok = not (missing or off or extra) and abs(h.amount_due - due) < 0.005
        rows.append({"invoice_no": h.invoice_no, "account_id": h.account_id, "statement_lines": len(expected),
                     "carried": len(got), "amount_due": h.amount_due, "statement_amount_due": due, "passes": ok,
                     "finding": "" if ok else f"missing {missing}, amount differs {off}, extra {extra}"})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ 2
def classification(lines):
    L = lines[lines["source"] != ""].copy()
    L["expected_section"] = L["source"].map(EXPECTED_SECTION)
    L["passes"] = L["section"] == L["expected_section"]
    return L[["invoice_no", "account_id", "label", "amount", "source", "section", "expected_section", "passes"]]


# ------------------------------------------------------------------ 3
def required_fields(headers, lines):
    rows = []
    for h in headers.to_dict("records"):
        missing = [f for f in REQUIRED if not _has(h.get(f))]
        L = lines[(lines["invoice_no"] == h["invoice_no"]) & (lines["block"] == "detail")]
        has_est = L["label"].str.endswith(", estimated").any()
        disclosure = ""
        if has_est:
            e = h.get("estimated")
            energy = L[L["label"].str.startswith("Energy") & L["qty"].notna()]["qty"].sum()
            if not e:
                disclosure = "missing"
            elif abs(e["actual_kwh"] + e["estimated_kwh"] - e["total_kwh"]) > 0.001 or abs(e["total_kwh"] - energy) > 0.001:
                disclosure = "inconsistent"
            else:
                disclosure = "present, ties to billed kWh"
        rows.append({"invoice_no": h["invoice_no"], "account_id": h["account_id"], "missing_fields": missing,
                     "estimated_usage": has_est, "disclosure": disclosure,
                     "passes": not missing and disclosure in ("", "present, ties to billed kWh")})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ 4
def adjustment_references(lines, register):
    known = set(register["invoice_no"])
    rows = []
    for r in lines[(lines["section"] == "ADJUSTMENT") & (lines["block"] == "detail")].itertuples():
        problems = []
        if not _has(r.ref_invoice):
            problems.append("no original invoice")
        elif r.ref_invoice not in known:
            problems.append("original invoice not found")
        if not _has(r.ref_period):
            problems.append("no service period")
        if not r.detail or not all({"billed_qty", "actual_qty"} <= set(x) for x in r.detail):
            problems.append("no billed vs actual quantities")
        rows.append({"invoice_no": r.invoice_no, "account_id": r.account_id, "label": r.label, "amount": r.amount,
                     "ref_invoice": r.ref_invoice, "ref_period": r.ref_period, "problems": problems,
                     "passes": not problems})
    return pd.DataFrame(rows, columns=["invoice_no", "account_id", "label", "amount", "ref_invoice", "ref_period",
                                       "problems", "passes"])


# ------------------------------------------------------------------ 5
def customer_recompute(headers, lines):
    out = []
    for h in headers.itertuples():
        L = lines[lines["invoice_no"] == h.invoice_no]
        det, summ = L[L["block"] == "detail"], L[L["block"] == "summary"]
        for r in det.itertuples():
            how, ok = "", True
            if _has(r.qty) and _has(r.rate):
                calc = round(r.qty * r.rate, 2)
                how, ok = f"{r.qty:,.3f} × {r.rate} = {calc:,.2f}", abs(calc - r.amount) <= CENT
            elif r.section == "PAYMENT":
                how = "stated payment"
            elif r.section == "INTEREST":
                calc = round(sum(p["balance"] * billing.RATE / billing.BASIS * p["days"] for p in (r.detail or [])), 2)
                how, ok = f"printed periods give {calc:,.2f}", abs(calc - r.amount) < 0.005
            elif r.section == "CREDIT" and r.detail:
                x = r.detail[0]
                ok = (abs(round(x["qty"] * x["rate_billed"], 2) - x["billed_amount"]) <= CENT
                      and abs(round(x["qty"] * x["rate_correct"], 2) - x["correct_amount"]) <= CENT
                      and abs((x["correct_amount"] - x["billed_amount"]) - r.amount) < 0.005)
                how = "printed billed and correct amounts"
            elif r.section == "ADJUSTMENT" and r.detail:
                ok = all(abs(round(x["billed_qty"] * x["rate"], 2) - x["billed_amount"]) <= CENT
                         and abs(round(x["actual_qty"] * x["rate"], 2) - x["actual_amount"]) <= CENT
                         and abs((x["actual_amount"] - x["billed_amount"]) - x["amount"]) < 0.005 for x in r.detail) \
                    and abs(sum(x["amount"] for x in r.detail) - r.amount) < 0.005
                how = "printed billed and actual quantities"
            else:
                how, ok = "no quantity, rate or detail printed", False
            out.append({"invoice_no": h.invoice_no, "account_id": h.account_id, "section": r.section,
                        "label": r.label, "amount": r.amount, "how": how, "passes": ok})
        for s in summ.itertuples():
            if s.section == "PRIOR":
                continue
            calc = round(det[det["section"] == s.section]["amount"].sum(), 2)
            out.append({"invoice_no": h.invoice_no, "account_id": h.account_id, "section": s.section,
                        "label": f"Subtotal: {s.label}", "amount": s.amount, "how": f"sum of lines {calc:,.2f}",
                        "passes": abs(calc - s.amount) < 0.005})
        total = round(summ["amount"].sum(), 2)
        out.append({"invoice_no": h.invoice_no, "account_id": h.account_id, "section": "TOTAL", "label": "Amount due",
                    "amount": h.amount_due, "how": f"sum of summary {total:,.2f}", "passes": abs(total - h.amount_due) < 0.005})
    return pd.DataFrame(out)


def run(headers, lines, register, statements, dec1):
    return dict(headers=headers, lines=lines, register=register,
                tie_out=tie_out(headers, lines, statements, dec1),
                classification=classification(lines),
                required=required_fields(headers, lines),
                references=adjustment_references(lines, register),
                recompute=customer_recompute(headers, lines))
