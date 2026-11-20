import json

import pytest
from agent_api.guard import ALLOWED_OPERATIONS, check_command, step_name, target_region
from agent_api.scope import ScopedDb, ScopeSet

REGIONS = ("us-east-1",)
UNKNOWN = "unknown database"
CHAIN = "only one AWS CLI command at a time"
UNPARSEABLE = "could not parse command"


def _db(ident, kind, cluster=None, members=()):
    arn_kind = "db" if kind == "instance" else "cluster"
    return ScopedDb(region="us-east-1", identifier=ident, kind=kind, engine="postgres",
                    instance_class="db.t3.micro" if kind == "instance" else None, status="available",
                    cluster=cluster, members=members, arn=f"arn:aws:rds:us-east-1:123:{arn_kind}:{ident}")


SCOPE = ScopeSet([
    _db("prod-a", "instance"),
    _db("aur-1", "cluster", members=(("aur-1-w", True), ("aur-1-r", False))),
    _db("aur-1-w", "instance", cluster="aur-1"),
    _db("aur-1-r", "instance", cluster="aur-1"),
])


def check(command):
    return check_command(command, SCOPE, REGIONS)


def dims_json(*pairs):
    return "'" + json.dumps([{"Name": n, "Value": v} for n, v in pairs]) + "'"


def mdq(namespace="AWS/RDS", name="DBInstanceIdentifier", value="prod-a"):
    q = [{"Id": "m1", "MetricStat": {"Metric": {"Namespace": namespace, "MetricName": "CPUUtilization",
                                               "Dimensions": [{"Name": name, "Value": value}]},
                                    "Period": 300, "Stat": "Average"}}]
    return "'" + json.dumps(q) + "'"


TIMES = "--start-time 2026-11-20T00:00:00Z --end-time 2026-11-20T01:00:00Z"
GMS = f"aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name CPUUtilization {TIMES} " \
      "--period 300 --statistics Average"
GMD = f"aws cloudwatch get-metric-data {TIMES} --metric-data-queries"

ALLOWED = [
    "aws rds describe-db-instances --db-instance-identifier prod-a",
    "aws rds describe-db-instances --db-instance-identifier aur-1-r --region us-east-1",
    "aws rds describe-db-instances",
    "aws rds describe-db-clusters --db-cluster-identifier aur-1",
    "aws rds describe-db-snapshots --db-instance-identifier prod-a",
    "aws rds describe-db-snapshots --db-snapshot-identifier some-snap",
    "aws rds describe-db-cluster-snapshots --db-cluster-identifier aur-1",
    "aws rds describe-db-cluster-snapshots --db-cluster-snapshot-identifier some-cluster-snap",
    "aws rds describe-events --source-type db-instance --source-identifier prod-a --duration 1440",
    "aws rds describe-events --source-type db-cluster --source-identifier aur-1",
    "aws rds describe-events --duration 60",
    "aws rds describe-pending-maintenance-actions",
    "aws rds describe-orderable-db-instance-options --engine postgres --db-instance-class db.t3.micro",
    "aws rds list-tags-for-resource --resource-name arn:aws:rds:us-east-1:123:db:prod-a",
    "aws rds list-tags-for-resource --resource-name arn:aws:rds:us-east-1:123:cluster:aur-1",
    f"{GMS} --dimensions Name=DBInstanceIdentifier,Value=prod-a",
    f"{GMS} --dimensions Name=DBClusterIdentifier,Value=aur-1",
    f"{GMD} {mdq()}",
    "aws cloudwatch list-metrics --namespace AWS/RDS",
    "aws cloudwatch list-metrics --namespace AWS/RDS --dimensions Name=DBInstanceIdentifier,Value=prod-a",
    "aws cloudwatch describe-alarms",
    "aws rds describe-db-instances --db-instance-identifier prod-a --output json",
]


