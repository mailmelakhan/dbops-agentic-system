import pytest
from dbops_frontend.api.agent import AgentApi
from dbops_frontend.api.executor import ExecutorApi
from dbops_frontend.views.approvals import order_in_progress


@pytest.fixture
def approvals_view(view_app):
    def make(view=None, confirm=None, **kw):
        session = kw.pop("session", {})
        if view:
            session["approvals_view"] = view
        if confirm:
            session["approvals_confirm"] = confirm
        return view_app("approvals", session=session, **kw)

    return make


def titles(at):
    return [e.label for e in at.expander]


def test_pending_list_titles(approvals_view):
    at = approvals_view(logged_in="geeta")
    at.run()
    assert not at.exception
    assert "CR-0143 · modify_instance_class · prod-orders-db · HIGH" in titles(at)
    assert len(at.expander) == 6
    assert any(c.value.startswith("Requested by ankit via chat at ") for c in at.caption)


def test_role_too_low_disables_with_reason(approvals_view):
    at = approvals_view(logged_in="geeta")
    at.run()
    assert at.button(key="approve_CR-0143").disabled and at.button(key="reject_CR-0143").disabled
    assert "Your role (SAFE-WRITE) can't approve HIGH changes" in [c.value for c in at.caption]
    assert not at.button(key="approve_CR-0141").disabled


def test_own_request_disabled_and_withdraw_shown(approvals_view):
    at = approvals_view(logged_in="ankit")
    at.run()
    assert at.button(key="approve_CR-0143").disabled
    assert "You can't approve your own request" in [c.value for c in at.caption]
    assert at.button(key="withdraw_CR-0143")
    assert not [b for b in at.button if b.key == "withdraw_CR-0140"]


def test_withdraw(approvals_view):
    at = approvals_view(logged_in="ankit")
    at.run()
    at.button(key="withdraw_CR-0143").click().run()
    assert not any(t.startswith("CR-0143") for t in titles(at))


def test_diff_and_window_shown(approvals_view):
    at = approvals_view(logged_in="lakhan")
    at.run()
    assert any("db.r6g.large" in m.value and "#ef4444" in m.value for m in at.markdown)
    assert any(m.value == "⚠ Outside window" for m in at.markdown)
    assert any(m.value == "✅ Inside window" for m in at.markdown)


def test_override_reason_only_when_apply_now_outside_window(approvals_view):
    at = approvals_view(logged_in="lakhan")
    at.run()
    assert not [t for t in at.text_input if t.key == "override_CR-0143"]
    at.radio(key="mode_CR-0143").set_value("Apply now").run()
    assert at.text_input(key="override_CR-0143")
    at.radio(key="mode_CR-0144").set_value("Apply now").run()
    assert not [t for t in at.text_input if t.key == "override_CR-0144"]


def test_apply_now_outside_window_without_reason_does_not_open_confirm(approvals_view):
    at = approvals_view(logged_in="lakhan")
    at.run()
    at.radio(key="mode_CR-0143").set_value("Apply now").run()
    at.button(key="approve_CR-0143").click().run()
    assert "approvals_confirm" not in at.session_state
    assert "Applying now outside the maintenance window needs a written reason." in [w.value for w in at.warning]


def test_approve_click_opens_confirm(approvals_view):
    at = approvals_view(logged_in="lakhan")
    at.run()
    at.button(key="approve_CR-0140").click().run()
    assert at.session_state["approvals_confirm"] == {"cr_id": "CR-0140", "mode": "now", "override_reason": None}
    assert at.button(key="confirm_execute")


def test_confirm_body_repeats_details(approvals_view, mock_server):
    url = mock_server()
    cr = AgentApi(url, "lakhan", "test-token").get_change_request("CR-0143")
    at = approvals_view(logged_in="lakhan", base_url=url, func="render_confirm", args=(cr, "now", "INC-77"))
    at.run()
    text = " ".join(m.value for m in at.markdown).replace("\\", "")  # values are markdown-escaped
    assert "prod-orders-db" in text and "Apply now" in text and "INC-77" in text


def test_execute_runs_change_and_moves_to_in_progress(approvals_view):
    at = approvals_view(logged_in="lakhan")
    at.run()
    at.button(key="approve_CR-0140").click().run()
    at.button(key="confirm_execute").click().run()
    assert "approvals_confirm" not in at.session_state
    assert at.session_state["approvals_view"] == "In progress"


def test_confirm_back_closes_without_change(approvals_view):
    at = approvals_view(logged_in="lakhan")
    at.run()
    at.button(key="approve_CR-0140").click().run()
    at.button(key="confirm_back").click().run()
    assert "approvals_confirm" not in at.session_state
    assert any(t.startswith("CR-0140") for t in titles(at))


def test_drifted_shown_in_plain_words(approvals_view):
    at = approvals_view(logged_in="lakhan")
    at.run()
    at.button(key="approve_CR-0139").click().run()
    at.button(key="confirm_execute").click().run()
    assert at.warning[0].value.startswith("The database no longer matches")


