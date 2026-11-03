"""The UI's "Hard scenarios" lines (frontend/src/WaveScreen.tsx HARD_CASES) must stay true on the current engine
(final review Important 2: two of them had gone false). Each claim is re-checked with a formulas run."""
import re
from pathlib import Path

import pytest

from backend.wave import build
from backend.wave.graph import negotiate_wave

SRC = (Path(__file__).resolve().parents[1] / "frontend/src/WaveScreen.tsx").read_text()
CASES = {i: f"{t}; {w}" for i, t, w in re.findall(r'\{ id: "(F2015-\d+)", title: "([^"]+)", what: "([^"]+)" \}',
                                              SRC[SRC.index("const HARD_CASES"):])}


def test_every_case_is_checked_here():
    assert set(CASES) == {"F2015-6291", "F2015-70412", "F2015-102056", "F2015-252123", "F2015-247837"}


@pytest.mark.parametrize("wid", sorted(CASES))
def test_hard_case_text_is_true(wid):
    w = build.build_wave(wid)
    r = negotiate_wave(w, "formulas")
    rw = w.airport.runways
    n_open = sum(x.open for x in rw)
    text = CASES[wid]
    if "every runway closed" in text:
        assert n_open == 0
    if m := re.search(r"(\d+) of (\d+) runways open", text):
        assert (n_open, len(rw)) == (int(m[1]), int(m[2]))
    if m := re.search(r"(\d+) of (\d+) runways closed", text):
        assert (len(rw) - n_open, len(rw)) == (int(m[1]), int(m[2]))
    if m := re.search(r"runway (\w+) closed", text):
        assert [x.id for x in rw if not x.open] == [m[1]]
    if m := re.search(r"(\d+) queued", text):
        assert len(w.queue) == int(m[1])
    if m := re.search(r"diverts? all (\d+) to (\w+)", text):
        alt = {f.callsign: (f.fuel or {}).get("alternate") for f in w.flights}
        div = [a["callsign"] for a in r["final_plan"]["assignments"] if a["action"] == "divert"]
        assert len(div) == int(m[1]) == len(w.flights)
        if "safety net" in text:
            assert r["outcome"] == "fallback"
        assert {alt[c] for c in div} == {m[2]}
    if m := re.search(r"agreed in turn (\d+)", text):
        assert r["outcome"] == "consensus" and r["turns"] == int(m[1])
    if m := re.search(r"status quo breaks ([A-Z-]+)", text):
        assert any(c["rule"] == m[1] and not c["passed"] for c in r["baseline"]["evaluation"]["checks"])
    if m := re.search(r"(\w+) lands (\d+) min late", text):
        a = next(a for a in r["final_plan"]["assignments"] if a["callsign"] == m[1])
        assert a["action"] == "land" and round(a["metrics"]["delay_min"]) == int(m[2])
