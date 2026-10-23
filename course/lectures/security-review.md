# Passing a hospital-style security review

**Where:** tag `security-review`: `docs/compliance/` (mapping, threat model, incident plan, vendor register, completed questionnaire), `.github/workflows/`, `SECURITY.md`.

## Learning objectives
1. Know what a hospital reviewer actually does: a questionnaire, a call, a request for evidence, a list of findings with a deadline.
2. Assemble a pack the reviewer can verify without you: every claim points to a file, a test or a log.
3. Handle gaps professionally: list them first, give a plan and an owner.
4. Run the review as a conversation with the CISO, not a form.

## Role-play (60 minutes)
Roles: CISO (Walter Brandt), privacy officer, IT lead, the FDE. The CISO gets the questionnaire and one rule: *no answer is accepted without evidence you can open in the next five minutes.*
1. Pick five questions at random (data, access, logging, incident, AI-specific). The FDE answers live, opening the evidence.
2. The CISO asks one hostile follow-up per answer ("what if the audit table is truncated by a DBA?", "what stops a prompt injection from exfiltrating a name?").
3. The privacy officer asks about the model provider and the BAA; the FDE answers with the vendor register and the gap list.
4. Debrief: which answers were strong because of a **test** rather than a sentence?

## The pack, and what each document is for
| Document | Reviewer's question |
|---|---|
| `hipaa-hitrust-mapping.md` | "Show me where each control lives and what you have not done." |
| `threat-model.md` | "What are you afraid of, and how do you know?" |
| `incident-response-plan.md` | "It's 2 a.m. and PHI went to the model provider. What happens?" |
| `vendor-register.md` | "Who else touches this and under what contract?" |
| `security-questionnaire.md` | the form they will send anyway, already answered |
| `fda-cds-memo.md` | "Is this a device?" |

## What makes a pack credible
Evidence over adjectives; the gaps list on page one; a test that fails if a document drifts from the code (`test_compliance_docs.py`); dates and owners; no certification claims.

## Pitfalls
Answering "Yes" to everything; promising controls you have not built; hiding the model vendor; treating the security review as a phase at the end instead of week one.
