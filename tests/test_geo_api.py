import glob, json
import pytest
from fastapi.testclient import TestClient
from backend import server
from backend.wave import build, trajectory
from backend.wave.version import ENGINE_VERSION
from scripts.make_samples import WAVE_ID as ORD_REPLAY   # the ORD replay id; T18 may change it

from tests.test_trajectory import _far_runway

c = TestClient(server.app)
ORD = "F2015-270287"   # a flights2015 row; builds regardless of which replay files exist

def test_geo_procedures_for_ord():
    g = c.get(f"/api/wave/{ORD}/geo").json()
    assert g["engine_version"] == ENGINE_VERSION and g["procedures"]["cycle"] == "2611"
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
    d["result"].pop("engine_version", None)                          # (load falls back to the result's version)
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
    rr = c.get(f"/api/runs/{r['run_id']}", headers={"Accept-Encoding": "gzip"})
    assert rr.headers.get("content-encoding") == "gzip" and rr.json()["run_id"] == r["run_id"]
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


# ---------------------------------------------------------------- fix round 1: bounded input, ETag, replay version

def _sq():
    return c.get(f"/api/wave/{ORD}").json()["status_quo"]["plan"]["assignments"]


@pytest.mark.parametrize("patch", [
    {"slot_min": 1e308}, {"slot_min": 1e5}, {"slot_min": -1e5}, {"slot_min": "nan"}, {"slot_min": "inf"},
    {"speed_reduction_min": -1}, {"speed_reduction_min": 241}, {"ground_delay_min": 1e9},
    {"runway": "99X"}, {"callsign": "ZZ9999"},
])
def test_unbounded_or_unknown_input_is_422(patch):
    for path, extra in (("/api/trajectories", {}), ("/api/evaluate", {}), ("/api/evaluate", {"include_trajectories": True})):
        asg = _sq()
        asg[0] = {**asg[0], **{k: (float(v) if v in ("nan", "inf") else v) for k, v in patch.items()}}
        body = {"wave_id": ORD, "assignments": asg, **extra}   # json.dumps writes NaN / Infinity tokens
        r = c.post(path, content=json.dumps(body), headers={"Content-Type": "application/json"})
        assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request", (path, patch, r.text[:200])


def test_duplicate_or_too_many_assignments_is_422():
    asg = _sq()
    assert c.post("/api/trajectories", json={"wave_id": ORD, "assignments": asg + [asg[0]]}).status_code == 422
    assert c.post("/api/evaluate", json={"wave_id": ORD, "assignments": asg * 3}).status_code == 422


def test_plan_from_dicts_bounds_are_the_waves_own():
    from backend.wave import edit
    w = build.build_wave(ORD)
    lo, hi = edit.slot_bounds(w)
    assert lo <= -60 and hi >= max(f.eta_earliest_min for f in w.flights) + 240
    asg = _sq()
    edit.plan_from_dicts([{**asg[0], "slot_min": hi}] + asg[1:], w)            # the bound itself is legal
    with pytest.raises(ValueError):
        edit.plan_from_dicts([{**asg[0], "slot_min": hi + 1}] + asg[1:], w)


def test_laps_capped():
    from backend.wave import edit
    from backend.wave.moves import fcfs_plan
    w = build.build_wave(ORD)
    p = fcfs_plan(w)
    a = next(x for x in p.assignments if x.action == "land")
    from dataclasses import replace
    slot = trajectory.earliest(w, a.callsign, a.runway) + 200.0
    ts = trajectory.build(w, p.with_assignment(replace(a, slot_min=slot)))
    t = next(x for x in ts.air if x.callsign == a.callsign)
    assert t.laps == trajectory.MAX_HOLD_LAPS and any("capped" in n for n in t.notes)
    assert abs(t.points[-1]["t_min"] - slot) < 1e-6
    assert abs(t.laps * t.lap_min + t.vector_min - 200.0) < 1e-6


