# FDA: is the Copilot a medical device? (CDS exemption memo)

**Status:** engineering memo for the regulatory file, written for the course project. It is not legal advice; a real product needs regulatory counsel to confirm the classification for its intended use.
**Question:** does CityForward Copilot fall under FDA's device authority (Software as a Medical Device), or is it exempt Clinical Decision Support under section 520(o)(1)(E) of the FD&C Act (21st Century Cures Act, 2016; FDA final CDS guidance, September 2022)?
**Answer:** designed to be **non-device CDS** for its clinical functions, and **administrative-support software** (520(o)(1)(A)) for prior-authorization drafting and coding. The design is the argument: the exemption is lost the moment the product does something the four criteria forbid, so each criterion is enforced in code and tested.

## Intended use (the sentence the whole file hangs on)

> CityForward Copilot helps licensed health care professionals review a hospitalized patient's existing chart. It summarizes and searches documentation already in the EHR and shows where each statement comes from. It does not diagnose, predict risk, recommend treatment, generate alerts, analyze images or waveforms, or act without a clinician's review. It is not for patients or caregivers and not for time-critical decisions.

The same text is returned by `GET /api/about` and attached to every AI output (`copilot/cds.py`). Intended use is judged by what the manufacturer says and does (labeling, UI, marketing, behavior), so those must never drift apart.

## The four criteria

| # | Criterion (all four required) | How the product meets it | Enforced / evidenced in |
|---|---|---|---|
| 1 | Does **not** acquire, process or analyze a medical image, an in-vitro diagnostic signal, or a pattern/signal from a signal-acquisition system (ECG, waveform) | The service reads FHIR text resources and discrete results. There is no image/waveform input path. Scanned documents are PDFs with text, used as documents | `chart.py` (inputs), no DICOM/waveform dependency; scope list in the SMART client registration |
| 2 | Displays, analyzes or prints **medical information about a patient** | Chart summary, question answering over the chart, cited passages | `summary.py`, `ask.py` |
| 3 | **Supports or provides recommendations to a health care professional** (not a patient/caregiver) about prevention, diagnosis or treatment, **without** a specific directive or a disease risk probability | Role gate: only staff roles authenticated through the hospital's SMART launch exist; there is no patient or caregiver role. Output is linted for treatment directives ("start X", "you should") and risk numbers ("35% risk of..."); a violating draft is revised once, then replaced by the plain source passages. No risk scores, no alerts, no time-critical use | `access.py` role matrix, `cds.lint()` wired into `ask._ground` and `summary._ground`, `tests/test_cds.py` |
| 4 | Enables the HCP to **independently review the basis** for the output, so they need not rely primarily on it | Every output returns its sources (FHIR references, verbatim text), the plain-language method and its limitations; the dashboard shows the sources next to each statement ("Why" panel) and links back to the EHR record. Every number must appear in the cited source or the output is not shown. Numeric confidence is not displayed (false precision encourages over-reliance) | `grounding.py`, `cds.envelope()`, `tests/test_cds.py::test_every_ai_response_carries_the_transparency_envelope` |

## Feature classification

| Feature | Class | Why | Human control |
|---|---|---|---|
| Patient summary, chart Q&A, dashboard "Why" panel | Non-device CDS (520(o)(1)(E)) | Meets all four criteria | Clinician reads sources; no auto action |
| Data-quality flags (pending result, missing implant record, unsigned note) | Non-device CDS | Rule-based and visible ("this lab was ordered and has no result"), no prediction | Rule text shown with the flag |
| Prior-authorization drafting | Administrative support (520(o)(1)(A)) | Billing/administrative workflow, not diagnosis or treatment | Clinician attests and approves; coordinator submits |
| ICD-10-CM coding assistant | Administrative support (520(o)(1)(A)) | Billing/administrative workflow | Coder or attending decides every line |

## Things that would make it a device (the "do not cross" list)

Each of these removes the exemption and would need a regulatory strategy (510(k)/De Novo, quality system, clinical validation) before shipping:

* a risk probability or score for a specific condition (readmission, sepsis, deterioration), even "just a number in the corner"
* an alert or notification that a clinician is expected to act on quickly (time-critical decision-making)
* a recommendation to start, stop or change a specific treatment, or a ranked differential diagnosis presented as the answer
* interpreting an ECG, imaging study, waveform or pathology slide
* any patient- or caregiver-facing output
* hiding the basis (no sources, or sources the clinician cannot open)
* inviting the clinician to rely primarily on the output (for example, auto-filling orders or auto-submitting without review)

The course's capstone exercise: a stakeholder asks for a "readmission risk" badge. Walk the list, explain why it crosses, and propose the compliant alternative (show the documented readmission-relevant facts with sources, no score).

## Hazards and controls (lightweight risk file)

| Hazard | Severity if it happens | Control | Test |
|---|---|---|---|
| Model states a value that is not in the chart | High | every number must appear in the cited source; otherwise withheld | `test_priorauth::test_a_fabricated_number_never_reaches_the_draft`, `ask`/`summary` grounding tests |
| Model omits a critical finding | High | cannot be fully controlled: labeled limitation; data-quality flags are rule-based and independent of the model; sources always shown | golden evaluation set (clinician-graded) tracks omissions |
| Automation bias (clinician stops checking) | High | sources next to each statement, no numeric confidence, no autonomous actions, "About" panel | UI review, `test_cds` |
| Output read as a directive | Medium | directive lint; wording rules in the prompt | `test_cds::test_lint_*` |
| Wrong patient's data shown | High | patient scope comes from the signed SMART token, retrieval filters by patient id in the database, unit-level row security | `test_rag`, RBAC tests |
| Model/prompt change silently lowers quality | Medium | change control: any model, prompt or retrieval change re-runs the golden evaluation set before release | `eval/` runner |
| Service outage | Low | every graph degrades to plain passages or a labeled template | fallback tests |

## Labeling and post-market practice

* Label every screen "AI-generated, for clinician review" and keep the intended-use text one click away.
* Track clinician feedback ("this was wrong") and the rate of withheld outputs; review monthly.
* Keep an audit trail of who saw what (see HIPAA/HITRUST pack).
* Version prompts and models; the golden evaluation set is the regression test for a change.

## References

* Federal Food, Drug, and Cosmetic Act section 520(o)(1)(E), added by the 21st Century Cures Act section 3060(a).
* FDA, *Clinical Decision Support Software: Guidance for Industry and FDA Staff* (final, September 2022).
* FDA, *Policy for Device Software Functions and Mobile Medical Applications*; *Changes to Existing Medical Software Policies Resulting from Section 3060 of the 21st Century Cures Act*.
