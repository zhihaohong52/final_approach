"""Fetch OSM airport surfaces via Overpass and write one derived surface file per airport into data/ref/surface/.

Usage: python3 scripts/fetch_surface.py [--only KORD,KATL] [--offline]
Airports: the same set as fetch_cifp.py (flights2015 destinations plus HUBS). Per airport the query is tried by
ICAO, then IATA, then a 6 km bbox around the reference point; the raw response is cached in
.cache/overpass/{ICAO}.json, so a rerun does not refetch. --offline rebuilds from the cache only.
Overpass is a shared service: one airport at a time, 5 s between requests, 2 retries with backoff.
Output matches _surface_row in scripts/db_seed.py. OSM data is ODbL.
"""
import argparse
import csv
import gzip
import json
import math
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from backend.geo.osm_surface import build_surface  # noqa: E402
from backend.wave.icao import iata_for  # noqa: E402
from fetch_cifp import airports  # noqa: E402

URL = "https://overpass-api.de/api/interpreter"
UA = "disruption-negotiator/3 (+https://github.com/zhihaohong52/final_approach)"
SLEEP_S = 5.0
RETRIES = 2
BACKOFF_S = (15.0, 45.0)
BBOX_M = 6000.0
try:   # python.org builds on macOS ship no CA bundle; certifi (already installed with requests) has one
    import certifi
    SSL = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL = ssl.create_default_context()
CACHE = ROOT / ".cache" / "overpass"
OUT = ROOT / "data" / "ref" / "surface"
REF = ROOT / "data" / "ref"
TAGS = "taxiway|taxilane|runway|parking_position|gate|holding_position|terminal"
SELECT = f'["aeroway"~"^({TAGS})$"]'


def query(kind: str, value: str | tuple) -> str:
    head = "[out:json][timeout:90];"
    if kind == "bbox":
        s, w, n, e = value
        body = f"(node{SELECT}({s},{w},{n},{e});way{SELECT}({s},{w},{n},{e}););"
    else:
        body = f'area[{kind}="{value}"]->.a;(node{SELECT}(area.a);way{SELECT}(area.a););'
    return f"{head}{body}out geom;"


def bbox(lat: float, lon: float) -> tuple[float, float, float, float]:
    dlat = BBOX_M / 111_320.0
    dlon = BBOX_M / (111_320.0 * math.cos(math.radians(lat)))
    return round(lat - dlat, 5), round(lon - dlon, 5), round(lat + dlat, 5), round(lon + dlon, 5)


def expand_geometry(osm: dict) -> dict:
    """`out geom` gives ways a `geometry` list but emits no node element for untagged vertices; the builder wants
    one node element per vertex, so synthesise them (tagged node elements win)."""
    nodes: dict[int, dict] = {}
    ways = []
    for el in osm.get("elements", []):
        if el.get("type") == "node" and "lat" in el:
            nodes[el["id"]] = el
        elif el.get("type") == "way":
            ways.append(el)
    for w in ways:
        geom = w.get("geometry") or []
        for nid, g in zip(w.get("nodes", []), geom):
            if g and nid not in nodes:
                nodes[nid] = {"type": "node", "id": nid, "lat": g["lat"], "lon": g["lon"]}
    slim = [{k: v for k, v in w.items() if k != "geometry"} for w in ways]
    return {**{k: v for k, v in osm.items() if k != "elements"}, "elements": [nodes[k] for k in sorted(nodes)] + slim}


def _post(q: str) -> dict:
    """One Overpass call with retries. Raises RuntimeError when it stays unusable."""
    last = "?"
    for attempt in range(RETRIES + 1):
        if attempt:
            time.sleep(BACKOFF_S[min(attempt - 1, len(BACKOFF_S) - 1)])
        req = urllib.request.Request(URL, data=("data=" + urllib.parse.quote(q)).encode(),
                                     headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=150, context=SSL) as r:
                body = r.read()
            doc = json.loads(body)
            remark = str(doc.get("remark") or "")
            if "runtime error" in remark or "timed out" in remark:
                last = remark[:120]
                continue
            return doc
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, ValueError) as e:   # ValueError: HTML error page, not JSON
            last = f"{type(e).__name__}: {str(e)[:80]}"
    raise RuntimeError(last)