def test_allowed_operations_match_design():
    assert ALLOWED_OPERATIONS == {
        "rds": frozenset({"describe-db-instances", "describe-db-clusters", "describe-db-snapshots",
                          "describe-db-cluster-snapshots", "describe-events",
                          "describe-pending-maintenance-actions", "describe-orderable-db-instance-options",
                          "list-tags-for-resource"}),
        "cloudwatch": frozenset({"get-metric-statistics", "get-metric-data", "list-metrics", "describe-alarms"}),
    }


def test_every_allowlisted_operation_is_exercised():
    seen = {(c.split()[1], c.split()[2]) for c in ALLOWED}
    assert seen == {(s, op) for s, ops in ALLOWED_OPERATIONS.items() for op in ops}


@pytest.mark.parametrize("command", ALLOWED)
def test_guard_allows(command):
    assert check(command) is None


@pytest.mark.parametrize("command,reason", [
    ("aws rds modify-db-instance --db-instance-identifier prod-a --db-instance-class db.t3.large",
     "operation not allowed: rds modify-db-instance"),
    ("aws rds delete-db-instance --db-instance-identifier prod-a --skip-final-snapshot",
     "operation not allowed: rds delete-db-instance"),
    ("aws rds create-db-snapshot --db-instance-identifier prod-a --db-snapshot-identifier s1",
     "operation not allowed: rds create-db-snapshot"),
    ("aws rds add-tags-to-resource --resource-name arn:aws:rds:us-east-1:123:db:prod-a --tags Key=a,Value=b",
     "operation not allowed: rds add-tags-to-resource"),
    ("aws s3 ls", "operation not allowed: s3 ls"),
    ("aws sts get-caller-identity", "operation not allowed: sts get-caller-identity"),
])
def test_guard_refuses_writes(command, reason):
    assert check(command) == reason


@pytest.mark.parametrize("command", [
    ("aws rds describe-db-instances --db-instance-identifier prod-a ; aws rds delete-db-instance "
     "--db-instance-identifier prod-a"),
    "aws rds describe-db-instances;aws rds delete-db-instance --db-instance-identifier prod-a",
    "aws rds describe-db-instances | jq .",
    "aws rds describe-db-instances && rm -rf /",
    "aws rds describe-db-instances & sleep 1",
    "aws rds describe-db-instances > out.json",
    "aws rds describe-db-instances < in.json",
    "aws rds describe-db-instances --db-instance-identifier $(echo other-db)",
    "aws rds describe-db-instances --db-instance-identifier `echo other-db`",
    "aws rds describe-db-instances --db-instance-identifier prod-a aws rds delete-db-instance",
    "aws rds describe-db-instances\naws rds delete-db-instance --db-instance-identifier prod-a",
])
def test_guard_refuses_chaining(command):
    assert check(command) == CHAIN


@pytest.mark.parametrize("command,reason", [
    ("aws rds describe-db-instances --endpoint-url http://evil", "flag not allowed: --endpoint-url"),
    ("aws rds describe-db-instances --endpoint-url=http://evil", "flag not allowed: --endpoint-url"),
    ("aws rds describe-db-instances --profile other", "flag not allowed: --profile"),
    ("aws --profile other rds describe-db-instances", "flag not allowed: --profile"),
    ("aws rds describe-db-instances --cli-input-json '{}'", "flag not allowed: --cli-input-json"),
    ("aws rds describe-db-instances --cli-input-yaml x", "flag not allowed: --cli-input-yaml"),
    (f"{GMS} --dimensions file://x", "flag not allowed: file://"),
    (f"{GMS} --dimensions fileb://x", "flag not allowed: fileb://"),
    ("aws rds describe-db-instances --debug", "flag not allowed: --debug"),
    ("aws rds describe-db-instances --db-instance-id prod-a", "flag not allowed: --db-instance-id"),
    ("aws rds describe-db-snapshots --include-public", "flag not allowed: --include-public"),
    ("aws rds describe-db-instances --region eu-west-1", "region not allowed: eu-west-1"),
    ("aws rds describe-db-instances --region=eu-west-1", "region not allowed: eu-west-1"),
])
def test_guard_refuses_flags(command, reason):
    assert check(command) == reason


