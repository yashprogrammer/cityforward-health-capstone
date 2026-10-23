# Handoff

You are not done when it works; you are done when the hospital can run it without you. A handoff is a transfer of **ownership**, with evidence that each owner can do the job.

## 1. Ownership (RACI: Responsible, Accountable, Consulted, Informed)

| Area | Responsible | Accountable | Consulted | Informed |
|---|---|---|---|---|
| Clinical safety of AI outputs, pause authority | Clinical safety officer | CMIO | pilot clinicians, vendor | CNO |
| Golden set and clinician grading | two named clinicians | CMIO | FDE, vendor | HIM |
| Application operations (deploy, scale, on-call) | hospital platform team (S. Whitlock) | IT director | vendor (L3 support) | CMIO |
| Security: access reviews, vulnerabilities, incidents | CISO team | CISO | vendor security | CMIO, privacy officer |
| Privacy: break-the-glass review, breach assessment | privacy officer | privacy officer | CISO, counsel | CMIO |
| Model and prompt changes | vendor engineering | vendor product owner | CMIO, clinical safety officer | platform team |
| Payer rules and prior-auth workflow | revenue cycle (C. Hughes) | revenue cycle director | vendor | CMIO |
| EHR integration (FHIR, SMART, events) | EHR team | IT director | vendor | platform team |
| Vendor management (BAA, annual assessment) | procurement / CISO | CISO | vendor | CMIO |

## 2. What you hand over

| Artifact | Where | Owner accepts when |
|---|---|---|
| Running system in the hospital cluster | `deploy/` (Argo CD app, Helm values, Terraform) | they can redeploy it from git without you |
| Runbooks: restart, scale, rotate a secret, replay events, switch the AI off, restore the database | `docs/compliance/incident-response-plan.md`, `deploy/README.md` | the on-call has executed each once in a drill |
| Dashboards and alerts | `deploy/platform/monitoring/` | alerts route to their pager and have been test-fired |
| Release and change policy | below | the platform team has run one release |
| Golden evaluation set and grading process | `ai/evalkit/` | two clinicians have graded a pack and the gates ran |
| Security pack (mapping, threat model, IR plan, vendor register, questionnaire) | `docs/compliance/` | the CISO has reviewed and recorded exceptions |
| Training: a 30-minute clinician session; a 60-minute coordinator/coder session; an admin session | slides + the About panel text | attendance recorded; clinicians can state what the tool does **not** do |
| Known limitations | below | read and acknowledged by the CMIO |

## 3. Service levels (proposed)

| Item | Target |
|---|---|
| Availability of the *clinical workflow* | independent of the AI (every feature has a no-AI mode) |
| AI features availability | 99.5% monthly, measured at the ingress |
| Interactive latency | p95 ≤ 8 s (summary, ask); background features ≤ 60 s |
| Audit log | zero unrecorded accesses; verification job daily |
| Support | L1 hospital service desk; L2 platform team; L3 vendor within 4 business hours for SEV2, 1 hour for SEV1 |

## 4. Release and change policy
1. Any change to **code, model, prompt, retrieval settings or the de-identifier** is a release.
2. A release needs: CI green (tests, scans), golden-set automatic gates passing, and, for a model/prompt/retrieval change, a clinician grading pass on the changed behaviour.
3. Deployed through Argo CD from a signed image; rollback is a git revert.
4. A silent provider-side model update is a change: re-run the golden set when the provider announces one, and monthly regardless.
5. Emergency change (security fix) may skip clinician grading but not the safety gates; document and review within five business days.

## 5. Operations calendar
Daily: audit chain verification, review dead-lettered events. Weekly: usage and spend, fallback rate, break-the-glass queue. Monthly: golden set re-run, clinician feedback review, access review, dependency updates. Quarterly: restore drill, incident tabletop, vendor register review. Annually: penetration test, vendor re-assessment, threat model review.

## 6. Known limitations (read these out in the handoff meeting)
* Free-text de-identification is best effort; the BAA is the real control.
* The assistant can omit a relevant finding; rule-based flags are independent of the model but narrow.
* Quality on your real notes is not yet measured; the golden set must grow from the pilot.
* Payer integration uses the payer's API key model of the mock; production payers use OAuth2/mTLS profiles.
* The embedding model in the default image is lexical; semantic search needs the `FASTEMBED=1` build.
* Some operational configuration has not run in your cluster yet (see `deploy/README.md`).

## 7. 30 / 60 / 90 days after handoff
| By | Outcome |
|---|---|
| 30 | pilot units live under supervision; weekly metrics reviewed; first golden-set additions from real feedback |
| 60 | restore drill done; incident tabletop done; first model/prompt release through the full process |
| 90 | expansion decision using the agreed gates; vendor assessment refreshed; named internal owner takes over L3 triage |

## 8. Handoff checklist (sign each line)
- [ ] Each owner above has accepted in writing
- [ ] On-call executed: switch AI off, restore database, rotate a secret, replay an event
- [ ] Alerts test-fired to the real pager
- [ ] CISO exceptions recorded in the vendor register
- [ ] Clinical safety officer holds the pause switch and knows how to use it
- [ ] Training attendance recorded
- [ ] Credentials: vendor accounts removed or time-boxed; break-glass for vendor support defined
- [ ] This document updated with the owners' names and the date
