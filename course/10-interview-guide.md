# Presenting this project in an interview

For a forward deployed engineer role, this project is your strongest evidence of the thing interviewers actually probe: **can you take an ambiguous customer problem to a deployed, defensible system, and tell the truth about it?** The code matters less than how you talk about the decisions.

## Three versions of the story

**30 seconds.** "I took a hospital's discharge delay (insured patients waited 6.3 hours versus 2.5) and built an AI layer that prepares cited summaries and prior-authorization evidence for clinicians to approve. The hard parts were not the model: it was integrating through FHIR and SMART, keeping patient identifiers out of prompts, proving the system could not invent a number, and getting through a hospital-style security review."

**5 minutes.** Problem and baseline → the wedge and why (not the scribe, not the risk score) → architecture in one picture → the three design decisions you are proudest of (below) → what you would do next.

**15-minute deep dive (demo order).** (1) The issue tree and wedge memo, 2 min. (2) Launch from the EHR; the dashboard, a flag with its rule, a summary with the Why panel, 3 min. (3) The refusals and fallbacks (unanswerable question, model switched off), 2 min. (4) The grounding test and the redaction leakage test, 3 min. (5) Prior auth end to end with the human steps, 3 min. (6) The incident drill and what changed because of it, 2 min.

## The decisions to be able to defend (each with the trade-off)

| Decision | Why | What it cost / the alternative |
|---|---|---|
| Wedge = discharge readiness, not a scribe or risk score | measurable, allowed, reachable; avoids the FDA device boundary and the previous vendor's wound | smaller story than "ambient AI"; I chose trust over ambition for engagement one |
| **Code verifies the model** (citations, numbers, code validity) with fallbacks | a fluent wrong sentence is the failure that hurts clinicians | extra engineering, some outputs fall back to plain passages: I treat the fallback rate as a metric |
| Humans attest, approve, sign; software prepares | legal and clinical accountability; CDS exemption | slower than full automation; the throughput gain is smaller but defensible |
| De-identify at the model gateway, the only door to a model | one choke point you can test (leakage test over every patient) and monitor (canary) | best-effort on free text, so a BAA is still required: I say so |
| Row-level security in the database, not only in the API | a missed check in code should not be a breach; fails closed | more complexity in sessions and tests |
| Deterministic supervisor over three agents | routing is a rule; no model needed; testable | less "agentic"; I can name when I *would* add a planner |
| Golden set with clinician grading and absolute safety gates | "it looked good in the demo" is not a release criterion | clinicians' time is the real cost |
| Everything degrades to a labelled no-AI mode | the clinical workflow must not depend on a model's uptime | more paths to test |

## Stories that show judgement (STAR format)

1. **The alert that missed a partial failure.** Situation: during the incident drill the alert on "redactions per hour" stayed silent while half the calls were unredacted. Task: detect a *partial* bypass. Action: replaced it with a per-call counter and added a canary that tests the live gateway without traffic. Result: detection in minutes, no dependence on volume. Lesson: alert on the property, not a proxy.
2. **"The retrieval failed on a synonym."** The evaluation caught that "oxygen saturation" did not find "SpO2" with a lexical embedder. I added query expansion for clinical abbreviations and a golden case, rather than swapping models blindly. Lesson: measure, then fix the cause.
3. **Saying no to a risk score.** A stakeholder asked for a readmission badge. I walked through the four FDA criteria, showed it would make the product a device, and offered the compliant alternative (documented facts with sources). Lesson: a good FDE explains *why*, and offers a path.
4. **A test that found a real bug.** (Pick your own; every learner's repo has one.)

## Questions you will be asked

| Question | Strong answer, in outline |
|---|---|
| How do you handle hallucinations in a clinical setting? | Don't rely on the model: require citations, verify every number in code, show sources, decline when evidence is missing, fall back to plain passages, measure fallback and omission rates, clinician-grade a golden set, gate releases on zero unsafe outputs. Be honest that omissions cannot be fully prevented. |
| How do you keep PHI out of the model? | Minimum necessary retrieval, de-identification at a single gateway (exact-match of the patient's own identifiers, patterns, date offsets), a test over every patient, a live canary, no prompt logging, and a BAA anyway because free text is best-effort. |
| What if the hospital will not allow any external model? | The gateway is provider-agnostic; run a model in their boundary (vLLM on their GPUs) and set the in-boundary flag; the evaluation tells you what quality you give up; the no-AI mode still delivers retrieval, flags and templates. |
| Is it a medical device? | Designed as non-device CDS plus administrative support: four criteria, enforced in code and tests, with the list of what would change that. Counsel confirms. |
| How would you scale it to 20 hospitals? | Per-tenant isolation (database per hospital), the same chart with per-hospital values, policy-as-code, a shared evaluation harness with per-site golden sets, centralized observability with PHI scrubbed. |
| What would you do differently? | Start the security pack in week one; measure the baseline before building anything; involve clinical graders earlier; write the incident drill sooner. |
| What is not done? | Say the gaps list: no BAA, no pen test, operational configs not yet run in a real cluster, small golden set. Interviewers trust candidates who name gaps. |

## Do not claim
"HIPAA compliant", "FDA approved/cleared", "production-ready", "clinically validated", accuracy percentages you did not measure, or anything about real patients. Say "designed to support", "evidence for", "in a synthetic environment".

## Package it
A repository with tagged sessions and a README that tells the story in 10 lines; a 3-minute recording of the demo; the executive readout recording; one page with the decisions table above; a short architecture picture. Make the first screen of the repo answer "what is this and why should I care".
