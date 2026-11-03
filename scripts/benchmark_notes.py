"""Post-run step for a benchmark: attach hand-written engine notes and re-render BENCHMARK.md.

  python3 scripts/benchmark_notes.py notes.json          # {"title", "items", "limits", "definition_change"?}
  python3 scripts/benchmark_notes.py --variants FIRM.json RELAXING.json [--prev PATH]
                                                         # attach fairness_variants + the pick (prev defaults to
                                                         # eval/archive/engine-v3-traffic/benchmark.json)
  python3 scripts/benchmark_notes.py --archive           # before a NEW engine's run: freeze eval/benchmark.json
                                                         # + BENCHMARK.md into eval/archive/engine-<its version>/
Order for a new engine: --archive, run the benchmark, benchmark_notes.py, db_seed.py benchmark.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend.wave.benchmark import markdown, pick_fairness  # noqa: E402

EVAL = ROOT / "eval"


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("notes", nargs="?", help="JSON file with the engine_notes object")
    ap.add_argument("--archive", action="store_true")
    ap.add_argument("--variants", nargs=2, metavar=("FIRM", "RELAXING"), help="the two --fairness benchmark reports")
    ap.add_argument("--prev", help="v3-traffic report for --variants")
    a = ap.parse_args(argv)
    rep = json.loads((EVAL / "benchmark.json").read_text())
    if a.archive:
        dest = EVAL / "archive" / f"engine-{rep.get('engine_version', 'v2')}"
        dest.mkdir(parents=True, exist_ok=True)
        for n in ("benchmark.json", "BENCHMARK.md"):
            shutil.copy2(EVAL / n, dest / n)
        print(f"archived to {dest}")
        return
    if a.variants:
        firm, relaxing = (json.loads(Path(p).read_text()) for p in a.variants)
        for want, r, path in (("firm", firm, a.variants[0]), ("relaxing", relaxing, a.variants[1])):
            if r.get("fairness") != want:
                ap.error(f"{path} has fairness {r.get('fairness')!r}, expected {want!r} (arguments are FIRM then RELAXING)")
        prev = json.loads(Path(a.prev or EVAL / "archive" / "engine-v3-traffic" / "benchmark.json").read_text())
        part = lambda r: {"arms": [{"label": x["label"], "summary": x["summary"]} for x in r["arms"]]}  # noqa: E731
        rep["fairness_variants"] = {"firm": part(firm), "relaxing": part(relaxing),
                                    "pick": pick_fairness(firm, relaxing, prev)}
        (EVAL / "benchmark.json").write_text(json.dumps(rep, indent=2))
        (EVAL / "BENCHMARK.md").write_text(markdown(rep))
        print(f"fairness_variants written (pick: {rep['fairness_variants']['pick']['variant']}); BENCHMARK.md re-rendered")
        return
    if not a.notes:
        ap.error("give a notes file, --variants or --archive")
    rep["engine_notes"] = json.loads(Path(a.notes).read_text())
    (EVAL / "benchmark.json").write_text(json.dumps(rep, indent=2))
    (EVAL / "BENCHMARK.md").write_text(markdown(rep))
    print("engine_notes written; BENCHMARK.md re-rendered")


if __name__ == "__main__":
    main()
