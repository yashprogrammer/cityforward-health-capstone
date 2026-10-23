"""De-identification of prompts (HIPAA Safe Harbor, 45 CFR 164.514(b)(2)) before anything leaves for a model.

We know exactly who the patient is (the FHIR Patient resource), so most identifiers are removed by EXACT match, which is far more reliable than
guessing with patterns. Patterns catch what we do not know in advance (an SSN typed into a note, a relative's phone number).

  names, MRN, member id, account numbers, street, city, zip, phone, email, SSN, URLs, IP addresses  -> [PLACEHOLDERS]
  every date                                                                                       -> [D+n] days from admission (reversible)
  ages over 89                                                                                     -> "90 or older"
  clinician names (Dr. X, X RN)                                                                    -> [CLINICIAN_n]   (not required by Safe Harbor; minimum necessary)

The Restorer puts originals back into the model's reply, locally, so the clinician still sees real names and dates and citations still check out.
Clinical content (labs, medications, diagnoses) is deliberately kept: that is the point of the task. Residual risk is documented in docs/compliance.
"""
import re
from datetime import date

from .chart import Identity

NOTE = ("Privacy: identifiers in the evidence are replaced by placeholders such as [PATIENT], [MRN] and [D+3] (the number of days after admission). "
        "Use placeholders exactly as written; never guess the original value.")

_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}:\d{2})(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?\b")
_US = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})\b")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_PHONE = re.compile(r"(?<!\d)(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}(?!\d)")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_URL = re.compile(r"\bhttps?://\S+|\bwww\.\S+", re.I)
_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_ACCOUNT = re.compile(r"\b[A-Z]{2}\d{4}-\d{4}\b")
_STATE_ZIP = re.compile(r"\b([A-Z]{2})\s+(\d{5})(?:-\d{4})?\b")
_AGE90 = re.compile(r"\b(9\d|1[0-4]\d)[- ]?(?:years?|yrs?|y\.?o\.?|y)(?:[- ]?old)?\b", re.I)
_CLINICIAN = re.compile(r"\bDr\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?|\b[A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+,\s+(?:RN|RRT|PT|OT|MD|DO|NP|PA-C)\b")


class Restorer:
    def __init__(self):
        self.map: dict[str, str] = {}

    def put(self, placeholder: str, original: str) -> str:
        self.map.setdefault(placeholder, original)
        return placeholder

    def restore_text(self, s: str) -> str:
        for ph in sorted(self.map, key=len, reverse=True):
            s = s.replace(ph, self.map[ph])
        return s

    def restore(self, obj):
        if isinstance(obj, str):
            return self.restore_text(obj)
        if isinstance(obj, list):
            return [self.restore(x) for x in obj]
        if isinstance(obj, dict):
            return {k: self.restore(v) for k, v in obj.items()}
        return obj


