"""
The spec's numbers, as tests. Run with:  python -m pytest -q
"""
import datetime as dt

import pytest

from m2c.calendar import daily_counts, expected_intervals
from m2c.metering import diagnose, generate


@pytest.fixture(scope="module", params=[7, 11, 2026])
def result(request, tmp_path_factory):
    d = tmp_path_factory.mktemp(f"data{request.param}")
    generate.write(str(d), seed=request.param)
    return diagnose.run(str(d))


# ---- calendar ------------------------------------------------------------
def test_calendar_november():
    cal = expected_intervals(2026, 11)
    days = daily_counts(cal).set_index("operating_date")["expected"]
    assert len(cal) == 2_884
    assert days[dt.date(2026, 11, 1)] == 100
    assert (days.drop(dt.date(2026, 11, 1)) == 96).all()


def test_calendar_spring_forward():
    cal = expected_intervals(2026, 3)
    days = daily_counts(cal).set_index("operating_date")["expected"]
    assert days[dt.date(2026, 3, 8)] == 92
    assert len(cal) == 31 * 96 - 4


# ---- counts (spec: Expected interval counts) -------------------------------
def test_counts_per_meter(result):
    s = result["summary"]
    assert (s["expected"] == 2_884).all()
    assert (s["source_a_received"] == 2_884).all()
    assert (s["source_b_raw"] == 2_880).all()
    assert (s["source_b_clean"] == 2_860).all()
    assert (s["source_b_naive"] == 2_856).all()
    assert (s["true_duplicates_removed"] == 20).all()
    assert (s["dst_repeated_labels_kept"] == 4).all()
    assert (s["conflicts"] == 0).all()
    assert (s["estimated_intervals"] == 24).all()


def test_month_level_check_is_fooled(result):
    mn = result["month_naive"]
    assert (mn["raw_rows"] == mn["assumed"]).all()


def test_per_day_table(result):
    d = result["days"].set_index(["meter_id", "operating_date"])
    for m in result["meters"]:
        nov1 = d.loc[(m, dt.date(2026, 11, 1))]
        assert (nov1.expected, nov1.raw_rows, nov1.unique_labels) == (100, 100, 96)
        nov18 = d.loc[(m, dt.date(2026, 11, 18))]
        assert (nov18.expected, nov18.raw_rows, nov18.unique_labels) == (96, 72, 72)
        others = d.loc[m].drop([dt.date(2026, 11, 1), dt.date(2026, 11, 18)])
        assert (others["unique_labels"] == 96).all()
        assert (others["raw_rows"] - 96).sum() == 20


def test_dst_repeats_are_the_0100_hour(result):
    rep = result["repeats"]
    dst = rep[rep["classification"] == diagnose.DST_REPEAT]
    assert sorted(set(x[-5:] for x in dst["timestamp_local"])) == ["01:00", "01:15", "01:30", "01:45"]


def test_order_comparison(result):
    o = result["orders"]
    assert (o["dedup_first_count"] == 96).all()
    assert (o["count_first_raw"] == 100).all()
    assert (o["count_first_repeats_diff_values"] == 4).all()


# ---- register reads ---------------------------------------------------------
def test_source_a_reconciles_source_b_does_not(result):
    reg = result["register"]
    assert reg["source_a_ok"].all()
    assert not reg["source_b_clean_ok"].any()
    assert not reg["source_b_naive_ok"].any()


def test_shortfall_is_fully_explained(result):
    """Cleaned B is short by exactly the outage intervals; naive B also loses the second DST hour."""
    reg = result["register"].set_index("meter_id")
    acc = result["accuracy"].groupby("meter_id")["kwh_actual"].sum()
    b, bn = result["b"], result["b_naive"]
    dropped = b.loc[~b.index.isin(bn.index)]
    dst_lost = (dropped[dropped["timestamp_local"].isin(result["repeated_labels"])]
                .groupby("meter_id")["kwh"].sum())
    for m in result["meters"]:
        x = reg.loc[m]
        assert abs((x.source_a - acc[m]) - x.source_b_clean) < 1e-6
        assert abs((x.source_b_clean - dst_lost[m]) - x.source_b_naive) < 1e-6


# ---- gap pattern ------------------------------------------------------------
def test_gap_is_one_contiguous_run(result):
    g = result["gaps"]
    assert len(g) == len(result["meters"])
    assert (g["first_label"] == "2026-11-18 02:15").all()
    assert (g["last_label"] == "2026-11-18 08:00").all()
    assert (g["intervals"] == 24).all() and (g["hours"] == 6).all()
    assert g["pattern"].str.startswith("Contiguous").all()


# ---- versions and true-up ---------------------------------------------------
def test_versions_and_true_up(result):
    v, s, reg = result["versions"], result["summary"].set_index("meter_id"), result["register"].set_index("meter_id")
    for m in result["meters"]:
        vm = v[v["meter_id"] == m]
        assert (vm["version"] == "initial").sum() == 2_884
        assert (vm["version"] == "final").sum() == 2_884
        assert (vm.loc[vm["version"] == "final", "quality"] == "actual").all()
        # final reconciles to register reads; true-up is final minus initial
        assert abs(s.loc[m, "final_total"] - reg.loc[m, "register_usage"]) <= reg.loc[m, "tolerance"]
        assert abs(s.loc[m, "true_up_kwh"] - (s.loc[m, "final_total"] - s.loc[m, "initial_total"])) < 1e-6
    # DST hour stays distinct: 8 intervals carry the four repeated labels
    fin = v[(v["version"] == "final") & (v["meter_id"] == result["meters"][0])]
    assert fin["label_local"].isin(result["repeated_labels"]).sum() == 8
