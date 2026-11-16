"""Scripted agent runs, replayed lazily from the clock."""

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from mock_backend.changes import RISK_TIER, audit_row, new_change_request, required_role
from mock_backend.clock import iso
from mock_backend.identity import Role
from mock_backend.state import MockState

TEXT_DELAY = 0.15
TOOL_DELAY = 1.2
OTHER_DELAY = 0.3
RESULT_LIMIT = 20_480
TERMINAL = {"run_completed": "completed", "run_failed": "failed", "run_cancelled": "cancelled"}


@dataclass(frozen=True)
class ScriptStep:
    delay_s: float
    event: dict | None = None
    propose: dict | None = None  # {"change_type", "database"}; becomes change_proposed when played


def truncate_result(result: Any, limit: int = RESULT_LIMIT) -> tuple[Any, bool]:
    text = json.dumps(result)
    if len(text.encode()) <= limit:
        return result, False
    return {"preview": text[:limit]}, True


def _text(*chunks: str) -> list[ScriptStep]:
    return [ScriptStep(TEXT_DELAY, {"type": "text_delta", "text": c}) for c in chunks]


def _tool(call_id: str, name: str, args: dict, result: Any) -> list[ScriptStep]:
    return [
        ScriptStep(OTHER_DELAY, {"type": "tool_call_started", "tool_call_id": call_id, "name": name, "args": args}),
        ScriptStep(TOOL_DELAY, {"type": "tool_call_finished", "tool_call_id": call_id, "status": "ok",
                                "result": result, "duration_ms": int(TOOL_DELAY * 1000)}),
    ]


def _describe(db: str, cls: str, engine: str) -> list[ScriptStep]:
    return _tool("t1", "rds.describe_db_instances", {"db_instance_identifier": db}, {
        "DBInstanceIdentifier": db, "DBInstanceClass": cls, "Engine": engine, "DBInstanceStatus": "available",
        "MultiAZ": True, "PreferredMaintenanceWindow": "sun:03:00-sun:03:30", "AllocatedStorage": 500,
    })


_PROPOSALS = [  # keyword, change type, database
    ("snapshot", "create_snapshot", "prod-orders-db"),
    ("tag", "modify_tags", "prod-orders-db"),
    ("restore", "restore_test", "prod-payments-db"),
    ("resize", "modify_instance_class", "prod-orders-db"),
]


def _mentions(text: str, keyword: str) -> bool:
    """Whole-word match, allowing simple inflections: "tags", "failed"; not "percentage" or "failover"."""
    return re.search(rf"\b{keyword}(s|es|ed|ing)?\b", text) is not None


def pick_script(message: str, role: Role) -> list[ScriptStep]:
    text = message.lower()
    started = [ScriptStep(0.0, {"type": "run_started"})]
    if _mentions(text, "fail"):
        return [*started, *_text("Looking into that… "),
                ScriptStep(OTHER_DELAY, {"type": "run_failed", "error": "Bedrock throttled the request"})]
    for keyword, change_type, db in _PROPOSALS:
        if not _mentions(text, keyword):
            continue
        steps = [*started, *_text(f"Checking {db} first. "),
                 *_describe(db, "db.r6g.large" if db == "prod-orders-db" else "db.r6g.xlarge", "aurora-postgresql")]
        if role.rank >= required_role(RISK_TIER[change_type]).rank:
            steps += [*_text(f"{db} is available. ", f"I've proposed a `{change_type}` change. "),
                      ScriptStep(OTHER_DELAY, propose={"change_type": change_type, "database": db}),
                      *_text("It needs approval on the Approvals page before anything happens.")]
        else:
            steps += _text(f"Your role ({role}) can't propose this change, so I haven't created a change request.")
        return [*steps, ScriptStep(OTHER_DELAY, {"type": "run_completed"})]
    return [
        *started, *_text("Let me look at prod-orders-db. "),
        *_describe("prod-orders-db", "db.r6g.large", "aurora-postgresql"),
        *_tool("t2", "cloudwatch.get_metric_statistics",
               {"namespace": "AWS/RDS", "metric_name": "CPUUtilization", "db_instance_identifier": "prod-orders-db",
                "period": 300, "statistics": ["Average", "Maximum"]},
               {"datapoints": [{"minutes_ago": m, "average": a, "maximum": x}
                               for m, a, x in [(15, 71.2, 88.0), (10, 79.5, 93.1), (5, 84.0, 96.4)]]}),
        *_text("prod-orders-db has ", "had CPU above 85% for the last 15 minutes ", "and about 9 days of free storage "
               "left at the current growth rate. ", "Consider a resize or a storage increase."),
        ScriptStep(OTHER_DELAY, {"type": "run_completed"}),
    ]


def _db_ref(state: MockState, identifier: str) -> dict:
    d = next(x for x in state.fleet if x["identifier"] == identifier)
    return {"identifier": d["identifier"], "kind": d["kind"], "engine": d["engine"], "region": d["region"]}


