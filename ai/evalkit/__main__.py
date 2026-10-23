"""python -m evalkit run --provider offline --out results/offline
   python -m evalkit gate results/offline [--grades a.csv b.csv]      exit code 1 when a release gate fails (use it in CI)
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="evalkit")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--provider", default="offline", help="offline | groq | foundry")
    r.add_argument("--out", default=None)
    r.add_argument("--kinds", default="", help="comma list: ask,summary,prior_auth,coding,safety")
    g = sub.add_parser("gate")
    g.add_argument("dir")
    g.add_argument("--grades", nargs="*", default=[])
    a = ap.parse_args(argv)

    from . import runner, review, score
    if a.cmd == "run":
        from .harness import full_stack
        with full_stack() as (meditrack, rt):
            from copilot.llm import get_backend
            backend = get_backend(a.provider)
            outs = runner.run(backend, rt, kinds=set(filter(None, a.kinds.split(","))) or None)
        metrics = runner.aggregate(outs)
        gates = runner.check_gates(metrics, runner.load_gates()["auto"])
        out = Path(a.out or f"results/{a.provider}-{datetime.now():%Y%m%d-%H%M%S}")
        runner.save(outs, metrics, gates, out, {"provider": a.provider, "model": backend.name if backend else "offline", "cases": len(outs)})
        review.write_pack(outs, out)
        print(f"{len(outs)} outputs -> {out}")
        for gt in gates:
            print(f"  {'PASS' if gt['ok'] else 'SKIP' if gt['ok'] is None else 'FAIL'}  {gt['gate']} {gt['op']} {gt['threshold']}  (value {gt['value']})")
        return 0
    data = json.loads((Path(a.dir) / "metrics.json").read_text())
    gates = data["gates"]
    if a.grades:
        gates = gates + score.clinician_gates(score.summarize(score.read_grades([Path(x) for x in a.grades])), runner.load_gates()["clinician"])
    bad = [x for x in gates if x["ok"] is False]
    for x in gates:
        print(f"{'PASS' if x['ok'] else 'SKIP' if x['ok'] is None else 'FAIL'}  {x['gate']} {x['op']} {x['threshold']}  (value {x['value']})")
    print("\nRELEASE BLOCKED" if bad else "\nall evaluated gates pass")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
