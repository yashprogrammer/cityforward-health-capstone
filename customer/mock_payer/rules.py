"""The payer's medical policy: what each service needs before it is approved.

Real payers publish this as PDFs and (increasingly) as CMS-mandated APIs (Da Vinci CRD / DTR). This is a fictional,
simplified policy for four services. HCPCS Level II codes are public; CPT is not used (AMA licence).

requirement kinds
  diagnosis    the claim must carry a diagnosis whose ICD-10-CM code starts with one of the prefixes
  value        a numeric measurement; outside the limit is a clinical *denial*, absent is a *pend* (more information)
  document     a supporting document / note summary (valueString or attachment) must be supplied
  attestation  the requesting clinician attests (valueBoolean true)
"""

PA_REQUIRED_PLANS = ("Medicare Advantage", "Health Plan", "Mutual", "Pinnacle Care", "Northwind Health", "Evergreen Health Plan")  # name contains one of these
NO_PA_PLANS = ("Medicare", "Medicaid")                                                                                             # exact names: traditional programs

RULES = {
    "E1390": {
        "name": "Oxygen concentrator (home oxygen therapy)", "manual_review": False, "approved_days": 90,
        "requirements": [
            {"id": "dx-chronic-resp", "kind": "diagnosis", "icd_prefixes": ["J43", "J44", "J84", "J96.1", "J96.2", "R09.02"],
             "text": "Diagnosis of chronic lung disease or hypoxemia (ICD-10-CM J43, J44, J84, J96.1, J96.2, R09.02)"},
            {"id": "spo2-qualifying", "kind": "value", "max": 88, "unit": "%", "within_days": 2,
             "text": "Resting SpO2 of 88% or lower on room air (or PaO2 of 55 mmHg or lower), measured within 2 days before discharge"},
            {"id": "face-to-face", "kind": "document", "text": "Face-to-face evaluation by the ordering practitioner (note summary or attachment)"},
            {"id": "therapy-optimized", "kind": "attestation", "text": "Underlying disease is optimally treated (bronchodilator / steroid course documented)"},
        ]},
    "E0143": {
        "name": "Walker, folding, wheeled, adjustable or fixed height (rolling walker)", "manual_review": False, "approved_days": 365,
        "requirements": [
            {"id": "mobility-limitation", "kind": "document", "text": "Documented mobility limitation that impairs daily activities in the home"},
            {"id": "pt-eval", "kind": "document", "text": "Physical therapy evaluation that recommends the walker"},
        ]},
    "SNF-ADM": {
        "name": "Skilled nursing facility admission (level-of-care review)", "manual_review": True, "approved_days": 14,
        "requirements": [
            {"id": "qualifying-stay", "kind": "value", "min": 3, "unit": "midnights", "text": "Inpatient stay of at least 3 midnights before transfer"},
            {"id": "skilled-need", "kind": "document", "text": "Daily skilled nursing or therapy need that cannot be met at home"},
            {"id": "functional-status", "kind": "document", "text": "Current functional status (PT / OT evaluation: transfers, ambulation, stairs)"},
            {"id": "discharge-plan", "kind": "document", "text": "Discharge plan: accepting facility and expected length of stay"},
        ]},
    "G0299": {
        "name": "Home health skilled nursing (RN visits)", "manual_review": False, "approved_days": 60,
        "requirements": [
            {"id": "homebound", "kind": "attestation", "text": "Patient is homebound (leaving home takes considerable and taxing effort)"},
            {"id": "face-to-face", "kind": "document", "text": "Face-to-face encounter related to the reason for home health, within 90 days before or 30 days after start"},
            {"id": "skilled-need", "kind": "document", "text": "Intermittent skilled nursing need (assessment, teaching, medication management)"},
            {"id": "plan-of-care", "kind": "attestation", "text": "Plan of care established and reviewed by the physician"},
        ]},
}


def pa_required(rule_code: str, plan_name: str | None) -> bool:
    """Do we need prior authorization for this service under this plan? Traditional Medicare / Medicaid / self-pay: no."""
    if rule_code not in RULES or not plan_name or plan_name in NO_PA_PLANS:
        return False
    return any(w in plan_name for w in PA_REQUIRED_PLANS)
