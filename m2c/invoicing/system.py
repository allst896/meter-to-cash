"""
The invoicing system: turns statements into the invoices customers see.

Invoices are built from templates, one per customer category. Each template maps a statement line
type to a printed section. Prior-period adjustments look up the original invoice they adjust, so the
customer can see what the adjustment is for.

Planted, both from ordinary system behavior rather than bad numbers:
  - The COMMERCIAL template maps TRUE_UP into the current-charges section as "Energy" (a mapping
    mistake). Every commercial invoice prints its true-up as part of December's energy.
  - The original-invoice lookup searches only invoices with status ISSUED. Issuing credit CR-0003
    marked A-1003's December invoice ADJUSTED, so its true-up finds no original invoice and the
    template falls back to a bare "Adjustment" with no reference or detail.
"""
from datetime import date

import pandas as pd

from m2c.billing import checks as billing_checks
from m2c.billing import system as billing

UTILITY = "Example Power & Light"
REMIT_TO = "Example Power & Light, PO Box 0000, Anytown, TX 00000"
BILL_TO = {
    "A-1001": "Commercial Customer 1001\n100 Example Plaza\nAnytown, TX 00000",
    "A-1002": "Residential Customer 1002\n22 Sample Street\nAnytown, TX 00000",
    "A-1003": "Industrial Customer 1003\n9 Demo Industrial Park\nAnytown, TX 00000",
}
CHARGE_LABELS = {"CUSTOMER": "Customer charge", "ENERGY_ON": "Energy, on-peak",
                 "ENERGY_OFF": "Energy, off-peak", "DELIVERY": "Delivery"}
CHARGE_ORDER = ["CUSTOMER", "ENERGY_ON", "ENERGY_OFF", "DELIVERY"]

ADJ = ("ADJUSTMENT", "Prior-period adjustment")
TEMPLATES = {
    "RESIDENTIAL": {"TRUE_UP": ADJ},
    "INDUSTRIAL": {"TRUE_UP": ADJ},
    "COMMERCIAL": {"TRUE_UP": ("CURRENT", "Energy")},      # the mapping mistake
}
EXPECTED_SECTION = {"PRIOR": "PRIOR", "PAYMENT": "PAYMENT", "CREDIT": "CREDIT", "INTEREST": "INTEREST",
                    "CURRENT": "CURRENT", "TRUE_UP": "ADJUSTMENT"}

NOV, DEC = ("2026-11-01", "2026-11-30"), ("2026-12-01", "2026-12-31")


def period_text(p):
    a, b = date.fromisoformat(p[0]), date.fromisoformat(p[1])
    return f"{a.strftime('%b')} {a.day}–{b.day}, {b.year}"


def _charge_lines(rated, meter, run, by_quality=False):
    """Printed charge lines. With by_quality, one line per rated line (actual / estimated), so every printed
    amount is exactly quantity x rate rounded once. Without it, qualities are combined (sum of rated amounts)."""
    r = rated[(rated["run"] == run) & (rated["meter_id"] == meter)]
    out = []
    for code in CHARGE_ORDER:
        g = r[r["charge_code"] == code]
        if g.empty:
            continue
        label = CHARGE_LABELS[code] + (f" ({g['key'].iloc[0]})" if code == "DELIVERY" else "")
        parts = [g[g["quality"] == q] for q in ["actual", "estimated"]] if by_quality else [g]
        for part in parts:
            if part.empty:
                continue
            est = by_quality and part["quality"].iloc[0] == "estimated"
            out.append({"code": code, "label": label + (", estimated" if est else ""),
                        "qty": round(part["quantity"].sum(), 3), "unit": part["unit"].iloc[0],
                        "rate": float(part["rate"].iloc[0]), "amount": round(part["amount"].sum(), 2)})
    return out


