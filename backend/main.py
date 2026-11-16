"""FastAPI entry point.  Run:  uvicorn backend.main:app --port 8000"""
from __future__ import annotations

import os
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, Response  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from backend import ai, intent as intent_mod, orchestrator, planner, security  # noqa: E402
from backend.logger import get_logger  # noqa: E402
from backend.models import ChatRequest, ChatResponse  # noqa: E402
from backend.tools import registry  # noqa: E402
from backend.utils.helpers import PROJECT_ROOT, TASK_ID_RE  # noqa: E402

log = get_logger("webpilot.api")
FRONTEND = PROJECT_ROOT / "frontend"
app = FastAPI(title="WebPilot AI", version="1.0.0", docs_url="/api/docs", redoc_url=None)

if security.allow_private():
    log.warning("WEBPILOT_ALLOW_PRIVATE_TEST=1: SSRF protection is RELAXED (tests only!)")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    if not request.url.path.startswith("/api/docs") and request.url.path != "/openapi.json":
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; font-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'")
    return resp


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "webpilot-ai", "version": app.version,
            "groq_configured": ai.groq_configured(), "tools": [t["name"] for t in registry.describe()],
            "time": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    message = req.message.strip()
    if not message:
        raise HTTPException(422, "message must not be blank")
    log.info("chat request (%d chars)", len(message))
    try:
        intent = intent_mod.parse_intent(message)
    except intent_mod.NoUrlFound as exc:
        tid = orchestrator.create_rejected_task(message, str(exc))
        return JSONResponse(status_code=400, content=orchestrator.snapshot(tid).model_dump())
    plan = planner.build_plan(intent)
    try:  # reject unsafe URLs before any task is started
        security.validate_url(plan.url)
    except security.UrlRejected as exc:
        tid = orchestrator.create_rejected_task(message, f"URL rejected: {exc}", intent, plan)
        return JSONResponse(status_code=400, content=orchestrator.snapshot(tid).model_dump())

    tid = orchestrator.create_task(message, intent, plan)
    if req.wait:
        orchestrator.run_task(tid)
        return orchestrator.snapshot(tid)
    orchestrator.start_background(tid)
    return JSONResponse(status_code=202, content=orchestrator.snapshot(tid).model_dump())


@app.get("/api/tasks/{task_id}", response_model=ChatResponse)
def get_task(task_id: str):
    if not TASK_ID_RE.match(task_id):
        raise HTTPException(400, "Invalid task id")
    snap = orchestrator.snapshot(task_id)
    if snap is None:
        raise HTTPException(404, "Task not found")
    return snap


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(FRONTEND / "index.html")


app.mount("/assets", StaticFiles(directory=FRONTEND), name="assets")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host=os.environ.get("HOST", "127.0.0.1"),
                port=int(os.environ.get("PORT", "8000")))
