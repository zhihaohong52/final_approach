import glob, json
import pytest
from fastapi.testclient import TestClient
from backend import server
from backend.wave import build, trajectory
from backend.wave.version import ENGINE_VERSION
from scripts.make_samples import WAVE_ID as ORD_REPLAY   # the ORD replay id; T18 may change it

try:   # T13 defines the shared helper in tests/test_trajectory.py
    from tests.test_trajectory import _far_runway
except ImportError:   # until T13 lands: the same definition (T13 brief)
    def _far_runway(w, cs) -> str:
        """Open runway end with the largest t_route_min, provided it exceeds the nominal runway's by > 1.0 min."""
        from backend.wave import route
        from backend.wave.moves import fcfs_runways
        rs = route.routes_for_wave(w)
        nominal = rs[(cs, fcfs_runways(w)[cs])].t_route_min
        best = max((r for (c, _), r in rs.items() if c == cs), key=lambda r: r.t_route_min)
        if best.t_route_min <= nominal + 1.0:
            pytest.skip("no runway end more than 1 min farther than nominal")
        return best.runway

c = TestClient(server.app)
ORD = "F2015-270287"   # a flights2015 row; builds regardless of which replay files exist

def test_geo_procedures_for_ord():
    g = c.get(f"/api/wave/{ORD}/geo").json()
    assert g["engine_version"] == ENGINE_VERSION and g["procedures"]["cycle"] == "2611" and g["surface"] is None
    assert all(f["src"] in ("cifp", "synth") for f in g["procedures"]["fixes"])

def test_geo_404_when_unavailable(monkeypatch):
    from backend.wave.fixtures import make_wave
    monkeypatch.setattr(server.WB, "build_wave", lambda wid: make_wave())   # fixture: no runway coordinates
    r = c.get(f"/api/wave/{ORD}/geo")
    assert r.status_code == 404 and r.json()["error"]["code"] == "geo_unavailable"

def test_trajectories_touchdown_equals_slot():
    w = c.get(f"/api/wave/{ORD}").json()
    asg = w["status_quo"]["plan"]["assignments"]
    ts = c.post("/api/trajectories", json={"wave_id": ORD, "assignments": asg}).json()
    by = {t["callsign"]: t for t in ts["air"]}
    assert all(abs(by[a["callsign"]]["points"][-1]["t_min"] - a["slot_min"]) < 1e-6 for a in asg if a["action"] == "land")

def test_replay_engine_mismatch_has_no_paths(tmp_path, monkeypatch):
    src = next(p for p in glob.glob("data/replay/*.json") if p.endswith(f"{ORD_REPLAY}.json"))
    d = json.load(open(src)); d.pop("engine_version", None)          # => treated as "v2"
    (tmp_path / f"{ORD_REPLAY}.json").write_text(json.dumps(d))
    from backend import datastore
    from backend.datastore.files import FilePaths, FileStore
    # replay.load reads get_store().replay(wave_id); monkeypatch restores the previous store after the test
    monkeypatch.setattr(datastore, "_store", FileStore(FilePaths(replay_dir=tmp_path, runs_dir=tmp_path / "runs",
                                                                 decisions_path=tmp_path / "decisions.jsonl")))
    r = c.post("/api/negotiate-wave", json={"wave_id": ORD_REPLAY, "mode": "decision", "replay": True, "stream": False}).json()
    assert "trajectories" not in r and r["paths_unavailable"] is True
    assert c.post("/api/trajectories", json={"run_id": r["run_id"]}).status_code == 409

def test_evaluate_suggestion_respects_route_earliest():
    from backend.wave import edit
    w = build.build_wave(ORD)
    asg = c.get(f"/api/wave/{ORD}").json()["status_quo"]["plan"]["assignments"]
    foc = next(a for a in asg if a["callsign"] == w.focus)
    far = _far_runway(w, foc["callsign"])
    foc["runway"] = far                                        # keep the nominal slot: now too early for the far runway
    r = c.post("/api/evaluate", json={"wave_id": ORD, "assignments": asg}).json()
    earliest = trajectory.earliest(w, foc["callsign"], far)
    sug = r["suggestions"].get(foc["callsign"])
    if earliest - foc["slot_min"] > edit.SEARCH_MIN:
        assert sug is None
    else:
        assert sug is not None and sug >= earliest - 1e-6

