"""Authorization decisions, in one place.

Two questions, always in this order: (1) may this ROLE do this ACTION at all? (2) may this person see THIS PATIENT (launch scope, unit)?
The role matrix mirrors who does what in a real revenue cycle: coordinators prepare and submit, only the treating clinician attests and signs,
coders code, IT sees no PHI.
"""
from fastapi import HTTPException

from . import db
from .auth import Principal

CLINICAL = {"index", "ask", "summary", "priorauth.view", "priorauth.draft", "priorauth.edit", "priorauth.submit",
            "coding.view", "coding.suggest"}
SIGNERS = {"priorauth.attest", "priorauth.approve", "coding.decide"}

ROLE_ACTIONS: dict[str, set[str]] = {
    "ATTENDING": CLINICAL | SIGNERS,
    "RESIDENT": CLINICAL | {"priorauth.attest"},          # residents draft and attest facts; the attending signs the request
    "NURSE": {"index", "ask", "summary", "priorauth.view", "coding.view"},
    "PA_COORD": {"index", "ask", "summary", "priorauth.view", "priorauth.draft", "priorauth.edit", "priorauth.submit"},
    "HIM_CODER": {"index", "ask", "summary", "coding.view", "coding.suggest", "coding.decide"},
    "IT_ADMIN": set(),                                       # operates the platform; no PHI
    "SERVICE": {"index"},                                    # the webhook worker
}


def patient_unit(patient_id: str) -> tuple[bool, str | None]:
    """(is_indexed, unit). Read as the table owner on purpose: this lookup is how we decide what the caller may see."""
    with db.session() as c:
        r = c.execute("SELECT unit FROM patient_index WHERE patient_id=%s", (patient_id,)).fetchone()
    return (r is not None), (r["unit"] if r else None)


def authorize(p: Principal, action: str, patient_id: str | None = None, unit: str | None = None) -> None:
    """Raise 403 unless `p` may perform `action` (on this patient). `unit` can be passed when the caller already has it (indexing)."""
    if action not in ROLE_ACTIONS.get(p.role, set()):
        raise HTTPException(403, f"role {p.role} may not perform {action}")
    if patient_id is None:
        return
    if p.patients and patient_id not in p.patients:
        raise HTTPException(403, "this token is limited to another patient")
    if p.role == "SERVICE":
        return
    if unit is None:
        indexed, unit = patient_unit(patient_id)
        if not indexed:
            raise HTTPException(409, "patient is not indexed yet; open the patient through the EHR launch or call /api/patients/{id}/index")
    if not p.can_see_unit(unit):
        raise HTTPException(403, "patient is on a unit outside your assignment")
