"""Fetch BTS On-Time PREZIP months and build wave arrivals + index.

Usage: python3 scripts/fetch_bts.py
Writes data/waves/arrivals.csv.gz and data/waves/index.csv.

       python3 scripts/fetch_bts.py --movements2015
Reads the 12 PREZIP months of 2015 (cached in .cache/bts/2015_M/) and writes one file per airport,
data/ref/movements2015/{IATA}.csv.gz, with every arrival and departure there (columns MOVE_COLS), for the
top 30 airports by 2015 arrivals plus the benchmark airports (~94 MB, gitignored; seeded to Postgres only).
Prints the TOP30 tuple to paste into backend/geo/store.py.
"""
import argparse
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend.wave.hubs import HUBS, MONTHS  # noqa: E402

CACHE = ROOT / ".cache" / "bts"
OUT = ROOT / "data" / "waves"
URL = ("https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_"
       "Performance_1987_present_{y}_{m}.zip")

COLS = {
    "FlightDate": "date", "Reporting_Airline": "carrier",
    "Flight_Number_Reporting_Airline": "flight_no", "Tail_Number": "tail",
    "Origin": "origin", "Dest": "dest", "CRSDepTime": "sched_dep",
    "CRSArrTime": "sched_arr", "DepTime": "dep_time", "ArrTime": "arr_time",
    "ArrDelay": "arr_delay", "WeatherDelay": "weather_delay",
    "NASDelay": "nas_delay", "LateAircraftDelay": "late_aircraft_delay",
    "CarrierDelay": "carrier_delay", "Distance": "distance",
    "Cancelled": "cancelled", "Diverted": "diverted",
}
DELAYS = ["arr_delay", "weather_delay", "nas_delay", "late_aircraft_delay",
          "carrier_delay"]


def download(month: str) -> Path:
    y, m = month.split("-")
    dest = CACHE / f"{y}_{int(m)}" / "bts.zip"
    if dest.exists() and zipfile.is_zipfile(dest):  # partial zips lack the footer
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = URL.format(y=y, m=int(m))
    subprocess.run(["curl", "-sSL", "--fail", "-o", str(dest), url], check=True)
    return dest


def load_month(zpath: Path) -> pd.DataFrame:
    with zipfile.ZipFile(zpath) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        with z.open(names[0]) as f:  # read only; nothing is extracted/executed
            df = pd.read_csv(f, usecols=list(COLS), low_memory=False)
    df = df.rename(columns=COLS)
    return df[df["dest"].isin(HUBS)]


