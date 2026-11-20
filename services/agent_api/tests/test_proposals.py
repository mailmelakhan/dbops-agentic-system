from datetime import UTC, datetime

import boto3
import pytest
from agent_api.identity import Caller, Role
from agent_api.mock_client import MockClient
from agent_api.proposals import ProposalError, Proposer, window_state
from agent_api.scope import ScopedDb, ScopeSet
from botocore.stub import Stubber
from jsonschema import Draft202012Validator
from mock_backend.app import create_app as create_mock_app
from mock_backend.clock import FakeClock
from mock_backend.routes_internal import ChangeRequestBody

NOW = datetime(2026, 11, 20, 10, 5, 30, tzinfo=UTC)  # a Friday
PRICES = {"aurora-postgresql": {"us-east-1": {"db.t4g.medium": 0.073}},
          "postgres": {"us-east-1": {"db.t4g.micro": 0.016}}}
ARN = "arn:aws:rds:us-east-1:123:"


def sdb(ident, kind="instance", engine="postgres", cls="db.t3.micro", cluster=None, members=()):
    return ScopedDb("us-east-1", ident, kind, engine, cls, "available", cluster, tuple(members),
                    f"{ARN}{'db' if kind == 'instance' else 'cluster'}:{ident}")


SCOPE = ScopeSet([
    sdb("pg-1"),
    sdb("aur-1", "cluster", "aurora-postgresql", None, members=[("aur-1-w", True), ("aur-1-r", False)]),
    sdb("aur-1-w", engine="aurora-postgresql", cls="db.r6g.large", cluster="aur-1"),
    sdb("aur-1-r", engine="aurora-postgresql", cls="db.r6g.large", cluster="aur-1"),
    sdb("both"), sdb("both", "cluster", "aurora-postgresql", None),
])


class FakeResolver:
    def current(self):
        return SCOPE


class FakeMock:
    def __init__(self):
        self.bodies = []

    def create_change_request(self, body):
        self.bodies.append(body)
        return {"id": "CR-1", **body}


@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "x")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")


@pytest.fixture
def aws():
    client = boto3.client("rds", region_name="us-east-1")
    with Stubber(client) as stub:
        yield client, stub


@pytest.fixture
def mock():
    return FakeMock()


@pytest.fixture
def proposer(aws, mock):
    return Proposer(FakeResolver(), lambda region: aws[0], mock, PRICES, clock=lambda: NOW)


PRIV = Caller("lakhan", Role.PRIVILEGED)
SAFE = Caller("geeta", Role.SAFE_WRITE)
READ = Caller("amit", Role.READ)


def test_tags_diff_from_real_tags(proposer, aws, mock):
    aws[1].add_response("list_tags_for_resource",
                        {"TagList": [{"Key": "env", "Value": "prod"}, {"Key": "owner", "Value": "a"}]},
                        {"ResourceName": f"{ARN}db:pg-1"})
    proposer.modify_tags(SAFE, "c1", "pg-1", {"owner": "b", "team": "dba"}, ["env"])
    body = mock.bodies[0]
    assert body["change_type"] == "modify_tags" and body["conversation_id"] == "c1"
    assert body["requested_by"] == "geeta"
    assert [d["field"] for d in body["diff"]] == ["tag:env", "tag:owner", "tag:team"]
    assert body["diff"][1] == {"field": "tag:owner", "before": "a", "after": "b"}
    assert body["params"]["before"] == {"env": "prod", "owner": "a"}
    assert body["params"]["after"] == {"owner": "b", "team": "dba"}
    assert body["rollback_plan"] == ["Re-apply the previous tag set"]
    assert body["maintenance_window"] == {"required": False, "window": None, "inside_now": None,
                                          "next_start": None}
    assert body["database"] == {"identifier": "pg-1", "kind": "instance", "engine": "postgres",
                                "region": "us-east-1"}


@pytest.mark.parametrize("ident,kind", [("pg-1", "instance"), ("aur-1", "cluster")])
def test_snapshot_kind(proposer, mock, ident, kind):
    proposer.create_snapshot(SAFE, None, ident)
    p = mock.bodies[0]["params"]
    assert p["snapshot_kind"] == kind
    assert p["snapshot_identifier"] == f"{ident}-dbops-202611201005"
    assert mock.bodies[0]["database"]["kind"] == kind


