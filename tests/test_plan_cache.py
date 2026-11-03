"""Final review Critical 1: the per-plan trajectory cache is bounded (Render has 512 MB; one TrajectorySet is about
125-170 KB on the replay waves), and the edit suggestion search does not fill it with its trial plans."""
from dataclasses import replace

from backend.wave import build, edit, fixtures, moves, trajectory
from backend.wave.graph import negotiate_wave


def test_plan_cache_has_a_hard_bound(monkeypatch):
    assert trajectory.PLAN_CACHE_MAX <= 256          # ~256 x 170 KB = ~43 MB worst case on the server
    w = fixtures.make_wave()
    p = moves.fcfs_plan(w)
    a = next(x for x in p.assignments if x.action == "land")
    trajectory.build.cache_clear()
    monkeypatch.setattr(trajectory, "_build", lambda wave, plan: object())
    for i in range(trajectory.PLAN_CACHE_MAX + 50):
        trajectory.build(w, p.with_assignment(replace(a, slot_min=a.slot_min + i * 0.01)))
    assert len(trajectory._CACHE) == trajectory.PLAN_CACHE_MAX
    trajectory.build.cache_clear()


def test_per_wave_caches_are_bounded_separately():
    assert trajectory.WAVE_CACHE_MAX <= 64 and trajectory.LAPS_CACHE_MAX <= 1024


def test_suggestion_search_does_not_cache_its_trial_plans():
    w = build.build_wave("F2015-270287")
    rows = [dict(r) for r in negotiate_wave(w, "formulas")["final_plan"]["assignments"]]
    lead = next(r for r in rows if r["callsign"] == "OO5559")
    other = next(r for r in rows if r["callsign"] == "MQ3318")
    other.update(runway=lead["runway"], slot_min=lead["slot_min"] + 0.5)   # breaks wake separation
    plan = edit.plan_from_dicts(rows)
    trajectory.build.cache_clear()
    out = edit.check_edit(w, plan, include_trajectories=True)
    assert out["suggestions"].get("MQ3318") is not None                   # the search ran and found a slot
    assert set(trajectory._CACHE) == {(trajectory.route.wave_key(w), plan.id)}   # only the edited plan, served
    assert trajectory._scratch() is None                                  # torn down: the worker thread is reused
    s = out["suggestions"]["MQ3318"]
    fixed = [dict(r, slot_min=s) if r["callsign"] == "MQ3318" else r for r in rows]
    assert edit.check_edit(w, edit.plan_from_dicts(fixed))["evaluation"]["feasible"]


def test_benchmark_restores_the_plan_cache_bound():
    from backend.wave import benchmark
    before = trajectory.PLAN_CACHE_MAX
    benchmark.main(["--fixtures", "--n", "1", "--arms", "formula", "--concurrency", "3", "--no-ablation",
                    "--outage-n", "0", "--out", str(__import__("tempfile").mkdtemp())])
    assert trajectory.PLAN_CACHE_MAX == before


def test_plan_cache_env_value_is_clamped():
    from backend.wave import trajectory
    assert trajectory._cache_cap(None) == 256
    assert trajectory._cache_cap("") == 256
    assert trajectory._cache_cap("0") == 256
    assert trajectory._cache_cap("-5") == 256
    assert trajectory._cache_cap("lots") == 256
    assert trajectory._cache_cap("512") == 512
