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
    return MockState(fleet=_fleet(), alarms=_alarms(now), as_of=now)
