from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from agent_api.errors import ApiProblem
from agent_api.identity import CallerDep
from agent_api.store import RunActive, Store

router = APIRouter()
TITLE_LENGTH = 60


class RunBody(BaseModel):
    message: str = Field(min_length=1)


def _store(request: Request) -> Store:
    return request.app.state.store


def _own_conversation(store: Store, conv_id: str, caller) -> dict:
    conv = store.get_conversation(conv_id, caller.username)
    if conv is None:
        raise ApiProblem(404, "not_found", f"No conversation {conv_id}")
    return conv


def _own_run(store: Store, run_id: str, caller) -> dict:
    run = store.get_run(run_id, caller.username)
    if run is None:
        raise ApiProblem(404, "not_found", f"No run {run_id}")
    return run


def _public_run(run: dict) -> dict:
    return {k: run[k] for k in ("run_id", "conversation_id", "status", "started_at")}


@router.get("/conversations")
def list_conversations(request: Request, caller: CallerDep):
    return _store(request).list_conversations(caller.username)


@router.post("/conversations", status_code=201)
def create_conversation(request: Request, caller: CallerDep):
    return {"id": _store(request).create_conversation(caller.username)["id"]}


@router.get("/conversations/{id}/messages")
def list_messages(id: str, request: Request, caller: CallerDep):
    store = _store(request)
    _own_conversation(store, id, caller)
    return store.list_messages(id)


@router.post("/conversations/{id}/runs", status_code=201)
def start_run(id: str, body: RunBody, request: Request, caller: CallerDep):
    store = _store(request)
    _own_conversation(store, id, caller)
    try:
        run_id = store.start_run(id, caller.username, body.message, body.message.strip()[:TITLE_LENGTH])
    except RunActive:
        raise ApiProblem(409, "run_active", "This conversation already has a run in progress") from None
    request.app.state.runner.start(run_id, caller, id, body.message)
    return {"run_id": run_id}


@router.get("/runs")
def list_runs(conversation_id: str, request: Request, caller: CallerDep, status: str | None = None):
    return [_public_run(r) for r in _store(request).list_runs(conversation_id, caller.username, status)]


@router.get("/runs/{run_id}/events")
def run_events(run_id: str, request: Request, caller: CallerDep, after_seq: int = 0):
    store = _store(request)
    _own_run(store, run_id, caller)
    events = store.events_after(run_id, after_seq)
    # Re-read the status after the events so a finished run never shows a status older than its events.
    return {"events": events, "run_status": _own_run(store, run_id, caller)["status"]}


@router.post("/runs/{run_id}/cancel", status_code=202)
def cancel_run(run_id: str, request: Request, caller: CallerDep):
    store = _store(request)
    if _own_run(store, run_id, caller)["status"] == "running":
        store.request_cancel(run_id)
    return {}
