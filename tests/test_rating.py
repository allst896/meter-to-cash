"""
Rating stage: each planted issue is caught by the checks the spec says catch it, and missed by the
ones it says miss it. Run with:  python -m pytest -q
"""
import pytest

from m2c.metering import diagnose, generate
from m2c.rating import checks as C
from m2c.rating import engine, inputs


@pytest.fixture(scope="module", params=[7, 11, 2026])
def result(request, tmp_path_factory):
    d = tmp_path_factory.mktemp(f"rating{request.param}")
    generate.write(str(d), seed=request.param)
    m = diagnose.run(str(d))
    v = m["versions"].copy()
    v["interval_end_utc"] = v["interval_end_utc"].dt.strftime("%Y-%m-%dT%H:%MZ")
    usage_path = str(d / "interval_usage.csv")
    v.to_csv(usage_path, index=False)
    rdir = d / "rating"
    rdir.mkdir()
    inputs.build(str(rdir), seed=request.param)
    lines, rates, usage = engine.run_all(str(rdir), usage_path)
    r = C.run(lines, rates, usage, str(rdir))
    r["usage"] = usage
    return r


# ---- volume conservation ----------------------------------------------------
def test_volume_conservation_finds_only_the_key_mismatch(result):
    vc = result["volume"]
    bad = vc[~vc["passes"]]
    assert set(bad["engine"]) == {"new"}
    assert set(bad["meter_id"]) == {"M2"}
    assert (bad["unrated_energy_kwh"].abs() < 1e-6).all()
    assert (bad["unrated_delivery_kwh"] == bad["metered_kwh"]).all()
    assert set(bad["zone_key"]) == {"NORTH"} and set(bad["closest_key"]) == {"LZ_NORTH"}
    assert len(bad) == len(engine.RUNS)          # every run


def test_wrong_zone_and_effective_dating_pass_volume_check(result):
    vc = result["volume"]
    assert vc[(vc["meter_id"] == "M3")]["passes"].all()
    assert vc[(vc["engine"] == "old")]["passes"].all()


# ---- delta from normal ------------------------------------------------------
def test_delta_flags_only_m3_delivery(result):
    d = result["delta"]
    flagged = d[d["flagged"]]
    assert list(zip(flagged["meter_id"], flagged["charge"])) == [("M3", "DELIVERY")]
    x = flagged.iloc[0]
    assert abs(x["rate_change"] - (0.044 / 0.036 - 1)) < 1e-3
    assert x["usage_normal"] and x["key_changed"]
    assert x["nov_key"] == "LZ_WEST" and x[f"key_{result['months'][-1]}"] == "LZ_HOUSTON"


# ---- system comparison ------------------------------------------------------
def test_comparison_by_run(result):
    br = result["by_run"].set_index("run")
    assert br.loc["Oct final", "rate_diffs"] == 0
    assert br.loc["Oct final", "missing_new"] == 1
    for run in ["Nov initial", "Nov final"]:
        assert br.loc[run, "rate_diff_charges"] == "ENERGY_ON"
        assert br.loc[run, "rate_diff_per_unit"] == [0.01]
        assert br.loc[run, "missing_new"] >= 1


def test_wrong_zone_invisible_to_comparison(result):
    c = result["comparison"]
    m3_del = c[(c["meter_id"] == "M3") & (c["charge_code"] == "DELIVERY")]
    assert (m3_del["classification"] == C.MATCH).all()


def test_off_peak_and_customer_lines_match(result):
    c = result["comparison"]
    assert (c[c["charge_code"].isin(["ENERGY_OFF", "CUSTOMER"])]["classification"] == C.MATCH).all()


def test_differences_tie_out(result):
    lines, c = result["lines"], result["comparison"]
    for run, *_ in engine.RUNS:
        new = lines[(lines["engine"] == "new") & (lines["run"] == run)]["amount"].sum()
        old = lines[(lines["engine"] == "old") & (lines["run"] == run)]["amount"].sum()
        assert abs((new - old) - c[c["run"] == run]["difference"].sum()) < 0.005


# ---- effective-date check ---------------------------------------------------
def test_effective_date_check_sides_with_old_engine(result):
    e = result["effective"]
    assert len(e) > 0
    assert (e["correct_engine"] == "old").all()
    assert (e["rate_for_service_period"] == "EON-1").all()
    assert (e["new_rate_id"] == "EON-2").all()
    assert e["new_rate_starts_after_service"].all()


def test_rated_kwh_matches_metering_output(result):
    """Old engine's November final energy kWh equals metering's final total for each meter."""
    lines, usage = result["lines"], result["usage"]["november"]
    fin = usage[usage["version"] == "final"].groupby("meter_id")["kwh"].sum()
    ln = lines[(lines["engine"] == "old") & (lines["run"] == "Nov final")
               & lines["charge_code"].isin(C.ENERGY)].groupby("meter_id")["quantity"].sum()
    assert ((fin - ln).abs() < 1e-6).all()