def fetch(icao: str, iata: str | None, ref: tuple[float, float] | None) -> dict:
    """Cached raw Overpass response for the airport (ICAO, then IATA, then bbox). Raises RuntimeError on failure."""
    path = CACHE / f"{icao}.json"
    if path.is_file():
        return json.loads(path.read_text())
    tries = [("icao", icao)] + ([("iata", iata)] if iata else []) + ([("bbox", bbox(*ref))] if ref else [])
    doc, errs = None, []
    for i, (kind, value) in enumerate(tries):
        if i:
            time.sleep(SLEEP_S)
        try:
            doc = _post(query(kind, value))
        except RuntimeError as e:
            errs.append(f"{kind}: {e}")
            continue
        if doc.get("elements"):
            break
    if doc is None:
        raise RuntimeError("; ".join(errs))
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, separators=(",", ":")))
    return doc


def reference_point(icao: str, iata: str | None) -> tuple[float, float] | None:
    p = ROOT / "data" / "ref" / "procedures" / f"{icao}.json.gz"
    if p.is_file():
        a = json.loads(gzip.decompress(p.read_bytes()))["airport"]
        return a["lat"], a["lon"]
    if iata:
        for r in csv.DictReader(open(ROOT / "data" / "challenge2" / "airports.csv")):
            if r["IATA_CODE"] == iata:
                return float(r["LATITUDE"]), float(r["LONGITUDE"])
    return None


def runways_for(iata: str | None) -> list[dict]:
    if not iata:
        return []
    with open(REF / "runways.csv", newline="") as f:
        return [r for r in csv.DictReader(f) if r["airport"] == iata]


def derive(osm: dict, icao: str, ref: tuple[float, float], runways: list[dict]) -> dict | None:
    """Surface document, or None when OSM has no taxi network for the airport."""
    stamp = (osm.get("osm3s") or {}).get("timestamp_osm_base") or "unknown"
    doc = build_surface(expand_geometry(osm), icao, runways, ref, stamp)
    if not doc["edges"]:
        return None
    doc["header"]["ref"] = [round(ref[0], 6), round(ref[1], 6)]
    return doc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated ICAO codes")
    ap.add_argument("--offline", action="store_true", help="build from .cache/overpass only")
    args = ap.parse_args()
    wanted = airports()
    if args.only:
        wanted = [a for a in wanted if a in args.only.split(",")]
    blobs: dict[str, bytes] = {}
    empty, failed = [], []
    reach_all: list[float] = []
    for n, icao in enumerate(wanted):
        iata = iata_for(icao) or (icao[1:] if icao.startswith("K") else None)
        ref = reference_point(icao, iata)
        if ref is None:
            failed.append(f"{icao}(no reference point)")
            continue
        cached = (CACHE / f"{icao}.json").is_file()
        if args.offline and not cached:
            failed.append(f"{icao}(not cached)")
            continue
        try:
            osm = fetch(icao, iata, ref)
        except RuntimeError as e:
            failed.append(f"{icao}({e})")
            print(f"[{n + 1}/{len(wanted)}] {icao} FAILED {e}", flush=True)
            time.sleep(SLEEP_S)
            continue
        doc = derive(osm, icao, ref, runways_for(iata))
        if doc is None:
            empty.append(icao)
        else:
            reach_all += list((doc["quality"]["reach_pct"]).values())
            raw = json.dumps(doc, separators=(",", ":"), sort_keys=True).encode()
            blobs[icao] = gzip.compress(raw, 9, mtime=0)
        print(f"[{n + 1}/{len(wanted)}] {icao} {'empty' if doc is None else len(blobs[icao])}", flush=True)
        if not cached and not args.offline:
            time.sleep(SLEEP_S)
    OUT.mkdir(parents=True, exist_ok=True)
    if not args.only:
        for old in OUT.glob("*.json.gz"):
            old.unlink()
    for icao, b in blobs.items():
        (OUT / f"{icao}.json.gz").write_bytes(b)
    size = sum(p.stat().st_size for p in OUT.glob("*.json.gz"))
    print(f"wrote {len(blobs)} files, {size} bytes; empty {len(empty)}: {' '.join(empty)}")
    print(f"failed {len(failed)}: {' '.join(failed)}")
    if reach_all:
        s = sorted(reach_all)
        print(f"reach_pct over {len(s)} runway ends: min {s[0]} median {s[len(s) // 2]} max {s[-1]}")


if __name__ == "__main__":
    main()
