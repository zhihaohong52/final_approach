from backend.wave import build
from backend.wave.airlines import airline_name, airline_of, groups
from tests.fixtures import make_wave


def test_regional_maps_to_major():
    assert airline_of("OH", "CLT", 2024)[0] == "AA" and airline_of("MQ", "DFW", 2015)[0] == "AA"
    assert airline_of("9E", "ATL", 2024)[0] == "DL"


def test_unknown_carrier_is_own_airline():
    assert airline_of("XX", "ATL", 2024) == ("XX", "no grouping row: operating carrier is its own airline")


def test_multi_partner_by_hub():
    assert airline_of("EV", "ATL", 2015)[0] == "DL" and airline_of("EV", "ORD", 2015)[0] == "UA"
    assert airline_of("OO", "ORD", 2015)[0] == "OO" and airline_of("EV", "DFW", 2015)[0] == "EV"


def test_us_airways_2015_is_aa():
    assert airline_of("US", "CLT", 2015)[0] == "AA"


def test_year_outside_single_range_falls_through():
    assert airline_of("US", "CLT", 2024) == ("US", "no grouping row: operating carrier is its own airline")
    assert airline_of("MQ", "DFW", 2010)[0] == "MQ"


def test_groups_stress_waves():
    w = build.build_wave("CLT-2024-07-29-19")
    assert list(groups(w)) == ["AA"] and len(groups(w)["AA"]) == 5
    assert list(groups(build.build_wave("ATL-2024-07-18-21"))) == ["DL"]


def test_wave_dict_has_airlines():
    d = make_wave().to_dict()
    assert [a["code"] for a in d["airlines"]] == ["AA", "DL", "UA"]
    assert set(d["airlines"][0]) == {"code", "name", "callsigns", "basis"}


def test_groups_cached_and_name_fallback():
    w = make_wave()
    assert groups(w) is groups(w)
    assert airline_name("ZZ") == "ZZ"
