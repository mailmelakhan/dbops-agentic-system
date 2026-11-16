"""Reports generated "in the background": generating for REPORT_SECONDS x time_scale, then ready."""

import csv
import io
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

from mock_backend.audit import filter_audit
from mock_backend.clock import iso
from mock_backend.errors import ApiProblem
from mock_backend.state import MockState

REPORT_SECONDS = 3.0
AUDIT_COLUMNS = ["ts", "user", "action", "tool", "database", "result", "change_request_id"]


def start_report(state: MockState, *, kind: str, title: str, created_by: str, now: datetime,
                 build: Callable[[], dict[str, str]]) -> str:
    report_id = f"rep-{uuid.uuid4().hex[:8]}"
    state.reports[report_id] = {"id": report_id, "kind": kind, "title": title, "status": "generating", "error": None,
                                "created_at": iso(now), "created_by": created_by, "formats": [],
                                "_started": now, "_build": build, "_content": {}}
    return report_id


def advance_reports(state: MockState, now: datetime, time_scale: float) -> None:
    for report in state.reports.values():
        if report["status"] != "generating":
            continue
        if now < report["_started"] + timedelta(seconds=REPORT_SECONDS * time_scale):
            continue
        report["_content"] = report["_build"]()
        report["formats"] = sorted(report["_content"])
        report["status"] = "ready"


def public_report(report: dict) -> dict:
    return {k: v for k, v in report.items() if not k.startswith("_")}


def get_report(state: MockState, report_id: str) -> dict:
    report = state.reports.get(report_id)
    if report is None:
        raise ApiProblem(404, "not_found", f"No report {report_id}")
    return report


def report_file(state: MockState, report_id: str, fmt: str) -> str:
    report = get_report(state, report_id)
    if report["status"] != "ready":
        raise ApiProblem(409, "not_ready", f"Report {report_id} is {report['status']}")
    if fmt not in report["_content"]:
        raise ApiProblem(404, "not_found", f"Report {report_id} has no {fmt} format")
    return report["_content"][fmt]


def _csv(rows: list[dict], columns: list[str]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if row.get(c) is None else row.get(c) for c in columns])
    return out.getvalue()


def _md_table(title: str, rows: list[dict], columns: list[str], intro: str = "") -> str:
    lines = [f"# {title}", ""]
    if intro:
        lines += [intro, ""]
    lines += ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    for row in rows:
        cells = ["" if row.get(c) is None else str(row.get(c)).replace("|", "\\|") for c in columns]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def change_history_builder(state: MockState, filters: dict) -> Callable[[], dict[str, str]]:
    def build() -> dict[str, str]:
        rows = filter_audit(state.audit, user=filters.get("user"), tool=filters.get("tool"),
                            database=filters.get("database"), from_=filters.get("from"), to=filters.get("to"))
        used = ", ".join(f"{k}={v}" for k, v in filters.items() if v) or "none"
        return {"csv": _csv(rows, AUDIT_COLUMNS),
                "md": _md_table("Change history", rows, AUDIT_COLUMNS, intro=f"Filters: {used}")}

    return build


def fleet_health_builder(state: MockState, env: str | None) -> Callable[[], dict[str, str]]:
    columns = ["identifier", "engine", "instance_class", "env", "health_score", "days_to_full", "open_alarms"]

    def build() -> dict[str, str]:
        rows = [{**d, "days_to_full": d["storage"]["days_to_full"]} for d in state.fleet
                if env is None or d["env"] == env]
        rows.sort(key=lambda d: d["health_score"])
        return {"csv": _csv(rows, columns), "md": _md_table("Fleet health", rows, columns, intro=f"As of {iso(state.as_of)}")}

    return build


def restore_test_builder(cr: dict) -> Callable[[], dict[str, str]]:
    def build() -> dict[str, str]:
        rows = [{"step": p["step"], "state": p["state"], "at": p["at"]} for p in cr["progress"]]
        outcome = cr["result"]["outcome"] if cr["result"] else "unknown"
        intro = f"{cr['id']} · {cr['database']['identifier']} · outcome: {outcome}"
        return {"md": _md_table(f"Restore test {cr['id']}", rows, ["step", "state", "at"], intro=intro)}

    return build
