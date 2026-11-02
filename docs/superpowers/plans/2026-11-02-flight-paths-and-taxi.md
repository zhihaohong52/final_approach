# Final Approach engine v3: flight paths, holding and taxi traffic — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every arrival flies an engine-computed 4D path (published STAR + approach, holding laps in a stack) and taxis a real OSM route among real ground traffic. The negotiation, the 14 safety rules, the radar, a new surface map and the 50-wave benchmark all use those paths.

**Architecture:**
- Offline scripts turn FAA CIFP, OpenStreetMap and BTS 2015 into compact per-airport files under `data/ref/`. These are committed and never fetched at runtime.
- Pure engine modules (`route`, `holding`, `trajectory`, `surface`, `ground_traffic`) build one `TrajectorySet` per plan.
- `utility.metrics_map` derives every metric from that set, and the API serves the same objects.
- Each milestone switches `ENGINE_VERSION`, re-records replays and adds a benchmark row.

**Tech Stack:** Python 3.12 stdlib (`heapq`, `math`, `gzip`, `json`, `functools.lru_cache`), pandas, FastAPI, pytest; React 18 + TypeScript 5.6 + Vite 5; vitest (new devDependency, frontend pure-function tests only). No new runtime Python dependencies (no networkx/shapely).

**Spec:** `docs/superpowers/specs/2026-11-02-flight-paths-and-taxi-design.md` (approved 2026-11-02). Also `docs/api-contract.md` (v2.1 → v2.2).

## Global Constraints

- Commit only when the user has given the go-ahead; branch `redesign/console`; never push or merge to `main`. Never touch or print `.env`.
- Contract changes (`docs/api-contract.md`, `frontend/src/api/types.ts`, `frontend/src/api/samples/`) go in commits prefixed `contract:`. Contract v2.2 is additive only.
- All of `frontend/` is in scope (coordinator decision, 2026-11-02).
- No model decides legality. Every `RuleCheck` cites `rule`, `source` and `evidence`. Every new limit is a row in `rules.LIMITS` with `source: "workbook" | "real data" | "assumption"`.
- Every trajectory point and taxi segment carries `src: "cifp" | "osm" | "synth"`. Synthesized parts are drawn dashed. Never draw a path the engine did not use.
- Runtime never fetches CIFP/OSM/BTS. Raw downloads go to `.cache/` (gitignored and dockerignored); derived files go to `data/ref/`.
- CIFP: pinned cycle **2611**, `https://aeronav.faa.gov/Upload_313-d/cifp/CIFP_261029.zip`. UI string: "Procedures: FAA CIFP 2611, not for navigation". The FAA disclaimer goes next to the derived files.
- OSM attribution "© OpenStreetMap contributors" is always visible on the surface map and in the README.
- Pinned physics: holding IAS 200 KIAS ≤ 6,000 ft, 230 KIAS 6,001-14,000 ft, 265 KIAS above; inbound leg 1.0 min ≤ 14,000 ft, 1.5 min above; right turns unless published; TAS = IAS × (1 + 0.02 × alt_ft/1000); turn rate is the least-bank option among 3°/s, 30° and 25° bank; g = 9.80665.
- Stack: 1,000 ft levels, 5 levels, bottom-first exit; only flights with `laps ≥ 1` enter the stack.
- Terminal speed profile: 280 KIAS above 10,000 ft, 250 below, 210 from the IAF, 170 at the FAF, 140 kt final (minus the METAR headwind on final only).
- Synthetic geometry: corner posts 40 NM at 045/135/225/315° true; downwind 5 NM abeam; join at the IF, or 2 NM before the FAF, at a 30° intercept; straight-in 10 NM at 3°; default hold base 6,000 ft AGL.
- Taxi: 20 kt straight; 10 kt for turns > 30° and on taxilanes; +30 s to park. Required roll: narrow 1,500 m, wide 2,000 m, ×1.15 wet. ROT = 2d/(v_td + v_exit) with v_exit 25 kt (exit angle ≤ 45°) or 12 kt.
- Ground conflicts: node headway 30 s; crossing gap: no landing on the strip in [t−60 s, t+90 s] and no takeoff in [t−60 s, t+60 s]; search horizon 30 min.
- Generic surface tier: straight line from the runway midpoint to the gate point × 1.4 at 15 kt.
- Gate→stand binding radius 150 m; OSM snap tolerance 15 m; surface quality gate: every open runway end reaches ≥ 80 % of wave stands.
- Budgets:
  - `utility.evaluate` p95 ≤ 3 ms on the ORD replay wave;
  - ≤ 60 MB RSS per loaded airport;
  - largest agent prompt ≤ 8,000 tokens (estimate = chars/4);
  - move label ≤ 120 chars;
  - movements data ≤ 50 MB;
  - procedures data ≤ 3 MB total.
- Engine versions: `"v2"` (implicit, no field), `"v3-air"` (M1), `"v3-time"` (M2a), `"v3-taxi"` (M2), `"v3-traffic"` (M3).
- Benchmark: same 50 ids, seed 7, same 4 arms (`typesafe/jev-1.14`, `liquid/d1-ultra`, `baseten/elephant-1`, `formula`). Failing waves are listed in `failures`, never dropped.

## Review Focus

1. **Wave airport with no coordinates or no derived files** (synthetic `make_wave` fixture, PBI, ISN, BTS 2024 waves at airports outside the top 30). Expected: the engine runs in the *no-geometry tier*, returning v2 numbers exactly, a `generic` provenance badge, and `geo_available: false`. Pinned in Task 13 (`test_no_geometry_tier_matches_v2`) and Task 16 (`test_geo_404_when_unavailable`).
2. **Runway closed by wind, or a runway that did not exist on the wave date** (ORD 09C/27C before 2020-11-05). Expected: no route is built for it, and `change_runway` never offers it. Pinned in Task 11 (`test_route_skips_runway_not_open_on_wave_date`).
3. **A replay recorded under an older engine version.** Expected: messages still stream, there are no paths, `/api/trajectories` returns 409 and the UI shows a banner. Pinned in Task 16 (`test_replay_engine_mismatch_has_no_paths`).
4. **Duty-manager drag to a slot earlier than the runway-dependent earliest, or to a different runway.** Expected: the `PLT-EARLIEST-ETA` failure cites `route_min`, and `/api/evaluate` suggestions respect the new earliest. Pinned in Task 14 (`test_earliest_eta_cites_route_for_far_runway`) and Task 16 (`test_evaluate_suggestion_respects_route_earliest`).
5. **Two wave flights converging on the same taxiway head-on, or a crossing needed while landings are continuous.** Expected: a deterministic wait, or an `ATC-TAXI-CONFLICT` failure after the 30-min horizon, never an exception or an infinite loop. Pinned in Task 29 (`test_head_on_resolved_by_wait`, `test_crossing_blocked_beyond_horizon_fails`).

---

## File Structure

**New, offline data (`atm/geo/` is offline-build logic plus runtime loaders; no engine logic)**
- `atm/wave/icao.py`: IATA→ICAO lookup from `data/challenge3/airports.dat`.
- `atm/geo/__init__.py`.
- `atm/geo/cifp.py`: fixed-column ARINC 424-18 record parsing and per-airport extraction.
- `atm/geo/osm_surface.py`: OSM JSON → simplified taxi graph dict (offline).
- `atm/geo/gates_geo.py`: gate id → stand coordinates binding.
- `atm/geo/store.py`: runtime loaders `load_procedures`, `load_surface`, `load_movements` (LRU, maxsize 4).
- `scripts/fetch_cifp.py`, `scripts/fetch_surface.py`, `scripts/geo_coverage.py`, `scripts/pick_replays.py`.
- Modified: `scripts/fetch_bts.py` (new `--movements2015` step) and `scripts/split_flights2015.py` (taxi columns).
- Data: `data/ref/procedures/{ICAO}.json.gz`, `data/ref/procedures/README.md` (FAA disclaimer), `data/ref/surface/{ICAO}.json.gz`, `data/ref/movements2015/{IATA}.csv.gz`, `data/ref/GEO_COVERAGE.md`.

**New engine**
- `atm/wave/version.py`: `ENGINE_VERSION`.
- `atm/wave/holding.py`: hold geometry, lap time, stack levels.
- `atm/wave/route.py`: STAR / approach / synthetic route per (flight, runway end).
- `atm/wave/trajectory.py`: `TrajectorySet` per plan, earliest landing, memoisation.
- `atm/wave/surface.py`: runtime taxi graph, exit choice, ROT, unimpeded routes, hold-short scheduler.
- `atm/wave/ground_traffic.py`: fixed ground traffic (queue arrivals and real departures), reservations.

**Modified engine**
- `atm/wave/model.py`: new `FlightMetrics` fields, `Gate` geo fields, `QueueArrival.origin`.
- `atm/wave/utility.py`: `metrics_map` replaces `metrics`.
- `atm/wave/rules.py`: 14 rules and new `LIMITS` rows.
- `atm/wave/moves.py`: `fcfs_runways`, runway-dependent `_min_slot`, the `hold` move, labels.
- `atm/wave/graph.py`: views and prompt.
- `atm/wave/build.py` and `atm/wave/flights2015.py`: ICAO, time base, gate geo, provenance.
- `atm/wave/replay.py`: `engine_version`.
- `atm/wave/benchmark.py`: engine history, lap share, taxi validation, coverage.
- `atm/wave/narrate.py`: brief mentions laps and hold-shorts.
- `atm/mcp_server.py`: `get_trajectory` tool.
- `atm/server.py`: `/api/wave/{id}/geo`, `POST /api/trajectories`, evaluate flag, result embedding, 409 guard.

**Frontend**
- `frontend/src/geo/track.ts` (new): `positionAt`, `projector` (pure; vitest).
- `frontend/src/geo/track.test.ts` (new).
- `frontend/src/components/Radar.tsx` (modify).
- `frontend/src/components/Surface.tsx` (new; replaces `AirportMap.tsx`, which is deleted).
- `frontend/src/components/Gantt.tsx`, `Strips.tsx`, `WaveScreen.tsx`, `frontend/src/api/client.ts`, `frontend/src/api/types.ts` (modify).
- `frontend/package.json` (add vitest and a `test` script).

**Tests (new)**
- `tests/test_icao.py`, `tests/test_cifp.py`, `tests/test_osm_surface.py`, `tests/test_gates_geo.py`, `tests/test_geo_store.py`.
- `tests/test_holding.py`, `tests/test_route.py`, `tests/test_trajectory.py`, `tests/test_surface.py`, `tests/test_ground_traffic.py`, `tests/test_geo_api.py`, `tests/test_perf.py`.
- Test data: `tests/data/cifp/kord_2611.txt`, `tests/data/osm/mini_airport.json`, `tests/data/movements/mini.csv`. The directory is named `tests/data/`, not `tests/fixtures/`, because a `tests/fixtures/` directory next to the existing `tests/fixtures.py` would break `from tests.fixtures import make_wave`.

---

## Milestone M0: Data and coverage

### Task 1: IATA→ICAO lookup

**Files:**
- Create: `atm/wave/icao.py`, `tests/test_icao.py`
- Modify: `atm/wave/flights2015.py:151-155` (`_icao` delegates)

**Interfaces:**
- Produces: `icao_for(iata: str) -> str` (raises `KeyError` if unknown) and `iata_for(icao: str) -> str | None`. The source is OpenFlights `data/challenge3/airports.dat` (columns 5 = IATA, 6 = ICAO), loaded once via `functools.cache`.

- [ ] **Step 1: Write the failing test**