def test_resize_cluster_lists_members(proposer, mock):
    with pytest.raises(ProposalError) as e:
        proposer.modify_instance_class(PRIV, None, "aur-1", "db.r6g.xlarge")
    assert "aur-1 is an Aurora cluster; resize one instance:" in str(e.value)
    assert "aur-1-w (writer, db.r6g.large)" in str(e.value)
    assert "aur-1-r (reader, db.r6g.large)" in str(e.value)
    assert mock.bodies == []


def describe_instance(stub, ident, cls, window, cluster=None):
    inst = {"DBInstanceIdentifier": ident, "DBInstanceClass": cls, "PreferredMaintenanceWindow": window}
    stub.add_response("describe_db_instances", {"DBInstances": [inst]},
                      {"DBInstanceIdentifier": ident})


def test_resize_aurora_reader_impact(proposer, aws, mock):
    describe_instance(aws[1], "aur-1-r", "db.r6g.large", "sun:03:00-sun:03:30")
    describe_cluster(aws[1], "aur-1", [("aur-1-w", True), ("aur-1-r", False)])
    proposer.modify_instance_class(PRIV, "c1", "aur-1-r", "db.r6g.xlarge")
    b = mock.bodies[0]
    assert b["params"] == {"from_class": "db.r6g.large", "to_class": "db.r6g.xlarge",
                           "cluster_identifier": "aur-1"}
    assert b["impact"].startswith("Reader instance")
    assert b["maintenance_window"] == {"required": True, "window": "sun:03:00-sun:03:30",
                                       "inside_now": False, "next_start": "2026-11-22T03:00:00Z"}
    assert b["rollback_plan"] == ["Snapshot taken automatically before the change",
                                  "Modify back to db.r6g.large"]
    assert b["database"]["kind"] == "instance"


def test_resize_writer_and_plain_impact(proposer, aws, mock):
    describe_instance(aws[1], "aur-1-w", "db.r6g.large", "sun:03:00-sun:03:30")
    describe_cluster(aws[1], "aur-1", [("aur-1-w", True), ("aur-1-r", False)])
    describe_instance(aws[1], "pg-1", "db.t3.micro", "sun:03:00-sun:03:30")
    proposer.modify_instance_class(PRIV, None, "aur-1-w", "db.r6g.xlarge")
    proposer.modify_instance_class(PRIV, None, "pg-1", "db.t3.small")
    assert mock.bodies[0]["impact"] == "Writer instance: ~1–2 min failover to a reader during the change"
    assert mock.bodies[1]["impact"] == "~5–10 min downtime (Multi-AZ failover)"
    assert "cluster_identifier" not in mock.bodies[1]["params"]


def test_resize_same_class_refused(proposer, aws, mock):
    describe_instance(aws[1], "pg-1", "db.t3.micro", "sun:03:00-sun:03:30")
    with pytest.raises(ProposalError, match="pg-1 is already db.t3.micro"):
        proposer.modify_instance_class(PRIV, None, "pg-1", "db.t3.micro")
    assert mock.bodies == []


def snaps(stub, op, key, rows, params):
    stub.add_response(op, {key: rows}, params)


def orderable(stub, engine, classes):
    stub.add_response("describe_orderable_db_instance_options",
                      {"OrderableDBInstanceOptions": [{"DBInstanceClass": c, "Engine": engine} for c in classes]},
                      {"Engine": engine})


def test_restore_test_aurora(proposer, aws, mock):
    rows = [{"DBClusterSnapshotIdentifier": "old", "SnapshotCreateTime": datetime(2026, 11, 1, tzinfo=UTC),
             "Status": "available"},
            {"DBClusterSnapshotIdentifier": "new", "SnapshotCreateTime": datetime(2026, 11, 18, tzinfo=UTC),
             "Status": "available"}]
    snaps(aws[1], "describe_db_cluster_snapshots", "DBClusterSnapshots", rows, {"DBClusterIdentifier": "aur-1"})
    orderable(aws[1], "aurora-postgresql", ["db.r6g.large", "db.t4g.medium"])
    proposer.restore_test(PRIV, "c1", "aur-1")
    b = mock.bodies[0]
    assert b["database"]["kind"] == "cluster"
    d = {x["field"]: x["after"] for x in b["diff"]}
    assert d["temporary_cluster"] == "restoretest-aur-1-202611201005"
    assert d["temporary_instance"] == "restoretest-aur-1-202611201005"
    assert d["instance_class"] == "db.t4g.medium"
    assert d["source_snapshot"] == "new"
    assert d["estimated_cost_per_hour"] == "≈ $0.07/h (approx.)"


