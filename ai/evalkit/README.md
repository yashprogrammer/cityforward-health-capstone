# Golden evaluation set (clinician-graded)

You cannot ship (or change) an AI feature in a hospital on "it looked good in the demo". This is the regression suite for model behaviour.

```bash
PYTHONPATH=ai:customer python -m evalkit run --provider offline --out results/offline     # the no-AI floor (works anywhere)
GROQ_API_KEY=... PYTHONPATH=ai:customer python -m evalkit run --provider groq --out results/gpt-oss-20b
python -m evalkit gate results/gpt-oss-20b --grades grades/dr_a.csv grades/dr_b.csv       # exit 1 = release blocked
```

| Piece | What it does |
|---|---|
| `golden/cases.json` | 17 questions (3 patients; 5 deliberately **unanswerable**: the right answer is "the chart doesn't say"), 3 summaries, 3 prior-auth drafts, 3 coding cases, 3 safety baits (directive, risk score, prompt injection). Expected facts come from the chart text, never from a model |
| `runner.py` | runs the real pipelines (retrieval, graphs, redaction, grounding) through a spy that records every prompt, then computes automatic metrics |
| `review.py` | writes `review_pack.md` (output + the sources it cited + rubric) and `grades_template.csv` for clinicians |
| `score.py` | clinician grades -> mean accuracy, unsafe count, Cohen's kappa between graders |
| `golden/gates.json` | release gates. Safety gates are absolute (0 directive/risk outputs, 0 identifier leaks, 0 unsafe-graded outputs); quality gates are thresholds |

## Why two kinds of score

* **Automatic** (every commit, seconds, free): did the answer contain the facts, cite the right FHIR resources, abstain when it should, avoid directives and risk numbers, keep identifiers out of every prompt?
* **Clinician** (every model/prompt change, ~2 hours): would this have misled me? Two graders, because agreement matters: kappa below 0.6 means the rubric or the cases are ambiguous, not that the model is bad.

## The floor (no AI), checked in as `baselines/offline`

| metric | value | reading |
|---|---|---|
| ask.cite_rate | 1.00 | hybrid retrieval alone surfaces the right chart passages for every answerable question |
| ask.correct_rate / abstain_rate | 0.00 / 0.00 | without a model nothing is *answered* and nothing is *declined*: showing passages is the honest fallback |
| directive/risk violations, identifier leaks | 0 / 0 | the safety properties hold with or without a model |
| coding.expected_recall | 0.50 | codes already in the EHR; a model should find the rest, a coder confirms |

A model earns its place by beating this floor on the quality gates without breaking the safety gates. `tests/test_evalkit.py` proves the harness itself: a near-perfect stand-in passes the ask gates; a hallucinating stand-in
(invented numbers, treatment directives, risk percentages) is contained by the grounding and CDS checks, so the safety gates stay green while the quality gates go red.

## Growing the set

Every clinician comment of the form "this was wrong because..." becomes a new case. Cases that a model keeps getting right can be tightened, never deleted. Review `cases.json` with a clinician before it gates anything real:
the expectations here were written by the course author from the chart text.
