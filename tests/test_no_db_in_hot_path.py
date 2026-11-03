from atm import datastore
from atm.wave import build, graph

class Exploding:
    def __getattr__(self, name):
        raise AssertionError(f"store.{name} called in the hot path")

def test_negotiation_and_evaluate_never_touch_the_store():
    w = build.build_wave("F2015-270287")
    datastore.set_store(Exploding())
    try:
        res = graph.negotiate_wave(w, "formulas", ["formula"], lambda m: None, narrate=False)
        assert res["outcome"] in ("consensus", "fallback")
        from atm.wave import edit
        edit.check_edit(w, edit.plan_from_dicts(res["final_plan"]["assignments"]))
    finally:
        datastore.set_store(None)


def test_cold_2024_wave_warms_workbook_limits_before_the_hot_path():
    from atm.wave import edit
    build._reset_caches()                       # constraints._load() is cold again
    w = build.build_wave("DCA-2024-06-22-16")
    datastore.set_store(Exploding())
    try:
        res = graph.negotiate_wave(w, "formulas", ["formula"], lambda m: None, narrate=False)
        edit.check_edit(w, edit.plan_from_dicts(res["final_plan"]["assignments"]))
    finally:
        datastore.set_store(None)


# more waves than route.CACHE_MAX, built the way the benchmark builds them (flights2015.build_from_row)
MANY_F2015 = [144520, 343919, 210010, 156608, 317184, 105396, 324426, 53108, 60842, 209722, 314303, 302254,
              115532, 112396, 169005, 161169, 251827, 209820, 298859, 112468]


def test_routes_pinned_at_build_survive_cache_eviction():
    from atm.wave import flights2015, route, utility
    from atm.wave.moves import fcfs_plan
    route.routes_for_wave.cache_clear()
    waves = [flights2015.build_from_row(r) for r in MANY_F2015]
    assert len(waves) > route.CACHE_MAX
    datastore.set_store(Exploding())
    try:
        w = waves[0]
        utility.evaluate(w, fcfs_plan(w))
        res = graph.negotiate_wave(w, "formulas", ["formula"], lambda m: None, narrate=False)
        assert res["outcome"] in ("consensus", "fallback")
    finally:
        datastore.set_store(None)


def test_pinned_routes_not_carried_to_a_changed_wave():
    from dataclasses import replace
    import copy, pickle
    from atm.wave import flights2015, route
    w = flights2015.build_from_row(270287)
    pinned = route.routes_for_wave(w)
    assert route.routes_for_wave(copy.deepcopy(w)).keys() == pinned.keys()
    assert route.routes_for_wave(pickle.loads(pickle.dumps(w))).keys() == pinned.keys()
    w2 = replace(w, queue=[])                       # content changed: routes for the queue must go
    assert not any(cs in {q.callsign for q in w.queue} for cs, _ in route.routes_for_wave(w2))
