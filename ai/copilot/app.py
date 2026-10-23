"""The Copilot HTTP API (FastAPI). Mounted by the hospital gateway under /ai (prefix stripped)."""
from contextlib import asynccontextmanager

import uuid

from fastapi import Body, FastAPI, HTTPException, Request
from pydantic import BaseModel

from . import ask as ask_mod
from . import audit, cds, coding_api, db, priorauth_api, rag, summary
from .access import authorize
from .auth import CurrentUser, Principal
from .chart import load_chart
from .config import settings
from .fhir_client import FhirClient, FhirError
from .gateway import Gateway
from .llm import get_backend
from .redact import Redactor


class Runtime:
    """Process-wide collaborators. Tests swap them; production builds them from the environment."""
    fhir: FhirClient | None = None
    gateway: Gateway | None = None

    def get_fhir(self) -> FhirClient:
        self.fhir = self.fhir or FhirClient()
        return self.fhir

    def get_gateway(self) -> Gateway:
        self.gateway = self.gateway or Gateway(get_backend(), redactor=Redactor(rag.identity_of))
        return self.gateway


rt = Runtime()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_schema()
    yield
    db.close_pool()


app = FastAPI(title="CityForward Copilot", version="1.0", lifespan=lifespan)
app.include_router(priorauth_api.router)
app.include_router(coding_api.router)


def _chart(patient_id: str):
    try:
        return load_chart(rt.get_fhir(), patient_id)
    except FhirError as e:
        raise HTTPException(502 if e.status != 404 else 404, f"EHR: {e}") from e


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    audit.set_context(rid, request.client.host if request.client else None)
    response = await call_next(request)
    response.headers["x-request-id"] = rid
    return response


@app.get("/healthz")
def healthz():
    with db.session() as c:
        c.execute("SELECT 1")
    return {"ok": True, "model": rt.get_gateway().model, "embedder": __import__("copilot.embeddings", fromlist=["x"]).get_embedder().name}


@app.post("/api/patients/{patient_id}/index")
def index_patient(patient_id: str, p: Principal = CurrentUser):
    chart = _chart(patient_id)
    authorize(p, "index", patient_id, unit=chart.unit)
    return rag.index_chart(chart, p)


class AskIn(BaseModel):
    question: str


@app.post("/api/patients/{patient_id}/ask")
def ask_patient(patient_id: str, body: AskIn = Body(...), p: Principal = CurrentUser):
    authorize(p, "ask", patient_id)
    if not body.question.strip() or len(body.question) > 1000:
        raise HTTPException(422, "question must be 1-1000 characters")
    res = ask_mod.ask(rt.get_gateway(), patient_id, body.question.strip(), principal=p)
    return {**res.model_dump(), "cds": cds.envelope("ask", [{"ref": c.source_ref, "text": c.text} for c in res.citations], generated=res.mode == "llm")}


@app.post("/api/patients/{patient_id}/summary")
def patient_summary(patient_id: str, p: Principal = CurrentUser):
    authorize(p, "summary", patient_id)
    res = summary.summarize(rt.get_gateway(), _chart(patient_id), principal=p)
    return {**res.model_dump(), "cds": cds.envelope("summary", [{"ref": x.ref, "text": x.text} for x in res.sources.values()], generated=res.mode == "llm")}


@app.get("/api/about")
def about(p: Principal = CurrentUser):
    """What this tool is and is not (shown in the dashboard's 'About' panel)."""
    return {"intended_use": cds.INTENDED_USE, "method": cds.METHOD, "limitations": cds.LIMITS, "criteria": cds.CRITERIA}


@app.get("/api/audit")
def audit_search(patient: str | None = None, actor: str | None = None, action: str | None = None, p: Principal = CurrentUser):
    authorize(p, "audit.view")
    audit.log(p, "audit.view", patient, detail={"actor": actor, "action": action})
    return audit.search(patient, actor, action)


@app.get("/api/audit/verify")
def audit_verify(p: Principal = CurrentUser):
    authorize(p, "audit.view")
    return audit.verify_chain()
