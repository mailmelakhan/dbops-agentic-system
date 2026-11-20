"""Result filter for `call_aws` (design §7.4). Fails closed: a shape it doesn't recognise is withheld."""

import copy
import json
import logging
from collections.abc import Callable
from typing import Any

from agent_api.scope import ScopeSet

log = logging.getLogger(__name__)

WITHHELD = {"error": "result withheld: unrecognised shape"}
RESULT_LIMIT = 20480

# Passed through unchanged. The guard has already checked the resource (TagList) or required AWS/RDS
# with in-scope DB dimensions (metric data); orderable options and paging tokens hold no database data.
_PASS_THROUGH = frozenset({"Marker", "NextToken", "Label", "Messages", "TagList", "Datapoints",
                           "MetricDataResults", "OrderableDBInstanceOptions"})
_DB_DIMENSIONS = {"dbinstanceidentifier": "instance", "dbclusteridentifier": "cluster"}
_EVENT_KINDS = {"db-instance": "instance", "db-cluster": "cluster"}
_ENVELOPE_KEYS = frozenset({"content", "isError"})


class _Withheld(Exception):
    pass


def filter_result(raw: Any, scope: ScopeSet, region: str) -> tuple[Any, bool]:
    """Return (filtered result, True), or (WITHHELD, False) if any part of the shape is unrecognised.

    `region` is the region the call targeted; it applies to rows whose ARN doesn't name one.
    """
    try:
        body = copy.deepcopy(_unwrap(raw))  # kept rows are scrubbed in place; never touch the caller's object
        filtered = {key: _filter_key(key, value, scope, region) for key, value in body.items()}
    except Exception as exc:  # noqa: BLE001 - any surprise in the filter must withhold, never pass data
        log.warning("result withheld (%s): %.4000r", exc, raw)
        return dict(WITHHELD), False
    return filtered, True


def truncate(result: Any, limit: int = RESULT_LIMIT) -> tuple[Any, bool]:
    """Same semantics as mock_backend.runs.truncate_result."""
    text = json.dumps(result)
    if len(text.encode()) <= limit:
        return result, False
    return {"preview": text[:limit]}, True


def _unwrap(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict) and "content" in raw:
        if not set(raw) <= _ENVELOPE_KEYS or raw.get("isError", False) is not False:
            raise _Withheld("MCP envelope is an error or has unknown keys")
        raw = raw["content"]
    if isinstance(raw, list):
        if len(raw) != 1:
            raise _Withheld("MCP content must hold exactly one item")
        item = raw[0]
        if not isinstance(item, dict) or set(item) != {"type", "text"} or item["type"] != "text":
            raise _Withheld("MCP content item is not text")
        raw = item["text"]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raise _Withheld("not JSON") from None
    if not isinstance(raw, dict):
        raise _Withheld("top level is not an object")
    return raw


def _filter_key(key: str, value: Any, scope: ScopeSet, region: str) -> Any:
    if key in _PASS_THROUGH:
        return value
    if key == "CompositeAlarms":
        _rows(value)
        return []  # a composite alarm can't be tied to one database
    keep = _ROW_FILTERS.get(key)
    if keep is None:
        raise _Withheld(f"unknown key {key!r}")
    return [row for row in _rows(value) if keep(row, scope, region)]


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise _Withheld("expected a list of objects")
    return value


def _parse_arn(arn: Any) -> tuple[str, str, str, str, str] | None:
    """(service, region, account, resource type, resource id) of an ARN, or None."""
    if not isinstance(arn, str):
        return None
    parts = arn.strip().split(":", 6)
    if len(parts) != 7 or parts[0] != "arn":
        return None
    return parts[2].lower(), parts[3], parts[4], parts[5].lower(), parts[6]


def _row_region(row: dict[str, Any], arn_key: str, service: str, region: str) -> str | None:
    """The row's region: from its ARN if present (None if that ARN is malformed), else the call's region."""
    if arn_key not in row:
        return region
    parsed = _parse_arn(row[arn_key])
    if parsed is None or parsed[0] != service:
        return None
    return parsed[1]


def _in_scope(scope: ScopeSet, ident: Any, region: str | None, kind: str) -> bool:
    return isinstance(ident, str) and region is not None and scope.contains(ident, region=region, kind=kind)


def _db_row(id_key: str, arn_key: str, kind: str) -> Callable[[dict, ScopeSet, str], bool]:
    arn_type = "db" if kind == "instance" else "cluster"

    def keep(row: dict[str, Any], scope: ScopeSet, region: str) -> bool:
        ident = row.get(id_key)
        if arn_key in row:
            parsed = _parse_arn(row[arn_key])
            if (parsed is None or parsed[0] != "rds" or parsed[3] != arn_type or not isinstance(ident, str)
                    or parsed[4].lower() != ident.lower()):
                return False
        if not _in_scope(scope, ident, _row_region(row, arn_key, "rds", region), kind):
            return False
        _scrub_references(row, scope, region)
        return True

    return keep


