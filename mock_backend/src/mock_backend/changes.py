"""Change requests: creation, the executor's rules and a lazily advanced lifecycle.

Internal bookkeeping lives in keys starting with "_" and is stripped by public().
"""

import math
from datetime import datetime, timedelta

from mock_backend.clock import iso
from mock_backend.errors import ApiProblem
from mock_backend.identity import Caller, Role
from mock_backend.state import MockState

RISK_TIER = {
    "modify_tags": "LOW",
    "create_snapshot": "LOW",
    "delete_snapshot": "LOW",
    "restore_test": "MEDIUM",
    "modify_instance_class": "HIGH",
}
STEPS = {
    "modify_tags": ["applying", "done"],
    "create_snapshot": ["creating", "available"],
    "delete_snapshot": ["deleting", "deleted"],
    "restore_test": ["restoring", "available", "verified", "cleaned_up"],
    "modify_instance_class": ["pre_snapshot", "modifying", "available"],
}
NEXT_WINDOW_STEPS = ["pre_snapshot", "scheduled", "applied"]
STEP_SECONDS = 2.0
APPLY_AFTER_SECONDS = 60.0
CONFIRM_SECONDS = 2.0
MAINTENANCE_WINDOW = "sun:03:00-sun:03:30"
DRIFTING = {"CR-0139"}  # mock rule: approving these always reports drift
CLEANUP_FAILS_ON = "legacy-reports-db"  # mock rule: restore tests of this database fail cleanup


def required_role(tier: str) -> Role:
    return Role.SAFE_WRITE if tier == "LOW" else Role.PRIVILEGED


def public(cr: dict) -> dict:
    return {k: v for k, v in cr.items() if not k.startswith("_")}


def audit_row(state: MockState, *, now: datetime, user: str, action: str, result: str = "ok",
              tool: str | None = None, database: str | None = None, change_request_id: str | None = None,
              detail: str = "") -> None:
    state.audit.append({
        "id": f"aud-{len(state.audit) + 1:05d}", "ts": iso(now), "user": user, "action": action, "tool": tool,
        "database": database, "result": result, "change_request_id": change_request_id, "detail": detail,
    })


def next_window_start(now: datetime) -> datetime:
    """Next Sunday 03:00 UTC strictly after now."""
    days_ahead = (6 - now.weekday()) % 7
    start = (now + timedelta(days=days_ahead)).replace(hour=3, minute=0, second=0, microsecond=0)
    return start if start > now else start + timedelta(days=7)


def _impact_and_rollback(cr_id: str, change_type: str, params: dict) -> tuple[str, list[str]]:
    match change_type:
        case "modify_tags":
            return "No downtime; tags only", ["Re-apply the previous tag set"]
        case "create_snapshot":
            return "No downtime; snapshot I/O may briefly add latency", ["Delete the snapshot"]
        case "delete_snapshot":
            return "No downtime; the snapshot is deleted permanently", ["None: a deleted snapshot can't be restored"]
        case "restore_test":
            return ("Temporary instance costs about $0.10/hour while it runs; no impact on the source",
                    ["Delete the temporary instance (also done automatically)"])
        case "modify_instance_class":
            return ("~5–10 min downtime (Multi-AZ failover)",
                    [f"Snapshot pre-{cr_id} taken automatically", f"Modify back to {params.get('from_class')}"])
    raise ValueError(change_type)


def new_change_request(state: MockState, *, change_type: str, database: dict, requested_by: str, params: dict,
                       diff: list[dict], summary: str, now: datetime, conversation_id: str | None = None,
                       rollback_of: str | None = None, requested_via: str = "chat") -> dict:
    cr_id = f"CR-{state.next_cr_number:04d}"
    state.next_cr_number += 1
    tier = RISK_TIER[change_type]
    impact, rollback_plan = _impact_and_rollback(cr_id, change_type, params)
    if tier == "HIGH":
        window = {"required": True, "window": MAINTENANCE_WINDOW, "inside_now": False,
                  "next_start": iso(next_window_start(now))}
    else:
        window = {"required": False, "window": None, "inside_now": None, "next_start": None}
    cr = {
        "id": cr_id, "change_type": change_type, "risk_tier": tier, "database": database,
        "requested_by": requested_by, "requested_via": requested_via, "conversation_id": conversation_id,
        "created_at": iso(now), "summary": summary, "params": params, "diff": diff, "impact": impact,
        "rollback_plan": rollback_plan, "maintenance_window": window, "status": "pending", "approval": None,
        "progress": [{"step": s, "state": "pending", "at": None} for s in STEPS[change_type]],
        "result": None, "rollback_of": rollback_of, "status_reason": None,
    }
    state.change_requests[cr_id] = cr
    audit_row(state, now=now, user=requested_by, action="change.proposed", database=database["identifier"],
              change_request_id=cr_id, detail=summary)
    return cr