```python
import os
import pytest
from atm.wave.icao import icao_for, iata_for

@pytest.mark.parametrize("iata,icao", [("OGG", "PHOG"), ("SJU", "TJSJ"), ("FCA", "KGPI"),
                                       ("KOA", "PHKO"), ("ORD", "KORD"), ("PBI", "KPBI")])
def test_icao_for_known(iata, icao):
    assert icao_for(iata) == icao

def test_every_flights2015_destination_resolves():
    dests = [f[:-7] for f in os.listdir("data/challenge2/flights")]
    assert len(dests) == 320
    assert all(len(icao_for(d)) == 4 for d in dests)

def test_iata_for_roundtrip():
    assert iata_for("KORD") == "ORD" and iata_for("ZZZZ") is None

def test_flights2015_uses_lookup():
    from atm.wave import flights2015
    assert flights2015._icao("OGG") == "PHOG"
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_icao.py -q`. Expected: FAIL (`ModuleNotFoundError: atm.wave.icao`).
- [ ] **Step 3: Implement** `icao_for` / `iata_for` in `atm/wave/icao.py`. Make `flights2015._icao(iata)` return `icao_for(iata)`, falling back to the old prefix rule only on `KeyError`.
- [ ] **Step 4: Run** `python3 -m pytest tests/test_icao.py tests/test_flights2015.py -q`. Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add atm/wave/icao.py atm/wave/flights2015.py tests/test_icao.py
git commit -m "fix: IATA to ICAO from OpenFlights (OGG was PHGG, SJU TJJU, FCA KFCA, KOA PHOA)"
```

### Task 2: CIFP record parser and per-airport extractor

**Files:**
- Create: `atm/geo/__init__.py`, `atm/geo/cifp.py`, `tests/test_cifp.py`, `tests/data/cifp/kord_2611.txt`

**Interfaces:**
- Produces:
  - `parse_lat(s: str) -> float`, `parse_lon(s: str) -> float`: ARINC `NddmmssSS` / `WdddmmssSS`.
  - `@dataclass Leg(seq: int, fix: str | None, desc: str, turn: str | None, pt: str, course_mag: float | None, dist_nm: float | None, time_min: float | None, alt_desc: str, alt1_ft: int | None, alt2_ft: int | None, vpa_deg: float | None, center: str | None, route_type: str, transition: str)`, from `parse_leg(line: str) -> Leg`. Columns (1-based, verified on cycle 2611):
    - ident 14-19, route type 20, transition 21-25, seq 27-29, fix 30-34, description 40-43 (col 43 `F` = FAF, `I` = IF; col 40-42 per ARINC), turn 44, path terminator 48-49;
    - course 71-74 (tenths, magnetic); distance or time 75-78 (`T010` = 1.0 min, otherwise tenths of NM);
    - altitude description 83, alt1 85-89, alt2 90-94 (`FL220` → 22000), vertical angle 103-106 (`-300` → 3.00);
    - RF center fix 107-111.
  - `extract_airport(lines: Iterable[str], icao: str, cycle: str) -> dict | None`, returning the procedures schema below (`None` if there is no PA record).
- Procedures schema (used by every later task):

```text
{"header": {"source": "FAA CIFP", "cycle": "2611", "script": "fetch_cifp.py/1"},
 "airport": {"icao", "lat", "lon", "elev_ft", "magvar_deg"(east +)},
 "fixes": {ident: [lat, lon]},                      # PC, EA, D, DB, PG used by kept legs
 "runways": {"10C": {"lat", "lon", "heading_mag"}},  # PG thresholds
 "stars": [{"id", "enroute": {trans: [Leg]}, "common": [Leg], "runway": {"27L"|"27B"|"ALL": [Leg]}}],
 "approaches": {"10C": {"id": "I10C", "kind": "ILS"|"RNAV"|"OTHER", "transitions": {iaf: [Leg]}, "final": [Leg]}},
 "holds": [{"fix", "inbound_mag", "turn", "leg_min"|null, "leg_nm"|null, "alt_ft", "kind": "HF"|"HM", "proc"}]}
```
  One approach per runway: prefer route type `I` (ILS), then `R`/`H` (RNAV), then other. Legs serialise via `dataclasses.asdict`.

- [ ] **Step 1: Build the fixture.** Copy verbatim from `.cache/cifp/FAACIFP18` (download by hand once with the Task 3 URL) into `tests/data/cifp/kord_2611.txt`:
  - the `KORD` PA record;
  - PG `RW10C`;
  - PC `BFORD`, PC `RAYYY`, PC `SHARN`, PC `RRUDA`, PC `TONIE`;
  - EA `BENKY`;
  - D `CGT`;
  - all `KORD` PE lines for `BENKY6` (route types 1-3, transitions `BFORD`, blank and `RW04B`);
  - all `KORD` PF lines for `I10C` with route type `I`.

  The file is public domain (CIFP disclaimer item 1).
- [ ] **Step 2: Write the failing tests**

```python
from pathlib import Path
import pytest
from atm.geo.cifp import parse_lat, parse_lon, parse_leg, extract_airport

