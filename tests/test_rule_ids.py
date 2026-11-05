"""The engine's rule list is one constant (rules.RULE_IDS), served in /api/config so the UI never hard-codes a count."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from backend import server
from backend.wave import build, moves, rules

ROOT = Path(__file__).resolve().parents[1]


def test_rule_ids_are_what_the_engine_checks():
    w = build.build_wave("F2015-270287")
    emitted = {c.rule for c in rules.check(w, moves.fcfs_plan(w))}
    assert emitted <= set(rules.RULE_IDS) and len(rules.RULE_IDS) == len(set(rules.RULE_IDS)) == 14
    src = (ROOT / "backend/wave/rules.py").read_text()
    in_check = set(re.findall(r'"((?:ATC|PLT|GND)-[A-Z-]+)",\s*"(?:ATC|PILOT|GROUND)"', src))
    assert in_check == set(rules.RULE_IDS)             # every rule the engine can emit is listed, nothing more


def test_rule_ids_match_the_contract_union():
    ts = (ROOT / "frontend/src/api/types.ts").read_text()
    block = ts[ts.index("export type RuleId ="):]
    block = block[:block.index(";")]
    assert set(re.findall(r'"([A-Z]+-[A-Z-]+)"', block)) == set(rules.RULE_IDS)


def test_config_serves_the_rule_ids():
    assert TestClient(server.app).get("/api/config").json()["rule_ids"] == list(rules.RULE_IDS)


def test_f2015_summary_window_end_is_one_hour_after_start():
    """Final review Minor 1: WaveSummary.window_end was the window start for flights.csv waves. F2015-7053 opens at
    19:00, the hour MQ3269 (12:35 sched, 419 min late) really lands."""
    w = build.build_wave("F2015-7053").to_dict()
    s = server._f2015_summary(w)
    assert s["window_start"] == "2015-01-04T19:00:00" and s["window_end"] == "2015-01-04T20:00:00"


def test_frontend_other_flight_keys_match_the_engine():
    from backend.wave import edit
    src = (ROOT / "frontend/src/rules.ts").read_text()
    line = next(x for x in src.splitlines() if x.startswith("export const OTHER_FLIGHT_KEYS"))
    assert tuple(re.findall(r'"([a-z_]+)"', line)) == edit.OTHER_FLIGHT_KEYS
