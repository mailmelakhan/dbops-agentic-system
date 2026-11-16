"""Audit & reports: the audit log with filters and paging, and background-generated reports."""

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from dbops_frontend.api.base import ApiError, ServiceUnavailable
from dbops_frontend.clients import agent_client
from dbops_frontend.format import escape_md, plain_error
from dbops_frontend.views.common import service_banner

VIEWS = ["Audit log", "Reports"]
COLUMNS = ["Time", "User", "Action / tool", "Database", "Result", "CR"]
RESULT_LABEL = {"ok": "✅ ok", "error": "⛔ error", "denied": "✋ denied"}
PAGE_SIZE = 50


def audit_frame(items: list[dict]) -> pd.DataFrame:
    rows = [[r["ts"], r["user"], r["tool"] or r["action"], r["database"] or "",
             RESULT_LABEL.get(r["result"], r["result"]), r["change_request_id"] or ""] for r in items]
    return pd.DataFrame(rows, columns=COLUMNS)


def report_status_label(report: dict) -> str:
    if report["status"] == "generating":
        return "⏳ generating…"
    if report["status"] == "ready":
        return "✅ ready"
    return f"⛔ failed: {report.get('error') or 'unknown error'}"


def _go(view: str) -> None:
    st.session_state["audit_next_view"] = view
    st.rerun()


# ---- audit log -----------------------------------------------------------------------------------------------


def _filters(facets: dict) -> dict:
    cols = st.columns([1, 1, 1, 2])
    user = cols[0].selectbox("User", ["All", *facets["users"]], key="audit_user")
    tool = cols[1].selectbox("Tool", ["All", *facets["tools"]], key="audit_tool")
    database = cols[2].selectbox("Database", ["All", *facets["databases"]], key="audit_db")
    today = date.today()
    dates = cols[3].date_input("Date range", value=(today - timedelta(days=7), today), key="audit_dates")
    start, end = (dates[0], dates[-1]) if isinstance(dates, tuple) and dates else (dates, dates)
    return {
        "user": None if user == "All" else user,
        "tool": None if tool == "All" else tool,
        "database": None if database == "All" else database,
        "from": f"{start.isoformat()}T00:00:00Z" if start else None,
        "to": f"{end.isoformat()}T23:59:59Z" if end else None,
    }


def _audit_log() -> None:
    agent = agent_client()
    try:
        facets = agent.audit_facets()
    except ServiceUnavailable:
        service_banner("agent-api")
        return
    filters = _filters(facets)
    signature = tuple(sorted(filters.items()))
    if st.session_state.get("audit_filter_sig") != signature:
        st.session_state["audit_filter_sig"] = signature
        st.session_state["audit_cursor_stack"] = [None]
    stack: list[str | None] = st.session_state["audit_cursor_stack"]
    try:
        page = agent.list_audit(filters, cursor=stack[-1], limit=PAGE_SIZE)
    except ServiceUnavailable:
        service_banner("agent-api")
        return
    except ApiError as err:
        st.warning(plain_error(err))
        return

    if page["items"]:
        st.dataframe(audit_frame(page["items"]), hide_index=True, width="stretch")
    else:
        st.info("No audit rows match these filters.")
    prev_col, next_col, page_col, export_col = st.columns([1, 1, 2, 2])
    if prev_col.button("Previous", key="audit_prev", disabled=len(stack) == 1):
        stack.pop()
        st.rerun()
    if next_col.button("Next page", key="audit_next", disabled=page["next_cursor"] is None):
        stack.append(page["next_cursor"])
        st.rerun()
    page_col.caption(f"Page {len(stack)}")
    if export_col.button("Export CSV", key="audit_export"):
        try:
            agent.start_change_history_report(filters)
        except ServiceUnavailable:
            service_banner("agent-api")
            return
        except ApiError as err:
            st.warning(plain_error(err))
            return
        _go("Reports")


# ---- reports -------------------------------------------------------------------------------------------------


def render_reports_table(reports: list[dict]) -> None:
    if not reports:
        st.info("No reports yet.")
        return
    agent = agent_client()
    for r in reports:
        with st.container(border=True):
            st.markdown(f"**{escape_md(r['title'])}** · {r['kind']} · {report_status_label(r)}")
            st.caption(f"Generated {r['created_at']} by {r['created_by']}")
            if r["status"] != "ready":
                continue
            cols = st.columns(max(len(r["formats"]), 1) + 3)
            for i, fmt in enumerate(r["formats"]):
                try:
                    data = agent.download_report(r["id"], fmt)
                except (ServiceUnavailable, ApiError):
                    cols[i].caption(f"{fmt.upper()} unavailable")
                    continue
                cols[i].download_button(f"Download {fmt.upper()}", data=data, file_name=f"{r['id']}.{fmt}",
                                        mime="text/csv" if fmt == "csv" else "text/markdown", key=f"dl_{r['id']}_{fmt}")


def _reports_body(polling: bool) -> None:
    try:
        reports = agent_client().list_reports()
    except ServiceUnavailable:
        service_banner("agent-api")
        return
    render_reports_table(reports)
    if polling and not any(r["status"] == "generating" for r in reports):
        st.rerun()  # everything finished: a full rerun stops the polling fragment


def _reports() -> None:
    try:
        generating = any(r["status"] == "generating" for r in agent_client().list_reports())
    except ServiceUnavailable:
        service_banner("agent-api")
        return
    st.fragment(_reports_body, run_every=3 if generating else None)(generating)


def render() -> None:
    st.header("Audit & reports")
    if "audit_next_view" in st.session_state:
        st.session_state["audit_view"] = st.session_state.pop("audit_next_view")
    st.session_state.setdefault("audit_view", "Audit log")
    view = st.segmented_control("View", VIEWS, key="audit_view", required=True) or "Audit log"
    if view == "Reports":
        _reports()
    else:
        _audit_log()
