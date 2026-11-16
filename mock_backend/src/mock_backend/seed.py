from datetime import datetime, timedelta

from mock_backend.clock import iso
from mock_backend.state import MockState

REGION = "us-east-1"

# identifier, kind, engine, class, env, score, free_gb, trend_gb_per_day, days_to_full, open_alarms
_FLEET = [
    ("prod-orders-db", "cluster", "aurora-postgresql", "db.r6g.large", "prod", 42, 45.0, 5.0, 9, 2),
    ("prod-payments-db", "cluster", "aurora-postgresql", "db.r6g.xlarge", "prod", 91, 600.0, 5.0, 120, 0),
    ("prod-analytics-db", "instance", "postgres", "db.m6g.2xlarge", "prod", 68, 270.0, 10.0, 27, 1),
    ("prod-users-db", "cluster", "aurora-mysql", "db.r6g.large", "prod", 88, 420.0, 2.0, 210, 0),
    ("prod-inventory-db", "cluster", "aurora-mysql", "db.r6g.large", "prod", 84, 285.0, 3.0, 95, 0),
    ("prod-search-db", "instance", "postgres", "db.m6g.large", "prod", 81, 192.0, 3.0, 64, 0),
    ("staging-legacy-db", "instance", "postgres", "db.t3.large", "staging", 74, 44.0, 2.0, 22, 0),
    ("legacy-reports-db", "instance", "mysql", "db.m5.large", "staging", 38, 120.0, 0.0, None, 1),
    ("staging-orders-db", "cluster", "aurora-postgresql", "db.r6g.large", "staging", 86, 300.0, 1.0, 300, 0),
    ("staging-payments-db", "cluster", "aurora-postgresql", "db.r6g.large", "staging", 90, 360.0, 2.0, 180, 0),
    ("staging-users-db", "cluster", "aurora-mysql", "db.t4g.medium", "staging", 79, 90.0, 2.0, 45, 0),
    ("dev-orders-db", "cluster", "aurora-postgresql", "db.t4g.medium", "dev", 95, 400.0, 1.0, 400, 0),
    ("dev-sandbox-db", "instance", "postgres", "db.t3.medium", "dev", 70, 33.0, 1.0, 33, 0),
    ("dev-mysql-db", "instance", "mysql", "db.t3.medium", "dev", 82, 150.0, 1.0, 150, 0),
]


def _fleet() -> list[dict]:
    return [
        {
            "identifier": ident, "kind": kind, "engine": engine, "instance_class": cls, "env": env,
            "region": REGION, "health_score": score,
            "storage": {"free_gb": free, "trend_gb_per_day": trend, "days_to_full": days},
            "open_alarms": alarms,
        }
        for ident, kind, engine, cls, env, score, free, trend, days, alarms in _FLEET
    ]


def _alarms(now: datetime) -> dict[str, list[dict]]:
    def alarm(name, metric, hours_ago, threshold, description, state="ALARM"):
        return {"name": name, "metric": metric, "state": state, "since": iso(now - timedelta(hours=hours_ago)),
                "threshold": threshold, "description": description}

    return {
        "prod-orders-db": [
            alarm("prod-orders-db-cpu-high", "CPUUtilization", 3, 85.0, "CPU above 85% for 15 minutes"),
            alarm("prod-orders-db-storage-low", "FreeStorageSpace", 30, 50.0, "Free storage below 50 GB"),
        ],
        "prod-analytics-db": [
            alarm("prod-analytics-db-replica-lag", "ReplicaLag", 1, 30.0, "Replica lag above 30 seconds"),
        ],
        "legacy-reports-db": [
            alarm("legacy-reports-db-connections", "DatabaseConnections", 52, 400.0, "Connections above 400"),
        ],
    }


def seed_state(now: datetime) -> MockState:
    state = MockState(fleet=_fleet(), alarms=_alarms(now), as_of=now)
    _seed_change_requests(state, now)
    _seed_audit(state, now)
    _seed_reports(state, now)
    return state


def _db_ref(state: MockState, identifier: str) -> dict:
    d = next(x for x in state.fleet if x["identifier"] == identifier)
    return {"identifier": d["identifier"], "kind": d["kind"], "engine": d["engine"], "region": d["region"]}


