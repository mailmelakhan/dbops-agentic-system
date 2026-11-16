import streamlit as st

from dbops_frontend.api.agent import AgentApi
from dbops_frontend.api.base import ApiError, ServiceUnavailable
from dbops_frontend.config import load_settings
from dbops_frontend.views import approvals, audit_reports, chat, health


def approvals_title(pending: int) -> str:
    return f"Approvals ({pending})" if pending else "Approvals"


@st.cache_data(ttl=5, show_spinner=False)
def _pending_count(username: str, agent_url: str, token: str) -> int:
    return len(AgentApi(agent_url, username, token).list_change_requests(["pending"]))


def pending_count() -> int:
    s = load_settings()
    try:
        return _pending_count(st.session_state["user"].username, s.agent_api_url, s.service_token)
    except (ServiceUnavailable, ApiError):
        return 0


def build_navigation(pending: int):
    pages = {
        "health": st.Page(health.render, title="Health", icon="🩺", url_path="health", default=True),
        "chat": st.Page(chat.render, title="Chat", icon="💬", url_path="chat"),
        "approvals": st.Page(approvals.render, title=approvals_title(pending), icon="✅", url_path="approvals"),
        "audit": st.Page(audit_reports.render, title="Audit & reports", icon="📜", url_path="audit"),
    }
    st.session_state["pages"] = pages
    return st.navigation(list(pages.values()))
