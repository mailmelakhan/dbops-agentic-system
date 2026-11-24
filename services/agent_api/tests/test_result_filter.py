import json

import pytest
from agent_api.result_filter import WITHHELD, filter_result, truncate, unwrap_tool_result
from agent_api.scope import ScopedDb, ScopeSet

REGION = "us-east-1"
ACCOUNT = "123"


def _db(ident, kind, cluster=None, members=()):
    arn_kind = "db" if kind == "instance" else "cluster"
    return ScopedDb(region=REGION, identifier=ident, kind=kind, engine="postgres",
                    instance_class="db.t3.micro" if kind == "instance" else None, status="available",
                    cluster=cluster, members=members, arn=f"arn:aws:rds:{REGION}:{ACCOUNT}:{arn_kind}:{ident}")


SCOPE = ScopeSet([
    _db("prod-a", "instance"),
    _db("aur-1", "cluster", members=(("aur-1-w", True), ("aur-1-r", False))),
    _db("aur-1-w", "instance", cluster="aur-1"),
    _db("aur-1-r", "instance", cluster="aur-1"),
])
WITHHELD_RESULT = ({"error": "result withheld: unrecognised shape"}, False)


def run(raw, region=REGION):
    return filter_result(raw, SCOPE, region)


def ids(rows, key):
    return [r[key] for r in rows]


def arn(kind, ident, region=REGION, account=ACCOUNT):
    return f"arn:aws:rds:{region}:{account}:{kind}:{ident}"


def dims(name, value):
    return [{"Name": name, "Value": value}]


def test_withheld_message_is_verbatim():
    assert WITHHELD == {"error": "result withheld: unrecognised shape"}


def test_filter_instances_and_clusters():
    raw = {
        "DBInstances": [
            {"DBInstanceIdentifier": "prod-a", "DBInstanceArn": arn("db", "prod-a")},
            {"DBInstanceIdentifier": "other-db", "DBInstanceArn": arn("db", "other-db")},
            {"DBInstanceIdentifier": "aur-1-r", "DBClusterIdentifier": "aur-1"},
            {"DBInstanceIdentifier": "AUR-1-W"},
        ],
        "DBClusters": [
            {"DBClusterIdentifier": "aur-1", "DBClusterArn": arn("cluster", "aur-1")},
            {"DBClusterIdentifier": "aur-2", "DBClusterArn": arn("cluster", "aur-2")},
        ],
    }
    out, ok = run(raw)
    assert ok
    assert ids(out["DBInstances"], "DBInstanceIdentifier") == ["prod-a", "aur-1-r", "AUR-1-W"]
    assert ids(out["DBClusters"], "DBClusterIdentifier") == ["aur-1"]
    assert "other-db" not in json.dumps(out) and "aur-2" not in json.dumps(out)


def test_filter_instance_rows_use_arn_region():
    raw = {"DBInstances": [
        {"DBInstanceIdentifier": "prod-a", "DBInstanceArn": arn("db", "prod-a", region="eu-west-1")},
        {"DBInstanceIdentifier": "prod-a"},
    ]}
    assert run(raw)[0]["DBInstances"] == [{"DBInstanceIdentifier": "prod-a"}]
    assert run(raw, region="eu-west-1")[0]["DBInstances"] == []


def test_filter_kind_namespaces():
    # "prod-a" is an in-scope instance; a cluster named "prod-a" is a different database.
    raw = {"DBClusters": [{"DBClusterIdentifier": "prod-a", "DBClusterArn": arn("cluster", "prod-a")}],
           "DBInstances": [{"DBInstanceIdentifier": "aur-1"}]}
    out, ok = run(raw)
    assert ok and out == {"DBClusters": [], "DBInstances": []}


def test_filter_scrubs_out_of_scope_replica_references():
    raw = {"DBInstances": [{"DBInstanceIdentifier": "prod-a",
                            "ReadReplicaDBInstanceIdentifiers": ["aur-1-r", "other-replica"],
                            "ReadReplicaSourceDBInstanceIdentifier": "other-source"}]}
    out, ok = run(raw)
    assert ok
    assert out["DBInstances"][0]["ReadReplicaDBInstanceIdentifiers"] == ["aur-1-r"]
    assert "other" not in json.dumps(out)


