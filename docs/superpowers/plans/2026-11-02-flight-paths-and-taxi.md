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

- Commits: one commit per task on branch `redesign/console` (user-approved). Every commit step stages the task's exact files with `git add <paths>` (never `git add -A` or `.`), then commits. Never push or merge to `main`. Never touch or print `.env`.
- Every commit message ends with these two trailer lines:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01CZQD1apQx15EEBy5x3LDJF`.
- Contract changes (`docs/api-contract.md`, `frontend/src/api/types.ts`, `frontend/src/api/samples/`) go in commits prefixed `contract:`. Contract v2.2 is additive only.
- All of `frontend/` is in scope: the user said "now you build both back and frontend". There is no approval gate on the frontend tasks (T17, T26, T32).
- No model decides legality. Every `RuleCheck` cites `rule`, `source` and `evidence`. Every new limit is a row in `rules.LIMITS` with `source: "workbook" | "real data" | "assumption"`.
- Every trajectory point and taxi segment carries `src: "cifp" | "osm" | "synth"`. Synthesized parts are drawn dashed. Never draw a path the engine did not use.
- Runtime never fetches CIFP/OSM/BTS. Raw downloads go to `.cache/` (gitignored and dockerignored); derived files go to `data/ref/` (and are seeded into `geo.*`, see the Datastore line above).
- Datastore (Postgres plan, design §15): derived files still go to `data/ref/` (seed and offline backend), but the runtime reads geometry through `backend.datastore` (`get_store().procedures(icao) / surface(icao) / movements_day(iata, date)` return gzipped bytes or `None`; PgStore in production, FileStore offline). `backend/geo/store.py` stays this plan's import surface: its loaders delegate to those methods and gunzip/parse. After generating files, run `python3 scripts/db_seed.py <dataset>` (datasets `procedures`, `surface`, `movements2015`, `coverage`; it needs `DATABASE_URL`, the direct owner URL; skip the step when it is unset). The runtime uses `DATABASE_URL_POOLED`. New tables come only via `migrations/`. `live_db` tests are opt-in: they need `DATABASE_TEST_URL` (a separate `atm_test` database) and `ATM_LIVE_DB=1`; the offline suite is `python3 -m pytest -q -m "not live_db"`.
- CIFP: pinned cycle **2611**, `https://aeronav.faa.gov/Upload_313-d/cifp/CIFP_261029.zip`. UI string: "Procedures: FAA CIFP 2611, not for navigation". The FAA disclaimer goes next to the derived files.
- OSM attribution "© OpenStreetMap contributors" is always visible on the surface map and in the README.
- Pinned physics: holding IAS 200 KIAS ≤ 6,000 ft, 230 KIAS 6,001-14,000 ft, 265 KIAS above; inbound leg 1.0 min ≤ 14,000 ft, 1.5 min above; right turns unless published; TAS = IAS × (1 + 0.02 × alt_ft/1000); turn rate is the least-bank option among 3°/s, 30° and 25° bank; g = 9.80665.
- Stack: 1,000 ft levels, 5 levels, bottom-first exit; only flights with `laps ≥ 1` enter the stack.
- Terminal speed profile: 280 KIAS above 10,000 ft, 250 below, 210 from the IAF, 170 at the FAF, 140 kt final (minus the METAR headwind on final only).
- Synthetic geometry: corner posts 40 NM at 045/135/225/315° true; downwind 5 NM abeam; join at the IF, or 2 NM before the FAF, at a 30° intercept; straight-in 10 NM at 3°; default hold base 6,000 ft AGL.
- Taxi: 20 kt straight; 10 kt for turns > 30° and on taxilanes; +30 s to park. Required roll: narrow 1,500 m, wide 2,000 m, ×1.15 wet. ROT = 2d/(v_td + v_exit) with v_exit 25 kt (exit angle ≤ 45°) or 12 kt.
- Ground conflicts: node headway 30 s; crossing gap: no landing on the strip in [t−60 s, t+90 s] and no takeoff in [t−60 s, t+60 s]; search horizon 30 min.
- Generic surface tier: straight line from the runway midpoint to the gate point × 1.4 at 15 kt.
- Gate→stand binding radius 150 m; OSM snap tolerance 15 m.
- Surface quality gate, decided per wave (T24): every open runway end that exists in OSM reaches ≥ 80 % of the wave's bound stands. Open ends that are absent from today's OSM (e.g. ORD 14L/32R/14R in 2015) use the generic straight-line taxi for flights on them (`src: "synth"`); this is an assumption row in `LIMITS`.
- Movements set: the union of the top 30 airports by 2015 arrivals and the 30 benchmark airports, if it fits the 50 MB cap. Otherwise the top 30 only, with the rest badged `partial`.
- Budgets:
  - `utility.evaluate` p95 ≤ 3 ms on the ORD replay wave;
  - ≤ 60 MB RSS per loaded airport;
  - largest agent prompt ≤ 8,000 tokens (estimate = chars/4);
  - move label ≤ 120 chars;
  - movements data ≤ 50 MB;
  - procedures data ≤ 3 MB total.
- Engine versions: `"v2"` (implicit, no field), `"v3-air"` (M1), `"v3-time"` (M2a), `"v3-taxi"` (M2), `"v3-traffic"` (M3). Tests compare versions to `backend.wave.version.ENGINE_VERSION`. The literal is pinned in exactly one test, `tests/test_version.py::test_version`, which each version-bumping task (T9, T21, T24, T31) updates.
- Caches (`routes_for_wave`, `trajectory.build`, `reservations`, per-wave precomputation) are keyed on a **content fingerprint** of the wave, `backend.wave.route.wave_key(wave) -> str` (defined in T11) (sha1 of `json.dumps(wave.to_dict(), sort_keys=True)`, memoised on the object), never on `wave.id` alone. Tests build variants of `make_wave()` that share an id.
- New path fields in `FlightMetrics` are stored unrounded. Tests that compare a path field to an existing rounded field (`in_block_min`, `hold_min`, `fuel_kg` are rounded to 0.1) use `abs=0.05`.
- Import order: `utility` imports `trajectory` lazily, inside `metrics_map`; `trajectory` imports `moves` lazily, inside functions. This avoids the `build`/`rules`/`moves` → `utility` import cycle.
- Benchmark: same 50 ids, seed 7, same 4 arms. Failing waves are listed in `failures`, never dropped. The command (README, verbatim):
  `python3 -m backend.wave.benchmark --source flights2015 --n 50 --arms openrouter:typesafe/jev-1.14,openrouter:liquid/d1-ultra,openrouter:baseten/elephant-1,formula --concurrency 4 --seed 7 --outage-n 20`

## Review Focus

1. **Wave whose runway ends have no coordinates** (the synthetic `make_wave` fixture, or an airport placed at (0, 0)). Expected: the engine runs in the *no-geometry tier*, returning v2 numbers (after T21, apart from the spec §6.1 delay definition), a `generic` provenance badge, and `geo_available: false`. Pinned in Task 13 (`test_no_geometry_tier_matches_v2`) and Task 16 (`test_geo_404_when_unavailable`). An airport **with** coordinates but no procedures file gets synthetic corner-post or straight-in routes, never the no-geometry tier. Pinned in Task 11 (`test_straight_in_when_no_procedures`).
2. **Runway closed by wind, or a runway that did not exist on the wave date** (ORD 09C/27C before 2020-11-05). Expected: no route is built for it, and `change_runway` never offers it. Pinned in Task 11 (`test_route_skips_runway_not_open_on_wave_date`).
3. **A replay recorded under an older engine version.** Expected: messages still stream, there are no paths, `/api/trajectories` returns 409 and the UI shows a banner. Pinned in Task 16 (`test_replay_engine_mismatch_has_no_paths`).
4. **Duty-manager drag to a slot earlier than the runway-dependent earliest, or to a different runway.** Expected: the `PLT-EARLIEST-ETA` failure cites `route_min`, and `/api/evaluate` suggestions respect the new earliest. Pinned in Task 14 (`test_earliest_eta_cites_route_for_far_runway`) and Task 16 (`test_evaluate_suggestion_respects_route_earliest`).
5. **Two wave flights converging on the same taxiway head-on, or a crossing needed while landings are continuous.** Expected: a deterministic wait, or an `ATC-TAXI-CONFLICT` failure after the 30-min horizon, never an exception or an infinite loop. Pinned in Task 29 (`test_head_on_resolved_by_wait`, `test_crossing_blocked_beyond_horizon_fails`).

---

## File Structure

**New, offline data (`backend/geo/` is offline-build logic plus runtime loaders; no engine logic)**
- `backend/wave/icao.py`: IATA→ICAO lookup from `data/challenge3/airports.dat`.
- `backend/geo/__init__.py`.
- `backend/geo/cifp.py`: fixed-column ARINC 424-18 record parsing and per-airport extraction.
- `backend/geo/osm_surface.py`: OSM JSON → simplified taxi graph dict (offline).
- `backend/geo/gates_geo.py`: gate id → stand coordinates binding.
- `backend/geo/store.py`: runtime loaders `load_procedures`, `load_surface`, `load_movements` (LRU, maxsize 4).
- `scripts/__init__.py` (new, empty, so tests can import `scripts.*`), `scripts/fetch_cifp.py`, `scripts/fetch_surface.py`, `scripts/geo_coverage.py`, `scripts/pick_replays.py`.
- Modified: `scripts/fetch_bts.py` (new `--movements2015` step) and `scripts/split_flights2015.py` (taxi columns).
- Data: `data/ref/procedures/{ICAO}.json.gz`, `data/ref/procedures/README.md` (FAA disclaimer), `data/ref/surface/{ICAO}.json.gz`, `data/ref/movements2015/{IATA}.csv.gz`, `data/ref/GEO_COVERAGE.md`.

**New engine**
- `backend/wave/version.py`: `ENGINE_VERSION`.
- `backend/wave/holding.py`: hold geometry, lap time, stack levels.
- `backend/wave/route.py`: STAR / approach / synthetic route per (flight, runway end).
- `backend/wave/trajectory.py`: `TrajectorySet` per plan, earliest landing, memoisation.
- `backend/wave/surface.py`: runtime taxi graph, exit choice, ROT, unimpeded routes, hold-short scheduler.
- `backend/wave/ground_traffic.py`: fixed ground traffic (queue arrivals and real departures), reservations.

**Modified engine**
- `backend/wave/model.py`: new `FlightMetrics` fields, `Gate` geo fields, `QueueArrival.origin`.
- `backend/wave/utility.py`: `metrics_map` replaces `metrics`.
- `backend/wave/rules.py`: 14 rules and new `LIMITS` rows.
- `backend/wave/moves.py`: `fcfs_runways`, runway-dependent `_min_slot`, the `hold` move, labels.
- `backend/wave/graph.py`: views and prompt.
- `backend/wave/build.py` and `backend/wave/flights2015.py`: ICAO, time base, gate geo, provenance.
- `backend/wave/replay.py`: `engine_version`.
- `backend/wave/benchmark.py`: engine history, lap share, taxi validation, coverage.
- `backend/wave/narrate.py`: brief mentions laps and hold-shorts.
- `backend/mcp_server.py`: `get_trajectory` tool.
- `backend/server.py`: `/api/wave/{id}/geo`, `POST /api/trajectories`, evaluate flag, result embedding, 409 guard.

**Frontend**
- `frontend/src/geo/track.ts` (new): `positionAt`, `projector` (pure; vitest).
- `frontend/src/geo/track.test.ts` (new).
- `frontend/src/components/Radar.tsx` (modify).
- `frontend/src/components/Surface.tsx` (new; replaces `AirportMap.tsx`, which is deleted).
- `frontend/src/components/Gantt.tsx`, `Strips.tsx`, `WaveScreen.tsx`, `frontend/src/api/client.ts`, `frontend/src/api/types.ts` (modify).
- `frontend/package.json` (add vitest and a `test` script).

**Tests (new)**
- `tests/test_icao.py`, `tests/test_cifp.py`, `tests/test_osm_surface.py`, `tests/test_gates_geo.py`, `tests/test_geo_store.py`.
- `tests/test_version.py`, `tests/test_holding.py`, `tests/test_route.py`, `tests/test_trajectory.py`, `tests/test_surface.py`, `tests/test_ground_traffic.py`, `tests/test_geo_api.py`, `tests/test_perf.py`, `tests/test_e2e_waves.py`.
- Test data: `tests/data/cifp/kord_2611.txt`, `tests/data/osm/mini_airport.json`, `tests/data/osm/mini_runways.json`, `tests/data/movements/mini.csv`. (`tests/data/` keeps test data apart from the `tests/fixtures.py` module. A `tests/fixtures/` directory would not actually break that import, because the regular module wins over a namespace directory, but separating them is clearer.)

---

## Milestone M0: Data and coverage

### Task 1: IATA→ICAO lookup

**Files:**
- Create: `backend/wave/icao.py`, `tests/test_icao.py`
- Modify: `backend/wave/flights2015.py:151-155` (`_icao` delegates); `backend/wave/build.py:516` (BTS waves: `icao=icao_for(iata)` instead of `"K" + iata`, keeping the prefix rule only on `KeyError`)

**Interfaces:**
- Produces: `icao_for(iata: str) -> str` (raises `KeyError` if unknown) and `iata_for(icao: str) -> str | None`. The source is OpenFlights `data/challenge3/airports.dat` (columns 5 = IATA, 6 = ICAO), loaded once via `functools.cache`.
  - Datastore: `icao_for` parses `get_store().rows("openflights")` (from `backend.datastore import get_store`), whose keys are `c1`..`c14` (so IATA = `c5`, ICAO = `c6`). FileStore reads `data/challenge3/airports.dat` as planned; Postgres serves the `ref.openflights` copy. `raw.ch3_airports_dat` is not used at runtime. No seed step: `openflights` is seeded by the Postgres plan's `small` dataset.

- [ ] **Step 1: Write the failing test**

```python
import os
import pytest
from backend.wave.icao import icao_for, iata_for

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

@pytest.mark.live_db   # opt-in: ATM_LIVE_DB=1 and DATABASE_TEST_URL (atm_test database), seeded with `ref.flights2015`
def test_db_flights2015_destinations_resolve_via_file_lookup(live_db_url):
    import psycopg
    with psycopg.connect(live_db_url) as c:
        dests = [r[0] for r in c.execute("select distinct dest from ref.flights2015")]
    assert len(dests) == 320
    assert all(len(icao_for(d)) == 4 for d in dests)

def test_flights2015_uses_lookup():
    from backend.wave import flights2015
    assert flights2015._icao("OGG") == "PHOG"

def test_bts_wave_uses_lookup(monkeypatch):
    from backend.wave import build, icao
    calls = []
    monkeypatch.setattr(icao, "icao_for", lambda i: calls.append(i) or "KORD")
    build.build_wave(build.list_waves(airport="ORD", limit=1)[0]["id"])
    assert calls == ["ORD"]
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_icao.py -q`. Expected: FAIL (`ModuleNotFoundError: backend.wave.icao`).
- [ ] **Step 3: Implement** `icao_for` / `iata_for` in `backend/wave/icao.py`. Make `flights2015._icao(iata)` return `icao_for(iata)`, falling back to the old prefix rule only on `KeyError`. In `build.build_wave`, call `icao.icao_for` through the module (`from backend.wave import icao`), so the monkeypatch in the test takes effect.
- [ ] **Step 4: Run** `python3 -m pytest tests/test_icao.py tests/test_flights2015.py tests/test_build.py -q -m "not live_db"`. Expected: PASS. With `ATM_LIVE_DB=1` and `DATABASE_TEST_URL` set, also run `python3 -m pytest tests/test_icao.py -q -m live_db`.
- [ ] **Step 5: Commit**

```bash
git add backend/wave/icao.py backend/wave/flights2015.py backend/wave/build.py tests/test_icao.py
git commit -m "fix: IATA to ICAO from OpenFlights (OGG was PHGG, SJU TJJU, FCA KFCA, KOA PHOA)"
```

### Task 2: CIFP record parser and per-airport extractor

**Files:**
- Create: `backend/geo/__init__.py`, `backend/geo/cifp.py`, `tests/test_cifp.py`, `tests/data/cifp/kord_2611.txt`