# Review fix: --query can relabel out-of-scope rows with an in-scope identifier (JMESPath literal).
@pytest.mark.parametrize("command", [
    ("aws rds describe-db-instances --query \"{DBInstances: DBInstances[].{DBInstanceIdentifier: 'prod-a', "
     "Endpoint: Endpoint}}\""),
    "aws rds describe-db-instances --db-instance-identifier prod-a --query 'DBInstances[] | [0]'",
    "aws rds describe-db-instances --query=DBInstances",
    "aws rds describe-db-clusters --query DBClusters",
    "aws rds describe-events --query Events",
    f"{GMS} --dimensions Name=DBInstanceIdentifier,Value=prod-a --query Datapoints",
    "aws cloudwatch describe-alarms --query MetricAlarms",
])
def test_guard_refuses_query_relabel(command):
    assert check(command) == "flag not allowed: --query"


@pytest.mark.parametrize("command,reason", [
    ("aws rds describe-db-instances --output json", None),
    ("aws rds describe-db-instances --output=json", None),
    ("aws rds describe-db-instances --output text", "flag not allowed: --output"),
    ("aws rds describe-db-instances --output table", "flag not allowed: --output"),
    ("aws rds describe-db-instances --output yaml", "flag not allowed: --output"),
    ("aws rds describe-db-instances --output yaml-stream", "flag not allowed: --output"),
    ("aws rds describe-db-instances --output json --output text", "flag not allowed: --output"),
    ("aws rds describe-db-instances --output", "flag not allowed: --output"),
    ("aws rds describe-db-instances --output json text", "flag not allowed: --output"),
])
def test_guard_output_json_only(command, reason):
    assert check(command) == reason


@pytest.mark.parametrize("command", [
    "aws rds describe-db-instances --db-instance-identifier other-db",
    "aws rds describe-db-clusters --db-cluster-identifier other-db",
    "aws rds describe-events --source-type db-instance --source-identifier other-db",
    "aws rds list-tags-for-resource --resource-name arn:aws:rds:us-east-1:1:db:other-db",
    f"{GMS} --dimensions Name=DBInstanceIdentifier,Value=other-db",
    f"{GMS} --dimensions Name=DBInstanceIdentifier,Value=prod-a Name=DBInstanceIdentifier,Value=other-db",
    f"{GMD} {mdq(value='other-db')}",
    "aws cloudwatch list-metrics --dimensions Name=DBInstanceIdentifier,Value=other-db",
    "aws rds describe-db-instances --db-instance-identifier prod-a --db-instance-identifier other-db",
])
def test_guard_out_of_scope(command):
    assert check(command) == UNKNOWN


@pytest.mark.parametrize("spelling", [
    "aws rds describe-db-instances --db-instance-identifier={id}",
    'aws rds list-tags-for-resource "--resource-name" "arn:aws:rds:us-east-1:1:db:{id}"',
    "aws rds list-tags-for-resource --resource-name=arn:aws:rds:us-east-1:1:db:{id}",
    f"{GMS} --dimensions " + "'[{{\"Name\":\"DBInstanceIdentifier\",\"Value\":\"{id}\"}}]'",
    f"{GMS} --dimensions Name=DBInstanceIdentifier,Value={{id}}",
    f"{GMS} --dimensions Value={{id}},Name=DBInstanceIdentifier",
    f"{GMS} --dimensions=Name=DBInstanceIdentifier,Value={{id}}",
    f"{GMD} " + "Id=m1,MetricStat={{Metric={{Namespace=AWS/RDS,MetricName=CPUUtilization,"
                "Dimensions=[{{Name=DBInstanceIdentifier,Value={id}}}]}},Period=300,Stat=Average}}",
])
@pytest.mark.parametrize("ident,allowed", [("PROD-A", True), ("aur-1-r", True), ("other-db", False)])
def test_guard_argument_spellings(spelling, ident, allowed):
    command = spelling.format(id=ident)
    assert check(command) == (None if allowed else UNKNOWN)


