"""Command guard for `call_aws` (design §7.3). Fails closed: anything it can't classify is refused."""

import json
import re
import shlex
from typing import Any

from agent_api.scope import ScopeSet

ALLOWED_OPERATIONS: dict[str, frozenset[str]] = {
    "rds": frozenset({
        "describe-db-instances", "describe-db-clusters", "describe-db-snapshots", "describe-db-cluster-snapshots",
        "describe-events", "describe-pending-maintenance-actions", "describe-orderable-db-instance-options",
        "list-tags-for-resource",
    }),
    "cloudwatch": frozenset({"get-metric-statistics", "get-metric-data", "list-metrics", "describe-alarms"}),
}

CHAINED = "only one AWS CLI command at a time"
UNKNOWN_DB = "unknown database"
UNPARSEABLE = "could not parse command"

FORBIDDEN_FLAGS = frozenset({"--endpoint-url", "--profile", "--cli-input-json", "--cli-input-yaml"})
# Values the AWS CLI would load from somewhere other than the command line.
FORBIDDEN_PREFIXES = ("file://", "fileb://", "http://", "https://")

# Flags accepted on every allowed operation. Anything not listed here or in ALLOWED_FLAGS is refused,
# so abbreviations, misspellings and flags that could carry a database reference we don't check never run.
# --query is deliberately absent: a JMESPath literal can relabel out-of-scope rows with an in-scope
# identifier, defeating the result filter. --output is only accepted as json (checked in _check).
GLOBAL_FLAGS = frozenset({"--region", "--output", "--no-paginate", "--max-items", "--page-size",
                          "--starting-token", "--no-cli-pager", "--cli-read-timeout", "--cli-connect-timeout"})
_RDS_PAGING = frozenset({"--filters", "--max-records", "--marker"})
ALLOWED_FLAGS: dict[tuple[str, str], frozenset[str]] = {
    ("rds", "describe-db-instances"): _RDS_PAGING | {"--db-instance-identifier"},
    ("rds", "describe-db-clusters"): _RDS_PAGING | {"--db-cluster-identifier"},
    ("rds", "describe-db-snapshots"): _RDS_PAGING | {"--db-instance-identifier", "--db-snapshot-identifier",
                                                     "--snapshot-type"},
    ("rds", "describe-db-cluster-snapshots"): _RDS_PAGING | {"--db-cluster-identifier",
                                                             "--db-cluster-snapshot-identifier", "--snapshot-type"},
    ("rds", "describe-events"): _RDS_PAGING | {"--source-identifier", "--source-type", "--start-time",
                                               "--end-time", "--duration", "--event-categories"},
    ("rds", "describe-pending-maintenance-actions"): _RDS_PAGING | {"--resource-identifier"},
    ("rds", "describe-orderable-db-instance-options"): _RDS_PAGING | {
        "--engine", "--engine-version", "--db-instance-class", "--license-model", "--availability-zone-group",
        "--vpc", "--no-vpc"},
    ("rds", "list-tags-for-resource"): frozenset({"--resource-name", "--filters"}),
    ("cloudwatch", "get-metric-statistics"): frozenset({
        "--namespace", "--metric-name", "--dimensions", "--start-time", "--end-time", "--period", "--statistics",
        "--extended-statistics", "--unit"}),
    ("cloudwatch", "get-metric-data"): frozenset({
        "--metric-data-queries", "--start-time", "--end-time", "--next-token", "--scan-by", "--max-datapoints",
        "--label-options"}),
    ("cloudwatch", "list-metrics"): frozenset({"--namespace", "--metric-name", "--dimensions", "--next-token",
                                               "--recently-active"}),
    ("cloudwatch", "describe-alarms"): frozenset({
        "--alarm-names", "--alarm-name-prefix", "--alarm-types", "--children-of-alarm-name",
        "--parents-of-alarm-name", "--state-value", "--action-prefix", "--max-records", "--next-token"}),
}
# Shared and public snapshot listings would include other accounts' snapshots.
ALLOWED_SNAPSHOT_TYPES = frozenset({"automated", "manual", "awsbackup"})