# Fields of an in-scope row that name other databases: (key, kind, is a list).
_REFERENCES = (("ReadReplicaDBInstanceIdentifiers", "instance", True),
               ("ReadReplicaDBClusterIdentifiers", "cluster", True),
               ("ReadReplicaSourceDBInstanceIdentifier", "instance", False),
               ("ReadReplicaSourceDBClusterIdentifier", "cluster", False),
               ("ReadReplicaIdentifiers", "instance", True),
               ("ReplicationSourceIdentifier", "cluster", False))


def _scrub_references(row: dict[str, Any], scope: ScopeSet, region: str) -> None:
    """Drop references to out-of-scope replicas and replication sources from a kept row (in place)."""
    for key, kind, is_list in _REFERENCES:
        if key not in row:
            continue
        value = row[key]
        if is_list:
            if not isinstance(value, list):
                raise _Withheld(f"{key} is not a list")
            row[key] = [v for v in value if _ref_in_scope(scope, v, region, kind)]
        elif not _ref_in_scope(scope, value, region, kind):
            del row[key]


def _ref_in_scope(scope: ScopeSet, ref: Any, region: str, kind: str) -> bool:
    """A reference may be a plain identifier or an ARN of either kind (Aurora replicas name clusters)."""
    parsed = _parse_arn(ref)
    if parsed is not None:
        return parsed[0] == "rds" and isinstance(ref, str) and scope.contains(ref)
    return _in_scope(scope, ref, region, kind)


def _snapshot(source_key: str, arn_key: str, kind: str) -> Callable[[dict, ScopeSet, str], bool]:
    def keep(row: dict[str, Any], scope: ScopeSet, region: str) -> bool:
        parsed = _parse_arn(row.get(arn_key))
        source = row.get(source_key)
        if parsed is None or parsed[0] != "rds" or not isinstance(source, str):
            return False
        db = scope.get(source, region=parsed[1], kind=kind)
        if db is None:
            return False
        db_arn = _parse_arn(db.arn)
        return db_arn is not None and db_arn[2] != "" and db_arn[2] == parsed[2]

    return keep


def _event(row: dict[str, Any], scope: ScopeSet, region: str) -> bool:
    source_type = row.get("SourceType")
    kind = _EVENT_KINDS.get(source_type) if isinstance(source_type, str) else None
    if kind is None:
        return False  # snapshot, parameter group, security group ... events
    if "SourceArn" in row and not (isinstance(row["SourceArn"], str) and scope.contains(row["SourceArn"], kind=kind)):
        return False
    return _in_scope(scope, row.get("SourceIdentifier"), _row_region(row, "SourceArn", "rds", region), kind)


def _pending_maintenance(row: dict[str, Any], scope: ScopeSet, region: str) -> bool:
    ident = row.get("ResourceIdentifier")
    parsed = _parse_arn(ident)
    return parsed is not None and parsed[0] == "rds" and isinstance(ident, str) and scope.contains(ident)


def _dimensions_in_scope(dimensions: Any, scope: ScopeSet, region: str | None) -> bool:
    """At least one DB dimension, and every DB dimension in scope."""
    if not isinstance(dimensions, list):
        return False
    found = False
    for dim in dimensions:
        if not isinstance(dim, dict) or not isinstance(dim.get("Name"), str):
            return False
        kind = _DB_DIMENSIONS.get(dim["Name"].lower())
        if kind:
            if not _in_scope(scope, dim.get("Value"), region, kind):
                return False
            found = True
    return found


def _alarm(row: dict[str, Any], scope: ScopeSet, region: str) -> bool:
    if row.get("Metrics"):
        return False  # metric-math alarms can reach any database
    return _dimensions_in_scope(row.get("Dimensions"), scope, _row_region(row, "AlarmArn", "cloudwatch", region))


def _metric(row: dict[str, Any], scope: ScopeSet, region: str) -> bool:
    return _dimensions_in_scope(row.get("Dimensions"), scope, region)


_ROW_FILTERS: dict[str, Callable[[dict, ScopeSet, str], bool]] = {
    "DBInstances": _db_row("DBInstanceIdentifier", "DBInstanceArn", "instance"),
    "DBClusters": _db_row("DBClusterIdentifier", "DBClusterArn", "cluster"),
    "DBSnapshots": _snapshot("DBInstanceIdentifier", "DBSnapshotArn", "instance"),
    "DBClusterSnapshots": _snapshot("DBClusterIdentifier", "DBClusterSnapshotArn", "cluster"),
    "Events": _event,
    "PendingMaintenanceActions": _pending_maintenance,
    "MetricAlarms": _alarm,
    "Metrics": _metric,
}
