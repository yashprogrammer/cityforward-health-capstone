# Working with Synthea FHIR bundles

**Where:** `legacy` branch, `customer/synthea` (generator notes, prepared bundles, golden patients) and `customer/meditrack/etl` (the lossy migration into MediTrack's legacy tables).
**Time:** 45 minutes, half of it hands-on.

## Learning objectives
1. Read a FHIR **transaction bundle**: entries, `urn:uuid:` references, search-style references (`Practitioner?identifier=…`), base64 notes.
2. Know what Synthea gives you (realistic structure, thin clinical stories) and what it does not (coherent hospital narratives, messy notes, payer rules): why the capstone adds three hand-written **golden patients**.
3. Understand why FDEs generate synthetic data: no PHI in development, reproducibility, shareable demos.
4. See the licensing line: ICD-10-CM is public domain; SNOMED CT needs a UMLS licence; CPT is AMA-licensed and not used.

## Walk-through
1. Open `customer/synthea/prepared/bundles/SYN-004.json` and list the resource types and counts (`Patient`, `Encounter`, `Condition`, `Observation`, `MedicationRequest`, `DocumentReference`, ...).
2. Follow one reference by hand: an `Observation.encounter` → the `Encounter` → its `participant` → the `Practitioner`.
3. Decode a note: `DocumentReference.content[0].attachment.data` is base64 text.
4. Read `prepare_synthea.py`'s docstring: how 500 generated patients became 19 (selection by reason for admission, date shifting to the demo window, fictional payers).
5. Open `GLD-001.json` (Robert Hale, anterior STEMI) and find the planted teaching points: a pending Lp(a), the implant record that is mentioned but not filed, a provisional diagnosis.
6. Run `poc/summarizer/records.py` on both a Synthea and a golden bundle.

## Exercises
* Write a function that returns every medication that was `active` at discharge for a patient. What do you do with `stopped` orders and with orders without an end date?
* Find the three patients in the cohort with the longest stays. Which resource types explain the length?
* Why do dates in this repository look like October 2026 when Synthea ran on a different date? (The shift preserves day offsets.)

## Pitfalls
* Treating `Condition.code` as always ICD-10-CM: Synthea uses SNOMED CT; the migration maps a subset.
* Dropping `Observation` components (blood pressure is two values in one resource).
* Assuming a note is plain text: always check `contentType`.
* Believing synthetic data is *representative*: it is structurally right and clinically thin.
