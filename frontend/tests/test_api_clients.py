import httpx
import pytest
from dbops_frontend.api.agent import AgentApi
from dbops_frontend.api.base import ApiError, ServiceUnavailable
from dbops_frontend.api.executor import ExecutorApi
from dbops_frontend.api.health import HealthApi
from dbops_frontend.config import load_settings


def test_fleet_filter(api):
    dbs = api(HealthApi, "amit").fleet(env="prod")["databases"]
    assert dbs and all(d["env"] == "prod" for d in dbs)


def test_api_error_mapping(api):
    with pytest.raises(ApiError) as e:
        api(ExecutorApi, "ankit").approve("CR-0143", "now", override_reason="x")
    assert (e.value.status, e.value.code) == (403, "self_approval")


def test_non_json_error_maps_to_http_error():
    def handler(req):
        return httpx.Response(502, text="Bad gateway")

    client = HealthApi("http://h", "geeta", "s", client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ApiError) as e:
        client.summary()
    assert (e.value.status, e.value.code, e.value.message) == (502, "http_error", "Bad gateway")


def test_unreachable_is_service_unavailable():
    with pytest.raises(ServiceUnavailable) as e:
        HealthApi("http://127.0.0.1:9", "amit", "t", timeout=0.5).summary()
    assert e.value.service == "health-analytics"


def test_identity_headers_sent_and_none_params_dropped():
    seen = {}

    def handler(req):
        seen.update(req.headers)
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"as_of": "x", "databases": []})

    HealthApi("http://h", "geeta", "s3cret", client=httpx.Client(transport=httpx.MockTransport(handler))).fleet(
        env="prod")
    assert (seen["x-dbops-user"], seen["x-dbops-service-token"]) == ("geeta", "s3cret")
    assert seen["url"] == "http://h/fleet?env=prod"


def test_list_change_requests_multi_status(api):
    crs = api(AgentApi, "amit").list_change_requests(["scheduled", "failed_cleanup_pending"])
    assert {c["id"] for c in crs} == {"CR-0135", "CR-0136"}


def test_report_download_bytes(api):
    a = api(AgentApi, "amit")
    rid = a.start_change_history_report({})
    assert a.get_report(rid)["status"] == "ready"  # time_scale=0 → ready on next read
    assert a.download_report(rid, "csv").startswith(b"ts,user,action")


def test_conversation_and_run_round_trip(api):
    a = api(AgentApi, "geeta")
    conv = a.create_conversation()
    run = a.start_run(conv, "take a snapshot of prod-orders-db")
    page = a.get_events(run, after_seq=0)
    assert page["run_status"] == "completed"
    assert a.running_runs(conv) == []
    assert [m["role"] for m in a.get_messages(conv)] == ["user", "assistant"]
    assert conv in {c["id"] for c in a.list_conversations()}


def test_executor_actions(api):
    e = api(ExecutorApi, "geeta")
    assert e.withdraw("CR-0140") == {"status": "withdrawn"}
    assert api(ExecutorApi, "ankit").reject("CR-0141", "x") == {"status": "rejected"}
    with pytest.raises(ApiError):  # withdrawn above
        api(ExecutorApi, "lakhan").approve("CR-0140", "now")
    p = api(ExecutorApi, "lakhan")
    assert p.cancel("CR-0135", "freeze") == {"status": "scheduled"}
    assert p.retry_cleanup("CR-0136") == {"status": "failed_cleanup_pending"}


def test_health_calls(api):
    h = api(HealthApi, "amit")
    assert h.summary()["total"] == 14
    assert len(h.alarms("prod-orders-db")) == 2
    h.refresh()
    assert h.start_fleet_report().startswith("rep-")


def test_audit_calls(api):
    a = api(AgentApi, "amit")
    page = a.list_audit({"user": "geeta", "from": None}, limit=5)
    assert len(page["items"]) == 5 and all(r["user"] == "geeta" for r in page["items"])
    assert "geeta" in a.audit_facets()["users"]
    assert api(AgentApi, "geeta").propose_rollback("CR-0130").startswith("CR-")


def test_settings_read_env_each_call(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_API_URL", "http://a")
    monkeypatch.setenv("EXECUTOR_URL", "http://e")
    monkeypatch.setenv("HEALTH_URL", "http://h")
    monkeypatch.setenv("DBOPS_SERVICE_TOKEN", "t")
    monkeypatch.setenv("DBOPS_USERS_FILE", str(tmp_path / "u.yaml"))
    assert load_settings().agent_api_url == "http://a"
    monkeypatch.setenv("AGENT_API_URL", "http://b")
    assert load_settings().agent_api_url == "http://b"
