import uuid

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from mock_backend.changes import get_cr, public
from mock_backend.clock import iso
from mock_backend.errors import ApiProblem
from mock_backend.identity import CallerDep
from mock_backend.runs import new_run, public_run
from mock_backend.state import StateDep, now_of

router = APIRouter()


@router.get("/change-requests")
def list_change_requests(_: CallerDep, state: StateDep, status: str | None = None, database: str | None = None):
    wanted = {s.strip() for s in status.split(",") if s.strip()} if status else None
    with state.lock:
        crs = [public(cr) for cr in state.change_requests.values()
               if (wanted is None or cr["status"] in wanted)
               and (database is None or cr["database"]["identifier"] == database)]
    return sorted(crs, key=lambda c: (c["created_at"], c["id"]), reverse=True)


@router.get("/change-requests/{id}")
def get_change_request(id: str, _: CallerDep, state: StateDep):
    with state.lock:
        return public(get_cr(state, id))


class RunBody(BaseModel):
    message: str = Field(min_length=1)


def _conversation(state, conv_id: str, caller) -> dict:
    conv = state.conversations.get(conv_id)
    if conv is None or conv["user"] != caller.username:
        raise ApiProblem(404, "not_found", f"No conversation {conv_id}")
    return conv


def _run(state, run_id: str, caller) -> dict:
    run = state.runs.get(run_id)
    if run is None or run["user"] != caller.username:
        raise ApiProblem(404, "not_found", f"No run {run_id}")
    return run


def _public_conversation(conv: dict) -> dict:
    return {"id": conv["id"], "title": conv["title"], "created_at": iso(conv["created_at"]),
            "updated_at": iso(conv["updated_at"])}


@router.get("/conversations")
def list_conversations(caller: CallerDep, state: StateDep):
    with state.lock:
        mine = [c for c in state.conversations.values() if c["user"] == caller.username]
        mine.sort(key=lambda c: c["updated_at"], reverse=True)
        return [_public_conversation(c) for c in mine]


@router.post("/conversations", status_code=201)
def create_conversation(request: Request, caller: CallerDep, state: StateDep):
    now = now_of(request)
    with state.lock:
        conv_id = f"conv-{uuid.uuid4().hex[:8]}"
        state.conversations[conv_id] = {"id": conv_id, "user": caller.username, "title": "New chat",
                                        "created_at": now, "updated_at": now, "messages": []}
        return {"id": conv_id}


@router.get("/conversations/{id}/messages")
def list_messages(id: str, caller: CallerDep, state: StateDep):
    with state.lock:
        return _conversation(state, id, caller)["messages"]


@router.post("/conversations/{id}/runs", status_code=201)
def start_run(id: str, body: RunBody, request: Request, caller: CallerDep, state: StateDep):
    now = now_of(request)
    with state.lock:
        conv = _conversation(state, id, caller)
        if any(r["conversation_id"] == id and r["status"] == "running" for r in state.runs.values()):
            raise ApiProblem(409, "run_active", "This conversation already has a run in progress")
        if not conv["messages"]:
            conv["title"] = body.message.strip()[:40]
        conv["messages"].append({"role": "user", "text": body.message, "tool_steps": [], "change_request_ids": [],
                                 "run_id": None})
        conv["updated_at"] = now
        run = new_run(state, id, caller.username, caller.role, body.message, now)
        return {"run_id": run["run_id"]}


@router.get("/runs")
def list_runs(conversation_id: str, caller: CallerDep, state: StateDep, status: str | None = None):
    with state.lock:
        return [public_run(r) for r in state.runs.values()
                if r["user"] == caller.username and r["conversation_id"] == conversation_id
                and (status is None or r["status"] == status)]


@router.get("/runs/{run_id}/events")
def run_events(run_id: str, caller: CallerDep, state: StateDep, after_seq: int = 0):
    with state.lock:
        run = _run(state, run_id, caller)
        return {"events": [e for e in run["events"] if e["seq"] > after_seq], "run_status": run["status"]}


@router.post("/runs/{run_id}/cancel", status_code=202)
def cancel_run(run_id: str, request: Request, caller: CallerDep, state: StateDep):
    now = now_of(request)
    with state.lock:
        run = _run(state, run_id, caller)
        if run["status"] == "running" and run["cancel_at"] is None:
            run["cancel_at"] = now
        return {}
