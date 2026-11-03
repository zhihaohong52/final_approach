"""Airline agents on the two stress waves (formulas mode, both fairness variants): every airline flight ends at its
status quo, or the airline objected in the log and the fallback reason names the flight (spec §5)."""
import pytest

from backend.wave import build
from backend.wave.fairness import VARIANTS
from backend.wave.graph import negotiate_wave

WAVES = ("CLT-2024-07-29-19", "ATL-2024-07-18-21")


@pytest.mark.parametrize("v", VARIANTS)
@pytest.mark.parametrize("wid", WAVES)
def test_stress_wave_airline_flights_not_worse(wid, v):
    r = negotiate_wave(build.build_wave(wid), "formulas", fairness=v)
    sq = {a["callsign"]: a["metrics"] for a in r["baseline"]["plan"]["assignments"]}   # metrics filled by evaluate()
    worse = [(cs, k) for a in r["final_plan"]["assignments"] for cs in [a["callsign"]]
             for k, eps in (("delay_min", .05), ("fuel_kg", .5)) if a["metrics"][k] > sq[cs][k] + eps]
    if worse:   # spec §5: else the airline objected in the log and the fallback says why
        assert any(m["type"] == "AGENT_TURN" and m["sender"].startswith("AIRLINE:") and
                   any(o["callsign"] == cs for o in m["body"].get("objections", [])) for cs, _ in worse for m in r["log"])
        if r["outcome"] == "fallback":
            assert any(cs in fb["body"]["reason"] for cs, _ in worse for fb in r["log"] if fb["type"] == "FALLBACK")
    assert r["evaluation"]["feasible"] and r["agents"] == ["ATC", f"AIRLINE:{'AA' if wid.startswith('CLT') else 'DL'}", "GROUND"]
    # firm only, the expected case (status quo legal, ATC's reservation falls to 0.92 x 0.78 ~ 0.72 < its 0.73 on it by turn 2)
    if v == "firm":
        assert not worse


# Status quo infeasible: the floor is the best feasible plan the airline has seen (preflight F8). That plan is not
# in the result, so its metrics are read from the log: every breach carries `floor`, `value`, `floor_source`.
BEST_SEEN_WAVES = ("F2015-77162", "F2015-292602")


def _objections(r):
    return [(m, o) for m in r["log"] if m["type"] == "AGENT_TURN" and m["sender"].startswith("AIRLINE:")
            for o in m["body"].get("objections", [])]


@pytest.mark.parametrize("v", VARIANTS)
@pytest.mark.parametrize("wid", BEST_SEEN_WAVES)
def test_best_seen_floor_waves(wid, v):
    r = negotiate_wave(build.build_wave(wid), "formulas", fairness=v)
    assert not r["baseline"]["evaluation"]["feasible"]           # the status quo is not a floor here
    assert r["evaluation"]["feasible"]
    objs = _objections(r)
    assert all(o["floor_source"] == "best_seen" for _, o in objs)
    if v == "firm":
        assert objs                                                # the floor does bite under firm
    final = {a["callsign"]: a["metrics"] for a in r["final_plan"]["assignments"]}
    for _, o in objs:
        assert o["value"] > o["allowed"] >= o["floor"]
    # spec §5 against the floor the engine used: an airline flight finishing above the floor it objected to is
    # only legitimate with that objection logged, and a fallback then names the flight in its reason
    on_final = [o for _, o in objs if o["plan_id"] == r["final_plan"]["id"]]
    for o in on_final:
        assert final[o["callsign"]][o["metric"]] == pytest.approx(o["value"], abs=0.1)
        if r["outcome"] == "fallback":
            assert any(o["callsign"] in fb["body"]["reason"] for fb in r["log"] if fb["type"] == "FALLBACK")