def test_filter_events_and_snapshots():
    raw = {
        "Events": [
            {"SourceIdentifier": "prod-a", "SourceType": "db-instance", "Message": "ok"},
            {"SourceIdentifier": "other-db", "SourceType": "db-instance", "Message": "x"},
            {"SourceIdentifier": "aur-1", "SourceType": "db-cluster", "Message": "ok"},
        ],
        "DBSnapshots": [
            {"DBSnapshotIdentifier": "s1", "DBInstanceIdentifier": "prod-a", "DBSnapshotArn": arn("snapshot", "s1")},
            {"DBSnapshotIdentifier": "s2", "DBInstanceIdentifier": "other-db",
             "DBSnapshotArn": arn("snapshot", "s2")},
        ],
        "DBClusterSnapshots": [
            {"DBClusterSnapshotIdentifier": "c1", "DBClusterIdentifier": "aur-1",
             "DBClusterSnapshotArn": arn("cluster-snapshot", "c1")},
            {"DBClusterSnapshotIdentifier": "c2", "DBClusterIdentifier": "aur-2",
             "DBClusterSnapshotArn": arn("cluster-snapshot", "c2")},
        ],
    }
    out, ok = run(raw)
    assert ok
    assert ids(out["Events"], "SourceIdentifier") == ["prod-a", "aur-1"]
    assert ids(out["DBSnapshots"], "DBSnapshotIdentifier") == ["s1"]
    assert ids(out["DBClusterSnapshots"], "DBClusterSnapshotIdentifier") == ["c1"]


def test_filter_cross_account_snapshot_dropped():
    raw = {"DBSnapshots": [
        {"DBSnapshotIdentifier": "mine", "DBInstanceIdentifier": "prod-a", "DBSnapshotArn": arn("snapshot", "mine")},
        {"DBSnapshotIdentifier": "shared", "DBInstanceIdentifier": "prod-a",
         "DBSnapshotArn": arn("snapshot", "shared", account="999")},
        {"DBSnapshotIdentifier": "no-arn", "DBInstanceIdentifier": "prod-a"},
        {"DBSnapshotIdentifier": "no-source", "DBSnapshotArn": arn("snapshot", "no-source")},
        {"DBSnapshotIdentifier": "cluster-kind", "DBInstanceIdentifier": "aur-1",
         "DBSnapshotArn": arn("snapshot", "cluster-kind")},
    ]}
    out, ok = run(raw)
    assert ok and ids(out["DBSnapshots"], "DBSnapshotIdentifier") == ["mine"]


def test_filter_snapshot_events_dropped():
    raw = {"Events": [
        {"SourceIdentifier": "prod-a", "SourceType": "db-snapshot", "Message": "snap"},
        {"SourceIdentifier": "prod-a", "SourceType": "db-parameter-group"},
        {"SourceIdentifier": "aur-1", "SourceType": "db-instance"},
        {"SourceIdentifier": "prod-a", "SourceType": "db-instance",
         "SourceArn": arn("db", "prod-a", region="eu-west-1")},
        {"SourceIdentifier": "prod-a", "SourceType": "db-instance", "SourceArn": arn("db", "other-db")},
        {"SourceIdentifier": "prod-a", "SourceType": ["db-instance"]},
        {"SourceIdentifier": "prod-a"},
        {"SourceIdentifier": "aur-1-w", "SourceType": "db-instance", "Message": "kept"},
    ]}
    out, ok = run(raw)
    assert ok and out["Events"] == [{"SourceIdentifier": "aur-1-w", "SourceType": "db-instance", "Message": "kept"}]


def test_filter_pending_maintenance():
    raw = {"PendingMaintenanceActions": [
        {"ResourceIdentifier": arn("db", "prod-a"), "PendingMaintenanceActionDetails": [{"Action": "os-upgrade"}]},
        {"ResourceIdentifier": arn("db", "other-db"), "PendingMaintenanceActionDetails": []},
        {"ResourceIdentifier": arn("cluster", "aur-1")},
        {"ResourceIdentifier": arn("cluster", "prod-a")},
        {"ResourceIdentifier": arn("db", "prod-a", region="eu-west-1")},
        {"ResourceIdentifier": "prod-a"},
        {},
    ]}
    out, ok = run(raw)
    assert ok
    assert ids(out["PendingMaintenanceActions"], "ResourceIdentifier") == [arn("db", "prod-a"),
                                                                           arn("cluster", "aur-1")]