def build(bdata, statements, usage):
    accts = bdata["accounts"].set_index("account_id")
    rated = bdata["lines"].copy()
    rated["quantity"] = rated["quantity"].astype(float)
    rated["amount"] = rated["amount"].astype(float)
    headers, lines = [], []

    # ---------------- invoice register
    register = []
    credited = set(bdata["credits"]["account_id"])
    for a in accts.index:
        n = a.split("-")[1]
        register.append({"invoice_no": f"INV-202612-{n}", "account_id": a, "invoice_date": "2026-12-01",
                         "service_from": NOV[0], "service_to": NOV[1],
                         "status": "ADJUSTED" if a in credited else "ISSUED"})
        register.append({"invoice_no": f"INV-202701-{n}", "account_id": a, "invoice_date": "2027-01-01",
                         "service_from": DEC[0], "service_to": DEC[1], "status": "ISSUED"})
    register = pd.DataFrame(register)

    def original_invoice(account, service_from):
        hit = register[(register["account_id"] == account) & (register["service_from"] == service_from)
                       & (register["status"] == "ISSUED")]                      # the lookup gap
        return None if hit.empty else hit.iloc[0]["invoice_no"]

    def add(inv, **kw):
        base = {"invoice_no": inv, "block": "detail", "qty": None, "unit": "", "rate": None,
                "ref_invoice": "", "ref_period": "", "detail": None, "source": ""}
        base.update(kw)
        lines.append(base)

    # ---------------- December 1 invoices (November service, initial data)
    u = usage[usage["version"] == "initial"]
    for a, acc in accts.iterrows():
        inv = f"INV-202612-{a.split('-')[1]}"
        cl = _charge_lines(rated, acc["meter_id"], "Nov initial (as billed)", by_quality=True)
        for c in cl:
            add(inv, account_id=a, section="CURRENT", label=c["label"], qty=c["qty"], unit=c["unit"],
                rate=c["rate"], amount=c["amount"], source="CURRENT")
        total = round(sum(c["amount"] for c in cl), 2)
        add(inv, account_id=a, block="summary", section="CURRENT", label="Current charges, November", amount=total)
        um = u[u["meter_id"] == acc["meter_id"]]
        est = um[um["quality"] == "estimated"].sort_values("interval_end_utc")
        disclosure = {
            "total_kwh": round(um["kwh"].sum(), 3), "actual_kwh": round(um.loc[um["quality"] == "actual", "kwh"].sum(), 3),
            "estimated_kwh": round(est["kwh"].sum(), 3), "estimated_intervals": len(est),
            "first": est["label_local"].iloc[0] if len(est) else "", "last": est["label_local"].iloc[-1] if len(est) else "",
        }
        headers.append({"invoice_no": inv, "account_id": a, "invoice_date": "2026-12-01", "bill_to": BILL_TO[a],
                        "service_period": period_text(NOV), "due_date": "2026-12-21", "remit_to": REMIT_TO,
                        "template": acc["description"].upper(), "amount_due": total,
                        "estimated": disclosure if len(est) else None})

    # ---------------- January 1 invoices (from the corrected statements)
    st = statements[statements["version"] == "corrected"]
    dec1 = bdata["dec1"].set_index("account_id")
    for a, acc in accts.iterrows():
        inv, m = f"INV-202701-{a.split('-')[1]}", acc["meter_id"]
        s = st[st["account_id"] == a].set_index("line_type")
        tmpl = TEMPLATES[acc["description"].upper()]
        prior = float(s.loc["PRIOR", "amount"])
        add(inv, account_id=a, block="summary", section="PRIOR", label="Prior balance (Dec 1 invoice)",
            amount=prior, source="PRIOR")
        for p in st[(st["account_id"] == a) & (st["line_type"] == "PAYMENT")].itertuples():
            add(inv, account_id=a, section="PAYMENT", label=p.description, amount=p.amount, source="PAYMENT")
        for c in bdata["credits"][bdata["credits"]["account_id"] == a].itertuples():
            billed = rated[(rated["run"] == "Nov initial (as billed)") & (rated["meter_id"] == m)
                           & (rated["charge_code"] == "DELIVERY")]
            fixed = rated[(rated["run"] == "Nov initial (corrected)") & (rated["meter_id"] == m)
                          & (rated["charge_code"] == "DELIVERY")]
            add(inv, account_id=a, section="CREDIT", label=f"Credit {c.credit_id}: {c.reason}",
                amount=-float(c.amount), source="CREDIT", ref_invoice=f"INV-202612-{a.split('-')[1]}",
                ref_period=period_text(NOV),
                detail=[{"what": "November delivery", "qty": round(billed["quantity"].sum(), 3),
                         "rate_billed": float(billed["rate"].iloc[0]), "billed_amount": round(billed["amount"].sum(), 2),
                         "rate_correct": float(fixed["rate"].iloc[0]), "correct_amount": round(fixed["amount"].sum(), 2)}])
        red = billing_checks._reductions(bdata, a, bdata["bank"], "received_date")
        segs = billing_checks.balance_periods(prior, date.fromisoformat(dec1.loc[a, "due_date"]), red)
        add(inv, account_id=a, section="INTEREST",
            label=f"Interest, {billing.RATE:.0%} a year after a {billing.GRACE_DAYS}-day grace period",
            amount=float(s.loc["INTEREST", "amount"]), source="INTEREST",
            detail=[{"from": x["from"].isoformat(), "to": x["to"].isoformat(), "balance": x["balance"], "days": x["days"]}
                    for x in segs if x["balance"] > 0])
        for c in _charge_lines(rated, m, "Dec initial"):
            add(inv, account_id=a, section="CURRENT", label=c["label"], qty=c["qty"], unit=c["unit"],
                rate=c["rate"], amount=c["amount"], source="CURRENT")

        # True-up, through the template
        tu = float(s.loc["TRUE_UP", "amount"])
        section, label = tmpl["TRUE_UP"]
        if section == "ADJUSTMENT":
            ref = original_invoice(a, NOV[0])
            if ref:
                ini = {c["code"]: c for c in _charge_lines(rated, m, "Nov initial (corrected)")}
                fin = {c["code"]: c for c in _charge_lines(rated, m, "Nov final (corrected)")}
                detail = [{"what": CHARGE_LABELS[k], "rate": fin[k]["rate"],
                           "billed_qty": ini[k]["qty"], "billed_amount": ini[k]["amount"],
                           "actual_qty": fin[k]["qty"], "actual_amount": fin[k]["amount"],
                           "amount": round(fin[k]["amount"] - ini[k]["amount"], 2)}
                          for k in CHARGE_ORDER if k != "CUSTOMER" and k in fin]
                add(inv, account_id=a, section="ADJUSTMENT",
                    label=f"{label}: November service, true-up of estimated usage", amount=tu, source="TRUE_UP",
                    ref_invoice=ref, ref_period=period_text(NOV), detail=detail)
            else:
                add(inv, account_id=a, section="ADJUSTMENT", label="Adjustment", amount=tu, source="TRUE_UP")
        else:
            add(inv, account_id=a, section=section, label=label, amount=tu, source="TRUE_UP")

        inv_lines = [l for l in lines if l["invoice_no"] == inv and l["block"] == "detail"]
        sec = lambda name: round(sum(l["amount"] for l in inv_lines if l["section"] == name), 2)
        for name, lab in [("PAYMENT", "Payments received"), ("CREDIT", "Credits"), ("INTEREST", "Interest"),
                          ("CURRENT", "Current charges, December"), ("ADJUSTMENT", "Prior-period adjustments")]:
            if any(l["section"] == name for l in inv_lines):
                add(inv, account_id=a, block="summary", section=name, label=lab, amount=sec(name))
        total = round(prior + sum(l["amount"] for l in inv_lines), 2)
        headers.append({"invoice_no": inv, "account_id": a, "invoice_date": "2027-01-01", "bill_to": BILL_TO[a],
                        "service_period": period_text(DEC), "due_date": billing.NEW_DUE.isoformat(),
                        "remit_to": REMIT_TO, "template": acc["description"].upper(), "amount_due": total,
                        "estimated": None})

    return pd.DataFrame(headers), pd.DataFrame(lines), register
