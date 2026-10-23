"""The one door to the model. Every call in the service goes through `Gateway.json(...)`, which is where policy lives:

  1. de-identify the prompt (HIPAA Safe Harbor identifiers become placeholders; restored in the reply)   [redaction hook]
  2. call the backend, measure tokens and latency, price it
  3. parse and validate the JSON against the caller's pydantic schema (one repair attempt is the caller's graph, not hidden here)
  4. write a metering row (no prompt or reply text: that is PHI) and an audit record

Callers never import a model client, so there is no way around the policy.
"""
import time
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from . import audit, db, telemetry
from .llm import Backend, LLMError, cost_usd, extract_json

T = TypeVar("T", bound=BaseModel)


class Gateway:
    def __init__(self, backend: Backend | None, redactor=None, recorder=None):
        self.backend = backend
        self.redactor = redactor           # object with .apply(text, patient) -> (text, Restorer)   (Build Session 4)
        self.recorder = recorder or self._record_db

    @property
    def online(self) -> bool:
        return self.backend is not None

    @property
    def model(self) -> str:
        return self.backend.name if self.backend else "offline"

    def json(self, system: str, user: str, schema: type[T], *, purpose: str, patient_id: str | None = None, actor: str | None = None,
             context=None) -> T:
        """Returns a validated `schema` instance or raises LLMError."""
        if self.backend is None:
            raise LLMError("no model configured")
        restorer, n_red = None, 0
        if self.redactor is not None:
            (system, user), restorer, n_red = self.redactor.apply_all([system, user], patient_id=patient_id, context=context)
        t0 = time.perf_counter()
        status, reply = "error", None
        span = telemetry.tracer().start_span("llm.call", attributes={"llm.purpose": purpose, "llm.model": self.model, "llm.redactions": n_red})
        try:
            reply = self.backend.chat(system, user)
            status = "invalid"
            raw = extract_json(reply.text)
            if restorer is not None:
                raw = restorer.restore(raw)
            out = schema.model_validate(raw)
            status = "ok"
            return out
        except ValidationError as e:
            raise LLMError(f"reply does not match the schema: {str(e).splitlines()[0]}") from e
        finally:
            ms = int((time.perf_counter() - t0) * 1000)
            tin, tout = (reply.tokens_in, reply.tokens_out) if reply else (0, 0)
            span.set_attributes({"llm.tokens_in": tin, "llm.tokens_out": tout, "llm.status": status})
            span.end()
            try:
                telemetry.record_llm(dict(purpose=purpose, model=self.model, status=status, tokens_in=tin, tokens_out=tout, cost_usd=cost_usd(self.model, tin, tout), ms=ms, redactions=n_red))
                self.recorder(dict(purpose=purpose, patient_id=patient_id, actor=actor, model=self.model, tokens_in=tin, tokens_out=tout, ms=ms,
                                   cost_usd=cost_usd(self.model, tin, tout), redactions=n_red, status=status))
            except Exception as e:   # noqa: BLE001 - metering must never take down a clinical request
                print(f"[gateway] could not record call: {e}")

    @staticmethod
    def _record_db(r: dict) -> None:
        audit.log(r["actor"] or "system", "ai.call", r["patient_id"], "allowed" if r["status"] == "ok" else "error",
                  {"purpose": r["purpose"], "model": r["model"], "tokens_in": r["tokens_in"], "tokens_out": r["tokens_out"], "redactions": r["redactions"], "status": r["status"]})
        with db.session() as c:
            c.execute("""INSERT INTO llm_call (purpose, patient_id, actor, model, tokens_in, tokens_out, ms, cost_usd, redactions, status)
                         VALUES (%(purpose)s,%(patient_id)s,%(actor)s,%(model)s,%(tokens_in)s,%(tokens_out)s,%(ms)s,%(cost_usd)s,%(redactions)s,%(status)s)""", r)
