"""A tiny stand-in for the hospital gateway when you run the pieces without Docker.

    MediTrack  uvicorn meditrack.backend.app:app --port 8002      (PYTHONPATH=customer)
    Copilot    uvicorn copilot.app:app --port 8080                (PYTHONPATH=ai)
    this       python ai/scripts/devgateway.py                    -> http://localhost:8001  (/ai/* -> copilot with the prefix stripped, the rest -> MediTrack)

Why a gateway at all: the SMART app must be same-origin with the EHR so the browser can call both without CORS, exactly as in the hospital.
"""
import os

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response

MEDITRACK = os.getenv("MEDITRACK_UPSTREAM", "http://localhost:8002")
COPILOT = os.getenv("COPILOT_UPSTREAM", "http://localhost:8080")
app = FastAPI()
client = httpx.AsyncClient(timeout=60.0, follow_redirects=False)
HOP = {"connection", "keep-alive", "transfer-encoding", "content-length", "content-encoding", "host"}


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def proxy(path: str, request: Request):
    if path == "ai" or path.startswith("ai/"):
        upstream, rest = COPILOT, path[2:] or "/"
    else:
        upstream, rest = MEDITRACK, "/" + path
    url = upstream + (rest if rest.startswith("/") else "/" + rest)
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
    r = await client.request(request.method, url, params=request.query_params, content=await request.body(), headers=headers)
    out = [(k, v) for k, v in r.headers.multi_items() if k.lower() not in HOP]
    resp = Response(r.content, status_code=r.status_code)
    for k, v in out:
        resp.headers.append(k, v)
    return resp


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8001")))
