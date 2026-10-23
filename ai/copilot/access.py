"""Authorization decisions, in one place. (Build Session RBAC replaces the body of `authorize` with the full role matrix; the call sites stay.)"""
from fastapi import HTTPException

from . import db
from .auth import Principal


def patient_unit(patient_id: str) -> tuple[bool, str | None]:
    """(is_indexed, unit). Read as the table owner on purpose: this lookup is how we decide what the caller may see."""
    with db.session() as c:
        r = c.execute("SELECT unit FROM patient_index WHERE patient_id=%s", (patient_id,)).fetchone()
    return (r is not None), (r["unit"] if r else None)


def authorize(p: Principal, action: str, patient_id: str, unit: str | None = None) -> None:
    """Raise 403 unless `p` may perform `action` on this patient. `unit` can be passed when the caller already has it (indexing)."""
    if p.patients and patient_id not in p.patients:
        raise HTTPException(403, "this token is limited to another patient")
    if unit is None:
        indexed, unit = patient_unit(patient_id)
        if not indexed:
            raise HTTPException(409, "patient is not indexed yet; open the patient through the EHR launch or call /api/patients/{id}/index")
    if not p.can_see_unit(unit):
        raise HTTPException(403, "patient is on a unit outside your assignment")