**Interfaces:**
- Produces:
  - `parse_lat(s: str) -> float`, `parse_lon(s: str) -> float`: ARINC `NddmmssSS` / `WdddmmssSS`.
  - `@dataclass Leg(seq: int, fix: str | None, desc: str, turn: str | None, pt: str, course_mag: float | None, dist_nm: float | None, time_min: float | None, alt_desc: str, alt1_ft: int | None, alt2_ft: int | None, vpa_deg: float | None, center: str | None, speed_kt: int | None, route_type: str, transition: str)`, from `parse_leg(line: str) -> Leg`. Columns (1-based, verified on cycle 2611):
    - ident 14-19, route type 20, transition 21-25, seq 27-29, fix 30-34, description 40-43 (col 43 `F` = FAF, `I` = IF; col 40-42 per ARINC), turn 44, path terminator 48-49;
    - course 71-74 (tenths, magnetic); distance or time 75-78 (`T010` = 1.0 min, otherwise tenths of NM);
    - altitude description 83, alt1 85-89, alt2 90-94 (`FL220` → 22000), speed limit 100-102 (knots, blank → None), vertical angle 103-106 (`-300` → 3.00);
    - RF center fix 107-111.
  - `extract_airport(lines: Iterable[str], icao: str, cycle: str) -> dict | None`, returning the procedures schema below (`None` if there is no PA record).
- Procedures schema (used by every later task):

```text
{"header": {"source": "FAA CIFP", "cycle": "2611", "script": "fetch_cifp.py/1", "fetched_at": ISO-8601 UTC, "tier": "star+iap"|"iap"|"none"},
 "airport": {"icao", "lat", "lon", "elev_ft", "magvar_deg"(east +)},
 "fixes": {ident: [lat, lon] | null},               # PC, EA, D, DB, PG used by kept legs; null (and a logged warning) when a referenced fix has no record
 "runways": {"10C": {"lat", "lon", "heading_mag"}},  # PG thresholds
 "stars": [{"id", "enroute": {trans: [Leg]}, "common": [Leg], "runway": {"27L"|"27B"|"ALL": [Leg]}}],
 "approaches": {"10C": {"id": "I10C", "kind": "ILS"|"RNAV"|"OTHER", "transitions": {iaf: [Leg]}, "final": [Leg]}},
 "holds": [{"fix", "inbound_mag", "turn", "leg_min"|null, "leg_nm"|null, "alt_ft", "kind": "HF"|"HM", "proc"}]}
```
  One approach per runway: prefer route type `I` (ILS), then `R`/`H` (RNAV), then other. Legs serialise via `dataclasses.asdict`.

- [ ] **Step 1: Build the fixture.** Copy verbatim from `.cache/cifp/FAACIFP18` (download by hand once with the Task 3 URL) into `tests/data/cifp/kord_2611.txt`:
  - the `KORD` PA record;
  - PG `RW10C`;
  - all `KORD` PE lines for `BENKY6` with route types 4-6 (RNAV enroute, common, runway transitions): transitions `BFORD`, blank (common: BENKY → NEWRK → AHSTN → PETAH) and `RW04B`;
  - all `KORD` PF lines for `I10C` with route type `I`;
  - the fix record (PC, EA, D or DB) for **every** fix referenced by those copied legs. This includes at least BFORD, BYLAW, BENKY, NEWRK, AHSTN, PETAH, TONIE, SHARN, RRUDA, RAYYY and CGT; collect them with `awk` over the copied legs' cols 30-34 and 35-36 (ICAO region) and 37 (section);
  - one RF leg line: the first line in the file with path terminator `RF` in cols 48-49, plus the fix records of its fix and centre fix. Note its airport, procedure and fix in a comment in the test.

  The file is public domain (CIFP disclaimer item 1).
- [ ] **Step 2: Write the failing tests**

```python
from pathlib import Path
import pytest
from backend.geo.cifp import parse_lat, parse_lon, parse_leg, extract_airport

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

def test_every_referenced_fix_has_coordinates():
    d = extract_airport(LINES, "KORD", "2611")
    assert all(v is not None for v in d["fixes"].values())

def test_rf_leg_has_center():
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
```

  `_field(dec, name)` looks up a field by its decoded name in `arinc424`'s JSON output. Inspect one decoded record once to find the exact key names, and add `import json` at the top. The `arinc424` calls (`Record().read(line)`, `.json()`) are unverified: confirm them against the package README on first run and adapt. The test skips when `arinc424` is not installed; install it locally with `pip install arinc424==0.3.0`, which is not in `requirements.txt`.

- [ ] **Step 3: Run** `python3 -m pytest tests/test_cifp.py -q`. Expected: FAIL (`ModuleNotFoundError: backend.geo`).
- [ ] **Step 4: Implement** `backend/geo/cifp.py` with the signatures and columns above. STAR route types: `1/4` enroute, `2/5` common, `3/6` runway transition. Approach route type `A` is a transition. `extract_airport` sets `header.fetched_at` from an optional `fetched_at: str | None = None` keyword argument, defaulting to the current UTC time. `header.tier` is `"star+iap"` if the airport has STARs and approaches, `"iap"` if it has approaches only, else `"none"`.
- [ ] **Step 5: Run** `python3 -m pytest tests/test_cifp.py -q`. Expected: PASS.
- [ ] **Step 6: Commit**

```bash
git add backend/geo/__init__.py backend/geo/cifp.py tests/test_cifp.py tests/data/cifp/kord_2611.txt
git commit -m "feat(geo): ARINC 424-18 CIFP parser and per-airport extractor"
```

### Task 3: CIFP fetch script, derived procedures and loader

**Files:**
- Create: `scripts/fetch_cifp.py`, `backend/geo/store.py`, `data/ref/procedures/README.md`, `data/ref/procedures/*.json.gz` (generated), `tests/test_geo_store.py`

**Interfaces:**
- Consumes: `extract_airport` (Task 2), `icao_for` (Task 1).
- Produces:
  - `store.load_procedures(icao: str) -> dict | None`, cached with `lru_cache(maxsize=4)`.
  - `store.PROCEDURES_DIR = ROOT / "data/ref/procedures"`.
  - `fetch_cifp.py` steps:
    - download to `.cache/cifp/CIFP_261029.zip`;
    - verify the SHA-256 constant `CIFP_SHA256` (recorded on the first run and pasted into the script) and the size 9,113,631 bytes;
    - extract one file per ICAO of: the 320 flights2015 destinations, plus `backend.wave.hubs.HUBS`.
  - Datastore: `load_procedures` delegates to `get_store().procedures(icao)` (`from backend.datastore import get_store`), which returns the gzipped JSON document or `None`; the loader gunzips and parses it. `scripts/db_seed.py procedures` loads `data/ref/procedures/*.json.gz` into `geo.procedures` (doc blob) and `geo.fixes` (points), swapped atomically. The seeder reads only `header.cycle`, `header.tier` and `fixes` (a map of ident to `[lat, lon]`) from the document: keep the output of `fetch_cifp.py` in sync with `_procedure_row` and `_fix_rows` in `scripts/db_seed.py`, or update them.

- [ ] **Step 1: Write the failing tests**

```python
from backend.geo import store

def test_kord_procedures_present():
    d = store.load_procedures("KORD")
    assert d["header"]["cycle"] == "2611"
    assert any(s["id"] == "BENKY6" for s in d["stars"]) and "10C" in d["approaches"]

def test_absent_airports():
    assert store.load_procedures("KPBI") is None and store.load_procedures("KISN") is None

def test_coverage_counts():
    import os
    from backend.wave.icao import icao_for
    dests = [f[:-7] for f in os.listdir("data/challenge2/flights")]
    have = [store.load_procedures(icao_for(d)) for d in dests]
    assert sum(bool(h and h["stars"]) for h in have) == 111
    assert sum(bool(h and h["approaches"]) for h in have) == 315

def test_procedures_size_cap():
    from pathlib import Path
    assert sum(p.stat().st_size for p in Path("data/ref/procedures").glob("*.json.gz")) <= 3_000_000

import pytest

@pytest.mark.live_db   # opt-in: ATM_LIVE_DB=1 and DATABASE_TEST_URL, after `db_seed.py procedures` into atm_test
def test_geo_procedures_match_files_by_sha256(live_db_url):
    import hashlib, psycopg
    from pathlib import Path
    with psycopg.connect(live_db_url) as c:
        db = dict(c.execute("select icao, sha256 from geo.procedures").fetchall())
    files = {p.name.split(".")[0]: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in Path("data/ref/procedures").glob("*.json.gz")}
    assert db == files
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_geo_store.py -q -m "not live_db"`. Expected: FAIL.
- [ ] **Step 3: Implement** `store.load_procedures` and `scripts/fetch_cifp.py`. Write `README.md` with the cycle, the URL, the SHA-256 and the FAA disclaimer items 6 and 7a-c verbatim, plus "not for navigation".
- [ ] **Step 4: Run** `python3 scripts/fetch_cifp.py`, then `python3 -m pytest tests/test_geo_store.py -q -m "not live_db"`. Expected: PASS. If the 111/315 counts differ, stop: the coverage numbers in spec §4.1 are wrong, so report it rather than editing the test.
- [ ] **Step 4b: Seed** `python3 scripts/db_seed.py procedures` (skip when `DATABASE_URL` is unset), then, if `ATM_LIVE_DB=1` and `DATABASE_TEST_URL` are set, run it against `atm_test` (`--url-env DATABASE_TEST_URL`) and `python3 -m pytest tests/test_geo_store.py -q -m live_db`. Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add scripts/fetch_cifp.py backend/geo/store.py data/ref/procedures tests/test_geo_store.py
git commit -m "data: FAA CIFP 2611 procedures per airport (STARs, approaches, coded holds)"
```

### Task 4: OSM surface graph builder

**Files:**
- Create: `backend/geo/osm_surface.py`, `tests/test_osm_surface.py`, `tests/data/osm/mini_airport.json`, `tests/data/osm/mini_runways.json` (the runway rows for the fixture, shared by T4, T5 and T23)

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
 "quality": {"reach_pct": {"10C": 0.93}}}                # per runway end present in OSM: share of ALL stands reachable
```
  - Nodes at way intersections, turn points > 10° and stand/holding positions; degree-2 straight chains are merged.
  - Taxiway endpoints within 15 m of a runway centreline are snapped onto it.
  - An exit is a taxiway edge leaving a runway node.
  - `crossing_strip` is the strip label (`"09R/27L"`) for runway edges that the router may cross.
  - `quality` stores only per-end `reach_pct`, and only for ends present in OSM. The real/generic **tier is decided per wave in T24**: open ends present in OSM against the wave's bound stands, ≥ 80 %. Ends that are in `runways` but have no OSM runway (e.g. ORD 14L/32R/14R, closed after 2015) are simply absent from `exits` and `reach_pct`.

- [ ] **Step 1: Write `tests/data/osm/mini_airport.json`.** It is an Overpass-style `elements` list:
  - one runway way `09/27`, 3,000 m east-west;
  - a parallel taxiway `A` 200 m north, joined to the runway by exits `A1` (90°, 500 m from the 09 threshold), `A2` (30°, 1,600 m) and `A3` (90°, 2,500 m);
  - a crossing taxiway `B` crossing a second runway `18/36`;
  - two stands `S1` (ref `1`) and `S2` (ref `2`) on taxilane `T`;
  - one `holding_position` before `18/36`;
  - the runway-side end of exit `A3` stops 10 m short of the runway centreline (to test snapping; this must stay the third exit, not a fourth).

  Also write `tests/data/osm/mini_runways.json`: the `runways.csv`-style rows `{runway, le_lat, le_lon, he_lat, he_lon}` for `09`/`27` and `18`/`36` matching the fixture's runway ways.
- [ ] **Step 2: Write the failing tests**

```python
import json
from backend.geo.osm_surface import build_surface

OSM = json.load(open("tests/data/osm/mini_airport.json"))
RWYS = json.load(open("tests/data/osm/mini_runways.json"))

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

def test_reach_drops_when_stands_unreachable():
    broken = json.loads(json.dumps(OSM)); broken["elements"] = [e for e in broken["elements"] if e.get("tags", {}).get("ref") != "T"]
    assert build_surface(broken, "KXXX", RWYS, (0.0, 0.0), "t")["quality"]["reach_pct"]["09"] == 0.0

def test_no_tier_stored_offline():
    assert "tier" not in _s()["quality"]
```

- [ ] **Step 3: Run** `python3 -m pytest tests/test_osm_surface.py -q`. Expected: FAIL.
- [ ] **Step 4: Implement** `build_surface`: a local flat projection around `ref_latlon` (metres); reachability by BFS.
- [ ] **Step 5: Run** the same tests. Expected: PASS.
- [ ] **Step 6: Commit**

```bash
git add backend/geo/osm_surface.py tests/test_osm_surface.py tests/data/osm/mini_airport.json tests/data/osm/mini_runways.json
git commit -m "feat(geo): OSM taxiway graph builder with exits, crossings and per-end reachability"
```

### Task 5: Surface fetch, gate→stand binding, derived surface files

**Files:**
- Create: `backend/geo/gates_geo.py`, `scripts/fetch_surface.py`, `tests/test_gates_geo.py`, `data/ref/surface/*.json.gz` (generated)
- Modify: `backend/geo/store.py`

**Interfaces:**
- Consumes: `build_surface` (Task 4), `icao_for`.
- Produces:
  - `store.load_surface(icao: str) -> dict | None`. It delegates to `get_store().surface(icao)` (`from backend.datastore import get_store`; gzipped JSON document or `None`) and gunzips/parses.
  - `scripts/db_seed.py surface` loads `data/ref/surface/*.json.gz` into `geo.surface` (doc blob, `reach_pct`), `geo.stands`, `geo.osm_gates` and `geo.holding_positions` (PostGIS points), swapped atomically. The seeder reads only these keys of the surface document: `header.osm_timestamp`, `quality.reach_pct`, `stands` (`id`, `ref`, `node`, `lat`, `lon`), `gates` (`ref`, `lat`, `lon`), `holding_positions` (`node`, `strip`) and `nodes` (`[lat, lon]` by index). `build_surface` (Task 4) and `fetch_surface.py` must keep those output keys in sync with `_surface_row`, `_stand_rows`, `_gate_rows` and `_hold_rows` in `scripts/db_seed.py`, or update the builders.
  - `gates_geo.bind_gates(gate_ids: list[tuple[str, bool]], surface: dict) -> dict[str, dict]`. The input is `(gate_id, remote)` pairs. The output maps `gate_id → {"lat", "lon", "stand": str, "node": int, "geo_src": "osm-ref"|"osm-near"|"osm-gate"|"osm-generic"|"synth"}`. Order (spec §5 steps 1-4, then the generic fallbacks):
    1. a stand whose `ref` equals the id (`osm-ref`);
    2. normalised (`B12` ↔ `12`, case-insensitive, leading letters stripped, within the stands nearest gates whose ref starts with the same letters) (`osm-ref`);
    3. the stand nearest the OSM gate node with that ref, within 150 m (`osm-near`);
    4. the OSM gate node itself, snapped to the nearest taxilane node (`osm-gate`; `stand` = the gate ref, `node` = the snapped node). `"osm-gate"` is a new `Gate.geo_src` value, added to the contract in T9;
    5. for generic ids (`A1..`, `W1`, `W2`), stands nearest the first `terminals` centroid, in id order (`osm-generic`);
    6. `RS` → the stand farthest from the terminal that is still reachable (`osm-generic`);
    7. otherwise `synth` (the airport reference point).
  - `fetch_surface.py`:
    - Overpass query by `icao`, then `iata`, then a 6 km bbox;
    - `[out:json][timeout:90]`, `out geom`;
    - 5 s sleep between airports, 2 retries;
    - raw files in `.cache/overpass/{ICAO}.json`;
    - covers the same airport set as Task 3;
    - User-Agent `disruption-negotiator/3 (+repo URL)`.

- [ ] **Step 1: Write the failing tests**

