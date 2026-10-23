"""Deterministic adjudication of a prior-authorization claim against the policy in rules.py."""
from datetime import date, datetime

from .rules import RULES


def evaluate(code: str, diagnoses: list[str], supporting: dict[str, dict], service_date: date | None) -> dict:
    """Returns {"decision": approved|pended|denied, "missing": [...], "denials": [...], "met": [...]}.

    A measurement that is present but outside the limit is a clinical denial. Anything simply absent is a pend: the
    payer asks for more information (that is the loop an AI assistant should avoid by assembling the evidence first).
    """
    rule = RULES[code]
    missing, denials, met = [], [], []
    for req in rule["requirements"]:
        info = supporting.get(req["id"])
        kind = req["kind"]
        if kind == "diagnosis":
            if any(d.startswith(tuple(req["icd_prefixes"])) for d in diagnoses):
                met.append(req["id"])
            else:
                missing.append((req["id"], req["text"]))
        elif kind == "value":
            if not info or info.get("value") is None:
                missing.append((req["id"], req["text"]))
                continue
            v = info["value"]
            if "max" in req and v > req["max"]:
                denials.append(f"{req['id']}: measured {v:g}{req['unit']}, policy requires {req['max']:g}{req['unit']} or lower")
            elif "min" in req and v < req["min"]:
                denials.append(f"{req['id']}: {v:g} {req['unit']}, policy requires at least {req['min']:g} {req['unit']}")
            elif "within_days" in req and service_date and info.get("date"):
                age = (service_date - info["date"]).days
                if age > req["within_days"]:
                    missing.append((req["id"], f"{req['text']} (supplied measurement is {age} days old)"))
                    continue
                met.append(req["id"])
            else:
                met.append(req["id"])
        elif kind == "document":
            if info and (info.get("text") or info.get("attachment")) and len((info.get("text") or "x").strip()) >= 20:
                met.append(req["id"])
            else:
                missing.append((req["id"], req["text"]))
        elif kind == "attestation":
            if info and info.get("boolean") is True:
                met.append(req["id"])
            else:
                missing.append((req["id"], req["text"]))
    decision = "denied" if denials else "pended" if missing else "approved"
    return {"decision": decision, "missing": missing, "denials": denials, "met": met}


def parse_date(s: str | None) -> date | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
