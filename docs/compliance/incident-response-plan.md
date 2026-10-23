# Incident response plan

Scope: the CityForward Copilot and its supporting infrastructure, used by a covered entity (the hospital) with the vendor as a business associate.
In the course project the data is synthetic; the plan is written as if it were not, because that is the point of the exercise.

## 1. Roles

| Role | Who (example) | Duties |
|---|---|---|
| Incident commander (IC) | vendor on-call engineering lead | owns the incident until closed; declares severity |
| Security lead | vendor security | evidence, containment decisions, forensics |
| Privacy officer (hospital) | hospital compliance | breach risk assessment, notifications |
| Clinical safety officer (hospital) | CMIO or delegate | judges whether an AI output reached patient care |
| Communications | vendor account lead | one voice to the hospital; no speculation |
| Scribe | anyone not the IC | timeline with timestamps (UTC) |

## 2. Severity

| Level | Definition | Response |
|---|---|---|
| SEV1 | confirmed or likely unauthorized disclosure of PHI, loss of audit integrity, wrong AI output known to have reached patient care | page now; hospital privacy officer informed within 4 hours |
| SEV2 | control failure without confirmed disclosure (redaction off, RLS policy missing, secret exposed but unused), sustained outage of the EHR integration | respond within 1 hour; hospital informed within 24 hours |
| SEV3 | degraded quality (fallback rate high), single failed event, vulnerability without exploit | next business day |

## 3. Principles

Stop the bleeding first, preserve evidence second, understand third. Prefer **failing closed**: switching the AI off (`LLM_PROVIDER=offline`) is always safe; every feature keeps working in its labelled no-AI mode.
Do not delete data or restart things before the evidence is saved.

## 4. Detection and triage

| Signal | Source | First actions |
|---|---|---|
| `AuditLogWriteFailing` | Prometheus (`copilot_audit_failures_total`) | **SEV2 at once.** Access is happening unrecorded. Check database health and disk; if not fixed in 15 minutes switch the service to read-only maintenance mode (scale to 0 behind the ingress banner). Run `GET /api/audit/verify` after recovery and note any gap in the timeline |
| `RedactionCanaryFailed` | the redaction canary (`ai/copilot/canary.py`), every 5 minutes against the live gateway | **SEV2, fires without any traffic.** Same first action as the next row |
| `RedactionStoppedWorking` | Prometheus (`copilot_unredacted_calls_total`: model calls that went out with nothing redacted) | **SEV2.** Set `LLM_PROVIDER=offline` immediately. Identify the window from `llm_call` (`redactions = 0`) and the audit trail; treat prompts in that window as disclosed to the model provider and go to playbook P1 |
| `GET /api/audit/verify` returns `ok: false` | scheduled check | **SEV1.** Preserve the database (snapshot), identify `first_bad_id`, go to playbook P2 |
| Falco: private key read, shell in container, unexpected egress | Falco | **SEV1** for key read or egress to a non-allowed destination; **SEV2** for a shell. Isolate the pod (apply a deny-all NetworkPolicy to its label), keep it running for forensics |
| `UnusualAccessDenials` / `BreakGlassUsed` | Prometheus, audit | SEV3 review by the privacy officer; escalate if one user is repeatedly denied across many patients |
| `EventDeadLetters` | Prometheus | SEV3. Replay from the events admin; if the EHR is down, hospital IT is the owner |
| Clinician reports a wrong AI statement | support, dashboard feedback | Clinical safety officer triages: did it reach a decision? Add the case to the golden set; SEV1 if harm or near-miss reached care |

## 5. Playbooks

**P1 - PHI may have reached the model provider.** (a) switch AI off; (b) establish scope from `llm_call` (time window, purposes, token counts) and the audit trail (which patients were in scope): prompts are not stored, so scope is by patient and time; (c) ask the provider for deletion/confirmation under the contract; (d) privacy officer performs the breach risk assessment (section 7); (e) fix the cause and add a test that fails without the fix (the redaction leakage test over all patients is the model); (f) re-enable AI only after the golden set and the leakage test pass.

