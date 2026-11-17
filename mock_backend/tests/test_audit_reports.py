def test_audit_paging_newest_first(client, contract):
    c = client("amit")
    p1 = c.get("/audit", params={"limit": 5}).json()
    contract.check("agent-api", "get", "/audit", 200, p1)
    assert len(p1["items"]) == 5 and p1["next_cursor"]
    p2 = c.get("/audit", params={"limit": 5, "cursor": p1["next_cursor"]}).json()
    rows = p1["items"] + p2["items"]
    ts = [r["ts"] for r in rows]
    assert ts == sorted(ts, reverse=True) and len({r["id"] for r in rows}) == 10


def test_audit_last_page_has_no_cursor(client):
    page = client("amit").get("/audit", params={"limit": 200}).json()
    assert page["next_cursor"] is None and len(page["items"]) >= 40


def test_bad_cursor_is_422(client):
    r = client("amit").get("/audit", params={"cursor": "nope"})
    assert (r.status_code, r.json()["error"]["code"]) == (422, "validation_error")


def test_audit_filters(client):
    rows = client("amit").get("/audit", params={"user": "geeta", "from": "2026-11-12T00:00:00Z"}).json()["items"]
    assert rows and all(r["user"] == "geeta" and r["ts"] >= "2026-11-12T00:00:00Z" for r in rows)
    rows = client("amit").get("/audit", params={"to": "2026-11-10T00:00:00Z"}).json()["items"]
    assert rows and all(r["ts"] <= "2026-11-10T00:00:00Z" for r in rows)


def test_audit_filter_by_tool_and_database(client):
    rows = client("amit").get("/audit", params={"tool": "rds.describe_db_instances",
                                                "database": "prod-orders-db"}).json()["items"]
    assert rows and all(r["tool"] == "rds.describe_db_instances" and r["database"] == "prod-orders-db"
                        for r in rows)


def test_facets(client, contract):
    body = client("amit").get("/audit/facets").json()
    contract.check("agent-api", "get", "/audit/facets", 200, body)
    assert {"lakhan", "ankit", "geeta", "amit"} <= set(body["users"])
    assert "rds.describe_db_instances" in body["tools"] and "prod-orders-db" in body["databases"]


def test_change_history_report_lifecycle(client, clock, contract):
    c = client("amit")
    r = c.post("/reports/change-history", json={"user": "geeta"})
    assert r.status_code == 202
    rid = r.json()["report_id"]
    meta = c.get(f"/reports/{rid}").json()
    contract.check("agent-api", "get", "/reports/{id}", 200, meta)
    assert (meta["status"], meta["kind"], meta["created_by"]) == ("generating", "change_history", "amit")
    r = c.get(f"/reports/{rid}/download", params={"format": "csv"})
    assert (r.status_code, r.json()["error"]["code"]) == (409, "not_ready")
    clock.advance(3.1)
    assert c.get(f"/reports/{rid}").json()["status"] == "ready"
    r = c.get(f"/reports/{rid}/download", params={"format": "csv"})
    assert r.headers["content-type"].startswith("text/csv")
    lines = r.text.splitlines()
    assert lines[0] == "ts,user,action,tool,database,result,change_request_id"
    assert len(lines) > 1 and all(",geeta," in line for line in lines[1:])
    assert c.get(f"/reports/{rid}/download", params={"format": "md"}).text.startswith("# ")


def test_fleet_health_report_listed(client, contract):
    r = client("amit").post("/reports/fleet-health", json={})
    contract.check("health-analytics", "post", "/reports/fleet-health", 202, r.json())
    reports = client("amit").get("/reports").json()
    contract.check("agent-api", "get", "/reports", 200, reports)
    assert reports[0]["id"] == r.json()["report_id"] and reports[0]["kind"] == "fleet_health"


def test_seeded_failed_report_has_error(client):
    failed = [r for r in client("amit").get("/reports").json() if r["status"] == "failed"]
    assert failed[0]["error"] == "Timed out reading CloudWatch"


def test_restore_test_links_report(client, clock):
    client("lakhan").post("/change-requests/CR-0142/approve", json={"mode": "now"})
    clock.advance(10)
    cr = client("lakhan").get("/change-requests/CR-0142").json()
    assert cr["status"] == "succeeded"
    reports = {r["id"]: r for r in client("lakhan").get("/reports").json()}
    assert reports[cr["result"]["report_id"]]["kind"] == "restore_test"
