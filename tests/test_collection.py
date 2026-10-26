"""
Collection stage: one short-pay that is really a question, one payment on the wrong account,
one read that arrived after the cutoff, and a dispute settled on the evidence.
Run with:  python -m pytest -q
"""
import pandas as pd
import pytest

from m2c.billing import checks as billing_checks
from m2c.billing import inputs as billing_inputs
from m2c.billing import system as billing
from m2c.collection import checks as C
from m2c.collection import inputs
from m2c.collection.reissue import reissue
from m2c.invoicing import pdf
from m2c.invoicing import system as invoicing
from m2c.metering import diagnose, generate
from m2c.rating import inputs as rating_inputs


@pytest.fixture(scope="module", params=[7, 11, 2026])
def result(request, tmp_path_factory):
    d = tmp_path_factory.mktemp(f"collection{request.param}")
    generate.write(str(d), seed=request.param)
    v = diagnose.run(str(d))["versions"].copy()
    v["interval_end_utc"] = v["interval_end_utc"].dt.strftime("%Y-%m-%dT%H:%MZ")
    usage_path = str(d / "interval_usage.csv")
    v.to_csv(usage_path, index=False)
    (d / "rating").mkdir()
    rating_inputs.build(str(d / "rating"), seed=request.param)
    (d / "billing").mkdir()
    billing_inputs.build(str(d / "billing"), str(d / "rating"), usage_path, seed=request.param)
    bd = billing.load(str(d / "billing"))
    rb = billing_checks.run(bd)
    pd.concat([rb["billed"], rb["corrected"]]).to_csv(d / "statements.csv", index=False)
    statements = pd.read_csv(d / "statements.csv", keep_default_na=False)
    usage = pd.read_csv(usage_path)
    headers, lines, _ = invoicing.build(bd, statements, usage)
    (d / "collection").mkdir()
    inputs.build(str(d / "collection"), headers, lines)
    cd = inputs.load(str(d / "collection"))
    r = C.run(cd, headers, lines, bd, statements, usage, pd.read_csv(d / "register_reads.csv"))
    r.update(dir=d, statements=statements, lines=lines, headers=headers, bd=bd, usage=usage)
    return r


def test_only_a1003_short_pays_by_the_bare_adjustment(result):
    cash = result["cash"]
    assert len(cash) == 4 and cash["invoice_no"].ne("").all()
    sp = cash[cash["result"] != "Exact match"]
    assert list(sp["account_id"]) == ["A-1003"] and list(sp["result"]) == ["Short-pay"]
    s = result["short_pays"].iloc[0]
    assert s["points_to_dispute"] and s["matching_line"] == "Adjustment" and not s["line_has_reference"]
    st = result["statements"]
    tu = st[(st["version"] == "corrected") & (st["account_id"] == "A-1003") & (st["line_type"] == "TRUE_UP")]["amount"]
    assert abs(s["shortfall"] - float(tu.iloc[0])) < 0.005 and s["shortfall"] > 0


def test_misapplied_payment_is_paired_to_its_owner(result):
    p = result["pairs"]
    assert len(p) == 1
    x = p.iloc[0]
    assert (x["posted_to"], x["belongs_to"], x["cause"]) == ("A-1020", "A-1002", "transposed digits")
    inv = result["headers"].set_index("account_id").loc["A-1002"]
    due = inv[inv["invoice_date"] == "2027-01-01"]["amount_due"].iloc[0]
    assert abs(x["amount"] - due) < 0.005
    bal = result["balances"].set_index("account_id")
    assert abs(bal["unexpected"].sum()) < 0.005                   # the pair nets to zero


def test_late_read_is_a_timing_gap_not_a_bad_number(result):
    rd = result["reads"].set_index("account_id")
    assert list(rd.index[rd["side"] != "Neither"]) == ["A-1002"]
    a2 = rd.loc["A-1002"]
    assert not a2["on_time"] and a2["basis"].startswith("Estimated") and a2["side"] == "Neither (timing)"
    for a in ["A-1001", "A-1003"]:
        assert abs(rd.loc[a, "difference_kwh"]) <= C.REGISTER_RESOLUTION
    held = result["bd"]["lines"]
    held = held[held["meter_id"] == "M2"]
    amt = lambda run: held[held["run"] == run]["amount"].astype(float).sum()
    assert abs(a2["pending_true_up"] - round(amt("Nov final (corrected)") - amt("Nov initial (corrected)"), 2)) < 0.005


def test_trace_ties_every_stage(result):
    t = result["traces"][0]
    assert all(t["ties"].values())
    assert t["metering"]["intervals"] == 24 and t["metering"]["difference_kwh"] > 0
    assert t["submission"]["on_time"]


def test_interest_only_where_ar_is_wrong(result):
    i = result["interest"].set_index("account_id")
    assert (i["correct"] == 0).all()
    assert set(i.index[i["as_posted"] > 0]) <= {"A-1002", "A-1003"}
    assert i.loc["A-1002", "as_posted"] > 0


def test_aging_clears(result):
    ag = result["aging"]
    rep = ag[ag["view"] == "After reposting"].set_index("account_id")
    assert list(rep.index[rep["balance"].abs() > 0.004]) == ["A-1003"]
    assert rep.loc["A-1003", "in_dispute"] == rep.loc["A-1003", "balance"]
    assert (ag[ag["view"] == "After reissue and payment"]["balance"].abs() < 0.005).all()
    assert (result["resolved"]["balance_posted"].abs() < 0.005).all()


def test_reissued_invoice_has_its_reference_and_fits_one_page(result):
    from pypdf import PdfReader
    hdr, lines, refs = reissue(result["bd"], result["statements"], result["usage"], result["cash"])
    assert len(refs) == 1 and refs["passes"].all()
    assert abs(hdr["reissue"]["balance"] - result["short_pays"].iloc[0]["shortfall"]) < 0.005
    path = result["dir"] / f"{hdr['invoice_no']}.pdf"
    pdf.render(hdr, lines, str(path))
    assert len(PdfReader(str(path)).pages) == 1