def test_invalid_state_shows_plain_words_and_refreshes(approvals_view):
    at = approvals_view(logged_in="lakhan")
    at.run()
    at.button(key="approve_CR-0140").click().run()
    ExecutorApi(at.base_url, "geeta", "test-token").withdraw("CR-0140")  # another user acts after page load
    at.button(key="confirm_execute").click().run()
    assert at.warning[0].value == "This request changed state since the page loaded. The list has been refreshed."
    assert not any(t.startswith("CR-0140") for t in titles(at))


def test_reject_with_reason(approvals_view):
    at = approvals_view(logged_in="lakhan")
    at.run()
    at.button(key="reject_CR-0140").click().run()
    at.text_input(key="reject_reason_CR-0140").input("Wrong tag").run()
    at.button(key="confirm_reject_CR-0140").click().run()
    assert not any(t.startswith("CR-0140") for t in titles(at))


def test_in_progress_cleanup_first_and_retry_privileged_only(approvals_view):
    at = approvals_view(logged_in="lakhan", view="In progress")
    at.run()
    assert not at.exception
    assert at.expander[0].label.startswith("CR-0136")
    cleanup = next(e for e in at.error if e.value == "Cleanup failed: the temporary restore instance is still running")
    assert cleanup.icon == "⛔"
    assert at.button(key="retry_CR-0136") and at.button(key="cancel_CR-0135")
    assert any(m.value.startswith("🗓 Next window starts ") for m in at.markdown)
    geeta = approvals_view(logged_in="geeta", view="In progress")
    geeta.run()
    assert not [b for b in geeta.button if b.key in {"retry_CR-0136", "cancel_CR-0135"}]


def test_step_tracker_icons(approvals_view):
    at = approvals_view(logged_in="lakhan", view="In progress")
    at.run()
    assert any(m.value == "✅ restoring → ✅ available → ✅ verified → ⛔ cleaned_up" for m in at.markdown)


def test_cancel_scheduled_needs_reason(approvals_view):
    at = approvals_view(logged_in="lakhan", view="In progress")
    at.run()
    at.button(key="cancel_CR-0135").click().run()
    assert at.button(key="confirm_cancel_CR-0135").disabled
    at.text_input(key="cancel_reason_CR-0135").input("Change freeze").run()
    at.button(key="confirm_cancel_CR-0135").click().run()
    assert not at.exception
    cr = AgentApi(at.base_url, "lakhan", "test-token").get_change_request("CR-0135")
    assert cr["status"] == "cancelled"


def test_order_in_progress():
    crs = [{"id": "a", "status": "executing", "created_at": "2"}, {"id": "b", "status": "failed_cleanup_pending",
           "created_at": "1"}, {"id": "c", "status": "scheduled", "created_at": "3"}]
    assert [c["id"] for c in order_in_progress(crs)] == ["b", "c", "a"]


def test_propose_rollback_switches_to_pending_with_focus(approvals_view):
    at = approvals_view(logged_in="geeta", view="Done")
    at.run()
    at.button(key="rollback_CR-0130").click().run()
    assert at.session_state["approvals_view"] == "Pending"
    focus = at.session_state["approvals_focus"]
    rollback = next(e for e in at.expander if e.label.startswith(f"↩ Rollback of CR-0130 · {focus}"))
    assert rollback.proto.expanded


def test_done_hides_rollback_when_not_eligible(approvals_view):
    at = approvals_view(logged_in="geeta", view="Done")
    at.run()
    keys = {b.key for b in at.button}
    assert "rollback_CR-0130" in keys and "rollback_CR-0132" not in keys and "rollback_CR-0137" not in keys


def test_read_user_sees_everything_disabled(approvals_view):
    at = approvals_view(logged_in="amit")
    at.run()
    decide = [b for b in at.button if b.key and b.key.startswith(("approve_", "reject_"))]
    assert decide and all(b.disabled for b in decide)


def test_empty_pending(approvals_view, mock_server):
    url = mock_server()
    for cr, user in [("CR-0139", "geeta"), ("CR-0140", "geeta"), ("CR-0141", "lakhan"), ("CR-0142", "ankit"),
                     ("CR-0143", "ankit"), ("CR-0144", "lakhan")]:
        ExecutorApi(url, user, "test-token").withdraw(cr)
    at = approvals_view(logged_in="amit", base_url=url)
    at.run()
    assert at.info[0].value == "No pending approvals."


def test_unreachable_banner(approvals_view):
    at = approvals_view(logged_in="amit", agent_url="http://127.0.0.1:9")
    at.run()
    assert at.error[0].value == "agent-api unavailable, showing nothing rather than stale data"


def test_simulated_banner(approvals_view, monkeypatch):
    monkeypatch.setenv("EXECUTOR_SIMULATED", "true")
    at = approvals_view(logged_in="geeta")
    at.run()
    assert not at.exception
    assert at.info[0].value.startswith("Execution is simulated")


def test_no_simulated_banner_by_default(approvals_view, monkeypatch):
    monkeypatch.delenv("EXECUTOR_SIMULATED", raising=False)
    at = approvals_view(logged_in="geeta")
    at.run()
    assert not at.exception
    assert not [i for i in at.info if i.value.startswith("Execution is simulated")]
