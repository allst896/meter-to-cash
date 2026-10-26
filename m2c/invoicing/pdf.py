"""Render invoices to PDF (ReportLab). Every page is marked as a synthetic sample."""
from datetime import date

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, KeepTogether

from m2c.invoicing.system import UTILITY

_FONT_PAIRS = [   # first pair found wins: Linux, Windows, macOS
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
]
F, FB = "Helvetica", "Helvetica-Bold"   # built-in fallback
for _reg, _bold in _FONT_PAIRS:
    try:
        pdfmetrics.registerFont(TTFont("Inv", _reg))
        pdfmetrics.registerFont(TTFont("Inv-Bold", _bold))
        F, FB = "Inv", "Inv-Bold"
        break
    except Exception:
        continue

INK, MUTED, RULE = colors.HexColor("#1b1b19"), colors.HexColor("#66655f"), colors.HexColor("#d9d6cf")
ACCENT, BAND = colors.HexColor("#1f4e79"), colors.HexColor("#f3efe4")
st = {
    "body": ParagraphStyle("b", fontName=F, fontSize=8.8, leading=12, textColor=INK),
    "small": ParagraphStyle("s", fontName=F, fontSize=7.6, leading=10, textColor=MUTED),
    "h": ParagraphStyle("h", fontName=FB, fontSize=10, leading=13, textColor=ACCENT, spaceBefore=7, spaceAfter=3),
    "title": ParagraphStyle("t", fontName=FB, fontSize=17, leading=21, textColor=ACCENT),
    "right": ParagraphStyle("r", fontName=F, fontSize=8.8, leading=12, textColor=INK, alignment=2),
}
W = letter[0] - 1.3 * inch


def money(x, signed=False):
    if abs(x) < 0.005:
        x = 0.0
    s = f"${abs(x):,.2f}"
    return ("−" + s) if x < 0 else (("+" + s) if signed and x > 0 else s)


def d(s):
    x = date.fromisoformat(s)
    return f"{x.strftime('%B')} {x.day}, {x.year}"