@pytest.mark.live_db   # opt-in: ATM_LIVE_DB=1 and DATABASE_TEST_URL; the replay guard reads engine_version from the stored body
def test_replay_guard_reads_engine_version_from_pg(live_db_url):
    from backend.datastore.pg import PgStore
    wid = "TEST-REPLAY-GUARD"
    st = PgStore(live_db_url); st.open()
    try:
        st.put_replay(wid, {"messages": [], "result": {}}, engine_version="v2")      # an older recording only
        assert st.replay(wid).get("engine_version", "v2") == "v2"                     # still returned: guard -> 409
        assert wid in st.replay_ids()                                                  # and still listed
        st.put_replay(wid, {"engine_version": ENGINE_VERSION, "messages": [], "result": {}},
                      engine_version=ENGINE_VERSION)
        assert st.replay(wid)["engine_version"] == ENGINE_VERSION                     # the current version wins
        body = st.replay(ORD_REPLAY)
        assert body is not None and body.get("engine_version", "v2") in ("v2", ENGINE_VERSION)
    finally:
        with st._conn() as conn:
            conn.execute("delete from eval.replays where wave_id = %s", (wid,))
        st.close()


def test_suggestions_never_before_route_earliest():
    """Discriminating companion of the test above (whose focus flight already flies its farthest runway at ORD):
    a flight moved to a runway end > 1 min farther along its route, sharing its slot with another wave flight
    there, gets no suggestion earlier than its route-earliest touchdown on that runway."""
    from dataclasses import replace
    from backend.wave import edit, route
    from backend.wave.moves import fcfs_plan, fcfs_runways
    w = build.build_wave(ORD)
    rs, nom = route.routes_for_wave(w), fcfs_runways(w)
    cs, far = next((f.callsign, r.runway) for f in w.flights
                   for r in sorted((r for (c, _), r in rs.items() if c == f.callsign), key=lambda r: -r.t_route_min)
                   if r.t_route_min > rs[(f.callsign, nom[f.callsign])].t_route_min + 1.0)
    p = fcfs_plan(w)
    a = p.get(cs)
    other = next(x for x in p.assignments if x.callsign != cs and x.action == "land")
    p = p.with_assignment(replace(a, runway=far)).with_assignment(replace(other, runway=far, slot_min=a.slot_min))
    earliest = trajectory.earliest(w, cs, far)
    assert earliest - a.slot_min > 1.0
    sug = edit.check_edit(w, p)["suggestions"].get(cs)
    assert sug is not None and sug >= earliest - 1e-6


def test_live_result_embeds_paths_and_run_id_form_serves_them(tmp_path, monkeypatch):
    from backend import datastore
    from backend.datastore.files import FilePaths, FileStore
    monkeypatch.setattr(datastore, "_store", FileStore(FilePaths(replay_dir=tmp_path, runs_dir=tmp_path / "runs",
                                                                 decisions_path=tmp_path / "decisions.jsonl")))
    server._RATE.clear()
    r = c.post("/api/negotiate-wave", json={"wave_id": ORD, "mode": "formulas", "stream": False}).json()
    assert r["engine_version"] == ENGINE_VERSION and "paths_unavailable" not in r
    fin, base = r["trajectories"]["final"], r["trajectories"]["baseline"]
    assert fin["plan_id"] == r["evaluation"]["plan_id"] and base["plan_id"] == r["baseline"]["evaluation"]["plan_id"]
    assert c.post("/api/trajectories", json={"run_id": r["run_id"]}).json() == fin
    assert c.post("/api/trajectories", json={"run_id": "nope"}).status_code == 404
    assert c.post("/api/trajectories", json={}).status_code == 422
    assert all(p["alt_src"] in ("constraint", "interp") for a in fin["air"] for p in a["points"])
    ev = c.post("/api/evaluate", json={"wave_id": ORD, "assignments": r["final_plan"]["assignments"],
                                       "include_trajectories": True}).json()
    assert ev["trajectories"] == fin


def test_geo_gzip_and_etag():
    r = c.get(f"/api/wave/{ORD}/geo", headers={"Accept-Encoding": "gzip"})
    assert r.headers["content-encoding"] == "gzip" and "Accept-Encoding" in r.headers["vary"]
    assert c.get(f"/api/wave/{ORD}/geo", headers={"Accept-Encoding": "identity"}).headers.get("content-encoding") is None
    assert c.get(f"/api/wave/{ORD}/geo", headers={"If-None-Match": r.headers["etag"]}).status_code == 304
    assert c.get("/api/wave/F2015-999999999/geo").status_code == 404