class Redactor:
    """Stateless apart from the identity lookup; `apply_all` is called by the Gateway for every model call."""

    def __init__(self, identity_lookup=None):
        self.lookup = identity_lookup           # patient_id -> Identity | None

    def identity_for(self, patient_id, context) -> Identity | None:
        if context is not None and getattr(context, "identity", None) is not None:
            return context.identity
        return self.lookup(patient_id) if (self.lookup and patient_id) else None

    def apply_all(self, texts: list[str], patient_id=None, context=None):
        ident = self.identity_for(patient_id, context)
        rs, total = Restorer(), 0
        out = []
        for t in texts:
            t, n = self.apply(t, ident, rs)
            out.append(t)
            total += n
        out[0] = out[0] + "\n\n" + NOTE      # the system prompt explains the placeholders
        return out, rs, total

    # ------------------------------------------------------------------
    def apply(self, text: str, ident: Identity | None, rs: Restorer) -> tuple[str, int]:
        n = 0

        def sub(pattern, make, s, flags=0):
            nonlocal n
            def repl(m):
                nonlocal n
                n += 1
                return make(m)
            return re.sub(pattern, repl, s, flags=flags) if isinstance(pattern, str) else pattern.sub(repl, s)

        if ident:
            full = [x for x in ident.names if x and len(x) > 1]
            ordered = sorted(set(full), key=len, reverse=True)
            for nm in ordered:
                tag = "[PATIENT]" if nm == (ident.names[0] if ident.names else None) else ("[PATIENT_LAST]" if nm == ident.names[-1] else "[PATIENT_FIRST]")
                text = sub(rf"(?<![\w]){re.escape(nm)}(?![\w])", lambda m, tag=tag: rs.put(tag, m.group(0)), text, re.I)
            for value, tag in ((ident.mrn, "[MRN]"), (ident.member_id, "[MEMBER_ID]"), (ident.street, "[ADDRESS]"), (ident.city, "[CITY]"), (ident.zip, "[ZIP]")):
                if value:
                    text = sub(rf"(?<![\w]){re.escape(value)}(?![\w])", lambda m, tag=tag: rs.put(tag, m.group(0)), text, re.I)
            for i, a in enumerate(sorted(set(ident.accounts))):
                text = sub(rf"(?<![\w]){re.escape(a)}(?![\w])", lambda m, i=i: rs.put(f"[ACCOUNT_{i + 1}]", m.group(0)), text)
            for i, ph in enumerate(ident.phones):
                text = text.replace(ph, rs.put(f"[PHONE_{i + 1}]", ph)) if ph in text else text
            for i, em in enumerate(ident.emails):
                text = sub(rf"(?<![\w]){re.escape(em)}(?![\w])", lambda m, i=i: rs.put(f"[EMAIL_{i + 1}]", m.group(0)), text, re.I)
        # patterns for what we could not know in advance
        text = sub(_SSN, lambda m: rs.put("[SSN]", m.group(0)), text)
        text = sub(_EMAIL, lambda m: rs.put(f"[EMAIL_X{len(rs.map)}]", m.group(0)), text)
        text = sub(_URL, lambda m: rs.put(f"[URL_{len(rs.map)}]", m.group(0)), text)
        text = sub(_PHONE, lambda m: rs.put(f"[PHONE_X{len(rs.map)}]", m.group(0)), text)
        text = sub(_IP, lambda m: rs.put(f"[IP_{len(rs.map)}]", m.group(0)) if all(int(x) < 256 for x in m.group(0).split(".")) else m.group(0), text)
        text = sub(_ACCOUNT, lambda m: rs.put(f"[ACCOUNT_X{len(rs.map)}]", m.group(0)), text)
        text = sub(_STATE_ZIP, lambda m: f"{m.group(1)} " + rs.put("[ZIP]", m.group(2)), text)
        text = sub(_CLINICIAN, lambda m: rs.put(f"[CLINICIAN_{len([k for k in rs.map if k.startswith('[CLINICIAN_')]) + 1}]", m.group(0)) if m.group(0) not in rs.map.values() else
                    next(k for k, v in rs.map.items() if v == m.group(0)), text)
        text = sub(_AGE90, lambda m: "90 or older", text)
        # dates last: DOB gets its own tag, everything else becomes an offset from admission
        anchor = date.fromisoformat(ident.anchor) if ident and ident.anchor else None

        def date_tag(d: date, original: str, time: str | None = None) -> str:
            if ident and ident.dob and d.isoformat() == ident.dob[:10]:
                return rs.put("[DOB]", original)
            if anchor is None:
                return rs.put(f"[DATE_{len(rs.map)}]", original)
            off = (d - anchor).days
            tag = f"[D{off:+d}]"
            return rs.put(tag, d.isoformat()) + (f" {time}" if time else "")
        text = sub(_ISO, lambda m: _safe_date(m, date_tag), text)
        text = sub(_US, lambda m: _safe_us_date(m, date_tag), text)
        return text, n


def _safe_date(m, date_tag):
    try:
        return date_tag(date(int(m.group(1)), int(m.group(2)), int(m.group(3))), m.group(0), m.group(4))
    except ValueError:
        return m.group(0)


def _safe_us_date(m, date_tag):
    mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    y += 2000 if y < 100 else 0
    try:
        return date_tag(date(y, mo, d), m.group(0))
    except ValueError:
        return m.group(0)