def test_filter_alarms_and_metrics():
    alarm_arn = f"arn:aws:cloudwatch:{REGION}:{ACCOUNT}:alarm:"
    raw = {
        "MetricAlarms": [
            {"AlarmName": "a1", "AlarmArn": alarm_arn + "a1", "Namespace": "AWS/RDS",
             "Dimensions": dims("DBInstanceIdentifier", "prod-a")},
            {"AlarmName": "a2", "Namespace": "AWS/RDS", "Dimensions": dims("DBInstanceIdentifier", "other-db")},
            {"AlarmName": "a3", "Namespace": "AWS/EC2", "Dimensions": dims("InstanceId", "i-1")},
            {"AlarmName": "a4", "Namespace": "AWS/RDS", "Dimensions": dims("DBClusterIdentifier", "aur-1")},
            {"AlarmName": "a5", "Namespace": "AWS/RDS", "Dimensions": dims("DBClusterIdentifier", "prod-a")},
            {"AlarmName": "a6", "AlarmArn": "arn:aws:cloudwatch:eu-west-1:123:alarm:a6", "Namespace": "AWS/RDS",
             "Dimensions": dims("DBInstanceIdentifier", "prod-a")},
            {"AlarmName": "a7", "Namespace": "AWS/RDS",
             "Dimensions": dims("DBInstanceIdentifier", "prod-a") + dims("DBClusterIdentifier", "aur-2")},
            {"AlarmName": "a8", "Metrics": [{"Id": "m", "Expression": "SEARCH('x')"}]},
        ],
        "CompositeAlarms": [{"AlarmName": "c1", "AlarmRule": "ALARM(a2)"}],
        "Metrics": [
            {"Namespace": "AWS/RDS", "MetricName": "CPUUtilization", "Dimensions": dims("DBInstanceIdentifier", "aur-1-r")},
            {"Namespace": "AWS/RDS", "MetricName": "CPUUtilization",
             "Dimensions": dims("DBInstanceIdentifier", "other-db")},
            {"Namespace": "AWS/RDS", "MetricName": "CPUUtilization", "Dimensions": dims("EngineName", "postgres")},
        ],
    }
    out, ok = run(raw)
    assert ok
    assert ids(out["MetricAlarms"], "AlarmName") == ["a1", "a4"]
    assert out["CompositeAlarms"] == []
    assert [m["Dimensions"][0]["Value"] for m in out["Metrics"]] == ["aur-1-r"]


def test_filter_passes_through_metric_data_tags_and_orderable():
    raw = {"Label": "CPUUtilization", "Datapoints": [{"Average": 1.0}],
           "MetricDataResults": [{"Id": "m1", "Values": [1.0]}], "Messages": [],
           "TagList": [{"Key": "dbops:demo", "Value": "true"}],
           "OrderableDBInstanceOptions": [{"DBInstanceClass": "db.t3.micro"}]}
    assert run(raw) == (raw, True)


def test_filter_parses_text_content():
    body = {"DBInstances": [{"DBInstanceIdentifier": "prod-a"}, {"DBInstanceIdentifier": "other-db"}]}
    expected = ({"DBInstances": [{"DBInstanceIdentifier": "prod-a"}]}, True)
    assert run([{"type": "text", "text": json.dumps(body)}]) == expected
    assert run({"content": [{"type": "text", "text": json.dumps(body)}]}) == expected
    assert run(json.dumps(body)) == expected


def test_filter_multi_content_withheld():
    body = json.dumps({"DBInstances": []})
    assert run([{"type": "text", "text": body}, {"type": "text", "text": body}]) == WITHHELD_RESULT
    assert run([{"type": "image", "data": "x"}]) == WITHHELD_RESULT
    assert run({"content": [{"type": "text", "text": body}, {"type": "resource", "resource": {}}]}) == WITHHELD_RESULT
    assert run([]) == WITHHELD_RESULT
    assert run({"content": [{"type": "text", "text": body}], "isError": True}) == WITHHELD_RESULT


def test_filter_does_not_mutate_input():
    raw = {"DBInstances": [{"DBInstanceIdentifier": "prod-a", "ReadReplicaDBInstanceIdentifiers": ["other"]}]}
    before = json.dumps(raw)
    run(raw)
    assert json.dumps(raw) == before


