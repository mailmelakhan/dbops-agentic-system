from mock_backend.changes import new_change_request


def approve(c, cr, **body):
    return c.post(f"/change-requests/{cr}/approve", json={"mode": "now", **body})


def state_db(state, identifier):
    d = next(x for x in state.fleet if x["identifier"] == identifier)
    return {"identifier": d["identifier"], "kind": d["kind"], "engine": d["engine"], "region": d["region"]}


def test_self_approval_refused(client):
    r = approve(client("ankit"), "CR-0143", override_reason="x")
    assert (r.status_code, r.json()["error"]["code"]) == (403, "self_approval")


def test_role_from_users_file_not_header(client):
    c = client("amit")
    c.headers["X-DBOps-Role"] = "PRIVILEGED"
    assert approve(c, "CR-0140").json()["error"]["code"] == "forbidden_role"


def test_safe_write_cannot_approve_medium(client):
    assert approve(client("geeta"), "CR-0142").json()["error"]["code"] == "forbidden_role"


def test_safe_write_can_approve_low(client):
    r = approve(client("geeta"), "CR-0141")
    assert (r.status_code, r.json()) == (202, {"status": "executing"})


def test_outside_window_needs_reason_then_runs_through_steps(client, clock):
    c = client("lakhan")
    r = approve(c, "CR-0143")
    assert (r.status_code, r.json()["error"]["code"]) == (422, "window_override_reason_required")
    r = approve(c, "CR-0143", override_reason="Incident INC-77")
    assert (r.status_code, r.json()) == (202, {"status": "executing"})
    clock.advance(6.1)
    cr = c.get("/change-requests/CR-0143").json()
    assert cr["status"] == "succeeded"
    assert [p["state"] for p in cr["progress"]] == ["done", "done", "done"]
    assert cr["approval"]["override_reason"] == "Incident INC-77"
    assert cr["result"]["outcome"] == "succeeded"


def test_progress_is_partial_midway(client, clock):
    c = client("lakhan")
    approve(c, "CR-0143", override_reason="x")
    clock.advance(2.5)
    cr = c.get("/change-requests/CR-0143").json()
    assert cr["status"] == "executing"
    assert [p["state"] for p in cr["progress"]] == ["done", "active", "pending"]


def test_inside_window_needs_no_reason(client):
    assert approve(client("ankit"), "CR-0144").status_code == 202


def test_next_window_schedules(client):
    r = client("lakhan").post("/change-requests/CR-0143/approve", json={"mode": "next_window"})
    assert r.json() == {"status": "scheduled"}
    cr = client("lakhan").get("/change-requests/CR-0143").json()
    steps = [(p["step"], p["state"]) for p in cr["progress"]]
    assert steps[:2] == [("pre_snapshot", "done"), ("scheduled", "active")]


def test_drifted(client):
    assert approve(client("lakhan"), "CR-0139").json()["error"]["code"] == "drifted"


def test_not_pending_is_invalid_state(client):
    r = approve(client("lakhan"), "CR-0134")
    assert (r.status_code, r.json()["error"]["code"]) == (409, "invalid_state")


def test_reject_needs_reason(client):
    r = client("lakhan").post("/change-requests/CR-0140/reject", json={"reason": ""})
    assert (r.status_code, r.json()["error"]["code"]) == (422, "validation_error")


def test_reject(client):
    r = client("lakhan").post("/change-requests/CR-0140/reject", json={"reason": "Wrong tag"})
    assert r.json() == {"status": "rejected"}
    cr = client("lakhan").get("/change-requests/CR-0140").json()
    assert (cr["status_reason"], cr["approval"]["decision"]) == ("Wrong tag", "rejected")


def test_withdraw_only_by_requester(client):
    assert client("lakhan").post("/change-requests/CR-0140/withdraw").json()["error"]["code"] == "not_requester"
    assert client("geeta").post("/change-requests/CR-0140/withdraw").json() == {"status": "withdrawn"}


def test_cancel_scheduled(client, clock):
    assert client("geeta").post("/change-requests/CR-0135/cancel", json={"reason": "r"}).status_code == 403
    assert client("lakhan").post("/change-requests/CR-0135/cancel", json={"reason": "freeze"}).status_code == 202
    clock.advance(2.1)
    cr = client("lakhan").get("/change-requests/CR-0135").json()
    assert (cr["status"], cr["status_reason"]) == ("cancelled", "freeze")


def test_seeded_scheduled_stays_scheduled(client, clock):
    clock.advance(3600)
    assert client("lakhan").get("/change-requests/CR-0135").json()["status"] == "scheduled"


def test_retry_cleanup(client, clock):
    assert client("geeta").post("/change-requests/CR-0136/retry-cleanup").status_code == 403
    assert client("lakhan").post("/change-requests/CR-0136/retry-cleanup").status_code == 202
    clock.advance(2.1)
    cr = client("lakhan").get("/change-requests/CR-0136").json()
    assert cr["status"] == "succeeded" and cr["progress"][-1]["state"] == "done"


def test_restore_test_on_legacy_db_ends_cleanup_pending(app, client, clock):
    state = app.state.mock
    with state.lock:
        cr = new_change_request(state, change_type="restore_test", database=state_db(state, "legacy-reports-db"),
                                requested_by="ankit", params={}, diff=[], summary="t", now=clock.now())
    approve(client("lakhan"), cr["id"])
    clock.advance(10)
    got = client("lakhan").get(f"/change-requests/{cr['id']}").json()
    assert got["status"] == "failed_cleanup_pending" and got["progress"][-1]["state"] == "failed"


def test_actions_write_audit_rows(app, client):
    client("lakhan").post("/change-requests/CR-0140/reject", json={"reason": "no"})
    rows = [r for r in app.state.mock.audit if r["change_request_id"] == "CR-0140"]
    assert any(r["action"] == "change.rejected" and r["user"] == "lakhan" for r in rows)