def _table(rows, widths, header=True, bold_last=False, num_from=1):
    t = Table(rows, colWidths=widths, hAlign="LEFT")
    style = [("FONT", (0, 0), (-1, -1), F, 8.6), ("TEXTCOLOR", (0, 0), (-1, -1), INK),
             ("ALIGN", (num_from, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
             ("TOPPADDING", (0, 0), (-1, -1), 2.4), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.4),
             ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]
    if header:
        style += [("FONT", (0, 0), (-1, 0), FB, 8.2), ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
                  ("LINEBELOW", (0, 0), (-1, 0), 0.9, INK)]
    if bold_last:
        style += [("FONT", (0, -1), (-1, -1), FB, 9.5), ("LINEABOVE", (0, -1), (-1, -1), 1.1, INK)]
    t.setStyle(TableStyle(style))
    return t


def render(header, lines, path):
    h, L = header, lines[lines["invoice_no"] == header["invoice_no"]]
    det, summ = L[L["block"] == "detail"], L[L["block"] == "summary"]
    story = []

    top = Table([[Paragraph(f"<b>{UTILITY}</b>", st["title"]),
                  Paragraph(f"<b>INVOICE</b><br/>{h['invoice_no']}<br/>Invoice date: {d(h['invoice_date'])}", st["right"])]],
                colWidths=[W * 0.6, W * 0.4])
    top.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [top, Spacer(1, 6)]
    banner = Table([[Paragraph("<b>SAMPLE INVOICE</b>: synthetic data for a portfolio demo (meter-to-cash). "
                               "Not a real bill. Company, customer and addresses are fictional.", st["small"])]], colWidths=[W])
    banner.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), BAND), ("TOPPADDING", (0, 0), (-1, -1), 5),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    story += [banner, Spacer(1, 10)]

    meta = [["Account", h["account_id"]], ["Service period", h["service_period"]],
            ["Invoice date", d(h["invoice_date"])], ["Due date", d(h["due_date"])]]
    mt = Table(meta, colWidths=[1.1 * inch, 1.9 * inch])
    mt.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), F, 8.8), ("TEXTCOLOR", (0, 0), (0, -1), MUTED),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 2), ("TOPPADDING", (0, 0), (-1, -1), 2)]))
    bill = Paragraph("<font color='#66655f'>Bill to</font><br/>" + h["bill_to"].replace("\n", "<br/>"), st["body"])
    two = Table([[bill, mt]], colWidths=[W * 0.52, W * 0.48])
    two.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [two, Spacer(1, 4)]

    # Account summary
    rows = [["Account summary", ""]]
    rows += [[r.label, money(r.amount, r.section not in ("PRIOR",))] for r in summ.itertuples()]
    rows.append(["Amount due", money(h["amount_due"])])
    story += [Paragraph("Account summary", st["h"]), _table(rows[1:], [W * 0.75, W * 0.25], header=False, bold_last=True)]

    def section(name, title):
        return det[det["section"] == name], title

    # Payments, credits, interest
    pay, _ = section("PAYMENT", "")
    if len(pay):
        story += [Paragraph("Payments received", st["h"]),
                  _table([[r.label, money(r.amount)] for r in pay.itertuples()], [W * 0.75, W * 0.25], header=False)]
    cred, _ = section("CREDIT", "")
    for r in cred.itertuples():
        x = r.detail[0]
        calc = (f"{x['what']}, {x['qty']:,.3f} kWh: billed at ${x['rate_billed']:.3f}/kWh = {money(x['billed_amount'])}; "
                f"correct rate ${x['rate_correct']:.3f}/kWh = {money(x['correct_amount'])}; "
                f"difference {money(x['billed_amount'] - x['correct_amount'])}. "
                f"Adjusts invoice {r.ref_invoice}, service period {r.ref_period}.")
        story += [Paragraph("Credits", st["h"]), _table([[r.label, money(r.amount)]], [W * 0.75, W * 0.25], header=False),
                  Paragraph(calc, st["small"])]
    intr, _ = section("INTEREST", "")
    for r in intr.itertuples():
        parts = [f"{money(p['balance'])} × 18% ÷ 365 × {p['days']} days ({d(p['from'])} to {d(p['to'])})" for p in (r.detail or [])]
        story += [Paragraph("Interest", st["h"]), _table([[r.label, money(r.amount)]], [W * 0.75, W * 0.25], header=False),
                  Paragraph(("Unpaid balance after the grace period: " + "; ".join(parts) + ".") if parts
                            else "No unpaid balance after the grace period.", st["small"])]

    # Current charges
    cur, _ = section("CURRENT", "")
    rows = [["Charge", "Quantity", "Rate", "Amount"]]
    for r in cur.itertuples():
        if r.qty is not None and r.qty == r.qty:
            q = "1 month" if r.unit == "month" else f"{r.qty:,.3f} kWh"
            rate = f"${r.rate:,.2f}" if r.unit == "month" else f"${r.rate:.3f}/kWh"
            rows.append([r.label, q, rate, money(r.amount)])
        else:
            rows.append([r.label, "", "", money(r.amount)])
    rows.append(["Current charges", "", "", money(round(cur["amount"].sum(), 2))])
    story += [Paragraph(f"Current charges, {h['service_period']}", st["h"]),
              _table(rows, [W * 0.4, W * 0.22, W * 0.16, W * 0.22], bold_last=True)]

    # Prior-period adjustments
    adj, _ = section("ADJUSTMENT", "")
    for r in adj.itertuples():
        block = [Paragraph("Prior-period adjustments", st["h"]),
                 _table([[r.label, money(r.amount, True)]], [W * 0.75, W * 0.25], header=False)]
        if r.ref_invoice:
            block.append(Paragraph(f"Adjusts invoice {r.ref_invoice}, service period {r.ref_period}. November was billed on "
                                   f"partly estimated usage; the meter read replaced the estimates with actuals.", st["small"]))
        if r.detail:
            rows = [["Charge (Nov rate)", "Billed kWh", "Billed", "Actual kWh", "Actual", "Difference"]]
            rows += [[f"{x['what']} (${x['rate']:.3f})", f"{x['billed_qty']:,.3f}", money(x["billed_amount"]),
                      f"{x['actual_qty']:,.3f}", money(x["actual_amount"]), money(x["amount"], True)] for x in r.detail]
            block += [Spacer(1, 3), _table(rows, [W * 0.25, W * 0.15, W * 0.14, W * 0.15, W * 0.14, W * 0.17])]
        story.append(KeepTogether(block))

    # Estimated-data disclosure
    if h.get("estimated"):
        e = h["estimated"]
        txt = (f"<b>This invoice includes estimated usage.</b> Of {e['total_kwh']:,.3f} kWh billed, {e['actual_kwh']:,.3f} kWh "
               f"are actual and {e['estimated_kwh']:,.3f} kWh are estimated: {e['estimated_intervals']} fifteen-minute "
               f"intervals labeled {e['first'][5:]} to {e['last'][5:]} (Central time, interval-ending), estimated by "
               f"interpolation between the readings before and after. Estimated usage will be trued up on a later invoice "
               f"after the meter is read. Detailed interval data is available in the customer portal.")
        box = Table([[Paragraph(txt, st["body"])]], colWidths=[W])
        box.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, ACCENT), ("TOPPADDING", (0, 0), (-1, -1), 6),
                                 ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LEFTPADDING", (0, 0), (-1, -1), 7)]))
        story += [Spacer(1, 10), box]

    story += [Spacer(1, 8), Paragraph("Amounts are quantity × rate, rounded to the cent on each line.", st["small"])]
    stub = Table([[Paragraph(f"<b>Remit to</b><br/>{h['remit_to']}", st["body"]),
                   Paragraph(f"Account {h['account_id']}<br/>Invoice {h['invoice_no']}<br/>"
                             f"<b>Amount due {money(h['amount_due'])}</b> by {d(h['due_date'])}", st["right"])]],
                 colWidths=[W * 0.55, W * 0.45])
    stub.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 0.8, MUTED, None, (3, 3)), ("TOPPADDING", (0, 0), (-1, -1), 8),
                              ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [Spacer(1, 6), KeepTogether([stub])]

    doc = SimpleDocTemplate(path, pagesize=letter, leftMargin=0.65 * inch, rightMargin=0.65 * inch,
                            topMargin=0.45 * inch, bottomMargin=0.45 * inch,
                            title=f"{h['invoice_no']} (sample)", author=f"{UTILITY} (fictional)")
    doc.build(story)