LINES = Path("tests/data/cifp/kord_2611.txt").read_text(encoding="latin-1").splitlines()

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
```

- [ ] **Step 3: Run** `python3 -m pytest tests/test_cifp.py -q`. Expected: FAIL (`ModuleNotFoundError: atm.geo`).
- [ ] **Step 4: Implement** `atm/geo/cifp.py` with the signatures and columns above. STAR route types: `1/4` enroute, `2/5` common, `3/6` runway transition. Approach route type `A` is a transition.
- [ ] **Step 5: Run** `python3 -m pytest tests/test_cifp.py -q`. Expected: PASS.
- [ ] **Step 6: Commit**

```bash
git add atm/geo tests/test_cifp.py tests/data/cifp
git commit -m "feat(geo): ARINC 424-18 CIFP parser and per-airport extractor"
```

### Task 3: CIFP fetch script, derived procedures and loader

**Files:**
- Create: `scripts/fetch_cifp.py`, `atm/geo/store.py`, `data/ref/procedures/README.md`, `data/ref/procedures/*.json.gz` (generated), `tests/test_geo_store.py`
- Modify: `.gitignore`, `.dockerignore` (add `.cache/`, already present in `.gitignore`: verify)

**Interfaces:**
- Consumes: `extract_airport` (Task 2), `icao_for` (Task 1).
- Produces:
  - `store.load_procedures(icao: str) -> dict | None`, cached with `lru_cache(maxsize=4)`.
  - `store.PROCEDURES_DIR = ROOT / "data/ref/procedures"`.
  - `fetch_cifp.py` steps:
    - download to `.cache/cifp/CIFP_261029.zip`;
    - verify the SHA-256 constant `CIFP_SHA256` (recorded on the first run and pasted into the script) and the size 9,113,631 bytes;
    - extract one file per ICAO of: the 320 flights2015 destinations, plus `atm.wave.hubs.HUBS`.

- [ ] **Step 1: Write the failing tests**

```python
from atm.geo import store

def test_kord_procedures_present():
    d = store.load_procedures("KORD")
    assert d["header"]["cycle"] == "2611"
    assert any(s["id"] == "BENKY6" for s in d["stars"]) and "10C" in d["approaches"]

def test_absent_airports():
    assert store.load_procedures("KPBI") is None and store.load_procedures("KISN") is None

def test_coverage_counts():
    import os
    from atm.wave.icao import icao_for
    dests = [f[:-7] for f in os.listdir("data/challenge2/flights")]
    have = [store.load_procedures(icao_for(d)) for d in dests]
    assert sum(bool(h and h["stars"]) for h in have) == 111
    assert sum(bool(h and h["approaches"]) for h in have) == 315

def test_procedures_size_cap():
    from pathlib import Path
    assert sum(p.stat().st_size for p in Path("data/ref/procedures").glob("*.json.gz")) <= 3_000_000
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_geo_store.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `store.load_procedures` and `scripts/fetch_cifp.py`. Write `README.md` with the cycle, the URL, the SHA-256 and the FAA disclaimer items 6 and 7a-c verbatim, plus "not for navigation".
- [ ] **Step 4: Run** `python3 scripts/fetch_cifp.py`, then `python3 -m pytest tests/test_geo_store.py -q`. Expected: PASS. If the 111/315 counts differ, stop: the coverage numbers in spec §4.1 are wrong, so report it rather than editing the test.
- [ ] **Step 5: Commit**

```bash
git add scripts/fetch_cifp.py atm/geo/store.py data/ref/procedures tests/test_geo_store.py .dockerignore
git commit -m "data: FAA CIFP 2611 procedures per airport (STARs, approaches, coded holds)"
```

### Task 4: OSM surface graph builder

**Files:**
- Create: `atm/geo/osm_surface.py`, `tests/test_osm_surface.py`, `tests/data/osm/mini_airport.json`

**Interfaces:**
- Produces: `build_surface(osm: dict, icao: str, runways: list[dict], ref_latlon: tuple[float, float], osm_timestamp: str) -> dict`. Here `runways` are rows of `data/ref/runways.csv` for the airport (`runway, le_lat, le_lon, he_lat, he_lon`). Surface schema:

```text
{"header": {"source": "OpenStreetMap", "license": "ODbL", "attribution": "© OpenStreetMap contributors", "osm_timestamp"},
 "nodes": [[lat, lon], ...],
 "edges": [[u, v, length_m, kind("taxiway"|"taxilane"|"runway"), name|null, crossing_strip|null]],   # undirected
 "exits": {"10C": [{"node", "dist_m", "angle_deg"}]},   # per runway END, sorted by dist_m from that threshold
 "stands": [{"id", "ref"|null, "node", "lat", "lon"}],
 "gates": [{"ref", "lat", "lon"}],
 "holding_positions": [{"node", "strip"|null}],
 "terminals": [[lat, lon]],                              # aeroway=terminal centroids
 "draw": {"taxiways": [{"name", "kind", "line": [[lat, lon]]}], "runways": [{"ids", "line", "width_m"}]},
 "quality": {"tier": "real"|"generic", "reach_pct": {"10C": 0.93}}}
```
  - Nodes at way intersections, turn points > 10° and stand/holding positions; degree-2 straight chains are merged.
  - Taxiway endpoints within 15 m of a runway centreline are snapped onto it.
  - An exit is a taxiway edge leaving a runway node.
  - `crossing_strip` is the strip label (`"09R/27L"`) for runway edges that the router may cross.
  - `quality.tier = "real"` iff every runway end in `runways` reaches ≥ 80 % of stands.

- [ ] **Step 1: Write `tests/data/osm/mini_airport.json`.** It is an Overpass-style `elements` list:
  - one runway way `09/27`, 3,000 m east-west;
  - a parallel taxiway `A` 200 m north, joined to the runway by exits `A1` (90°, 500 m from the 09 threshold), `A2` (30°, 1,600 m) and `A3` (90°, 2,500 m);
  - a crossing taxiway `B` crossing a second runway `18/36`;
  - two stands `S1` (ref `1`) and `S2` (ref `2`) on taxilane `T`;
  - one `holding_position` before `18/36`;
  - one taxiway endpoint 10 m off the runway (to test snapping).
- [ ] **Step 2: Write the failing tests**

```python
import json
from atm.geo.osm_surface import build_surface

OSM = json.load(open("tests/data/osm/mini_airport.json"))
RWYS = [...]  # the two runways' end coords, as in the fixture

def _s():
    return build_surface(OSM, "KXXX", RWYS, (0.0, 0.0), "2026-11-02T13:23:20Z")

def test_exits_per_end_sorted_with_angles():
    ex = _s()["exits"]["09"]
    assert [round(e["dist_m"], -2) for e in ex] == [500, 1600, 2500]
    assert [round(e["angle_deg"]) for e in ex] == [90, 30, 90]

def test_snap_within_15m_connects_dangling_taxiway():
    s = _s()
    assert s["quality"]["reach_pct"]["09"] == 1.0

def test_crossing_edges_labelled():
    assert any(e[5] == "18/36" for e in _s()["edges"])

def test_degree2_chains_merged():
    assert len(_s()["nodes"]) < sum(len(w["nodes"]) for w in OSM["elements"] if w["type"] == "way")

def test_quality_generic_when_stands_unreachable():
    broken = json.loads(json.dumps(OSM)); broken["elements"] = [e for e in broken["elements"] if e.get("tags", {}).get("ref") != "T"]
    assert build_surface(broken, "KXXX", RWYS, (0.0, 0.0), "t")["quality"]["tier"] == "generic"
```

- [ ] **Step 3: Run** `python3 -m pytest tests/test_osm_surface.py -q`. Expected: FAIL.
- [ ] **Step 4: Implement** `build_surface`: a local flat projection around `ref_latlon` (metres); reachability by BFS.
- [ ] **Step 5: Run** the same tests. Expected: PASS.
- [ ] **Step 6: Commit** `git commit -m "feat(geo): OSM taxiway graph builder with exits, crossings and quality gate"`

### Task 5: Surface fetch, gate→stand binding, derived surface files

**Files:**
- Create: `atm/geo/gates_geo.py`, `scripts/fetch_surface.py`, `tests/test_gates_geo.py`, `data/ref/surface/*.json.gz` (generated)
- Modify: `atm/geo/store.py`

**Interfaces:**
- Consumes: `build_surface` (Task 4), `icao_for`.
- Produces:
  - `store.load_surface(icao: str) -> dict | None`.
  - `gates_geo.bind_gates(gate_ids: list[tuple[str, bool]], surface: dict) -> dict[str, dict]`. The input is `(gate_id, remote)` pairs. The output maps `gate_id → {"lat", "lon", "stand": str, "node": int, "geo_src": "osm-ref"|"osm-near"|"osm-generic"|"synth"}`. Order:
    1. a stand whose `ref` equals the id;
    2. normalised (`B12` ↔ `12`, case-insensitive, leading letters stripped, within the stands nearest gates whose ref starts with the same letters);
    3. the stand nearest the OSM gate node with that ref, within 150 m;
    4. for generic ids (`A1..`, `W1`, `W2`), stands nearest the first `terminals` centroid, in id order (`osm-generic`);
    5. `RS` → the stand farthest from the terminal that is still reachable (`osm-generic`);
    6. otherwise `synth` (the airport reference point).
  - `fetch_surface.py`:
    - Overpass query by `icao`, then `iata`, then a 6 km bbox;
    - `[out:json][timeout:90]`, `out geom`;
    - 5 s sleep between airports, 2 retries;
    - raw files in `.cache/overpass/{ICAO}.json`;
    - covers the same airport set as Task 3;
    - User-Agent `disruption-negotiator/3 (+repo URL)`.

- [ ] **Step 1: Write the failing tests**

```python
from atm.geo import store
from atm.geo.gates_geo import bind_gates

def test_bind_exact_ref_and_generic_on_fixture():
    import json
    from atm.geo.osm_surface import build_surface
    s = build_surface(json.load(open("tests/data/osm/mini_airport.json")), "KXXX", [...], (0.0, 0.0), "t")
    b = bind_gates([("1", False), ("A1", False), ("RS", True)], s)
    assert b["1"]["geo_src"] == "osm-ref" and b["A1"]["geo_src"] == "osm-generic" and b["RS"]["stand"] != b["A1"]["stand"]

def test_replay_hubs_have_real_surface():
    for icao in ("KORD", "KATL", "KDFW", "KDEN", "KMIA"):
        s = store.load_surface(icao)
        assert s["quality"]["tier"] == "real", icao
        assert len(s["stands"]) >= 200 and s["header"]["attribution"] == "© OpenStreetMap contributors"

def test_ord_replay_gates_bind_to_osm():
    from atm.wave import build
    w = build.build_wave("F2015-270287")
    b = bind_gates([(g.id, g.remote) for g in w.gates], store.load_surface("KORD"))
    assert sum(v["geo_src"] in ("osm-ref", "osm-near") for v in b.values()) >= len(w.gates) - 3
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_gates_geo.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `bind_gates`, `load_surface` and `fetch_surface.py`. Then run `python3 scripts/fetch_surface.py` (long: about 320 × 5 s plus query time; run it in the background).
- [ ] **Step 4: Run** `python3 -m pytest tests/test_gates_geo.py -q`. Expected: PASS. Record the total `data/ref/surface` size in the commit message. If the total is > 15 MB, stop and report (spec §5 estimates 5-12 MB).
- [ ] **Step 5: Commit**

```bash
git add atm/geo scripts/fetch_surface.py tests/test_gates_geo.py data/ref/surface
git commit -m "data: OSM surface graphs per airport (ODbL) and gate to stand binding"
```

### Task 6: 2015 ground movements and taxi columns

**Files:**
- Modify: `scripts/fetch_bts.py` (new `--movements2015` mode), `scripts/split_flights2015.py:20-23` (`FILE_COLS` += `wheels_on, taxi_in, wheels_off, taxi_out`), `atm/geo/store.py`
- Create: `tests/data/movements/mini.csv`, `data/ref/movements2015/{IATA}.csv.gz` (generated), regenerated `data/challenge2/flights/*.csv.gz`
- Test: `tests/test_geo_store.py` (append)

**Interfaces:**
- Produces:
  - `@dataclass Movement(callsign: str, tail: str | None, kind: Literal["arrival", "departure"], date: str, sched_min_abs: int, wheels_min_abs: int | None, taxi_min: float | None, other_airport: str)`.
  - `store.load_movements(iata: str, date: str, from_abs: int, to_abs: int) -> list[Movement] | None`. `None` means no file for the airport; minutes are local minutes since midnight.
  - The movements CSV columns are `date,kind,carrier,flight_no,tail,other,sched,wheels,taxi,cancelled`.
  - The top 30 airports by 2015 arrivals are computed from the downloaded months and written as `TOP30` in `scripts/fetch_bts.py` output (also listed in `GEO_COVERAGE.md`).
  - flights2015 rows gain `wheels_on, taxi_in, wheels_off, taxi_out` (floats, NaN when missing).

- [ ] **Step 1: Write the failing tests**

```python
def test_movements_window_from_fixture(tmp_path, monkeypatch):
    import shutil, gzip
    from atm.geo import store
    p = tmp_path / "XXX.csv.gz"
    with open("tests/data/movements/mini.csv", "rb") as f, gzip.open(p, "wb") as g:
        shutil.copyfileobj(f, g)
    monkeypatch.setattr(store, "MOVEMENTS_DIR", tmp_path); store.load_movements.cache_clear()
    ms = store.load_movements("XXX", "2015-08-19", 8 * 60, 9 * 60)
    assert {m.kind for m in ms} == {"arrival", "departure"} and all(8 * 60 <= m.sched_min_abs < 9 * 60 for m in ms)
    assert store.load_movements("NOPE", "2015-08-19", 0, 1440) is None

def test_flights2015_rows_have_taxi_in():
    from atm.wave import flights2015 as F
    df = F._dest_frame("ORD")
    assert {"wheels_on", "taxi_in", "wheels_off", "taxi_out"} <= set(df.columns) and df["taxi_in"].notna().mean() > 0.99

def test_movements_size_cap():
    from pathlib import Path
    assert sum(p.stat().st_size for p in Path("data/ref/movements2015").glob("*.csv.gz")) <= 50_000_000
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_geo_store.py -q -k "movements or taxi_in"`. Expected: FAIL.
- [ ] **Step 3: Implement** `load_movements`, the movements step (12 PREZIP months for 2015, cached in `.cache/bts/2015_M/`) and the split columns. Re-run `scripts/split_flights2015.py`. If the size cap is exceeded, apply the spec §5 fallback: keep only ±2 h around hours that contain a flights.csv row.
- [ ] **Step 4: Run** `python3 -m pytest tests/test_geo_store.py tests/test_flights2015.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "data: BTS 2015 arrivals and departures for top-30 airports; taxi columns in flights2015 split"`

### Task 7: Coverage report and memory check

**Files:**
- Create: `scripts/geo_coverage.py`, `data/ref/GEO_COVERAGE.md` (generated), `tests/test_perf.py`

**Interfaces:**
- Produces:
  - `geo_coverage.coverage() -> list[dict]`, one row per flights2015 destination with `iata, icao, procedures_tier ("star+iap"|"iap"|"none"), surface_tier ("real"|"generic"|"none"), stands, movements (bool)`.
  - The markdown table is written to `data/ref/GEO_COVERAGE.md`, with totals and the top 30 list.

- [ ] **Step 1: Write the failing tests**

```python
from atm.geo import store

def test_coverage_rows_and_totals():
    from scripts.geo_coverage import coverage   # add scripts/__init__.py if absent
    rows = coverage()
    assert len(rows) == 320 and sum(r["procedures_tier"] == "star+iap" for r in rows) == 111

def test_ord_surface_rss_under_60mb():
    # fresh interpreter: ru_maxrss is a high-water mark, so an in-process delta can read 0
    import subprocess, sys
    code = ("import resource,sys;from atm.geo import store;"
            "b=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss;store.load_surface('KORD');store.load_procedures('KORD');"
            "a=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss;print((a-b)/(1048576 if sys.platform=='darwin' else 1024))")
    mb = float(subprocess.check_output([sys.executable, "-c", code], text=True))
    assert mb <= 60
```

- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** `coverage()` and the markdown writer. Run it to generate `GEO_COVERAGE.md`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "data: geo coverage report (procedures, surface, movements tiers)"`

### Task 8: M0 verification

- [ ] **Step 1:** `python3 -m pytest -q`. Expected: all pass (129 existing plus new).
- [ ] **Step 2:** `cd frontend && npx tsc --noEmit && npm run build`. Expected: exit 0.
- [ ] **Step 3:** `PYTHONPATH=. python3 scripts/make_samples.py`, then `git diff --stat frontend/src/api/samples`. Expected: changes only where the ICAO fix changed a METAR station (none for the 5 hub replays). Note any change in the commit.
- [ ] **Step 4:** `docker build -t dn-m0 .`, then `docker run --rm -e PORT=7860 -p 7860:7860 dn-m0` and `curl -s localhost:7860/api/config`. Expected: JSON with `"replay_available": true`. Record the image size delta vs before M0.
- [ ] **Step 5:** No benchmark row (no runtime metric change). Commit any regenerated samples with `contract: samples regenerated after ICAO fix (no schema change)`, only if they changed.

---

## Milestone M1: Airborne (`v3-air`)

### Task 9: Engine version and contract v2.2 part 1 (model + types)

**Files:**
- Create: `atm/wave/version.py`
- Modify: `atm/wave/model.py` (`FlightMetrics`, `Gate`, `QueueArrival`, `Provenance` doc), `frontend/src/api/types.ts`, `docs/api-contract.md`, `tests/test_model.py`

**Interfaces:**
- Produces:
  - `ENGINE_VERSION = "v3-air"`.
  - `FlightMetrics` adds these fields, all defaulted so v2 constructors still work: `hold_laps: int = 0`, `hold_fix: str | None = None`, `hold_level_ft: int | None = None`, `lap_min: float | None = None`, `vector_min: float = 0.0`, `route_min: float | None = None`, `route_extra_min: float = 0.0`, `exit: str | None = None`, `rot_s: float | None = None`, `taxi_unimpeded_min: float | None = None`, `taxi_wait_min: float = 0.0`, `taxi_min: float | None = None`.
  - `Gate` adds `lat: float | None = None`, `lon: float | None = None`, `geo_src: str | None = None`.
  - `QueueArrival` adds `origin: str | None = None`.
  - `WaveScenario.to_dict()` adds `engine_version` and `geo_available: bool`.
  - TS: the spec §7 types verbatim (`WaveGeo`, `TrajectorySet`, `RuleId` additions, `Move.kind` `"hold"`, `Provenance.layer` additions), `NegotiationResult.engine_version?` and `trajectories?`, `BenchmarkReport.engine_version?`, `coverage?` and `taxi_validation?`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_model.py`)

```python
def test_v22_fields_defaulted_and_serialised():
    from atm.wave.model import FlightMetrics, Gate
    from atm.wave.fixtures import make_wave
    m = FlightMetrics()
    assert m.hold_laps == 0 and m.taxi_min is None and "route_extra_min" in m.to_dict()
    assert Gate("A1", "narrow", "A").to_dict()["geo_src"] is None
    d = make_wave().to_dict()
    assert d["engine_version"] == "v3-air" and d["geo_available"] is False
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_model.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the fields. `geo_available` = `store.load_procedures(icao) is not None and every open runway has coordinates`. Update `types.ts` and `docs/api-contract.md` (status line "v2.2 (2 Nov 2026): …", additive).
- [ ] **Step 4: Run** `python3 -m pytest -q && (cd frontend && npx tsc --noEmit)`. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "contract: v2.2 part 1 (FlightMetrics path fields, Gate geo, engine_version, WaveGeo/TrajectorySet types)"`

### Task 10: Holding geometry

**Files:**
- Create: `atm/wave/holding.py`, `tests/test_holding.py`

**Interfaces:**
- Produces:
  - `tas_kt(ias_kt: float, alt_ft: float) -> float`;
  - `holding_ias_kt(alt_ft: float) -> float`;
  - `leg_min_for(alt_ft: float) -> float`;
  - `turn_rate_dps(tas_kt: float) -> float`;
  - `lap_min(alt_ft: float, leg_min: float | None = None) -> float`;
  - `@dataclass HoldSpec(fix: str, lat: float, lon: float, inbound_true: float, turn: Literal["R", "L"], leg_min: float, base_ft: int, levels_ft: list[int], lap_min: float, src: Literal["cifp", "synth"])`;
  - `make_hold(fix, lat, lon, inbound_true, turn, leg_min, base_ft, src) -> HoldSpec` (levels = base + k×1000 for k = 0..4);
  - `racetrack(h: HoldSpec, step_deg: float = 15.0) -> list[tuple[float, float]]` (closed polygon, lat/lon);
  - `stack_levels(holders: list[tuple[str, float, float]], h: HoldSpec) -> tuple[dict[str, int], list[str]]`. Holders are `(callsign, entry_min, exit_min)`. It returns callsign→level and a list of violation strings (count > 5, or exit not bottom-first).

- [ ] **Step 1: Write the failing tests** (values from the spec §4.4 and Global Constraints)

```python
import pytest
from atm.wave import holding as H

def test_lap_times_pinned():
    assert H.lap_min(12000) == pytest.approx(5.36, abs=0.03)   # 230 KIAS, 25 deg bank
    assert H.lap_min(5000) == pytest.approx(4.59, abs=0.03)    # 200 KIAS
    assert H.lap_min(16000) == pytest.approx(7.12, abs=0.05)   # 265 KIAS, 1.5-min legs

def test_turn_rate_uses_least_bank():
    assert H.turn_rate_dps(H.tas_kt(230, 12000)) == pytest.approx(1.786, abs=0.01)

def test_racetrack_size_and_closure():
    h = H.make_hold("BENKY", 41.511831, -88.709017, 90.0, "R", 1.0, 12000, "synth")
    poly = H.racetrack(h)
    assert poly[0] == pytest.approx(poly[-1])
    # width = 2 x turn radius = 5.08 NM, on the right of the inbound course (south for 090)
    lats = [p[0] for p in poly]
    assert (max(lats) - min(lats)) * 60 == pytest.approx(5.08, abs=0.1) and min(lats) < 41.511831 - 0.05

def test_stack_fifo_and_capacity():
    h = H.make_hold("X", 0, 0, 0, "R", 1.0, 12000, "synth")
    lv, bad = H.stack_levels([("A", 0, 10), ("B", 2, 15), ("C", 3, 20)], h)
    assert lv == {"A": 12000, "B": 13000, "C": 14000} and bad == []
    _, bad = H.stack_levels([("A", 0, 20), ("B", 2, 10)], h)
    assert bad and "B" in bad[0]
    _, bad = H.stack_levels([(c, i, 50) for i, c in enumerate("ABCDEF")], h)
    assert any("6 > 5 levels" in b for b in bad)
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_holding.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** with the pinned constants. When a lower level empties, the occupants above step down a level.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(engine): AIM 5-3-8 holding geometry, lap time and stack levels"`

### Task 11: Route builder (STAR, approach, synthetic fallbacks)

**Files:**
- Create: `atm/wave/route.py`, `tests/test_route.py`
- Modify: `atm/wave/flights2015.py` and `atm/wave/build.py` (set `QueueArrival.origin`)

**Interfaces:**
- Consumes: `store.load_procedures`, `holding.make_hold`, `Wave`, `build._airport_coords(iata)`.
- Produces:
  - `@dataclass RoutePoint(lat: float, lon: float, alt_ft: float, kind: str, fix: str | None, src: Literal["cifp", "synth"], alt_src: Literal["constraint", "interp"], t_min: float)`. Here `t_min` is the time from entry, and `kind` is in `entry|fix|vector|final|threshold`.
  - `@dataclass Route(callsign: str, runway: str, star: str | None, transition: str | None, approach: str | None, entry_fix: str, points: list[RoutePoint], t_route_min: float, hold: HoldSpec, hold_index: int, tier: Literal["star+iap", "iap", "straight-in"])`.
  - `select_star(proc: dict, origin_latlon: tuple[float, float] | None, runway: str) -> tuple[str, str] | None`. It returns `(star_id, transition)`, tie-breaking by lower id. With no origin it uses the first star (by id) that has a transition for the runway.
  - `build_route(wave: Wave, callsign: str, runway_end: str) -> Route | None`. It returns `None` (the no-geometry tier) when the runway end lacks coordinates or the airport has no procedures file **and** no coordinates.
  - `routes_for_wave(wave: Wave) -> dict[tuple[str, str], Route]`, keyed by (callsign, runway end) for wave and queue flights over open runway ends. It is cached by `wave.id` (`lru_cache(maxsize=16)` on the id, with the wave kept in a side dict).
- Rules pinned by the tests:
  - runway transitions `NNB` apply to every end with number NN;
  - procedures whose runway is not in `wave.airport.runways` are skipped (the date wind-back already happened in `build._runways`);
  - a STAR ending in FM/VM, or a STAR whose last fix is not an approach IAF/IF, gets synth vectors: downwind 5 NM abeam, base, then a 30° intercept to the IF, or to 2 NM before the FAF;
  - no STAR: corner post at 40 NM on the nearest of 045/135/225/315° to the origin bearing, then direct to the IAF;
  - no approach: a 10 NM 3° straight-in;
  - hold = **first** fix of the STAR common route (the meter fix where the enroute transitions join; KORD BENKY6 → BENKY at 12,000 ft), or the last enroute-transition fix if there is no common route. Use a coded HF/HM at that fix if one exists (inbound = course + magvar), else `make_hold(..., src="synth")`; base = its altitude constraint, else the corner-post/IAF altitude, else elevation + 6,000 ft;
  - magnetic → true: `true = mag + magvar_deg`.

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from atm.wave import build, route
from atm.wave.fixtures import make_wave

ORD = "F2015-270287"

def test_ord_route_follows_star_and_ends_at_threshold():
    w = build.build_wave(ORD)
    cs = w.flights[0].callsign
    r = route.build_route(w, cs, w.airport.runways[0].id)
    assert r.tier == "star+iap" and r.star is not None
    assert r.points[-1].kind == "threshold" and r.points[0].kind == "entry"
    assert all(b.t_min > a.t_min for a, b in zip(r.points, r.points[1:]))
    assert r.hold.fix == next(p.fix for p in r.points[: r.hold_index + 1][::-1] if p.fix)

def test_fm_ended_star_gets_synth_vectors():
    w = build.build_wave(ORD)
    rs = route.routes_for_wave(w)
    assert any(any(p.kind == "vector" and p.src == "synth" for p in r.points) for r in rs.values())

def test_route_skips_runway_not_open_on_wave_date():
    w = build.build_wave(ORD)   # 2015-08-19: 09C/27C opened 2020-11-05
    assert not any(k[1] in ("09C", "27C") for k in route.routes_for_wave(w))

def test_far_runway_costs_more_route_time():
    w = build.build_wave(ORD)
    cs = w.flights[0].callsign
    times = sorted(route.build_route(w, cs, r.id).t_route_min for r in w.airport.runways if r.open)
    assert times[-1] - times[0] >= 1.0

def test_corner_post_when_no_star(monkeypatch):
    # an airport with approaches but no STARs: ABE per GEO_COVERAGE.md
    from atm.wave import flights2015 as F
    row = F.list_rows(airport="ABE", limit=1)[0]["row_id"]
    w = build.build_wave(f"F2015-{row}")
    r = route.build_route(w, w.flights[0].callsign, next(x.id for x in w.airport.runways if x.open))
    assert r.tier == "iap" and r.points[0].src == "synth" and r.hold.src in ("cifp", "synth")

def test_no_geometry_returns_none():
    w = make_wave()   # fixture runways have no coordinates
    assert route.build_route(w, "DL100", "26R") is None
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_route.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `route.py`, with the speed profile from Global Constraints and TAS via `holding.tas_kt`. RF legs become chords every 10°. Set `QueueArrival.origin` from the row's `origin` in both builders.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(engine): routes from CIFP STAR and approach, synthetic vectors, corner posts, straight-in"`

### Task 12: Trajectory assembly per plan

**Files:**
- Create: `atm/wave/trajectory.py`, `tests/test_trajectory.py`
- Modify: `atm/wave/moves.py` (extract `fcfs_runways(wave) -> dict[str, str]` from `fcfs_plan`; `fcfs_plan` uses it)

**Interfaces:**
- Consumes: `routes_for_wave`, `holding.stack_levels`, `holding.racetrack`.
- Produces:
  - `moves.fcfs_runways(wave: Wave) -> dict[str, str]`: the runway choice loop of v2 `fcfs_plan`, unchanged.
  - `trajectory.entry_time(wave: Wave, callsign: str) -> float | None`: `eta_earliest_min − T_route(nominal)`.
  - `trajectory.earliest(wave: Wave, callsign: str, runway: str, sr: float = 0.0, gd: float = 0.0) -> float`. It equals `eta_earliest_min + sr + gd` in the no-geometry tier and when `runway` is the nominal runway.
  - `@dataclass AirTrack(callsign, wave: bool, runway, appears_min, points: list[dict], hold: dict | None, route_min: float, route_extra_min: float, laps: int, vector_min: float, lap_min: float | None, hold_fix: str | None, hold_level_ft: int | None, src_tier: str)`.
  - `@dataclass TrajectorySet(plan_id: str, engine_version: str, air: list[AirTrack], ground: list = [], conflicts: list = [], stack_violations: dict[str, list[str]] = {})`, with `to_dict()` in contract shape.
  - `trajectory.build(wave: Wave, plan: JointPlan) -> TrajectorySet`, memoised on `(wave.id, plan.id)` with `lru_cache(maxsize=4096)`.
  - Per flight, with `D = slot − earliest(...)`: `laps = floor(D / lap_min)` and `vector_min = D − laps·lap_min`. The hold points (racetrack, laps times) are inserted at `hold_index`; vector stretch is an extra downwind out-and-back (`kind "vector"`, `src "synth"`) before the final. Fixed traffic: `wave=False`, zero laps, timed to land at its `slot_min`.
  - `route_extra_min = T_route(r) − T_route(nominal)`.

- [ ] **Step 1: Write the failing tests**

```python
import math
import pytest
from atm.wave import build, trajectory
from atm.wave.moves import fcfs_plan, fcfs_runways
from atm.wave.fixtures import make_wave

ORD = "F2015-270287"

def test_fcfs_runways_matches_v2_fcfs():
    w = build.build_wave(ORD)
    assert fcfs_runways(w) == {a.callsign: a.runway for a in fcfs_plan(w).assignments}

def test_touchdown_equals_slot_and_hold_identity():
    w = build.build_wave(ORD)
    p = fcfs_plan(w)
    a = p.assignments[0]
    late = p.with_assignment(type(a)(**{**a.__dict__, "slot_min": a.slot_min + 12.0}))
    t = next(x for x in trajectory.build(w, late).air if x.callsign == a.callsign)
    D = a.slot_min + 12.0 - trajectory.earliest(w, a.callsign, a.runway)   # FCFS slot may already sit behind queue traffic
    assert t.points[-1]["t_min"] == pytest.approx(a.slot_min + 12.0, abs=1e-6)
    assert t.laps == math.floor(D / t.lap_min) and 0 <= t.vector_min < t.lap_min
    assert t.laps * t.lap_min + t.vector_min == pytest.approx(D, abs=1e-6)
    assert all(q["t_min"] >= p_["t_min"] for p_, q in zip(t.points, t.points[1:]))
    assert all(q["src"] in ("cifp", "synth") for q in t.points)

def test_vector_only_flight_not_in_stack():
    w = build.build_wave(ORD)
    a = fcfs_plan(w).assignments[0]
    t = next(x for x in trajectory.build(w, fcfs_plan(w)).air if x.callsign == a.callsign)
    assert t.laps == 0 and t.hold_level_ft is None

def test_queue_traffic_drawn_with_zero_laps():
    w = build.build_wave("F2015-75")   # DFW, 13 queue arrivals
    ts = trajectory.build(w, fcfs_plan(w))
    assert sum(not x.wave for x in ts.air) == len(w.queue) and all(x.laps == 0 for x in ts.air if not x.wave)

def test_earliest_no_geometry_tier_is_v2():
    w = make_wave()
    f = w.flights[0]
    assert trajectory.earliest(w, f.callsign, "27L", 3, 0) == f.eta_earliest_min + 3
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_trajectory.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `fcfs_runways` (a pure extraction; `tests/test_moves.py` must stay green), then `trajectory.py`.
- [ ] **Step 4: Run** `python3 -m pytest tests/test_trajectory.py tests/test_moves.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(engine): per-plan 4D trajectories with holding laps, vectors and stack levels"`

### Task 13: Joint metrics, runway-dependent earliest, route fuel

**Files:**
- Modify:
  - `atm/wave/utility.py` (remove `metrics`, add public `metrics_map`, fuel and route terms);
  - `atm/wave/moves.py:19,73-76,228,242` (`_min_slot(wave, a)` uses `trajectory.earliest`; per-flight metrics via `metrics_map`);
  - `atm/wave/graph.py:54,86,167,178,712`;
  - `atm/wave/rules.py:14,check` (default `mm` from `metrics_map`).
- Test: `tests/test_trajectory.py` (append), all existing tests.

**Interfaces:**
- Consumes: `trajectory.build`, `trajectory.earliest`.
- Produces:
  - `utility.metrics_map(wave: Wave, plan: JointPlan) -> dict[str, FlightMetrics]`, the only metrics entry point.
  - `hold_min = laps·lap_min + vector_min` (or v2 formula in the no-geometry tier).
  - `fuel_kg = (hold_min + max(route_extra_min, −hold_min)) × burn + SR × burn × 0.20`.
  - Path fields filled from the `AirTrack`.
  - `utility.metrics` no longer exists.

- [ ] **Step 1: Write the failing tests**

```python
def test_metrics_removed_and_map_is_joint():
    import atm.wave.utility as U
    assert not hasattr(U, "metrics")
    from atm.wave import build
    from atm.wave.moves import fcfs_plan
    w = build.build_wave("F2015-270287")
    mm = U.metrics_map(w, fcfs_plan(w))
    assert all(m.route_min is not None for m in mm.values())

def test_route_extra_costs_fuel_on_far_runway():
    from dataclasses import replace
    from atm.wave import build, route, trajectory
    from atm.wave.moves import fcfs_plan
    from atm.wave.utility import metrics_map, hold_burn
    w = build.build_wave("F2015-270287")
    p = fcfs_plan(w); a = p.assignments[0]
    rs = route.routes_for_wave(w)
    far = max((r for (cs, _), r in rs.items() if cs == a.callsign), key=lambda r: r.t_route_min)
    extra = far.t_route_min - rs[(a.callsign, a.runway)].t_route_min
    m = metrics_map(w, p.with_assignment(replace(a, runway=far.runway,
                                                  slot_min=trajectory.earliest(w, a.callsign, far.runway))))[a.callsign]
    assert m.route_extra_min == pytest.approx(extra, abs=1e-6) and m.hold_min == pytest.approx(0, abs=1e-6)
    assert m.fuel_kg == pytest.approx(extra * hold_burn(w.flight(a.callsign)), rel=1e-3)

def test_no_geometry_tier_matches_v2():
    # frozen v2 numbers for the synthetic wave (captured before this task; paste the dict)
    from atm.wave.fixtures import make_wave
    from atm.wave.moves import fcfs_plan
    from atm.wave.utility import evaluate
    ev = evaluate(make_wave(), fcfs_plan(make_wave()))
    assert ev["totals"] == V2_FIXTURE_TOTALS
```

  Before changing code, capture `V2_FIXTURE_TOTALS` by running `evaluate(make_wave(), fcfs_plan(make_wave()))["totals"]` on the current code, and paste it as a module constant.

- [ ] **Step 2: Run** `python3 -m pytest tests/test_trajectory.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the changes; update the call sites listed above.
- [ ] **Step 4: Run** `python3 -m pytest -q`. Expected: everything passes. Existing tests use the synthetic wave (no-geometry tier), so they are unchanged.
- [ ] **Step 5: Commit** `git commit -m "feat(engine): joint metrics from trajectories; runway-dependent earliest landing and route fuel"`

### Task 14: Rules for the airborne model (`ATC-HOLD-STACK`, changed PLT rules)

**Files:**
- Modify: `atm/wave/rules.py` (`LIMITS`, `check`, `_duty_check`), `tests/test_rules.py`

**Interfaces:**
- Consumes: `TrajectorySet.stack_violations`, `trajectory.earliest`, `FlightMetrics` path fields.
- Produces:
  - `RuleCheck("ATC-HOLD-STACK", "ATC", ...)` for every landing wave flight. Source: `"FAA JO 7110.65 §4-5-1 (1,000 ft) and §2-1-4 (first come, first served); 5 levels (assumption)"`. Evidence keys: `hold_fix, laps, level_ft, holders, levels`.
  - `PLT-EARLIEST-ETA` evidence adds `route_min, entry_fix, t_entry`, and its detail names the runway's route minutes.
  - `PLT-FUEL-RESERVE` checks `hold_min + max(0, route_extra_min) ≤ extra_min`; evidence adds `laps, lap_min, hold_fix, route_extra_min`.
  - In `PLT-CREW-DUTY`, "unavoidable" uses `min(earliest over open runways) + taxi`.
  - New `LIMITS` rows: holding geometry (AIM 5-3-8, real), stack (assumption), terminal burn proxy (assumption), speed profile (assumption), procedures cycle (real data, "current procedures flown over 2015 traffic").

- [ ] **Step 1: Write the failing tests** (append; real ORD wave)

```python
def test_hold_stack_fifo_violation_cited():
    w = build.build_wave("F2015-270287")
    # two wave flights sharing a hold fix: later entrant leaves first -> fail
    p = _two_holders_reversed(w)          # helper: same STAR hold fix, B enters after A but slot(B) < slot(A), both >= 1 lap
    c = _by(rules.check(w, p), "ATC-HOLD-STACK", p.assignments[1].callsign)
    assert not c.passed and c.source.startswith("FAA JO 7110.65 §4-5-1")
    assert {"hold_fix", "laps", "level_ft", "holders", "levels"} <= set(c.evidence)

def test_earliest_eta_cites_route_for_far_runway():
    w = build.build_wave("F2015-270287"); p = fcfs_plan(w); a = p.assignments[0]
    far = _far_runway(w, a.callsign)      # helper from test_trajectory
    c = _by(rules.check(w, p.with_assignment(replace(a, runway=far))), "PLT-EARLIEST-ETA", a.callsign)
    assert not c.passed and "route_min" in c.evidence and far in c.detail

def test_fuel_reserve_counts_route_extra():
    ...  # extra_min set to route_extra - 0.5 on the far runway at its earliest slot -> fail; evidence route_extra_min > 0

def test_limits_list_new_rows():
    names = [l["limit"] for l in rules.LIMITS]
    assert "holding stack" in names and "holding pattern geometry" in names and "procedures cycle" in names
```

  `_two_holders_reversed` and `_far_runway` are test helpers. `_two_holders_reversed` searches the wave's routes for a pair sharing `route.hold.fix`; if there is none, it monkeypatches `route.select_star` to give two flights the same STAR and clears the `routes_for_wave` cache. It picks slots `earliest + 1.2·lap` and `earliest + 2.2·lap`.

- [ ] **Step 2: Run** `python3 -m pytest tests/test_rules.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the changes. 13 rules after this task.
- [ ] **Step 4: Run** `python3 -m pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(rules): ATC-HOLD-STACK; earliest ETA and fuel reserve use routes and laps"`

### Task 15: `hold` move, labels and agent views

**Files:**
- Modify: `atm/wave/moves.py` (`AUTHORITY["ATC"]` += `"hold"`, `_candidates`, `_validate`, `apply`, labels), `atm/wave/graph.py:_view`, `tests/test_moves.py`, `tests/test_graph.py`

**Interfaces:**
- Produces:
  - `Move(kind="hold", params={"laps": 1..4})`. `apply` re-slots the flight to the earliest wake-legal slot ≥ `earliest + laps·lap_min` (via `_reslot` with that floor).
  - Label format: `Hold {cs} {laps} lap(s) at {fix} ({FLxxx|xx,xxx ft}, {min:.1f} min, ~{kg:.0f} kg)`.
  - The `shift_slot` label reads `Vector {cs} {±2} min (path stretch)`.
  - The `change_runway` label appends `({±route:.1f} min route)`.
  - `_view` adds `"path"` per flight: `"STAR {star}.{trans} · hold {fix} base {ft} · lap {lap:.1f} min = {kg:.0f} kg"` (ATC and PILOT views).
  - Move labels ≤ 120 chars.

- [ ] **Step 1: Write the failing tests**

```python
def test_hold_move_offered_and_applied():
    w = build.build_wave("F2015-270287"); p = fcfs_plan(w)
    hm = [m for m in legal_moves(w, p, "ATC") if m.kind == "hold"]
    assert hm and all(1 <= m.params["laps"] <= 4 for m in hm)
    m = hm[0]; q = apply(w, p, m)
    t = next(x for x in trajectory.build(w, q).air if x.callsign == m.callsigns[0])
    assert t.laps == m.params["laps"] and m.label.startswith(f"Hold {m.callsigns[0]} ")

def test_labels_within_120_chars():
    w = build.build_wave("F2015-75"); p = fcfs_plan(w)
    for ag in negotiators(w):
        assert all(len(m.label) <= 120 for m in legal_moves(w, p, ag))

def test_largest_prompt_under_8k_tokens():
    # over the 5 replay waves, the biggest prompt any agent receives (chars/4)
    assert max(_prompt_chars(wid) for wid in REPLAY_IDS) / 4 <= 8000
```

  `_prompt_chars` drives `graph.negotiate_wave(w, "formulas", chain=["formula"])` with a patched decider that records the prompt text length. If the decider never builds prompts in formulas mode, record `json.dumps(_view(...))` plus the move labels instead. Say which in a test comment.

- [ ] **Step 2: Run** `python3 -m pytest tests/test_moves.py tests/test_graph.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the changes.
- [ ] **Step 4: Run** `python3 -m pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "contract: Move.kind hold; path-aware move labels and agent views"`

### Task 16: API: `/geo` (procedures), `/api/trajectories`, result embedding, replay guard

**Files:**
- Modify: `atm/server.py`, `atm/wave/replay.py`, `atm/wave/edit.py`, `scripts/make_samples.py` (add `geo.json`, `trajectories.json`), `docs/api-contract.md`
- Create: `tests/test_geo_api.py`

**Interfaces:**
- Consumes: `trajectory.build`, `route.routes_for_wave`, `holding.racetrack`, `ENGINE_VERSION`.
- Produces:
  - `GET /api/wave/{id}/geo` → `WaveGeo` with `surface: null` in M1. It returns 404 `geo_unavailable` when `geo_available` is false, and sends `ETag = sha1(engine_version + wave_id)`.
  - `POST /api/trajectories` `{wave_id, assignments}` → `TrajectorySet`; 422/404 as `/api/evaluate`.
  - `POST /api/evaluate` with `include_trajectories: true` adds `trajectories`.
  - `NegotiationResult` gains `engine_version` and `trajectories: {final, baseline}`.
  - Replay files gain `engine_version`. `replay.load` returns it, defaulting to `"v2"` when absent.
  - On replay with a mismatch, the result is streamed without `trajectories`, and the run is marked `paths_unavailable: true`. `POST /api/trajectories` with `{run_id}` for such a run returns `409 engine_mismatch`.
  - Routes: add `GZipMiddleware(minimum_size=1024)`.

- [ ] **Step 1: Write the failing tests**

```python
from fastapi.testclient import TestClient
from atm import server

c = TestClient(server.app)
ORD = "F2015-270287"

def test_geo_procedures_for_ord():
    g = c.get(f"/api/wave/{ORD}/geo").json()
    assert g["engine_version"] == "v3-air" and g["procedures"]["cycle"] == "2611" and g["surface"] is None
    assert all(f["src"] in ("cifp", "synth") for f in g["procedures"]["fixes"])

def test_geo_404_when_unavailable():
    r = c.get("/api/wave/STUB-NOGEO/geo")      # stub id resolving to make_wave(); or monkeypatch WB.build_wave
    assert r.status_code == 404 and r.json()["error"]["code"] == "geo_unavailable"

def test_trajectories_touchdown_equals_slot():
    w = c.get(f"/api/wave/{ORD}").json()
    asg = w["status_quo"]["plan"]["assignments"]
    ts = c.post("/api/trajectories", json={"wave_id": ORD, "assignments": asg}).json()
    by = {t["callsign"]: t for t in ts["air"]}
    assert all(abs(by[a["callsign"]]["points"][-1]["t_min"] - a["slot_min"]) < 1e-6 for a in asg if a["action"] == "land")

def test_replay_engine_mismatch_has_no_paths(tmp_path, monkeypatch):
    # copy a replay, strip engine_version (=> "v2"), point REPLAY_DIR at tmp
    ...
    r = c.post("/api/negotiate-wave", json={"wave_id": ORD, "mode": "decision", "replay": True, "stream": False}).json()
    assert "trajectories" not in r and r["paths_unavailable"] is True
    assert c.post("/api/trajectories", json={"run_id": r["run_id"]}).status_code == 409

def test_evaluate_suggestion_respects_route_earliest():
    ...  # edit focus flight to far runway at nominal slot; suggestions[cs] >= earliest on far runway
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_geo_api.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the endpoints, embedding and guard. Update the contract doc's endpoint table.
- [ ] **Step 4: Run** `python3 -m pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "contract: v2.2 part 2 (GET /api/wave/{id}/geo, POST /api/trajectories, result trajectories, engine_version replay guard)"`

### Task 17: Radar flies trajectories; strips show path info

**Files:**
- Create: `frontend/src/geo/track.ts`, `frontend/src/geo/track.test.ts`
- Modify:
  - `frontend/package.json` (devDependency `vitest@^2`, script `"test": "vitest run"`);
  - `frontend/src/api/client.ts` (`getGeo(waveId)`, `postTrajectories(waveId, assignments, signal)`);
  - `frontend/src/components/Radar.tsx`, `frontend/src/components/Strips.tsx`, `frontend/src/WaveScreen.tsx`.

**Interfaces:**
- Consumes: the `WaveGeo` and `TrajectorySet` types (Task 9) and the endpoints (Task 16).
- Produces:
  - `positionAt(points: {t_min: number; lat: number; lon: number; alt_ft?: number}[], t: number): {lat: number; lon: number; alt_ft?: number; seg: number} | null`. It returns `null` before the first point and after the last; between points it interpolates linearly.
  - `scanTimes(t: number, n: number, step: number): number[]`.
  - `holdLap(track, t): {lap: number; of: number} | null`.
- Radar behaviour (spec §9):
  - blips at `positionAt(track.points, scan(t))`;
  - trail = 5 previous scans; predictor = `positionAt(t + 1)`;
  - ranges `[10, 20, 40, 80]`;
  - racetracks only for holds used in the shown plan (`WaveGeo.procedures.holds` polygon), with label `{fix} ×{n} · {lo}-{hi}k`;
  - at most 8 fix labels (entry and hold fixes, and the FAF of runways in use);
  - `src === "synth"` segments dashed, with a legend reading "dashed = synthesized, not published";
  - data block line 2 shows altitude hundreds plus a trend arrow, and `HLD {lap}/{of}` while holding;
  - aircraft hidden before `appears_min`;
  - footer "Procedures: FAA CIFP 2611, not for navigation".
- If `geo_available` is false, or the run has `paths_unavailable`: keep the v2 centreline drawing, captioned "schematic, not the engine's path" (spec M1 exit criterion). For `paths_unavailable` the caption is "recorded on engine {v}; paths not available" and no blips are drawn.
- Strips gain `entry {fix} T+{m}` (before appearing), `HLD {fix} {laps}L {FL}`, and the route delta.

- [ ] **Step 1: Write the failing tests** (`track.test.ts`)

```ts
import { describe, expect, it } from "vitest";
import { positionAt, scanTimes } from "./track";
const pts = [{ t_min: 0, lat: 0, lon: 0 }, { t_min: 2, lat: 0, lon: 2 }, { t_min: 3, lat: 1, lon: 2 }];
describe("positionAt", () => {
  it("interpolates linearly within a segment", () => expect(positionAt(pts, 1)).toMatchObject({ lat: 0, lon: 1, seg: 0 }));
  it("is null outside the track", () => { expect(positionAt(pts, -0.1)).toBeNull(); expect(positionAt(pts, 3.1)).toBeNull(); });
  it("hits points exactly", () => expect(positionAt(pts, 3)).toMatchObject({ lat: 1, lon: 2 }));
});
it("scan steps are 4.8 s back from t", () => expect(scanTimes(10, 3, 4.8 / 60).map((x) => +x.toFixed(4))).toEqual([10, 9.92, 9.84]));
```

- [ ] **Step 2: Run** `cd frontend && npm install && npm test`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `track.ts`, then the Radar, Strips and client changes.
- [ ] **Step 4: Run** `cd frontend && npm test && npx tsc --noEmit && npm run build`. Expected: PASS. Then run `npm run dev` with the API (`uvicorn atm.server:app --port 8000`) and open the ORD replay. Check by eye that blips follow the STAR, synthesized segments are dashed, and a held flight shows a racetrack. Take a Playwright screenshot.
- [ ] **Step 5: Commit** `git commit -m "feat(ui): radar flies engine trajectories with holds, sparse fix labels and synth dashing"`

### Task 18: Re-choose and re-record replays

**Files:**
- Create: `scripts/pick_replays.py`
- Modify: `atm/wave/replay.py` (`record` writes `engine_version`), `data/replay/*.json`, `atm/server.py` (`_replay_summaries` unchanged; verify)

**Interfaces:**
- Produces: `pick_replays.rank(hub: str, n: int = 20) -> list[tuple[str, float, int]]`. It returns `(wave_id, fcfs_total_hold_min, max_laps)` for flights2015 rows at the hub, sorted by `max_laps` desc then hold desc. CLI: `python3 scripts/pick_replays.py ORD MIA ATL DFW DEN`. It prints the top 3 per hub.
- Rule (spec §6.2): at least 3 of 5 replays have ≥ 1 lap in the status-quo or agreed plan. Keep the current wave per hub when it already qualifies; otherwise swap in the top-ranked wave.

- [ ] **Step 1: Write the failing test** (append to `tests/test_geo_api.py`)

```python
def test_replays_are_current_engine_and_show_laps():
    import json, glob
    from atm.wave import build, trajectory
    from atm.wave.edit import plan_from_dicts
    files = sorted(glob.glob("data/replay/*.json")); assert len(files) == 5
    with_lap = 0
    for f in files:
        d = json.load(open(f)); assert d["engine_version"] == ENGINE_VERSION   # from atm.wave.version; stays valid after M2a/M2/M3 re-records
        w = build.build_wave(d["result"]["wave_id"])
        for key in ("final_plan",):
            ts = trajectory.build(w, plan_from_dicts(d["result"][key]["assignments"]))
            with_lap += any(t.laps >= 1 for t in ts.air if t.wave)
    assert with_lap >= 3
```

- [ ] **Step 2: Run** it. Expected: FAIL.
- [ ] **Step 3: Implement** `pick_replays.py`, run it, then record with `PYTHONPATH=. python3 -c "from atm.wave import replay; replay.record([...], mode='decision')"` (keys from `.env` loaded by the app's usual path; never print them). If no keys are available, use `mode='formulas'` and note it in the commit. If a hub has no qualifying wave, report the counts and keep the old one. If the ORD replay id changes, update the default `WAVE_ID` in `scripts/make_samples.py` (Tasks 20/22/28/33 run it without an argument).
- [ ] **Step 4: Run** `python3 -m pytest -q`. Expected: PASS. `PYTHONPATH=. python3 scripts/make_samples.py <new ORD id if changed>`.
- [ ] **Step 5: Commit** `git commit -m "data: replays re-chosen and re-recorded on engine v3-air (>= 3 of 5 show a holding lap)"`, then commit the samples separately with `contract: samples regenerated for v3-air`.

### Task 19: Benchmark: freeze v2, engine history, lap share; run v3-air

**Files:**
- Create: `eval/archive/engine-v2/benchmark.json`, `eval/archive/engine-v2/BENCHMARK.md` (copies plus `"engine_version": "v2"`)
- Modify: `atm/wave/benchmark.py` (`summarize`, `markdown`, new functions), `eval/benchmark.json`, `eval/BENCHMARK.md`, `tests/test_benchmark_small.py`

**Interfaces:**
- Produces:
  - `lap_share_pct(results: dict[str, dict]) -> float`: the % of agreed plans with ≥ 1 wave-flight lap.
  - `veto_counts(results) -> dict[str, int]`: per rule, the failed checks in agent proposals.
  - `engine_history(archive_dir: Path, current: dict) -> list[dict]`: rows `{engine_version, arm, delay_min, fuel_kg, lap_share_pct, taxi_wait_min, pax_protected_pct, overtime_min, consensus_rate_pct, rule_violations_in_agreed_plans, n_feasible}`, including `arm="baseline"`.
  - `attribution(prev: dict, cur: dict) -> dict`: `{"physics_delta": baseline_cur − baseline_prev, "negotiation_delta": {arm: arm_cur − baseline_cur}}` per metric.
  - `markdown(rep)` gains an "Engine history" section and an "Attribution" section, with headline claims taken from `negotiation_delta`.
  - `BenchmarkReport` gains `engine_version`, `lap_share_pct` (per arm and baseline) and `regressions: [{wave_id, arm, metric, v_prev, v_cur, cause}]`.

- [ ] **Step 1: Write the failing tests** (append; use `--fixtures` small runs as the existing tests do)

```python
def test_engine_history_and_attribution(tmp_path):
    from atm.wave import benchmark as B
    prev = {"engine_version": "v2", "baseline": {"delay_min": 10.0, "fuel_kg": 1.0}, "arms": [{"model": "formula", "summary": {"delay_min": 8.0, "fuel_kg": 1.0}}]}
    cur = {"engine_version": "v3-air", "baseline": {"delay_min": 12.0, "fuel_kg": 3.0}, "arms": [{"model": "formula", "summary": {"delay_min": 9.0, "fuel_kg": 2.0}}]}
    a = B.attribution(prev, cur)
    assert a["physics_delta"]["delay_min"] == 2.0 and a["negotiation_delta"]["formula"]["delay_min"] == -3.0

def test_report_has_engine_version_and_lap_share():
    rep = B.main(["--fixtures", "--n", "3", "--arms", "formula", "--no-ablation", "--out", str(tmp_path)])
    assert rep["engine_version"] == "v3-air" and "lap_share_pct" in rep["baseline"]
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_benchmark_small.py -q`. Expected: FAIL.
- [ ] **Step 3: Copy** the frozen v2 files first, then implement.
- [ ] **Step 4: Run** `python3 -m pytest -q`. Expected: PASS. Then run the full benchmark: `PYTHONPATH=. python3 -m atm.wave.benchmark --n 50 --seed 7 --arms typesafe/jev-1.14,liquid/d1-ultra,baseten/elephant-1,formula` (the arm string format follows `arm_parts`; check `--help`). Expected: `rule_violations_in_agreed_plans == 0` for every arm, and a v3-air row with attribution vs v2 in `eval/BENCHMARK.md`.
- [ ] **Step 5: Commit** `git commit -m "eval: freeze engine v2 results; engine history and attribution; v3-air benchmark row"`

### Task 20: M1 verification

- [ ] **Step 1:** `python3 -m pytest -q`. Expected: all pass, including `tests/test_perf.py::test_evaluate_p95_under_3ms_ord` (add it now: 200 `evaluate` calls on distinct ORD plans produced by `legal_moves` + `apply`, `statistics.quantiles(ms, n=20)[18] <= 3.0`).
- [ ] **Step 2:** `cd frontend && npm test && npx tsc --noEmit && npm run build`. Expected: exit 0.
- [ ] **Step 3:** `PYTHONPATH=. python3 scripts/make_samples.py`. Expected: `geo.json` and `trajectories.json` are written, and `git diff --stat` shows only sample files.
- [ ] **Step 4:** Docker build and run as in Task 8. Then run `curl -s -X POST localhost:7860/api/negotiate-wave -H 'content-type: application/json' -d '{"wave_id":"<ORD replay id>","mode":"decision","replay":true,"stream":false}' | python3 -c "import json,sys; r=json.load(sys.stdin); print(r['engine_version'], bool(r['trajectories']))"`. Expected: `v3-air True`.
- [ ] **Step 5:** Benchmark row v3-air is present (Task 19). Commit samples with the `contract:` prefix if they changed.

---

## Milestone M2a: Time base (`v3-time`)

### Task 21: BTS scheduled arrival is a gate time

**Files:**
- Modify:
  - `atm/wave/flights2015.py:_flight` (`eta_earliest`, `crew floor` line 320) and `build_from_row` (queue landing = `wheels_on`);
  - `atm/wave/build.py:_build_flight` (eta, crew line 462) and `_queue`;
  - `atm/wave/utility.py` (`delay_min = in_block − sched_min`; `TYPICAL_TAXI_MIN` removed from delay);
  - `atm/wave/version.py` (`"v3-time"`), `docs/api-contract.md` (Conventions: the time base sentence), `README.md`, `tests/test_build.py`, `tests/test_flights2015.py`.

**Interfaces:**
- Produces:
  - `utility.taxi_in_ref(wave: Wave, callsign: str) -> float`: the flight's real `taxi_in` if known, else the airport's 2015 median from `store.load_movements`/flights2015 (`airport_taxi_median(iata) -> float`, cached), else 6.0 (generic).
  - Semantics (spec §6.1):
    - `eta_earliest_min = sched_min + attributed delays − taxi_in_ref` (for a real row this equals `wheels_on − T0`);
    - queue `slot_min` = the real `wheels_on − T0` when known, else `sched_min − taxi_in_ref`;
    - `delay_min = max(0, in_block − sched_min)`;
    - crew floors use `eta_earliest + taxi_in_ref + turnaround`.
  - Provenance: the flights layer detail gains "scheduled times are gate times (BTS); landing = wheels-on".

- [ ] **Step 1: Write the failing tests**

```python
def test_eta_earliest_is_wheels_on_for_real_row():
    from atm.wave import flights2015 as F, build
    w = build.build_wave("F2015-270287")
    f = next(x for x in w.flights if x.callsign == w.focus)
    row = F._locate(270287)
    assert f.eta_earliest_min == pytest.approx(_wheels_on_minus_t0(row, w.window_start), abs=0.01)

def test_delay_is_in_block_minus_scheduled_gate_time():
    w = build.build_wave("F2015-270287"); p = fcfs_plan(w)
    m = metrics_map(w, p)[w.focus]
    assert m.delay_min == pytest.approx(max(0.0, m.in_block_min - w.flight(w.focus).sched_min), abs=0.05)

def test_version_v3_time():
    from atm.wave.version import ENGINE_VERSION
    assert ENGINE_VERSION == "v3-time"
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_build.py tests/test_flights2015.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** Fix any existing test that hard-codes the old `+6` with an inline comment citing spec §6.1, and list each such test in the commit body.
- [ ] **Step 4: Run** `python3 -m pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "contract: time base fix: BTS scheduled arrival is gate time; earliest landing = wheels-on (engine v3-time)"`

### Task 22: M2a verification and benchmark row

- [ ] **Step 1:** `python3 -m pytest -q`. Expected: PASS.
- [ ] **Step 2:** `cd frontend && npm test && npx tsc --noEmit && npm run build`. Expected: exit 0.
- [ ] **Step 3:** Re-record the 5 replays (Task 18 command; same wave ids), then `PYTHONPATH=. python3 scripts/make_samples.py`.
- [ ] **Step 4:** Run the full benchmark (Task 19 command). Expected: a `v3-time` row whose `physics_delta` vs `v3-air` is reported as a **definition change** in `BENCHMARK.md` (heading "v3-time: definition change, not physics"), and 0 rule violations in agreed plans.
- [ ] **Step 5:** Docker smoke test as in Task 8. Commit: `git commit -m "eval: v3-time benchmark row (definition change); replays re-recorded"`, then samples with the `contract:` prefix.

---

## Milestone M2: Surface (`v3-taxi`)

### Task 23: Runtime taxi graph, exit choice, ROT, unimpeded routes

**Files:**
- Create: `atm/wave/surface.py`, `tests/test_surface.py`

**Interfaces:**
- Consumes: `store.load_surface`, `gates_geo.bind_gates`.
- Produces:
  - `@dataclass TaxiGraph(icao: str, nodes: list[tuple[float, float]], adj: dict[int, list[tuple[int, float, str, str | None, str | None]]], exits: dict[str, list[dict]], stands: dict[str, dict], tier: str)`. Each `adj` entry is `(v, length_m, kind, name, crossing_strip)`.
  - `load_graph(icao: str) -> TaxiGraph | None` (LRU 4).
  - `choose_exit(g: TaxiGraph, runway_end: str, size: Literal["narrow", "wide"], wet: bool) -> dict`: the first exit with `dist_m ≥ 1500|2000 × (1.15 if wet)`, skipping exits with `angle_deg > 90` (backward acute), else the last exit.
  - `rot_s(exit: dict, final_gs_kt: float) -> float`: `2·d/(v_td + v_exit)` in m/s, with v_exit 25 kt if `angle_deg ≤ 45`, else 12 kt.
  - `@dataclass TaxiPath(nodes: list[int], seg_s: list[float], names: list[str], crossings: list[tuple[int, str]], unimpeded_min: float, src: Literal["osm", "synth"])`.
  - `unimpeded(g: TaxiGraph, exit_node: int, stand_node: int) -> TaxiPath`: Dijkstra on time. Edge time = length / (20 kt, or 10 kt if turn > 30° or taxilane), plus 30 s parking. Ties break by node id. Results are cached per (icao, exit_node) as a full tree.
  - `generic_taxi_min(runway: Runway, gate_lat: float, gate_lon: float) -> float`: straight line from the runway midpoint × 1.4 at 15 kt.

- [ ] **Step 1: Write the failing tests** (mini fixture graph built through `build_surface`, plus ORD)

```python
def test_exit_choice_narrow_dry_wet_wide(mini):
    assert choose_exit(mini, "09", "narrow", False)["dist_m"] == pytest.approx(1600, abs=50)
    assert choose_exit(mini, "09", "narrow", True)["dist_m"] == pytest.approx(2500, abs=50)   # 1725 m needed
    assert choose_exit(mini, "09", "wide", False)["dist_m"] == pytest.approx(2500, abs=50)

def test_rot_formula():
    assert rot_s({"dist_m": 1600, "angle_deg": 30}, 140) == pytest.approx(2 * 1600 / (140 * 0.514444 + 25 * 0.514444), rel=1e-6)

def test_unimpeded_time_on_mini_graph(mini):
    p = unimpeded(mini, mini.exits["09"][1]["node"], mini.stands["1"]["node"])
    assert p.src == "osm" and p.names[0] == "A2" and p.seg_s[-1] == pytest.approx(30.0)
    assert p.unimpeded_min == pytest.approx(sum(p.seg_s) / 60)

def test_ord_routes_exist_for_all_open_ends():
    w = build.build_wave("F2015-270287"); g = load_graph("KORD")
    for r in (x for x in w.airport.runways if x.open):
        e = choose_exit(g, r.id, "narrow", False)
        assert unimpeded(g, e["node"], next(iter(g.stands.values()))["node"]).unimpeded_min < 30
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_surface.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `surface.py`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(engine): taxi graph, exit choice, runway occupancy and unimpeded routes"`

### Task 24: Taxi in metrics, gate geo, real `ATC-TAXI-ROUTE`

**Files:**
- Modify:
  - `atm/wave/trajectory.py` (`TrajectorySet.ground` without waits);
  - `atm/wave/utility.py` (`in_block = slot + rot/60 + taxi`; ATC `taxi` term `(taxi − best unimpeded over open runways for its gate)/10`; remove `taxi_min` near/far except in the no-geometry tier);
  - `atm/wave/rules.py` (`ATC-TAXI-ROUTE`, `LIMITS` taxi row replaced);
  - `atm/wave/build.py`, `atm/wave/flights2015.py` (gates get `lat/lon/geo_src` via `bind_gates`; provenance `surface` layer);
  - `atm/wave/version.py` (`"v3-taxi"`), `tests/test_rules.py`, `tests/test_surface.py`.

**Interfaces:**
- Produces:
  - `@dataclass GroundTrack(callsign, kind: Literal["arrival", "departure"], wave: bool, exit: str | None, stand: str, route_names: list[str], points: list[dict], waits: list[dict], unimpeded_min: float, taxi_min: float, src: str)`.
  - `ATC-TAXI-ROUTE` evidence: `runway, exit, gate, stand, route, crossings, unimpeded_min, tier`. Source: `"OpenStreetMap surface (ODbL), routed"`, or `"generic surface (straight line x1.4 at 15 kt)"`, or `"ops rule (no geometry)"`. It fails iff the tier is `real` and no path exists.
  - Tiers: real graph → OSM route; graph exists but tier `generic`, or gate is `synth` → `generic_taxi_min`; no runway coordinates → v2 `taxi_min` (no-geometry tier, unchanged numbers).
  - New provenance layer: `Provenance("surface", "real"|"generic", "OpenStreetMap {osm_timestamp}")`.

- [ ] **Step 1: Write the failing tests**

```python
def test_in_block_is_slot_plus_rot_plus_taxi():
    w = build.build_wave("F2015-270287"); p = fcfs_plan(w)
    ts = trajectory.build(w, p); mm = metrics_map(w, p)
    for g in (x for x in ts.ground if x.wave):
        a = p.get(g.callsign)
        assert mm[a.callsign].in_block_min == pytest.approx(a.slot_min + mm[a.callsign].rot_s / 60 + g.taxi_min, abs=0.05)
        assert g.points[-1]["t_min"] == pytest.approx(mm[a.callsign].in_block_min, abs=1e-6)

def test_taxi_route_rule_cites_route():
    w = build.build_wave("F2015-270287"); c = _by(rules.check(w, fcfs_plan(w)), "ATC-TAXI-ROUTE", w.focus)
    assert c.passed and c.evidence["tier"] == "real" and c.evidence["route"] and c.source.startswith("OpenStreetMap")

def test_taxi_route_fails_when_stand_unreachable(monkeypatch):
    ...  # monkeypatch unimpeded to raise NoPath for the focus gate -> ATC-TAXI-ROUTE fails, detail names the gate

def test_gates_have_geo_on_replay_hub():
    w = build.build_wave("F2015-270287")
    assert all(g.lat is not None and g.geo_src for g in w.gates)
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_surface.py tests/test_rules.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the changes.
- [ ] **Step 4: Run** `python3 -m pytest -q`. Expected: PASS. Re-run `tests/test_perf.py`: p95 ≤ 3 ms.
- [ ] **Step 5: Commit** `git commit -m "contract: routed taxi-in drives in-block; real ATC-TAXI-ROUTE; gate coordinates (engine v3-taxi)"`

### Task 25: API: `/geo` surface part, ground tracks

**Files:**
- Modify: `atm/server.py` (geo `surface` filled from `load_surface` `draw`/`stands`/`holding_positions`), `scripts/make_samples.py`, `tests/test_geo_api.py`

**Interfaces:**
- Produces: `WaveGeo.surface` per spec §7 (`taxiways`, `runways`, `stands` with `gate`, `holding_positions`, `attribution`). `TrajectorySet.ground` is serialised.

- [ ] **Step 1: Write the failing test**

```python
def test_geo_surface_and_ground_tracks():
    g = c.get(f"/api/wave/{ORD_REPLAY}/geo").json()
    assert g["surface"]["attribution"] == "© OpenStreetMap contributors" and g["surface"]["stands"]
    w = c.get(f"/api/wave/{ORD_REPLAY}").json()
    ts = c.post("/api/trajectories", json={"wave_id": ORD_REPLAY, "assignments": w["status_quo"]["plan"]["assignments"]}).json()
    assert ts["ground"] and all(t["src"] in ("osm", "synth") for t in ts["ground"])
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_geo_api.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the changes.
- [ ] **Step 4: Run** `python3 -m pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "contract: WaveGeo.surface and TrajectorySet.ground"`

### Task 26: Surface map, shared clock, Gantt taxi and hold segments; remove AirportMap

**Files:**
- Create: `frontend/src/components/Surface.tsx`
- Delete: `frontend/src/components/AirportMap.tsx` (unused; nothing imports it, verified on 2 Nov 2026)
- Modify: `frontend/src/WaveScreen.tsx`, `frontend/src/components/Gantt.tsx`, `frontend/src/geo/track.ts` (add `projector`), `frontend/src/geo/track.test.ts`, `frontend/src/styles.css`

**Interfaces:**
- Produces:
  - `projector(lat0: number, lon0: number): (lat: number, lon: number) => [number, number]` (metres, flat; shared by Radar and Surface).
  - `<Surface geo={WaveGeo} traj={TrajectorySet | null} t={number} tone={(cs) => Tone} selected onSelect />`.
- Surface behaviour:
  - taxiways (taxilanes thinner), runways, stands and holding positions drawn to scale;
  - aircraft at `positionAt(ground.points, t)` at 1 s steps (`Math.floor(t * 60) / 60`);
  - attribution always visible;
  - a generic surface shows "surface not modelled for this airport".
- Shared clock: one `t` in `WaveScreen`. The panel auto-hands-off: radar until the focus flight's touchdown, surface after it, with a manual toggle; side by side at ≥ 1280 px.
- Gantt: hatched hold bar before the slot (one segment per lap); a thin taxi segment from touchdown to in-block before the gate bar.

- [ ] **Step 1: Write the failing test** (`track.test.ts`)

```ts
it("projector is metres east/north", () => {
  const p = projector(41.98, -87.9); const [x, y] = p(41.98 + 1 / 60, -87.9);
  expect(x).toBeCloseTo(0, 6); expect(y).toBeCloseTo(1852, 0);
});
```

- [ ] **Step 2: Run** `cd frontend && npm test`. Expected: FAIL.
- [ ] **Step 3: Implement** `projector`, `Surface.tsx`, the hand-off and the Gantt segments. Delete `AirportMap.tsx`, then run `grep -rn AirportMap frontend/src`. Expected: no output.
- [ ] **Step 4: Run** `npm test && npx tsc --noEmit && npm run build`. Expected: PASS. Manual check on the ORD replay: the focus aircraft taxis exit → stand on the map at the same `t` as the Gantt cursor. Take a Playwright screenshot.
- [ ] **Step 5: Commit** `git commit -m "feat(ui): airport surface map on the replay clock; Gantt hold laps and taxi segments; remove unused AirportMap"`

### Task 27: Taxi validation, replays, v3-taxi benchmark row

**Files:**
- Modify: `atm/wave/benchmark.py`, `eval/benchmark.json`, `eval/BENCHMARK.md`, `data/replay/*.json`, `tests/test_benchmark_small.py`

**Interfaces:**
- Produces: `taxi_validation(waves: list, results: dict[str, dict]) -> dict` with:
  - `{"n", "mae_min", "bias_min" (routed − real), "per_airport": {iata: {"n", "routed_median", "bts_median"}}, "worst": [{"wave_id", "callsign", "routed", "real", "cause"}] (5)}`;
  - focus flights only, FCFS plan, real surface tier only; no fitting;
  - `cause` is a heuristic string: `"BTS includes gate wait"` if real > routed + 10, `"long route / remote stand"` if routed > real + 5, else `"-"`.
- `BenchmarkReport.taxi_validation` and `coverage: {"procedures": {...}, "surface": {...}, "ground_traffic": {...}}` (tier counts over the 50 waves).

- [ ] **Step 1: Write the failing test**

```python
def test_taxi_validation_shape():
    v = B.taxi_validation(waves=[_w_ord()], results={"x": {"wave_id": "F2015-270287"}})
    assert set(v) == {"n", "mae_min", "bias_min", "per_airport", "worst"} and v["n"] == 1
```

- [ ] **Step 2: Run** it. Expected: FAIL.
- [ ] **Step 3: Implement** the changes, then re-record the replays (Task 18 command; the same 5 ids).
- [ ] **Step 4: Run** `python3 -m pytest -q`. Expected: PASS. Then run the full benchmark (Task 19 command). Expected: a v3-taxi row, a taxi validation table (expect `bias_min < 0`; the README states it, it is not corrected) and coverage tiers in `BENCHMARK.md`.
- [ ] **Step 5: Commit** `git commit -m "eval: v3-taxi row with taxi-in validation vs BTS 2015 and coverage tiers; replays re-recorded"`

### Task 28: M2 verification

- [ ] **Step 1:** `python3 -m pytest -q`. Expected: PASS (including perf ≤ 3 ms and RSS ≤ 60 MB).
- [ ] **Step 2:** `cd frontend && npm test && npx tsc --noEmit && npm run build`. Expected: exit 0.
- [ ] **Step 3:** `PYTHONPATH=. python3 scripts/make_samples.py`. Expected: `geo.json` now has `surface`; commit with the `contract:` prefix.
- [ ] **Step 4:** Docker smoke test as in Task 20 (expect `v3-taxi True`). Check the memory of the running container (`docker stats --no-stream`): it stays under 512 MB after opening all 5 replay waves' `/geo`.
- [ ] **Step 5:** Confirm that the v3-taxi row and taxi validation are present in `eval/BENCHMARK.md`.

---

## Milestone M3: Ground traffic (`v3-traffic`)

### Task 29: Hold-short scheduler and independent verifier

**Files:**
- Modify: `atm/wave/surface.py`, `tests/test_surface.py`

`Reservations` is defined here in `surface.py` as `@dataclass Reservations(node_busy: dict[int, list[tuple[float, float, str]]], edge_dir: dict[tuple[int, int], list[tuple[float, float, str]]], runway_events: dict[str, list[tuple[float, str, str]]])` (minutes; `runway_events` per strip label holds `(t, "landing"|"takeoff", callsign)`). Task 30 fills it from fixed traffic.

**Interfaces:**
- Produces:
  - `@dataclass Scheduled(points: list[dict], waits: list[dict], ok: bool, reason: str | None, exit_clear_min: float)`.
  - `schedule(g: TaxiGraph, path: TaxiPath, t0_min: float, res: Reservations, callsign: str, horizon_min: float = 30.0, headway_s: float = 30.0) -> Scheduled`. Single-path safe-interval search: at each node, wait at the previous node (or the OSM holding position before a runway) until
    (a) the node is free of reservations by ± headway,
    (b) the next edge has no opposite-direction occupancy overlapping the traversal,
    (c) for crossings, no landing on that strip in [t − 1, t + 1.5] min and no takeoff in [t − 1, t + 1] min.
    Waits carry a `reason`: `"hold short {strip} for {landing|takeoff} {cs}"` or `"give way to {cs}"`. `ok = False` when the cumulative wait exceeds `horizon_min`. The scheduler mutates `res` to add its own reservations.
  - `verify_separation(tracks: list[GroundTrack], headway_s: float = 30.0) -> list[str]`: a brute-force pairwise check over sampled 1 s positions and node passage times. It is independent of `schedule` and used only by tests.

- [ ] **Step 1: Write the failing tests** (mini graph)

```python
def test_head_on_resolved_by_wait(mini):
    res = Reservations({}, {}, {})
    a = schedule(mini, path_S1_to_exit(mini), 0.0, res, "DEP1")
    b = schedule(mini, path_exit_to_S1(mini), 0.2, res, "ARR1")
    assert a.ok and b.ok and b.waits and "give way to DEP1" in b.waits[0]["reason"]
    assert verify_separation([track(a, "DEP1"), track(b, "ARR1")]) == []

def test_crossing_waits_for_landing_gap(mini):
    res = Reservations({}, {}, {"18/36": [(1.0, "landing", "UA12")]})
    s = schedule(mini, path_across_1836(mini), 0.5, res, "AA1")
    assert s.ok and s.waits[0]["reason"] == "hold short 18/36 for landing UA12" and s.waits[0]["to_min"] >= 2.5

def test_crossing_blocked_beyond_horizon_fails(mini):
    res = Reservations({}, {}, {"18/36": [(t / 2, "landing", f"L{t}") for t in range(0, 80)]})
    s = schedule(mini, path_across_1836(mini), 0.0, res, "AA1")
    assert not s.ok and "30 min" in s.reason
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_surface.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `schedule` and `verify_separation`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(engine): deterministic hold-short taxi scheduler with independent separation verifier"`

### Task 30: Fixed ground traffic and reservations

**Files:**
- Create: `atm/wave/ground_traffic.py`, `tests/test_ground_traffic.py`
- Modify: `atm/wave/build.py`, `atm/wave/flights2015.py` (provenance `ground_traffic`)

**Interfaces:**
- Consumes: `store.load_movements`, `surface.load_graph`, `surface.unimpeded`, `surface.schedule`, `surface.Reservations`, `moves.fcfs_runways`.
- Produces:
  - `@dataclass FixedMover(callsign: str, kind: Literal["arrival", "departure"], runway: str, stand_node: int, t_start_min: float, path: TaxiPath)`. For arrivals `t_start` is the exit time; for departures it is the pushback time `WHEELS_OFF − TAXI_OUT` (T0-relative).
  - `fixed_traffic(wave: Wave) -> list[FixedMover]`: queue arrivals plus real departures in `[T0 − 30, T0 + 120]` min. The departure stand is seeded (`build._rng(wave.id, cs, "stand")`) among stands nearest the carrier's most-used pier (`simulated`). The departure runway is the arrival flow runway on the same strip side, else `fcfs_runways` majority.
  - Uses `surface.Reservations` and `surface.schedule` (Task 29): `Reservations(node_busy: dict[int, list[tuple[float, float, str]]], edge_dir: dict[tuple[int, int], list[tuple[float, float, str]]], runway_events: dict[str, list[tuple[float, str, str]]])`. Times are in minutes; `runway_events` entries are `(t, "landing"|"takeoff", callsign)` per strip.
  - `reservations(wave: Wave) -> Reservations`: fixed movers are scheduled FCFS by `t_start`, cached per `wave.id`.
  - Provenance: `ground_traffic` is `real` ("BTS 2015 movements") where movements exist, else `generic` ("challenge flights.csv subset, partial").

- [ ] **Step 1: Write the failing tests**

```python
def test_ord_has_real_departures():
    w = build.build_wave("F2015-270287")
    ft = fixed_traffic(w)
    assert any(m.kind == "departure" for m in ft) and any(p.layer == "ground_traffic" and p.status == "real" for p in w.provenance)

def test_reservations_deterministic_and_headway():
    w = build.build_wave("F2015-270287")
    r1, r2 = reservations(w), reservations(build.build_wave("F2015-270287"))
    assert r1 == r2
    for busy in r1.node_busy.values():
        s = sorted(busy)
        assert all(b[0] - a[0] >= 0.5 - 1e-9 for a, b in zip(s, s[1:]))   # 30 s

def test_partial_badge_without_movements():
    ...  # wave at an airport outside top 30 -> ground_traffic generic, detail contains "partial"
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_ground_traffic.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `ground_traffic.py`, scheduling fixed movers with `surface.schedule` (Task 29) in `t_start` order.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(engine): fixed ground traffic with real 2015 departures and reservation table"`

### Task 31: `ATC-TAXI-CONFLICT`, waits in metrics and conflicts in the TrajectorySet

**Files:**
- Modify:
  - `atm/wave/trajectory.py` (wave flights scheduled in exit-time order against a copy of `reservations(wave)` plus the plan's landings as runway events; `conflicts` filled);
  - `atm/wave/utility.py` (`taxi_wait_min`, `taxi_min = unimpeded + waits`);
  - `atm/wave/rules.py` (new rule, `LIMITS` rows for headway, crossing gap and horizon);
  - `atm/wave/moves.py` (labels add `+{wait} hold-short {strip}` when > 0);
  - `atm/wave/version.py` (`"v3-traffic"`), `docs/api-contract.md`, `frontend/src/api/types.ts` (`RuleId`), `tests/test_rules.py`.

**Interfaces:**
- Produces:
  - `RuleCheck("ATC-TAXI-CONFLICT", "ATC", ok, detail, "FAA JO 7110.65 §3-7-2 (ground control route and hold-short); headway 30 s, crossing gap, 30-min horizon (assumption)", evidence={"waits": n, "wait_min", "crossings", "exit_clear_min", "next_landing_min"})`.
  - It fails iff `Scheduled.ok` is false, or `exit_clear_min` > the next landing slot on that strip.
  - When no real surface exists, it passes with detail "ground conflicts not modelled ({tier})".
  - `TrajectorySet.conflicts` = failing checks mapped to `{rule, callsign, at_min, lat, lon, detail}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_taxi_wait_feeds_in_block():
    w, p = _ord_plan_with_crossing()      # helper: pick a wave flight whose route crosses a strip with a landing 0.5 min after its exit
    m = metrics_map(w, p)[p.assignments[0].callsign]
    assert m.taxi_wait_min > 0 and m.taxi_min == pytest.approx(m.taxi_unimpeded_min + m.taxi_wait_min, abs=1e-6)

def test_taxi_conflict_rule_passes_with_waits_and_cites_source():
    w, p = _ord_plan_with_crossing()
    c = _by(rules.check(w, p), "ATC-TAXI-CONFLICT", p.assignments[0].callsign)
    assert c.passed and c.evidence["waits"] >= 1 and c.source.startswith("FAA JO 7110.65 §3-7-2")

def test_taxi_conflict_fails_when_runway_cannot_be_vacated(monkeypatch):
    ...  # reserve the exit node continuously -> fail, detail names the next landing

def test_all_benchmark_fcfs_ground_tracks_separated():
    for wid in _bench_ids()[:10]:
        w = build.build_wave(wid); ts = trajectory.build(w, fcfs_plan(w))
        assert verify_separation(ts.ground) == [], wid
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_rules.py tests/test_surface.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the changes (14 rules now). Update the `/api/evaluate` docstring "same 14 rules".
- [ ] **Step 4: Run** `python3 -m pytest -q`. Expected: PASS, with perf ≤ 3 ms p95. If over budget, profile and memoise further. Never skip the rule.
- [ ] **Step 5: Commit** `git commit -m "contract: ATC-TAXI-CONFLICT; hold-short waits feed in-block; conflicts in TrajectorySet (engine v3-traffic)"`

### Task 32: Surface map waits, conflicts and crossings

**Files:**
- Modify: `frontend/src/components/Surface.tsx`, `frontend/src/geo/track.ts` (`activeWait(track, t)`), `frontend/src/geo/track.test.ts`, `frontend/src/styles.css`

**Interfaces:**
- Produces:
  - `activeWait(waits: {from_min: number; to_min: number; reason: string; lat: number; lon: number}[], t: number): (typeof waits)[number] | null`.
  - Surface draws an amber bar at an active wait (reason on hover), red markers at `conflicts` for the shown (rejected) proposal, a brief flash on a crossed runway while a crossing is in progress, and grey fixed traffic and departures.

- [ ] **Step 1: Write the failing test**

```ts
it("activeWait picks the wait covering t", () => {
  const w = [{ from_min: 1, to_min: 2, reason: "hold short 27L/09R for landing UA12", lat: 0, lon: 0 }];
  expect(activeWait(w, 1.5)?.reason).toContain("hold short"); expect(activeWait(w, 2.1)).toBeNull();
});
```

- [ ] **Step 2: Run** `npm test`. Expected: FAIL.
- [ ] **Step 3: Implement** the changes.
- [ ] **Step 4: Run** `npm test && npx tsc --noEmit && npm run build`. Expected: PASS. Manual check on a replay with a crossing: amber hold-short shows, then the crossing flash. Take a Playwright screenshot.
- [ ] **Step 5: Commit** `git commit -m "feat(ui): surface map shows hold-shorts, crossings and rule conflicts"`

### Task 33: M3 verification and v3-traffic benchmark row

- [ ] **Step 1:** `python3 -m pytest -q`. Expected: PASS.
- [ ] **Step 2:** `cd frontend && npm test && npx tsc --noEmit && npm run build`. Expected: exit 0.
- [ ] **Step 3:** Re-record the replays (Task 18), then `PYTHONPATH=. python3 scripts/make_samples.py`; commit with the `contract:` prefix.
- [ ] **Step 4:** Run the full benchmark. Expected: a v3-traffic row with `veto_counts` for `ATC-HOLD-STACK` and `ATC-TAXI-CONFLICT`, mean `taxi_wait_min` per arm, 0 rule violations in agreed plans, regressions listed with causes, and any new failures in `failures`.
- [ ] **Step 5:** Docker smoke test (expect `v3-traffic True`) plus a `docker stats` check under 512 MB.

---

## Milestone M4: Polish

### Task 34: Narration and MCP tool

**Files:**
- Modify: `atm/wave/narrate.py` (`_template`, `_compact`), `atm/mcp_server.py`, `tests/test_mcp.py`, `tests/test_graph.py`

**Interfaces:**
- Produces:
  - The template brief adds, for the focus flight, `"{laps} lap(s) at {fix} ({kg} kg)"` when laps > 0 and `"hold-short {strip} {min} min"` when taxi waits > 0. `_compact` passes `hold_laps, hold_fix, taxi_wait_min` to Claude.
  - MCP tool `get_trajectory(wave_id: str, assignments: list[dict]) -> dict`, which returns the `TrajectorySet` dict (read-only, all agents).

- [ ] **Step 1: Write the failing tests**

```python
def test_template_mentions_laps():
    r = _result_with_focus_metrics(hold_laps=2, hold_fix="BENKY", fuel_kg=428.0)
    assert "2 laps at BENKY" in narrate._template(r)["brief"]

def test_mcp_lists_get_trajectory():
    from atm import mcp_server
    assert "get_trajectory" in {t.name for t in mcp_server.list_tools_sync()}   # adapt to the registry accessor used in tests/test_mcp.py
```

- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement** the changes.
- [ ] **Step 4: Run** `python3 -m pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat: brief mentions holding laps and hold-shorts; MCP get_trajectory"`

### Task 35: Docs, story and final verification

**Files:**
- Modify: `README.md` (paths story, data sources with CIFP disclaimer and OSM attribution, cost, taxi validation bias statement), `eval/BENCHMARK.md` (narrative header), `docs/api-contract.md` (final v2.2 status line)
- Create: `docs/screens/` screenshots (radar holding, surface hold-short)

- [ ] **Step 1:** Write the README section. It must contain these exact strings, checked by `tests/test_api.py::test_readme_attributions` (add it): "Procedures: FAA CIFP 2611, not for navigation" and "© OpenStreetMap contributors".
- [ ] **Step 2:** `python3 -m pytest -q`. Expected: PASS.
- [ ] **Step 3:** `cd frontend && npm test && npx tsc --noEmit && npm run build`, then `PYTHONPATH=. python3 scripts/make_samples.py`. Expected: exit 0 and no sample diff.
- [ ] **Step 4:** Demo walkthrough of all 5 replays offline (no keys, `replay: true`). Each must show radar paths, at least 3 must show a holding lap, and the surface map must run on the same clock. Docker smoke test.
- [ ] **Step 5:** Commit `git commit -m "docs: v3 engine story, sources and attributions; screenshots"`

---

## Self-review (applied)

1. **Spec coverage** (spec section → tasks):
   - §5 pipeline: T2-T7. ICAO fix: T1. §6.1: T21.
   - §6.2: T11-T13. §6.3: T10, T12, T14. §6.4: T23-T24, T29 (scheduler), T30 (fixed traffic), T31. §6.5: T13, T24, T31. §6.6: T14, T24, T31. §6.7: T15. §6.8: T12 memo, T20 perf. §6.9: T11, T24, T29 tiers.
   - §7: T9, T16, T25, T31. §8: T16 guard, T18. §9: T17, T26, T32. §10: T19, T22, T27, T33. §12: tests per task.
   - AirportMap removal: T26.
2. **Step scan:**
   - Placeholders `...` remain only in test bodies whose setup is a named helper with the stated behaviour (T14, T16, T24, T29, T31). Each is described in prose, with its assertions written out.
   - No product function bodies are given.
3. **Type consistency:**
   - `metrics_map`, `trajectory.build`, `earliest`, `fcfs_runways`, `TaxiPath`, `Reservations` and `Scheduled` are used with the signatures of their producing tasks.
   - `GroundTrack` is defined in T24 and used in T29/T31. `Reservations` is defined in T29 and used in T30/T31.
   - `ENGINE_VERSION` values follow Global Constraints.
4. **Review Focus:** five lines, each pinned to a named test in its owning task.
5. **Proportion:** the plan is about the spec's length, and the code blocks are tests plus schemas.

## Changes and flags versus the design

- **CIFP column positions corrected:** time/distance is cols 75-78 (not 79-82), and the FAF/IF flag is col 43 (not 42). Verified on cycle 2611 lines (Task 2). Spec §4.1 should be amended to match.
- **Hold fix wording in spec §6.3 needs amending:** "last fix of the STAR common route" is wrong for KORD BENKY6. Its common route is BENKY (12,000 ft) → NEWRK → AHSTN → PETAH, so BENKY, the meter fix where the enroute transitions join, is the **first** common-route fix. The plan uses "first fix of the common route (else the last enroute-transition fix)" (Tasks 2 and 11).
- **ABE as the no-STAR test airport:** verified in CIFP 2611 (records A, F, G; no E).
- **No-geometry tier added** (unchanged v2 formulas) for waves whose runways lack coordinates, such as the synthetic test fixture. The design's "generic" tier needs a gate/runway position, so it cannot cover this case.
- **New engine version `v3-time`** for M2a, so its benchmark row is attributable.
- **Contract additions:**
  - `QueueArrival.origin` (additive), so fixed traffic can pick a STAR;
  - `paths_unavailable` on replayed results;
  - `geo_unavailable` 404 code.
- **vitest** added as a frontend devDependency (frontend had no test runner).
- **Test data directory** named `tests/data/`, because `tests/fixtures.py` already exists.