def test_restore_test_plain_instance(proposer, aws, mock):
    snaps(aws[1], "describe_db_snapshots", "DBSnapshots",
          [{"DBSnapshotIdentifier": "s1", "SnapshotCreateTime": NOW, "Status": "available"}],
          {"DBInstanceIdentifier": "pg-1"})
    orderable(aws[1], "postgres", ["db.t4g.micro", "db.t4g.medium"])
    proposer.restore_test(PRIV, None, "pg-1")
    d = {x["field"]: x["after"] for x in mock.bodies[0]["diff"]}
    assert "temporary_cluster" not in d and d["instance_class"] == "db.t4g.micro"
    assert d["estimated_cost_per_hour"] == "≈ $0.02/h (approx.)"


def test_restore_test_no_snapshot(proposer, aws, mock):
    snaps(aws[1], "describe_db_snapshots", "DBSnapshots", [], {"DBInstanceIdentifier": "pg-1"})
    with pytest.raises(ProposalError, match="pg-1 has no snapshot to restore from"):
        proposer.restore_test(PRIV, None, "pg-1")
    assert mock.bodies == []


def test_restore_unknown_price(proposer, aws, mock):
    snaps(aws[1], "describe_db_snapshots", "DBSnapshots",
          [{"DBSnapshotIdentifier": "s1", "SnapshotCreateTime": NOW, "Status": "available"}],
          {"DBInstanceIdentifier": "pg-1"})
    orderable(aws[1], "postgres", ["db.t3.micro"])
    proposer.restore_test(PRIV, None, "pg-1")
    d = {x["field"]: x["after"] for x in mock.bodies[0]["diff"]}
    assert d["estimated_cost_per_hour"] == "cost estimate unavailable"


def test_role_rechecked(proposer, mock):
    with pytest.raises(ProposalError, match=r"Your role \(SAFE-WRITE\) can't propose restore_test"):
        proposer.restore_test(SAFE, None, "pg-1")
    with pytest.raises(ProposalError, match=r"Your role \(READ\) can't propose modify_tags"):
        proposer.modify_tags(READ, None, "pg-1", {"a": "b"}, [])
    assert mock.bodies == []


def test_out_of_scope_refused(proposer, mock):
    for call in (lambda: proposer.create_snapshot(SAFE, None, "nope"),
                 lambda: proposer.modify_instance_class(PRIV, None, "nope", "db.t3.small")):
        with pytest.raises(ProposalError, match="unknown database"):
            call()
    assert mock.bodies == []


def test_ambiguous_id_refused(proposer):
    with pytest.raises(ProposalError, match="both is both an instance and a cluster; say which"):
        proposer.create_snapshot(SAFE, None, "both")


def test_window_wraps_sunday_to_monday():
    w = "sun:23:30-mon:00:30"
    sun_2345 = datetime(2026, 11, 22, 23, 45, tzinfo=UTC)
    mon_0015 = datetime(2026, 11, 23, 0, 15, tzinfo=UTC)
    mon_0100 = datetime(2026, 11, 23, 1, 0, tzinfo=UTC)
    assert window_state(w, sun_2345) == (True, "2026-11-29T23:30:00Z")
    assert window_state(w, mon_0015) == (True, "2026-11-29T23:30:00Z")
    assert window_state(w, mon_0100) == (False, "2026-11-29T23:30:00Z")


def test_window_wraps_week_end():
    w = "sat:23:00-sun:01:00"
    assert window_state(w, datetime(2026, 11, 22, 0, 30, tzinfo=UTC)) == (True, "2026-11-28T23:00:00Z")
    assert window_state(w, datetime(2026, 11, 22, 2, 0, tzinfo=UTC)) == (False, "2026-11-28T23:00:00Z")


