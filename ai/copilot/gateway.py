"""The one door to the model. Every call in the service goes through `Gateway.json(...)`, which is where policy lives:

  1. de-identify the prompt (HIPAA Safe Harbor identifiers become placeholders; restored in the reply)   [redaction hook]
  2. call the backend, measure tokens and latency, price it
  3. parse and validate the JSON against the caller's pydantic schema (one repair attempt is the caller's graph, not hidden here)
  4. write a metering row (no prompt or reply text: that is PHI) and an audit record

Callers never import a model client, so there is no way around the policy.
"""
import os
import time
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from . import audit, db, telemetry
from .llm import Backend, LLMError, OpenAICompatible, RoutedBackend, cost_usd, extract_json

T = TypeVar("T", bound=BaseModel)


class Gateway:
    def __init__(self, backend: Backend | None, redactor=None, recorder=None, daily_budget_usd: float | None = None):
        real = backend.default if isinstance(backend, RoutedBackend) else backend
        if redactor is None and isinstance(real, OpenAICompatible) and not real.in_boundary:
            # fail closed: the incident drill's root cause was a gateway wired up without its redactor. A real model client cannot be built that way
            # unless an operator states the model runs inside the hospital boundary (LLM_IN_BOUNDARY=1).
            raise ValueError("refusing to send prompts to a model client without de-identification; pass a redactor (or set LLM_IN_BOUNDARY=1 for a model inside the hospital boundary)")
        self.backend = backend
        self.redactor = redactor           # object with .apply_all(texts, patient_id=, context=)   (Build Session 4)
        self.recorder = recorder or self._record_db
        self.daily_budget_usd = float(os.getenv("COPILOT_DAILY_BUDGET_USD", "0")) if daily_budget_usd is None else daily_budget_usd
        self._spend: tuple[float, float] = (0.0, 0.0)      # (monotonic time read, dollars today)
        self._budget_noted = False

    @property
    def online(self) -> bool:
        return self.backend is not None

    @property
    def model(self) -> str:
        return self.backend.name if self.backend else "offline"

    def prepare(self, system: str, user: str, patient_id=None, context=None):
        """The exact text that leaves for the model: de-identified when a redactor is configured. Returns (system, user, restorer, redaction count)."""
        if self.redactor is None:
            return system, user, None, 0
        (system, user), restorer, n = self.redactor.apply_all([system, user], patient_id=patient_id, context=context)
        return system, user, restorer, n

    def dry_run(self, system: str, user: str, patient_id=None, context=None) -> str:
        """What WOULD be sent, without sending it. Used by the redaction canary to test the live wiring continuously."""
        s, u, _, _ = self.prepare(system, user, patient_id, context)
        return s + "\n" + u

    def _backend_for(self, purpose: str):
        return self.backend.for_purpose(purpose) if isinstance(self.backend, RoutedBackend) else self.backend

    def spend_today(self, max_age: float = 20.0) -> float:
        """Dollars spent since midnight (cached for a few seconds: this is a circuit breaker, not an accountant)."""
        now = time.monotonic()
        if now - self._spend[0] > max_age:
            with db.system() as c:
                total = float(c.execute("SELECT COALESCE(sum(cost_usd), 0) AS t FROM llm_call WHERE ts >= date_trunc('day', now())").fetchone()["t"])
            self._spend = (now, total)
        return self._spend[1]

    def _check_budget(self) -> None:
        if self.daily_budget_usd and self.spend_today() >= self.daily_budget_usd:
            telemetry.BUDGET_BLOCKED.inc()
            if not self._budget_noted:
                self._budget_noted = True
                audit.log("system", "ai.budget-exhausted", detail={"cap_usd": self.daily_budget_usd})
            raise LLMError(f"daily model budget of ${self.daily_budget_usd:g} reached: AI features are in no-AI mode until the cap is raised or tomorrow")

    def json(self, system: str, user: str, schema: type[T], *, purpose: str, patient_id: str | None = None, actor: str | None = None,
             context=None) -> T:
        """Returns a validated `schema` instance or raises LLMError."""
        if self.backend is None:
            raise LLMError("no model configured")
        self._check_budget()
        backend = self._backend_for(purpose)
        system, user, restorer, n_red = self.prepare(system, user, patient_id, context)
        t0 = time.perf_counter()
        status, reply = "error", None
        span = telemetry.tracer().start_span("llm.call", attributes={"llm.purpose": purpose, "llm.model": backend.name, "llm.redactions": n_red})
        try:
            reply = backend.chat(system, user)
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
            model_name = backend.name
            self._spend = (self._spend[0], self._spend[1] + cost_usd(model_name, tin, tout))
            span.set_attributes({"llm.tokens_in": tin, "llm.tokens_out": tout, "llm.status": status})
            span.end()
            try:
                telemetry.record_llm(dict(purpose=purpose, model=model_name, status=status, tokens_in=tin, tokens_out=tout, cost_usd=cost_usd(self.model, tin, tout), ms=ms, redactions=n_red))
                self.recorder(dict(purpose=purpose, patient_id=patient_id, actor=actor, model=model_name, tokens_in=tin, tokens_out=tout, ms=ms,
                                   cost_usd=cost_usd(model_name, tin, tout), redactions=n_red, status=status))
            except Exception as e:   # noqa: BLE001 - metering must never take down a clinical request
                print(f"[gateway] could not record call: {e}")

    @staticmethod
    def _record_db(r: dict) -> None:
        audit.log(r["actor"] or "system", "ai.call", r["patient_id"], "allowed" if r["status"] == "ok" else "error",
                  {"purpose": r["purpose"], "model": r["model"], "tokens_in": r["tokens_in"], "tokens_out": r["tokens_out"], "redactions": r["redactions"], "status": r["status"]})
        with db.session() as c:
            c.execute("""INSERT INTO llm_call (purpose, patient_id, actor, model, tokens_in, tokens_out, ms, cost_usd, redactions, status)
                         VALUES (%(purpose)s,%(patient_id)s,%(actor)s,%(model)s,%(tokens_in)s,%(tokens_out)s,%(ms)s,%(cost_usd)s,%(redactions)s,%(status)s)""", r)
