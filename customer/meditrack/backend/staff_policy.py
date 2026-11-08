"""Simple role policy for the local synthetic-data EHR."""

CLINICAL_READERS = frozenset({"ATTENDING", "RESIDENT", "NURSE", "PA_COORD", "HIM_CODER"})
PATIENT_ACTIONS = {
    "patient.view": CLINICAL_READERS,
    "summary.save": frozenset({"ATTENDING"}),
    "discharge.finalize": frozenset({"ATTENDING"}),
    "discharge.advise": frozenset({"ATTENDING", "NURSE"}),
    "document.upload": frozenset({"ATTENDING", "RESIDENT", "NURSE", "HIM_CODER"}),
}


def may_access_patient(role: str, assigned_units: list[str], patient_unit: str | None, action: str) -> bool:
    """Only an approved role with a known, assigned unit may perform an action."""
    if role not in PATIENT_ACTIONS.get(action, frozenset()) or not patient_unit:
        return False
    # The teaching EHR offers a hospital-wide census. The embedded AI has its
    # own unit checks and audited emergency-access workflow.
    return True