def test_aws_error_becomes_proposal_error(proposer, aws, mock):
    aws[1].add_client_error("list_tags_for_resource", "AccessDenied", "secret detail")
    with pytest.raises(ProposalError) as e:
        proposer.modify_tags(SAFE, None, "pg-1", {"a": "b"}, [])
    assert str(e.value) == "Couldn't read tags for pg-1 from AWS"
    aws[1].add_response("describe_db_snapshots", {"DBSnapshots": [
        {"DBSnapshotIdentifier": "s1", "SnapshotCreateTime": NOW, "Status": "available"}]},
        {"DBInstanceIdentifier": "pg-1"})
    aws[1].add_client_error("describe_orderable_db_instance_options", "Throttling", "secret detail")
    with pytest.raises(ProposalError) as e:
        proposer.restore_test(PRIV, None, "pg-1")
    assert str(e.value) == "Couldn't read orderable instance classes for pg-1 from AWS"
    assert "secret" not in str(e.value) and mock.bodies == []


def test_bodies_validate(aws, users_file, contract, client_transport_cls):
    mock_app = create_mock_app(users_file, "test-token", clock=FakeClock(NOW), time_scale=1.0)
    transport = client_transport_cls(mock_app)
    try:
        real = MockClient("http://mock", "test-token", transport=transport)
        rec = FakeMock()

        class Both:
            def create_change_request(self, body):
                rec.create_change_request(body)
                return real.create_change_request(body)

        client, stub = aws
        p = Proposer(FakeResolver(), lambda region: client, Both(), PRICES, clock=lambda: NOW)
        stub.add_response("list_tags_for_resource", {"TagList": [{"Key": "env", "Value": "prod"}]},
                          {"ResourceName": f"{ARN}db:pg-1"})
        stub.add_response("describe_db_cluster_snapshots", {"DBClusterSnapshots": [
            {"DBClusterSnapshotIdentifier": "n", "SnapshotCreateTime": NOW, "Status": "available"}]},
            {"DBClusterIdentifier": "aur-1"})
        orderable(stub, "aurora-postgresql", ["db.t4g.medium"])
        describe_instance(stub, "aur-1-r", "db.r6g.large", "sun:03:00-sun:03:30")
        describe_cluster(stub, "aur-1", [("aur-1-w", True), ("aur-1-r", False)])
        crs = [p.modify_tags(SAFE, "c1", "pg-1", {"team": "dba"}, []),
               p.create_snapshot(SAFE, None, "aur-1"),
               p.restore_test(PRIV, None, "aur-1"),
               p.modify_instance_class(PRIV, None, "aur-1-r", "db.r6g.xlarge")]
        for body in rec.bodies:
            ChangeRequestBody(**body)
        for cr in crs:
            doc = contract.docs["agent-api"]
            Draft202012Validator({"$ref": "#/components/schemas/ChangeRequest",
                                  "components": doc["components"]}).validate(cr)
    finally:
        transport.close()


def describe_cluster(stub, cluster, members):
    stub.add_response("describe_db_clusters", {"DBClusters": [{"DBClusterIdentifier": cluster, "DBClusterMembers": [
        {"DBInstanceIdentifier": m, "IsClusterWriter": w} for m, w in members]}]}, {"DBClusterIdentifier": cluster})


# Final-review fix F5: the writer is tagged but its cluster isn't, so scope has no cluster members to look at.
def test_member_only_tagged_writer_impact(aws, mock):
    scope = ScopeSet([sdb("aur-2-w", engine="aurora-postgresql", cls="db.r6g.large", cluster="aur-2")])
    resolver = type("R", (), {"current": lambda self: scope})()
    proposer = Proposer(resolver, lambda region: aws[0], mock, PRICES, clock=lambda: NOW)
    describe_instance(aws[1], "aur-2-w", "db.r6g.large", "sun:03:00-sun:03:30")
    describe_cluster(aws[1], "aur-2", [("aur-2-w", True), ("aur-2-r", False)])
    proposer.modify_instance_class(PRIV, None, "aur-2-w", "db.r6g.xlarge")
    assert mock.bodies[0]["impact"].startswith("Writer instance")
    assert mock.bodies[0]["params"]["cluster_identifier"] == "aur-2"
    aws[1].assert_no_pending_responses()
