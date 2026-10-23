# What almost went wrong (live incident)

**Where:** tag `incident`: `incident/drill.py`, `incident/README.md`, `ai/copilot/canary.py`, playbook P1 in `docs/compliance/incident-response-plan.md`.

A 45-minute live session. The instructor runs `python -m incident.drill --pause`; the room plays the response team.

## The incident
A reasonable-looking change wires the model gateway without its de-identification step. Nothing crashes. Features work. Identifiers flow into prompts.

## Phases and the questions to ask at each
| Phase | Ask the room |
|---|---|
| T0 baseline | "How would you know what normal looks like?" |
| T1 the bad deploy | "Which of our tests would have failed? Why didn't they run?" |
| T2 detection | "Which signal fires first, the canary or the metric alert? Which one needs traffic?" (The first alert summed redactions over all traffic and stayed silent while half the calls were unredacted.) |
| T3 containment | "What is the safest action in the first ten minutes, and what does it cost us?" (Switch the AI off; every feature has a labelled no-AI mode.) |
| T4 scoping | "We do not store prompts. How do we know who was exposed?" (Metering + audit: patients, purposes, window.) |
| T5 fix | "What test would have prevented it? What guard makes the bug a startup error?" |
| T6 report | "What does the privacy officer need, and when does the 60-day clock start?" |

## Debrief themes
Monitor the property, not a proxy; design logs for the incident you will have; fail closed; a fix without a test is a promise; the covered entity decides on notification, the vendor supplies facts fast.

## Variations for repeat runs
* Cross-patient retrieval: a query that omits the patient filter (row-level security should return nothing; discuss what happens if the service connected as a superuser).
* A leaked service key (playbook P3): rotate, re-register, review the audit window.
* A prompt injection planted in a note (playbook P4).
