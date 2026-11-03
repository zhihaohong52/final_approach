"""Record and load pre-recorded negotiation runs (data/replay/{wave_id}.json) for no-model-call replay."""
from __future__ import annotations

import json
from pathlib import Path

from backend.datastore import get_store
from backend.wave.loader import _load

WB = _load("build")
GR = _load("graph")

REPLAY_DIR = Path(__file__).resolve().parents[2] / "data" / "replay"


def record(wave_ids: list[str], mode: str = "decision", out_dir: str | Path = REPLAY_DIR) -> list[str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for wid in wave_ids:
        msgs: list[dict] = []
        res = GR.negotiate_wave(WB.build_wave(wid), mode, chain=["formula"] if mode == "formulas" else None,
                                on_message=msgs.append)
        res = {k: v for k, v in res.items() if k != "log"}
        p = out / f"{wid}.json"
        body = {"messages": msgs, "result": res}
        p.write_text(json.dumps(body))
        store = get_store()
        store.put_replay(wid, body, engine_version=body.get("engine_version"))   # None: the store's current version
        paths.append(str(p))
        _report_degraded(store, f"replay {wid}")
    return paths


def _report_degraded(store, what: str) -> None:
    """Scripts write through get_store(): Postgres when DATABASE_URL_POOLED (or ATM_STORE=pg) is set, else files.
    Say so plainly when a configured database did not take the write (it went to the files only)."""
    st = store.status()
    if st.degraded:
        print(f"WARNING: {what} written to files only; the database write fell back ({st.reason})", flush=True)


def load(wave_id: str) -> dict:
    """Raises FileNotFoundError if the wave has no recording."""
    rec = get_store().replay(wave_id)
    if rec is None:
        raise FileNotFoundError(wave_id)
    return rec
