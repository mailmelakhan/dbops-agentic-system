from concurrent.futures import ThreadPoolExecutor

from agent_api.app import create_app
from fastapi.testclient import TestClient


def _new_conv(client, contract):
    r = client.post("/conversations")
    assert r.status_code == 201
    contract.check("agent-api", "post", "/conversations", 201, r.json())
    return r.json()["id"]


def _start(client, conv_id, message="hello"):
    return client.post(f"/conversations/{conv_id}/runs", json={"message": message})


def test_create_and_list_conversations(client, contract):
    c = client("lakhan")
    _new_conv(c, contract)
    r = c.get("/conversations")
    assert r.status_code == 200
    contract.check("agent-api", "get", "/conversations", 200, r.json())
    assert [x["title"] for x in r.json()] == ["New chat"]


def test_start_run_sets_title_and_calls_runner(client, contract, fake_runner):
    c = client("lakhan")
    conv_id = _new_conv(c, contract)
    message = "x" * 70
    r = _start(c, conv_id, message)
    assert r.status_code == 201
    contract.check("agent-api", "post", "/conversations/{id}/runs", 201, r.json())
    run_id = r.json()["run_id"]
    assert fake_runner.calls == [(run_id, "lakhan", conv_id, message)]
    convs = c.get("/conversations").json()
    assert convs[0]["title"] == "x" * 60
    msgs = c.get(f"/conversations/{conv_id}/messages")
    contract.check("agent-api", "get", "/conversations/{id}/messages", 200, msgs.json())
    assert [(m["role"], m["text"]) for m in msgs.json()] == [("user", message)]
    runs = c.get("/runs", params={"conversation_id": conv_id, "status": "running"})
    contract.check("agent-api", "get", "/runs", 200, runs.json())
    assert [x["run_id"] for x in runs.json()] == [run_id]


def test_second_start_is_409(client, contract):
    c = client("lakhan")
    conv_id = _new_conv(c, contract)
    assert _start(c, conv_id).status_code == 201
    r = _start(c, conv_id)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "run_active"


def test_concurrent_start_one_wins(client, contract):
    c = client("lakhan")
    conv_id = _new_conv(c, contract)
    with ThreadPoolExecutor(2) as pool:
        statuses = sorted(f.result().status_code for f in [pool.submit(_start, c, conv_id) for _ in range(2)])
    assert statuses == [201, 409]


def test_events_after_seq(client, contract, store):
    c = client("lakhan")
    conv_id = _new_conv(c, contract)
    run_id = _start(c, conv_id).json()["run_id"]
    store.append_event(run_id, {"type": "run_started"})
    store.append_event(run_id, {"type": "text_delta", "text": "a"})
    store.append_event(run_id, {"type": "text_delta", "text": "b"})
    r = c.get(f"/runs/{run_id}/events", params={"after_seq": 1})
    assert r.status_code == 200
    contract.check("agent-api", "get", "/runs/{run_id}/events", 200, r.json())
    assert [e["seq"] for e in r.json()["events"]] == [2, 3]
    assert r.json()["run_status"] == "running"


def test_cancel_sets_flag(client, contract, store):
    c = client("lakhan")
    run_id = _start(c, _new_conv(c, contract)).json()["run_id"]
    assert not store.cancel_requested(run_id)
    r = c.post(f"/runs/{run_id}/cancel")
    assert r.status_code == 202
    contract.check("agent-api", "post", "/runs/{run_id}/cancel", 202, r.json())
    assert store.cancel_requested(run_id)


def test_other_users_get_404(client, contract):
    c, other = client("lakhan"), client("ankit")
    conv_id = _new_conv(c, contract)
    run_id = _start(c, conv_id).json()["run_id"]
    for r in (other.get(f"/conversations/{conv_id}/messages"), other.get(f"/runs/{run_id}/events"),
              other.post(f"/runs/{run_id}/cancel"), _start(other, conv_id)):
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "not_found"


def test_startup_fails_running_runs(settings, store, fake_runner, client, contract):
    c = client("lakhan")
    conv_id = _new_conv(c, contract)
    run_id = _start(c, conv_id).json()["run_id"]
    app = create_app(settings, store, fake_runner)
    c2 = TestClient(app, headers=c.headers)
    assert c2.get("/runs", params={"conversation_id": conv_id, "status": "running"}).json() == []
    assert store.get_run(run_id, "lakhan")["status"] == "failed"
    assert store.get_run(run_id, "lakhan")["error"] == "agent-api restarted"


def test_identity_required(app):
    assert TestClient(app).get("/conversations").status_code == 401
    bad = TestClient(app, headers={"X-DBOps-User": "nobody", "X-DBOps-Service-Token": "test-token"})
    assert bad.get("/conversations").status_code == 401
