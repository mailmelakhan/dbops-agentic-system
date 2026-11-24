import pytest

INTERNAL = {"X-DBOps-Internal": "agent-api"}
DB = {"identifier": "real-db-1", "kind": "instance", "engine": "aurora-postgresql", "region": "us-east-1"}
WINDOW = {"required": True, "window": "tue:04:00-tue:04:30", "inside_now": False,
          "next_start": "2026-11-17T04:00:00Z"}


def cr_body(change_type="modify_instance_class", database=DB, **over):
    body = {"change_type": change_type, "database": database, "requested_by": "geeta", "summary": "Resize real-db-1",
            "params": {"from_class": "db.r6g.large", "to_class": "db.r6g.xlarge"},
            "diff": [{"field": "instance_class", "before": "db.r6g.large", "after": "db.r6g.xlarge"}],
            "impact": "reader instance; no failover", "rollback_plan": ["Modify back to db.r6g.large"],
            "maintenance_window": WINDOW}
    return {**body, **over}


def post_cr(c, **kw):
    return c.post("/internal/change-requests", json=cr_body(**kw), headers=INTERNAL)


def test_internal_cr_created_as_given(client, contract):
    c = client("geeta")
    r = post_cr(c)
    assert r.status_code == 201
    cr = r.json()
    assert cr["id"].startswith("CR-")
    assert (cr["risk_tier"], cr["status"]) == ("HIGH", "pending")
    assert cr["impact"] == "reader instance; no failover"
    assert cr["maintenance_window"] == WINDOW
    assert cr["rollback_plan"] == ["Modify back to db.r6g.large"]
    got = c.get(f"/change-requests/{cr['id']}")
    assert got.json() == cr
    contract.check("agent-api", "get", "/change-requests/{id}", 200, got.json())


def test_internal_requires_internal_header(client):
    c = client("geeta")
    assert c.post("/internal/change-requests", json=cr_body()).status_code == 401
    r = c.post("/internal/audit", json={"user": "geeta", "action": "x", "result": "ok"})
    assert (r.status_code, r.json()["error"]["code"]) == (401, "unauthenticated")
    c.headers["X-DBOps-Service-Token"] = "wrong"
    assert post_cr(c).status_code == 401


def test_internal_unknown_requester(client):
    r = post_cr(client("geeta"), requested_by="mallory")
    assert (r.status_code, r.json()["error"]["code"]) == (422, "validation_error")
    r = client("geeta").post("/internal/audit", json={"user": "mallory", "action": "x", "result": "ok"},
                             headers=INTERNAL)
    assert r.status_code == 422


def test_internal_audit_row(client):
    c = client("geeta")
    body = {"user": "geeta", "action": "chat.tool_call", "tool": "rds.describe_db_instances",
            "database": "real-db-1", "result": "refused"}
    assert c.post("/internal/audit", json=body, headers=INTERNAL).status_code == 201
    items = c.get("/audit?database=real-db-1").json()["items"]
    assert (items[0]["action"], items[0]["result"], items[0]["tool"]) == ("chat.tool_call", "refused", "rds.describe_db_instances")


@pytest.mark.parametrize("change_type,kind", [("modify_tags", "instance"), ("restore_test", "cluster")])
def test_executor_runs_unknown_database(client, clock, change_type, kind):
    db = {**DB, "kind": kind}
    r = post_cr(client("geeta"), change_type=change_type, database=db, params={}, diff=[],
                maintenance_window={"required": False, "window": None, "inside_now": None, "next_start": None})
    cr_id = r.json()["id"]
    c = client("lakhan")
    assert c.post(f"/change-requests/{cr_id}/approve", json={"mode": "now"}).status_code == 202
    clock.advance(30)
    assert c.get(f"/change-requests/{cr_id}").json()["status"] == "succeeded"


def test_internal_audit_ts_override(client):
    c = client("geeta")
    body = {"user": "geeta", "action": "chat.tool_call", "database": "ts-db", "result": "ok",
            "ts": "2026-11-15T09:00:00Z"}
    assert c.post("/internal/audit", json=body, headers=INTERNAL).status_code == 201
    items = c.get("/audit?database=ts-db").json()["items"]
    assert items[0]["ts"] == "2026-11-15T09:00:00Z"


def test_internal_audit_bad_ts(client):
    body = {"user": "geeta", "action": "x", "result": "ok", "ts": "yesterday"}
    r = client("geeta").post("/internal/audit", json=body, headers=INTERNAL)
    assert (r.status_code, r.json()["error"]["code"]) == (422, "validation_error")