**P2 - Audit chain broken or audit writes lost.** (a) snapshot the database; (b) recompute from the last verified chain head (anchored copy); (c) compare with Kubernetes audit logs, ingress logs and Key Vault logs to rebuild what happened; (d) determine whether the cause was a fault or a person (database privileges, trigger disabled events in the PostgreSQL log); (e) if a person with privileges is implicated treat as SEV1 insider incident with HR and legal.

**P3 - Credential or key compromise** (SMART backend key, webhook secret, signing key, model API key, database password). (a) rotate in Key Vault; (b) for the SMART backend key: register the new public key at the EHR and deactivate the old client (the EHR rejects the old key immediately); (c) webhook secret: set `AI_WEBHOOK_SECRET_PREVIOUS` to the old value for a short overlap, deploy, then remove it; (d) review the audit trail for the exposure window; (e) find how it leaked (git history scan is in CI).

**P4 - Prompt injection / data exfiltration attempt.** Falco "unexpected outbound" or a model output containing identifiers it should not know. (a) isolate the pod; (b) pull the offending document (`source_ref` is in the audit/trace) and quarantine it from indexing; (c) check that the output checks held (grounding, lint); (d) add the document text as a golden injection case.

**P5 - Wrong AI output reached patient care.** Clinical safety officer leads. (a) identify the patient and clinician from the audit trail; (b) clinical review and, if needed, disclosure under the hospital's patient-safety policy; (c) reproduce with the saved evidence (the output's sources are stored with it); (d) fix, add to the golden set, re-run release gates; (e) consider reporting to the hospital's patient-safety organization. If the output was a directive or prediction, the CDS lint failed: that is also a regulatory finding (see [fda-cds-memo.md](fda-cds-memo.md)).

**P6 - Ransomware / destructive attack.** Isolate namespaces with deny-all policies; restore the database from point-in-time backup into a new server; rebuild the cluster from Terraform and Argo CD (git is the source of truth); signed images mean rebuilt workloads can be trusted. Hospital downtime procedures apply to the EHR itself.

## 6. Evidence preservation

Save before changing: pod logs and events, `GET /api/audit/verify` output, database snapshot, Falco alerts, ingress logs, Key Vault and PostgreSQL logs from Log Analytics (retention 365 days), the relevant git commit and image digest (`kubectl get pod -o jsonpath='{..imageID}'`). Hash the evidence bundle and record who handled it.

## 7. Breach assessment and notification (HIPAA Breach Notification Rule, 45 CFR 164.400-414)

1. The vendor (business associate) notifies the covered entity **without unreasonable delay and no later than the contract states (target: 24 hours from discovery of a SEV1)**; the contract may be stricter than the rule.
2. The covered entity decides whether it is a reportable breach using the four-factor risk assessment (nature and extent of PHI and likelihood of re-identification; who received it; whether it was actually acquired or viewed; extent mitigated). De-identified, encrypted, or unreadable data is outside the definition, which is why the redaction and encryption evidence matters.
3. If reportable: notify affected individuals without unreasonable delay and **no later than 60 days** from discovery; notify HHS (immediately for 500+ individuals, annually for fewer); media notice for 500+ in a state. State laws can be shorter: counsel decides.
4. Document everything: HIPAA requires the documentation to be kept six years.

## 8. Communication rules

One channel, timestamped updates every hour for SEV1, facts only, no blame, no speculation about cause before evidence. The scribe's timeline is the record.

## 9. Recovery and review

Close when: containment verified, root cause fixed with a regression test, golden set and leakage tests green, notifications complete. Blameless post-incident review within five business days: what detected it, what we would have wanted to see sooner, which control failed, which test was missing. Every review produces at least one new test or alert.

## 10. Tabletop exercise: "What almost went wrong"

Scenario (used in the course's incident lecture): a developer changes how prompts are assembled and, as a side effect, the redactor is bypassed for one feature. The golden-set leakage test is not run because the change touched only a template.
Discussion points: which alert fires first (the canary within minutes; `RedactionStoppedWorking` as soon as traffic flows), what the on-call does in the first 10 minutes (switch AI off), how scope is established when prompts are not stored (time window + audit trail), what the privacy officer needs, and which test and CI rule would have prevented it (leakage test as a required check on any change under `ai/copilot`).

The runnable version of this scenario is `incident/drill.py` (instructor guide: `incident/README.md`).
