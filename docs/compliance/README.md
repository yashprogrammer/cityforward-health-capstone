# Compliance pack (what you hand a hospital's security reviewer)

> Everything here describes a **course project on synthetic patients**. Controls are real and tested where marked; this is engineering evidence and a mapping aid, **not a certification, an audit result or legal advice**.
> A real engagement needs the hospital's counsel and a qualified assessor (HITRUST assessments are performed by authorized external assessors).

| Document | Question it answers |
|---|---|
| [fda-cds-memo.md](fda-cds-memo.md) | Is this a medical device? Why the CDS exemption applies, and the list of things that would end it |
| [hipaa-hitrust-mapping.md](hipaa-hitrust-mapping.md) | Where is each HIPAA Security Rule safeguard and HITRUST control area implemented, tested, or left to the customer? The five questions a reviewer always asks |
| [threat-model.md](threat-model.md) | What can go wrong, including the AI-specific threats, and which control and test answers each |
| [incident-response-plan.md](incident-response-plan.md) | Who does what in the first hour, the first day, and the 60-day breach clock |
| [vendor-register.md](vendor-register.md) | Which third parties touch the system, what data they see, and what must be true before real PHI flows |
| [security-questionnaire.md](security-questionnaire.md) | A completed hospital-style security questionnaire with evidence pointers |

Evidence index: every code path, test and file named in these documents is checked to exist by `ai/tests/test_compliance_docs.py`, so the documents cannot silently drift away from the repository.

Report a vulnerability: see [../../SECURITY.md](../../SECURITY.md).
