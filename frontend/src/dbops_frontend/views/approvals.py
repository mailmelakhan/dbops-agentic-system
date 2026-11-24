"""Approvals: the only page that calls the executor."""

import streamlit as st

from dbops_frontend.api.base import ApiError, ServiceUnavailable
from dbops_frontend.clients import agent_client, executor_client
from dbops_frontend.config import load_settings
from dbops_frontend.format import cr_title, diff_html, escape_md, md_inline, plain_error, status_label
from dbops_frontend.permissions import (
    can_cancel,
    can_decide,
    can_propose_rollback,
    can_retry_cleanup,
    can_withdraw,
)
from dbops_frontend.views.common import api_error_banner, risk_badge, service_banner

VIEWS = {
    "Pending": ["pending"],
    "In progress": ["approved", "executing", "scheduled", "failed_cleanup_pending"],
    "Done": ["succeeded", "failed", "rejected", "cancelled", "withdrawn"],
}
SCHEDULE, APPLY_NOW = "Schedule for next window", "Apply now"
STEP_ICON = {"done": "✅", "active": "⏳", "pending": "▫️", "failed": "⛔"}
OVERRIDE_NEEDED = "Applying now outside the maintenance window needs a written reason."


def order_in_progress(crs: list[dict]) -> list[dict]:
    newest_first = sorted(crs, key=lambda c: c["created_at"], reverse=True)
    return sorted(newest_first, key=lambda c: c["status"] != "failed_cleanup_pending")


def _user():
    return st.session_state["user"]


def _go(view: str, focus: str | None = None) -> None:
    """Switch view on the next run (the view control already exists in this one)."""
    st.session_state["approvals_next_view"] = view
    if focus:
        st.session_state["approvals_focus"] = focus
    st.rerun()


def _act(call, success_view: str | None = None) -> None:
    """Run one executor or agent-api action; errors become a plain-words flash. Always reruns."""
    try:
        call()
    except ApiError as err:
        st.session_state["approvals_flash"] = plain_error(err)
    except ServiceUnavailable as err:
        st.session_state["approvals_flash"] = f"{err.service} unavailable, so nothing was changed."
    else:
        if success_view:
            _go(success_view)
    st.rerun()


# ---- confirmation dialog -------------------------------------------------------------------------------------


def _close_confirm() -> None:
    st.session_state.pop("approvals_confirm", None)


def render_confirm(cr: dict, mode: str, override_reason: str | None) -> None:
    st.markdown(f"**Database:** {escape_md(cr['database']['identifier'])}")
    st.markdown(f"**Change:** {escape_md(cr['summary'])} ({cr['change_type']}, {cr['risk_tier']})")
    st.markdown(f"**Mode:** {APPLY_NOW if mode == 'now' else SCHEDULE}")
    if override_reason:
        st.markdown(f"**Override reason:** {escape_md(override_reason)}")
    left, right = st.columns(2)
    if left.button("Execute", key="confirm_execute", type="primary", width="stretch"):
        _close_confirm()
        _act(lambda: executor_client().approve(cr["id"], mode, override_reason), success_view="In progress")
    if right.button("Cancel", key="confirm_back", width="stretch"):
        _close_confirm()
        st.rerun()


@st.dialog("Confirm execution", on_dismiss=_close_confirm)
def _confirm_dialog(confirm: dict) -> None:
    try:
        cr = agent_client().get_change_request(confirm["cr_id"])
    except (ApiError, ServiceUnavailable):
        st.warning("Couldn't load this change request.")
        return
    render_confirm(cr, confirm["mode"], confirm["override_reason"])


# ---- cards ---------------------------------------------------------------------------------------------------


def _header(cr: dict) -> None:
    risk_badge(cr["risk_tier"])
    st.caption(f"Requested by {md_inline(cr['requested_by'])} via {cr['requested_via']} at {cr['created_at']}")
    st.markdown(diff_html(cr["diff"]), unsafe_allow_html=True)
    st.markdown(f"**Impact:** {escape_md(cr['impact'])}")
    st.markdown("**Rollback plan**\n" + "\n".join(f"- {escape_md(step)}" for step in cr["rollback_plan"]))


def _window(cr: dict) -> tuple[str, str | None, bool]:
    """HIGH only: show the window, return (mode, override_reason, blocked)."""
    w = cr["maintenance_window"]
    st.markdown("✅ Inside window" if w["inside_now"] else "⚠ Outside window")
    st.caption(f"Window {w['window']} · next starts {w['next_start']}")
    choice = st.radio("Execution", [SCHEDULE, APPLY_NOW], key=f"mode_{cr['id']}", horizontal=True)
    if choice == SCHEDULE:
        return "next_window", None, False
    if w["inside_now"]:
        return "now", None, False
    reason = st.text_input("Override reason", key=f"override_{cr['id']}").strip()
    return "now", reason or None, not reason


