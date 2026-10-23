# Synthea data for CityForward Health

Patients are **synthetic**, generated with [Synthea](https://github.com/synthetichealth/synthea) (FHIR R4, US Core profiles).
You do **not** need Java or Synthea to run the course: the small data set Synthea produced is committed in `prepared/`.

## What is in `prepared/`

| File | What |
|---|---|
| `bundles/SYN-001..019.json` | 19 patients, each a FHIR *transaction bundle*: Patient, Encounters, Conditions, labs and vitals (Observation), Procedures, MedicationRequests, clinical notes (DocumentReference with base64 text), Practitioners, Coverage. One recent hospital stay per patient plus 24 months of history. |
| `bundles/GLD-001..003.json` | Three hand-written "golden" patients in the same format (a heart attack, a hip fracture, a COPD exacerbation). Synthea's inpatient stays are thin, so the demo and the evals use these for clinically coherent stories. |
| `manifest.json` | Per case: unit, bed, status (ADM / DA / DSCH), dates, disposition, payer and member id: the facts MediTrack needs that FHIR doesn't carry. |
| `mis_history.json` | 150 admission-only rows: older stays "migrated from the previous system", used for the discharge-turnaround report. |

## How the raw cohort was generated (author only)

```bash
# Synthea master branch, Java 17+. 500 living patients, ages 50-90, Columbus OH, seed 2610, reference date 2026-10-15
java -jar synthea-with-dependencies.jar -p 500 -s 2610 -cs 1 -r 20261015 -a 50-90 \
  --exporter.years_of_history=3 --exporter.clinical_note.export=true \
  --generate.append_numbers_to_person_names=false --exporter.csv.export=false Ohio Columbus
python prepare_synthea.py /path/to/output          # trims, shifts dates, writes prepared/
python build_golden.py                              # adds the three golden patients
```

`prepare_synthea.py` explains what it does in its docstring. The short version:

* picks the clinically richest recent stay for each of a fixed list of reasons (STEMI, NSTEMI, heart failure, appendicitis, COVID-19, skilled-nursing transfer, lung cancer, aortic stenosis)
* treats the ED visit that precedes an admission as part of the stay
* drops billing noise (Claim, ExplanationOfBenefit, ...), keeps the latest values of each lab, and shifts all dates by whole days so the stay lands in the demo window ("now" is 2026-10-22)
* replaces real insurer names with fictional plans and adds a Coverage resource; about half of the Medicare patients become a fictional Medicare Advantage plan (that is where prior authorization lives)

## Loading into MediTrack

`python -m meditrack.backend.seed` (from `customer/`) reads `prepared/` and fills MediTrack's legacy tables through
`meditrack/etl/load_bundles.py`: the lossy migration a real EHR vendor would run (ICD-10-CM only, lab values as text,
vitals inside the lab table). Conditions that have no SNOMED CT to ICD-10-CM mapping are skipped and counted, never invented.

## Licensing notes

* **ICD-10-CM** is public domain (CMS). **SNOMED CT** requires a UMLS licence and cannot be redistributed: the bundles contain only the few codes that appear in the cohort. **CPT** is AMA-licensed and is not used (procedures use SNOMED CT, equipment uses HCPCS Level II). **LOINC** and **RxNorm** codes are freely usable.
* Synthea is Apache-2.0. Every person here is fictional; SSNs start with 999 and NPIs with 9999.
