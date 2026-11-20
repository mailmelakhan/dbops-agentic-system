import boto3
import pytest
from agent_api.scope import ScopedDb, ScopeResolver, ScopeSet, ScopeUnavailable
from botocore.exceptions import ClientError
from botocore.stub import Stubber

TAG = [{"Key": "dbops:demo", "Value": "true"}]
NOTAG = []


@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "x")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")


def inst(ident, tags, region="us-east-1", cluster=None):
    d = {"DBInstanceIdentifier": ident, "Engine": "postgres", "DBInstanceClass": "db.t3.micro",
         "DBInstanceStatus": "available", "TagList": tags,
         "DBInstanceArn": f"arn:aws:rds:{region}:123:db:{ident}"}
    if cluster:
        d["DBClusterIdentifier"] = cluster
    return d


def clus(ident, tags, members=(), region="us-east-1"):
    return {"DBClusterIdentifier": ident, "Engine": "aurora-postgresql", "Status": "available",
            "TagList": tags, "DBClusterArn": f"arn:aws:rds:{region}:123:cluster:{ident}",
            "DBClusterMembers": [{"DBInstanceIdentifier": m, "IsClusterWriter": w} for m, w in members]}


class Env:
    def __init__(self, regions=("us-east-1",), **kw):
        self.stubbers, self.clients = {}, {}
        for r in regions:
            c = boto3.client("rds", region_name=r)
            self.clients[r] = c
            self.stubbers[r] = Stubber(c)

    def add(self, region, instances, clusters):
        s = self.stubbers[region]
        s.add_response("describe_db_instances", {"DBInstances": instances})
        s.add_response("describe_db_clusters", {"DBClusters": clusters})

    def resolver(self, regions=("us-east-1",), key="dbops:demo", value="true", **kw):
        for s in self.stubbers.values():
            s.activate()
        return ScopeResolver(lambda r: self.clients[r], regions, key, value, **kw)


def test_tagged_instance_only():
    e = Env()
    e.add("us-east-1", [inst("a", TAG), inst("b", NOTAG)], [])
    s = e.resolver().current()
    assert [d.identifier for d in s.all()] == ["a"]
    assert s.get("a").cluster is None and s.get("a").kind == "instance"
    assert not s.contains("b")


def test_tagged_cluster_brings_members():
    e = Env()
    e.add("us-east-1", [inst("c1-w", NOTAG, cluster="c1"), inst("c1-r", NOTAG, cluster="c1")],
          [clus("c1", TAG, [("c1-w", True), ("c1-r", False)])])
    s = e.resolver().current()
    assert {d.identifier for d in s.all()} == {"c1", "c1-w", "c1-r"}
    assert s.get("c1-r").cluster == "c1"
    assert s.get("c1").kind == "cluster"
    assert s.get("c1").members == (("c1-w", True), ("c1-r", False))


def test_tagged_member_alone():
    e = Env()
    e.add("us-east-1", [inst("c2-w", NOTAG, cluster="c2"), inst("c2-r", TAG, cluster="c2")],
          [clus("c2", NOTAG, [("c2-w", True), ("c2-r", False)])])
    s = e.resolver().current()
    assert [d.identifier for d in s.all()] == ["c2-r"]
    assert s.get("c2-r").cluster == "c2"
    assert not s.contains("c2")


def test_case_insensitive_and_arn():
    e = Env()
    e.add("us-east-1", [inst("a", TAG)], [clus("c1", TAG)])
    s = e.resolver().current()
    assert s.contains("A")
    assert s.contains("arn:aws:rds:us-east-1:123:db:a")
    assert s.contains("ARN:AWS:RDS:us-east-1:123:cluster:C1")
    assert s.get("arn:aws:rds:us-east-1:123:db:a").identifier == "a"
    assert s.contains("a", region="us-east-1")
    assert not s.contains("a", region="eu-west-1")
    assert not s.contains("arn:aws:rds:eu-west-1:123:db:a")


def test_tag_value_must_match():
    e = Env()
    e.add("us-east-1", [inst("a", [{"Key": "dbops:demo", "Value": "false"}])], [])
    assert e.resolver().current().all() == []
    e = Env()
    e.add("us-east-1", [inst("a", [{"Key": "team", "Value": "dba"}]), inst("b", TAG)], [])
    s = e.resolver(key="team", value="dba").current()
    assert [d.identifier for d in s.all()] == ["a"]


def test_cache_and_failure():
    now = [0.0]
    e = Env()
    e.add("us-east-1", [inst("a", TAG)], [])
    r = e.resolver(clock=lambda: now[0])
    first = r.current()
    now[0] = 59
    assert r.current() is first  # no second AWS call (stubber has no more responses)
    e.stubbers["us-east-1"].assert_no_pending_responses()
    now[0] = 61
    e.stubbers["us-east-1"].add_client_error("describe_db_instances", "Throttling")
    with pytest.raises(ScopeUnavailable, match="Couldn't read the database list from AWS"):
        r.current()
    e.add("us-east-1", [inst("a", TAG), inst("z", TAG)], [])
    assert {d.identifier for d in r.current().all()} == {"a", "z"}


def test_failure_not_cached_on_first_call():
    e = Env()
    e.stubbers["us-east-1"].add_client_error("describe_db_instances", "AccessDenied")
    r = e.resolver()
    with pytest.raises(ScopeUnavailable):
        r.current()
    e.add("us-east-1", [inst("a", TAG)], [])
    assert r.current().contains("a")


def test_botocore_error_wrapped():
    def factory(region):
        raise ClientError({"Error": {"Code": "X", "Message": "m"}}, "op")
    with pytest.raises(ScopeUnavailable):
        ScopeResolver(factory, ("us-east-1",), "k", "v").current()


def test_multi_region():
    e = Env(regions=("us-east-1", "eu-west-1"))
    e.add("us-east-1", [inst("a", TAG)], [])
    e.add("eu-west-1", [inst("b", TAG, region="eu-west-1")], [])
    s = e.resolver(regions=("us-east-1", "eu-west-1")).current()
    assert {(d.region, d.identifier) for d in s.all()} == {("us-east-1", "a"), ("eu-west-1", "b")}
    assert s.contains("b", region="eu-west-1") and not s.contains("b", region="us-east-1")


def test_scopeset_direct_construction():
    db = ScopedDb(region="us-east-1", identifier="x", kind="instance", engine="postgres",
                  instance_class=None, status="available", cluster=None, members=(),
                  arn="arn:aws:rds:us-east-1:1:db:x")
    assert ScopeSet([db]).contains("X")
