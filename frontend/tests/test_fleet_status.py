from dbops_frontend.fleet_status import Status, attention_count, db_status, sort_fleet, status_reason, worst_status
from dbops_frontend.format import risk_badge_html, status_badge_html


def db(ident, score, days, alarms=0):
    return {"identifier": ident, "health_score": score, "storage": {"days_to_full": days}, "open_alarms": alarms}


def test_status_rule():
    assert db_status(db("a", 42, 9, 2)) is Status.CRITICAL
    assert db_status(db("a", 90, 14)) is Status.CRITICAL
    assert db_status(db("a", 49, None)) is Status.CRITICAL
    assert db_status(db("a", 79, 200)) is Status.WARNING
    assert db_status(db("a", 90, 30)) is Status.WARNING
    assert db_status(db("a", 80, 31)) is Status.OK
    assert db_status(db("a", 95, None)) is Status.OK


def test_alarm_only_is_warning():
    assert db_status(db("a", 95, None, 1)) is Status.WARNING


def test_reason_most_serious_first():
    assert status_reason(db("a", 42, 9, 2)) == "Storage full in 9 days · Score 42 · 2 alarms"
    assert status_reason(db("a", 38, None, 1)) == "Score 38 · 1 alarm"
    assert status_reason(db("a", 68, 27, 1)) == "Storage full in 27 days · Score 68 · 1 alarm"
    assert status_reason(db("a", 70, 9)) == "Storage full in 9 days · Score 70"   # red storage before amber score
    assert status_reason(db("a", 91, 120)) == "Score 91"


def test_reason_edge_cases():
    assert status_reason(db("a", 90, 1)) == "Storage full in 1 day"
    assert status_reason(db("a", 90, 0)) == "Storage full"
    assert "Storage" not in status_reason(db("a", 30, None))
    assert status_reason(db("a", 95, None, 1)) == "1 alarm"


def test_sort_worst_first():
    dbs = [db("ok-b", 90, None), db("warn", 70, None), db("ok-a", 90, None), db("crit-hi", 45, None),
           db("crit-lo", 30, None)]
    assert [d["identifier"] for d in sort_fleet(dbs)] == ["crit-lo", "crit-hi", "warn", "ok-a", "ok-b"]


def test_counts_and_worst():
    dbs = [db("a", 90, None), db("b", 70, None), db("c", 30, None)]
    assert attention_count(dbs) == 2 and worst_status(dbs) is Status.CRITICAL
    assert attention_count([]) == 0 and worst_status([]) is None
    assert worst_status([db("a", 90, None)]) is Status.OK


def test_status_badge_html():
    crit, warn, ok = (status_badge_html(s) for s in ("CRITICAL", "WARNING", "OK"))
    assert "#dc2626" in crit and ">CRITICAL<" in crit and "#ffffff" in crit
    assert "#f59e0b" in warn and "#111827" in warn and ">WARNING<" in warn
    assert "#16a34a" in ok and ">OK<" in ok
    assert "#dc2626" in risk_badge_html("HIGH") and ">HIGH<" in risk_badge_html("HIGH")


def test_reason_negative_days():
    assert status_reason(db("a", 90, -3)) == "Storage full"
    assert db_status(db("a", 90, -3)) is Status.CRITICAL
