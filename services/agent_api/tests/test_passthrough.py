from datetime import UTC, datetime

import anyio.from_thread
import httpx
import pytest
from agent_api.app import create_app
from agent_api.errors import ApiProblem
from agent_api.mock_client import MockClient
from fastapi.testclient import TestClient
from mock_backend.app import create_app as create_mock_app
from mock_backend.clock import FakeClock

TOKEN = "test-token"
MOCK = "http://mock"


class ClientTransport(httpx.BaseTransport):
    """Sync transport over an in-process ASGI app: httpx.ASGITransport driven through a blocking portal.

    (starlette's TestClient is built on a different httpx package here, so it cannot be used as a transport.)
    """

    def __init__(self, asgi_app):
        self._asgi = httpx.ASGITransport(app=asgi_app)
        self._cm = anyio.from_thread.start_blocking_portal()
        self._portal = self._cm.__enter__()

    async def _call(self, request: httpx.Request) -> httpx.Response:
        resp = await self._asgi.handle_async_request(request)
        content = await resp.aread()
        return httpx.Response(resp.status_code, headers=resp.headers, content=content)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        request.read()
        return self._portal.call(self._call, request)

    def close(self) -> None:
        self._cm.__exit__(None, None, None)


class Unreachable(httpx.BaseTransport):
    def handle_request(self, request):
        raise httpx.ConnectError("refused", request=request)


@pytest.fixture
def clock():
    return FakeClock(datetime(2026, 11, 16, 10, 0, tzinfo=UTC))


@pytest.fixture
def mock_app(users_file, clock):
    return create_mock_app(users_file, TOKEN, clock=clock, time_scale=1.0)


@pytest.fixture
def mock_transport(mock_app):
    transport = ClientTransport(mock_app)
    yield transport
    transport.close()


@pytest.fixture
def app(settings, store, fake_runner, mock_transport):
    return create_app(settings, store, fake_runner, passthrough_transport=mock_transport)


@pytest.fixture
def mock(mock_app):
    def make(username):
        return TestClient(mock_app, headers={"X-DBOps-User": username, "X-DBOps-Service-Token": TOKEN})

    return make


def test_passthrough_lists_seeded_crs(client, mock):
    via = client("amit").get("/change-requests", params={"status": "pending"})
    direct = mock("amit").get("/change-requests", params={"status": "pending"})
    assert via.status_code == 200 and via.json() == direct.json() and via.json()


def test_passthrough_download_bytes(client, mock, clock):
    c = client("amit")
    r = c.post("/reports/change-history", json={"user": "geeta"})
    assert r.status_code == 202
    rid = r.json()["report_id"]
    clock.advance(3.1)
    via = c.get(f"/reports/{rid}/download", params={"format": "csv"})
    direct = mock("amit").get(f"/reports/{rid}/download", params={"format": "csv"})
    assert via.status_code == 200 and via.content == direct.content and via.content
    assert via.headers["content-type"].startswith("text/csv")
    assert via.headers["content-disposition"] == direct.headers["content-disposition"]


def test_passthrough_error_status(client, mock):
    pending = mock("amit").get("/change-requests", params={"status": "pending"}).json()[0]["id"]
    via = client("lakhan").post(f"/change-requests/{pending}/rollback", json={})
    direct = mock("lakhan").post(f"/change-requests/{pending}/rollback", json={})
    assert via.status_code == 409 and via.json()["error"]["code"] == "invalid_state"
    assert via.json() == direct.json()


def test_passthrough_requires_caller(app):
    r = TestClient(app).get("/change-requests")
    assert r.status_code == 401


def test_passthrough_unreachable(settings, store, fake_runner):
    app = create_app(settings, store, fake_runner, passthrough_transport=Unreachable())
    c = TestClient(app, headers={"X-DBOps-User": "amit", "X-DBOps-Service-Token": TOKEN})
    r = c.get("/change-requests")
    assert r.status_code == 502
    assert r.json() == {"error": {"code": "upstream_unavailable", "message": "mock-backend unreachable"}}


def _cr_body():
    return {
        "change_type": "modify_tags",
        "database": {"identifier": "prod-orders-db", "kind": "instance", "engine": "postgres",
                     "region": "us-east-1"},
        "requested_by": "geeta", "summary": "Tag it", "params": {"tags": {"a": "b"}},
        "diff": [{"field": "tags.a", "before": None, "after": "b"}], "impact": "None",
        "rollback_plan": ["Remove tag"], "maintenance_window": {"mode": "now"},
    }


def test_mock_client_creates_cr(client, mock_transport):
    mc = MockClient(MOCK, TOKEN, transport=mock_transport)
    cr = mc.create_change_request(_cr_body())
    assert cr["id"]
    got = client("amit").get(f"/change-requests/{cr['id']}")
    assert got.status_code == 200 and got.json()["requested_by"] == "geeta"


def test_mock_client_create_cr_failure_raises(mock_transport):
    mc = MockClient(MOCK, TOKEN, transport=mock_transport)
    with pytest.raises(ApiProblem):
        mc.create_change_request({**_cr_body(), "requested_by": "nobody"})


def test_mock_client_audit_posts_row(mock_transport, mock):
    mc = MockClient(MOCK, TOKEN, transport=mock_transport)
    mc.audit({"user": "geeta", "action": "tool_call", "result": "ok", "tool": "x.y", "database": "prod-orders-db"})
    rows = mock("amit").get("/audit", params={"tool": "x.y"}).json()["items"]
    assert len(rows) == 1 and rows[0]["user"] == "geeta"


def test_mock_client_audit_swallows_errors():
    mc = MockClient(MOCK, TOKEN, transport=Unreachable())
    assert mc.audit({"user": "geeta", "action": "a", "result": "ok"}) is None
