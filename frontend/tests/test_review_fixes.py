"""Regression tests for the final-review findings (each failed before its fix)."""

import re
from pathlib import Path

import pytest
from dbops_frontend.api.agent import AgentApi
from dbops_frontend.api.base import ApiError
from dbops_frontend.auth import authenticate, load_users
from dbops_frontend.format import cr_title

ROOT = Path(__file__).resolve().parents[2]


# ---- Important #1: any ApiError on page load shows a banner, never a traceback ----

@pytest.mark.parametrize("module", ["fleet_panel", "chat", "approvals", "audit_reports"])
def test_wrong_service_token_shows_banner_not_traceback(view_app, monkeypatch, module):
    at = view_app(module, logged_in="lakhan")
    monkeypatch.setenv("DBOPS_SERVICE_TOKEN", "wrong-token")
    at.run()
    assert not at.exception
    assert at.error and "Missing or wrong service token" in at.error[0].value


def test_audit_reports_view_wrong_token(view_app, monkeypatch):
    at = view_app("audit_reports", logged_in="lakhan", session={"audit_view": "Reports"})
    monkeypatch.setenv("DBOPS_SERVICE_TOKEN", "wrong-token")
    at.run()
    assert not at.exception and at.error


# ---- Important #2: chat keeps the run on 5xx while polling; drops it cleanly on 404 ----

def _start_live_run(view_app, mock_server):
    url = mock_server(time_scale=1.0)
    at = view_app("chat", logged_in="lakhan", base_url=url)
    at.run()
    at.chat_input[0].set_value("how is the fleet?").run()
    return at


def test_chat_keeps_run_on_5xx(view_app, mock_server, monkeypatch):
    at = _start_live_run(view_app, mock_server)
    monkeypatch.setattr(AgentApi, "get_events", lambda *a, **k: (_ for _ in ()).throw(
        ApiError(503, "http_error", "Service Unavailable")))
    at.run()
    assert not at.exception
    conv = at.session_state["chat_conv_id"]
    assert conv in at.session_state["chat_runs"]
    assert at.warning[0].value == "agent-api unreachable, retrying…"


def test_chat_drops_run_on_404_and_reenables_input(view_app, mock_server, monkeypatch):
    at = _start_live_run(view_app, mock_server)
    monkeypatch.setattr(AgentApi, "get_events", lambda *a, **k: (_ for _ in ()).throw(
        ApiError(404, "not_found", "No run")))
    at.run()
    assert not at.exception
    assert at.session_state["chat_runs"] == {}
    assert not at.chat_input[0].disabled


def test_chat_run_active_adopts_running_run(view_app, mock_server):
    url = mock_server(time_scale=1.0)
    at = view_app("chat", logged_in="lakhan", base_url=url)
    at.run()
    at.chat_input[0].set_value("how is the fleet?").run()
    conv = at.session_state["chat_conv_id"]
    run_id = at.session_state["chat_runs"].pop(conv).run_id  # simulate a lost local run
    at.run()
    at.chat_input[0].set_value("again").run()
    assert at.session_state["chat_runs"][conv].run_id == run_id


# ---- Important #3: backend strings render literally (no markdown links/images) ----

def test_cr_title_escapes_markdown():
    t = cr_title({"id": "CR-1", "change_type": "modify_tags", "rollback_of": None,
                  "database": {"identifier": "**bold** [link](http://evil.example)"}})
    assert not re.search(r"(?<!\\)\]\(", t) and "**bold**" not in t


def _hostile(cr):
    return {**cr, "database": {**cr["database"], "identifier": "[link](http://evil.example)"},
            "status_reason": "![x](http://evil.example/p.png)", "requested_by": "**geeta**",
            "result": {"outcome": "succeeded", "message": "[approve here](http://evil.example)", "report_id": None}}


def test_hostile_cr_fields_render_literally(view_app, monkeypatch):
    real = AgentApi.list_change_requests
    monkeypatch.setattr(AgentApi, "list_change_requests",
                        lambda self, *a, **k: [_hostile(c) for c in real(self, *a, **k)])
    at = view_app("approvals", logged_in="lakhan", session={"approvals_view": "Done"})
    at.run()
    rendered = [e.label for e in at.expander] + [c.value for c in at.caption] + [m.value for m in at.markdown]
    assert rendered and not any(re.search(r"(?<!\\)\]\(http", r) for r in rendered)


# ---- Important #4: cancelled and withdrawn CRs stay visible ----

def test_done_shows_withdrawn_and_cancelled(view_app):
    at = view_app("approvals", logged_in="lakhan", session={"approvals_view": "Done"})
    at.run()
    assert any(e.label.startswith("CR-0138") for e in at.expander)


# ---- upgraded minor: long password must not crash login ----

def test_long_password_is_rejected_not_crash(users_file):
    assert authenticate(load_users(users_file), "lakhan", "x" * 100) is None


# ---- upgraded minor: Docker images install the locked versions ----

@pytest.mark.parametrize("member", ["frontend", "mock_backend"])
def test_dockerfile_installs_locked_requirements(member):
    dockerfile = (ROOT / member / "Dockerfile").read_text()
    assert f"{member}/requirements.lock" in dockerfile and (ROOT / member / "requirements.lock").exists()
