def test_fleet_matches_contract_and_seed(client, contract):
    body = client("rita").get("/fleet").json()
    contract.check("health-analytics", "get", "/fleet", 200, body)
    assert len(body["databases"]) == 14
    unhealthy = sorted(d["identifier"] for d in body["databases"] if d["health_score"] < 50)
    assert unhealthy == ["legacy-reports-db", "prod-orders-db"]


def test_fleet_filters_by_env_and_engine(client):
    dbs = client("rita").get("/fleet", params={"env": "prod", "engine": "aurora-postgresql"}).json()["databases"]
    assert dbs and all(d["env"] == "prod" and d["engine"] == "aurora-postgresql" for d in dbs)


def test_summary_counts(client, contract):
    body = client("rita").get("/fleet/summary").json()
    contract.check("health-analytics", "get", "/fleet/summary", 200, body)
    assert (body["total"], body["unhealthy"], body["under_30_days_to_full"]) == (14, 2, 3)


def test_alarms_for_known_and_unknown_db(client, contract):
    body = client("rita").get("/fleet/prod-orders-db/alarms").json()
    contract.check("health-analytics", "get", "/fleet/{identifier}/alarms", 200, body)
    assert len(body) == 2
    r = client("rita").get("/fleet/nope/alarms")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_refresh_is_rate_limited_to_once_a_minute(client, clock):
    c = client("rita")
    assert c.post("/refresh").status_code == 202
    r = c.post("/refresh")
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    clock.advance(61)
    assert c.post("/refresh").status_code == 202
