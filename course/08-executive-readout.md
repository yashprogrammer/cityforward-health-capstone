# Executive readout to the CMIO

You record this (the assignment requires it). It is a decision meeting, not a tour: the CMIO should leave knowing what you built, what it did on the measures you agreed, what could go wrong and how you contain it, and exactly what you are asking her to decide.

**Length:** 12 minutes plus questions. **Audience:** Dr. Feldman (CMIO), with Marcus Webb, the CISO and revenue cycle in the room. **Rule:** every claim on a slide has its evidence on the same slide or in the appendix; anything you did not measure is labelled *not yet measured*.

## Storyline

| # | Slide | The one sentence | Evidence on the slide |
|---|---|---|---|
| 1 | **Decision requested** | "I am asking you to approve a four-week supervised pilot on 4 West and 5 South, starting with synthetic-then-real data after the security review closes." | the ask, the date, the owners |
| 2 | **The problem we agreed to fix** | "Insured patients wait 6.3 hours after discharge is advised, versus 2.5 for self-pay, and about 38% of authorization requests come back with a documentation query." | the MIS numbers and the five query reasons |
| 3 | **What we built** | "Before anyone asks, the system prepares a cited stay summary and the prior-authorization evidence package; clinicians attest and approve; a coordinator submits." | one architecture picture: EHR → events → agents → human review → payer |
| 4 | **How it behaves** | "It cites the chart for every statement, shows its rule for every flag, declines to answer what the chart does not say, and falls back to plain passages when it cannot be sure." | a 30-second recording: the Hale summary, the Why panel, the unanswerable question |
| 5 | **Results against the agreed measures** | table below | your evaluation run, with graders named |
| 6 | **What we protect and how we know** | "No identifier leaves in a prompt, every access is recorded tamper-evidently, access follows role and unit, and none of this depends on trusting the model." | leakage test over all patients, audit chain verification, row-level security, the incident drill result |
| 7 | **What could go wrong** | "The model can omit something or read a note wrongly; people can over-trust fluent text." | the risk table with owner and control; the FDA boundary (no decisions, no predictions) |
| 8 | **What we have not proven** | honest list | below |
| 9 | **Cost and operations** | "About a cent per discharge in model cost; the real cost is the review time we ask of two clinicians; here is who is on call." | the budget table; the runbook and SLOs |
| 10 | **The pilot plan and your decision** | three options: approve as proposed / approve with a narrower scope / not now, with what you would need to see | dates, gates for expansion |

## Slide 5: results table (fill from your runs; do not round up)

| Measure (agreed in scope) | Baseline | Target | Result | Evidence |
|---|---|---|---|---|
| Insured TAT | 6.3 h | ≤ 4.8 h | *pilot not yet run* | measured during the pilot from the MIS report |
| Authorizations queried | ~38% | ≤ 25% | *pilot not yet run* | payer response log |
| Coordinator assembly time | measured in week 2: ___ | −40% | ___ on N=10 requests in simulation | stopwatch study with Carol |
| Clinician-graded accuracy | n/a | ≥ 4.2 / 5 | ___ (two graders, N=___ outputs, kappa ___) | `evalkit` grades |
| Unsafe outputs (clinician-graded) | n/a | 0 | ___ | `evalkit` grades |
| Answers grounded (cited, numbers verified) | n/a | 100% shown | by construction; fallback rate ___% | `copilot_fallback_total` |
| Identifier leaks to the model | n/a | 0 | 0 across all ___ patients | `ai/tests/test_redact.py` |
| Unanswerable questions correctly declined | n/a | ≥ 90% | ___ | golden set |

The two rows labelled *pilot not yet run* are the honest ones: the readout is for a decision to **run** the pilot, so the outcome measures are targets, and the evidence you can show is about safety and behaviour.

## Slide 8: what we have not proven (say it before they ask)

* Outcome measures (TAT, query rate) on real workflows. They need the pilot.
* Accuracy on real, messy notes at volume. The golden set is small and was written from synthetic charts; clinicians will extend it.
* De-identification of free text is best-effort; a BAA with the model provider is still required before real data.
* No penetration test, no independent assessment (SOC 2/HITRUST); the controls are evidenced, not certified.
* Operational config (Kyverno, Falco, the Terraform landing zone) is written and statically checked but has not run in your cluster yet.

## Speaker notes by slide (the parts people get wrong)

1. Start with the ask. A CMIO who hears the decision in the first 30 seconds listens to the rest differently.
2. Use *their* numbers and credit their report. Do not introduce a new metric.
4. Show the refusal and the fallback as features. The ChartBot memory is why.
5. Read the table including the empty cells. "Not yet" is a strength when it is honest.
6. One concrete demonstration (the leakage test or the audit chain) is worth ten adjectives.
7. Name the owner of each risk. A risk without an owner is a worry.
9. Money is not the argument here. Say so and move on to who is accountable at 2 a.m.

## Questions to rehearse

| Question | A good answer |
|---|---|
| "What happens when it is wrong?" | "A clinician sees the source next to every statement and decides; wrong outputs are reported in the dashboard, become golden-set cases, and block release until fixed. The safety gate is zero outputs graded unsafe." |
| "Will clinicians trust it?" | "Trust is earned by checkable behaviour: citations, refusals, and a visible fallback. We measure usage and feedback monthly, and we expect some clinicians to ignore it." |
| "Why not a bigger model?" | "It is a one-line change and a re-run of the golden set. The quality gain is measured, not assumed; the compliance cost of a different provider is larger than the price difference." |
| "Is this a medical device?" | "We designed it so it is not: no diagnosis, no prediction, no treatment recommendation, sources always visible, clinicians only. Here is the memo; your counsel should confirm." |
| "What if we want it to predict readmissions?" | "That would be a different regulatory category and project. I can show you what it would take." |
| "Who owns it after you leave?" | "The handoff pack names owners for each component, the on-call, the release process and the change policy." |
| "What does it cost?" | "About a cent per discharge in model cost. The real cost is two clinicians' review time and IT's operations: here are the hours." |

## Recording checklist
Slides readable at 720p; your face visible for the opening and closing; the demo clip full screen; no real patient data anywhere on screen; captions on; under 15 minutes; file named `readout-<yourname>.mp4`.
