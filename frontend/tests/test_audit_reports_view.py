from datetime import date

import pytest
from dbops_frontend.views.audit_reports import audit_frame, report_status_label


@pytest.fixture
def audit_view(view_app):
    def make(view=None, **kw):
        kw.setdefault("logged_in", "rita")
        session = kw.pop("session", {})
        if view:
            session["audit_view"] = view
        return view_app("audit_reports", session=session, **kw)

    return make


def report_lines(at):
    return [m.value for m in at.markdown if any(s in m.value for s in ("⏳", "✅ ready", "⛔ failed"))]


def test_default_date_range_is_last_7_days(audit_view):
    at = audit_view()
    at.run()
    assert not at.exception
    start, end = at.date_input(key="audit_dates").value
    assert (end - start).days == 7 and end == date.today()


def test_table_shows_rows(audit_view):
    at = audit_view()
    at.run()
    df = at.dataframe[0].value
    assert list(df.columns) == ["Time", "User", "Action / tool", "Database", "Result", "CR"] and len(df) > 0


def test_filter_by_user(audit_view):
    at = audit_view()
    at.run()
    at.selectbox(key="audit_user").select("sam").run()
    assert set(at.dataframe[0].value["User"]) == {"sam"}


def test_paging(audit_view):
    at = audit_view()
    at.run()
    at.date_input(key="audit_dates").set_value((date(2026, 1, 1), date.today())).run()
    assert at.button(key="audit_prev").disabled
    first = at.dataframe[0].value["Time"].tolist()
    at.button(key="audit_next").click().run()
    second = at.dataframe[0].value["Time"].tolist()
    assert second and second[0] <= first[-1] and not at.button(key="audit_prev").disabled
    at.button(key="audit_prev").click().run()
    assert at.dataframe[0].value["Time"].tolist() == first


def test_audit_frame_columns():
    df = audit_frame([{"id": "a1", "ts": "t", "user": "sam", "action": "chat.tool_call", "tool": "rds.x",
                       "database": "db", "result": "ok", "change_request_id": None, "detail": ""},
                      {"id": "a2", "ts": "t", "user": "sam", "action": "change.approved", "tool": None,
                       "database": "db", "result": "denied", "change_request_id": "CR-1", "detail": ""}])
    assert list(df.columns) == ["Time", "User", "Action / tool", "Database", "Result", "CR"]
    assert df.iloc[0]["Action / tool"] == "rds.x" and df.iloc[0]["Result"] == "✅ ok"
    assert (df.iloc[1]["Action / tool"], df.iloc[1]["Result"], df.iloc[1]["CR"]) == (
        "change.approved", "✋ denied", "CR-1")


def test_report_status_label():
    assert report_status_label({"status": "generating"}) == "⏳ generating…"
    assert report_status_label({"status": "ready"}) == "✅ ready"
    assert report_status_label({"status": "failed", "error": "boom"}) == "⛔ failed: boom"


def test_export_shows_generating_at_top(audit_view, mock_server):
    at = audit_view(base_url=mock_server(time_scale=1.0))
    at.run()
    at.button(key="audit_export").click().run()
    assert at.session_state["audit_view"] == "Reports"
    assert "⏳ generating…" in report_lines(at)[0]


def test_ready_report_has_download_buttons(audit_view):
    at = audit_view(view="Reports")
    at.run()
    labels = [b.proto.label for b in at.get("download_button")]
    assert "Download CSV" in labels and "Download MD" in labels


def test_failed_report_shows_error(audit_view):
    at = audit_view(view="Reports")
    at.run()
    assert any("⛔ failed: Timed out reading CloudWatch" in line for line in report_lines(at))


def test_empty_reports(audit_view):
    at = audit_view(func="render_reports_table", args=([],))
    at.run()
    assert at.info[0].value == "No reports yet."


def test_unreachable_banner(audit_view):
    at = audit_view(agent_url="http://127.0.0.1:9")
    at.run()
    assert at.error[0].value == "agent-api unavailable, showing nothing rather than stale data"
