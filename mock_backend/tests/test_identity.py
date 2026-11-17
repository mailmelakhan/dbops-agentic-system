from fastapi.testclient import TestClient


def test_missing_token_is_401(app):
    r = TestClient(app).get("/fleet", headers={"X-DBOps-User": "lakhan"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthenticated"


def test_wrong_token_is_401(app):
    r = TestClient(app).get("/fleet", headers={"X-DBOps-User": "lakhan", "X-DBOps-Service-Token": "nope"})
    assert r.status_code == 401


def test_unknown_user_is_401(app):
    r = TestClient(app).get("/fleet", headers={"X-DBOps-User": "mallory", "X-DBOps-Service-Token": "test-token"})
    assert r.status_code == 401


def test_healthz_needs_no_auth(app):
    assert TestClient(app).get("/healthz").json() == {"ok": True}
