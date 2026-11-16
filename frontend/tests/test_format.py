import pytest
from dbops_frontend.api.base import ApiError
from dbops_frontend.format import (
    Band,
    cr_title,
    days_band,
    days_label,
    diff_html,
    format_duration,
    plain_error,
    risk_badge_html,
    score_band,
    score_label,
    status_label,
    tool_step_title,
)


@pytest.mark.parametrize("score,band", [(100, Band.GREEN), (80, Band.GREEN), (79, Band.AMBER), (50, Band.AMBER),
                                        (49, Band.RED), (0, Band.RED)])
def test_score_band_edges(score, band):
    assert score_band(score) is band


@pytest.mark.parametrize("days,band,label", [(0, Band.RED, "⛔ 0 d"), (14, Band.RED, "⛔ 14 d"),
                                             (15, Band.AMBER, "⚠ 15 d"), (30, Band.AMBER, "⚠ 30 d"),
                                             (31, Band.NEUTRAL, "31 d"), (None, Band.NEUTRAL, "—")])
def test_days_band_edges_and_null(days, band, label):
    assert (days_band(days), days_label(days)) == (band, label)


def test_score_label():
    assert [score_label(s) for s in (92, 64, 31)] == ["✅ 92", "⚠ 64", "⛔ 31"]


def test_risk_badge_has_word_and_exact_colours():
    h = risk_badge_html("HIGH")
    assert "#dc2626" in h and "HIGH" in h and "#ffffff" in h.lower()
    m = risk_badge_html("MEDIUM")
    assert "#f59e0b" in m and "#111827" in m and "MEDIUM" in m
    low = risk_badge_html("LOW")
    assert "#16a34a" in low and "LOW" in low


def test_risk_badge_escapes_unknown_tier():
    assert "<script>" not in risk_badge_html("<script>")


def test_diff_html_escapes_values():
    out = diff_html([{"field": "tag:<b>", "before": "<img src=x onerror=alert(1)>", "after": "**bold**"}])
    assert "<img" not in out and "&lt;img" in out and "**bold**" in out and "&lt;b&gt;" in out
    assert "#ef4444" in out and "#22c55e" in out


def test_diff_html_shows_none_and_non_strings():
    out = diff_html([{"field": "snapshot", "before": None, "after": {"a": 1}}])
    assert "—" in out and "{&quot;a&quot;: 1}" in out


def test_format_duration():
    assert (format_duration(850), format_duration(1200), format_duration(61000)) == ("850 ms", "1.2 s", "61.0 s")


def test_tool_step_title():
    assert tool_step_title({"name": "rds.x", "duration_ms": 1200, "status": "ok"}) == "🔧 rds.x · 1.2 s · ok"
    assert tool_step_title({"name": "rds.x", "duration_ms": 300, "status": "error"}) == "🔧 rds.x · 300 ms · error"
    assert tool_step_title({"name": "rds.x", "status": None}) == "🔧 rds.x · ⏳ running"


def test_cr_title():
    cr = {"id": "CR-0143", "change_type": "modify_instance_class", "database": {"identifier": "prod-orders-db"},
          "rollback_of": None}
    assert cr_title(cr) == "CR-0143 · modify_instance_class · prod-orders-db"


def test_cr_title_rollback():
    assert cr_title({"id": "CR-0150", "change_type": "modify_tags", "database": {"identifier": "db1"},
                     "rollback_of": "CR-0130"}) == "↩ Rollback of CR-0130 · CR-0150 · modify_tags · db1"


def test_status_labels_always_have_a_word():
    assert status_label("failed_cleanup_pending") == "⛔ Cleanup failed"
    assert status_label("succeeded") == "✅ Succeeded"
    assert status_label("pending") == "⏳ Pending"


def test_plain_error_drifted():
    assert plain_error(ApiError(409, "drifted", "x")).startswith("The database no longer matches")


def test_plain_error_copy():
    assert plain_error(ApiError(409, "invalid_state", "")) == (
        "This request changed state since the page loaded. The list has been refreshed.")
    assert plain_error(ApiError(429, "rate_limited", "")) == "A refresh was already requested in the last minute."
    assert plain_error(ApiError(409, "run_active", "")) == "This conversation already has a run in progress."
    assert plain_error(ApiError(500, "boom", "Something broke")) == "Something broke (boom)"


def test_escape_md_renders_literally():
    from dbops_frontend.format import escape_md

    assert escape_md("**bold** [x](y) <b>") == r"\*\*bold\*\* \[x\]\(y\) \<b\>"
    assert escape_md("~5–10 min downtime") == r"\~5–10 min downtime"
