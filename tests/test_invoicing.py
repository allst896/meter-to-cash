"""
Invoicing stage: every invoice ties out, and two still fail the customer.
Run with:  python -m pytest -q
"""
import pandas as pd
import pytest

from m2c.billing import checks as billing_checks
from m2c.billing import inputs
from m2c.billing import system as billing
from m2c.invoicing import checks as C
from m2c.invoicing import pdf, system
from m2c.metering import diagnose, generate
from m2c.rating import inputs as rating_inputs


@pytest.fixture(scope="module", params=[7, 11, 2026])
def result(request, tmp_path_factory):
    d = tmp_path_factory.mktemp(f"invoicing{request.param}")
    generate.write(str(d), seed=request.param)
    v = diagnose.run(str(d))["versions"].copy()
    v["interval_end_utc"] = v["interval_end_utc"].dt.strftime("%Y-%m-%dT%H:%MZ")
    usage = str(d / "interval_usage.csv")
    v.to_csv(usage, index=False)
    (d / "rating").mkdir()
    rating_inputs.build(str(d / "rating"), seed=request.param)
    (d / "billing").mkdir()
    inputs.build(str(d / "billing"), str(d / "rating"), usage, seed=request.param)
    bd = billing.load(str(d / "billing"))
    rb = billing_checks.run(bd)
    st_path = d / "statements.csv"
    pd.concat([rb["billed"], rb["corrected"]]).to_csv(st_path, index=False)
    statements = pd.read_csv(st_path, keep_default_na=False)
    headers, lines, register = system.build(bd, statements, pd.read_csv(usage))
    r = C.run(headers, lines, register, statements, bd["dec1"])
    r["dir"] = d
    return r


def _failing(df):
    return sorted(set(df.loc[~df["passes"], "invoice_no"]))


def test_six_invoices(result):
    assert len(result["headers"]) == 6
    assert set(result["headers"]["invoice_date"]) == {"2026-12-01", "2027-01-01"}


def test_every_invoice_ties_out(result):
    assert result["tie_out"]["passes"].all()


def test_only_the_commercial_true_up_is_misclassified(result):
    bad = result["classification"][~result["classification"]["passes"]]
    assert list(bad["invoice_no"]) == ["INV-202701-1001"]
    assert list(bad["source"]) == ["TRUE_UP"] and list(bad["section"]) == ["CURRENT"]


def test_required_fields_and_disclosures(result):
    req = result["required"]
    assert req["passes"].all()
    dec = req[req["invoice_no"].str.startswith("INV-202612")]
    assert dec["estimated_usage"].all()
    assert (dec["disclosure"] == "present, ties to billed kWh").all()


def test_only_the_adjusted_invoice_loses_its_reference(result):
    ref = result["references"]
    assert _failing(ref) == ["INV-202701-1003"]
    bad = ref[~ref["passes"]].iloc[0]
    assert "no original invoice" in bad["problems"] and bad["amount"] > 0     # a charge the customer can't explain
    reg = result["register"].set_index("invoice_no")
    assert reg.loc["INV-202612-1003", "status"] == "ADJUSTED"


def test_recompute_fails_exactly_the_two_lines(result):
    rc = result["recompute"]
    bad = rc[~rc["passes"]]
    assert sorted(bad["invoice_no"]) == ["INV-202701-1001", "INV-202701-1003"]
    assert len(bad) == 2


def test_true_up_amount_matches_statement(result):
    st = pd.read_csv(result["dir"] / "statements.csv", keep_default_na=False)
    st = st[(st["version"] == "corrected") & (st["line_type"] == "TRUE_UP")].set_index("account_id")["amount"]
    ref = result["references"].set_index("account_id")["amount"]
    assert abs(ref["A-1003"] - st["A-1003"]) < 0.005


def test_pdfs_render_one_page(result):
    from pypdf import PdfReader
    for h in result["headers"].to_dict("records"):
        path = result["dir"] / f"{h['invoice_no']}.pdf"
        pdf.render(h, result["lines"], str(path))
        assert len(PdfReader(str(path)).pages) == 1
