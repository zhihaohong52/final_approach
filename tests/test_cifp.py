import json
from pathlib import Path
import pytest
from atm.geo.cifp import parse_lat, parse_lon, parse_leg, extract_airport

LINES = Path("tests/data/cifp/kord_2611.txt").read_text(encoding="latin-1").splitlines()


def _field(dec, name):
    return str(dec[name])   # arinc424's Record.json() is a flat {field name: raw text} object


def test_coords():
    assert parse_lat("N41583699") == pytest.approx(41.976942, abs=1e-6)
    assert parse_lon("W087542934") == pytest.approx(-87.908150, abs=1e-6)

def test_faf_and_vpa():
    legs = {l.fix: l for l in map(parse_leg, (x for x in LINES if x[12] == "F" and "I10C" in x[13:19]))}
    assert legs["RAYYY"].desc[3] == "F" and legs["RAYYY"].pt == "CF" and legs["RAYYY"].course_mag == 93.0
    assert legs["RW10C"].vpa_deg == pytest.approx(3.0)

def test_missed_hold_at_cgt():
    hm = next(parse_leg(x) for x in LINES if x[47:49] == "HM")
    assert (hm.fix, hm.turn, hm.course_mag, hm.time_min, hm.alt1_ft) == ("CGT", "R", 336.0, 1.0, 4000)

def test_extract_kord():
    d = extract_airport(LINES, "KORD", "2611")
    assert d["airport"]["magvar_deg"] == pytest.approx(-3.0) and d["airport"]["elev_ft"] == 680
    assert d["fixes"]["BENKY"] == pytest.approx([41.511831, -88.709017], abs=1e-6)
    star = next(s for s in d["stars"] if s["id"] == "BENKY6")
    assert "BFORD" in star["enroute"] and star["common"][0]["fix"] == "BENKY"   # common route BENKY-NEWRK-AHSTN-PETAH
    assert star["common"][0]["alt1_ft"] == 12000 and star["common"][-1]["fix"] == "PETAH"
    assert any(l["pt"] in ("FM", "VM") for l in star["runway"]["04B"])
    ap = d["approaches"]["10C"]
    assert ap["kind"] == "ILS" and ap["final"][-1]["fix"] == "RW10C"
    assert {"fix": "CGT", "turn": "R", "inbound_mag": 336.0, "leg_min": 1.0, "alt_ft": 4000, "kind": "HM"}.items() \
        <= next(h for h in d["holds"] if h["fix"] == "CGT").items()

def test_extract_absent_airport_is_none():
    assert extract_airport(LINES, "KPBI", "2611") is None

def test_every_referenced_fix_has_coordinates():
    d = extract_airport(LINES, "KORD", "2611")
    assert all(v is not None for v in d["fixes"].values())

def test_rf_leg_has_center():
    # First RF line in FAACIFP18 (cycle 2611): PADL (Dillingham, AK), RNAV (GPS) approach H01-Y,
    # transition BROUS, seq 030 to fix FISAX, centre fix CFNPX.
    rf = next(parse_leg(x) for x in LINES if x[47:49] == "RF")
    assert rf.pt == "RF" and rf.fix and rf.center and rf.turn in ("L", "R")

def test_header_has_fetch_time_and_tier():
    h = extract_airport(LINES, "KORD", "2611")["header"]
    assert h["tier"] == "star+iap" and h["fetched_at"].endswith("Z")

def test_matches_arinc424_decode():
    # dev-only cross-check (spec §12); arinc424 (MIT) is never a runtime dependency
    arinc424 = pytest.importorskip("arinc424")
    for x in (x for x in LINES if x[12] in "EF"):
        rec = arinc424.Record(); rec.read(x)
        dec = json.loads(rec.json())
        assert parse_leg(x).pt == _field(dec, "Path and Termination").strip()
        assert (parse_leg(x).fix or "") == _field(dec, "Fix Identifier").strip()