def _pending_card(cr: dict, focus: str | None) -> None:
    user, cid = _user(), cr["id"]
    with st.expander(f"{cr_title(cr)} · {cr['risk_tier']}", expanded=cid == focus):
        _header(cr)
        mode, override, blocked = ("now", None, False)
        if cr["risk_tier"] == "HIGH":
            mode, override, blocked = _window(cr)
        decision = can_decide(user, cr)
        approve_col, reject_col, withdraw_col = st.columns(3)
        if approve_col.button("Approve & execute", key=f"approve_{cid}", type="primary",
                              disabled=not decision.allowed, width="stretch"):
            if blocked:
                st.warning(OVERRIDE_NEEDED)
            else:
                st.session_state["approvals_confirm"] = {"cr_id": cid, "mode": mode, "override_reason": override}
        if reject_col.button("Reject", key=f"reject_{cid}", disabled=not decision.allowed, width="stretch"):
            st.session_state["approvals_rejecting"] = cid
        if can_withdraw(user, cr) and withdraw_col.button("Withdraw", key=f"withdraw_{cid}", width="stretch"):
            _act(lambda: executor_client().withdraw(cid))
        if not decision.allowed:
            st.caption(decision.reason)
        if st.session_state.get("approvals_rejecting") == cid:
            reason = st.text_input("Reject reason", key=f"reject_reason_{cid}").strip()
            if st.button("Confirm reject", key=f"confirm_reject_{cid}", disabled=not reason):
                st.session_state.pop("approvals_rejecting", None)
                _act(lambda: executor_client().reject(cid, reason))


def _tracker(cr: dict) -> None:
    st.markdown(" → ".join(f"{STEP_ICON.get(p['state'], '')} {p['step']}" for p in cr["progress"]))


def _in_progress_card(cr: dict) -> None:
    user, cid = _user(), cr["id"]
    with st.expander(f"{cr_title(cr)} · {cr['risk_tier']}", expanded=cr["status"] == "failed_cleanup_pending"):
        risk_badge(cr["risk_tier"])
        st.caption(f"{status_label(cr['status'])} · requested by {md_inline(cr['requested_by'])}")
        _tracker(cr)
        if cr["status"] == "failed_cleanup_pending":
            st.error("Cleanup failed: the temporary restore instance is still running", icon="⛔")
            if can_retry_cleanup(user, cr) and st.button("Retry cleanup", key=f"retry_{cid}", type="primary"):
                _act(lambda: executor_client().retry_cleanup(cid))
        if cr["status"] == "scheduled":
            st.markdown(f"🗓 Next window starts {cr['maintenance_window']['next_start']}")
            if can_cancel(user, cr):
                if st.button("Cancel change", key=f"cancel_{cid}"):
                    st.session_state["approvals_cancelling"] = cid
                if st.session_state.get("approvals_cancelling") == cid:
                    reason = st.text_input("Cancel reason", key=f"cancel_reason_{cid}").strip()
                    if st.button("Confirm cancel", key=f"confirm_cancel_{cid}", disabled=not reason):
                        st.session_state.pop("approvals_cancelling", None)
                        _act(lambda: executor_client().cancel(cid, reason))


def _done_card(cr: dict) -> None:
    cid = cr["id"]
    with st.expander(f"{cr_title(cr)} · {cr['risk_tier']}"):
        st.markdown(f"{status_label(cr['status'])} · {escape_md(cr['summary'])}")
        if cr.get("status_reason"):
            st.caption(f"Reason: {md_inline(cr['status_reason'])}")
        if cr.get("result"):
            st.caption(md_inline(cr["result"]["message"]))
        st.markdown(diff_html(cr["diff"]), unsafe_allow_html=True)
        if can_propose_rollback(_user(), cr) and st.button("Propose rollback", key=f"rollback_{cid}"):
            try:
                new_id = agent_client().propose_rollback(cid)
            except ApiError as err:
                st.session_state["approvals_flash"] = plain_error(err)
                st.rerun()
            except ServiceUnavailable:
                st.session_state["approvals_flash"] = "agent-api unavailable, so nothing was changed."
                st.rerun()
            else:
                _go("Pending", focus=new_id)


# ---- page ----------------------------------------------------------------------------------------------------


def _list(view: str) -> list[dict] | None:
    try:
        return agent_client().list_change_requests(VIEWS[view])
    except ServiceUnavailable:
        service_banner("agent-api")
        return None
    except ApiError as err:
        api_error_banner("agent-api", err)
        return None


@st.fragment(run_every=5)
def _in_progress() -> None:
    crs = _list("In progress")
    if crs is None:
        return
    if not crs:
        st.info("Nothing in progress.")
    for cr in order_in_progress(crs):
        _in_progress_card(cr)


def render() -> None:
    st.header("Approvals")
    if load_settings().executor_simulated:
        st.info("Execution is simulated: approving a change does not touch AWS yet.")
    if "approvals_next_view" in st.session_state:
        st.session_state["approvals_view"] = st.session_state.pop("approvals_next_view")
    st.session_state.setdefault("approvals_view", "Pending")
    flash = st.session_state.pop("approvals_flash", None)
    if flash:
        st.warning(flash)
    view = st.segmented_control("View", list(VIEWS), key="approvals_view", required=True) or "Pending"

    if view == "In progress":
        _in_progress()
    else:
        crs = _list(view)
        if crs is None:
            return
        if view == "Pending":
            if not crs:
                st.info("No pending approvals.")
            focus = st.session_state.get("approvals_focus")
            for cr in crs:
                _pending_card(cr, focus)
        else:
            if not crs:
                st.info("No finished changes yet.")
            for cr in crs:
                _done_card(cr)

    confirm = st.session_state.get("approvals_confirm")
    if confirm:
        _confirm_dialog(confirm)
