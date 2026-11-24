"""Result filter for `call_aws` (design §7.4). Fails closed: a shape it doesn't recognise is withheld."""

import copy
import json
import logging
import re
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


# Keys of a Strands ToolResult as built by strands.tools.mcp.MCPClient._handle_tool_result (strands 1.65).
_TOOL_RESULT_KEYS = frozenset({"toolUseId", "status", "content", "structuredContent", "metadata", "isError"})
# awslabs.aws-api-mcp-server 1.6.1 `call_aws`: a JSON list with one CallAWSResponse per command, each
# {"cli_command", "response": {"error", "status_code", "error_code", "pagination_token", "as_json"}, "metadata",
#  "validation_failures", "missing_context_failures", "failed_constraints"} (an error sets a top-level "error").
# The managed server's wrapper is unverified; {"response": {"json": ...}, "metadata": ...} is also accepted.
_CALL_KEYS = frozenset({"cli_command", "response", "metadata", "validation_failures", "missing_context_failures",
                        "failed_constraints", "error"})
_RESPONSE_KEYS = frozenset({"error", "status_code", "error_code", "pagination_token", "json", "as_json"})


def unwrap_tool_result(tool_result: Any) -> Any:
    """The AWS CLI output inside a `call_aws` ToolResult, for `filter_result`.

    Returns None (which `filter_result` withholds) for an error result or any shape it doesn't recognise.
    Only the text block is read; structuredContent and metadata are ignored, never passed on.
    """
    try:
        return _cli_body(tool_result)
    except Exception as exc:  # noqa: BLE001 - any surprise must withhold, never pass data
        log.warning("tool result withheld (%s): %.4000r", exc, tool_result)
        return None


AWS_ERROR = "AWS returned an error"
CLI_REJECTED = "The AWS CLI rejected the command syntax"
# botocore's ClientError text: "An error occurred (Code) when calling the X operation: ...".
_ERROR_CODE_IN_MESSAGE = re.compile(r"An error occurred \(([A-Za-z][A-Za-z0-9.]{0,99})\)")
_ERROR_CODE = re.compile(r"[A-Za-z][A-Za-z0-9.]{0,99}")


def aws_failure(tool_result: Any) -> dict[str, str] | None:
    """A readable error for a `call_aws` result reporting a failed command, else None.

    Only the fixed messages and an AWS error code (a bare token like InvalidParameterCombination) are returned;
    the raw AWS message can name other databases, so it is logged, never passed on. Anything that isn't a
    recognised failure returns None and goes through `filter_result` (which withholds what it doesn't know).
    """
    try:
        payload = _tool_payload(tool_result)
        call = _one_call(payload) if isinstance(payload, list) else payload
    except Exception:  # noqa: BLE001 - not a recognised failure: the result filter decides
        return None
    if not isinstance(call, dict) or not ("response" in call or "cli_command" in call) or not set(call) <= _CALL_KEYS:
        return None
    if any(call.get(k) for k in ("validation_failures", "missing_context_failures", "failed_constraints")):
        log.warning("AWS CLI rejected the command: %.4000r", call)
        return {"error": CLI_REJECTED}
    response = call.get("response")
    response_error = response.get("error") if isinstance(response, dict) else None
    if call.get("error") is None and response_error is None:
        return None
    log.warning("AWS returned an error: %.4000r", call)
    code = response.get("error_code") if isinstance(response, dict) else None
    if not (isinstance(code, str) and _ERROR_CODE.fullmatch(code)):
        code = None
        for message in (response_error, call.get("error")):
            match = _ERROR_CODE_IN_MESSAGE.search(message) if isinstance(message, str) else None
            if match:
                code = match.group(1)
                break
    return {"error": AWS_ERROR, "code": code} if code else {"error": AWS_ERROR}


def _tool_payload(tool_result: Any) -> Any:
    """The parsed JSON text block of a successful ToolResult."""
    if not isinstance(tool_result, dict) or not set(tool_result) <= _TOOL_RESULT_KEYS:
        raise _Withheld("not a ToolResult")
    if tool_result.get("status") != "success" or tool_result.get("isError", False) is not False:
        raise _Withheld("tool returned an error")
    content = tool_result.get("content")
    if not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict) \
            or set(content[0]) != {"text"} or not isinstance(content[0]["text"], str):
        raise _Withheld("content must be exactly one text block")
    return json.loads(content[0]["text"])


def _one_call(payload: list[Any]) -> dict[str, Any]:
    """The one CallAWSResponse in a `call_aws` result list."""
    if len(payload) != 1 or not isinstance(payload[0], dict) or "cli_command" not in payload[0]:
        raise _Withheld("expected exactly one command result")
    return payload[0]


def _cli_body(tool_result: Any) -> dict[str, Any]:
    payload = _tool_payload(tool_result)
    if isinstance(payload, list):
        payload = _one_call(payload)
    if not isinstance(payload, dict):
        raise _Withheld("top level is not an object")
    if "response" in payload or "cli_command" in payload:
        payload = _call_response(payload)
    payload.pop("ResponseMetadata", None)  # request id, HTTP status and headers: no database data
    return payload


def _call_response(call: dict[str, Any]) -> dict[str, Any]:
    if not set(call) <= _CALL_KEYS or call.get("error") is not None:
        raise _Withheld("command failed or has unknown keys")
    if any(call.get(k) for k in ("validation_failures", "missing_context_failures", "failed_constraints")):
        raise _Withheld("command failed validation")
    response = call.get("response")
    if not isinstance(response, dict) or not set(response) <= _RESPONSE_KEYS or response.get("error") is not None:
        raise _Withheld("response is an error or has unknown keys")
    bodies = [response[k] for k in ("json", "as_json") if response.get(k) is not None]
    if len(bodies) != 1 or not isinstance(bodies[0], str):
        raise _Withheld("response must carry exactly one JSON body")
    body = json.loads(bodies[0])
    if not isinstance(body, dict):
        raise _Withheld("body is not an object")
    return body


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
