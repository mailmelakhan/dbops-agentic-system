from unittest.mock import ANY

import pytest
from dbops_frontend.views.health import fleet_frame, style_fleet


@pytest.fixture
def health_view(view_app):
    def make(**kw):
        kw.setdefault("logged_in", "amit")
        return view_app("health", **kw)

    return make


def db(ident, score, days, alarms=0):
    return {"identifier": ident, "engine": "e", "instance_class": "c", "health_score": score,
            "storage": {"days_to_full": days}, "open_alarms": alarms}


def test_summary_metrics(health_view):
    at = health_view()
    at.run()
    assert not at.exception
    assert {m.label: m.value for m in at.metric} == {
        "Databases": "14", "Unhealthy": "2", "< 30 days to full": "3", "Open alarms": ANY}
    caption = next(c.value for c in at.caption if c.value.startswith("Data as of "))
    assert caption.endswith("(health-analytics)")


def test_table_has_all_databases(health_view):
    at = health_view()
    at.run()
    assert len(at.dataframe[0].value) == 14


def test_fleet_frame_columns_and_order():
    df = fleet_frame([db("a", 90, None), db("b", 40, 9, 2)])
    assert list(df.columns) == ["Database", "Engine", "Class", "Score", "Days to full", "Alarms"]
    assert df["Database"].tolist() == ["b", "a"]
    assert df["Score"].tolist() == ["⛔ 40", "✅ 90"] and df["Days to full"].tolist() == ["⛔ 9 d", "—"]


def test_style_fleet_colours_cells():
    dbs = [db("a", 90, None), db("b", 64, 20)]
    html = style_fleet(fleet_frame(dbs), dbs).to_html()
    assert "#22c55e" in html and "#f59e0b" in html and "#111827" in html


def test_env_filter(health_view):
    at = health_view()
    at.run()
    at.selectbox(key="health_env").select("prod").run()
    names = set(at.dataframe[0].value["Database"])
    assert "prod-orders-db" in names and "legacy-reports-db" not in names


def test_empty_filter_result(health_view):
    at = health_view()
    at.run()
    at.selectbox(key="health_env").select("dev").run()
    at.selectbox(key="health_engine").select("aurora-mysql").run()
    assert at.info[0].value == "No databases match these filters."


def test_unreachable_banner(health_view):
    at = health_view(health_url="http://127.0.0.1:9")
    at.run()
    assert not at.exception
    assert at.error[0].value == "health-analytics unavailable, showing nothing rather than stale data"
    assert len(at.metric) == 0 and len(at.dataframe) == 0


def test_refresh_twice_plain_words(health_view):
    at = health_view()
    at.run()
    at.button(key="health_refresh").click().run()
    at.button(key="health_refresh").click().run()
    assert at.info[0].value == "A refresh was already requested in the last minute."


def test_generate_report_points_to_reports(health_view):
    at = health_view()
    at.run()
    at.button(key="health_report").click().run()
    assert at.session_state["audit_view"] == "Reports"
    assert at.success[0].value == "Health report is generating."
    assert at.button(key="health_open_reports")


def test_alarms_panel(health_view):
    at = health_view(func="render_alarms", args=("prod-orders-db",))
    at.run()
    assert at.expander[0].label == "Alarms · prod-orders-db"
    assert at.markdown[0].value.startswith("⛔ ALARM")


def test_alarms_panel_empty(health_view):
    at = health_view(func="render_alarms", args=("dev-orders-db",))
    at.run()
    assert at.caption[0].value == "No open alarms."