def get_cr(state: MockState, cr_id: str) -> dict:
    cr = state.change_requests.get(cr_id)
    if cr is None:
        raise ApiProblem(404, "not_found", f"No change request {cr_id}")
    return cr


def _require_status(cr: dict, *statuses: str) -> None:
    if cr["status"] not in statuses:
        raise ApiProblem(409, "invalid_state", f"{cr['id']} is {cr['status']}, expected {' or '.join(statuses)}")


def _deny(state: MockState, cr: dict, caller: Caller, now: datetime, action: str, code: str, message: str):
    audit_row(state, now=now, user=caller.username, action=action, result="denied",
              database=cr["database"]["identifier"], change_request_id=cr["id"], detail=message)
    raise ApiProblem(403, code, message)


def _check_decider(state: MockState, cr: dict, caller: Caller, now: datetime, action: str) -> None:
    if caller.username == cr["requested_by"]:
        _deny(state, cr, caller, now, action, "self_approval", "You can't approve or reject your own request")
    needed = required_role(cr["risk_tier"])
    if caller.role.rank < needed.rank:
        _deny(state, cr, caller, now, action, "forbidden_role",
              f"Role {caller.role} can't decide {cr['risk_tier']} changes (needs {needed})")


def _approval(caller: Caller, now: datetime, decision: str, mode: str | None = None,
              override_reason: str | None = None, reason: str | None = None) -> dict:
    return {"decision": decision, "decided_by": caller.username, "decided_at": iso(now), "mode": mode,
            "override_reason": override_reason, "reason": reason}


def approve(state: MockState, cr_id: str, caller: Caller, now: datetime, mode: str,
            override_reason: str | None) -> str:
    cr = get_cr(state, cr_id)
    _require_status(cr, "pending")
    _check_decider(state, cr, caller, now, "change.approve_denied")
    high = cr["risk_tier"] == "HIGH"
    reason = (override_reason or "").strip() or None
    if high and mode == "now" and not cr["maintenance_window"]["inside_now"] and reason is None:
        raise ApiProblem(422, "window_override_reason_required",
                         "Applying now outside the maintenance window needs a written override reason")
    if cr_id in DRIFTING:
        raise ApiProblem(409, "drifted", f"{cr['database']['identifier']} no longer matches the diff's before values")
    effective_mode = mode if high else "now"
    cr["approval"] = _approval(caller, now, "approved", mode=effective_mode,
                               override_reason=reason if high and effective_mode == "now" else None)
    audit_row(state, now=now, user=caller.username, action="change.approved", database=cr["database"]["identifier"],
              change_request_id=cr_id, detail=f"mode={effective_mode}" + (f"; override: {reason}" if reason else ""))
    if effective_mode == "next_window":
        cr["status"] = "scheduled"
        cr["progress"] = [{"step": "pre_snapshot", "state": "done", "at": iso(now)},
                          {"step": "scheduled", "state": "active", "at": iso(now)},
                          {"step": "applied", "state": "pending", "at": None}]
        cr["_apply_after"] = APPLY_AFTER_SECONDS
        cr["_scheduled_at"] = now
    else:
        cr["status"] = "executing"
        cr["_started_at"] = now
        cr["progress"][0].update(state="active", at=iso(now))
    return cr["status"]


def reject(state: MockState, cr_id: str, caller: Caller, now: datetime, reason: str) -> str:
    cr = get_cr(state, cr_id)
    _require_status(cr, "pending")
    _check_decider(state, cr, caller, now, "change.reject_denied")
    cr["status"] = "rejected"
    cr["status_reason"] = reason
    cr["approval"] = _approval(caller, now, "rejected", reason=reason)
    audit_row(state, now=now, user=caller.username, action="change.rejected", database=cr["database"]["identifier"],
              change_request_id=cr_id, detail=reason)
    return "rejected"


def withdraw(state: MockState, cr_id: str, caller: Caller, now: datetime) -> str:
    cr = get_cr(state, cr_id)
    if caller.username != cr["requested_by"]:
        _deny(state, cr, caller, now, "change.withdraw_denied", "not_requester",
              "Only the requester can withdraw this request")
    _require_status(cr, "pending")
    cr["status"] = "withdrawn"
    audit_row(state, now=now, user=caller.username, action="change.withdrawn", database=cr["database"]["identifier"],
              change_request_id=cr_id)
    return "withdrawn"


def cancel(state: MockState, cr_id: str, caller: Caller, now: datetime, reason: str) -> str:
    cr = get_cr(state, cr_id)
    if caller.role is not Role.PRIVILEGED:
        _deny(state, cr, caller, now, "change.cancel_denied", "forbidden_role", "Only PRIVILEGED users can cancel")
    _require_status(cr, "scheduled")
    cr["_cancel_requested_at"] = now
    cr["status_reason"] = reason
    audit_row(state, now=now, user=caller.username, action="change.cancel_requested",
              database=cr["database"]["identifier"], change_request_id=cr_id, detail=reason)
    return "scheduled"


