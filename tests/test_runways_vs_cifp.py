"""runways.csv designators/orientation must agree with the FAA CIFP runway thresholds (CLT was reversed)."""
import csv
import gzip
import json
import math
from pathlib import Path

REF = Path("data/ref")


def _nm(a, b, c, d):
    return math.hypot((c - a) * 60, (d - b) * 60 * math.cos(math.radians((a + c) / 2)))


def test_clt_runway_ends_match_cifp_thresholds():
    cifp = json.load(gzip.open(REF / "procedures" / "KCLT.json.gz"))
    magvar = cifp["airport"]["magvar_deg"] or 0
    rows = [r for r in csv.DictReader(open(REF / "runways.csv", newline="")) if r["airport"] == "CLT"]
    assert {r["runway"] for r in rows} == {"01L", "01R", "18", "19L", "19R", "36"}
    for r in rows:
        p = cifp["runways"][r["runway"]]
        assert _nm(float(r["le_lat"]), float(r["le_lon"]), p["lat"], p["lon"]) < 0.5, r["runway"]
        diff = abs((float(r["heading_true"]) - (p["heading_mag"] + magvar) + 180) % 360 - 180)
        assert diff < 30, r["runway"]


def test_clt_renames_pair_old_and_new_designators_by_side():
    hist = {r["runway"]: r["old_id"] for r in csv.DictReader(open(REF / "runway_history.csv", newline=""))
            if r["airport"] == "CLT" and r["event"] == "renamed"}
    assert hist == {"19R": "18R", "01L": "36L", "19L": "18C", "01R": "36C", "18": "18L", "36": "36R"}