def _seed_change_requests(state: MockState, now: datetime) -> None:
    from mock_backend import changes

    def make(number, change_type, db, by, hours_ago, summary, diff, params=None, status="pending", **extra):
        state.next_cr_number = number
        created = now - timedelta(hours=hours_ago)
        cr = changes.new_change_request(state, change_type=change_type, database=_db_ref(state, db),
                                        requested_by=by, params=params or {}, diff=diff, summary=summary,
                                        now=created)
        cr["status"] = status
        for key, value in extra.items():
            cr[key] = value
        return cr, created

    def approval(by, at, mode="now", **kw):
        return {"decision": "approved", "decided_by": by, "decided_at": iso(at), "mode": mode,
                "override_reason": kw.get("override_reason"), "reason": None}

    def all_done(cr, at):
        cr["progress"] = [{"step": p["step"], "state": "done", "at": iso(at)} for p in cr["progress"]]

    def succeeded(cr, created, approver, message):
        done_at = created + timedelta(minutes=20)
        cr["approval"] = approval(approver, created + timedelta(minutes=15))
        all_done(cr, done_at)
        cr["result"] = {"outcome": "succeeded", "message": message, "report_id": None}

    tags_before, tags_after = {"owner": "payments"}, {"owner": "payments-core"}
    cr, t = make(130, "modify_tags", "prod-payments-db", "sam", 144, "Change owner tag on prod-payments-db",
                 [{"field": "tag:owner", "before": "payments", "after": "payments-core"}],
                 params={"before": tags_before, "after": tags_after}, status="succeeded")
    succeeded(cr, t, "priya", "Tags updated")

    cr, t = make(131, "create_snapshot", "prod-orders-db", "sam", 120, "Snapshot prod-orders-db before release",
                 [{"field": "snapshot", "before": None, "after": "prod-orders-db-pre-release-20261111"}],
                 params={"snapshot_identifier": "prod-orders-db-pre-release-20261111"}, status="succeeded")
    succeeded(cr, t, "priya", "Snapshot available")

    cr, t = make(132, "modify_instance_class", "staging-legacy-db", "arjun", 96,
                 "Resize staging-legacy-db to db.t3.large",
                 [{"field": "instance_class", "before": "db.t3.medium", "after": "db.t3.large"}],
                 params={"from_class": "db.t3.medium", "to_class": "db.t3.large"}, status="succeeded")
    succeeded(cr, t, "priya", "Instance class changed")
    cr["maintenance_window"]["inside_now"] = None

    cr, t = make(133, "create_snapshot", "legacy-reports-db", "sam", 72, "Snapshot legacy-reports-db",
                 [{"field": "snapshot", "before": None, "after": "legacy-reports-db-manual-20261113"}],
                 params={"snapshot_identifier": "legacy-reports-db-manual-20261113"}, status="failed")
    cr["approval"] = approval("priya", t + timedelta(minutes=5))
    cr["progress"] = [{"step": "creating", "state": "failed", "at": iso(t + timedelta(minutes=9))},
                      {"step": "available", "state": "pending", "at": None}]
    cr["result"] = {"outcome": "failed", "message": "SnapshotQuotaExceeded: manual snapshot quota reached",
                    "report_id": None}

    cr, t = make(134, "modify_tags", "prod-orders-db", "sam", 60, "Set owner tag on prod-orders-db",
                 [{"field": "tag:owner", "before": "orders", "after": "order-team"}],
                 params={"before": {"owner": "orders"}, "after": {"owner": "order-team"}}, status="rejected",
                 status_reason="Wrong owner tag")
    cr["approval"] = {"decision": "rejected", "decided_by": "priya", "decided_at": iso(t + timedelta(minutes=30)),
                      "mode": None, "override_reason": None, "reason": "Wrong owner tag"}

    cr, t = make(135, "modify_instance_class", "prod-payments-db", "arjun", 30,
                 "Resize prod-payments-db to db.r6g.2xlarge",
                 [{"field": "instance_class", "before": "db.r6g.xlarge", "after": "db.r6g.2xlarge"}],
                 params={"from_class": "db.r6g.xlarge", "to_class": "db.r6g.2xlarge"}, status="scheduled")
    cr["approval"] = approval("priya", t + timedelta(hours=1), mode="next_window")
    cr["progress"] = [{"step": "pre_snapshot", "state": "done", "at": iso(t + timedelta(hours=1))},
                      {"step": "scheduled", "state": "active", "at": iso(t + timedelta(hours=1))},
                      {"step": "applied", "state": "pending", "at": None}]

    cr, t = make(136, "restore_test", "legacy-reports-db", "arjun", 26, "Restore test of legacy-reports-db",
                 [{"field": "temporary_instance", "before": None, "after": "restoretest-legacy-reports-db-202611150800"},
                  {"field": "instance_class", "before": None, "after": "db.t3.micro"}],
                 params={"source_snapshot": "legacy-reports-db-auto-20261115"}, status="failed_cleanup_pending")
    cr["approval"] = approval("priya", t + timedelta(minutes=10))
    cr["progress"] = [{"step": s, "state": "done", "at": iso(t + timedelta(minutes=20 + 5 * i))}
                      for i, s in enumerate(["restoring", "available", "verified"])]
    cr["progress"].append({"step": "cleaned_up", "state": "failed", "at": iso(t + timedelta(minutes=50))})
    cr["result"] = {"outcome": "failed", "report_id": None,
                    "message": "Cleanup failed after 3 retries; the temporary instance is still running"}

    cr, t = make(137, "restore_test", "prod-payments-db", "priya", 24, "Restore test of prod-payments-db",
                 [{"field": "temporary_instance", "before": None, "after": "restoretest-prod-payments-db-202611151000"},
                  {"field": "instance_class", "before": None, "after": "db.t4g.medium"}],
                 params={"source_snapshot": "prod-payments-db-auto-20261115"}, status="succeeded")
    succeeded(cr, t, "arjun", "Restore test passed; temporary instance deleted")

    make(138, "modify_tags", "dev-sandbox-db", "sam", 20, "Add team tag to dev-sandbox-db",
         [{"field": "tag:team", "before": None, "after": "platform"}],
         params={"before": {}, "after": {"team": "platform"}}, status="withdrawn")

    make(139, "modify_tags", "staging-legacy-db", "sam", 8, "Set cost-center tag on staging-legacy-db",
         [{"field": "tag:cost-center", "before": "cc-100", "after": "cc-210"}],
         params={"before": {"cost-center": "cc-100"}, "after": {"cost-center": "cc-210"}})
    make(140, "modify_tags", "prod-orders-db", "sam", 6, "Add backup-tier tag to prod-orders-db",
         [{"field": "tag:backup-tier", "before": None, "after": "gold"}],
         params={"before": {}, "after": {"backup-tier": "gold"}})
    make(141, "create_snapshot", "prod-payments-db", "priya", 5, "Snapshot prod-payments-db",
         [{"field": "snapshot", "before": None, "after": "prod-payments-db-manual-20261116"}],
         params={"snapshot_identifier": "prod-payments-db-manual-20261116"})
    make(142, "restore_test", "prod-payments-db", "arjun", 4, "Restore test of prod-payments-db",
         [{"field": "temporary_instance", "before": None, "after": "restoretest-prod-payments-db-202611160600"},
          {"field": "instance_class", "before": None, "after": "db.t4g.medium"},
          {"field": "estimated_cost_per_hour", "before": None, "after": "$0.07"}],
         params={"source_snapshot": "prod-payments-db-auto-20261116"})
    make(143, "modify_instance_class", "prod-orders-db", "arjun", 2, "Resize prod-orders-db to db.r6g.xlarge",
         [{"field": "instance_class", "before": "db.r6g.large", "after": "db.r6g.xlarge"}],
         params={"from_class": "db.r6g.large", "to_class": "db.r6g.xlarge"})
    cr, _ = make(144, "modify_instance_class", "staging-legacy-db", "priya", 1,
                 "Resize staging-legacy-db to db.t3.xlarge",
                 [{"field": "instance_class", "before": "db.t3.large", "after": "db.t3.xlarge"}],
                 params={"from_class": "db.t3.large", "to_class": "db.t3.xlarge"})
    cr["maintenance_window"]["inside_now"] = True
    state.next_cr_number = 145


