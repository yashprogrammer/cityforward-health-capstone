# ICD-10-CM and SNOMED coding with a clinician in the loop

**Where:** tag `coding`: `ai/copilot/coding.py`, `terminology.py`, `coding_api.py`, `tests/test_coding.py`.

## Learning objectives
1. Know the code sets: **ICD-10-CM** (diagnoses, US, public domain), **SNOMED CT** (clinical terms; licence via UMLS; what Synthea uses), **HCPCS Level II** (equipment and services, public), **CPT** (AMA licence: we do not use it).
2. Understand why coding is *administrative support* in the FDA sense, and why a human still decides every line.
3. Build model suggestions that code **cannot be fooled by**: validity and billability checks, verbatim-quote evidence, official descriptions instead of the model's.
4. Turn conflicts into **documentation queries** instead of silently choosing.

## The ideas in order
1. **The model proposes, the code set disposes.** A suggested code must exist, be billable (leaf level), and come with a quote that appears verbatim in the cited chart text. Everything else is dropped, with one repair attempt (the model sees "J96.0 is not billable; children: J96.01, J96.02, J96.00").
2. **Descriptions come from the official list.** If a model's description could differ from the code, it will, someday.
3. **Rules a coder knows by heart become checks.** *Excludes1*: hypoxemia R09.02 and acute respiratory failure with hypoxia J96.01 cannot be reported together. *Unspecified* codes ask for more detail. *Provisional* diagnoses ask whether the diagnosis was confirmed.
4. **The worksheet is a conversation.** Every line is accepted, rejected (reason required) or changed (validated); the coder picks the principal diagnosis; a conflicting set cannot be finalized.
5. **Don't let the AI read its own homework.** The worksheet PDF filed back into the chart is excluded from future evidence.

## Demo (10 minutes)
Whitaker's COPD admission: let the model propose J44.1 (principal), J96.01, F17.210; see R09.02 from the EHR flagged against J96.01; read the generated query; reject R09.02 with a reason; finalize; open the PDF in MediTrack's Documents tab.

## Discussion
* Who bears the risk if the coder accepts a wrong suggestion? Why does the audit log record the decision, not the suggestion?
* Why not let the model pick the principal diagnosis? (It is a sequencing decision with reimbursement consequences: a human signs it.)
* What does it mean that SNOMED cannot be redistributed? How would a production deployment get the SNOMED-to-ICD-10-CM map?

## Pitfalls
Letting the model's confidence stand in for validation; coding symptoms that are integral to a documented diagnosis; hiding unspecified-code queries because "the coder will notice".
