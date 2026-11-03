"""Regenerate frontend/src/api/samples/* from the real API (FastAPI TestClient over backend.server.app).

Usage: PYTHONPATH=. ATM_STORE=files python3 scripts/make_samples.py [wave_id]
Uses a pre-recorded run (replay) so no model is called; runs and decisions are written to temp dirs."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from backend import datastore, server
from backend.datastore.files import FilePaths, FileStore

OUT = Path(__file__).resolve().parents[1] / "frontend" / "src" / "api" / "samples"
WAVE_ID = "F2015-310938"   # the ORD replay wave (importable: tests read it; override with argv[1])


def _write_json(name: str, data) -> None:
    (OUT / name).write_text(json.dumps(data, indent=2) + "\n")
    print("wrote", name)


def main(wave_id: str = WAVE_ID) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp, \
            mock.patch.object(server, "REPLAY_SPACING_S", 0):
        datastore.set_store(FileStore(FilePaths(runs_dir=Path(tmp) / "runs",
                                                decisions_path=Path(tmp) / "decisions.jsonl")))
        c = TestClient(server.app)

        def get(path: str):
            r = c.get(path)
            r.raise_for_status()
            return r.json()

        _write_json("config.json", get("/api/config"))
        _write_json("flights.json", get("/api/flights?airport=ATL&limit=3"))
        _write_json("waves.json", get("/api/waves?limit=3"))
        _write_json("wave.json", get(f"/api/wave/{wave_id}"))
        _write_json("geo.json", get(f"/api/wave/{wave_id}/geo"))

        r = c.post("/api/negotiate-wave", json={"wave_id": wave_id, "mode": "decision", "replay": True})
        r.raise_for_status()
        (OUT / "negotiate_stream.txt").write_text(r.text)
        print("wrote negotiate_stream.txt")
        run_id = next(json.loads(b.split("data: ", 1)[1]) for b in r.text.split("\n\n")
                      if b.startswith("event: start"))["run_id"]
        result = get(f"/api/runs/{run_id}")
        _write_json("result.json", result)

        # the agreed plan's paths, recomputed from its assignments (also when the replay predates this engine)
        t = c.post("/api/trajectories", json={"wave_id": wave_id, "assignments": result["final_plan"]["assignments"]})
        t.raise_for_status()
        _write_json("trajectories.json", t.json())

        d = c.post("/api/decision", json={"run_id": run_id, "decision": "APPROVE"})
        d.raise_for_status()
        _write_json("decision.json", d.json())

        if datastore.get_store().benchmark_current() is not None:
            _write_json("benchmark.json", get("/api/benchmark"))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else WAVE_ID)
