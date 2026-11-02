"""Fetch the FAA CIFP (cycle 2611) and write one procedures file per airport into data/ref/procedures/.

Usage: python3 scripts/fetch_cifp.py
The zip goes to .cache/cifp/ (verified by size and SHA-256, then only parsed, never executed).
Airports: the flights2015 destinations plus atm.wave.hubs.HUBS. Airports with no terminal data
in the cycle are skipped and listed. Output matches _procedure_row/_fix_rows in scripts/db_seed.py.
"""
import gzip
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from atm.geo.cifp import extract_airport  # noqa: E402
from atm.wave.hubs import HUBS  # noqa: E402
from atm.wave.icao import icao_or_prefix  # noqa: E402

CYCLE = "2611"
URL = "https://aeronav.faa.gov/Upload_313-d/cifp/CIFP_261029.zip"
CIFP_SHA256 = "e3ff2b31a4f6ad3eda1342b83ada1ae399aae7bd8c1e67f3cacd5b288b5e141d"
CIFP_SIZE = 9_113_631
MEMBER = "FAACIFP18"
CACHE = ROOT / ".cache" / "cifp"
ZIP = CACHE / "CIFP_261029.zip"
OUT = ROOT / "data" / "ref" / "procedures"
FLIGHTS = ROOT / "data" / "challenge2" / "flights"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"


def download() -> None:
    if ZIP.is_file():
        return
    CACHE.mkdir(parents=True, exist_ok=True)
    subprocess.run(["curl", "-sSL", "--fail", "-A", UA, "--max-time", "600", "-o", str(ZIP), URL], check=True)


def verify() -> None:
    size = ZIP.stat().st_size
    digest = hashlib.sha256(ZIP.read_bytes()).hexdigest()
    if size != CIFP_SIZE or digest != CIFP_SHA256:
        raise SystemExit(f"{ZIP}: size {size} sha256 {digest} does not match the pinned cycle {CYCLE}")


def airports() -> list[str]:
    iatas = {p.name[:-len(".csv.gz")] for p in FLIGHTS.glob("*.csv.gz")} | set(HUBS)
    return sorted({icao_or_prefix(i) for i in iatas})


def main() -> None:
    download()
    verify()
    with zipfile.ZipFile(ZIP) as z:
        lines = z.read(MEMBER).decode("latin-1").splitlines()
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.json.gz"):
        old.unlink()
    fetched_at = f"cycle-{CYCLE}"   # fixed so regenerating gives byte-identical files
    tiers: dict[str, int] = {}
    skipped = []
    for icao in airports():
        doc = extract_airport(lines, icao, CYCLE, fetched_at=fetched_at)
        if doc is None:
            skipped.append(icao)
            continue
        tiers[doc["header"]["tier"]] = tiers.get(doc["header"]["tier"], 0) + 1
        raw = json.dumps(doc, separators=(",", ":"), sort_keys=True).encode()
        (OUT / f"{icao}.json.gz").write_bytes(gzip.compress(raw, 9, mtime=0))
    n = sum(tiers.values())
    size = sum(p.stat().st_size for p in OUT.glob("*.json.gz"))
    print(f"wrote {n} files, {size} bytes; tiers {tiers}")
    print(f"skipped (no terminal data in {CYCLE}) {len(skipped)}: {' '.join(skipped)}")


if __name__ == "__main__":
    main()
