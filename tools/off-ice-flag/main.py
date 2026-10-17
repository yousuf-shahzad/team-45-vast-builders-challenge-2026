"""Lane D: FastAPI wiring only (contract version 2). Logic lives in the lane modules.

Every /api route takes an optional `source` query param: `chunks` (the Split game tab)
or `whole` (the Whole video tab). Without it, the default tab is used.
"""

import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

import decision
import eval as evaluation
import questions
import vss_client

HERE = Path(__file__).resolve().parent


def _warm():
    try:
        questions.current_state()
    except Exception as e:  # the first request retries and reports it
        print(f"warm-up failed: {type(e).__name__}: {e}", flush=True)


@asynccontextmanager
async def lifespan(app):
    threading.Thread(target=_warm, daemon=True).start()  # build the tape before the first visitor
    yield


app = FastAPI(title="Off-ice Flag", lifespan=lifespan)


def _err(code, **body):
    return JSONResponse(body, status_code=code)


@app.exception_handler(vss_client.UpstreamError)
@app.exception_handler(decision.UpstreamError)
@app.exception_handler(questions.Upstream)
def _upstream(request, exc):
    print(f"upstream: {request.url.path}: {exc}", flush=True)  # error text never carries a secret
    return _err(502, error="upstream")


@app.exception_handler(questions.NotReady)
def _not_ready(request, exc):
    return _err(409, error="not_ready", status=exc.status)


@app.middleware("http")
async def _fixture_header(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/") and questions.is_fixture():
        response.headers["X-Off-Ice-Fixture"] = "1"
    return response


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/sources")
def get_sources():
    return questions.sources()


@app.post("/api/sources/{source}")
async def load_source(source: str, request: Request):
    try:
        body = await request.json()
    except ValueError:
        body = {}
    try:
        return questions.load(source, body.get("videos") if isinstance(body, dict) else None)
    except ValueError as e:
        return _err(400, error=str(e))


@app.get("/api/library")
def library():
    return {"videos": vss_client.library()}


@app.get("/api/tape")
def tape(source: str = None):
    return questions.current_state(source).tape


@app.get("/api/segments/{card_id}/stream")
def stream(card_id: str, request: Request, source: str = None):
    state = questions.current_state(source)
    card = state.card(card_id)
    if card is None or state.fixture:
        return _err(404, error="unknown card" if card is None else "fixture has no video")
    status, headers, body = vss_client.stream(card["source"], request.headers.get("range"))
    return StreamingResponse(body, status_code=status, headers=headers)


@app.get("/api/segments/{card_id}/detections")
def detections(card_id: str, source: str = None):
    state = questions.current_state(source)
    if state.card(card_id) is None:
        return _err(404, error="unknown card")
    d = state.detections.get(card_id)
    return d if d else _err(502, error="upstream")


@app.get("/api/cards/{card_id}/why")
def why(card_id: str, source: str = None):
    state = questions.current_state(source)
    if state.card(card_id) is None:
        return _err(404, error="unknown card")
    w = state.whys.get(card_id)
    return w if w else _err(502, error="upstream")


@app.get("/api/questions")
def saved(source: str = None):
    return {"contract_version": 2, "questions": questions.current_state(source).questions}


@app.post("/api/ask")
async def ask(request: Request, source: str = None):
    try:
        body = await request.json()
    except ValueError:
        body = {}
    text = body.get("question") if isinstance(body, dict) else None
    if not isinstance(text, str) or not text.strip():
        return _err(400, error="empty question")
    return await run_in_threadpool(lambda: questions.ask(questions.current_state(source), text))


@app.get("/api/search")
def search(q: str = "", source: str = None):
    if not q.strip():
        return _err(400, error="empty query")
    return questions.search(questions.current_state(source), q)


@app.get("/api/eval")
def score(source: str = None):
    state = questions.current_state(source)
    if not state.ready:
        raise questions.NotReady(state.tape["status"])
    labels = evaluation.load_labels(questions.FIXTURES, state.fixture)
    if labels is None:
        return _err(409, error="not_ready", status="no_labels")
    return evaluation.score(labels, state.cards)


if (HERE / "static").is_dir():
    app.mount("/", StaticFiles(directory=HERE / "static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