_TOOLS = ["rds.describe_db_instances", "cloudwatch.get_metric_statistics", "rds.describe_events",
          "cloudwatch.describe_alarms"]


def _seed_audit(state: MockState, now: datetime) -> None:
    from mock_backend.changes import audit_row

    users = ["priya", "arjun", "sam", "rita"]
    dbs = [d["identifier"] for d in state.fleet]
    for i in range(32):
        user, tool, db = users[i % 4], _TOOLS[i % len(_TOOLS)], dbs[(i * 5) % len(dbs)]
        audit_row(state, now=now - timedelta(hours=7.5 * i + 0.5), user=user, action="chat.tool_call", tool=tool,
                  database=db, result="error" if i % 11 == 10 else "ok",
                  detail=f'{{"db_instance_identifier": "{db}"}}')


def _seed_reports(state: MockState, now: datetime) -> None:
    from mock_backend.reports import fleet_health_builder, restore_test_builder

    def ready(report_id, kind, title, by, hours_ago, content):
        state.reports[report_id] = {"id": report_id, "kind": kind, "title": title, "status": "ready", "error": None,
                                    "created_at": iso(now - timedelta(hours=hours_ago)), "created_by": by,
                                    "formats": sorted(content), "_started": now, "_build": None,
                                    "_content": content}

    restore_cr = state.change_requests["CR-0137"]
    ready("rep-seed-restore", "restore_test", "Restore test CR-0137", "executor", 23,
          restore_test_builder(restore_cr)())
    restore_cr["result"]["report_id"] = "rep-seed-restore"
    ready("rep-seed-fleet", "fleet_health", "Fleet health (all envs)", "rita", 12, fleet_health_builder(state, None)())
    state.reports["rep-seed-failed"] = {
        "id": "rep-seed-failed", "kind": "change_history", "title": "Change history (last 30 days)",
        "status": "failed", "error": "Timed out reading CloudWatch", "created_at": iso(now - timedelta(hours=6)),
        "created_by": "arjun", "formats": [], "_started": now, "_build": None, "_content": {}}