@pytest.mark.parametrize("command", [
    "aws rds describe-db-instances --db-instance-identifier 'prod-a",
    "",
    "   ",
    "rds describe-db-instances",
    "aws",
    "aws rds",
    "aws rds describe-db-instances prod-a",
    f"{GMS} --dimensions '[{{\"Name\":\"DBInstanceIdentifier\",'",
    f"{GMS} --dimensions Name=DBInstanceIdentifier,Value=prod-a,Extra",
    "aws rds describe-db-instances --db-instance-identifier",
])
def test_guard_unparseable(command):
    assert check(command) == UNPARSEABLE


# Controller ruling 1: kind and region come from the flag / ARN, never guessed.
@pytest.mark.parametrize("command,reason", [
    ("aws rds describe-db-instances --db-instance-identifier aur-1", UNKNOWN),  # cluster id as instance
    ("aws rds describe-db-clusters --db-cluster-identifier prod-a", UNKNOWN),   # instance id as cluster
    ("aws rds describe-db-clusters --db-cluster-identifier aur-1-w", UNKNOWN),  # member is not a cluster
    ("aws rds list-tags-for-resource --resource-name arn:aws:rds:us-east-1:1:db:aur-1", UNKNOWN),
    ("aws rds list-tags-for-resource --resource-name arn:aws:rds:us-east-1:1:cluster:prod-a", UNKNOWN),
    ("aws rds list-tags-for-resource --resource-name arn:aws:rds:us-east-1:1:snapshot:prod-a", UNKNOWN),
    ("aws rds list-tags-for-resource --resource-name prod-a", UNKNOWN),
    ("aws rds list-tags-for-resource", UNKNOWN),
    ("aws rds list-tags-for-resource --resource-name arn:aws:rds:us-west-2:1:db:prod-a", UNKNOWN),
    (f"{GMS} --dimensions Name=DBInstanceIdentifier,Value=aur-1", UNKNOWN),
    (f"{GMS} --dimensions Name=DBClusterIdentifier,Value=prod-a", UNKNOWN),
    (f"{GMS} --dimensions Name=DBClusterIdentifier,Value=aur-1", None),
])
def test_guard_kind_from_flag(command, reason):
    assert check(command) == reason


def test_guard_region_from_command():
    two = ScopeSet([_db("prod-a", "instance"),
                    ScopedDb(region="eu-west-1", identifier="eu-db", kind="instance", engine="postgres",
                             instance_class="db.t3.micro", status="available", cluster=None, members=(),
                             arn="arn:aws:rds:eu-west-1:123:db:eu-db")])
    regions = ("us-east-1", "eu-west-1")
    assert check_command("aws rds describe-db-instances --db-instance-identifier eu-db", two, regions) == UNKNOWN
    assert check_command("aws rds describe-db-instances --db-instance-identifier eu-db --region eu-west-1",
                         two, regions) is None
    assert check_command("aws rds describe-db-instances --db-instance-identifier prod-a --region eu-west-1",
                         two, regions) == UNKNOWN


# Controller ruling 2: --source-identifier needs --source-type db-instance or db-cluster.
@pytest.mark.parametrize("command,reason", [
    ("aws rds describe-events --source-identifier prod-a", UNKNOWN),
    ("aws rds describe-events --source-type db-snapshot --source-identifier prod-a", UNKNOWN),
    ("aws rds describe-events --source-type db-parameter-group --source-identifier default", UNKNOWN),
    ("aws rds describe-events --source-type db-cluster --source-identifier prod-a", UNKNOWN),
    ("aws rds describe-events --source-type db-instance --source-identifier aur-1", UNKNOWN),
    ("aws rds describe-events --source-type db-instance --source-identifier aur-1-w", None),
    ("aws rds describe-events --source-type db-cluster --source-identifier aur-1", None),
    ("aws rds describe-events --source-type db-instance", None),
])
def test_guard_source_type_required(command, reason):
    assert check(command) == reason


