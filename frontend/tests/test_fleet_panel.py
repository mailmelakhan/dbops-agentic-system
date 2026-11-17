import pytest
from dbops_frontend.api.health import HealthApi

ORDER = ["legacy-reports-db", "prod-orders-db", "prod-analytics-db", "dev-sandbox-db", "staging-legacy-db",
         "staging-users-db", "prod-search-db", "dev-mysql-db", "prod-inventory-db", "staging-orders-db",
         "prod-users-db", "staging-payments-db", "prod-payments-db", "dev-orders-db"]


@pytest.fixture
def panel(view_app):
    def make(**kw):
        kw.setdefault("logged_in", "amit")
        return view_app("fleet_panel", **kw)
    return make


def rows(at):
    return [b.label for b in at.button if b.key.startswith("fleet_row_")]


def test_header_and_as_of(panel):
    at = panel(); at.run()
    assert not at.exception
    assert "6 of 14 need attention" in at.markdown[0].value and ">CRITICAL<" in at.markdown[0].value
    assert at.caption[0].value.startswith("Data as of ")


def test_rows_worst_first(panel):
    at = panel(); at.run()
    assert rows(at) == ORDER


def test_row_shows_badge_and_reason(panel):
    at = panel(); at.run()
    texts = [m.value for m in at.markdown]
    assert any(">CRITICAL<" in t and "Storage full in 9 days · Score 42 · 2 alarms" in t for t in texts)


def test_row_click_toggles_alarms(panel):
    at = panel(); at.run()
    at.button(key="fleet_row_prod-orders-db").click().run()
    assert at.session_state["fleet_open"] == "prod-orders-db"
    assert any(m.value.startswith("⛔ ALARM") for m in at.markdown)
    at.button(key="fleet_row_prod-orders-db").click().run()
    assert "fleet_open" not in at.session_state
    assert not any(m.value.startswith("⛔ ALARM") for m in at.markdown)


def test_unreachable(panel):
    at = panel(health_url="http://127.0.0.1:9"); at.run()
    assert not at.exception
    assert at.error[0].value == "health-analytics unavailable, showing nothing rather than stale data"
    assert rows(at) == []


def test_empty_fleet(panel, monkeypatch):
    monkeypatch.setattr(HealthApi, "fleet", lambda self, **k: {"as_of": "2026-11-16T10:00:00Z", "databases": []})
    at = panel(); at.run()
    assert at.info[0].value == "No databases yet." and rows(at) == []


def test_alarms_empty(panel):
    at = panel(func="render_alarms", args=("dev-orders-db",)); at.run()
    assert at.caption[0].value == "Alarms · dev-orders-db" and at.caption[1].value == "No open alarms."


# ---- Final review Important #1: a hung health-analytics must not stall every page ----

FLEET = {"as_of": "2026-11-16T10:00:00Z", "databases": []}


def test_panel_uses_short_timeout(panel, monkeypatch):
    seen = []
    monkeypatch.setattr(HealthApi, "fleet", lambda self, **k: seen.append(self._timeout) or FLEET)
    at = panel()
    at.run()
    assert seen == [1.0]


def test_backoff_after_unreachable(panel, monkeypatch):
    from dbops_frontend.api.base import ServiceUnavailable

    calls = []

    def down(self, **k):
        calls.append(1)
        raise ServiceUnavailable("health-analytics")

    monkeypatch.setattr(HealthApi, "fleet", down)
    at = panel()
    at.run()
    at.run()
    assert len(calls) == 1   # second rerun within the back-off makes no call
    assert at.error[0].value == "health-analytics unavailable, showing nothing rather than stale data"
