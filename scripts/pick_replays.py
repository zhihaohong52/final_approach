"""Rank flights2015 waves at a hub by the holding laps the FCFS plan or the formula-agreed plan produces (spec §6.2: the radar demo needs
real holding paths).  Usage: PYTHONPATH=. python3 scripts/pick_replays.py ORD MIA ATL DFW DEN [--scan N]

rank(hub, n) -> [(wave_id, fcfs_plan_hold_min, max_laps)]; max_laps is over the FCFS plan or the formula-agreed plan, the hold minutes are the FCFS plan's only; sorted by max_laps desc, then hold desc.  Candidates are
the `scan` most delayed (weather + late-aircraft) supported rows at the hub, so the scan stays bounded."""
from __future__ import annotations

import sys

SCAN = 300
_SKIPPED: list[str] = []   # candidates that failed to build or negotiate (counted, not hidden)


def _score(wave_id: str) -> tuple[float, int] | None:
    from backend.wave import build, graph, moves, trajectory
    from backend.wave.edit import plan_from_dicts
    try:
        w = build.build_wave(wave_id)
        ts = trajectory.build(w, moves.fcfs_plan(w))
        air = [t for t in ts.air if t.wave]
        laps = max((t.laps for t in air), default=0)
        hold = sum(t.laps * (t.lap_min or 0.0) + t.vector_min for t in air)
        # the agreed plan (formula arm: deterministic, no model call) can hold too (the Task 15 hold move)
        fp = graph.negotiate_wave(w, "formulas", chain=["formula"]).get("final_plan")
        if fp:
            agreed = trajectory.build(w, plan_from_dicts(fp["assignments"]))
            laps = max([laps] + [t.laps for t in agreed.air if t.wave])
    except Exception as e:
        print(f"  skipped {wave_id}: {type(e).__name__}: {e}"[:200], file=sys.stderr, flush=True)
        _SKIPPED.append(wave_id)
        return None
    return hold, laps


def candidates(hub: str, scan: int = SCAN) -> list[str]:
    from backend.datastore import files
    df = files._flights2015_index()
    df = df[df["supported"] & (df["dest"] == hub.upper())].copy()
    df["_s"] = df["weather_delay"].fillna(0) + df["late_aircraft_delay"].fillna(0)
    top = df.sort_values(["_s", "row_id"], ascending=[False, True]).head(scan)
    return [f"F2015-{int(r)}" for r in top["row_id"]]


def rank(hub: str, n: int = 20, scan: int = SCAN) -> list[tuple[str, float, int]]:
    rows = []
    for wid in candidates(hub, scan):
        s = _score(wid)
        if s is not None:
            rows.append((wid, round(s[0], 1), s[1]))
    rows.sort(key=lambda r: (-r[2], -r[1]))
    return rows[:n]


if __name__ == "__main__":
    import backend  # noqa: F401  (loads .env)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    scan = int(sys.argv[sys.argv.index("--scan") + 1]) if "--scan" in sys.argv else SCAN
    args = [a for a in args if not a.isdigit()]
    for hub in args:
        print(hub, flush=True)
        _SKIPPED.clear()
        for wid, hold, laps in rank(hub, 3, scan):
            print(f"  {wid}  max_laps={laps}  fcfs_plan_hold_min={hold}  (laps: FCFS or formula-agreed plan)", flush=True)
        print(f"  skipped {len(_SKIPPED)} of {scan} candidates", flush=True)
