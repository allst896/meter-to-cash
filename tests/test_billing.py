"""
Billing stage: the balance ties, the interest doesn't, and the checks find out why.
Run with:  python -m pytest -q
"""
from datetime import date

import pytest

from m2c.billing import checks as C
from m2c.billing import inputs
from m2c.billing import system as S
from m2c.metering import diagnose, generate
from m2c.rating import inputs as rating_inputs


@pytest.fixture(scope="module", params=[7, 11, 2026])
def result(request, tmp_path_factory):
    d = tmp_path_factory.mktemp(f"billing{request.param}")
    generate.write(str(d), seed=request.param)
    v = diagnose.run(str(d))["versions"].copy()
    v["interval_end_utc"] = v["interval_end_utc"].dt.strftime("%Y-%m-%dT%H:%MZ")
    usage = str(d / "interval_usage.csv")
    v.to_csv(usage, index=False)
    (d / "rating").mkdir()
    rating_inputs.build(str(d / "rating"), seed=request.param)
    (d / "billing").mkdir()
    inputs.build(str(d / "billing"), str(d / "rating"), usage, seed=request.param)
    return C.run(S.load(str(d / "billing")))


def test_every_statement_rolls_forward(result):
    for rf in (result["roll_forward"], result["roll_forward_corrected"]):
        assert rf["ties"].all() and rf["prior_matches_dec1"].all() and rf["payments_match_ledger"].all()


def test_only_a1001_interest_is_wrong(result):
    rc = result["recalc"].set_index("account_id")
    assert list(rc.index[~rc["matches"]]) == ["A-1001"]
    pay = float(result["data"]["bank"].set_index("account_id").loc["A-1001", "amount"])
    assert abs(rc.loc["A-1001", "difference"] - round(pay * S.RATE / S.BASIS * 2, 2)) <= 0.01


def test_late_payment_interest_is_legitimate(result):
    rc = result["recalc"].set_index("account_id")
    assert rc.loc["A-1002", "billed_interest"] > 0 and rc.loc["A-1002", "matches"]
    assert rc.loc["A-1003", "billed_interest"] == 0


def test_balance_periods_locate_the_date(result):
    rc = result["recalc"].set_index("account_id")
    assert rc.loc["A-1001", "billed_breakpoints"] == [date(2026, 12, 28)]
    assert rc.loc["A-1001", "recalc_breakpoints"] == []     # paid before interest started


def test_daily_and_period_methods_agree(result):
    """The billing system (daily) and the recalculation (periods) agree when given the same dates."""
    d = result["data"]
    dec1 = d["dec1"].set_index("account_id")
    for a in d["accounts"]["account_id"]:
        red = C._reductions(d, a, d["ledger"], "payment_date")
        prior, due = float(dec1.loc[a, "amount_due"]), date.fromisoformat(dec1.loc[a, "due_date"])
        assert S.interest_daily(prior, due, red) == round(sum(s["interest"] for s in C.balance_periods(prior, due, red)), 2)


def test_ledger_vs_bank(result):
    lb = result["ledger_bank"]
    bad = lb[~lb["date_matches"]]
    assert len(bad) == 1 and lb["amount_matches"].all()
    x = bad.iloc[0]
    assert (x.payment_date, x.received_date) == ("2026-12-28", "2026-12-18")
    assert x.dated_after_entry and x.days_off == 10


def test_correction_removes_exactly_the_overcharge(result):
    b, c = result["billed"], result["corrected"]
    due = lambda st, a: st[(st["account_id"] == a) & (st["line_type"] == "AMOUNT_DUE")]["amount"].iloc[0]
    over = result["recalc"].set_index("account_id").loc["A-1001", "difference"]
    assert abs((due(b, "A-1001") - due(c, "A-1001")) - over) < 0.005
    for a in ["A-1002", "A-1003"]:
        assert due(b, a) == due(c, a)


def test_chain_ties_to_corrected_rating(result):
    ch = result["chain"]
    assert ch["ties"].all()
    assert (ch.set_index("account_id").loc[["A-1001", "A-1002"], "credit"] == 0).all()
    assert ch.set_index("account_id").loc["A-1003", "credit"] > 0


def test_no_interest_on_true_up(result):
    """Interest depends only on the Dec 1 balance and cash: changing the true-up changes nothing."""
    d = result["data"]
    lines = d["lines"].copy()
    d2 = dict(d, lines=lines.assign(amount=lines["amount"].where(lines["run"] != "Nov final (corrected)",
                                                                 lines["amount"] * 1.5)))
    a = S.statements(d)
    b = S.statements(d2)
    pick = lambda st: st[st["line_type"] == "INTEREST"]["amount"].tolist()
    assert pick(a) == pick(b)
