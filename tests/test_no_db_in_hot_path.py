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
