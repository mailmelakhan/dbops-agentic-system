def test_list_by_comma_separated_status(client, contract):
    body = client("amit").get("/change-requests", params={"status": "scheduled,failed_cleanup_pending"}).json()
    assert {c["id"] for c in body} == {"CR-0135", "CR-0136"}
    for cr in body:
        contract.check("agent-api", "get", "/change-requests/{id}", 200, cr)


def test_every_seeded_cr_matches_contract(client, contract):
    body = client("amit").get("/change-requests").json()
    assert len(body) >= 14
    for cr in body:
        contract.check("agent-api", "get", "/change-requests/{id}", 200, cr)


def test_pending_seed(client):
    ids = {c["id"] for c in client("amit").get("/change-requests", params={"status": "pending"}).json()}
    assert ids == {"CR-0139", "CR-0140", "CR-0141", "CR-0142", "CR-0143", "CR-0144"}


def test_filter_by_database(client):
    crs = client("amit").get("/change-requests", params={"database": "prod-orders-db"}).json()
    assert crs and all(c["database"]["identifier"] == "prod-orders-db" for c in crs)


def test_list_is_newest_first(client):
    created = [c["created_at"] for c in client("amit").get("/change-requests").json()]
    assert created == sorted(created, reverse=True)


def test_high_cr_carries_window(client):
    cr = client("amit").get("/change-requests/CR-0143").json()
    assert cr["risk_tier"] == "HIGH" and cr["maintenance_window"]["required"] is True
    assert cr["maintenance_window"]["inside_now"] is False
    assert cr["diff"] == [{"field": "instance_class", "before": "db.r6g.large", "after": "db.r6g.xlarge"}]


def test_unknown_cr_is_404(client):
    r = client("amit").get("/change-requests/CR-9999")
    assert (r.status_code, r.json()["error"]["code"]) == (404, "not_found")
