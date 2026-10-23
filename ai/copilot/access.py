"""Authorization decisions, in one place.

Two questions, always in this order: (1) may this ROLE do this ACTION at all? (2) may this person see THIS PATIENT (launch scope, unit)?
The role matrix mirrors who does what in a real revenue cycle: coordinators prepare and submit, only the treating clinician attests and signs,
coders code, IT sees no PHI.
"""
from fastapi import HTTPException

from . import audit, db
from .auth import Principal

CLINICAL = {"index", "ask", "summary", "priorauth.view", "priorauth.draft", "priorauth.edit", "priorauth.submit",
            "coding.view", "coding.suggest"}
SIGNERS = {"priorauth.attest", "priorauth.approve", "coding.decide"}

ROLE_ACTIONS: dict[str, set[str]] = {
    "ATTENDING": CLINICAL | SIGNERS | {"breakglass"},
    "RESIDENT": CLINICAL | {"priorauth.attest", "breakglass"},          # residents draft and attest facts; the attending signs the request
    "NURSE": {"index", "ask", "summary", "priorauth.view", "coding.view", "breakglass"},
    "PA_COORD": {"index", "ask", "summary", "priorauth.view", "priorauth.draft", "priorauth.edit", "priorauth.submit"},
    "HIM_CODER": {"index", "ask", "summary", "coding.view", "coding.suggest", "coding.decide"},
    "IT_ADMIN": {"audit.view", "breakglass.review"},         # operates the platform and reviews access logs; sees no clinical content
    "COMPLIANCE": {"audit.view", "breakglass.review"},       # privacy officer (a Keycloak-issued role)
    "SERVICE": {"index"},                                    # the webhook worker
}


def patient_unit(patient_id: str) -> tuple[bool, str | None]:
    """(is_indexed, unit). Read as the table owner on purpose: this lookup is how we decide what the caller may see."""
    with db.system() as c:
        r = c.execute("SELECT unit FROM patient_index WHERE patient_id=%s", (patient_id,)).fetchone()
    return (r is not None), (r["unit"] if r else None)


def has_break_glass(user: str, patient_id: str) -> bool:
    with db.system() as c:
        return c.execute("SELECT 1 FROM break_glass WHERE user_id=%s AND patient_id=%s AND expires_at > now()", (user, patient_id)).fetchone() is not None


def authorize(p: Principal, action: str, patient_id: str | None = None, unit: str | None = None, about: str | None = None) -> None:
    """Raise 403 unless `p` may perform `action` (on this patient). `unit` can be passed when the caller already has it (indexing);
    `about` names the patient for the audit trail when this is a role-only check made before the chart is fetched."""
    def deny(why: str, status: int = 403):
        audit.log(p, action, patient_id or about, "denied", {"why": why})
        raise HTTPException(status, why)

    if action not in ROLE_ACTIONS.get(p.role, set()):
        deny(f"role {p.role} may not perform {action}")
    if patient_id is None:
        return
    if p.patients and patient_id not in p.patients:
        deny("this token is limited to another patient")
    if p.role == "SERVICE":
        audit.log(p, action, patient_id)
        return
    if unit is None:
        indexed, unit = patient_unit(patient_id)
        if not indexed:
            raise HTTPException(409, "patient is not indexed yet; open the patient through the EHR launch or call /api/patients/{id}/index")
    if not p.can_see_unit(unit):
        if not has_break_glass(p.user, patient_id):
            deny("patient is on a unit outside your assignment (break-glass access requires a documented reason)")
        audit.log(p, action, patient_id, detail={"break_glass": True})      # allowed, and flagged for the privacy officer's review
        return
    audit.log(p, action, patient_id)
