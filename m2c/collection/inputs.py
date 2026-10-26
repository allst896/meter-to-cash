"""
Synthetic inputs for the collection stage: January 2027 cash against the January 1 invoices.

  Open items        the three January 1 invoices, plus A-1020's from the rest of the book
  Bank remittance   what actually arrived, with whatever the payer wrote on it (source of record)
  AR postings       what the cash clerks keyed into accounts receivable

Planted
  Short-pay         A-1003 pays everything except the bare $"Adjustment" line it can't explain
  Misapplied cash   A-1002 mails a check with "A-1002" on the memo and no invoice number; the clerk keys
                    it to A-1020 (transposed digits). A-1020 had already paid its own invoice by autopay.
  (The late field read was planted upstream, in the billing inputs.)

After the January 22 run (used by the resolution, not the checks)
  A-1003 pays the balance after the invoice is reissued with its reference.
"""
import pandas as pd

OTHER_ACCOUNT = ("A-1020", "Residential Customer 1020", 87.12)
REISSUE_NO = "INV-202701-1003-R1"


def build(data_dir, headers, lines):
    jan = headers[headers["invoice_date"] == "2027-01-01"].set_index("account_id")
    due = lambda a: round(float(jan.loc[a, "amount_due"]), 2)
    inv = lambda a: jan.loc[a, "invoice_no"]

    # The line the customer can't explain: a printed adjustment with no reference
    adj = lines[(lines["section"] == "ADJUSTMENT") & (lines["block"] == "detail") & (lines["ref_invoice"] == "")]
    disputed_acct, disputed_amt = adj["account_id"].iloc[0], round(float(adj["amount"].iloc[0]), 2)

    other_id, other_name, other_amt = OTHER_ACCOUNT
    open_items = pd.DataFrame(
        [(inv(a), a, "2027-01-01", jan.loc[a, "due_date"], due(a)) for a in jan.index]
        + [(f"INV-202701-{other_id.split('-')[1]}", other_id, "2027-01-01", "2027-01-21", other_amt)],
        columns=["invoice_no", "account_id", "invoice_date", "due_date", "amount"])

    bank = pd.DataFrame([
        ("2027-01-12", "ACH-20270112-0091", other_name, other_amt, f"INV-202701-{other_id.split('-')[1]}", other_id),
        ("2027-01-15", "CHK-20270115-2204", "Residential Customer 1002", due("A-1002"), "", "A-1002"),
        ("2027-01-19", "ACH-20270119-0318", "Commercial Customer 1001", due("A-1001"), inv("A-1001"), "A-1001"),
        ("2027-01-20", "ACH-20270120-0577", "Industrial Customer 1003",
         round(due(disputed_acct) - disputed_amt, 2), inv(disputed_acct), disputed_acct),
        ("2027-02-03", "ACH-20270203-0129", "Industrial Customer 1003", disputed_amt, REISSUE_NO, disputed_acct),
    ], columns=["received_date", "reference", "payer", "amount", "invoice_ref", "account_ref"])

    postings = pd.DataFrame([
        ("2027-01-12", other_id, f"INV-202701-{other_id.split('-')[1]}", other_amt, "ACH-20270112-0091", "autopay"),
        ("2027-01-15", other_id, "", due("A-1002"), "CHK-20270115-2204", "mchen"),        # the transposition
        ("2027-01-19", "A-1001", inv("A-1001"), due("A-1001"), "ACH-20270119-0318", "jlopez"),
        ("2027-01-20", disputed_acct, inv(disputed_acct), round(due(disputed_acct) - disputed_amt, 2),
         "ACH-20270120-0577", "jlopez"),
    ], columns=["posted_date", "account_id", "invoice_no", "amount", "reference", "entered_by"])

    accounts = pd.DataFrame([("A-1001", "Commercial Customer 1001"), ("A-1002", "Residential Customer 1002"),
                             ("A-1003", "Industrial Customer 1003"), (other_id, other_name)],
                            columns=["account_id", "name"])

    accounts.to_csv(f"{data_dir}/accounts.csv", index=False)
    open_items.to_csv(f"{data_dir}/open_items.csv", index=False)
    bank.to_csv(f"{data_dir}/bank_remittance.csv", index=False)
    postings.to_csv(f"{data_dir}/ar_postings.csv", index=False)


def load(data_dir):
    rd = lambda f: pd.read_csv(f"{data_dir}/{f}", keep_default_na=False)
    d = dict(accounts=rd("accounts.csv"), open_items=rd("open_items.csv"), bank=rd("bank_remittance.csv"),
             postings=rd("ar_postings.csv"))
    for k in ("open_items", "bank", "postings"):
        d[k]["amount"] = d[k]["amount"].astype(float)
    return d
