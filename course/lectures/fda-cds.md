# FDA: SaMD or CDS-exempt?

**Where:** tag `fda-cds`: `ai/copilot/cds.py`, `docs/compliance/fda-cds-memo.md`, `ai/tests/test_cds.py`. Not legal advice: say so on the first slide.

## Learning objectives
1. State the four criteria of the Cures Act CDS exemption (section 520(o)(1)(E)) and apply them to a real feature.
2. Recognize what crosses the line: risk scores for a disease, time-critical alerts, treatment directives, image/waveform analysis, patient-facing output, hidden basis.
3. Turn each criterion into something **enforced and tested**, not a sentence in a policy.
4. Explain to a stakeholder, kindly and concretely, why their feature request is a device.

## Structure (40 minutes)
1. **The four criteria, in plain words** (10 min): not an image or signal; displays patient information; supports a clinician (not a patient) without a specific directive or risk probability; lets the clinician review the basis so they need not rely on it.
2. **Classification exercise** (10 min, groups): for each feature, device or exempt, and why: stay summary; readmission-risk badge; "start amiodarone" suggestion; pending-lab flag; sepsis alert; prior-auth drafting; a chat answer with a 40% risk figure; an ECG interpretation.
3. **How the product enforces it** (10 min): role gate (no patient role); `cds.lint` on every output; the transparency envelope (sources, method, limits); no numeric confidence; human sign-off for anything consequential; rule-based flags with the rule shown. Run the tests.
4. **The stakeholder conversation** (10 min, role-play): Marcus asks for a readmission badge. Write the response that explains why not, offers the compliant alternative, and keeps the relationship.

## Answer key for the classification exercise
Exempt CDS: stay summary, pending-lab flag (rule-based, visible). Administrative: prior-auth drafting. **Device:** readmission-risk badge (risk score), sepsis alert (time-critical, risk), "start amiodarone" (directive), ECG interpretation (signal), the chat answer with a risk figure (probability), any patient-facing version.

## Key point
Intended use is judged by what you say, show and do: labelling, UI, marketing and behaviour must agree. The memo's "do not cross" list belongs on the team's wall.
