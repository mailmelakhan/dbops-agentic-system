"""Per-rerun API clients for the logged-in user."""

import streamlit as st

from dbops_frontend.api.agent import AgentApi
from dbops_frontend.api.executor import ExecutorApi
from dbops_frontend.api.health import HealthApi
from dbops_frontend.config import load_settings


def _username() -> str:
    return st.session_state["user"].username


def agent_client() -> AgentApi:
    s = load_settings()
    return AgentApi(s.agent_api_url, _username(), s.service_token)


def health_client(timeout: float = 5.0) -> HealthApi:
    s = load_settings()
    return HealthApi(s.health_url, _username(), s.service_token, timeout=timeout)


def executor_client() -> ExecutorApi:
    """Only the Approvals view may call this."""
    s = load_settings()
    return ExecutorApi(s.executor_url, _username(), s.service_token)
