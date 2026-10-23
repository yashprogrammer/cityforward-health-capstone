# Scoping the wedge

A **wedge** is the narrowest piece of the problem that (a) delivers value someone can measure within weeks, (b) is allowed, (c) can be built with the data and integration that exist, and (d) earns the access and trust to do the next, bigger thing. A good wedge is boring to describe and hard to argue with.

## Candidates

| # | Candidate | Pain | Measurable in weeks? | Data ready? | Regulatory / compliance exposure | Integration effort | Champion |
|---|---|---|---|---|---|---|---|
| 1 | Patient-history summarizer (the POC) | medium (everyone) | weak (time saved is hard to audit) | yes (notes, results) | low-medium (CDS, must be cited) | low | CMIO |
| 2 | **Discharge readiness: cited stay summary + prior-auth evidence assembly** | **high (6.3 h vs 2.5 h; 38% queries)** | **yes (MIS report, payer log)** | partly (free text; measurable gap) | low (administrative + CDS with human sign-off) | medium (FHIR read, events, PDF write-back) | CMIO, Webb, Hughes |
| 3 | Ambient scribe | high | yes | audio, new infrastructure | high (clinical documentation; many incumbents) | high | CMIO (but burned by ChartBot) |
| 4 | Readmission risk prediction | medium | slow (months) | needs history and outcomes | **high: a risk score is a device** | medium | CMIO (but out of bounds) |
| 5 | Coding assistant (inpatient) | medium (HIM backlog) | yes (query rate, days in A/R) | yes | low (administrative) | low | HIM |
| 6 | Denial management | high (money) | yes, but lagging by months | claims data we do not have | low | high (billing system) | revenue cycle |

Scoring (1 = poor, 5 = excellent; weights reflect that this is a first engagement where trust matters more than ambition):

| Criterion (weight) | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|
| Pain (3) | 3 | 5 | 5 | 3 | 3 | 5 |
| Measurable soon (3) | 2 | 5 | 4 | 2 | 4 | 2 |
| Data ready (2) | 5 | 3 | 1 | 2 | 4 | 1 |
| Compliance headroom (3) | 4 | 4 | 1 | 1 | 4 | 4 |
| Integration effort (2, higher = easier) | 5 | 3 | 1 | 3 | 5 | 1 |
| Champion strength (2) | 4 | 5 | 2 | 1 | 3 | 3 |
| **Weighted total (max 75)** | 55 | **64** | 38 | 30 | 57 | 43 |

## Decision

**Wedge = candidate 2: discharge readiness.** On two pilot units (4 West cardiac, 5 South ortho) prepare, before anyone asks, a **cited stay summary** and the **prior-authorization evidence package** for the services the plan will want, with a clinician attesting and approving and a coordinator submitting.

Why it wins: it attacks the largest controllable part of the 3.8-hour gap (see the issue tree: evidence assembly and the payer rework loop), it has two numbers that already exist in the customer's own report, it needs no clinical judgement from software (so the FDA boundary is comfortable), and ChartBot's wound is addressed by the design itself (every statement cited, every number checked, a human decides).

Why not the others: the summarizer alone proves nothing the customer can count (it becomes the POC and the first piece of the wedge); the scribe and the risk score are where the previous vendor and the regulators bite; denial management is real money but needs claims data and a billing integration we do not have; coding scores a close second (57) and is the natural **second** step (HIM is a supportive stakeholder and the work reuses the same retrieval and checks).

## Expansion path (what the wedge earns)

1. Wedge: summary + prior-auth packages on two units (weeks 1-12).
2. Coding worksheet for HIM and the clinical dashboard inside MediTrack (weeks 12-20): same data, new users.
3. More units and more services (skilled nursing, home health), then payer-specific rules.
4. Only then, with clinical governance in place, discuss anything predictive (and expect it to be a regulated project).

## What would make us change the decision
* Week-2 shadowing shows that evidence is mostly *absent*, not scattered: then the wedge is documentation prompts at the point of care, not assembly.
* The CISO cannot approve a model endpoint within eight weeks: ship the no-AI mode first (retrieval, flags, templates), which is already part of the design.
* The payer refuses structured submission: keep assembling, submit through the portal.
