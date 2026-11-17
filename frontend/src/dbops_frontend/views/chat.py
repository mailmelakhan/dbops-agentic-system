"""Chat: runs live in agent-api; this page only starts a run once and polls its events.

Changes can only be proposed here. This page never calls the change service.
"""

import json

import streamlit as st

from dbops_frontend.api.base import ApiError, ServiceUnavailable
from dbops_frontend.chat_state import ActiveRun, Turn, apply_events, build_turn, turn_from_message
from dbops_frontend.clients import agent_client
from dbops_frontend.format import md_inline, plain_error, risk_badge_html, tool_step_title
from dbops_frontend.views.common import api_error_banner, service_banner, switch_to

UNREACHABLE = "agent-api unreachable, retrying…"
RUN_GONE = {400, 401, 403, 404, 422}  # the run is not ours or no longer exists: stop polling it


def _runs() -> dict[str, ActiveRun]:
    return st.session_state.setdefault("chat_runs", {})


def _adopt_running(agent, conv_id: str) -> None:
    """Pick up a run still in progress (e.g. after a browser refresh and a new login)."""
    if conv_id in _runs():
        return
    running = agent.running_runs(conv_id)
    if running:
        _runs()[conv_id] = ActiveRun(running[0]["run_id"])


def _first_load(agent) -> None:
    if "chat_conv_id" in st.session_state:
        return
    conversations = agent.list_conversations()
    conv_id = conversations[0]["id"] if conversations else None
    st.session_state["chat_conv_id"] = conv_id
    if conv_id:
        _adopt_running(agent, conv_id)


def _sidebar(agent) -> None:
    with st.sidebar:
        st.divider()
        if st.button("+ New chat", key="chat_new", width="stretch"):
            try:
                st.session_state["chat_conv_id"] = agent.create_conversation()
            except ServiceUnavailable:
                st.warning(UNREACHABLE)
                return
            except ApiError as err:
                st.warning(plain_error(err))
                return
            st.rerun()
        try:
            conversations = agent.list_conversations()
        except (ServiceUnavailable, ApiError):
            return
        current = st.session_state.get("chat_conv_id")
        for conv in conversations:
            kind = "primary" if conv["id"] == current else "secondary"
            if st.button(conv["title"], key=f"chat_conv_{conv['id']}", type=kind, width="stretch"):
                st.session_state["chat_conv_id"] = conv["id"]
                try:
                    _adopt_running(agent, conv["id"])
                except (ServiceUnavailable, ApiError):
                    pass  # history and polling show the problem on the rerun
                st.rerun()


def _proposal(agent, proposal: dict, key_prefix: str) -> None:
    cr_id = proposal["change_request_id"]
    if "change_type" not in proposal:
        try:
            cr = agent.get_change_request(cr_id)
            proposal = {**proposal, "change_type": cr["change_type"], "risk_tier": cr["risk_tier"]}
        except (ServiceUnavailable, ApiError):
            proposal = {**proposal, "change_type": "change", "risk_tier": None}
    st.markdown(f"📝 Proposed {md_inline(proposal['change_type'])} → {md_inline(cr_id)}")
    if proposal.get("risk_tier"):
        st.markdown(risk_badge_html(proposal["risk_tier"]), unsafe_allow_html=True)
    if st.button("Open in Approvals", key=f"{key_prefix}_open_{cr_id}"):
        st.session_state["approvals_focus"] = cr_id
        st.session_state["approvals_view"] = "Pending"
        switch_to("approvals")


def render_turn(agent, turn: Turn, key_prefix: str) -> None:
    with st.chat_message(turn.role):
        if turn.text:
            st.markdown(turn.text)
        for step in turn.tool_steps:
            with st.expander(tool_step_title(step.__dict__), expanded=False):
                st.caption("Arguments")
                st.code(json.dumps(step.args, indent=2, default=str), language="json")
                st.caption("Result")
                st.code(json.dumps(step.result, indent=2, default=str), language="json")
                if step.truncated:
                    st.caption("Result truncated to 20 KB")
        for proposal in turn.proposals:
            _proposal(agent, proposal, key_prefix)
        if turn.status == "failed" and turn.error:
            st.error(turn.error)
        elif turn.status == "cancelled":
            st.caption("⏹ Stopped")
        elif turn.status == "running" and not (turn.text or turn.tool_steps):
            st.caption("⏳ Thinking…")


@st.fragment(run_every=0.5)
def _live_turn(conv_id: str) -> None:
    run = _runs().get(conv_id)
    if run is None:
        return
    agent = agent_client()
    status = "running"
    try:
        page = agent.get_events(run.run_id, run.last_seq)
        apply_events(run, page["events"])
        status = page["run_status"]
    except ServiceUnavailable:
        st.warning(UNREACHABLE)
    except ApiError as err:
        if err.status in RUN_GONE:
            st.session_state["chat_flash"] = plain_error(err)
            _runs().pop(conv_id, None)
            st.rerun()  # full rerun re-enables the input
        st.warning(UNREACHABLE)  # 5xx / 429: transient, keep the run and keep polling
    render_turn(agent, build_turn(run.events), key_prefix="live")
    if status != "running":
        _runs().pop(conv_id, None)
        st.rerun()
    if st.button("Stop", key="chat_stop"):
        try:
            agent.cancel_run(run.run_id)
        except (ServiceUnavailable, ApiError):
            st.warning(UNREACHABLE)


def _submit(agent, prompt: str) -> None:
    conv_id = st.session_state.get("chat_conv_id")
    try:
        if conv_id is None:
            conv_id = agent.create_conversation()
            st.session_state["chat_conv_id"] = conv_id
        _runs()[conv_id] = ActiveRun(agent.start_run(conv_id, prompt))
    except ApiError as err:
        if err.code == "run_active":
            try:
                _adopt_running(agent, conv_id)
            except (ServiceUnavailable, ApiError):
                pass
            if conv_id in _runs():
                st.rerun()
        st.warning(plain_error(err))
        return
    except ServiceUnavailable:
        st.warning(UNREACHABLE)
        return
    st.rerun()


def render() -> None:
    st.header("Chat")
    agent = agent_client()
    flash = st.session_state.pop("chat_flash", None)
    if flash:
        st.warning(flash)
    try:
        _first_load(agent)
    except ServiceUnavailable:
        service_banner("agent-api")
        return
    except ApiError as err:
        api_error_banner("agent-api", err)
        return
    _sidebar(agent)

    conv_id = st.session_state.get("chat_conv_id")
    active = _runs().get(conv_id) if conv_id else None
    if conv_id:
        try:
            for i, msg in enumerate(agent.get_messages(conv_id)):
                render_turn(agent, turn_from_message(msg), key_prefix=f"h{i}")
        except ServiceUnavailable:
            if active is None:
                service_banner("agent-api")
                return
        except ApiError as err:
            st.warning(plain_error(err))
    else:
        st.caption("Ask a question about your databases to start a conversation.")
    if active is not None:
        _live_turn(conv_id)

    prompt = st.bottom.chat_input(  # stays pinned inside the layout column
        "Ask about your databases", disabled=active is not None, key="chat_input")
    if prompt:
        _submit(agent, prompt)
