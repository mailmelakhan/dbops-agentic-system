from datetime import timedelta

from fastapi import APIRouter, Request
from pydantic import BaseModel

from mock_backend import reports
from mock_backend.clock import iso
from mock_backend.errors import ApiProblem
from mock_backend.identity import CallerDep
from mock_backend.state import StateDep, now_of

router = APIRouter()


@router.get("/fleet")
def fleet(_: CallerDep, state: StateDep, env: str | None = None, engine: str | None = None):
    with state.lock:
        dbs = [d for d in state.fleet
               if (env is None or d["env"] == env) and (engine is None or d["engine"] == engine)]
        return {"as_of": iso(state.as_of), "databases": dbs}


@router.get("/fleet/summary")
def summary(_: CallerDep, state: StateDep):
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
def alarms(identifier: str, _: CallerDep, state: StateDep):
    with state.lock:
        if not any(d["identifier"] == identifier for d in state.fleet):
            raise ApiProblem(404, "not_found", f"No database {identifier!r}")
        return state.alarms.get(identifier, [])


@router.post("/refresh", status_code=202)
def refresh(request: Request, _: CallerDep, state: StateDep):
    now = now_of(request)
    with state.lock:
        if state.last_refresh is not None and now - state.last_refresh < timedelta(seconds=60):
            raise ApiProblem(429, "rate_limited", "A refresh was already requested in the last minute")
        state.last_refresh = now
        state.as_of = now
        return {}


class FleetReportBody(BaseModel):
    env: str | None = None


@router.post("/reports/fleet-health", status_code=202)
def start_fleet_report(request: Request, caller: CallerDep, state: StateDep, body: FleetReportBody | None = None):
    env = body.env if body else None
    title = f"Fleet health ({env})" if env else "Fleet health (all envs)"
    with state.lock:
        report_id = reports.start_report(state, kind="fleet_health", title=title, created_by=caller.username,
                                         now=now_of(request), build=reports.fleet_health_builder(state, env))
    return {"report_id": report_id}