def test_filter_keeps_pagination_tokens():
    out, ok = run({"DBInstances": [], "Marker": "m-1", "NextToken": "t-1"})
    assert ok and out == {"DBInstances": [], "Marker": "m-1", "NextToken": "t-1"}


@pytest.mark.parametrize("raw", [
    {"Foo": []},
    "not json",
    json.dumps([1, 2]),
    {"DBInstances": ["prod-a"]},
    {"DBInstances": {"DBInstanceIdentifier": "prod-a"}},
    {"DBInstances": [{"DBInstanceIdentifier": "prod-a"}], "DBInstance": {"DBInstanceIdentifier": "other-db"}},
    42,
    None,
])
def test_filter_unknown_shape(raw):
    assert run(raw) == WITHHELD_RESULT


def test_truncate_20kb():
    small = {"a": 1}
    assert truncate(small) == (small, False)
    big = {"rows": ["x" * 100] * 300}
    out, truncated = truncate(big)
    assert truncated is True
    assert len(out["preview"].encode()) <= 20480


CLI_BODY = {"DBInstances": [{"DBInstanceIdentifier": "prod-a", "DBInstanceArn": arn("db", "prod-a")},
                            {"DBInstanceIdentifier": "other-db", "DBInstanceArn": arn("db", "other-db")}]}
META = {"RequestId": "r-1", "HTTPStatusCode": 200}


def tool_result(text, **extra):
    return {"toolUseId": "tu-1", "status": "success", "content": [{"text": text}], **extra}


def server_text(response=None, json_key="as_json", body=CLI_BODY, **overrides):
    """awslabs.aws-api-mcp-server 1.6.1: a JSON list of CallAWSResponse, one per command."""
    inner = response if response is not None else {
        "error": None, "status_code": 200, "error_code": None, "pagination_token": None,
        json_key: json.dumps({**body, "ResponseMetadata": META})}
    item = {"cli_command": "aws rds describe-db-instances", "response": inner,
            "metadata": {"service": "rds", "operation": "DescribeDBInstances", "region_name": "us-east-1"},
            "validation_failures": None, "missing_context_failures": None, "failed_constraints": None}
    return json.dumps([{**item, **overrides}])


@pytest.mark.parametrize("result", [
    tool_result(server_text()),
    tool_result(server_text(json_key="json")),
    tool_result(server_text(), structuredContent={"result": [{"leak": "other-db"}]}, isError=False,
                metadata={"fastmcp": {}}),
    tool_result(json.dumps({"response": {"json": json.dumps(CLI_BODY), "error": None, "status_code": 200},
                            "metadata": {}})),
    tool_result(json.dumps(CLI_BODY)),
])
def test_unwrap_tool_result_forms(result):
    out, ok = run(unwrap_tool_result(result))
    assert ok and ids(out["DBInstances"], "DBInstanceIdentifier") == ["prod-a"]


@pytest.mark.parametrize("result", [
    {**tool_result(server_text()), "status": "error"},
    tool_result(server_text(), isError=True),
    {"toolUseId": "tu-1", "status": "success", "content": [{"text": "{}"}, {"text": "{}"}]},
    {"toolUseId": "tu-1", "status": "success", "content": []},
    {"toolUseId": "tu-1", "status": "success", "content": [{"json": CLI_BODY}]},
    tool_result(server_text(), surprise=1),
    tool_result("not json"),
    tool_result(json.dumps([json.loads(server_text())[0]] * 2)),  # two commands
    tool_result(server_text(error="AccessDenied")),
    tool_result(server_text(response={"error": "boom", "status_code": 400, "as_json": None})),
    tool_result(server_text(response={"error": None, "status_code": 200})),
    tool_result(server_text(response={"error": None, "status_code": 200, "json": "{}", "as_json": "{}"})),
    tool_result(server_text(validation_failures=[{"reason": "bad"}])),
    tool_result(server_text(response={"response": "alias output", "error": None})),
    tool_result(json.dumps({"response": {"json": json.dumps(CLI_BODY)}, "extra": 1})),
    "not a dict",
])
def test_unwrap_tool_result_withholds(result):
    assert run(unwrap_tool_result(result)) == WITHHELD_RESULT


def test_unwrap_drops_response_metadata_only():
    body = unwrap_tool_result(tool_result(server_text()))
    assert "ResponseMetadata" not in body and set(body) == {"DBInstances"}
