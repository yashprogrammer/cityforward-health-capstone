# Live incident drill: "What almost went wrong"

A 45-minute session for the course's incident lecture (also usable as a hospital tabletop). Synthetic patients only; no model is called.

```bash
PYTHONPATH=ai:customer:. python -m incident.drill --pause      # press Enter between phases, talk in between
PYTHONPATH=ai:customer:. python -m incident.drill              # the whole report at once
```

## The story

A developer improves how prompts are assembled. The change is reviewed, tests pass, and nothing crashes. It also quietly skips the de-identification step in the model gateway. Clinicians keep using the dashboard; the feature "works". Identifiers flow to the model provider.

## Phases (each is a function in `drill.py`)

| Phase | What the room does | Teaching point |
|---|---|---|
| T0 baseline | read the clean prompts | you need a known-good baseline to recognize bad |
| T1 the bad deploy | watch features keep working while names, MRNs and dates appear in prompts | the dangerous failures are silent |
| T2 detection | compare the **canary** (fires in minutes, needs no traffic) with the **metric alert** (needs real traffic) | monitor the property you care about directly, not a proxy; an alert on a *total* masks a *partial* failure (our first version of the alert did) |
| T3 containment | switch the AI off | fail closed: every feature has a labelled no-AI mode, so containment costs convenience, not care |
| T4 scoping | reconstruct who was exposed from metering and audit | prompts are deliberately not stored, so scope is by patient, time and purpose: design logging for the incident you will have |
| T5 fix and verify | restore the redactor, re-run the leakage scan and canary | a fix without a test that fails without it is a promise |
| T6 report | fill the breach-risk worksheet, compute the notification deadline | the covered entity decides; the vendor's job is to give it facts quickly |

## Debrief questions

1. Which control would you have trusted to catch this? Which actually caught it first?
2. What did the alert on a *sum* of redactions miss? (Run the first version against mixed healthy/broken traffic.)
3. Why do we not store prompts for later forensics? What would it cost in a breach if we did?
4. The hospital's privacy officer asks "how do you know it was only three patients?": what is your evidence, and what is it missing?
5. After this drill the gateway constructor refuses a real model client without a redactor. What is the *next* way someone could bypass de-identification? (Hint: a second code path that builds its own HTTP client to the model.)

## What changed in the product because of the drill

* `copilot/canary.py` and the `RedactionCanaryFailed` alert
* the alert on unredacted calls (`copilot_unredacted_calls_total`) replaces the one that summed redactions
* `Gateway.__init__` refuses a real model client without a redactor
* playbook P1 and the tabletop in `docs/compliance/incident-response-plan.md`
