import time

import psycopg

from backend import datastore
from backend.datastore import pg
from backend.datastore.fallback import FallbackStore


class Exploding:                                     # any primary call fails the test
    def __getattr__(self, name):
        raise AssertionError(f"config called primary.{name}")


class Down:                                          # a database that is asleep or out of compute hours
    def __getattr__(self, name):
        def fail(*a, **k): raise psycopg.OperationalError("connection refused")
        return fail


def test_config_files_store(api_client):
    c = api_client.get("/api/config").json()
    assert c["store"] == {"backend": "files", "degraded": False, "reason": None, "persistent": False}


def test_config_never_queries_the_database(api_client):
    datastore.set_store(FallbackStore(Exploding(), datastore.get_store()))
    c = api_client.get("/api/config").json()
    assert c["store"] == {"backend": "postgres", "degraded": False, "reason": None, "persistent": True}


def test_down_db_reports_degraded_and_not_persisted(api_client, monkeypatch):   # design 13
    monkeypatch.setattr(pg.time, "sleep", lambda s: None)
    datastore.set_store(FallbackStore(Down(), datastore.get_store()))
    d = api_client.post("/api/decision", json={"run_id": "0123456789ab", "decision": "APPROVE"}).json()
    assert d["ok"] is True and d["persisted"] is False
    s = api_client.get("/api/config").json()["store"]
    assert s["backend"] == "files" and s["degraded"] is True and s["persistent"] is False


def test_decision_reports_persisted(api_client):
    r = api_client.post("/api/negotiate-wave", json={"wave_id": "ATL-2024-07-15-16", "mode": "formulas", "stream": False}).json()
    d = api_client.post("/api/decision", json={"run_id": r["run_id"], "decision": "APPROVE"}).json()
    assert d["ok"] is True and d["persisted"] is False      # FileStore is not persistent on Render's disk


def test_runs_list_for_wave(api_client):
    r = api_client.post("/api/negotiate-wave", json={"wave_id": "ATL-2024-07-15-16", "mode": "formulas", "stream": False}).json()
    for _ in range(40):                                       # background save thread: poll up to 2 s
        runs = api_client.get("/api/runs?wave_id=ATL-2024-07-15-16&limit=5").json()["runs"]
        if runs: break
        time.sleep(0.05)
    assert runs[0]["run_id"] == r["run_id"] and runs[0]["decided"] is False


def test_runs_list_requires_wave_id(api_client):
    r = api_client.get("/api/runs")
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"


def test_v1_decision_rejects_out_of_range_flight_id(api_client):
    r = api_client.post("/api/decision", json={"flight_id": 2**40, "plan_id": "p", "decision": "APPROVE"})
    assert r.status_code == 422
