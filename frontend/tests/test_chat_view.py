import pytest
from dbops_frontend.api.agent import AgentApi


@pytest.fixture
def chat_view(view_app):
    def make(**kw):
        return view_app("chat", **kw)

    return make


def test_send_message_completes_turn(chat_view):
    at = chat_view(logged_in="geeta")
    at.run()
    at.chat_input[0].set_value("take a snapshot of prod-orders-db").run()
    at.run()
    assert not at.exception
    assert any(e.label.startswith("🔧 ") for e in at.expander)
    assert any(m.value.startswith("📝 Proposed create_snapshot → CR-") for m in at.markdown)
    assert any(b.label == "Open in Approvals" for b in at.button)
    assert not at.chat_input[0].disabled


def test_tool_steps_start_collapsed(chat_view):
    at = chat_view(logged_in="lakhan")
    at.run()
    at.chat_input[0].set_value("how is prod-orders-db?").run()
    at.run()
    tools = [e for e in at.expander if e.label.startswith("🔧 ")]
    assert tools and all(not e.proto.expanded for e in tools)


def test_history_reloads_most_recent_conversation(chat_view, mock_server):
    url = mock_server()
    at = chat_view(logged_in="geeta", base_url=url)
    at.run()
    at.chat_input[0].set_value("hello there").run()
    at.run()
    fresh = chat_view(logged_in="geeta", base_url=url)
    fresh.run()
    assert any("hello there" in m.value for m in fresh.markdown)


def test_sidebar_lists_conversations_and_new_chat(chat_view, mock_server):
    url = mock_server()
    agent = AgentApi(url, "geeta", "test-token")
    agent.create_conversation()
    at = chat_view(logged_in="geeta", base_url=url)
    at.run()
    labels = [b.label for b in at.sidebar.button]
    assert "+ New chat" in labels and "New chat" in labels
    at.sidebar.button(key="chat_new").click().run()
    assert len(agent.list_conversations()) == 2


def test_resume_running_run_after_refresh(chat_view, mock_server):
    url = mock_server(time_scale=1.0)
    agent = AgentApi(url, "lakhan", "test-token")
    conv = agent.create_conversation()
    run = agent.start_run(conv, "how is the fleet?")
    at = chat_view(logged_in="lakhan", base_url=url)
    at.run()
    assert at.session_state["chat_runs"][conv].run_id == run
    assert at.chat_input[0].disabled
    assert at.button(key="chat_stop")


def test_stop_cancels_run(chat_view, mock_server):
    url = mock_server(time_scale=1.0)
    at = chat_view(logged_in="lakhan", base_url=url)
    at.run()
    at.chat_input[0].set_value("how is the fleet?").run()
    at.button(key="chat_stop").click().run()
    at.run()
    conv = at.session_state["chat_conv_id"]
    assert AgentApi(url, "lakhan", "test-token").running_runs(conv) == []


def test_chat_keeps_run_when_agent_api_unreachable(chat_view, mock_server, monkeypatch):
    url = mock_server(time_scale=1.0)
    at = chat_view(logged_in="lakhan", base_url=url)
    at.run()
    at.chat_input[0].set_value("how is the fleet?").run()
    monkeypatch.setenv("AGENT_API_URL", "http://127.0.0.1:9")
    at.run()
    assert not at.exception
    assert at.warning[0].value == "agent-api unreachable, retrying…"
    conv = at.session_state["chat_conv_id"]
    assert at.session_state["chat_runs"][conv].run_id
    monkeypatch.setenv("AGENT_API_URL", url)
    at.run()
    assert not at.warning


def test_unreachable_without_run_shows_banner(chat_view):
    at = chat_view(logged_in="lakhan", agent_url="http://127.0.0.1:9")
    at.run()
    assert at.error[0].value == "agent-api unavailable, showing nothing rather than stale data"


def test_failed_run_shows_error(chat_view):
    at = chat_view(logged_in="lakhan")
    at.run()
    at.chat_input[0].set_value("please fail").run()
    at.run()
    shown = [*at.error, *at.markdown]  # live turn: st.error; once in history: the message's "⛔ <error>" text
    assert any("⛔" in x.value and "Bedrock throttled the request" in x.value for x in at.markdown) or any(
        "Bedrock throttled the request" in x.value for x in at.error)
    assert shown
