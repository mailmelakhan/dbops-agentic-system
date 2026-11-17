import pytest
from dbops_frontend.views import approvals


def panel_shown(at):
    return any("need attention" in m.value for m in at.markdown)


@pytest.mark.parametrize("module,header", [("chat", "Chat"), ("approvals", "Approvals"),
                                           ("audit_reports", "Audit & reports")])
def test_panel_on_every_page(view_app, module, header):
    at = view_app(module, logged_in="amit", with_panel=True)
    at.run()
    assert not at.exception
    assert at.header[0].value == header and panel_shown(at)


def test_toggle_hides_and_shows_panel(view_app):
    at = view_app("chat", logged_in="amit", with_panel=True)
    at.run()
    assert at.toggle(key="fleet_panel_shown").value is True
    at.toggle(key="fleet_panel_shown").set_value(False).run()
    assert not panel_shown(at) and at.header[0].value == "Chat"
    assert not [b for b in at.button if b.key and b.key.startswith("fleet_row_")]   # no fleet calls drawn
    at.toggle(key="fleet_panel_shown").set_value(True).run()
    assert panel_shown(at)


def test_page_works_when_health_down(view_app):
    at = view_app("approvals", logged_in="lakhan", with_panel=True, health_url="http://127.0.0.1:9")
    at.run()
    assert not at.exception and at.header[0].value == "Approvals"
    assert at.error[0].value == "health-analytics unavailable, showing nothing rather than stale data"
    assert any(e.label.startswith("CR-") for e in at.expander)


def test_panel_drawn_before_page_runs(view_app, monkeypatch):
    def stops():
        import streamlit as st
        st.stop()
    monkeypatch.setattr(approvals, "render", stops)
    at = view_app("approvals", logged_in="amit", with_panel=True)
    at.run()
    assert panel_shown(at)


def test_chat_input_pinned_with_panel(view_app):
    at = view_app("chat", logged_in="amit", with_panel=True)
    at.run()
    assert at.chat_input and not at.main.chat_input   # in st.bottom (pinned), not inline in the page column
