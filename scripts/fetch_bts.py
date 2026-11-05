"""Fetch BTS On-Time PREZIP months and build wave arrivals + index.

Usage: python3 scripts/fetch_bts.py
Writes data/waves/arrivals.csv.gz and data/waves/index.csv.

       python3 scripts/fetch_bts.py --index
Rebuilds data/waves/index.csv from the arrivals already in data/waves/arrivals.csv.gz (no download).

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


RANK_CAP = 8   # disrupted landings in one hour above which a wave is ranked last


def build_index(df: pd.DataFrame) -> pd.DataFrame:
    """One wave per (airport, local date, hour) in which at least 3 arrivals really land (scheduled gate arrival +
    ARR_DELAY - taxi-in, `build.landing_times`) and at least one of them is disrupted. A late flight is in the
    hour it lands, not the hour it was scheduled, so a wave is the traffic it actually meets. Landings more than a
    day after the BTS date are left out (the wave builder reads that date and the day before)."""
    from backend.wave.build import landing_times
    d = df[df["cancelled"] == 0]
    d = pd.concat([landing_times(g, ap) for ap, g in d.groupby("dest")]).dropna(subset=["land_abs"])
    off = (d["land_abs"] // 1440).astype(int)
    d = d[off.isin([0, 1])].copy()
    off = off[d.index]
    d["date"] = (pd.to_datetime(d["date"]) + pd.to_timedelta(off, unit="D")).dt.strftime("%Y-%m-%d")
    d["hour"] = ((d["land_abs"] - 1440 * off) // 60).astype(int)
    d["disrupted"] = (d["weather_delay"] > 15) | (d["late_aircraft_delay"] > 20)
    d["fid"] = d["carrier"].astype(str) + d["flight_no"].astype("Int64").astype(str)
    d = d[d["date"].str[:7].isin(MONTHS)]
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
    # busiest first, but hours with more than RANK_CAP disrupted landings (storm backlogs of up to ~60) go last:
    # the app's list and the benchmark sample read the index in this order
    over = idx["n_disrupted"] > RANK_CAP
    return idx.assign(_over=over).sort_values(["_over", "n_disrupted", "wave_id"], ascending=[True, False, True]) \
              .drop(columns="_over").reset_index(drop=True)


MOVE_COLS = ["date", "kind", "carrier", "flight_no", "tail", "other", "sched", "wheels", "taxi", "cancelled"]
MOVE_SRC = ["FlightDate", "Reporting_Airline", "Flight_Number_Reporting_Airline", "Tail_Number", "Origin", "Dest",
            "CRSDepTime", "CRSArrTime", "WheelsOff", "TaxiOut", "WheelsOn", "TaxiIn", "Cancelled"]
MOVEMENTS = ROOT / "data" / "ref" / "movements2015"
MOVEMENTS_2024 = ROOT / "data" / "ref" / "movements2024"
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
    return _read_month(f"2015-{month:02d}", cols)


def _read_month(month: str, cols: list[str]) -> pd.DataFrame:
    with zipfile.ZipFile(download(month)) as z:  # read only; nothing is extracted/executed
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


def movements2024() -> None:
    """The 2024 wave months (hubs.MONTHS) at the hubs, same columns as movements2015, so 2024 waves get real
    departures in ground traffic."""
    frames = [month_movements(_read_month(m, MOVE_SRC), set(HUBS)) for m in MONTHS]
    for old in MOVEMENTS_2024.glob("*.csv.gz"):
        old.unlink()
    total = write_movements(frames, sorted(HUBS), MOVEMENTS_2024)
    print(f"{len(HUBS)} airports, {total / 1e6:.1f} MB (not in git: seeded to Postgres only)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--movements2015", action="store_true", help="write data/ref/movements2015/{IATA}.csv.gz")
    if ap.parse_known_args()[0].movements2015:
        movements2015()
        return
    ap.add_argument("--movements2024", action="store_true", help="write data/ref/movements2024/{IATA}.csv.gz")
    if ap.parse_known_args()[0].movements2024:
        movements2024()
        return
    ap.add_argument("--index", action="store_true", help="rebuild data/waves/index.csv from arrivals.csv.gz")
    if ap.parse_known_args()[0].index:
        idx = build_index(pd.read_csv(OUT / "arrivals.csv.gz", low_memory=False))
        idx.to_csv(OUT / "index.csv", index=False)
        print(f"waves={len(idx)}")
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
