"""The fix for the disputed invoice: the lookup includes ADJUSTED invoices, and the invoice is reissued."""
from m2c.collection import checks, inputs
from m2c.invoicing import checks as inv_checks
from m2c.invoicing import system as invoicing


def reissue(bd, statements, usage, cash):
    """The fix: the lookup includes ADJUSTED invoices. Reissue the disputed invoice with its reference."""
    h, L, reg = invoicing.build(bd, statements, usage, lookup_statuses=("ISSUED", "ADJUSTED"))
    orig = inputs.REISSUE_NO[:-3]
    hdr = h[h["invoice_no"] == orig].iloc[0].to_dict()
    lines = L[L["invoice_no"] == orig].copy()
    lines["invoice_no"] = inputs.REISSUE_NO
    paid = cash[cash["invoice_no"] == orig].iloc[0]
    hdr.update(invoice_no=inputs.REISSUE_NO, invoice_date=checks.REISSUE_DATE.isoformat(),
               due_date=checks.REISSUE_DUE.isoformat(),
               reissue={"replaces": orig, "original_date": "2027-01-01",
                        "reason": "Adjustment detail added; charges unchanged.",
                        "paid": float(paid["amount"]), "paid_on": paid["received_date"],
                        "balance": round(hdr["amount_due"] - float(paid["amount"]), 2),
                        "due": checks.REISSUE_DUE.isoformat()})
    refs = inv_checks.adjustment_references(lines, reg)
    return hdr, lines, refs
