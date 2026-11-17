import pytest


def rb(c, cr):
    return c.post(f"/change-requests/{cr}/rollback")


def test_tag_rollback_swaps_diff(client, contract):
    r = rb(client("geeta"), "CR-0130")
    assert r.status_code == 201
    contract.check("agent-api", "post", "/change-requests/{id}/rollback", 201, r.json())
    new = client("geeta").get(f"/change-requests/{r.json()['change_request_id']}").json()
    old = client("geeta").get("/change-requests/CR-0130").json()
    assert (new["rollback_of"], new["risk_tier"], new["status"]) == ("CR-0130", "LOW", "pending")
    assert (new["change_type"], new["requested_by"], new["requested_via"]) == ("modify_tags", "geeta", "approvals")
    assert [(d["before"], d["after"]) for d in new["diff"]] == [(d["after"], d["before"]) for d in old["diff"]]
    assert rb(client("geeta"), "CR-0130").status_code == 409


def test_resize_rollback_needs_privileged(client):
    assert rb(client("geeta"), "CR-0132").json()["error"]["code"] == "forbidden_role"
    cid = rb(client("lakhan"), "CR-0132").json()["change_request_id"]
    new = client("lakhan").get(f"/change-requests/{cid}").json()
    assert new["risk_tier"] == "HIGH" and new["params"] == {"from_class": "db.t3.large", "to_class": "db.t3.medium"}
    assert new["maintenance_window"]["required"] is True


def test_snapshot_rollback_is_delete_snapshot(client):
    cid = rb(client("geeta"), "CR-0131").json()["change_request_id"]
    new = client("geeta").get(f"/change-requests/{cid}").json()
    assert (new["change_type"], new["risk_tier"]) == ("delete_snapshot", "LOW")


def test_read_user_cannot_roll_back(client):
    assert rb(client("amit"), "CR-0130").json()["error"]["code"] == "forbidden_role"


@pytest.mark.parametrize("cr", ["CR-0137", "CR-0140"])  # restore test; pending
def test_rollback_not_allowed(client, cr):
    r = rb(client("lakhan"), cr)
    assert (r.status_code, r.json()["error"]["code"]) == (409, "invalid_state")
