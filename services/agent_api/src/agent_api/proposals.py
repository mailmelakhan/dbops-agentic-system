"""Change proposals: build change-request bodies from real AWS values (design §6, §7.2)."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from agent_api.identity import Caller, Role
from agent_api.scope import ScopedDb

log = logging.getLogger(__name__)
_WEEK = 7 * 24 * 60
_DAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
_PREFERRED_CLASSES = ("db.t4g.micro", "db.t4g.small", "db.t4g.medium", "db.t3.micro")
_TIER = {"modify_tags": "LOW", "create_snapshot": "LOW", "restore_test": "MEDIUM",
         "modify_instance_class": "HIGH"}
NO_WINDOW = {"required": False, "window": None, "inside_now": None, "next_start": None}


class ProposalError(Exception):
    """A plain refusal returned to the model."""


def required_role(tier: str) -> Role:
    return Role.SAFE_WRITE if tier == "LOW" else Role.PRIVILEGED


def _aws_read(what: str, fn: Callable[[], Any]) -> Any:
    """Run a boto3 read; AWS failures become a plain refusal (details are logged, not shown to the model)."""
    try:
        return fn()
    except (ClientError, BotoCoreError):
        log.warning("AWS read failed: %s", what, exc_info=True)
        raise ProposalError(f"Couldn't read {what} from AWS") from None


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _minute_of_week(spec: str) -> int:
    day, hh, mm = spec.strip().lower().split(":")
    return _DAYS.index(day) * 1440 + int(hh) * 60 + int(mm)


def window_state(window: str, now: datetime) -> tuple[bool, str]:
    """(inside_now, next_start ISO) for a "ddd:hh:mm-ddd:hh:mm" UTC window; may wrap past the week end."""
    try:
        start_s, end_s = window.split("-")
        start, end = _minute_of_week(start_s), _minute_of_week(end_s)
    except (ValueError, IndexError) as exc:
        raise ProposalError(f"Couldn't read the maintenance window {window!r}") from exc
    now = now.astimezone(UTC).replace(second=0, microsecond=0)
    now_mow = ((now.weekday() + 1) % 7) * 1440 + now.hour * 60 + now.minute
    length = (end - start) % _WEEK or _WEEK
    inside = (now_mow - start) % _WEEK < length
    delta = (start - now_mow) % _WEEK or _WEEK
    return inside, _iso(now + timedelta(minutes=delta))


class Proposer:
    def __init__(self, scope, rds_client_factory: Callable[[str], Any], mock, prices: dict,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self._scope = scope
        self._factory = rds_client_factory
        self._mock = mock
        self._prices = prices
        self._clock = clock

    # ---- shared helpers
    def _check_role(self, caller: Caller, change_type: str) -> None:
        if caller.role.rank < required_role(_TIER[change_type]).rank:
            raise ProposalError(f"Your role ({caller.role.value}) can't propose {change_type}")

    def _resolve(self, identifier: str, *, instance_only: bool = False) -> ScopedDb:
        scope = self._scope.current()
        inst = scope.get(identifier, kind="instance")
        clus = None if instance_only else scope.get(identifier, kind="cluster")
        if instance_only and inst is None:
            clus = scope.get(identifier, kind="cluster")
            if clus is not None:
                return clus
        if inst and clus:
            raise ProposalError(f"{identifier} is both an instance and a cluster; say which")
        db = inst or clus
        if db is None:
            raise ProposalError("unknown database")
        return db

    def _stamp(self) -> str:
        return self._clock().astimezone(UTC).strftime("%Y%m%d%H%M")

    def _submit(self, caller: Caller, conversation_id: str | None, change_type: str, db: ScopedDb, *,
                summary: str, params: dict, diff: list[dict], impact: str, rollback: list[str],
                window: dict | None = None) -> dict:
        body = {
            "change_type": change_type,
            "database": {"identifier": db.identifier, "kind": db.kind, "engine": db.engine,
                         "region": db.region},
            "requested_by": caller.username, "summary": summary, "params": params, "diff": diff,
            "impact": impact, "rollback_plan": rollback, "maintenance_window": window or dict(NO_WINDOW),
        }
        if conversation_id:
            body["conversation_id"] = conversation_id
        return self._mock.create_change_request(body)

    # ---- tags
    def modify_tags(self, caller: Caller, conversation_id: str | None, identifier: str,
                    add: dict[str, str], remove: list[str]) -> dict:
        self._check_role(caller, "modify_tags")
        db = self._resolve(identifier)
        rds = self._factory(db.region)
        tags = _aws_read(f"tags for {db.identifier}",
                         lambda: rds.list_tags_for_resource(ResourceName=db.arn))
        current = {t["Key"]: t["Value"] for t in tags.get("TagList", [])}
        after = {k: v for k, v in current.items() if k not in remove}
        after.update(add)
        diff = [{"field": f"tag:{k}", "before": current.get(k), "after": after.get(k)}
                for k in sorted(set(current) | set(after)) if current.get(k) != after.get(k)]
        if not diff:
            raise ProposalError(f"{db.identifier} already has those tags; nothing to change")
        return self._submit(
            caller, conversation_id, "modify_tags", db,
            summary=f"Change tags on {db.identifier}", params={"before": current, "after": after},
            diff=diff, impact="No downtime; tags only", rollback=["Re-apply the previous tag set"])

    # ---- snapshot
    def create_snapshot(self, caller: Caller, conversation_id: str | None, identifier: str) -> dict:
        self._check_role(caller, "create_snapshot")
        db = self._resolve(identifier)
        name = f"{db.identifier}-dbops-{self._stamp()}"
        return self._submit(
            caller, conversation_id, "create_snapshot", db,
            summary=f"Snapshot {db.identifier} as {name}",
            params={"snapshot_identifier": name, "snapshot_kind": db.kind},
            diff=[{"field": "snapshot", "before": None, "after": name}],
            impact="No downtime; a brief I/O pause is possible on single-AZ instances",
            rollback=[f"Delete snapshot {name}"])

    # ---- restore test
    def restore_test(self, caller: Caller, conversation_id: str | None, identifier: str,
                     snapshot_id: str | None = None) -> dict:
        self._check_role(caller, "restore_test")
        db = self._resolve(identifier)
        rds = self._factory(db.region)
        snapshot = self._source_snapshot(rds, db, snapshot_id)
        cls = self._smallest_class(rds, db)
        temp = f"restoretest-{db.identifier}-{self._stamp()}"
        diff: list[dict] = []
        if db.kind == "cluster":
            diff.append({"field": "temporary_cluster", "before": None, "after": temp})
        diff += [
            {"field": "temporary_instance", "before": None, "after": temp},
            {"field": "instance_class", "before": None, "after": cls},
            {"field": "source_snapshot", "before": None, "after": snapshot},
            {"field": "estimated_cost_per_hour", "before": None, "after": self._cost(db, cls)},
        ]
        return self._submit(
            caller, conversation_id, "restore_test", db,
            summary=f"Restore-test {db.identifier} from {snapshot}",
            params={"source_snapshot": snapshot, "temporary_identifier": temp, "instance_class": cls},
            diff=diff, impact="No impact on the source database; a temporary copy runs until deleted",
            rollback=[f"Delete the temporary {'cluster and instance' if db.kind == 'cluster' else 'instance'} {temp}"])

    def _source_snapshot(self, rds: Any, db: ScopedDb, snapshot_id: str | None) -> str:
        if db.kind == "cluster":
            op, key, id_key, filt = ("describe_db_cluster_snapshots", "DBClusterSnapshots",
                                     "DBClusterSnapshotIdentifier", "DBClusterIdentifier")
        else:
            op, key, id_key, filt = ("describe_db_snapshots", "DBSnapshots",
                                     "DBSnapshotIdentifier", "DBInstanceIdentifier")
        rows = _aws_read(f"snapshots for {db.identifier}", lambda: [
            r for page in rds.get_paginator(op).paginate(**{filt: db.identifier})
            for r in page.get(key, [])])
        rows = [r for r in rows if r.get("Status", "available") == "available"]
        if snapshot_id:
            if not any(r[id_key] == snapshot_id for r in rows):
                raise ProposalError(f"{db.identifier} has no available snapshot {snapshot_id}")
            return snapshot_id
        if not rows:
            raise ProposalError(f"{db.identifier} has no snapshot to restore from")
        return max(rows, key=lambda r: r["SnapshotCreateTime"])[id_key]

    def _smallest_class(self, rds: Any, db: ScopedDb) -> str:
        orderable = _aws_read(f"orderable instance classes for {db.identifier}", lambda: {
            o["DBInstanceClass"]
            for page in rds.get_paginator("describe_orderable_db_instance_options").paginate(Engine=db.engine)
            for o in page.get("OrderableDBInstanceOptions", [])})
        for cls in _PREFERRED_CLASSES:
            if cls in orderable:
                return cls
        raise ProposalError(f"No small instance class is orderable for {db.engine}")

    def _cost(self, db: ScopedDb, cls: str) -> str:
        price = self._prices.get(db.engine, {}).get(db.region, {}).get(cls)
        return f"≈ ${price:.2f}/h (approx.)" if price is not None else "cost estimate unavailable"

    # ---- resize
    def modify_instance_class(self, caller: Caller, conversation_id: str | None, identifier: str,
                              to_class: str) -> dict:
        self._check_role(caller, "modify_instance_class")
        db = self._resolve(identifier, instance_only=True)
        if db.kind == "cluster":
            members = ", ".join(
                f"{m} ({'writer' if w else 'reader'}, {self._member_class(m, db)})" for m, w in db.members)
            raise ProposalError(f"{db.identifier} is an Aurora cluster; resize one instance: {members}")
        rds = self._factory(db.region)
        inst = _aws_read(f"instance {db.identifier}", lambda: rds.describe_db_instances(
            DBInstanceIdentifier=db.identifier))["DBInstances"][0]
        from_class = inst["DBInstanceClass"]
        if to_class == from_class:
            raise ProposalError(f"{db.identifier} is already {from_class}")
        params: dict = {"from_class": from_class, "to_class": to_class}
        if db.cluster:
            params["cluster_identifier"] = db.cluster
            writer = self._is_cluster_writer(rds, db)
            impact = ("Writer instance: ~1–2 min failover to a reader during the change" if writer else
                      "Reader instance: no write downtime; reads on this instance pause while it restarts")
        else:
            impact = "~5–10 min downtime (Multi-AZ failover)"
        pmw = inst["PreferredMaintenanceWindow"]
        inside, next_start = window_state(pmw, self._clock())
        return self._submit(
            caller, conversation_id, "modify_instance_class", db,
            summary=f"Resize {db.identifier} from {from_class} to {to_class}", params=params,
            diff=[{"field": "instance_class", "before": from_class, "after": to_class}], impact=impact,
            rollback=["Snapshot taken automatically before the change", f"Modify back to {from_class}"],
            window={"required": True, "window": pmw, "inside_now": inside, "next_start": next_start})

    def _is_cluster_writer(self, rds: Any, db: ScopedDb) -> bool:
        """Writer status from AWS: the cluster itself may be out of scope when only the member is tagged."""
        clusters = _aws_read(f"cluster {db.cluster}", lambda: rds.describe_db_clusters(
            DBClusterIdentifier=db.cluster))["DBClusters"]
        return any(m.get("DBInstanceIdentifier") == db.identifier and m.get("IsClusterWriter") is True
                   for c in clusters for m in c.get("DBClusterMembers", []))

    def _member_class(self, member: str, cluster: ScopedDb) -> str:
        m = self._scope.current().get(member, region=cluster.region, kind="instance")
        return (m.instance_class if m else None) or "unknown class"