_PUNCTUATION = set("();<>|&")
_ARN = re.compile(r"^arn:aws[\w-]*:rds:[^:]+:[^:]*:(db|cluster):.+$", re.IGNORECASE)
_SOURCE_KINDS = {"db-instance": "instance", "db-cluster": "cluster"}
_DB_DIMENSIONS = {"dbinstanceidentifier": "instance", "dbclusteridentifier": "cluster"}
_WORD = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class _Refused(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def check_command(command: str, scope: ScopeSet, regions: tuple[str, ...]) -> str | None:
    """None if the command may run, else the refusal reason shown to the model."""
    try:
        _check(command, scope, tuple(regions))
    except _Refused as refused:
        return refused.reason
    except Exception:  # noqa: BLE001 - any surprise in the guard must refuse, never allow
        return UNPARSEABLE
    return None


def target_region(command: str, regions: tuple[str, ...]) -> str:
    """The region a command the guard allowed will run in: the last --region given, else regions[0]."""
    tokens = _tokenize(command)
    flags = _parse_flags(tokens[3:], GLOBAL_FLAGS | ALLOWED_FLAGS[(tokens[1], tokens[2])])
    values = _single(flags.get("--region", []))
    return values[-1] if values else regions[0]


def step_name(command: str) -> str:
    try:
        tokens = shlex.split(command)
    except (ValueError, AttributeError):
        return "aws"
    if len(tokens) >= 3 and tokens[0] == "aws" and _WORD.match(tokens[1]) and _WORD.match(tokens[2]):
        return f"{tokens[1]}.{tokens[2].replace('-', '_')}"
    return "aws"


def _tokenize(command: str) -> list[str]:
    if not isinstance(command, str):
        raise _Refused(UNPARSEABLE)
    if "`" in command or "$(" in command or "${" in command or "\n" in command or "\r" in command:
        raise _Refused(CHAINED)
    if "$" in command:
        raise _Refused(UNPARSEABLE)
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        tokens = list(lexer)
    except ValueError:
        raise _Refused(UNPARSEABLE) from None
    if any(tok and set(tok) <= _PUNCTUATION for tok in tokens):
        raise _Refused(CHAINED)
    if not tokens or tokens[0] != "aws":
        raise _Refused(UNPARSEABLE)
    if "aws" in tokens[1:]:
        raise _Refused(CHAINED)
    if any(tok.startswith("#") for tok in tokens):
        raise _Refused(UNPARSEABLE)
    return tokens


def _check_forbidden(tokens: list[str]) -> None:
    for tok in tokens[1:]:
        if tok.startswith("--"):
            name, _, value = tok.partition("=")
            if name in FORBIDDEN_FLAGS:
                raise _Refused(f"flag not allowed: {name}")
        else:
            value = tok
        for prefix in FORBIDDEN_PREFIXES:
            if value.strip().lower().startswith(prefix):
                raise _Refused(f"flag not allowed: {prefix}")


def _parse_flags(tokens: list[str], allowed: frozenset[str]) -> dict[str, list[list[str]]]:
    """Map each flag to its occurrences, each a list of the values that followed it."""
    flags: dict[str, list[list[str]]] = {}
    current: list[str] | None = None
    for tok in tokens:
        if tok.startswith("-"):
            name, eq, value = tok.partition("=")
            if not name.startswith("--") or name not in allowed:
                raise _Refused(f"flag not allowed: {name}")
            current = [value] if eq else []
            flags.setdefault(name, []).append(current)
        elif current is None:
            raise _Refused(UNPARSEABLE)
        else:
            current.append(tok)
    return flags


def _single(occurrences: list[list[str]]) -> list[str]:
    """Every value given to a flag that must carry one value per occurrence."""
    if any(len(occ) != 1 for occ in occurrences):
        raise _Refused(UNPARSEABLE)
    return [occ[0] for occ in occurrences]


def _values(occurrences: list[list[str]]) -> list[str]:
    if any(not occ for occ in occurrences):
        raise _Refused(UNPARSEABLE)
    return [v for occ in occurrences for v in occ]


def _check(command: str, scope: ScopeSet, regions: tuple[str, ...]) -> None:
    tokens = _tokenize(command)
    _check_forbidden(tokens)
    if len(tokens) < 3 or tokens[1].startswith("-") or tokens[2].startswith("-"):
        raise _Refused(UNPARSEABLE)
    service, op = tokens[1], tokens[2]
    if op not in ALLOWED_OPERATIONS.get(service, frozenset()):
        raise _Refused(f"operation not allowed: {service} {op}")
    flags = _parse_flags(tokens[3:], GLOBAL_FLAGS | ALLOWED_FLAGS[(service, op)])

    if any(occ != ["json"] for occ in flags.get("--output", [])):
        raise _Refused("flag not allowed: --output")

    if not regions:
        raise _Refused(UNPARSEABLE)
    region = regions[0]
    for value in _single(flags.get("--region", [])):
        if value not in regions:
            raise _Refused(f"region not allowed: {value}")
        region = value  # the AWS CLI uses the last --region given

    for value in _single(flags.get("--snapshot-type", [])):
        if value not in ALLOWED_SNAPSHOT_TYPES:
            raise _Refused("flag not allowed: --snapshot-type")

    def need(identifier: Any, kind: str) -> None:
        if not isinstance(identifier, str) or not scope.contains(identifier, region=region, kind=kind):
            raise _Refused(UNKNOWN_DB)

    for value in _values(flags.get("--db-instance-identifier", [])):
        need(value, "instance")
    for value in _values(flags.get("--db-cluster-identifier", [])):
        need(value, "cluster")
    for flag in ("--resource-name", "--resource-identifier"):
        for value in _values(flags.get(flag, [])):
            match = _ARN.match(value.strip())
            if not match:
                raise _Refused(UNKNOWN_DB)
            need(value, "instance" if match.group(1).lower() == "db" else "cluster")
    if op == "list-tags-for-resource" and "--resource-name" not in flags:
        raise _Refused(UNKNOWN_DB)

    if "--source-identifier" in flags:
        source_types = set(_single(flags.get("--source-type", [])))
        if len(source_types) != 1 or next(iter(source_types)) not in _SOURCE_KINDS:
            raise _Refused(UNKNOWN_DB)
        kind = _SOURCE_KINDS[next(iter(source_types))]
        for value in _values(flags["--source-identifier"]):
            need(value, kind)

    if service == "cloudwatch":
        _check_metrics(op, flags, need)


def _check_metrics(op: str, flags: dict[str, list[list[str]]], need) -> None:
    dimensions = [d for v in _values(flags.get("--dimensions", [])) for d in _as_list(_structured(v))]
    if op == "list-metrics":
        for dim in dimensions:
            if not isinstance(dim, dict) or not set(dim) <= {"Name", "Value"}:
                raise _Refused(UNKNOWN_DB)
            kind = _DB_DIMENSIONS.get(str(dim.get("Name", "")).lower())
            if kind and "Value" in dim:
                need(dim["Value"], kind)
    elif op == "get-metric-statistics":
        namespaces = _single(flags.get("--namespace", []))
        if not namespaces or any(ns != "AWS/RDS" for ns in namespaces):
            raise _Refused(UNKNOWN_DB)
        _check_db_dimensions(dimensions, need)
    elif op == "get-metric-data":
        queries = [q for v in _values(flags.get("--metric-data-queries", [])) for q in _as_list(_structured(v))]
        if not queries:
            raise _Refused(UNKNOWN_DB)
        for query in queries:
            _check_query(query, need)


def _check_query(query: Any, need) -> None:
    """Only plain MetricStat queries on AWS/RDS; no expressions (SEARCH/SELECT reach any database)."""
    if not isinstance(query, dict) or not set(query) <= {"Id", "MetricStat", "Label", "ReturnData", "Period"}:
        raise _Refused(UNKNOWN_DB)
    stat = query.get("MetricStat")
    if not isinstance(stat, dict) or not set(stat) <= {"Metric", "Period", "Stat", "Unit"}:
        raise _Refused(UNKNOWN_DB)
    metric = stat.get("Metric")
    if not isinstance(metric, dict) or not set(metric) <= {"Namespace", "MetricName", "Dimensions"}:
        raise _Refused(UNKNOWN_DB)
    if metric.get("Namespace") != "AWS/RDS":
        raise _Refused(UNKNOWN_DB)
    dimensions = metric.get("Dimensions")
    if not isinstance(dimensions, list):
        raise _Refused(UNKNOWN_DB)
    _check_db_dimensions(dimensions, need)


def _check_db_dimensions(dimensions: list[Any], need) -> None:
    """At least one DB dimension, and every DB dimension in scope."""
    found = False
    for dim in dimensions:
        if not isinstance(dim, dict) or set(dim) != {"Name", "Value"} or not isinstance(dim["Name"], str):
            raise _Refused(UNKNOWN_DB)
        kind = _DB_DIMENSIONS.get(dim["Name"].lower())
        if kind:
            need(dim["Value"], kind)
            found = True
    if not found:
        raise _Refused(UNKNOWN_DB)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key {key}")
        out[key] = value
    return out


def _structured(value: str) -> Any:
    """Parse a CloudWatch structure argument the way the AWS CLI does: JSON if it starts with [ or {,
    otherwise shorthand. Anything outside the strict subset below is refused."""
    text = value.strip()
    if text[:1] in ("[", "{"):
        try:
            return json.loads(text, object_pairs_hook=_no_duplicates)
        except ValueError:
            raise _Refused(UNPARSEABLE) from None
    return _Shorthand(text).parse()


class _Shorthand:
    """Strict subset of AWS CLI shorthand: key=value pairs, {struct}, [list], bare scalars.
    Quotes, escapes, whitespace and implicit scalar lists (Key=a,b) are refused."""

    _KEY = re.compile(r"[A-Za-z0-9_]+")
    _SCALAR = re.compile(r"[^,={}\[\]\s'\"\\]+")

    def __init__(self, text: str):
        self.text = text
        self.pos = 0

    def parse(self) -> dict[str, Any]:
        result = self._struct()
        if self.pos != len(self.text):
            raise _Refused(UNPARSEABLE)
        return result

    def _peek(self) -> str:
        return self.text[self.pos:self.pos + 1]

    def _expect(self, char: str) -> None:
        if self._peek() != char:
            raise _Refused(UNPARSEABLE)
        self.pos += 1

    def _match(self, pattern: re.Pattern[str]) -> str:
        m = pattern.match(self.text, self.pos)
        if not m:
            raise _Refused(UNPARSEABLE)
        self.pos = m.end()
        return m.group(0)

    def _struct(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        while True:
            key = self._match(self._KEY)
            self._expect("=")
            if key in out:
                raise _Refused(UNPARSEABLE)
            out[key] = self._value()
            if self._peek() != ",":
                return out
            self.pos += 1

    def _value(self) -> Any:
        char = self._peek()
        if char == "{":
            self.pos += 1
            value = self._struct()
            self._expect("}")
            return value
        if char == "[":
            self.pos += 1
            items: list[Any] = []
            if self._peek() != "]":
                items.append(self._value())
                while self._peek() == ",":
                    self.pos += 1
                    items.append(self._value())
            self._expect("]")
            return items
        return self._match(self._SCALAR)
