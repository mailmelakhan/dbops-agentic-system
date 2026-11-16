"""Folding run events into a drawable assistant turn. Pure: no Streamlit, no HTTP."""

from dataclasses import dataclass, field
from typing import Any

TERMINAL = {"run_completed": "completed", "run_failed": "failed", "run_cancelled": "cancelled"}


@dataclass
class ActiveRun:
    run_id: str
    last_seq: int = 0
    events: list[dict] = field(default_factory=list)


@dataclass
class ToolStep:
    tool_call_id: str
    name: str
    args: dict
    status: str | None
    result: Any
    truncated: bool
    duration_ms: int | None


@dataclass
class Turn:
    role: str
    text: str
    tool_steps: list[ToolStep]
    proposals: list[dict]
    status: str
    error: str | None


def apply_events(run: ActiveRun, events: list[dict]) -> None:
    """Append each event exactly once, in seq order, whatever pages arrive and however often."""
    for e in sorted(events, key=lambda e: e["seq"]):
        if e["seq"] > run.last_seq:
            run.events.append(e)
            run.last_seq = e["seq"]


def build_turn(events: list[dict]) -> Turn:
    text, steps, proposals, status, error = "", {}, [], "running", None
    for e in events:
        match e["type"]:
            case "text_delta":
                text += e["text"]
            case "tool_call_started":
                steps[e["tool_call_id"]] = ToolStep(e["tool_call_id"], e["name"], e.get("args", {}), None, None,
                                                    False, None)
            case "tool_call_finished":
                step = steps.get(e["tool_call_id"])
                if step is not None:
                    step.status = e["status"]
                    step.result = e.get("result")
                    step.truncated = bool(e.get("truncated", False))
                    step.duration_ms = e.get("duration_ms")
            case "change_proposed":
                proposals.append({k: e[k] for k in ("change_request_id", "change_type", "risk_tier")})
            case t if t in TERMINAL:
                status = TERMINAL[t]
                error = e.get("error")
    return Turn("assistant", text, list(steps.values()), proposals, status, error)


def turn_from_message(msg: dict) -> Turn:
    steps = [ToolStep(s["tool_call_id"], s["name"], s.get("args", {}), s["status"], s.get("result"),
                      bool(s.get("truncated", False)), s.get("duration_ms")) for s in msg.get("tool_steps", [])]
    proposals = [{"change_request_id": cr_id} for cr_id in msg.get("change_request_ids", [])]
    return Turn(msg["role"], msg["text"], steps, proposals, "completed", None)
