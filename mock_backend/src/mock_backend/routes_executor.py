from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, field_validator

from mock_backend import changes
from mock_backend.identity import CallerDep
from mock_backend.state import StateDep, now_of

router = APIRouter()


class ApproveBody(BaseModel):
    mode: Literal["now", "next_window"]
    override_reason: str | None = None


class ReasonBody(BaseModel):
    reason: str

    @field_validator("reason")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a reason is required")
        return value.strip()


@router.post("/change-requests/{id}/approve", status_code=202)
def approve(id: str, body: ApproveBody, request: Request, caller: CallerDep, state: StateDep):
    with state.lock:
        return {"status": changes.approve(state, id, caller, now_of(request), body.mode, body.override_reason)}


@router.post("/change-requests/{id}/reject")
def reject(id: str, body: ReasonBody, request: Request, caller: CallerDep, state: StateDep):
    with state.lock:
        return {"status": changes.reject(state, id, caller, now_of(request), body.reason)}


@router.post("/change-requests/{id}/withdraw")
def withdraw(id: str, request: Request, caller: CallerDep, state: StateDep):
    with state.lock:
        return {"status": changes.withdraw(state, id, caller, now_of(request))}


@router.post("/change-requests/{id}/cancel", status_code=202)
def cancel(id: str, body: ReasonBody, request: Request, caller: CallerDep, state: StateDep):
    with state.lock:
        return {"status": changes.cancel(state, id, caller, now_of(request), body.reason)}


@router.post("/change-requests/{id}/retry-cleanup", status_code=202)
def retry_cleanup(id: str, request: Request, caller: CallerDep, state: StateDep):
    with state.lock:
        return {"status": changes.retry_cleanup(state, id, caller, now_of(request))}