# Controller ruling 3: metric reads must be AWS/RDS and name an in-scope DB dimension.
@pytest.mark.parametrize("command,reason", [
    (GMS.replace("AWS/RDS", "AWS/EC2") + " --dimensions Name=DBInstanceIdentifier,Value=prod-a", UNKNOWN),
    (GMS.replace("--namespace AWS/RDS ", "") + " --dimensions Name=DBInstanceIdentifier,Value=prod-a", UNKNOWN),
    (GMS, UNKNOWN),
    (f"{GMS} --dimensions Name=EngineName,Value=postgres", UNKNOWN),
    (f"{GMS} --dimensions Name=DBInstanceIdentifier,Value=prod-a Name=EngineName,Value=postgres", None),
    (f"{GMS} --dimensions {dims_json(('DBInstanceIdentifier', 'prod-a'), ('DBClusterIdentifier', 'aur-1'))}",
     None),
    (f"{GMS} --dimensions {dims_json(('DBInstanceIdentifier', 'prod-a'), ('DBClusterIdentifier', 'x'))}",
     UNKNOWN),
    (f"{GMD} {mdq(namespace='AWS/EC2')}", UNKNOWN),
    (f"{GMD} {mdq(name='EngineName', value='postgres')}", UNKNOWN),
    (f"{GMD} '[{{\"Id\":\"e1\",\"Expression\":\"SEARCH(\\\"{{AWS/RDS,DBInstanceIdentifier}}\\\", \\\"Average\\\")\"}}]'",
     UNKNOWN),
    (f"{GMD} '" + json.dumps([json.loads(mdq()[1:-1])[0], {"Id": "e1", "Expression": "m1*2"}]) + "'", UNKNOWN),
    (f"{GMD} '" + json.dumps([{**json.loads(mdq()[1:-1])[0], "AccountId": "999"}]) + "'", UNKNOWN),
    (f"{GMD} '" + json.dumps([json.loads(mdq()[1:-1])[0],
                              json.loads(mdq(namespace='AWS/EC2', name='InstanceId', value='i-1')[1:-1])[0]]) + "'",
     UNKNOWN),
    (f"{GMD} '[{{\"Id\":\"m1\"'", UNPARSEABLE),
    (f"aws cloudwatch get-metric-data {TIMES}", UNKNOWN),
    (f"{GMD}", UNPARSEABLE),
    ("aws cloudwatch list-metrics --namespace AWS/EC2", None),
    ("aws cloudwatch list-metrics --dimensions Name=DBInstanceIdentifier", None),
])
def test_guard_metrics_must_be_rds(command, reason):
    assert check(command) == reason


def test_step_name():
    assert step_name(f"{GMS} --dimensions Name=DBInstanceIdentifier,Value=prod-a") == \
        "cloudwatch.get_metric_statistics"
    assert step_name("aws rds describe-db-instances --db-instance-identifier prod-a") == "rds.describe_db_instances"
    assert step_name("aws rds 'unbalanced") == "aws"
    assert step_name("") == "aws"
    assert step_name("ls -la") == "aws"


@pytest.mark.parametrize("command,region", [
    ("aws rds describe-db-instances", "us-east-1"),
    ("aws rds describe-db-instances --region eu-west-1", "eu-west-1"),
    ("aws rds describe-db-instances --region=eu-west-1", "eu-west-1"),
    ("aws rds describe-db-instances --region us-east-1 --region eu-west-1", "eu-west-1"),
])
def test_target_region_matches_guard(command, region):
    assert check_command(command, SCOPE, ("us-east-1", "eu-west-1")) is None
    assert target_region(command, ("us-east-1", "eu-west-1")) == region
