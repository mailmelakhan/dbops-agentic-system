import json

from mock_backend.runs import truncate_result


def start(c, text):
    r = c.post("/conversations")
    assert r.status_code == 201
    conv = r.json()["id"]
    r = c.post(f"/conversations/{conv}/runs", json={"message": text})
    assert r.status_code == 201
    return conv, r.json()["run_id"]


def events(c, run_id, after=0):
    return c.get(f"/runs/{run_id}/events", params={"after_seq": after}).json()


def test_run_plays_back_contiguous_events(client, clock, contract):
    c = client("priya")
    _, run = start(c, "how is prod-orders-db?")
    assert [e["type"] for e in events(c, run)["events"]] == ["run_started"]
    clock.advance(30)
    page = events(c, run)
    contract.check("agent-api", "get", "/runs/{run_id}/events", 200, page)
    assert page["run_status"] == "completed"
    assert [e["seq"] for e in page["events"]] == list(range(1, len(page["events"]) + 1))
    assert page["events"][-1]["type"] == "run_completed"
    types = [e["type"] for e in page["events"]]
    assert "tool_call_started" in types and "tool_call_finished" in types and "text_delta" in types


def test_after_seq_returns_only_newer(client, clock):
    c = client("priya")
    _, run = start(c, "hi")
    clock.advance(30)
    newer = events(c, run, after=3)["events"]
    assert newer and all(e["seq"] > 3 for e in newer)


def test_second_run_while_active_is_409(client):
    c = client("priya")
    conv, _ = start(c, "hi")
    r = c.post(f"/conversations/{conv}/runs", json={"message": "again"})
    assert (r.status_code, r.json()["error"]["code"]) == (409, "run_active")


def test_snapshot_proposal_creates_pending_cr(client, clock):
    c = client("sam")
    conv, run = start(c, "take a snapshot of prod-orders-db")
    clock.advance(30)
    prop = next(e for e in events(c, run)["events"] if e["type"] == "change_proposed")
    assert (prop["change_type"], prop["risk_tier"]) == ("create_snapshot", "LOW")
    cr = c.get(f"/change-requests/{prop['change_request_id']}").json()
    assert (cr["status"], cr["requested_by"], cr["requested_via"], cr["conversation_id"]) == (
        "pending", "sam", "chat", conv)


def test_read_user_cannot_propose(client, clock):
    c = client("rita")
    _, run = start(c, "resize prod-orders-db")
    clock.advance(30)
    evs = events(c, run)["events"]
    assert not [e for e in evs if e["type"] == "change_proposed"]
    assert "can't propose" in "".join(e.get("text", "") for e in evs)


def test_safe_write_cannot_propose_resize(client, clock):
    c = client("sam")
    _, run = start(c, "resize prod-orders-db")
    clock.advance(30)
    assert not [e for e in events(c, run)["events"] if e["type"] == "change_proposed"]


def test_cancel_mid_run(client, clock):
    c = client("priya")
    _, run = start(c, "how is the fleet?")
    clock.advance(0.5)
    assert c.post(f"/runs/{run}/cancel").status_code == 202
    clock.advance(30)
    page = events(c, run)
    assert page["run_status"] == "cancelled" and page["events"][-1]["type"] == "run_cancelled"
    assert [e["type"] for e in page["events"]].count("run_cancelled") == 1


def test_failed_run(client, clock):
    c = client("priya")
    _, run = start(c, "please fail")
    clock.advance(30)
    page = events(c, run)
    assert page["run_status"] == "failed" and page["events"][-1]["error"] == "Bedrock throttled the request"


def test_messages_and_title_after_completion(client, clock, contract):
    c = client("sam")
    conv, run = start(c, "take a snapshot of prod-orders-db")
    clock.advance(30)
    events(c, run)
    msgs = c.get(f"/conversations/{conv}/messages").json()
    contract.check("agent-api", "get", "/conversations/{id}/messages", 200, msgs)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["text"] == "take a snapshot of prod-orders-db"
    assert msgs[1]["run_id"] == run and len(msgs[1]["change_request_ids"]) == 1 and msgs[1]["tool_steps"]
    titles = {x["id"]: x["title"] for x in c.get("/conversations").json()}
    assert titles[conv] == "take a snapshot of prod-orders-db"


def test_new_conversation_title(client, contract):
    c = client("sam")
    conv = c.post("/conversations").json()["id"]
    body = c.get("/conversations").json()
    contract.check("agent-api", "get", "/conversations", 200, body)
    assert {x["id"]: x["title"] for x in body}[conv] == "New chat"


def test_running_run_lookup(client, clock, contract):
    c = client("priya")
    conv, run = start(c, "hi")
    body = c.get("/runs", params={"conversation_id": conv, "status": "running"}).json()
    contract.check("agent-api", "get", "/runs", 200, body)
    assert [r["run_id"] for r in body] == [run]
    clock.advance(30)
    assert c.get("/runs", params={"conversation_id": conv, "status": "running"}).json() == []


def test_conversations_are_per_user(client):
    conv, run = start(client("sam"), "hi")
    assert conv not in {x["id"] for x in client("priya").get("/conversations").json()}
    assert client("priya").get(f"/conversations/{conv}/messages").status_code == 404
    assert client("priya").get(f"/runs/{run}/events").status_code == 404


def test_tool_calls_are_audited(app, client, clock):
    c = client("priya")
    _, run = start(c, "how is prod-orders-db?")
    clock.advance(30)
    events(c, run)
    assert any(r["action"] == "chat.tool_call" and r["tool"] == "rds.describe_db_instances"
               for r in app.state.mock.audit)


def test_truncate_result():
    out, truncated = truncate_result({"rows": "x" * 30_000})
    assert truncated and len(json.dumps(out)) < 21_000
    assert truncate_result({"a": 1}) == ({"a": 1}, False)


def test_keywords_match_whole_words(client, clock):
    c = client("sam")
    _, run = start(c, "what's the failover status and storage percentage?")
    clock.advance(30)
    page = events(c, run)
    assert page["run_status"] == "completed"
    assert not [e for e in page["events"] if e["type"] == "change_proposed"]


def test_plural_keywords_still_match(client, clock):
    c = client("sam")
    _, run = start(c, "please update the tags on prod-orders-db")
    clock.advance(30)
    assert [e for e in events(c, run)["events"] if e["type"] == "change_proposed"]