def build_index(df: pd.DataFrame) -> pd.DataFrame:
    d = df[df["cancelled"] == 0].copy()
    d["hour"] = (d["sched_arr"] // 100) % 24
    d["disrupted"] = (d["weather_delay"] > 15) | (d["late_aircraft_delay"] > 20)
    d["fid"] = d["carrier"].astype(str) + d["flight_no"].astype(str)
    rows = []
    for (ap, date, hour), g in d.groupby(["dest", "date", "hour"]):
        n = len(g)
        dis = g[g["disrupted"]]
        if n < 3 or dis.empty:
            continue
        rows.append({
            "wave_id": f"{ap}-{date}-{hour:02d}", "airport": ap, "date": date,
            "hour": hour, "n_arrivals": n, "n_disrupted": len(dis),
            "disrupted_flights": ";".join(dis["fid"]),
        })
    idx = pd.DataFrame(rows)
    return idx.sort_values(["n_disrupted", "wave_id"],
                           ascending=[False, True]).reset_index(drop=True)


MOVE_COLS = ["date", "kind", "carrier", "flight_no", "tail", "other", "sched", "wheels", "taxi", "cancelled"]
MOVE_SRC = ["FlightDate", "Reporting_Airline", "Flight_Number_Reporting_Airline", "Tail_Number", "Origin", "Dest",
            "CRSDepTime", "CRSArrTime", "WheelsOff", "TaxiOut", "WheelsOn", "TaxiIn", "Cancelled"]
MOVEMENTS = ROOT / "data" / "ref" / "movements2015"
BENCHMARK = ROOT / "eval" / "benchmark.json"
FLIGHTS_INDEX = ROOT / "data" / "challenge2" / "flights_index.csv"


def hhmm_min(v: pd.Series) -> pd.Series:
    """HHMM -> minutes since midnight (2400 -> 1440 is clipped to the 23:59 of that day); NaN stays NaN."""
    v = pd.to_numeric(v, errors="coerce")
    return (v // 100).clip(upper=23) * 60 + (v % 100).where(v < 2400, 59)


def wrap_after(actual: pd.Series, sched: pd.Series) -> pd.Series:
    """Actual wheels time on the scheduled clock: +1440 when it wrapped past midnight (actual far below schedule);
    same rule as scripts/split_flights2015.py."""
    return actual.where(~(actual < sched - 720), actual + 1440)


def _read_2015(month: int, cols: list[str]) -> pd.DataFrame:
    with zipfile.ZipFile(download(f"2015-{month:02d}")) as z:  # read only; nothing is extracted/executed
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        with z.open(names[0]) as f:
            return pd.read_csv(f, usecols=cols, low_memory=False,
                               dtype={"Tail_Number": str, "Origin": str, "Dest": str, "Reporting_Airline": str})


def benchmark_airports() -> list[str]:
    ids = {int(w.rsplit("-", 1)[1]) for w in json.loads(BENCHMARK.read_text())["wave_ids"]}
    idx = pd.read_csv(FLIGHTS_INDEX, usecols=["row_id", "dest"])
    return sorted(idx[idx["row_id"].isin(ids)]["dest"].unique())


def month_movements(df: pd.DataFrame, airports: set[str]) -> pd.DataFrame:
    """Arrival and departure rows (MOVE_COLS) at `airports` for one month; date is the local date at that airport."""
    df = df.rename(columns={"FlightDate": "d0"})
    d0 = pd.to_datetime(df["d0"])
    sdep, sarr = hhmm_min(df["CRSDepTime"]), hhmm_min(df["CRSArrTime"])
    # BTS dates are the departure date; a scheduled arrival clock before the departure clock means next day
    arr_date = (d0 + pd.to_timedelta((sarr < sdep).astype(int), unit="D")).dt.strftime("%Y-%m-%d")
    dep_date = d0.dt.strftime("%Y-%m-%d")
    common = {"carrier": df["Reporting_Airline"], "flight_no": df["Flight_Number_Reporting_Airline"],
              "tail": df["Tail_Number"], "cancelled": df["Cancelled"].astype(int)}
    parts = []
    for kind, apt, other, date, sched, wheels, taxi in (
            ("arrival", "Dest", "Origin", arr_date, sarr, "WheelsOn", "TaxiIn"),
            ("departure", "Origin", "Dest", dep_date, sdep, "WheelsOff", "TaxiOut")):
        m = df[apt].isin(airports)
        w = wrap_after(hhmm_min(df[wheels]), sched)
        part = pd.DataFrame({"airport": df[apt], "date": date, "kind": kind, **common, "other": df[other],
                             "sched": sched, "wheels": w, "taxi": df[taxi]})[m]
        parts.append(part)
    out = pd.concat(parts, ignore_index=True)
    out["flight_no"] = pd.to_numeric(out["flight_no"], errors="coerce").fillna(0).astype(int)
    out["sched"] = out["sched"].fillna(0).astype(int)
    out["wheels"] = out["wheels"].astype("Int64")
    out["taxi"] = out["taxi"].round().astype("Int64")   # BTS taxi times are whole minutes
    return out


def write_movements(frames: list[pd.DataFrame], airports: list[str], out_dir: Path) -> int:
    df = pd.concat(frames, ignore_index=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    for apt, g in df.groupby("airport", sort=True):
        g = g.sort_values(["date", "sched", "kind", "carrier", "flight_no"], kind="stable")
        p = out_dir / f"{apt}.csv.gz"
        g[MOVE_COLS].to_csv(p, index=False, compression={"method": "gzip", "compresslevel": 9})
        total += p.stat().st_size
    return total


def movements2015() -> None:
    months = range(1, 13)
    arrivals = pd.Series(dtype="int64")
    for m in months:  # pass 1: arrivals per airport (all flights; cancelled ones count as scheduled arrivals)
        arrivals = arrivals.add(_read_2015(m, ["Dest"])["Dest"].value_counts(), fill_value=0)
    top30 = tuple(sorted(arrivals.sort_values(ascending=False, kind="stable").head(30).index))
    bench = benchmark_airports()
    union = sorted(set(top30) | set(bench))
    print(f"TOP30 = {top30!r}")
    print(f"benchmark airports ({len(bench)}): {bench}")
    frames = [month_movements(_read_2015(m, MOVE_SRC), set(union)) for m in months]
    for old in MOVEMENTS.glob("*.csv.gz"):
        old.unlink()
    total = write_movements(frames, union, MOVEMENTS)
    print(f"{len(union)} airports, {total / 1e6:.1f} MB (not in git: seeded to Postgres only)")
    print(f"MOVEMENT_AIRPORTS = {tuple(union)!r}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--movements2015", action="store_true", help="write data/ref/movements2015/{IATA}.csv.gz")
    if ap.parse_known_args()[0].movements2015:
        movements2015()
        return
    ap.add_argument("--months", nargs="+", default=MONTHS,
                    help="YYYY-MM months to include (default: hubs.MONTHS)")
    months = ap.parse_args().months
    OUT.mkdir(parents=True, exist_ok=True)
    frames = [load_month(download(m)) for m in months]
    df = pd.concat(frames, ignore_index=True)
    df[DELAYS] = df[DELAYS].fillna(0)
    for c in ("sched_dep", "sched_arr"):
        df[c] = df[c].astype(int)
    df["cancelled"] = df["cancelled"].astype(int)
    df["diverted"] = df["diverted"].astype(int)
    df = df[list(COLS.values())]
    df.to_csv(OUT / "arrivals.csv.gz", index=False, compression={"method": "gzip", "compresslevel": 9})
    idx = build_index(df)
    idx.to_csv(OUT / "index.csv", index=False)
    print(f"arrivals={len(df)} waves={len(idx)}")
    print(idx["airport"].value_counts().head(5).to_string())


if __name__ == "__main__":
    main()