def _proposal_details(state: MockState, change_type: str, db: str, now: datetime) -> dict:
    stamp = now.strftime("%Y%m%d%H%M")
    match change_type:
        case "create_snapshot":
            snap = f"{db}-manual-{stamp}"
            return {"params": {"snapshot_identifier": snap}, "summary": f"Snapshot {db}",
                    "diff": [{"field": "snapshot", "before": None, "after": snap}]}
        case "modify_tags":
            return {"params": {"before": {}, "after": {"reviewed-by": "dbops"}},
                    "summary": f"Add reviewed-by tag to {db}",
                    "diff": [{"field": "tag:reviewed-by", "before": None, "after": "dbops"}]}
        case "restore_test":
            temp = f"restoretest-{db}-{stamp}"
            return {"params": {"source_snapshot": f"{db}-auto-{now:%Y%m%d}"}, "summary": f"Restore test of {db}",
                    "diff": [{"field": "temporary_instance", "before": None, "after": temp},
                             {"field": "instance_class", "before": None, "after": "db.t4g.medium"},
                             {"field": "estimated_cost_per_hour", "before": None, "after": "$0.07"}]}
        case "modify_instance_class":
            current = next(x for x in state.fleet if x["identifier"] == db)["instance_class"]
            target = "db.r6g.xlarge" if current == "db.r6g.large" else "db.r6g.2xlarge"
            return {"params": {"from_class": current, "to_class": target}, "summary": f"Resize {db} to {target}",
                    "diff": [{"field": "instance_class", "before": current, "after": target}]}
    raise ValueError(change_type)


def new_run(state: MockState, conversation_id: str, username: str, role: Role, message: str, now: datetime) -> dict:
    run = {"run_id": f"run-{uuid.uuid4().hex[:8]}", "conversation_id": conversation_id, "user": username,
           "status": "running", "started_at": now, "script": pick_script(message, role), "played": 0,
           "elapsed_s": 0.0, "events": [], "cancel_at": None}
    state.runs[run["run_id"]] = run
    return run


def public_run(run: dict) -> dict:
    return {"run_id": run["run_id"], "conversation_id": run["conversation_id"], "status": run["status"],
            "started_at": iso(run["started_at"])}


def _emit(run: dict, event: dict, at: datetime) -> dict:
    full = {"seq": len(run["events"]) + 1, **event, "ts": iso(at)}
    run["events"].append(full)
    return full


def _assistant_message(run: dict) -> dict:
    text, steps, crs = "", {}, []
    for e in run["events"]:
        match e["type"]:
            case "text_delta":
                text += e["text"]
            case "tool_call_started":
                steps[e["tool_call_id"]] = {"tool_call_id": e["tool_call_id"], "name": e["name"], "args": e["args"],
                                            "status": "error", "result": None, "truncated": False, "duration_ms": 0}
            case "tool_call_finished":
                steps[e["tool_call_id"]].update(status=e["status"], result=e["result"],
                                                truncated=e.get("truncated", False), duration_ms=e["duration_ms"])
            case "change_proposed":
                crs.append(e["change_request_id"])
            case "run_failed":
                text += f"\n\n⛔ {e['error']}" if text else f"⛔ {e['error']}"
    return {"role": "assistant", "text": text, "tool_steps": list(steps.values()), "change_request_ids": crs,
            "run_id": run["run_id"]}


def _finish(state: MockState, run: dict, status: str, at: datetime) -> None:
    run["status"] = status
    conv = state.conversations[run["conversation_id"]]
    conv["messages"].append(_assistant_message(run))
    conv["updated_at"] = at


def _play(state: MockState, run: dict, step: ScriptStep, at: datetime) -> None:
    if step.propose is not None:
        db = step.propose["database"]
        details = _proposal_details(state, step.propose["change_type"], db, at)
        cr = new_change_request(state, change_type=step.propose["change_type"], database=_db_ref(state, db),
                                requested_by=run["user"], now=at, conversation_id=run["conversation_id"], **details)
        _emit(run, {"type": "change_proposed", "change_request_id": cr["id"], "change_type": cr["change_type"],
                    "risk_tier": cr["risk_tier"]}, at)
        return
    event = dict(step.event)
    if event["type"] == "tool_call_finished":
        event["result"], truncated = truncate_result(event["result"])
        if truncated:
            event["truncated"] = True
        started = next(e for e in run["events"]
                       if e["type"] == "tool_call_started" and e["tool_call_id"] == event["tool_call_id"])
        audit_row(state, now=at, user=run["user"], action="chat.tool_call", tool=started["name"],
                  database=started["args"].get("db_instance_identifier"), result=event["status"],
                  detail=json.dumps(started["args"]))
    _emit(run, event, at)
    if event["type"] in TERMINAL:
        _finish(state, run, TERMINAL[event["type"]], at)


def advance_runs(state: MockState, now: datetime, time_scale: float) -> None:
    for run in state.runs.values():
        script = run["script"]
        while run["status"] == "running" and run["played"] < len(script):
            step = script[run["played"]]
            at = run["started_at"] + timedelta(seconds=(run["elapsed_s"] + step.delay_s) * time_scale)
            cancel_at = run["cancel_at"]
            if cancel_at is not None and at > cancel_at:
                _emit(run, {"type": "run_cancelled"}, cancel_at)
                _finish(state, run, "cancelled", cancel_at)
                break
            if at > now:
                break
            _play(state, run, step, at)
            run["elapsed_s"] += step.delay_s
            run["played"] += 1
