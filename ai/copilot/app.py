"""The Copilot HTTP API (FastAPI). Mounted by the hospital gateway under /ai (prefix stripped)."""
from contextlib import asynccontextmanager

import os
import uuid
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ask as ask_mod
from . import audit, breakglass, cds, coding_api, dashboard, db, events, priorauth_api, rag, summary, telemetry, usage
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
    telemetry.start_metrics_server()
    events.start_worker()
    yield
    db.close_pool()


app = FastAPI(title="CityForward Copilot", version="1.0", lifespan=lifespan)
app.include_router(priorauth_api.router)
app.include_router(coding_api.router)
app.include_router(breakglass.router)
app.include_router(events.router)
app.include_router(dashboard.router)
app.include_router(usage.router)


def _chart(patient_id: str):
    try:
        return load_chart(rt.get_fhir(), patient_id)
    except FhirError as e:
        raise HTTPException(502 if e.status != 404 else 404, f"EHR: {e}") from e


@app.middleware("http")
async def request_context(request: Request, call_next):
    import time
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    audit.set_context(rid, request.client.host if request.client else None)
    t0 = time.perf_counter()
    response = await call_next(request)
    route = getattr(request.scope.get("route"), "path", None) or "unmatched"
    telemetry.HTTP_SECONDS.labels(request.method, route, str(response.status_code)).observe(time.perf_counter() - t0)
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
    if res.mode != "llm":
        telemetry.FALLBACKS.labels("ask").inc()
    return {**res.model_dump(), "cds": cds.envelope("ask", [{"ref": c.source_ref, "text": c.text} for c in res.citations], generated=res.mode == "llm")}


@app.post("/api/patients/{patient_id}/summary")
def patient_summary(patient_id: str, p: Principal = CurrentUser):
    authorize(p, "summary", patient_id)
    res = summary.summarize_cached(rt.get_gateway(), _chart(patient_id), p)
    if res.mode != "llm":
        telemetry.FALLBACKS.labels("summary").inc()
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


# ---------------------------------------------------------------------------------------------- the SMART app (built React bundle)
DASH = Path(os.getenv("DASHBOARD_DIR", Path(__file__).resolve().parents[1] / "dashboard" / "dist"))

if (DASH / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=DASH / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    @app.get("/launch", include_in_schema=False)
    @app.get("/auth/callback", include_in_schema=False)
    def spa():
        return FileResponse(DASH / "index.html", headers={"Cache-Control": "no-store"})


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    # the app talks only to its own origin (the gateway) and may be embedded only by the EHR page on the same site
    response.headers.setdefault("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'")
    if request.url.path.startswith(("/api", "/webhooks")):
        response.headers["Cache-Control"] = "no-store"
    return response


telemetry.setup(app)
