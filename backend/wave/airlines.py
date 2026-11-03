"""Operating carrier -> airline (the commercial owner whose interests an airline agent represents).

Data check (design §3.1): no marketing carrier is available in the data we hold.
`data/challenge2/flights.csv` has the operating `AIRLINE` column only, `scripts/fetch_bts.py` keeps
`Reporting_Airline` only (the operating carrier), and no raw BTS PREZIPs are kept. So a regional
(OH, MQ, 9E ...) cannot be tied to the major it flies for from the flight rows. A later fetch of BTS's
marketing-carrier table could supply this; it is not fetched here.

Instead this is a small static table (SOURCE). Rule: a single-partner regional (SINGLE) maps to its major for
the years the agreement held. A multi-partner regional (MULTI) maps to the major that hubs at the wave's
airport (HUB_MAJOR) when that major is one of its partners that year. Anything else is its own airline.
Deterministic and data-free at runtime; the rule engine never reads it.
"""
from __future__ import annotations

from backend.wave.model import Wave

SOURCE = ("static table compiled from public carrier partnership history, unverified against a "
          "marketing-carrier dataset (BTS flight rows carry the operating carrier only). Limit: a multi-partner "
          "regional is grouped only by hub major or a per-hub override (HUB_OVERRIDE); e.g. OO at ORD and EV at "
          "DFW stay their own airline because the data has no marketing-carrier field")

# carrier -> [(first_year, last_year, major, basis)]
SINGLE: dict[str, list[tuple[int, int, str, str]]] = {
    "MQ": [(2015, 2025, "AA", "Envoy (MQ) flies only as American Eagle")],
    "US": [(2015, 2015, "AA", "US Airways (US) merged into American in Apr 2015 and flew under AA")],
    "OH": [(2015, 2025, "AA", "PSA (OH) flies only as American Eagle")],
    "9E": [(2015, 2025, "DL", "Endeavor (9E) flies only as Delta Connection")],
}

# carrier -> [(first_year, last_year, {partner majors}, basis)]
MULTI: dict[str, list[tuple[int, int, frozenset[str], str]]] = {
    "EV": [(2015, 2018, frozenset({"DL", "UA"}), "ExpressJet (EV) flew for Delta Connection and United Express"),
           (2019, 2020, frozenset({"UA"}), "ExpressJet (EV) flew only for United Express before ending in 2020")],
    "OO": [(2015, 2025, frozenset({"AA", "DL", "UA", "AS"}), "SkyWest (OO) flies for AA, DL, UA and AS")],
    "YX": [(2015, 2025, frozenset({"AA", "DL", "UA"}), "Republic (YX) flies for AA, DL and UA")],
    "YV": [(2015, 2020, frozenset({"AA", "UA"}), "Mesa (YV) flew for American Eagle and United Express (AA end year unverified)"),
           (2021, 2025, frozenset({"UA"}), "Mesa (YV) flew for United Express (unverified)")],
    "G7": [(2015, 2025, frozenset({"DL", "UA"}), "GoJet (G7) flew for Delta Connection and United Express")],
}

# hub airport -> the one major that hubs there (ORD is a hub for both AA and UA, so it maps to none)
HUB_MAJOR = {"ATL": "DL", "CLT": "AA", "DFW": "AA", "MIA": "AA", "DEN": "UA"}

# (carrier, hub, first_year, last_year) -> (major, basis): hubs where the data is one-sided
HUB_OVERRIDE: dict[tuple[str, str], list[tuple[int, int, str, str]]] = {
    ("EV", "ORD"): [(2015, 2015, "UA", "ExpressJet (EV) at ORD flew United Express")],
}


def airline_of(carrier: str, hub_iata: str, year: int) -> tuple[str, str]:
    """(airline code, basis text) for an operating carrier at a hub in a year."""
    for lo, hi, major, basis in SINGLE.get(carrier, ()):
        if lo <= year <= hi:
            return major, basis
    for lo, hi, partners, basis in MULTI.get(carrier, ()):
        if lo <= year <= hi:
            for olo, ohi, major, obasis in HUB_OVERRIDE.get((carrier, hub_iata), ()):
                if olo <= year <= ohi:
                    return major, obasis
            hub = HUB_MAJOR.get(hub_iata)
            if hub in partners:
                return hub, f"{basis}; grouped under {hub}, the hub major at {hub_iata}"
            return carrier, f"{basis}; no single partner major at {hub_iata}, so its own airline"
    return carrier, "no grouping row: operating carrier is its own airline"


def _year(wave: Wave) -> int:
    return int(wave.window_start[:4])


def _grouped(wave: Wave) -> tuple[dict[str, list[str]], dict[str, str]]:
    """(groups, basis per code), cached on the wave: this is called inside evaluate()."""
    iata, year = wave.airport.iata, _year(wave)
    key = (id(wave.flights), len(wave.flights), iata, year)
    c = wave.__dict__.get("_airline_cache")
    if c is not None and c[0] == key and c[1] is wave.flights:
        return c[2], c[3]
    g: dict[str, list[str]] = {}
    basis: dict[str, str] = {}
    for f in wave.flights:
        code, b = airline_of(f.carrier, iata, year)
        g.setdefault(code, []).append(f.callsign)
        # a code's own carrier row is the clearest basis; otherwise the first partner's
        if code not in basis or f.carrier == code:
            basis[code] = b
    g = {k: g[k] for k in sorted(g)}
    wave.__dict__["_airline_cache"] = (key, wave.flights, g, basis)
    return g, basis


def groups(wave: Wave) -> dict[str, list[str]]:
    """airline code -> callsigns in wave order, codes sorted. Treat as read-only (cached per wave)."""
    return _grouped(wave)[0]


def airline_name(code: str, wave: Wave | None = None) -> str:
    """airlines.csv name, else the code. A wave's own flight rows are used first (no store read)."""
    if wave is not None:
        for f in wave.flights:
            if f.carrier == code:
                return f.airline
    from backend.wave import build
    return build._airlines().get(code, code)


def describe(wave: Wave) -> list[dict]:
    g, basis = _grouped(wave)
    return [{"code": c, "name": airline_name(c, wave), "callsigns": list(cs), "basis": basis[c]}
            for c, cs in g.items()]
