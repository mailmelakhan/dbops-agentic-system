import uuid

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from mock_backend import reports
from mock_backend.audit import audit_facets, query_audit
from mock_backend.changes import get_cr, propose_rollback, public
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


@router.post("/change-requests/{id}/rollback", status_code=201)
def rollback(id: str, request: Request, caller: CallerDep, state: StateDep):
    with state.lock:
        return {"change_request_id": propose_rollback(state, id, caller, now_of(request))["id"]}


@router.get("/audit")
def audit(_: CallerDep, state: StateDep, request: Request, user: str | None = None, tool: str | None = None,
          database: str | None = None, to: str | None = None, limit: int = 50, cursor: str | None = None):
    from_ = request.query_params.get("from")
    with state.lock:
        return query_audit(state.audit, user=user, tool=tool, database=database, from_=from_, to=to, limit=limit,
                           cursor=cursor)


@router.get("/audit/facets")
def facets(_: CallerDep, state: StateDep):
    with state.lock:
        return audit_facets(state.audit)


class AuditFilters(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    user: str | None = None
    tool: str | None = None
    database: str | None = None
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None


@router.get("/reports")
def list_reports(_: CallerDep, state: StateDep):
    with state.lock:
        items = [reports.public_report(r) for r in state.reports.values()]
    return sorted(items, key=lambda r: (r["created_at"], r["id"]), reverse=True)


@router.get("/reports/{id}")
def get_report(id: str, _: CallerDep, state: StateDep):
    with state.lock:
        return reports.public_report(reports.get_report(state, id))


@router.get("/reports/{id}/download")
def download_report(id: str, format: str, _: CallerDep, state: StateDep):
    with state.lock:
        body = reports.report_file(state, id, format)
    media = "text/csv" if format == "csv" else "text/markdown"
    return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{id}.{format}"'})


@router.post("/reports/change-history", status_code=202)
def start_change_history(body: AuditFilters, request: Request, caller: CallerDep, state: StateDep):
    filters = {"user": body.user, "tool": body.tool, "database": body.database, "from": body.from_, "to": body.to}
    with state.lock:
        report_id = reports.start_report(state, kind="change_history", title="Change history",
                                         created_by=caller.username, now=now_of(request),
                                         build=reports.change_history_builder(state, filters))
    return {"report_id": report_id}