```python
from backend.geo import store
from backend.geo.gates_geo import bind_gates

def test_bind_exact_ref_and_generic_on_fixture():
    import json
    from backend.geo.osm_surface import build_surface
    s = build_surface(json.load(open("tests/data/osm/mini_airport.json")), "KXXX",
                      json.load(open("tests/data/osm/mini_runways.json")), (0.0, 0.0), "t")
    b = bind_gates([("1", False), ("A1", False), ("RS", True)], s)
    assert b["1"]["geo_src"] == "osm-ref" and b["A1"]["geo_src"] == "osm-generic" and b["RS"]["stand"] != b["A1"]["stand"]

def test_replay_hubs_have_real_surface():
    for icao in ("KORD", "KATL", "KDFW", "KDEN", "KMIA"):
        s = store.load_surface(icao)
        assert s["quality"]["reach_pct"] and min(s["quality"]["reach_pct"].values()) >= 0.8, icao
        assert len(s["stands"]) >= 200 and s["header"]["attribution"] == "© OpenStreetMap contributors"

def test_gate_node_fallback_when_no_stand_matches():
    import json
    from backend.geo.osm_surface import build_surface
    osm = json.load(open("tests/data/osm/mini_airport.json"))
    osm["elements"].append({"type": "node", "id": 990001, "lat": 0.0021, "lon": 0.0090,
                            "tags": {"aeroway": "gate", "ref": "G9"}})   # > 150 m from both stands
    s = build_surface(osm, "KXXX", json.load(open("tests/data/osm/mini_runways.json")), (0.0, 0.0), "t")
    assert bind_gates([("G9", False)], s)["G9"]["geo_src"] == "osm-gate"

def test_ord_replay_gates_bind_to_osm():
    from backend.wave import build
    w = build.build_wave("F2015-270287")
    b = bind_gates([(g.id, g.remote) for g in w.gates], store.load_surface("KORD"))
    assert sum(v["geo_src"] in ("osm-ref", "osm-near") for v in b.values()) >= len(w.gates) - 3

import pytest

@pytest.mark.live_db   # opt-in: ATM_LIVE_DB=1 and DATABASE_TEST_URL, after `db_seed.py surface` into atm_test
def test_bindings_agree_with_nearest_stand_query(live_db_url):
    import psycopg
    from backend.wave import build
    w = build.build_wave("F2015-270287")
    b = bind_gates([(g.id, g.remote) for g in w.gates], store.load_surface("KORD"))
    with psycopg.connect(live_db_url) as c:
        for gid, v in list(b.items())[:20]:
            if v["geo_src"] not in ("osm-ref", "osm-near"):
                continue
            row = c.execute("select 1 from geo.stands where icao = 'KORD' and id = %s and "
                            "ST_DWithin(geog, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, 150)",
                            (v["stand"], v["lon"], v["lat"])).fetchone()
            assert row, gid
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_gates_geo.py -q -m "not live_db"`. Expected: FAIL.
- [ ] **Step 3: Implement** `bind_gates`, `load_surface` and `fetch_surface.py`. Then run `python3 scripts/fetch_surface.py` (long: about 320 × 5 s plus query time; run it in the background).
- [ ] **Step 4: Run** `python3 -m pytest tests/test_gates_geo.py -q -m "not live_db"`. Expected: PASS. Record the total `data/ref/surface` size in the commit message. If the total is > 15 MB, stop and report (spec §5 estimates 5-12 MB).
- [ ] **Step 4b: Seed** `python3 scripts/db_seed.py surface` (skip when `DATABASE_URL` is unset), then, if `ATM_LIVE_DB=1` and `DATABASE_TEST_URL` are set, seed `atm_test` (`--url-env DATABASE_TEST_URL`) and run `python3 -m pytest tests/test_gates_geo.py -q -m live_db`. Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add backend/geo/gates_geo.py backend/geo/store.py scripts/fetch_surface.py tests/test_gates_geo.py data/ref/surface
git commit -m "data: OSM surface graphs per airport (ODbL) and gate to stand binding"
```

### Task 6: 2015 ground movements and taxi columns

**Files:**
- Modify: `scripts/fetch_bts.py` (new `--movements2015` mode); `scripts/split_flights2015.py:14-22` (add `WHEELS_ON, TAXI_IN, WHEELS_OFF, TAXI_OUT` to the `SRC` rename map as `wheels_on, taxi_in, wheels_off, taxi_out`, and to `FILE_COLS`; convert `WHEELS_ON`/`WHEELS_OFF` from HHMM to local minutes since midnight, adding 1440 when the time wraps past midnight relative to the scheduled time); `backend/geo/store.py`
- Create: `tests/data/movements/mini.csv`, `data/ref/movements2015/{IATA}.csv.gz` (generated), regenerated `data/challenge2/flights/*.csv.gz`
- Test: `tests/test_geo_store.py` (append)

**Interfaces:**
- Produces:
  - In `backend/geo/store.py`: `MOVEMENTS_DIR = ROOT / "data/ref/movements2015"` and `@dataclass Movement(callsign: str, tail: str | None, kind: Literal["arrival", "departure"], date: str, sched_min_abs: int, wheels_min_abs: int | None, taxi_min: float | None, other_airport: str)`.
  - `store.load_movements(iata: str, date: str, from_abs: int, to_abs: int) -> list[Movement] | None`. `None` means no file for the airport; minutes are local minutes since midnight.
  - The movements CSV columns are `date,kind,carrier,flight_no,tail,other,sched,wheels,taxi,cancelled`.
  - `store.TOP30: tuple[str, ...]` is a constant in `backend/geo/store.py`: the top 30 IATA codes by 2015 arrivals. `fetch_bts.py --movements2015` computes it once from the downloaded months and prints the tuple to paste in. It is also listed in `GEO_COVERAGE.md`.
  - `store.MOVEMENT_AIRPORTS: tuple[str, ...]` = sorted union of `TOP30` and the 30 benchmark airports (the `dest` of the `eval/benchmark.json` `wave_ids`). Movements are written for all of them if the total fits the 50 MB cap; otherwise for `TOP30` only, and `MOVEMENT_AIRPORTS = TOP30` (the rest are badged partial, spec §6.9).
  - flights2015 rows gain `wheels_on, taxi_in, wheels_off, taxi_out` (floats, NaN when missing).
  - Datastore: `load_movements(iata, date, from_abs, to_abs)` keeps its signature but does one `get_store().movements_day(iata, date)` fetch (`from backend.datastore import get_store`; a gzipped CSV for that airport-day or `None`), gunzips, parses and filters to the window. When the window crosses midnight (`to_abs > 1440`), it also reads the next day's `movements_day` blob and concatenates it before filtering. `scripts/db_seed.py movements2015` repacks `data/ref/movements2015/{IATA}.csv.gz` per airport-day into `geo.movements2015_day`; it finds the day through the first of `backend.datastore.pack.MOVEMENT_DATE_COLS` present in the CSV, so the `date` column here must stay among those candidates. `scripts/db_seed.py flights2015` carries the new taxi columns into `ref.flights2015` (they exist from the Postgres plan's D2 and are null until then).

- [ ] **Step 1: Write the failing tests**

```python
def test_movements_window_from_fixture(tmp_path, monkeypatch):
    import shutil, gzip
    from backend.geo import store
    p = tmp_path / "XXX.csv.gz"
    with open("tests/data/movements/mini.csv", "rb") as f, gzip.open(p, "wb") as g:
        shutil.copyfileobj(f, g)
    monkeypatch.setattr(store, "MOVEMENTS_DIR", tmp_path); store.load_movements.cache_clear()
    ms = store.load_movements("XXX", "2015-08-19", 8 * 60, 9 * 60)
    assert {m.kind for m in ms} == {"arrival", "departure"} and all(8 * 60 <= m.sched_min_abs < 9 * 60 for m in ms)
    assert store.load_movements("NOPE", "2015-08-19", 0, 1440) is None

def test_flights2015_rows_have_taxi_in():
    from backend.wave import flights2015 as F
    df = F._dest_frame("ORD")
    assert {"wheels_on", "taxi_in", "wheels_off", "taxi_out"} <= set(df.columns) and df["taxi_in"].notna().mean() > 0.99

def test_top30_and_movement_airports():
    from backend.geo import store
    assert len(store.TOP30) == 30 and set(store.TOP30) <= set(store.MOVEMENT_AIRPORTS)
    from pathlib import Path
    assert {p.name[:3] for p in Path("data/ref/movements2015").glob("*.csv.gz")} == set(store.MOVEMENT_AIRPORTS)

def test_movements_size_cap():
    from pathlib import Path
    assert sum(p.stat().st_size for p in Path("data/ref/movements2015").glob("*.csv.gz")) <= 50_000_000

@pytest.mark.live_db   # opt-in: ATM_LIVE_DB=1 and DATABASE_TEST_URL, after `db_seed.py movements2015` into atm_test
def test_movements_day_blob_matches_file(live_db_url):
    import gzip, io
    import pandas as pd
    from backend.datastore.pg import PgStore
    st = PgStore(live_db_url); st.open()
    try:
        blob = st.movements_day("ORD", "2015-08-19")
    finally:
        st.close()
    assert blob is not None
    day = pd.read_csv(io.BytesIO(gzip.decompress(blob)), dtype=str)
    full = pd.read_csv("data/ref/movements2015/ORD.csv.gz", dtype=str)
    assert len(day) > 0 and len(day) == (full["date"] == "2015-08-19").sum()
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_geo_store.py -q -m "not live_db" -k "movements or taxi_in"`. Expected: FAIL.
- [ ] **Step 3: Implement** `load_movements`, the movements step (12 PREZIP months for 2015, cached in `.cache/bts/2015_M/`) and the split columns. Re-run `scripts/split_flights2015.py`. If the size cap is exceeded, apply the spec §5 fallback: keep only ±2 h around hours that contain a flights.csv row.
- [ ] **Step 4: Run** `python3 -m pytest tests/test_geo_store.py tests/test_flights2015.py -q -m "not live_db"`. Expected: PASS.
- [ ] **Step 4b: Seed** `python3 scripts/db_seed.py movements2015` and `python3 scripts/db_seed.py flights2015` (skip both when `DATABASE_URL` is unset), then, if `ATM_LIVE_DB=1` and `DATABASE_TEST_URL` are set, seed `atm_test` (`--url-env DATABASE_TEST_URL`) and run `python3 -m pytest tests/test_geo_store.py -q -m live_db`. Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add scripts/fetch_bts.py scripts/split_flights2015.py backend/geo/store.py data/ref/movements2015 data/challenge2/flights data/challenge2/flights_index.csv tests/data/movements/mini.csv tests/test_geo_store.py
git commit -m "data: BTS 2015 arrivals and departures (top-30 and benchmark airports); taxi columns in flights2015 split"
```

### Task 7: Coverage report and memory check

**Files:**
- Create: `scripts/__init__.py` (empty), `scripts/geo_coverage.py`, `data/ref/GEO_COVERAGE.md` (generated), `tests/test_perf.py`

**Interfaces:**
- Produces:
  - `geo_coverage.coverage() -> list[dict]`, one row per flights2015 destination with `iata, icao, procedures_tier ("star+iap"|"iap"|"none"), surface_tier ("real"|"generic"|"none"; airport-level approximation: "real" when every end in `reach_pct` is ≥ 0.8 over all stands; the per-wave tier is decided in T24), stands, movements (bool)`.
  - The markdown table is written to `data/ref/GEO_COVERAGE.md`, with totals and the top 30 list.
  - Datastore: `coverage()` is also what `scripts/db_seed.py coverage` loads into `geo.coverage` (the seeder imports `scripts/geo_coverage.py`, and prints "no files yet" until it exists). Its row keys `iata, icao, procedures_tier, surface_tier, stands, movements` must stay as listed, because `_load_coverage` in `scripts/db_seed.py` reads them.

- [ ] **Step 1: Write the failing tests**

```python
from backend.geo import store

def test_coverage_rows_and_totals():
    from scripts.geo_coverage import coverage
    rows = coverage()
    assert len(rows) == 320 and sum(r["procedures_tier"] == "star+iap" for r in rows) == 111

def test_ord_surface_rss_under_60mb():
    # fresh interpreter: ru_maxrss is a high-water mark, so an in-process delta can read 0
    import subprocess, sys
    code = ("import resource,sys;from backend.geo import store;"
            "b=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss;store.load_surface('KORD');store.load_procedures('KORD');"
            "a=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss;print((a-b)/(1048576 if sys.platform=='darwin' else 1024))")
    mb = float(subprocess.check_output([sys.executable, "-c", code], text=True))
    assert mb <= 60

import pytest

@pytest.mark.live_db   # opt-in: ATM_LIVE_DB=1 and DATABASE_TEST_URL; PgStore variant of the RSS check (needs surface and procedures seeded)
def test_ord_surface_rss_under_60mb_pgstore(live_db_url):
    import subprocess, sys
    code = ("import resource,sys;from backend.datastore.pg import PgStore;"
            "import os;st=PgStore(os.environ['DATABASE_TEST_URL']);st.open();"
            "b=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss;"
            "import gzip,json;[json.loads(gzip.decompress(f(i))) for f,i in ((st.surface,'KORD'),(st.procedures,'KORD'))];"
            "a=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss;print((a-b)/(1048576 if sys.platform=='darwin' else 1024))")
    mb = float(subprocess.check_output([sys.executable, "-c", code], text=True))
    assert mb <= 60
```

- [ ] **Step 2: Run** the tests with `-m "not live_db"`. Expected: FAIL.
- [ ] **Step 3: Implement** `coverage()` and the markdown writer. Run it to generate `GEO_COVERAGE.md`.
- [ ] **Step 4: Run** the tests with `-m "not live_db"`. Expected: PASS.
- [ ] **Step 4b: Seed** `python3 scripts/db_seed.py coverage` (skip when `DATABASE_URL` is unset), then, if `ATM_LIVE_DB=1` and `DATABASE_TEST_URL` are set, seed `atm_test` (`--url-env DATABASE_TEST_URL`) and run `python3 -m pytest tests/test_perf.py -q -m live_db`. Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add scripts/__init__.py scripts/geo_coverage.py data/ref/GEO_COVERAGE.md tests/test_perf.py
git commit -m "data: geo coverage report (procedures, surface, movements tiers)"
```

### Task 8: M0 verification

- [ ] **Step 1:** `python3 -m pytest -q -m "not live_db"`. Expected: all pass (all existing tests, 130 at plan time, plus the new ones).
- [ ] **Step 2:** `cd frontend && npx tsc --noEmit && npm run build`. Expected: exit 0.
- [ ] **Step 3:** `PYTHONPATH=. python3 scripts/make_samples.py`, then `git diff --stat frontend/src/api/samples`. Expected: changes only where the ICAO fix changed a METAR station (none for the 5 hub replays). Note any change in the commit.
- [ ] **Step 4:** `docker build -t dn-m0 .`, then `docker run --rm -e PORT=7860 -p 7860:7860 dn-m0` and `curl -s localhost:7860/api/config`. Expected: JSON with `"replay_available": true`. Record the image size delta vs before M0. Run it once with `DATABASE_URL_POOLED` set (add `-e DATABASE_URL_POOLED`, from the environment, never printed) and once without it; both must answer `/api/config` (PgStore and the file fallback).
- [ ] **Step 4b:** `python3 scripts/db_seed.py --check`. Expected: exit 0 (nothing stale; needs `DATABASE_URL`, skip when unset).
- [ ] **Step 4c:** `ATM_LIVE_DB=1 python3 -m pytest -q -m live_db`. Expected: all pass, including geo parity (needs `DATABASE_TEST_URL`, a separate `atm_test` database seeded with the geo datasets).
- [ ] **Step 4d:** Record `select pg_database_size(current_database())` against the size estimate in the Postgres design §7, in the commit message or the handoff note (needs `DATABASE_URL`).
- [ ] **Step 5:** No benchmark row (no runtime metric change). Only if the samples changed:

```bash
git add frontend/src/api/samples
git commit -m "contract: samples regenerated after ICAO fix (no schema change)"
```

---

## Milestone M1: Airborne (`v3-air`)

### Task 9: Engine version and contract v2.2 part 1 (model + types)

**Files:**
- Create: `backend/wave/version.py`, `tests/test_version.py`, `frontend/src/api/samples/v2.1/` (a frozen copy of today's `wave.json`, `result.json`, `benchmark.json`), `frontend/src/api/samples.typecheck.ts`
- Modify: `backend/wave/model.py` (`FlightMetrics`, `Gate`, `QueueArrival`, `Provenance` doc), `frontend/src/api/types.ts`, `frontend/tsconfig.json` (only if `resolveJsonModule` is missing), `docs/api-contract.md`, `tests/test_model.py`

**Interfaces:**
- Produces:
  - `ENGINE_VERSION = "v3-air"`.
  - `FlightMetrics` adds these fields, all defaulted so v2 constructors still work: `hold_laps: int = 0`, `hold_fix: str | None = None`, `hold_level_ft: int | None = None`, `lap_min: float | None = None`, `vector_min: float = 0.0`, `route_min: float | None = None`, `route_extra_min: float = 0.0`, `exit: str | None = None`, `rot_s: float | None = None`, `taxi_unimpeded_min: float | None = None`, `taxi_wait_min: float = 0.0`, `taxi_min: float | None = None`.
  - `Gate` adds `lat: float | None = None`, `lon: float | None = None`, `geo_src: str | None = None`; the TS union is `"osm-ref" | "osm-near" | "osm-gate" | "osm-generic" | "synth"`.
  - `QueueArrival` adds `origin: str | None = None`.
  - `WaveScenario.to_dict()` adds `engine_version` and `geo_available: bool`. `geo_available` is true iff every open runway end has `le/he` coordinates **and** the airport is not at (0, 0). Procedures are optional: an airport without a procedures file still gets synthetic routes (T11).
  - TS: the spec §7 types verbatim (`WaveGeo`, `TrajectorySet`, `RuleId` additions `"ATC-HOLD-STACK" | "ATC-TAXI-CONFLICT"` (both added here; T31 does not touch `types.ts`), `Move.kind` `"hold"`, `Provenance.layer` additions), `NegotiationResult.engine_version?` and `trajectories?`, `BenchmarkReport.engine_version?`, `coverage?` and `taxi_validation?`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_model.py`)

```python
def test_v22_fields_defaulted_and_serialised():
    from backend.wave.model import FlightMetrics, Gate
    from backend.wave.fixtures import make_wave
    m = FlightMetrics()
    assert m.hold_laps == 0 and m.taxi_min is None and "route_extra_min" in m.to_dict()
    assert Gate("A1", "narrow", "A").to_dict()["geo_src"] is None
    d = make_wave().to_dict()
    from backend.wave.version import ENGINE_VERSION
    assert d["engine_version"] == ENGINE_VERSION and d["geo_available"] is False
```

`tests/test_version.py` (the only test that pins the literal; T21, T24 and T31 update it):

```python
from backend.wave.version import ENGINE_VERSION

def test_version():
    assert ENGINE_VERSION == "v3-air"
```

`frontend/src/api/samples.typecheck.ts` checks that v2.1 JSON is still valid under the v2.2 types (spec §12). It is compiled by `npx tsc --noEmit`:

```ts
import type { WaveScenario, NegotiationResult, BenchmarkReport } from "./types";
import wave from "./samples/v2.1/wave.json";
import result from "./samples/v2.1/result.json";
import bench from "./samples/v2.1/benchmark.json";
export const _w: WaveScenario = wave as WaveScenario;
export const _r: NegotiationResult = result as NegotiationResult;
export const _b: BenchmarkReport = bench as BenchmarkReport;
```

Use direct assignment without `as` if the JSON's literal types allow it; keep `as` only where string unions widen to `string`, and say so in a comment.

- [ ] **Step 2: Run** `python3 -m pytest tests/test_model.py tests/test_version.py -q`. Expected: FAIL. Before changing `types.ts`, copy today's `frontend/src/api/samples/{wave,result,benchmark}.json` into `frontend/src/api/samples/v2.1/`.
- [ ] **Step 3: Implement** the fields and `geo_available` as defined above. Update `types.ts` and `docs/api-contract.md` (status line "v2.2 (2 Nov 2026): …", additive).
- [ ] **Step 4: Run** `python3 -m pytest -q && (cd frontend && npx tsc --noEmit)`. Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add backend/wave/version.py backend/wave/model.py frontend/src/api/types.ts frontend/src/api/samples/v2.1 frontend/src/api/samples.typecheck.ts frontend/tsconfig.json docs/api-contract.md tests/test_model.py tests/test_version.py
git commit -m "contract: v2.2 part 1 (FlightMetrics path fields, Gate geo, engine_version, WaveGeo/TrajectorySet types)"
```

### Task 10: Holding geometry

**Files:**
- Create: `backend/wave/holding.py`, `tests/test_holding.py`

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
from backend.wave import holding as H

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
- [ ] **Step 5: Commit**

```bash
git add backend/wave/holding.py tests/test_holding.py
git commit -m "feat(engine): AIM 5-3-8 holding geometry, lap time and stack levels"
```

### Task 11: Route builder (STAR, approach, synthetic fallbacks)

**Files:**
- Create: `backend/wave/route.py`, `tests/test_route.py`
- Modify: `backend/wave/flights2015.py` and `backend/wave/build.py` (set `QueueArrival.origin`)

**Interfaces:**
- Consumes: `store.load_procedures`, `holding.make_hold`, `Wave`, `build._airport_coords(iata)`.
- Produces:
  - `@dataclass RoutePoint(lat: float, lon: float, alt_ft: float, kind: str, fix: str | None, src: Literal["cifp", "synth"], alt_src: Literal["constraint", "interp"], t_min: float)`. Here `t_min` is the time from entry, and `kind` is in `entry|fix|vector|final|threshold`.
  - `@dataclass Route(callsign: str, runway: str, star: str | None, transition: str | None, approach: str | None, entry_fix: str, points: list[RoutePoint], t_route_min: float, hold: HoldSpec, hold_index: int, tier: Literal["star+iap", "iap", "straight-in"])`.
  - `select_star(proc: dict, origin_latlon: tuple[float, float] | None, runway: str) -> tuple[str, str] | None`. It returns `(star_id, transition)`, tie-breaking by lower id. With no origin it uses the first star (by id) that has a transition for the runway.
  - `build_route(wave: Wave, callsign: str, runway_end: str) -> Route | None`. It returns `None` (the no-geometry tier) **iff** the runway end has no `le/he` coordinates, or the airport is at (0, 0) (how `build_wave` marks unknown coordinates). With coordinates but no procedures file it builds the synthetic corner-post / straight-in route (spec §6.2.4), `tier = "straight-in"`.
  - `wave_key(wave: Wave) -> str`: sha1 of `json.dumps(wave.to_dict(), sort_keys=True)`, memoised on the object (`wave.__dict__["_key"]`). Every engine cache keys on it.
  - `routes_for_wave(wave: Wave) -> dict[tuple[str, str], Route]`, keyed by (callsign, runway end) for wave and queue flights over open runway ends. It is cached in a module dict keyed by `wave_key(wave)` (keeping at most 16 entries, oldest evicted). `routes_for_wave.cache_clear()` empties it, for tests.
- Rules pinned by the tests:
  - runway transitions `NNB` apply to every end with number NN;
  - procedures whose runway is not in `wave.airport.runways` are skipped (the date wind-back already happened in `build._runways`);
  - a STAR ending in FM/VM, or a STAR whose last fix is not an approach IAF/IF, gets synth vectors: downwind 5 NM abeam, base, then a 30° intercept to the IF, or to 2 NM before the FAF;
  - no STAR: corner post at 40 NM on the nearest of 045/135/225/315° to the origin bearing, then direct to the IAF;
  - no approach: a 10 NM 3° straight-in;
  - hold = **first** fix of the STAR common route (the meter fix where the enroute transitions join; KORD BENKY6 → BENKY at 12,000 ft), or the last enroute-transition fix if there is no common route. Use a coded HF/HM at that fix if one exists (inbound = course + magvar), else `make_hold(..., src="synth")`; base = its altitude constraint, else the corner-post/IAF altitude, else elevation + 6,000 ft;
  - magnetic → true: `true = mag + magvar_deg`;
  - a CIFP leg `speed_kt` caps the profile IAS from that fix onward until the next constraint (spec §5 "altitude and speed constraints").

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from backend.wave import build, route
from backend.wave.fixtures import make_wave

ORD = "F2015-270287"

def test_ord_route_follows_star_and_ends_at_threshold():
    w = build.build_wave(ORD)
    cs = w.flights[0].callsign
    r = route.build_route(w, cs, next(x.id for x in w.airport.runways if x.open))
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
    from backend.wave import flights2015 as F
    row = F.list_rows(airport="ABE", limit=1)[0]["row_id"]
    w = build.build_wave(f"F2015-{row}")
    r = route.build_route(w, w.flights[0].callsign, next(x.id for x in w.airport.runways if x.open))
    assert r.tier == "iap" and r.points[0].src == "synth" and r.hold.src in ("cifp", "synth")

def test_straight_in_when_no_procedures(monkeypatch):
    # spec §12 "straight-in only" (PBI/ISN cannot be built: no runways.csv rows), simulated on ABE
    from backend.geo import store
    from backend.wave import flights2015 as F
    monkeypatch.setattr(store, "load_procedures", lambda icao: None); route.routes_for_wave.cache_clear()
    row = F.list_rows(airport="ABE", limit=1)[0]["row_id"]
    w = build.build_wave(f"F2015-{row}")
    rw = next(x for x in w.airport.runways if x.open)
    r = route.build_route(w, w.flights[0].callsign, rw.id)
    assert r.tier == "straight-in" and all(p.src == "synth" for p in r.points)
    assert r.points[-1].kind == "threshold"
    d0 = _nm(r.points[0], rw)   # helper: great-circle NM from the point to the runway threshold
    assert 9.5 <= d0 <= 40.5    # straight-in from 10 NM, or the corner post at 40 NM before it

def test_cache_keyed_on_content_not_id():
    from dataclasses import replace
    w = build.build_wave(ORD)
    a = route.routes_for_wave(w)
    w2 = replace(w, airport=replace(w.airport, runways=[r for r in w.airport.runways if r.id != next(iter(a))[1]]))
    assert w2.id == w.id and route.routes_for_wave(w2) != a

def test_no_geometry_returns_none():
    w = make_wave()   # fixture runways have no coordinates
    assert route.build_route(w, "DL100", "26R") is None
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_route.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `route.py`, with the speed profile from Global Constraints and TAS via `holding.tas_kt`. RF legs become chords every 10°. Set `QueueArrival.origin` from the row's `origin` in both builders.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add backend/wave/route.py backend/wave/build.py backend/wave/flights2015.py tests/test_route.py
git commit -m "feat(engine): routes from CIFP STAR and approach, synthetic vectors, corner posts, straight-in"
```

### Task 12: Trajectory assembly per plan

**Files:**
- Create: `backend/wave/trajectory.py`, `tests/test_trajectory.py`
- Modify: `backend/wave/moves.py` (extract `fcfs_runways(wave) -> dict[str, str]` from `fcfs_plan`; `fcfs_plan` uses it)

**Interfaces:**
- Consumes: `routes_for_wave`, `wave_key`, `holding.stack_levels`, `holding.racetrack`.
- Produces:
  - `moves.fcfs_runways(wave: Wave) -> dict[str, str]`: the runway choice loop of v2 `fcfs_plan`, unchanged.
  - `trajectory.entry_time(wave: Wave, callsign: str) -> float | None`: `eta_earliest_min − T_route(nominal)`.
  - `trajectory.earliest(wave: Wave, callsign: str, runway: str, sr: float = 0.0, gd: float = 0.0) -> float`. It equals `eta_earliest_min + sr + gd` in the no-geometry tier and when `runway` is the nominal runway.
  - `@dataclass AirTrack(callsign, wave: bool, runway, appears_min, points: list[dict], hold: dict | None, route_min: float, route_extra_min: float, laps: int, vector_min: float, lap_min: float | None, hold_fix: str | None, hold_level_ft: int | None, src_tier: str)`.
  - `@dataclass TrajectorySet(plan_id: str, engine_version: str, air: list[AirTrack], ground: list = field(default_factory=list), conflicts: list = field(default_factory=list), stack_violations: dict[str, list[str]] = field(default_factory=dict))`, with `to_dict()` in contract shape.
  - `trajectory.build(wave: Wave, plan: JointPlan) -> TrajectorySet`, memoised on `(route.wave_key(wave), plan.id)` (module dict, at most 4096 entries; `build.cache_clear()` for tests). Never key on `wave.id` alone: tests build variants of `make_wave()` that share an id.
  - Imports: `trajectory` imports `moves` lazily, inside the functions that need `fcfs_runways` (moves → utility → trajectory would otherwise cycle).
  - Per flight, with `D = slot − earliest(...)`: `laps = floor(D / lap_min)` and `vector_min = D − laps·lap_min`. The hold points (racetrack, laps times) are inserted at `hold_index`; vector stretch is an extra downwind out-and-back (`kind "vector"`, `src "synth"`) before the final. Fixed traffic: `wave=False`, zero laps, timed to land at its `slot_min`.
  - `route_extra_min = T_route(r) − T_route(nominal)`.

- [ ] **Step 1: Write the failing tests**

```python
import math
import pytest
from backend.wave import build, trajectory
from backend.wave.moves import fcfs_plan, fcfs_runways
from backend.wave.fixtures import make_wave

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
    p = fcfs_plan(w); a = p.assignments[0]
    lap = trajectory.build(w, p).air[0].lap_min or 5.0
    slot = trajectory.earliest(w, a.callsign, a.runway) + 0.5 * lap
    t = next(x for x in trajectory.build(w, p.with_assignment(type(a)(**{**a.__dict__, "slot_min": slot}))).air
             if x.callsign == a.callsign)
    assert t.laps == 0 and t.vector_min > 0 and t.hold_level_ft is None

def test_points_within_150nm_and_replay_plans_invariant():
    import glob, json
    from backend.wave.edit import plan_from_dicts
    for f in sorted(glob.glob("data/replay/*.json")):
        d = json.load(open(f)); w = build.build_wave(d["result"]["wave_id"])
        for p in (fcfs_plan(w), plan_from_dicts(d["result"]["final_plan"]["assignments"])):
            for t in trajectory.build(w, p).air:
                assert all(_nm_from_arp(w, q) <= 150 for q in t.points)     # helper: great-circle NM to the airport reference point
                assert all(b["t_min"] >= a["t_min"] for a, b in zip(t.points, t.points[1:]))

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
- [ ] **Step 5: Commit**

```bash
git add backend/wave/trajectory.py backend/wave/moves.py tests/test_trajectory.py
git commit -m "feat(engine): per-plan 4D trajectories with holding laps, vectors and stack levels"
```

### Task 13: Joint metrics, runway-dependent earliest, route fuel

**Files:**
- Modify:
  - `backend/wave/utility.py` (remove `metrics`, add public `metrics_map`, fuel and route terms);
  - `backend/wave/moves.py:19,73-76,228,242` (`_min_slot(wave, a)` uses `trajectory.earliest`; per-flight metrics via `metrics_map`);
  - `backend/wave/graph.py:54,86,167,178,712`;
  - `backend/wave/rules.py:14,check` (default `mm` from `metrics_map`);
  - `backend/wave/baseline.py:8,20` (status quo diverts iff `hold_min + max(0, route_extra_min) > extra_min`, matching PLT-FUEL-RESERVE; uses `metrics_map(wave, plan)`);
  - `tests/test_rules.py:303-313` (`test_divert_delay_includes_existing_delay` is rewritten to call `metrics_map(w2, JointPlan([...divert assignment..., other FCFS assignments]))[f.callsign]`, keeping its assertion).
- Test: `tests/test_trajectory.py` (append), all existing tests.

**Interfaces:**
- Consumes: `trajectory.build`, `trajectory.earliest`.
- Produces:
  - `utility.metrics_map(wave: Wave, plan: JointPlan) -> dict[str, FlightMetrics]`, the only metrics entry point.
  - `hold_min = laps·lap_min + vector_min` (or v2 formula in the no-geometry tier).
  - `fuel_kg = (hold_min + max(route_extra_min, −hold_min)) × burn + SR × burn × 0.20`.
  - Path fields filled from the `AirTrack`, **stored unrounded** (`route_min`, `route_extra_min`, `vector_min`, `lap_min`, `rot_s`, `taxi_*`). Existing fields keep their 0.1 rounding.
  - `utility.metrics` no longer exists.
  - `utility` imports `trajectory` **lazily** inside `metrics_map`, the same pattern as `evaluate`'s lazy `rules` import. `build`, `flights2015`, `rules` and `moves` import `utility` at module level, so a top-level import would cycle.

- [ ] **Step 1: Write the failing tests**

```python
def test_metrics_removed_and_map_is_joint():
    import backend.wave.utility as U
    assert not hasattr(U, "metrics")
    from backend.wave import build
    from backend.wave.moves import fcfs_plan
    w = build.build_wave("F2015-270287")
    mm = U.metrics_map(w, fcfs_plan(w))
    assert all(m.route_min is not None for m in mm.values())

def _far_runway(w, cs) -> str:
    """Open runway end with the largest t_route_min, provided it exceeds the nominal runway's by > 1.0 min.
    Shared with tests/test_rules.py (T14) and tests/test_geo_api.py (T16)."""
    from backend.wave import route
    from backend.wave.moves import fcfs_runways
    rs = route.routes_for_wave(w)
    nominal = rs[(cs, fcfs_runways(w)[cs])].t_route_min
    best = max((r for (c, _), r in rs.items() if c == cs), key=lambda r: r.t_route_min)
    if best.t_route_min <= nominal + 1.0:
        pytest.skip("no runway end more than 1 min farther than nominal")
    return best.runway

def test_route_extra_costs_fuel_on_far_runway():
    from dataclasses import replace
    from backend.wave import build, route, trajectory
    from backend.wave.moves import fcfs_plan
    from backend.wave.utility import metrics_map, hold_burn
    w = build.build_wave("F2015-270287")
    p = fcfs_plan(w); a = p.assignments[0]
    rs = route.routes_for_wave(w)
    far = rs[(a.callsign, _far_runway(w, a.callsign))]
    extra = far.t_route_min - rs[(a.callsign, a.runway)].t_route_min
    m = metrics_map(w, p.with_assignment(replace(a, runway=far.runway,
                                                  slot_min=trajectory.earliest(w, a.callsign, far.runway))))[a.callsign]
    assert m.route_extra_min == pytest.approx(extra, abs=1e-6) and m.hold_min == pytest.approx(0, abs=1e-6)
    assert m.fuel_kg == pytest.approx(extra * hold_burn(w.flight(a.callsign)), abs=0.05)   # fuel_kg is rounded to 0.1

def test_no_geometry_tier_matches_v2():
    # frozen v2 numbers for the synthetic wave (captured before this task; paste the dict)
    from backend.wave.fixtures import make_wave
    from backend.wave.moves import fcfs_plan
    from backend.wave.utility import evaluate
    ev = evaluate(make_wave(), fcfs_plan(make_wave()))
    assert ev["totals"] == V2_FIXTURE_TOTALS
```

  (T21 later re-captures `V2_FIXTURE_TOTALS["delay_min"]` for the spec §6.1 delay definition. The rest of the dict stays frozen.)

  Before changing code, capture `V2_FIXTURE_TOTALS` by running `evaluate(make_wave(), fcfs_plan(make_wave()))["totals"]` on the current code, and paste it as a module constant.

- [ ] **Step 2: Run** `python3 -m pytest tests/test_trajectory.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the changes; update the call sites listed above.
- [ ] **Step 4: Run** `python3 -m pytest -q`. Expected: everything passes. Existing tests use the synthetic wave (no-geometry tier) and are unchanged, except `test_divert_delay_includes_existing_delay` (rewritten above).
- [ ] **Step 5: Commit**

```bash
git add backend/wave/utility.py backend/wave/moves.py backend/wave/graph.py backend/wave/rules.py backend/wave/baseline.py tests/test_trajectory.py tests/test_rules.py
git commit -m "feat(engine): joint metrics from trajectories; runway-dependent earliest landing and route fuel"
```

### Task 14: Rules for the airborne model (`ATC-HOLD-STACK`, changed PLT rules)

**Files:**
- Modify: `backend/wave/rules.py` (`LIMITS`, `check`, `_duty_check`), `tests/test_rules.py`

**Interfaces:**
- Consumes: `TrajectorySet.stack_violations`, `trajectory.earliest`, `FlightMetrics` path fields.
- Produces:
  - `RuleCheck("ATC-HOLD-STACK", "ATC", ...)` for every landing wave flight. Source: `"FAA JO 7110.65 §4-5-1 (1,000 ft) and §2-1-4 (first come, first served); 5 levels (assumption)"`. Evidence keys: `hold_fix, laps, level_ft, holders, levels`.
  - `PLT-EARLIEST-ETA` evidence adds `route_min, entry_fix, t_entry`, and its detail names the runway's route minutes.
  - `PLT-FUEL-RESERVE` checks `hold_min + max(0, route_extra_min) ≤ extra_min`; evidence adds `laps, lap_min, hold_fix, route_extra_min`.
  - In `PLT-CREW-DUTY`, "unavoidable" uses the earliest **in-block** minimised over open runways: `min over open r of (trajectory.earliest(f, r) + taxi(r, gate))`. In M1, taxi is still the v2 heuristic `utility.taxi_min(wave, r, gate)`; T24 replaces it with ROT + unimpeded routed taxi.
  - New `LIMITS` rows (exact `limit` names; `source` is one of `workbook | real data | assumption`):
    - `"holding pattern geometry"`: `source "real data"` (AIM 5-3-8 speeds, legs and turns; lap time derived);
    - `"holding stack"`: `"assumption"` (5 levels; 1,000 ft per JO 7110.65 §4-5-1);
    - `"terminal burn proxy"`: `"assumption"`;
    - `"terminal speed profile"`: `"assumption"`;
    - `"procedures cycle"`: `"real data"`, note "current procedures (CIFP 2611) flown over 2015 traffic".

- [ ] **Step 1: Write the failing tests** (append; real ORD wave)

```python
def test_hold_stack_fifo_violation_cited(two_holders_reversed):
    w, p = two_holders_reversed          # same hold fix; B enters after A but leaves first; both >= 1 lap
    c = _by(rules.check(w, p), "ATC-HOLD-STACK", p.assignments[1].callsign)
    assert not c.passed and c.source.startswith("FAA JO 7110.65 §4-5-1")
    assert {"hold_fix", "laps", "level_ft", "holders", "levels"} <= set(c.evidence)

def test_earliest_eta_cites_route_for_far_runway():
    w = build.build_wave("F2015-270287"); p = fcfs_plan(w); a = p.assignments[0]
    far = _far_runway(w, a.callsign)      # from tests.test_trajectory
    c = _by(rules.check(w, p.with_assignment(replace(a, runway=far))), "PLT-EARLIEST-ETA", a.callsign)
    assert not c.passed and "route_min" in c.evidence and far in c.detail

def test_fuel_reserve_counts_route_extra():
    from dataclasses import replace as _r
    w = build.build_wave("F2015-270287"); p = fcfs_plan(w); a = p.assignments[0]
    far = _far_runway(w, a.callsign)
    rs = route.routes_for_wave(w)
    extra = rs[(a.callsign, far)].t_route_min - rs[(a.callsign, a.runway)].t_route_min
    f = w.flight(a.callsign)
    w2 = _r(w, flights=[_r(f, fuel={**f.fuel, "extra_min": extra - 0.5}) if x is f else x for x in w.flights])
    q = p.with_assignment(replace(a, runway=far, slot_min=trajectory.earliest(w2, a.callsign, far)))
    c = _by(rules.check(w2, q), "PLT-FUEL-RESERVE", a.callsign)
    assert not c.passed and c.evidence["route_extra_min"] > 0

def test_limits_list_new_rows():
    rows = {l["limit"]: l for l in rules.LIMITS}
    assert rows["holding pattern geometry"]["source"] == "real data" and rows["holding stack"]["source"] == "assumption"
    assert rows["procedures cycle"]["source"] == "real data"
    assert all(l["source"] in ("workbook", "real data", "assumption") for l in rules.LIMITS)
```

  Imports: `from tests.test_trajectory import _far_runway` (defined in T13), plus `route`, `trajectory`, `replace`.
  `two_holders_reversed` is a pytest **yield fixture** in `tests/test_rules.py` taking `monkeypatch`. It:
  - clears the `route.routes_for_wave` and `trajectory.build` caches on entry, and again in teardown after `yield`, so patched routes never leak into later tests;
  - searches the ORD wave's routes for two flights sharing `route.hold.fix`; if there is none, it monkeypatches `route.select_star` to give two flights the same STAR;
  - yields `(w, plan)` where A (earlier entrant) gets slot `earliest + 2.2·lap` and B gets `earliest + 1.2·lap` (B leaves first), with B as `plan.assignments[1]`.

- [ ] **Step 2: Run** `python3 -m pytest tests/test_rules.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the changes. 13 rules after this task.
- [ ] **Step 4: Run** `python3 -m pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add backend/wave/rules.py tests/test_rules.py
git commit -m "feat(rules): ATC-HOLD-STACK; earliest ETA and fuel reserve use routes and laps"
```

### Task 15: `hold` move, labels and agent views

**Files:**
- Modify: `backend/wave/moves.py` (`AUTHORITY["ATC"]` += `"hold"`, `_candidates`, `_validate`, `apply`, labels), `backend/wave/graph.py:_view`, `tests/test_moves.py`, `tests/test_graph.py`

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
    hm = [m for m in moves._candidates(w, p, "ATC") if m.kind == "hold"]   # legal_moves caps at MAX_MOVES=12 and may rank holds out
    assert hm and all(1 <= m.params["laps"] <= 4 for m in hm)
    m = hm[0]; q = apply(w, p, m)
    t = next(x for x in trajectory.build(w, q).air if x.callsign == m.callsigns[0])
    assert t.laps == m.params["laps"] and m.label.startswith(f"Hold {m.callsigns[0]} ")

def test_labels_within_120_chars():
    w = build.build_wave("F2015-75"); p = fcfs_plan(w)
    for ag in negotiators(w):
        assert all(len(m.label) <= 120 for m in legal_moves(w, p, ag))

REPLAY_IDS = sorted(p.stem for p in Path("data/replay").glob("*.json"))

def test_largest_prompt_under_8k_tokens(monkeypatch):
    # over the replay waves, the biggest prompt any agent receives (chars/4)
    assert max(_prompt_chars(wid, monkeypatch) for wid in REPLAY_IDS) / 4 <= 8000
```

  `_prompt_chars(wid, monkeypatch)` runs `graph.negotiate_wave(build.build_wave(wid), "decision", chain=["formula"])`. Formulas mode sets `_Run.chain = []` and builds no prompt, so decision mode with the local formula chain is used: no network. It wraps `backend.decide.Decider.choice` (signature `choice(self, state, instructions, options, ...)`) with `monkeypatch.setattr` to record `len(json.dumps(state)) + len(instructions) + len(json.dumps(options))` before calling the original, and returns the max. Imports: `json`, `Path`, `build`, `graph`, `moves`.

- [ ] **Step 2: Run** `python3 -m pytest tests/test_moves.py tests/test_graph.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the changes.
- [ ] **Step 4: Run** `python3 -m pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add backend/wave/moves.py backend/wave/graph.py tests/test_moves.py tests/test_graph.py
git commit -m "feat: hold move; path-aware move labels and agent views"
```

### Task 16: API: `/geo` (procedures), `/api/trajectories`, result embedding, replay guard

**Files:**
- Modify: `backend/server.py`, `backend/wave/graph.py` (`negotiate_wave` result dict: add `engine_version` and `trajectories {final, baseline}` here, so `replay.record` writes them into the recorded files), `backend/wave/replay.py`, `backend/wave/edit.py`, `scripts/make_samples.py` (add `geo.json`, `trajectories.json`), `docs/api-contract.md`
- Create: `tests/test_geo_api.py`

**Interfaces:**
- Consumes: `trajectory.build`, `route.routes_for_wave`, `holding.racetrack`, `ENGINE_VERSION`.
- Produces:
  - `GET /api/wave/{id}/geo` → `WaveGeo` with `surface: null` in M1. It returns 404 `geo_unavailable` when `geo_available` is false, and sends `ETag = sha1(engine_version + wave_id)`.
  - `POST /api/trajectories` accepts either `{wave_id, assignments}` → `TrajectorySet`, or `{run_id}` → the stored run's final `TrajectorySet` (409 `engine_mismatch` when the run is `paths_unavailable`). Errors are 422/404 as for `/api/evaluate`. Document **both** request forms in `docs/api-contract.md` in this task's `contract:` commit.
  - `POST /api/evaluate` with `include_trajectories: true` adds `trajectories`.
  - `NegotiationResult` gains `engine_version` and `trajectories: {final, baseline}`.
  - Replay files gain `engine_version`. `replay.load` returns it, defaulting to `"v2"` when absent.
  - On replay with a mismatch, the result is streamed without `trajectories`, and the run is marked `paths_unavailable: true`. `POST /api/trajectories` with `{run_id}` for such a run returns `409 engine_mismatch`.
  - Datastore: `/geo` reads procedures through `store.load_procedures`, so it serves `get_store().procedures(icao)` (PgStore in production, FileStore offline) with no change to the response. The replay guard reads `engine_version` from the replay body that `get_store().replay(wave_id)` returns, defaulting to `"v2"`. `PgStore.replay(wave_id)` returns the newest row for the wave, preferring the current `ENGINE_VERSION` (`order by (engine_version = current) desc, recorded_at desc limit 1`), so a wave recorded only under `v2` still returns its `v2` body and the guard sees the mismatch (the same as FileStore, which keeps one file per wave). `replay_ids()` lists waves recorded under any version, so older recordings stay in the demo list and replay without paths.
  - Compression: **no app-wide `GZipMiddleware`**. Starlette 0.40 (pinned via FastAPI 0.115.2) would buffer the SSE streams of `/api/negotiate-wave` and `/mcp`. The `/geo` handler compresses its own body with `gzip.compress` when `Accept-Encoding` contains `gzip`, setting `Content-Encoding: gzip` and `Vary: Accept-Encoding`.

- [ ] **Step 1: Write the failing tests**

```python
import glob, json
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

import pytest

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
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_geo_api.py -q -m "not live_db"`. Expected: FAIL.
- [ ] **Step 3: Implement** the endpoints, embedding and guard. Update the contract doc's endpoint table.
- [ ] **Step 4: Run** `python3 -m pytest -q -m "not live_db"`. Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add backend/server.py backend/wave/graph.py backend/wave/replay.py backend/wave/edit.py scripts/make_samples.py docs/api-contract.md tests/test_geo_api.py
git commit -m "contract: v2.2 part 2 (GET /api/wave/{id}/geo, POST /api/trajectories, result trajectories, engine_version replay guard)"
```

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
- [ ] **Step 4: Run** `cd frontend && npm test && npx tsc --noEmit && npm run build`. Expected: PASS. Then run `npm run dev` with the API (`uvicorn backend.server:app --port 8000`) and open the ORD replay. Check by eye that blips follow the STAR, synthesized segments are dashed, and a held flight shows a racetrack. Take a Playwright screenshot.
- [ ] **Step 5: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/geo/track.ts frontend/src/geo/track.test.ts frontend/src/api/client.ts frontend/src/components/Radar.tsx frontend/src/components/Strips.tsx frontend/src/WaveScreen.tsx
git commit -m "feat(ui): radar flies engine trajectories with holds, sparse fix labels and synth dashing"
```

### Task 18: Re-choose and re-record replays

**Files:**
- Create: `scripts/pick_replays.py`
- Modify: `backend/wave/replay.py` (`record` writes `engine_version`), `data/replay/*.json`, `backend/server.py` (`_replay_summaries` unchanged; verify)

**Interfaces:**
- Produces: `pick_replays.rank(hub: str, n: int = 20) -> list[tuple[str, float, int]]`. It returns `(wave_id, fcfs_total_hold_min, max_laps)` for flights2015 rows at the hub, sorted by `max_laps` desc then hold desc. CLI: `python3 scripts/pick_replays.py ORD MIA ATL DFW DEN`. It prints the top 3 per hub.
- Datastore: `replay.record` writes the file **and** calls `get_store().put_replay(wave_id, body, engine_version=ENGINE_VERSION)`, so Postgres holds the re-recorded replay, keyed by `engine_version`. No skip logic: `get_store()` writes to Postgres only when `DATABASE_URL_POOLED` (or `ATM_STORE=pg`) is set, and otherwise to the files (FileStore). If a configured database does not take the write, `record` prints a `WARNING: ... written to files only` line (it checks `get_store().status().degraded` after the write). The 409 guard reads `engine_version` from the stored body.
- Rule (spec §6.2): at least 3 of 5 replays have ≥ 1 lap in the status-quo or agreed plan. Keep the current wave per hub when it already qualifies; otherwise swap in the top-ranked wave.

- [ ] **Step 1: Write the failing test** (append to `tests/test_geo_api.py`)

```python
def test_replays_are_current_engine_and_show_laps():
    import json, glob
    from backend.wave import build, trajectory
    from backend.wave.edit import plan_from_dicts
    from backend.wave.version import ENGINE_VERSION
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
    import json, glob
    from backend.datastore.pg import PgStore
    from backend.wave.version import ENGINE_VERSION
    st = PgStore(live_db_url); st.open()
    try:
        for f in sorted(glob.glob("data/replay/*.json")):
            wid = json.load(open(f))["result"]["wave_id"]
            assert (st.replay(wid) or {}).get("engine_version") == ENGINE_VERSION
    finally:
        st.close()
```

- [ ] **Step 2: Run** it with `-m "not live_db"`. Expected: FAIL.
- [ ] **Step 3: Implement** `pick_replays.py`, run it, then record with `PYTHONPATH=. python3 -c "from backend.wave import replay; replay.record([...], mode='decision')"` (keys from `.env` loaded by the app's usual path; never print them). If no keys are available, use `mode='formulas'` and note it in the commit. If a hub has no qualifying wave, report the counts and keep the old one. If the ORD replay id changes, update the default `WAVE_ID` in `scripts/make_samples.py` (Tasks 20/22/28/33 run it without an argument).
- [ ] **Step 4: Run** `python3 -m pytest -q -m "not live_db"`. Expected: PASS. `PYTHONPATH=. python3 scripts/make_samples.py <new ORD id if changed>`. Then `python3 scripts/db_seed.py replays` (skip when `DATABASE_URL` is unset) so Postgres matches the files, and, if `ATM_LIVE_DB=1` and `DATABASE_TEST_URL` are set, seed `atm_test` (`--url-env DATABASE_TEST_URL`) and run `python3 -m pytest tests/test_geo_api.py -q -m live_db`.
- [ ] **Step 5: Commit** (two commits)

```bash
git add scripts/pick_replays.py backend/wave/replay.py data/replay tests/test_geo_api.py scripts/make_samples.py
git commit -m "data: replays re-chosen and re-recorded on engine v3-air (>= 3 of 5 show a holding lap)"
git add frontend/src/api/samples
git commit -m "contract: samples regenerated for v3-air"
```

`git add data/replay` also stages the deletion of any replay file that was swapped out.

### Task 19: Benchmark: freeze v2, engine history, lap share; run v3-air

**Files:**
- Create: `eval/archive/engine-v2/benchmark.json`, `eval/archive/engine-v2/BENCHMARK.md` (copies plus `"engine_version": "v2"`)
- Modify: `backend/wave/benchmark.py` (`summarize`, `markdown`, new functions), `eval/benchmark.json`, `eval/BENCHMARK.md`, `tests/test_benchmark_small.py` (`SUMMARY_KEYS` at lines 3-5 is compared with set equality: add `lap_share_pct` and every other new per-arm summary key)

**Interfaces:**
- Produces:
  - `lap_share_pct(results: dict[str, dict]) -> float`: the % of agreed plans with ≥ 1 wave-flight lap.
  - `veto_counts(results) -> dict[str, int]`: per rule, the failed checks in agent proposals.
  - `engine_history(archive_dir: Path, current: dict) -> list[dict]`: rows `{engine_version, arm, delay_min, fuel_kg, lap_share_pct, taxi_wait_min, pax_protected_pct, overtime_min, consensus_rate_pct, rule_violations_in_agreed_plans, n_feasible}`, including `arm="baseline"`.
  - `attribution(prev: dict, cur: dict) -> dict`: `{"physics_delta": baseline_cur − baseline_prev, "negotiation_delta": {arm: arm_cur − baseline_cur}}` per metric.
  - `markdown(rep)` gains an "Engine history" section and an "Attribution" section, with headline claims taken from `negotiation_delta`.
  - `BenchmarkReport` gains `engine_version`, `lap_share_pct` (per arm and baseline) and `regressions: [{wave_id, arm, metric, v_prev, v_cur, cause}]`.
  - Datastore: `benchmark.py` still writes `eval/benchmark.json` as today and also calls `get_store().put_benchmark(report, markdown, label)`; "freeze v2" keeps the v2 row with `current=False` (`is_current=false` in `eval.benchmark_reports`). No skip logic: `get_store()` writes to Postgres only when `DATABASE_URL_POOLED` (or `ATM_STORE=pg`) is set, and otherwise to the files; `benchmark.main` prints a `WARNING: ... written to files only` line when a configured database did not take the write.
  - Prompt budget (spec §6.7): each arm's summary gains `max_prompt_chars`, the largest `state + instructions + options` length any `Decider.choice` call received in that arm's runs (0 for `formula`). The T22, T27 and T33 benchmark runs record it automatically; `BENCHMARK.md` shows it next to the 32,000-char (8k-token) budget.

- [ ] **Step 1: Write the failing tests** (append; use `--fixtures` small runs as the existing tests do)

```python
def test_engine_history_and_attribution(tmp_path):
    from backend.wave import benchmark as B
    prev = {"engine_version": "v2", "baseline": {"delay_min": 10.0, "fuel_kg": 1.0}, "arms": [{"model": "formula", "summary": {"delay_min": 8.0, "fuel_kg": 1.0}}]}
    cur = {"engine_version": "v3-air", "baseline": {"delay_min": 12.0, "fuel_kg": 3.0}, "arms": [{"model": "formula", "summary": {"delay_min": 9.0, "fuel_kg": 2.0}}]}
    a = B.attribution(prev, cur)
    assert a["physics_delta"]["delay_min"] == 2.0 and a["negotiation_delta"]["formula"]["delay_min"] == -3.0

def test_report_has_engine_version_and_lap_share(tmp_path):
    from backend.wave import benchmark as B
    from backend.wave.version import ENGINE_VERSION
    rep = B.main(["--fixtures", "--n", "3", "--arms", "formula", "--no-ablation", "--outage-n", "0",
                  "--concurrency", "2", "--out", str(tmp_path)])
    assert rep["engine_version"] == ENGINE_VERSION and "lap_share_pct" in rep["baseline"]

import pytest

@pytest.mark.live_db   # opt-in: ATM_LIVE_DB=1 and DATABASE_TEST_URL; the v2 row survives as non-current history
def test_frozen_v2_row_is_not_current(live_db_url):
    import psycopg
    with psycopg.connect(live_db_url) as c:
        cur = c.execute("select count(*) from eval.benchmark_reports where is_current").fetchone()[0]
    assert cur == 1
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_benchmark_small.py -q -m "not live_db"`. Expected: FAIL.
- [ ] **Step 3: Copy** the frozen v2 files first, then implement.
- [ ] **Step 4: Run** `python3 -m pytest -q -m "not live_db"`. Expected: PASS. Then run the full benchmark with the README command (Global Constraints): `PYTHONPATH=. python3 -m backend.wave.benchmark --source flights2015 --n 50 --arms openrouter:typesafe/jev-1.14,openrouter:liquid/d1-ultra,openrouter:baseten/elephant-1,formula --concurrency 4 --seed 7 --outage-n 20`. Expected: `rule_violations_in_agreed_plans == 0` for every arm, and a v3-air row with attribution vs v2 in `eval/BENCHMARK.md`. The run's own `put_benchmark` call writes the v3-air row to Postgres; afterwards `python3 scripts/db_seed.py benchmark` (skip when `DATABASE_URL` is unset) loads the frozen v2 archive row from the files, and, if `ATM_LIVE_DB=1` and `DATABASE_TEST_URL` are set, `python3 -m pytest tests/test_benchmark_small.py -q -m live_db` checks it. Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add backend/wave/benchmark.py eval/archive/engine-v2 eval/benchmark.json eval/BENCHMARK.md tests/test_benchmark_small.py
git commit -m "eval: freeze engine v2 results; engine history and attribution; v3-air benchmark row"
```

### Task 20: M1 verification

- [ ] **Step 1:** `python3 -m pytest -q`. Expected: all pass, including `tests/test_perf.py::test_evaluate_p95_under_3ms_ord` (add it now: 200 `evaluate` calls on distinct ORD plans produced by `legal_moves` + `apply`, `statistics.quantiles(ms, n=20)[18] <= 3.0`).
- [ ] **Step 1b:** Add `tests/test_e2e_waves.py` (spec §12 end-to-end and invariants), then run it:

```python
import json, math, time
import pytest
from backend.wave import build, graph, trajectory
from backend.wave.edit import plan_from_dicts
from backend.wave.moves import fcfs_plan
from backend.wave.utility import evaluate

WAVES = json.load(open("eval/benchmark.json"))["wave_ids"]          # the 50 benchmark waves
REPLAYS = sorted(p.stem for p in __import__("pathlib").Path("data/replay").glob("*.json"))

def _nm(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 3440.065 * math.asin(math.sqrt(a))

def _invariants(w, plan):
    slots = {a.callsign: a.slot_min for a in plan.assignments if a.action == "land"}
    for t in trajectory.build(w, plan).air:
        assert all(b["t_min"] >= a["t_min"] for a, b in zip(t.points, t.points[1:])), t.callsign
        assert isinstance(t.laps, int) and t.laps >= 0
        assert all(q["src"] in ("cifp", "synth") for q in t.points)
        assert all(_nm(q["lat"], q["lon"], w.airport.lat, w.airport.lon) <= 150 for q in t.points), t.callsign
        if t.wave and t.callsign in slots:
            assert t.points[-1]["t_min"] == pytest.approx(slots[t.callsign], abs=1e-6)

@pytest.mark.parametrize("wid", WAVES)
def test_benchmark_wave_builds_and_negotiates(wid):
    w = build.build_wave(wid)
    _invariants(w, fcfs_plan(w))
    r = graph.negotiate_wave(w, "formulas")
    if r["outcome"] == "agreed":
        assert r["evaluation"]["feasible"]                      # no failed hard rule in an agreed plan
    _invariants(w, plan_from_dicts(r["final_plan"]["assignments"]))

@pytest.mark.parametrize("wid", REPLAYS)
def test_replay_wave_negotiates_under_2s(wid):
    w = build.build_wave(wid); t0 = time.perf_counter()
    graph.negotiate_wave(w, "formulas")
    assert time.perf_counter() - t0 < 2.0
```

  `w.airport.lat/lon` is the airport reference point. Run `python3 -m pytest tests/test_e2e_waves.py -q`. Expected: PASS.
- [ ] **Step 2:** `cd frontend && npm test && npx tsc --noEmit && npm run build`. Expected: exit 0.
- [ ] **Step 3:** `PYTHONPATH=. python3 scripts/make_samples.py`. Expected: `geo.json` and `trajectories.json` are written, and `git diff --stat` shows only sample files.
- [ ] **Step 4:** Docker build and run as in Task 8. Then run `curl -s -X POST localhost:7860/api/negotiate-wave -H 'content-type: application/json' -d '{"wave_id":"<ORD replay id>","mode":"decision","replay":true,"stream":false}' | python3 -c "import json,sys; r=json.load(sys.stdin); print(r['engine_version'], bool(r['trajectories']))"`. Expected: `v3-air True`.
- [ ] **Step 5:** Benchmark row v3-air is present (Task 19). Commit the new tests, and the samples if they changed:

```bash
git add tests/test_perf.py tests/test_e2e_waves.py
git commit -m "test: M1 perf budget, 50-wave invariants and end-to-end formulas runs"
git add frontend/src/api/samples
git commit -m "contract: samples regenerated for v3-air (geo, trajectories)"
```

---

## Milestone M2a: Time base (`v3-time`)

### Task 21: BTS scheduled arrival is a gate time

**Files:**
- Modify:
  - `backend/wave/flights2015.py:_flight` (`eta_earliest`, `crew floor` line 320) and `build_from_row` (queue landing = `wheels_on`);
  - `backend/wave/build.py:_build_flight` (eta, crew line 462) and `_queue`;
  - `backend/wave/utility.py` (`delay_min = in_block − sched_min`; `TYPICAL_TAXI_MIN` removed from delay);
  - `backend/wave/version.py` (`"v3-time"`), `docs/api-contract.md` (Conventions: the time base sentence), `README.md`, `tests/test_build.py`, `tests/test_flights2015.py`, `tests/test_trajectory.py` (re-capture `V2_FIXTURE_TOTALS["delay_min"]` only, with a comment citing spec §6.1), `tests/test_version.py` (`"v3-time"`).

**Interfaces:**
- Produces:
  - `utility.taxi_in_ref(wave: Wave, callsign: str) -> float`: the flight's real `taxi_in` if known, else the airport's 2015 median from `store.load_movements`/flights2015 (`airport_taxi_median(iata) -> float`, cached), else 6.0 (generic).
  - Semantics (spec §6.1):
    - `eta_earliest_min = sched_min + attributed delays − taxi_in_ref` (for a real row this equals `wheels_on − T0`);
    - queue `slot_min` = the real `wheels_on − T0` when known, else `sched_min − taxi_in_ref`;
    - `delay_min = max(0, in_block − sched_min)`;
    - crew floors use `eta_earliest + taxi_in_ref + turnaround` (the same `taxi_in_ref` in `PLT-CREW-DUTY`'s "unavoidable"). T24 switches both to the minimum unimpeded routed taxi over open runways.
  - Provenance: the flights layer detail gains "scheduled times are gate times (BTS); landing = wheels-on".

- [ ] **Step 1: Write the failing tests**

```python
def test_eta_earliest_is_wheels_on_for_real_row():
    from backend.wave import flights2015 as F, build
    w = build.build_wave("F2015-270287")
    f = next(x for x in w.flights if x.callsign == w.focus)
    df = F._dest_frame(F._locate(270287)["dest"])          # _locate returns the 14-key index row only
    row = df[df["row_id"] == 270287].iloc[0]                # has wheels_on (local minutes, T6)
    t0 = int(w.window_start[11:13]) * 60 + int(w.window_start[14:16])   # window_start is "YYYY-MM-DDTHH:MM:SS" local
    assert f.eta_earliest_min == pytest.approx(row["wheels_on"] - t0, abs=0.01)

def test_delay_is_in_block_minus_scheduled_gate_time():
    w = build.build_wave("F2015-270287"); p = fcfs_plan(w)
    m = metrics_map(w, p)[w.focus]
    assert m.delay_min == pytest.approx(max(0.0, m.in_block_min - w.flight(w.focus).sched_min), abs=0.05)

```

  Update `tests/test_version.py::test_version` to `"v3-time"`.

- [ ] **Step 2: Run** `python3 -m pytest tests/test_build.py tests/test_flights2015.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** Fix any existing test that hard-codes the old `+6` with an inline comment citing spec §6.1, and list each such test in the commit body. Re-capture `V2_FIXTURE_TOTALS["delay_min"]` in `tests/test_trajectory.py` from the new code, with the comment `# §6.1: delay = in-block − scheduled gate time (v3-time)`; every other key stays as frozen in T13.
- [ ] **Step 4: Run** `python3 -m pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add backend/wave/flights2015.py backend/wave/build.py backend/wave/utility.py backend/wave/version.py docs/api-contract.md README.md tests/test_build.py tests/test_flights2015.py tests/test_trajectory.py tests/test_version.py
git commit -m "contract: time base fix: BTS scheduled arrival is gate time; earliest landing = wheels-on (engine v3-time)"
```

### Task 22: M2a verification and benchmark row

- [ ] **Step 1:** `python3 -m pytest -q`. Expected: PASS.
- [ ] **Step 2:** `cd frontend && npm test && npx tsc --noEmit && npm run build`. Expected: exit 0.
- [ ] **Step 3:** Re-record the 5 replays (Task 18 command; same wave ids), then `PYTHONPATH=. python3 scripts/make_samples.py`.
- [ ] **Step 4:** Run the full benchmark (Task 19 command). Expected: a `v3-time` row whose `physics_delta` vs `v3-air` is reported as a **definition change** in `BENCHMARK.md` (heading "v3-time: definition change, not physics"), and 0 rule violations in agreed plans.
- [ ] **Step 5:** Docker smoke test as in Task 8. Then commit:

```bash
git add data/replay eval/benchmark.json eval/BENCHMARK.md
git commit -m "eval: v3-time benchmark row (definition change); replays re-recorded"
git add frontend/src/api/samples
git commit -m "contract: samples regenerated for v3-time"
```

---

## Milestone M2: Surface (`v3-taxi`)

### Task 23: Runtime taxi graph, exit choice, ROT, unimpeded routes

**Files:**
- Create: `backend/wave/surface.py`, `tests/test_surface.py` (the `mini` fixture: `TaxiGraph` from `build_surface` over `tests/data/osm/mini_airport.json` and `tests/data/osm/mini_runways.json`)

**Interfaces:**
- Consumes: `store.load_surface`, `gates_geo.bind_gates`.
- Produces:
  - `@dataclass TaxiGraph(icao: str, nodes: list[tuple[float, float]], adj: dict[int, list[tuple[int, float, str, str | None, str | None]]], exits: dict[str, list[dict]], stands: dict[str, dict], reach_pct: dict[str, float])`. Each `adj` entry is `(v, length_m, kind, name, crossing_strip)`. `stands` is keyed by the stand's `ref` when set, else by its OSM `id` (as a string). `exits` holds only runway ends present in OSM. There is no tier field: the tier is decided per wave in T24.
  - `load_graph(icao: str) -> TaxiGraph | None` (LRU 4).
  - `choose_exit(g: TaxiGraph, runway_end: str, size: Literal["narrow", "wide"], wet: bool) -> dict`: the first exit with `dist_m ≥ 1500|2000 × (1.15 if wet)`, skipping exits with `angle_deg > 90` (backward acute), else the last exit.
  - `rot_s(exit: dict, final_gs_kt: float) -> float`: `2·d/(v_td + v_exit)` in m/s, with v_exit 25 kt if `angle_deg ≤ 45`, else 12 kt.
  - `class NoPath(Exception)`: raised by `unimpeded` when the stand is unreachable from the exit node.
  - `@dataclass TaxiPath(nodes: list[int], seg_s: list[float], names: list[str], crossings: list[tuple[int, str]], unimpeded_min: float, src: Literal["osm", "synth"])`.
  - `unimpeded(g: TaxiGraph, exit_node: int, stand_node: int) -> TaxiPath`: Dijkstra on time. Edge time = length / (20 kt, or 10 kt if turn > 30° or taxilane), plus 30 s parking. Ties break by node id. Results are cached per (icao, exit_node) as a full tree. Raises `NoPath` if `stand_node` is not in the tree.
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

def test_unreachable_stand_raises_nopath(mini):
    with pytest.raises(NoPath):
        unimpeded(mini, mini.exits["09"][0]["node"], len(mini.nodes) + 99)   # a node id outside the graph

def test_ord_routes_exist_for_all_open_ends_in_osm():
    w = build.build_wave("F2015-270287"); g = load_graph("KORD")
    # ends that are open in the wave but absent from OSM (e.g. closed since 2015) use the generic tier (spec §6.9, C14)
    for r in (x for x in w.airport.runways if x.open and x.id in g.exits):
        e = choose_exit(g, r.id, "narrow", False)
        assert unimpeded(g, e["node"], next(iter(g.stands.values()))["node"]).unimpeded_min < 30
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_surface.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `surface.py`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add backend/wave/surface.py tests/test_surface.py
git commit -m "feat(engine): taxi graph, exit choice, runway occupancy and unimpeded routes"
```

### Task 24: Taxi in metrics, gate geo, real `ATC-TAXI-ROUTE`

**Files:**
- Modify:
  - `backend/wave/trajectory.py` (`TrajectorySet.ground` without waits);
  - `backend/wave/utility.py` (`in_block = slot + rot/60 + taxi`; ATC `taxi` term `(taxi − best unimpeded over open runways for its gate)/10`; remove `taxi_min` near/far except in the no-geometry tier);
  - `backend/wave/rules.py` (`ATC-TAXI-ROUTE`, `LIMITS` taxi row replaced);
  - `backend/wave/build.py`, `backend/wave/flights2015.py` (gates get `lat/lon/geo_src` via `bind_gates`; provenance `surface` layer);
  - `backend/wave/moves.py` (`change_runway` and gate-move labels append `taxi {m:.0f} min`; still ≤ 120 chars) and `backend/wave/graph.py:_view` (per-flight `taxi: "{exit} → {stand} {unimpeded:.1f} min"`), spec §6.7;
  - crew floors (`flights2015.py` line 320, `build.py` line 462) and `PLT-CREW-DUTY`'s "unavoidable" switch from `taxi_in_ref` (T21) to the minimum over open runways of `ROT + unimpeded routed taxi` to the flight's gate (`PLT-CREW-DUTY` evidence gains `taxi`, that minimum);
  - `backend/wave/version.py` (`"v3-taxi"`), `tests/test_rules.py`, `tests/test_surface.py`, `tests/test_version.py` (`"v3-taxi"`).

**Interfaces:**
- Produces:
  - `@dataclass GroundTrack(...)` is defined in `backend/wave/surface.py`; `trajectory.py` imports it from there:
    `GroundTrack(callsign, kind: Literal["arrival", "departure"], wave: bool, exit: str | None, stand: str, route_names: list[str], points: list[dict], waits: list[dict], unimpeded_min: float, taxi_min: float, src: str)`.
  - `surface.stand_node_for(wave, gate_id) -> int | None` (the bound stand's node) and `surface.min_unimpeded_taxi(wave, callsign, runway_end) -> float` (ROT + unimpeded taxi to the flight's gate; generic estimate when the end is missing from OSM). Rules and tests use both.
  - `ATC-TAXI-ROUTE` evidence: `runway, exit, gate, stand, route, crossings, unimpeded_min, tier`. Source: `"OpenStreetMap surface (ODbL), routed"`, or `"generic surface (straight line x1.4 at 15 kt)"`, or `"ops rule (no geometry)"`. It fails iff the tier is `real` and no path exists.
  - Per-wave surface tier (spec §6.9): `surface.wave_tier(wave, g) -> Literal["real", "generic"]` is `real` iff, for every open runway end **present in `g.exits`**, ≥ 80 % of the wave's bound stands (`bind_gates` results with `geo_src` ≠ `synth`) are reachable. Memoised on `route.wave_key(wave)` (`wave_tier.cache_clear()` for tests).
  - Tiers per flight: real tier and the flight's runway end in `g.exits` → OSM route; runway end missing from OSM (C14), wave tier `generic`, or gate `synth` → `generic_taxi_min`, with `src: "synth"`; no runway coordinates → v2 `taxi_min` (no-geometry tier, unchanged numbers). `ATC-TAXI-ROUTE` evidence `tier` names which applied.
  - `LIMITS` rows added: `"taxi route"` (`real data`, OSM routed), and `"runway ends missing from OSM"` (`assumption`: "open in 2015 but not in today's OSM; taxi uses the straight-line generic estimate").
  - New provenance layer: `Provenance("surface", "real"|"generic", "OpenStreetMap {osm_timestamp}")`.

- [ ] **Step 1: Write the failing tests**

```python
def test_in_block_is_slot_plus_rot_plus_taxi():
    w = build.build_wave("F2015-270287"); p = fcfs_plan(w)
    ts = trajectory.build(w, p); mm = metrics_map(w, p)
    for g in (x for x in ts.ground if x.wave):
        a = p.get(g.callsign)
        assert mm[a.callsign].in_block_min == pytest.approx(a.slot_min + mm[a.callsign].rot_s / 60 + g.taxi_min, abs=0.05)
        assert g.points[-1]["t_min"] == pytest.approx(mm[a.callsign].in_block_min, abs=0.05)   # in_block_min is rounded to 0.1

def test_taxi_route_rule_cites_route():
    w = build.build_wave("F2015-270287"); c = _by(rules.check(w, fcfs_plan(w)), "ATC-TAXI-ROUTE", w.focus)
    assert c.passed and c.evidence["tier"] == "real" and c.evidence["route"] and c.source.startswith("OpenStreetMap")

def test_taxi_route_fails_when_stand_unreachable(monkeypatch):
    w = build.build_wave("F2015-270287"); p = fcfs_plan(w)
    gate = p.get(w.focus).gate
    real = surface.unimpeded
    def fake(g, exit_node, stand):
        if stand == surface.stand_node_for(w, gate):
            raise surface.NoPath(gate)
        return real(g, exit_node, stand)
    monkeypatch.setattr(surface, "unimpeded", fake)
    trajectory.build.cache_clear(); surface.wave_tier.cache_clear()
    c = _by(rules.check(w, p), "ATC-TAXI-ROUTE", w.focus)
    assert not c.passed and gate in c.detail

def test_crew_floor_uses_routed_taxi():
    w = build.build_wave("F2015-270287"); p = fcfs_plan(w)
    c = _by(rules.check(w, p), "PLT-CREW-DUTY", w.focus)
    assert "taxi" in c.evidence and c.evidence["taxi"] == pytest.approx(
        min(surface.min_unimpeded_taxi(w, w.focus, r.id) for r in w.airport.runways if r.open), abs=0.05)

def test_change_runway_label_has_taxi():
    w = build.build_wave("F2015-270287"); p = fcfs_plan(w)
    ms = [m for m in moves._candidates(w, p, "ATC") if m.kind == "change_runway"]
    assert ms and all("taxi" in m.label and len(m.label) <= 120 for m in ms)

def test_gates_have_geo_on_replay_hub():
    w = build.build_wave("F2015-270287")
    assert all(g.lat is not None and g.geo_src for g in w.gates)
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_surface.py tests/test_rules.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the changes. `trajectory.py`, `utility.py` and `rules.py` call `surface.unimpeded` through the module (`from backend.wave import surface`; `surface.unimpeded(...)`), never `from backend.wave.surface import unimpeded`, so the test's monkeypatch takes effect.
- [ ] **Step 4: Run** `python3 -m pytest -q`. Expected: PASS. Re-run `tests/test_perf.py`: p95 ≤ 3 ms.
- [ ] **Step 5: Commit**

```bash
git add backend/wave/trajectory.py backend/wave/surface.py backend/wave/utility.py backend/wave/rules.py backend/wave/moves.py backend/wave/graph.py backend/wave/build.py backend/wave/flights2015.py backend/wave/version.py tests/test_rules.py tests/test_surface.py tests/test_version.py
git commit -m "contract: routed taxi-in drives in-block; real ATC-TAXI-ROUTE; gate coordinates (engine v3-taxi)"
```

### Task 25: API: `/geo` surface part, ground tracks

**Files:**
- Modify: `backend/server.py` (geo `surface` filled from `load_surface` `draw`/`stands`/`holding_positions`), `scripts/make_samples.py`, `tests/test_geo_api.py`

**Interfaces:**
- Produces: `WaveGeo.surface` per spec §7 (`taxiways`, `runways`, `stands` with `gate`, `holding_positions`, `attribution`). `TrajectorySet.ground` is serialised.

- [ ] **Step 1: Write the failing test** (append to `tests/test_geo_api.py`; `ORD_REPLAY` is imported at the top of that file from `scripts.make_samples.WAVE_ID`, see Task 16)

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
- [ ] **Step 5: Commit**

```bash
git add backend/server.py scripts/make_samples.py tests/test_geo_api.py
git commit -m "contract: WaveGeo.surface and TrajectorySet.ground"
```

### Task 26: Surface map, shared clock, Gantt taxi and hold segments; remove AirportMap

**Files:**
- Create: `frontend/src/components/Surface.tsx`
- Delete: `frontend/src/components/AirportMap.tsx` (unused; nothing imports it, verified on 2 Nov 2026)
- Modify: `frontend/src/WaveScreen.tsx`, `frontend/src/components/Gantt.tsx`, `frontend/src/components/Strips.tsx`, `frontend/src/api/client.ts` (`postEvaluate(..., {includeTrajectories})`), `frontend/src/geo/track.ts` (add `projector`), `frontend/src/geo/track.test.ts`, `frontend/src/styles.css`

**Interfaces:**
- Produces:
  - `projector(lat0: number, lon0: number): (lat: number, lon: number) => [number, number]` (metres, flat equirectangular; shared by Radar and Surface). It pins `R = 1852 * 60 * 180 / Math.PI` (≈ 6,366,707 m, so 1 arc-minute of latitude = 1 NM exactly); the test depends on this.
  - `<Surface geo={WaveGeo} traj={TrajectorySet | null} t={number} tone={(cs) => Tone} selected onSelect />`.
- Surface behaviour:
  - taxiways (taxilanes thinner), runways, stands and holding positions drawn to scale;
  - aircraft at `positionAt(ground.points, t)` at 1 s steps (`Math.floor(t * 60) / 60`);
  - attribution always visible;
  - a generic surface shows "surface not modelled for this airport".
- Shared clock: one `t` in `WaveScreen`. The panel auto-hands-off: radar until the focus flight's touchdown, surface after it, with a manual toggle; side by side at ≥ 1280 px.
- Gantt: hatched hold bar before the slot (one segment per lap); a thin taxi segment from touchdown to in-block before the gate bar. While a bar is dragged, the hold and taxi segments update live from `POST /api/evaluate` with `include_trajectories: true` (Task 16), debounced to one request in flight, with the previous request aborted.
- Strips (spec §9): after touchdown each strip shows `TXI {exit} → {stand} {taxi:.0f}m`; while taxiing, the exit name stays visible until in-block.

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
- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Surface.tsx frontend/src/components/AirportMap.tsx frontend/src/WaveScreen.tsx frontend/src/components/Gantt.tsx frontend/src/components/Strips.tsx frontend/src/api/client.ts frontend/src/geo/track.ts frontend/src/geo/track.test.ts frontend/src/styles.css
git commit -m "feat(ui): airport surface map on the replay clock; Gantt hold laps and taxi segments; remove unused AirportMap"
```

(`git add` of the deleted `AirportMap.tsx` stages its removal.)
### Task 27: Taxi validation, replays, v3-taxi benchmark row

**Files:**
- Modify: `backend/wave/benchmark.py`, `eval/benchmark.json`, `eval/BENCHMARK.md`, `data/replay/*.json`, `tests/test_benchmark_small.py`

**Interfaces:**
- Produces: `taxi_validation(waves: list, results: dict[str, dict]) -> dict` with:
  - `{"n", "mae_min", "bias_min" (routed − real), "per_airport": {iata: {"n", "routed_median", "bts_median"}}, "worst": [{"wave_id", "callsign", "routed", "real", "cause"}] (5)}`;
  - focus flights only, FCFS plan, real surface tier only; no fitting;
  - `cause` is a heuristic string: `"BTS includes gate wait"` if real > routed + 10, `"long route / remote stand"` if routed > real + 5, else `"-"`.
- `BenchmarkReport.taxi_validation` and `coverage: {"procedures": {...}, "surface": {...}, "ground_traffic": {...}}` (tier counts over the 50 waves).

- [ ] **Step 1: Write the failing test**

```python
from backend.wave import benchmark as B, build

_w_ord = lambda: build.build_wave("F2015-270287")

def test_taxi_validation_shape():
    v = B.taxi_validation(waves=[_w_ord()], results={"x": {"wave_id": "F2015-270287"}})
    assert set(v) == {"n", "mae_min", "bias_min", "per_airport", "worst"} and v["n"] == 1
```

- [ ] **Step 2: Run** it. Expected: FAIL.
- [ ] **Step 3: Implement** the changes, then re-record the replays (Task 18 command; the same 5 ids).
- [ ] **Step 4: Run** `python3 -m pytest -q`. Expected: PASS. Then run the full benchmark (Global Constraints command). Expected: a v3-taxi row, a taxi validation table (expect `bias_min < 0`; the README states it, it is not corrected) and coverage tiers in `BENCHMARK.md`.
- [ ] **Step 5: Commit**

```bash
git add backend/wave/benchmark.py eval/benchmark.json eval/BENCHMARK.md data/replay tests/test_benchmark_small.py
git commit -m "eval: v3-taxi row with taxi-in validation vs BTS 2015 and coverage tiers; replays re-recorded"
```

### Task 28: M2 verification

- [ ] **Step 1:** `python3 -m pytest -q`. Expected: PASS (including perf ≤ 3 ms and RSS ≤ 60 MB).
- [ ] **Step 2:** `cd frontend && npm test && npx tsc --noEmit && npm run build`. Expected: exit 0.
- [ ] **Step 3:** `PYTHONPATH=. python3 scripts/make_samples.py`. Expected: `geo.json` now has `surface`. Then:

```bash
git add frontend/src/api/samples
git commit -m "contract: samples regenerated for v3-taxi (geo surface, ground tracks)"
```
- [ ] **Step 4:** Docker smoke test as in Task 20 (expect `v3-taxi True`). Check the memory of the running container (`docker stats --no-stream`): it stays under 512 MB after opening all 5 replay waves' `/geo`.
- [ ] **Step 5:** Confirm that the v3-taxi row and taxi validation are present in `eval/BENCHMARK.md`.

---

## Milestone M3: Ground traffic (`v3-traffic`)

### Task 29: Hold-short scheduler and independent verifier

**Files:**
- Modify: `backend/wave/surface.py`, `tests/test_surface.py`

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
    path = path_across_1836(mini)
    s = schedule(mini, path, 0.5, res, "AA1")
    assert s.ok and s.waits[0]["reason"] == "hold short 18/36 for landing UA12"
    # spec §4.4 gap: no landing in [t - 1, t + 1.5] -> crossings blocked for t in [-0.5, 2.0]
    node = next(n for n, strip in path.crossings if strip == "18/36")
    assert next(q["t_min"] for q in s.points if q["node"] == node) > 2.0

def test_crossing_blocked_beyond_horizon_fails(mini):
    res = Reservations({}, {}, {"18/36": [(t / 2, "landing", f"L{t}") for t in range(0, 80)]})
    s = schedule(mini, path_across_1836(mini), 0.0, res, "AA1")
    assert not s.ok and "30 min" in s.reason
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_surface.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `schedule` and `verify_separation`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add backend/wave/surface.py tests/test_surface.py
git commit -m "feat(engine): deterministic hold-short taxi scheduler with independent separation verifier"
```

### Task 30: Fixed ground traffic and reservations

**Files:**
- Create: `backend/wave/ground_traffic.py`, `tests/test_ground_traffic.py`
- Modify: `backend/wave/build.py`, `backend/wave/flights2015.py` (provenance `ground_traffic`), `backend/wave/rules.py` (`LIMITS` row `"departure runway"`, `assumption`)

**Interfaces:**
- Consumes: `store.load_movements`, `surface.load_graph`, `surface.unimpeded`, `surface.schedule`, `surface.Reservations`, `moves.fcfs_runways`.
- Produces:
  - `@dataclass FixedMover(callsign: str, kind: Literal["arrival", "departure"], runway: str, stand_node: int, t_start_min: float, path: TaxiPath)`. For arrivals `t_start` is the exit time; for departures it is the pushback time `WHEELS_OFF − TAXI_OUT` (T0-relative).
  - `fixed_traffic(wave: Wave) -> list[FixedMover]`: queue arrivals plus real departures in `[T0 − 30, T0 + 120]` min. The departure stand is seeded (`build._rng(wave.id, cs, "stand")`) among stands nearest the carrier's most-used pier (`simulated`). The departure runway follows spec §6.4: on a **mixed-mode** airport, the arrival flow's runway; else the parallel departure runway in the same direction. Mixed-mode detection (an assumption, recorded in `LIMITS` as `"departure runway"`): the airport is mixed-mode iff the wave has no open runway end parallel to (heading within 10°) and in the same direction as the `fcfs_runways` majority arrival runway. Otherwise the departure runway is the parallel end not used by arrivals that is nearest the departure stand.
  - Uses `surface.Reservations` and `surface.schedule` (Task 29): `Reservations(node_busy: dict[int, list[tuple[float, float, str]]], edge_dir: dict[tuple[int, int], list[tuple[float, float, str]]], runway_events: dict[str, list[tuple[float, str, str]]])`. Times are in minutes; `runway_events` entries are `(t, "landing"|"takeoff", callsign)` per strip.
  - `reservations(wave: Wave) -> Reservations`: fixed movers are scheduled FCFS by `t_start`, cached on `route.wave_key(wave)` (`reservations.cache_clear()` for tests).
  - Provenance: `ground_traffic` is `real` ("BTS 2015 movements") where movements exist, else `generic` ("challenge flights.csv subset, partial").

- [ ] **Step 1: Write the failing tests**

```python
def test_ord_has_real_departures():
    w = build.build_wave("F2015-270287")
    ft = fixed_traffic(w)
    assert any(m.kind == "departure" for m in ft) and any(p.layer == "ground_traffic" and p.status == "real" for p in w.provenance)

def test_reservations_deterministic_and_headway():
    w = build.build_wave("F2015-270287")
    r1 = reservations(w)
    reservations.cache_clear()                      # otherwise r2 is the cached r1 and the test asserts nothing
    r2 = reservations(build.build_wave("F2015-270287"))
    assert r1 is not r2
    assert r1 == r2
    for busy in r1.node_busy.values():
        s = sorted(busy)
        assert all(b[0] - a[0] >= 0.5 - 1e-9 for a, b in zip(s, s[1:]))   # 30 s

def test_partial_badge_without_movements():
    from backend.geo import store
    from backend.wave import flights2015 as F
    iata = next(r["dest"] for r in F.list_rows(limit=500) if r["dest"] not in store.MOVEMENT_AIRPORTS)
    w = build.build_wave(f"F2015-{F.list_rows(airport=iata, limit=1)[0]['row_id']}")
    pv = next(p for p in w.provenance if p.layer == "ground_traffic")
    assert pv.status == "generic" and "partial" in pv.detail
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_ground_traffic.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `ground_traffic.py`, scheduling fixed movers with `surface.schedule` (Task 29) in `t_start` order.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add backend/wave/ground_traffic.py backend/wave/build.py backend/wave/flights2015.py backend/wave/rules.py tests/test_ground_traffic.py
git commit -m "feat(engine): fixed ground traffic with real 2015 departures and reservation table"
```

### Task 31: `ATC-TAXI-CONFLICT`, waits in metrics and conflicts in the TrajectorySet

**Files:**
- Modify:
  - `backend/wave/trajectory.py` (wave flights scheduled in exit-time order against a copy of `reservations(wave)` plus the plan's landings as runway events; `conflicts` filled);
  - `backend/wave/utility.py` (`taxi_wait_min`, `taxi_min = unimpeded + waits`);
  - `backend/wave/rules.py` (new rule, `LIMITS` rows for headway, crossing gap and horizon);
  - `backend/wave/moves.py` (labels add `+{wait} hold-short {strip}` when > 0);
  - `backend/wave/graph.py:_view` (per-flight hold-short clause `"hold short {strip} {wait:.1f} min for {cs}"` when a wait exists, spec §6.7; ≤ 120 chars);
  - `backend/wave/version.py` (`"v3-traffic"`), `docs/api-contract.md`, `tests/test_rules.py`, `tests/test_version.py` (`"v3-traffic"`). `types.ts` already has `ATC-TAXI-CONFLICT` (Task 9).

**Interfaces:**
- Produces:
  - `RuleCheck("ATC-TAXI-CONFLICT", "ATC", ok, detail, "FAA JO 7110.65 §3-7-2 (ground control route and hold-short); headway 30 s, crossing gap, 30-min horizon (assumption)", evidence={"waits": n, "wait_min", "crossings", "exit_clear_min", "next_landing_min"})`.
  - It fails iff `Scheduled.ok` is false, or `exit_clear_min` > the next landing slot on that strip; the detail then names that next landing's callsign. Ground-track points carry `node` (graph node id) so tests can address the exit node.
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
    from backend.wave import ground_traffic, surface
    w = build.build_wave("F2015-270287"); p = fcfs_plan(w)
    a = p.get(w.focus)
    ex = next(t for t in trajectory.build(w, p).ground if t.callsign == w.focus).points[0]   # exit point
    nxt = min((b for b in p.assignments if b.action == "land" and b.runway == a.runway and b.slot_min > a.slot_min),
              key=lambda b: b.slot_min)
    real = ground_traffic.reservations(w)
    blocked = surface.Reservations({**real.node_busy, ex["node"]: [(a.slot_min, nxt.slot_min + 1.0, "BLOCK")]},
                                   real.edge_dir, real.runway_events)
    monkeypatch.setattr(ground_traffic, "reservations", lambda wave: blocked)
    trajectory.build.cache_clear()
    c = _by(rules.check(w, p), "ATC-TAXI-CONFLICT", w.focus)
    assert not c.passed and nxt.callsign in c.detail

def test_all_benchmark_fcfs_ground_tracks_separated():
    for wid in _bench_ids()[:10]:
        w = build.build_wave(wid); ts = trajectory.build(w, fcfs_plan(w))
        assert verify_separation(ts.ground) == [], wid
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_rules.py tests/test_surface.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the changes (14 rules now). `trajectory.py` calls `ground_traffic.reservations` through the module (`from backend.wave import ground_traffic`), so the vacate test's monkeypatch takes effect. Update the `/api/evaluate` docstring "same 14 rules".
- [ ] **Step 4: Run** `python3 -m pytest -q`. Expected: PASS, with perf ≤ 3 ms p95. If over budget, profile and memoise further. Never skip the rule.
- [ ] **Step 5: Commit**

```bash
git add backend/wave/trajectory.py backend/wave/utility.py backend/wave/rules.py backend/wave/moves.py backend/wave/graph.py backend/wave/version.py backend/server.py docs/api-contract.md tests/test_rules.py tests/test_surface.py tests/test_version.py
git commit -m "contract: ATC-TAXI-CONFLICT; hold-short waits feed in-block; conflicts in TrajectorySet (engine v3-traffic)"
```

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
- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Surface.tsx frontend/src/geo/track.ts frontend/src/geo/track.test.ts frontend/src/styles.css
git commit -m "feat(ui): surface map shows hold-shorts, crossings and rule conflicts"
```

### Task 33: M3 verification and v3-traffic benchmark row

- [ ] **Step 1:** `python3 -m pytest -q`. Expected: PASS.
- [ ] **Step 2:** `cd frontend && npm test && npx tsc --noEmit && npm run build`. Expected: exit 0.
- [ ] **Step 3:** Re-record the replays (Task 18), then `PYTHONPATH=. python3 scripts/make_samples.py`.
- [ ] **Step 4:** Run the full benchmark (Global Constraints command). Expected: a v3-traffic row with `veto_counts` for `ATC-HOLD-STACK` and `ATC-TAXI-CONFLICT`, mean `taxi_wait_min` per arm, 0 rule violations in agreed plans, regressions listed with causes, and any new failures in `failures`.
- [ ] **Step 5:** Docker smoke test (expect `v3-traffic True`) plus a `docker stats` check under 512 MB. Then commit:

```bash
git add data/replay eval/benchmark.json eval/BENCHMARK.md
git commit -m "eval: v3-traffic benchmark row; replays re-recorded"
git add frontend/src/api/samples
git commit -m "contract: samples regenerated for v3-traffic"
```

---

## Milestone M4: Polish

### Task 34: Narration and MCP tool

**Files:**
- Modify: `backend/wave/narrate.py` (`_template`, `_compact`), `backend/mcp_server.py`, `tests/test_mcp.py`, `tests/test_graph.py`

**Interfaces:**
- Produces:
  - The template brief adds, for the focus flight, `f"{n} lap{'s' if n != 1 else ''} at {fix} ({kg:.0f} kg)"` when laps > 0, placed **before** the totals sentence so `_trim(MAX_WORDS)` never cuts it, and `"hold-short {strip} {min} min"` when taxi waits > 0. `_compact` passes `hold_laps, hold_fix, taxi_wait_min` to Claude.
  - MCP tool `get_trajectory(wave_id: str, assignments: list[dict]) -> dict`, which returns the `TrajectorySet` dict (read-only, all agents).

- [ ] **Step 1: Write the failing tests**

```python
def test_template_mentions_laps():
    r = _result_with_focus_metrics(hold_laps=2, hold_fix="BENKY", fuel_kg=428.0)
    assert "2 laps at BENKY" in narrate._template(r)["brief"]

def test_template_one_lap_singular():
    r = _result_with_focus_metrics(hold_laps=1, hold_fix="BENKY", fuel_kg=214.0)
    assert "1 lap at BENKY" in narrate._template(r)["brief"]
```

  In `tests/test_mcp.py`, extend the existing exact set at line 10 (it uses `asyncio.run(mcp_server.mcp.list_tools())`) rather than adding a new test:

```python
    assert names == {"list_waves", "get_wave", "fcfs_plan", "legal_moves", "check_plan", "negotiate", "get_trajectory"}
```

- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement** the changes.
- [ ] **Step 4: Run** `python3 -m pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add backend/wave/narrate.py backend/mcp_server.py tests/test_mcp.py tests/test_graph.py
git commit -m "feat: brief mentions holding laps and hold-shorts; MCP get_trajectory"
```

### Task 35: Docs, story and final verification

**Files:**
- Modify: `README.md` (paths story, data sources with CIFP disclaimer and OSM attribution, cost, taxi validation bias statement), `eval/BENCHMARK.md` (narrative header), `docs/api-contract.md` (final v2.2 status line)
- Create: `docs/screens/` screenshots (radar holding, surface hold-short)

- [ ] **Step 1:** Write the README section, including the subsection "Data: Postgres (Neon) with file fallback" (`backend.datastore`, `scripts/db_seed.py <dataset>`, `DATABASE_URL` / `DATABASE_URL_POOLED`, the opt-in `live_db` tests). It must contain these exact strings, checked by `tests/test_api.py::test_readme_attributions` (add it): "Procedures: FAA CIFP 2611, not for navigation" and "© OpenStreetMap contributors".
- [ ] **Step 2:** `python3 -m pytest -q -m "not live_db"`. Expected: PASS.
- [ ] **Step 3:** `cd frontend && npm test && npx tsc --noEmit && npm run build`, then `PYTHONPATH=. python3 scripts/make_samples.py`. Expected: exit 0 and no sample diff.
- [ ] **Step 4:** Demo walkthrough of all 5 replays offline (no keys, `replay: true`). Each must show radar paths, at least 3 must show a holding lap, and the surface map must run on the same clock. Docker smoke test.
- [ ] **Step 5: Commit**

```bash
git add README.md eval/BENCHMARK.md docs/api-contract.md docs/screens tests/test_api.py
git commit -m "docs: v3 engine story, sources and attributions; screenshots"
```

---

## Self-review (applied)

1. **Spec coverage** (spec section → tasks):
   - §5 pipeline: T2-T7. ICAO fix: T1. §6.1: T21.
   - §6.2: T11-T13. §6.3: T10, T12, T14. §6.4: T23-T24, T29 (scheduler), T30 (fixed traffic), T31. §6.5: T13, T24, T31. §6.6: T14, T24, T31. §6.7: T15. §6.8: T12 memo, T20 perf. §6.9: T11, T24, T29 tiers.
   - §7: T9, T16, T25, T31. §8: T16 guard, T18. §9: T17, T26, T32. §10: T19, T22, T27, T33. §12: tests per task.
   - AirportMap removal: T26.
2. **Step scan:**
   - No test body is `...`: the five that were (T14 fuel reserve, T16 suggestion, T24 NoPath, T30 partial badge, T31 vacate) now have their assertions written out (pre-flight C4). Named helpers (`_far_runway`, `two_holders_reversed`, `_ord_plan_with_crossing`, the T29 path builders) are defined in prose or code in their owning task.
   - No product function bodies are given.
3. **Type consistency:**
   - `metrics_map`, `trajectory.build`, `earliest`, `fcfs_runways`, `TaxiPath`, `Reservations` and `Scheduled` are used with the signatures of their producing tasks.
   - `GroundTrack` is defined in T24 (in `backend/wave/surface.py`) and used in T29/T31. `NoPath` is defined in T23. `Reservations` is defined in T29 and used in T30/T31.
   - `ENGINE_VERSION` values follow Global Constraints.
4. **Review Focus:** five lines, each pinned to a named test in its owning task.
5. **Proportion:** the plan is about the spec's length, and the code blocks are tests plus schemas.
6. **Pre-flight scan (2 Nov 2026):** the 73-row table in `.superpowers/sdd/2026-11-02-flight-paths-and-taxi/preflight.md` was applied in place. Each task's own text carries its fix, because tasks are extracted one at a time as briefs. C1 (all of `frontend/` in scope) and C2 (one commit per task on `redesign/console`, explicit `git add`) are in Global Constraints.

## Changes and flags versus the design

- **CIFP column positions corrected:** time/distance is cols 75-78 (not 79-82), and the FAF/IF flag is col 43 (not 42). Verified on cycle 2611 lines (Task 2). Spec §4.1 amended to match.
- **Hold fix wording in spec §6.3 amended:** "last fix of the STAR common route" was wrong for KORD BENKY6. Its common route is BENKY (12,000 ft) → NEWRK → AHSTN → PETAH, so BENKY, the meter fix where the enroute transitions join, is the **first** common-route fix. Spec §6.3 and §14 call 3 now say "first"; the plan uses "first fix of the common route (else the last enroute-transition fix)" (Tasks 2 and 11).
- **ABE as the no-STAR test airport:** verified in CIFP 2611 (records A, F, G; no E).
- **No-geometry tier added** (unchanged v2 formulas) for waves whose runways lack coordinates, such as the synthetic test fixture. The design's "generic" tier needs a gate/runway position, so it cannot cover this case.
- **New engine version `v3-time`** for M2a, so its benchmark row is attributable.
- **Contract additions:**
  - `QueueArrival.origin` (additive), so fixed traffic can pick a STAR;
  - `paths_unavailable` on replayed results;
  - `geo_unavailable` 404 code.
- **vitest** added as a frontend devDependency (frontend had no test runner).
- **Test data directory** named `tests/data/` (spec §12 says `tests/fixtures/cifp/`). A `tests/fixtures/` directory would not break `from tests.fixtures import ...`, because the regular module wins over a namespace directory; `tests/data/` was kept only to keep data apart from that module.
- **Surface quality tier is per wave** (T24), not stored offline: open ends present in OSM against the wave's bound stands, ≥ 80 %. Runway ends open in 2015 but missing from today's OSM use the straight-line generic taxi (`LIMITS` assumption row).
- **`Gate.geo_src` gains `"osm-gate"`** (spec §5 binding step 4: the OSM gate node itself), added to the contract in T9.
- **Movements set** is the union of the top 30 and the 30 benchmark airports, within the 50 MB cap (T6); otherwise the top 30, with the rest badged partial.
- **No app-wide gzip** (T16): Starlette's `GZipMiddleware` would buffer the SSE streams, so only `/geo` compresses its own response.
