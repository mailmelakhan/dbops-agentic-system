"""Internal endpoints used only by agent-api. Not part of the public contract, so hidden from the schema."""

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from mock_backend.changes import audit_row, new_change_request, public
from mock_backend.errors import ApiProblem
from mock_backend.identity import InternalCallerDep
from mock_backend.state import StateDep, now_of

router = APIRouter(prefix="/internal", include_in_schema=False)


class DatabaseRef(BaseModel):
    identifier: str
    kind: Literal["instance", "cluster"]
    engine: str
    region: str


class ChangeRequestBody(BaseModel):
    change_type: Literal["modify_tags", "create_snapshot", "delete_snapshot", "restore_test",
                         "modify_instance_class"]
    database: DatabaseRef
    requested_by: str
    summary: str
    params: dict
    diff: list[dict]
    impact: str
    rollback_plan: list[str]
    maintenance_window: dict
    conversation_id: str | None = None


class AuditBody(BaseModel):
    user: str
    action: str
    result: str
    tool: str | None = None
    database: str | None = None
    detail: str | None = None
    ts: str | None = None


def _known_user(request: Request, username: str) -> None:
    if username not in request.app.state.roles:
        raise ApiProblem(422, "validation_error", f"Unknown user {username!r}")


@router.post("/change-requests", status_code=201)
def create_change_request(body: ChangeRequestBody, request: Request, _: InternalCallerDep, state: StateDep):
    _known_user(request, body.requested_by)
    with state.lock:
        cr = new_change_request(
            state, change_type=body.change_type, database=body.database.model_dump(),
            requested_by=body.requested_by, params=body.params, diff=body.diff, summary=body.summary,
            now=now_of(request), conversation_id=body.conversation_id, impact=body.impact,
            rollback_plan=body.rollback_plan, maintenance_window=body.maintenance_window)
        return public(cr)


@router.post("/audit", status_code=201)
def create_audit_row(body: AuditBody, request: Request, _: InternalCallerDep, state: StateDep):
    _known_user(request, body.user)
    with state.lock:
        audit_row(state, now=now_of(request), user=body.user, action=body.action, result=body.result,
                  tool=body.tool, database=body.database, detail=body.detail or "")
        if body.ts is not None:
            state.audit[-1]["ts"] = body.ts
        return dict(state.audit[-1])