def retry_cleanup(state: MockState, cr_id: str, caller: Caller, now: datetime) -> str:
    cr = get_cr(state, cr_id)
    if caller.role is not Role.PRIVILEGED:
        _deny(state, cr, caller, now, "change.retry_denied", "forbidden_role",
              "Only PRIVILEGED users can retry cleanup")
    _require_status(cr, "failed_cleanup_pending")
    cr["_retry_requested_at"] = now
    cr["progress"][-1].update(state="active", at=iso(now))
    audit_row(state, now=now, user=caller.username, action="change.cleanup_retry",
              database=cr["database"]["identifier"], change_request_id=cr_id)
    return "failed_cleanup_pending"


def _due(start: datetime, seconds: float, now: datetime, time_scale: float) -> datetime | None:
    """When start + seconds*time_scale has passed, return that moment, else None."""
    at = start + timedelta(seconds=seconds * time_scale)
    return at if now >= at else None


def _finish(state: MockState, cr: dict, now: datetime, outcome: str, message: str) -> None:
    cr["result"] = {"outcome": outcome, "message": message, "report_id": None}


def _advance_executing(state: MockState, cr: dict, now: datetime, time_scale: float) -> None:
    started: datetime = cr["_started_at"]
    steps = cr["progress"]
    step_len = STEP_SECONDS * time_scale
    elapsed = (now - started).total_seconds()
    done = len(steps) if step_len <= 0 else min(len(steps), math.floor(elapsed / step_len))
    fails_cleanup = cr["change_type"] == "restore_test" and cr["database"]["identifier"] == CLEANUP_FAILS_ON
    for i, step in enumerate(steps):
        finished_at = iso(started + timedelta(seconds=(i + 1) * step_len))
        if i < done:
            if fails_cleanup and i == len(steps) - 1:
                step.update(state="failed", at=finished_at)
            else:
                step.update(state="done", at=finished_at)
        elif i == done:
            step.update(state="active", at=step["at"] or iso(started + timedelta(seconds=i * step_len)))
        else:
            step.update(state="pending", at=None)
    if done < len(steps):
        return
    del cr["_started_at"]
    db = cr["database"]["identifier"]
    if fails_cleanup:
        cr["status"] = "failed_cleanup_pending"
        _finish(state, cr, now, "failed", "Cleanup failed after 3 retries; the temporary instance is still running")
        audit_row(state, now=now, user="executor", action="change.cleanup_failed", result="error", database=db,
                  change_request_id=cr["id"])
    else:
        cr["status"] = "succeeded"
        _finish(state, cr, now, "succeeded", f"{cr['summary']}: done")
        audit_row(state, now=now, user="executor", action="change.executed", database=db, change_request_id=cr["id"])


def advance_change_requests(state: MockState, now: datetime, time_scale: float) -> None:
    for cr in state.change_requests.values():
        db = cr["database"]["identifier"]
        if cr["status"] == "executing" and "_started_at" in cr:
            _advance_executing(state, cr, now, time_scale)
        elif cr["status"] == "scheduled" and "_cancel_requested_at" in cr:
            if at := _due(cr["_cancel_requested_at"], CONFIRM_SECONDS, now, time_scale):
                del cr["_cancel_requested_at"]
                cr["status"] = "cancelled"
                cr["progress"][1].update(state="failed", at=iso(at))
                audit_row(state, now=at, user="executor", action="change.cancelled", database=db,
                          change_request_id=cr["id"], detail=cr["status_reason"] or "")
        elif cr["status"] == "scheduled" and "_scheduled_at" in cr:
            if at := _due(cr["_scheduled_at"], cr["_apply_after"], now, time_scale):
                del cr["_scheduled_at"]
                cr["progress"][1].update(state="done", at=iso(at))
                cr["progress"][2].update(state="done", at=iso(at))
                cr["status"] = "succeeded"
                _finish(state, cr, at, "succeeded", f"{cr['summary']}: applied in the maintenance window")
                audit_row(state, now=at, user="executor", action="change.executed", database=db,
                          change_request_id=cr["id"])
        elif (cr["status"] == "failed_cleanup_pending" and "_retry_requested_at" in cr
              and (at := _due(cr["_retry_requested_at"], CONFIRM_SECONDS, now, time_scale))):
            del cr["_retry_requested_at"]
            cr["progress"][-1].update(state="done", at=iso(at))
            cr["status"] = "succeeded"
            _finish(state, cr, at, "succeeded", "Cleanup retried; the temporary instance was deleted")
            audit_row(state, now=at, user="executor", action="change.cleanup_retried", database=db,
                      change_request_id=cr["id"])
