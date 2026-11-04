"""Fill airports whose OpenStreetMap surface fails the quality gate from the X-Plane Scenery Gateway.

Usage: python3 scripts/fetch_surface_xplane.py [--only KIAH,PHNL] [--offline]
For each airport (the fetch_cifp.py set) whose data/ref/surface/{ICAO}.json.gz is missing, below the gate, or already
from the Gateway, the recommended Gateway scenery's apt.dat is fetched (cached in .cache/xplane/{ICAO}.dat with its
metadata), turned into Overpass-like elements (backend.geo.xplane_surface) and built by the same builder as OSM.
The file is replaced only when every runway end reaches >= 80 % of stands (the "real" gate of geo_coverage.py).
Writes data/ref/surface/XPLANE.md (the airports, scenery ids and authors) and COPYING.xplane (the GPL text).
Gateway data is GPL v2 or later; the derived files keep that licence.
"""
import argparse
import base64
import gzip
import io
import json
import subprocess
import sys
import time
import warnings
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from backend.geo import xplane_surface as XS  # noqa: E402
from backend.geo.osm_surface import build_surface  # noqa: E402
from backend.wave.icao import iata_for  # noqa: E402
from fetch_cifp import airports  # noqa: E402
from fetch_surface import reference_point, runways_for  # noqa: E402
from geo_coverage import REAL_REACH, _surface_tier  # noqa: E402

API = "https://gateway.x-plane.com/apiv1"
CACHE = ROOT / ".cache" / "xplane"
OUT = ROOT / "data" / "ref" / "surface"
SLEEP_S = 1.0


def _get(url: str) -> dict:
    """GET JSON through curl (python.org builds on macOS ship no CA bundle). Raises RuntimeError."""
    for attempt in range(3):
        r = subprocess.run(["curl", "-sfL", "--max-time", "120", url], capture_output=True)
        if r.returncode == 0:
            return json.loads(r.stdout)
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"curl exit {r.returncode} for {url}")


def fetch(icao: str, offline: bool) -> tuple[str, dict] | None:
    """(apt.dat text, metadata) of the recommended Gateway scenery, cached; None when the Gateway has none."""
    dat, meta = CACHE / f"{icao}.dat", CACHE / f"{icao}.meta.json"
    if dat.is_file() and meta.is_file():
        return dat.read_text(errors="replace"), json.loads(meta.read_text())
    if offline:
        return None
    sid = _get(f"{API}/airport/{icao}")["airport"].get("recommendedSceneryId")
    if not sid:
        return None
    s = _get(f"{API}/scenery/{sid}")["scenery"]
    z = zipfile.ZipFile(io.BytesIO(base64.b64decode(s["masterZipBlob"])))
    name = next((n for n in z.namelist() if n.endswith(".dat")), None)
    if name is None:
        return None
    text = z.read(name).decode("utf-8", "replace")
    m = {"scenery": sid, "uploaded": (s.get("dateUploaded") or "")[:10], "author": s.get("userName")}
    CACHE.mkdir(parents=True, exist_ok=True)
    dat.write_text(text)
    meta.write_text(json.dumps(m))
    copying = CACHE / "COPYING"
    if not copying.is_file():
        inner = next((n for n in z.namelist() if n.endswith(".zip")), None)
        if inner:
            iz = zipfile.ZipFile(io.BytesIO(z.read(inner)))
            lic = next((n for n in iz.namelist() if n.endswith("COPYING")), None)
            if lic:
                copying.write_bytes(iz.read(lic))
    return text, m


def derive(text: str, meta: dict, icao: str, ref: tuple[float, float], runways: list[dict]) -> dict | None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")   # dropped holds on runway pavement are expected
        doc = build_surface(XS.to_elements(text), icao, runways, ref, "")
    if not doc["edges"]:
        return None
    doc["header"] = {**XS.HEADER, "gateway_scenery": meta["scenery"], "gateway_uploaded": meta.get("uploaded"),
                     "gateway_author": meta.get("author"), "ref": [round(ref[0], 6), round(ref[1], 6)]}
    return doc


def current(icao: str) -> dict | None:
    p = OUT / f"{icao}.json.gz"
    return json.loads(gzip.decompress(p.read_bytes())) if p.is_file() else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated ICAO codes")
    ap.add_argument("--offline", action="store_true", help="build from .cache/xplane only")
    args = ap.parse_args()
    wanted = airports()
    if args.only:
        wanted = [a for a in wanted if a in args.only.split(",")]
    taken, kept = [], []
    for n, icao in enumerate(wanted):
        old = current(icao)
        from_gateway = ((old or {}).get("header") or {}).get("source") == XS.HEADER["source"]
        if not from_gateway and _surface_tier(old) == "real":
            continue
        iata = iata_for(icao) or (icao[1:] if icao.startswith("K") else None)
        ref = reference_point(icao, iata)
        if ref is None:
            kept.append(f"{icao}(no reference point)")
            continue
        cached = (CACHE / f"{icao}.dat").is_file()
        try:
            got = fetch(icao, args.offline)
        except RuntimeError as e:
            kept.append(f"{icao}({e})")
            continue
        doc = derive(*got, icao, ref, runways_for(iata)) if got else None
        tier = _surface_tier(doc)
        if tier == "real":
            raw = json.dumps(doc, separators=(",", ":"), sort_keys=True).encode()
            (OUT / f"{icao}.json.gz").write_bytes(gzip.compress(raw, 9, mtime=0))
            taken.append((icao, iata, doc["header"]))
        else:
            kept.append(f"{icao}({'no gateway scenery' if got is None else 'below the gate' if doc else 'no taxi network'})")
        print(f"[{n + 1}/{len(wanted)}] {icao} {'X-Plane' if tier == 'real' else 'kept'}", flush=True)
        if not cached and not args.offline:   # the Gateway is a shared service: one request pair a second
            time.sleep(SLEEP_S)
    if not args.only:
        lines = ["# Surfaces from the X-Plane Scenery Gateway", "",
                 "Airports whose OpenStreetMap surface fails the quality gate (every runway end reaches "
                 f">= {REAL_REACH:.0%} of stands), filled from the recommended Gateway scenery by "
                 "`scripts/fetch_surface_xplane.py`. Gateway data is free software under the GNU GPL v2 or later "
                 "(COPYING.xplane); these derived files keep that licence. The rest of the repository is MIT.", "",
                 "| ICAO | IATA | Gateway scenery | Uploaded | Author |", "|---|---|---|---|---|"]
        for icao, iata, h in sorted(taken, key=lambda t: t[0]):
            lines.append(f"| {icao} | {iata or ''} | {h['gateway_scenery']} | {h.get('gateway_uploaded') or ''} | {h.get('gateway_author') or ''} |")
        (OUT / "XPLANE.md").write_text("\n".join(lines) + "\n")
        if (CACHE / "COPYING").is_file():
            (OUT / "COPYING.xplane").write_bytes((CACHE / "COPYING").read_bytes())
    print(f"X-Plane: {len(taken)}; kept: {len(kept)} {' '.join(kept)}")


if __name__ == "__main__":
    main()
