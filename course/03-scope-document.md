# Scope document: CityForward Health, Discharge Readiness (pilot)

*A scope document is a contract with yourself and the customer about what "done" means. It is short, specific, and signed by the sponsor. This is the filled-in example for the capstone; the template headings are what you reuse.*

| | |
|---|---|
| Customer / sponsor | CityForward Health / Dr. Naomi Feldman, CMIO |
| Engagement lead (FDE) | you |
| Pilot units | 4 West Cardiac Telemetry, 5 South Orthopaedics |
| Duration | 12 weeks to pilot go-live, then 4 weeks of supervised use |
| Status | draft for sponsor sign-off |

## 1. Objective (one sentence, measurable)
Reduce the time insured patients wait between "discharge advised" and leaving, and the share of prior-authorization requests returned with a documentation query, **without changing who makes any clinical or authorization decision**.

## 2. Success metrics (baseline is the MIS report; targets are proposals to be agreed)
| Metric | Baseline | Pilot target | How measured |
|---|---|---|---|
| Insured discharge TAT (hours, advised → departure) | 6.3 | ≤ 4.8 on pilot units | MIS report, weekly |
| Authorizations returned with a documentation query | ~38% | ≤ 25% | payer response log |
| Coordinator time to assemble a request | to be measured in week 2 (shadowing) | −40% | time study + audit timestamps |
| Clinician-graded accuracy of AI outputs | n/a | ≥ 4.2 / 5 mean, **zero outputs graded unsafe** | golden evaluation set, two graders |
| Outputs that fail grounding checks and fall back | n/a | tracked, alert at > 30% | Prometheus |

## 3. In scope
1. A chart-grounded, cited stay summary for the clinician taking over care.
2. A prior-authorization drafting assistant: assembles evidence per payer requirement; a clinician attests and approves; a coordinator submits.
3. ICD-10-CM coding suggestions for the HIM coder, with quotes from the chart; the coder decides.
4. A SMART on FHIR dashboard launched from inside MediTrack (timeline, rule-based flags with a "Why" panel).
5. Events from MediTrack (discharge advised, discharged, document added) trigger preparation of the above.
6. The controls the hospital requires: PHI de-identification before any model call, audit of every access, role- and unit-based access control, a security review pack, deployment into the hospital's Kubernetes.

## 4. Out of scope (said out loud, so nobody is surprised)
* Any recommendation of treatment, any diagnosis or risk score (this keeps the product outside FDA device regulation; see the CDS memo).
* Writing diagnoses, orders or results into the chart. The only write-back is a labelled PDF packet.
* Autonomous submission to a payer, or attestation by software.
* Patient- or family-facing features.
* Ambient scribing, imaging, waveform analysis.
* Replacing MediTrack, or changing its database.
* Real PHI before the security review is passed and a BAA covers the model provider.

## 5. Assumptions (each has an owner; if one is false, the plan changes)
| # | Assumption | Owner | Check by |
|---|---|---|---|
| A1 | MediTrack's FHIR API, SMART launch and webhooks are enabled in the pilot environment | IT (S. Whitlock) | week 1 |
| A2 | The hospital provides identity (SSO) for clinicians with unit assignment | IT / CISO | week 2 |
| A3 | A model endpoint covered by a BAA is available before real data flows | CISO / procurement | week 6 |
| A4 | Two clinicians can grade 30+ outputs for two hours every release | CMIO | week 4 |
| A5 | The payer's prior-authorization policies for the four services are available in machine-readable or published form | revenue cycle | week 2 |

## 6. Constraints
HIPAA minimum necessary and BAA; HITRUST-based security review (questionnaire, threat model, incident plan, vendor register); hospital change windows (Tuesday 02:00-04:00); no PHI in non-production; data stays in the agreed region; the clinical safety officer can pause the feature at any time.

## 7. Dependencies and risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Free-text notes too sparse for the payer's evidence | medium | high | measure in week 2; the assistant reports gaps instead of guessing |
| Model provider has no BAA in time | medium | high | Foundry under the Microsoft BAA, or a model inside the VNet; synthetic data until then |
| Clinicians over-trust fluent text | medium | high | sources beside every statement, no confidence numbers, human sign-off, training |
| Security review takes longer than the build | high | medium | start it in week 1 with the pre-filled pack |
| Scope creep toward "predict readmission" | high | high | out-of-scope list above; the CDS memo says why |

## 8. Acceptance criteria (the demo that ends the pilot build)
1. A clinician launches the dashboard from MediTrack and sees a cited summary and flags with their rules.
2. A discharge-advised event produces prior-authorization drafts with gaps listed; a coordinator and a clinician complete and submit one; the payer's response is recorded and the packet appears in the chart.
3. The security pack is complete and the hospital's reviewer has no open critical findings.
4. The golden evaluation set meets its gates, including zero unsafe outputs from the clinician grading.
5. The system runs in the hospital's cluster, observed, with runbooks and an on-call named.

## 9. Governance
Weekly 30-minute playback with the sponsor; a clinical safety review before any pilot unit goes live; change control: any model, prompt or retrieval change is re-evaluated on the golden set before release.

**Sign-off:** ____________________ (CMIO)   ____________________ (CISO)   ____________________ (FDE)
