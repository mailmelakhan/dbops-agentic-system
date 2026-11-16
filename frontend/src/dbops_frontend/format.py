"""Pure formatting: colour bands, labels with words or icons, badge and diff HTML, plain-words errors."""

import html
import json
from enum import StrEnum

from dbops_frontend.api.base import ApiError

GREEN, AMBER, RED = "#22c55e", "#f59e0b", "#ef4444"
DARK_TEXT, WHITE = "#111827", "#ffffff"
RISK_STYLE = {"LOW": ("#16a34a", WHITE), "MEDIUM": ("#f59e0b", DARK_TEXT), "HIGH": ("#dc2626", WHITE)}


class Band(StrEnum):
    GREEN = "green"
    AMBER = "amber"
    RED = "red"
    NEUTRAL = "neutral"


BAND_COLOR = {Band.GREEN: GREEN, Band.AMBER: AMBER, Band.RED: RED, Band.NEUTRAL: ""}
_BAND_ICON = {Band.GREEN: "✅", Band.AMBER: "⚠", Band.RED: "⛔", Band.NEUTRAL: ""}


def score_band(score: int) -> Band:
    if score >= 80:
        return Band.GREEN
    return Band.AMBER if score >= 50 else Band.RED


def score_label(score: int) -> str:
    return f"{_BAND_ICON[score_band(score)]} {score}"


def days_band(days: int | None) -> Band:
    if days is None or days > 30:
        return Band.NEUTRAL
    return Band.RED if days <= 14 else Band.AMBER


def days_label(days: int | None) -> str:
    if days is None:
        return "—"
    icon = _BAND_ICON[days_band(days)]
    return f"{icon} {days} d" if icon else f"{days} d"


def risk_badge_html(tier: str) -> str:
    bg, fg = RISK_STYLE.get(tier, ("#6b7280", WHITE))
    return (f'<span style="background:{bg};color:{fg};font-weight:700;padding:2px 8px;border-radius:4px;'
            f'font-size:0.8rem;letter-spacing:0.04em">{html.escape(tier)}</span>')


def _value(v) -> str:
    if v is None:
        return "—"
    return html.escape(v if isinstance(v, str) else json.dumps(v))


def diff_html(diff: list[dict]) -> str:
    lines = []
    for d in diff:
        lines.append(
            f'<div style="font-family:\'JetBrains Mono\',monospace;font-size:0.9rem">{html.escape(str(d["field"]))}: '
            f'<span style="color:{RED};font-weight:600">{_value(d.get("before"))}</span> → '
            f'<span style="color:{GREEN};font-weight:600">{_value(d.get("after"))}</span></div>')
    return "".join(lines)


STATUS_LABEL = {
    "pending": "⏳ Pending", "approved": "👍 Approved", "executing": "⏳ Executing", "scheduled": "🗓 Scheduled",
    "succeeded": "✅ Succeeded", "failed": "⛔ Failed", "rejected": "✋ Rejected", "withdrawn": "↩ Withdrawn",
    "cancelled": "✖ Cancelled", "failed_cleanup_pending": "⛔ Cleanup failed",
}


def status_label(status: str) -> str:
    return STATUS_LABEL.get(status, status)


def format_duration(ms: int) -> str:
    return f"{ms} ms" if ms < 1000 else f"{ms / 1000:.1f} s"


def tool_step_title(step: dict) -> str:
    if step.get("status") is None:
        return f"🔧 {step['name']} · ⏳ running"
    return f"🔧 {step['name']} · {format_duration(step.get('duration_ms') or 0)} · {step['status']}"


def cr_title(cr: dict) -> str:
    title = f"{cr['id']} · {cr['change_type']} · {cr['database']['identifier']}"
    return f"↩ Rollback of {cr['rollback_of']} · {title}" if cr.get("rollback_of") else title


ERROR_COPY = {
    "drifted": "The database no longer matches this request's 'before' values, so nothing was changed. "
               "Ask for a new request.",
    "self_approval": "You can't approve your own request.",
    "forbidden_role": "Your role doesn't allow this action.",
    "invalid_state": "This request changed state since the page loaded. The list has been refreshed.",
    "window_override_reason_required": "Applying now outside the maintenance window needs a written reason.",
    "not_requester": "Only the requester can withdraw this request.",
    "rate_limited": "A refresh was already requested in the last minute.",
    "run_active": "This conversation already has a run in progress.",
}


def plain_error(err: ApiError) -> str:
    return ERROR_COPY.get(err.code, f"{err.message} ({err.code})")