def test_geo_etag_is_body_hash_weak_compare_and_star():
    import gzip as _gz, hashlib
    r = c.get(f"/api/wave/{ORD}/geo", headers={"Accept-Encoding": "identity"})
    assert r.headers["etag"] == '"' + hashlib.sha1(r.content).hexdigest() + '"'
    rz = c.get(f"/api/wave/{ORD}/geo", headers={"Accept-Encoding": "gzip"})
    assert rz.headers["etag"] == r.headers["etag"][:-1] + '-gz"'
    for inm in (r.headers["etag"], "W/" + r.headers["etag"], "*", '"x", ' + rz.headers["etag"]):
        assert c.get(f"/api/wave/{ORD}/geo", headers={"If-None-Match": inm}).status_code == 304, inm
    assert c.get(f"/api/wave/{ORD}/geo", headers={"If-None-Match": '"stale"'}).status_code == 200
    r0 = c.get(f"/api/wave/{ORD}/geo", headers={"Accept-Encoding": "gzip;q=0, identity"})
    assert r0.headers.get("content-encoding") is None


def test_trajectories_and_runs_are_gzipped_on_request():
    r = c.post("/api/trajectories", json={"wave_id": ORD, "assignments": _sq()}, headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip" and r.json()["air"]


def test_replay_load_falls_back_to_result_engine_version(tmp_path, monkeypatch):
    from backend import datastore
    from backend.datastore.files import FilePaths, FileStore
    from backend.wave import replay
    (tmp_path / "W1.json").write_text(json.dumps({"messages": [], "result": {"engine_version": ENGINE_VERSION}}))
    (tmp_path / "W2.json").write_text(json.dumps({"messages": [], "result": {}}))
    monkeypatch.setattr(datastore, "_store", FileStore(FilePaths(replay_dir=tmp_path, runs_dir=tmp_path / "runs",
                                                                 decisions_path=tmp_path / "decisions.jsonl")))
    assert replay.load("W1")["engine_version"] == ENGINE_VERSION and replay.load("W2")["engine_version"] == "v2"


def test_include_trajectories_must_be_bool():
    r = c.post("/api/evaluate", json={"wave_id": ORD, "assignments": _sq(), "include_trajectories": "yes"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"


def test_replays_are_current_engine_and_show_laps():
    from backend.wave.edit import plan_from_dicts
    files = sorted(glob.glob("data/replay/*.json")); assert len(files) == 5
    with_lap = 0
    for f in files:
        d = json.load(open(f)); assert d["engine_version"] == ENGINE_VERSION   # stays valid after the M2a/M2/M3 re-records
        w = build.build_wave(d["result"]["wave_id"])
        plans = [d["result"]["final_plan"], d["result"]["baseline"]["plan"]]   # agreed plan or status quo (spec §6.2)
        with_lap += any(any(t.laps >= 1 for t in trajectory.build(w, plan_from_dicts(p["assignments"])).air if t.wave)
                        for p in plans if p)
    assert with_lap >= 3


@pytest.mark.live_db   # opt-in: ATM_LIVE_DB=1 and DATABASE_TEST_URL; the stored replays carry the current engine_version
def test_replays_in_pg_are_current_engine(live_db_url):
    from backend.datastore.pg import PgStore
    st = PgStore(live_db_url); st.open()
    try:
        for f in sorted(glob.glob("data/replay/*.json")):
            wid = json.load(open(f))["result"]["wave_id"]
            assert (st.replay(wid) or {}).get("engine_version") == ENGINE_VERSION
    finally:
        st.close()

def test_geo_surface_and_ground_tracks():
    g = c.get(f"/api/wave/{ORD_REPLAY}/geo").json()
    assert g["surface"]["attribution"] == "© OpenStreetMap contributors" and g["surface"]["stands"]
    w = c.get(f"/api/wave/{ORD_REPLAY}").json()
    ts = c.post("/api/trajectories", json={"wave_id": ORD_REPLAY, "assignments": w["status_quo"]["plan"]["assignments"]}).json()
    assert ts["ground"] and all(t["src"] in ("osm", "synth") for t in ts["ground"])


def test_geo_surface_shape_and_ground_stands_are_drawn():
    g = c.get(f"/api/wave/{ORD_REPLAY}/geo").json()
    s = g["surface"]
    assert s["status"] in ("real", "generic") and s["source"].startswith("OpenStreetMap")
    assert s["taxiways"] and s["runways"] and s["holding_positions"]
    assert all(t["kind"] in ("taxiway", "taxilane") and len(t["line"]) >= 2 for t in s["taxiways"])
    assert all({"id", "lat", "lon"} <= set(h) for h in s["holding_positions"])
    ids = {x["id"] for x in s["stands"]}
    assert len(ids) == len(s["stands"]) and all("geo_src" in x for x in s["stands"])
    assert any("gate" in x for x in s["stands"])
    # every OSM-routed ground track ends at a stand the map draws
    w = c.get(f"/api/wave/{ORD_REPLAY}").json()
    ts = c.post("/api/trajectories", json={"wave_id": ORD_REPLAY, "assignments": w["status_quo"]["plan"]["assignments"]}).json()
    assert all(t["stand"] in ids for t in ts["ground"] if t["src"] == "osm")
    # a stand's gate agrees with the wave's Gate coordinates
    gates = {x["id"]: x for x in w["gates"]}
    for x in s["stands"]:
        if "gate" in x and gates.get(x["gate"], {}).get("lat") is not None and gates[x["gate"]].get("geo_src") != "synth":
            assert abs(gates[x["gate"]]["lat"] - x["lat"]) < 1e-5


def test_stand_labels_unique_and_collision_proof():
    from backend.wave import surface
    sts = [{"id": "b", "ref": "A1"}, {"id": "a", "ref": "A1"}, {"id": "c", "ref": None}, {"id": "d", "ref": "A1/a"}]
    # both colliding stands gate-bound: lowest id owns the ref, the other is disambiguated; no label repeats
    lab = surface.stand_labels(sts, {"a", "b"})
    assert lab["a"] == "A1" and lab["b"] == "A1/b" and lab["c"] == "c" and len(set(lab.values())) == 4
    # a bound stand beats a lower-id unbound one
    assert surface.stand_labels(sts, {"b"})["b"] == "A1"


@pytest.mark.parametrize("wid", sorted(p.split("/")[-1][:-5] for p in glob.glob("data/replay/*.json")))
def test_replay_wave_gate_labels_agree_between_tracks_and_map(wid):
    from backend.wave import wavegeo
    w = build.build_wave(wid)
    gg = wavegeo.surface._geo(w)
    sp = wavegeo.surface_part(w)
    if sp is None or not gg.bind:
        pytest.skip("no surface")
    labels = {x["id"] for x in sp["stands"]}
    assert len(labels) == len(sp["stands"])
    if sp["status"] == "real":
        assert {gg.stand_label[g] for g, b in gg.bind.items() if b["geo_src"] != "synth"} <= labels
    else:
        assert sp["stands"] == []
    assert all(abs(v - round(v, 6)) < 1e-12 for t in sp["taxiways"] for pt in t["line"] for v in pt)


def test_geo_serves_fixed_ground_traffic_once_and_trajectories_only_wave_tracks():
    """Controller ruling T31: the plan-independent fixed traffic is in the cached /geo body (`ground_traffic`), never in
    a TrajectorySet."""
    from backend.wave import ground_traffic
    g = c.get(f"/api/wave/{ORD}/geo").json()
    gt = g["ground_traffic"]
    fixed = ground_traffic.fixed_tracks(build.build_wave(ORD))
    assert gt and len(gt["tracks"]) == len(fixed) > 0
    t0 = gt["tracks"][0]
    assert {"callsign", "kind", "runway", "stand", "points", "waits"} <= set(t0) and len(t0["points"][0]) == 3
    assert all(isinstance(e[0], float | int) and e[1] in ("landing", "takeoff") for v in gt["runway_events"].values()
               for e in v)
    asg = c.get(f"/api/wave/{ORD}").json()["status_quo"]["plan"]["assignments"]
    ts = c.post("/api/trajectories", json={"wave_id": ORD, "assignments": asg}).json()
    assert ts["ground"] and all(t["wave"] for t in ts["ground"])
    fixed_cs = {t.callsign for t in fixed}
    assert not fixed_cs & {t["callsign"] for t in ts["ground"]}


# ---------------------------------------------------------------- T32 ruling: the paths of a rejected move's plan

def _shift(cs: str, minutes=3) -> dict:
    return {"id": "m1", "agent": "ATC", "kind": "shift_slot", "callsigns": [cs], "params": {"minutes": minutes}, "label": "x"}


def test_trajectories_with_move_are_the_engines_moved_plan():
    from backend.wave import edit, moves
    w = build.build_wave(ORD)
    asg = _sq()
    cs = next(a["callsign"] for a in asg if a["action"] == "land" and a["callsign"] != w.focus)
    r = c.post("/api/trajectories", json={"wave_id": ORD, "assignments": asg, "move": _shift(cs)})
    assert r.status_code == 200
    moved = moves.apply(w, edit.plan_from_dicts(asg, w), edit.move_from_dict(_shift(cs)))
    assert r.json() == json.loads(json.dumps(trajectory.build(w, moved).to_dict()))
    assert r.json() != c.post("/api/trajectories", json={"wave_id": ORD, "assignments": asg}).json()


@pytest.mark.parametrize("move", [
    "shift", [], {"agent": "ATC", "callsigns": ["X"]}, {"kind": "shift_slot", "agent": "ATC", "callsigns": []},
    {"kind": "shift_slot", "agent": "ATC", "callsigns": ["A", "B", "C"]},
    {"kind": "shift_slot", "agent": "ATC", "callsigns": ["{cs}"], "params": {"minutes": True}},
    {"kind": "shift_slot", "agent": "ATC", "callsigns": ["{cs}"], "params": {"minutes": "x" * 100}},
    {"kind": "shift_slot", "agent": "ATC", "callsigns": ["{cs}"], "params": {"minutes": 1e9}},     # out of the slot bounds
    {"kind": "shift_slot", "agent": "ATC", "callsigns": ["{cs}"], "params": {"minutes": "soon"}},
    {"kind": "shift_slot", "agent": "ATC", "callsigns": ["ZZ9999"], "params": {"minutes": 3}},     # not a wave flight
    {"kind": "reassign_gate", "agent": "ATC", "callsigns": ["{cs}"], "params": {"gate": "A1"}},  # no authority
    {"kind": "teleport", "agent": "ATC", "callsigns": ["{cs}"], "params": {}},
    # round 3: magnitudes that overflow (math.ceil(inf), isfinite of a huge int) and oversized strings
    *({"kind": k, "agent": "ATC", "callsigns": ["{cs}"], "params": {"minutes": v}}
      for k in ("shift_slot", "speed_reduction", "ground_delay") for v in (1e308, -1e308, "1e308", "-1e308", "RAW_BIG_INT")),
    {"kind": "shift_slot", "agent": "ATC", "callsigns": ["X" * 10_000], "params": {"minutes": 3}},
    {"kind": "shift_slot", "agent": "ATC", "callsigns": ["{cs}"], "params": {"k" * 10_000: 3}},
    {"kind": "shift_slot", "agent": "ATC", "callsigns": ["{cs}"], "params": {"minutes": 3}, "id": "i" * 10_000},
    {"kind": "shift_slot", "agent": "ATC", "callsigns": ["{cs}"], "params": {"minutes": 3}, "label": "l" * 10_000},
    {"kind": "shift_slot", "agent": "ATC", "callsigns": ["{cs}"], "params": {"minutes": 3}, "label": {"x": 1}},
])
def test_bad_move_is_422(move):
    asg = _sq()
    cs = next(a["callsign"] for a in asg if a["action"] == "land")
    raw = json.dumps({"wave_id": ORD, "assignments": asg, "move": move}).replace("{cs}", cs)
    raw = raw.replace('"RAW_BIG_INT"', "1" + "0" * 400)        # a JSON integer no float can hold
    r = c.post("/api/trajectories", content=raw, headers={"Content-Type": "application/json"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request", (str(move)[:120], r.text[:200])
    assert len(r.text) < 2_000                                    # the error never echoes a huge input


def test_move_hot_path_never_reads_the_store(monkeypatch):
    from backend import datastore
    asg = _sq()
    w = build.build_wave(ORD)
    cs = next(a["callsign"] for a in asg if a["action"] == "land" and a["callsign"] != w.focus)
    assert c.post("/api/trajectories", json={"wave_id": ORD, "assignments": asg, "move": _shift(cs)}).status_code == 200

    class Boom:
        def __getattr__(self, name):
            raise AssertionError(f"store read on the hot path: {name}")
    monkeypatch.setattr(datastore, "_store", Boom())
    assert c.post("/api/trajectories", json={"wave_id": ORD, "assignments": asg, "move": _shift(cs, 4)}).status_code == 200
