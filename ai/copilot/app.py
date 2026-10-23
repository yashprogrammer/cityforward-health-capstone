"""The Copilot HTTP API (FastAPI). Mounted by the hospital gateway under /ai (prefix stripped)."""
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, HTTPException
from pydantic import BaseModel

from . import ask as ask_mod
from . import coding_api, db, priorauth_api, rag, summary
from .access import authorize
from .auth import CurrentUser, Principal
from .chart import load_chart
from .config import settings
from .fhir_client import FhirClient, FhirError
from .gateway import Gateway
from .llm import get_backend


class Runtime:
    """Process-wide collaborators. Tests swap them; production builds them from the environment."""
    fhir: FhirClient | None = None
    gateway: Gateway | None = None

    def get_fhir(self) -> FhirClient:
        self.fhir = self.fhir or FhirClient()
        return self.fhir

    def get_gateway(self) -> Gateway:
        self.gateway = self.gateway or Gateway(get_backend())
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
    return ask_mod.ask(rt.get_gateway(), patient_id, body.question.strip(), principal=p)


@app.post("/api/patients/{patient_id}/summary")
def patient_summary(patient_id: str, p: Principal = CurrentUser):
    authorize(p, "summary", patient_id)
    return summary.summarize(rt.get_gateway(), _chart(patient_id), principal=p)
