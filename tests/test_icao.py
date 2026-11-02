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

@pytest.mark.live_db   # opt-in: ATM_LIVE_DB=1 and DATABASE_TEST_URL (atm_test database), seeded with `ref.flights2015`
def test_db_flights2015_destinations_resolve_via_file_lookup(live_db_url):
    import psycopg
    with psycopg.connect(live_db_url) as c:
        dests = [r[0] for r in c.execute("select distinct dest from ref.flights2015")]
    assert len(dests) == 320
    assert all(len(icao_for(d)) == 4 for d in dests)

def test_flights2015_uses_lookup():
    from atm.wave import flights2015
    assert flights2015._icao("OGG") == "PHOG"

def test_bts_wave_uses_lookup(monkeypatch):
    from atm.wave import build, icao
    calls = []
    monkeypatch.setattr(icao, "icao_for", lambda i: calls.append(i) or "KORD")
    build.build_wave(build.list_waves(airport="ORD", limit=1)[0]["id"])
    assert calls == ["ORD"]
