from datetime import timedelta

from fastapi import APIRouter, Depends, Request

from mock_backend.clock import iso
from mock_backend.errors import ApiProblem
from mock_backend.identity import Caller, get_caller
from mock_backend.state import MockState, get_state, now_of

router = APIRouter()


@router.get("/fleet")
def fleet(env: str | None = None, engine: str | None = None,
          _: Caller = Depends(get_caller), state: MockState = Depends(get_state)):
    with state.lock:
        dbs = [d for d in state.fleet
               if (env is None or d["env"] == env) and (engine is None or d["engine"] == engine)]
        return {"as_of": iso(state.as_of), "databases": dbs}


@router.get("/fleet/summary")
def summary(_: Caller = Depends(get_caller), state: MockState = Depends(get_state)):
    with state.lock:
        days = [d["storage"]["days_to_full"] for d in state.fleet]
        return {
            "as_of": iso(state.as_of),
            "total": len(state.fleet),
            "unhealthy": sum(d["health_score"] < 50 for d in state.fleet),
            "under_30_days_to_full": sum(x is not None and x < 30 for x in days),
            "open_alarms": sum(d["open_alarms"] for d in state.fleet),
        }


@router.get("/fleet/{identifier}/alarms")
def alarms(identifier: str, _: Caller = Depends(get_caller), state: MockState = Depends(get_state)):
    with state.lock:
        if not any(d["identifier"] == identifier for d in state.fleet):
            raise ApiProblem(404, "not_found", f"No database {identifier!r}")
        return state.alarms.get(identifier, [])


@router.post("/refresh", status_code=202)
def refresh(request: Request, _: Caller = Depends(get_caller), state: MockState = Depends(get_state)):
    now = now_of(request)
    with state.lock:
        if state.last_refresh is not None and now - state.last_refresh < timedelta(seconds=60):
            raise ApiProblem(429, "rate_limited", "A refresh was already requested in the last minute")
        state.last_refresh = now
        state.as_of = now
        return {}
