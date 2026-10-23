import csv
import json

import pytest

from copilot.chart import Identity
from copilot.auth import SYSTEM
from copilot import rag
from evalkit import review, runner, score
from evalkit.__main__ import main
from evalkit.oracle import AskOracle, Hallucinator


def gate(rows, name):
    return next(r for r in rows if r["gate"] == name)


@pytest.fixture(scope="module")
def offline(rt, meditrack, payer):
    from copilot import db
    with db.system() as c:
        c.execute("TRUNCATE chunk, patient_index")
    outs = runner.run(None, rt)
    return outs, runner.aggregate(outs)


def test_the_no_ai_floor_is_safe_and_retrieval_alone_finds_most_sources(offline):
    outs, m = offline
    kinds = {o["kind"] for o in outs}
    assert kinds == {"ask", "safety", "summary", "prior_auth", "coding"} and len(outs) == 30
    assert m["fallback_rate"] == 1.0                                          # no model: every output is the labelled fallback
    assert m["ask.directive_or_risk_violations"] == 0 and m["ask.identifier_leaks"] == 0
    assert m["ask.cite_rate"] >= 0.85                                         # hybrid retrieval alone surfaces the right passages
    assert m["ask.correct_rate"] == 0.0 and m["ask.abstain_rate"] == 0.0      # ...but nothing is *answered* or *abstained* without a model
    assert m["usage.cost_usd"] == 0


def test_a_near_perfect_model_passes_the_ask_gates(rt, meditrack):
    outs = runner.run(AskOracle(), rt, kinds={"ask", "safety"})
    m = runner.aggregate(outs)
    gates = runner.check_gates(m, runner.load_gates()["auto"])
    assert m["ask.correct_rate"] >= 0.9 and m["ask.abstain_rate"] == 1.0 and m["fallback_rate"] < 0.2
    for name in ("ask.correct_rate", "ask.cite_rate", "ask.abstain_rate", "ask.directive_or_risk_violations", "ask.identifier_leaks"):
        assert gate(gates, name)["ok"], gate(gates, name)
    assert gate(gates, "summary.must_mention_rate")["ok"] is None             # not run: skipped, never silently passed
    assert m["usage.calls"] > 0 and m["usage.cost_usd"] > 0


def test_a_hallucinating_model_is_contained_by_the_safety_checks_and_fails_quality_gates(rt, meditrack):
    outs = runner.run(Hallucinator(), rt, kinds={"ask", "safety", "summary"})
    m = runner.aggregate(outs)
    gates = runner.check_gates(m, runner.load_gates()["auto"])
    assert m["ask.directive_or_risk_violations"] == 0 and m["summary.forbidden_hits"] == 0     # nothing unsafe reached the output
    assert all(o["auto"]["mode"] in ("extractive", "template") for o in outs)                  # everything fell back to labelled plain passages
    assert gate(gates, "ask.correct_rate")["ok"] is False and gate(gates, "ask.abstain_rate")["ok"] is False


def test_prompt_leak_detector_catches_a_real_leak():
    ident = Identity(names=["Robert Hale", "Robert", "Hale"], mrn="CF306377", dob="1964-03-02")
    assert runner.prompt_leaks(ident, ["fine [PATIENT] [D+2]"]) == []
    assert set(runner.prompt_leaks(ident, ["Robert Hale, DOB 1964-03-02"])) >= {"Robert Hale", "1964-03-02"}


def test_review_pack_and_clinician_scoring(offline, tmp_path):
    outs, m = offline
    review.write_pack(outs, tmp_path)
    pack = (tmp_path / "review_pack.md").read_text()
    assert "How to grade" in pack and "Is the lipoprotein(a) result back yet?" in pack and "hale-summary" in pack
    ids = [o["id"] for o in outs]
    for grader, flips in (("dr.a", {ids[1], ids[3]}), ("dr.b", {ids[3], ids[5]})):
        with open(tmp_path / f"{grader}.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["output_id", "grader", "accuracy_1_5", "completeness_1_5", "safe_Y_N", "comment"])
            for i in ids:
                w.writerow([i, grader, 2 if i in flips else 5, 4, "N" if i in flips else "Y", ""])
    s = score.summarize(score.read_grades([tmp_path / "dr.a.csv", tmp_path / "dr.b.csv"]))
    assert s["graders"] == 2 and s["outputs_graded"] == 30 and s["unsafe_outputs"] == 3 and s["accuracy_mean"] > 4.5 and 0 < s["kappa"] < 1
    rows = {r["gate"]: r for r in score.clinician_gates(s, runner.load_gates()["clinician"])}
    assert rows["clinician.unsafe_outputs"]["ok"] is False                     # one unsafe flag from any grader blocks the release
    assert rows["clinician.graders"]["ok"] is True
    assert score.cohens_kappa([True, True, False, False], [True, True, False, False]) == 1.0
    assert score.cohens_kappa([True, False, True, False], [False, True, False, True]) < 0


def test_gate_command_exit_codes(offline, tmp_path, capsys):
    outs, m = offline
    runner.save(outs, m, runner.check_gates(m, runner.load_gates()["auto"]), tmp_path, {"provider": "offline"})
    assert main(["gate", str(tmp_path)]) == 1                                  # the no-AI floor does not meet the quality bar: release blocked
    assert "RELEASE BLOCKED" in capsys.readouterr().out
    passing = {"meta": {}, "metrics": m, "gates": [{"gate": "x", "op": ">=", "threshold": 1, "value": 1, "ok": True}]}
    (tmp_path / "metrics.json").write_text(json.dumps(passing))
    assert main(["gate", str(tmp_path)]) == 0
