import socket
import threading
import time

import bcrypt
import pytest
import uvicorn
import yaml
from fastapi.testclient import TestClient
from mock_backend.app import create_app

TOKEN = "test-token"
USERS = [("priya", "PRIVILEGED"), ("arjun", "PRIVILEGED"), ("sam", "SAFE-WRITE"), ("rita", "READ")]


@pytest.fixture
def users_file(tmp_path):
    """All four demo users; every password is "pw"."""
    h = bcrypt.hashpw(b"pw", bcrypt.gensalt(4)).decode()
    users = [{"username": u, "display_name": u.title(), "role": r, "password_hash": h} for u, r in USERS]
    path = tmp_path / "users.yaml"
    path.write_text(yaml.safe_dump({"users": users}))
    return path


@pytest.fixture
def mock_client(users_file):
    app = create_app(users_file, TOKEN, time_scale=0.0)

    def make(username: str) -> TestClient:
        return TestClient(app)

    return make


@pytest.fixture
def api(mock_client):
    def make(cls, username: str):
        return cls("http://testserver", username, TOKEN, client=mock_client(username))

    return make


@pytest.fixture
def mock_server(users_file):
    """Factory: run the mock in uvicorn on a free port; returns its base URL."""
    servers = []

    def start(time_scale: float = 0.0) -> str:
        app = create_app(users_file, TOKEN, time_scale=time_scale)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("mock server did not start")
            time.sleep(0.02)
        servers.append((server, thread))
        return f"http://127.0.0.1:{port}"

    yield start
    for server, thread in servers:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.fixture
def frontend_env(monkeypatch, users_file):
    """Point the frontend's settings at one mock base URL."""

    def apply(base_url: str) -> None:
        for var in ("AGENT_API_URL", "EXECUTOR_URL", "HEALTH_URL"):
            monkeypatch.setenv(var, base_url)
        monkeypatch.setenv("DBOPS_SERVICE_TOKEN", TOKEN)
        monkeypatch.setenv("DBOPS_USERS_FILE", str(users_file))

    return apply


FRONTEND = __import__("pathlib").Path(__file__).resolve().parents[1]


def _view_script(module: str, func: str = "render", args: tuple = ()) -> None:
    import importlib

    getattr(importlib.import_module(f"dbops_frontend.views.{module}"), func)(*args)


@pytest.fixture
def view_app(mock_server, frontend_env, monkeypatch):
    """AppTest around one view's render(), logged in, against a live mock (time_scale=0 unless base_url given)."""
    from dbops_frontend.auth import Role, User
    from streamlit.testing.v1 import AppTest

    roles = dict(USERS)

    def make(module: str, *, logged_in: str, base_url: str | None = None, agent_url: str | None = None,
             health_url: str | None = None, session: dict | None = None, func: str = "render",
             args: tuple = ()) -> AppTest:
        url = base_url or mock_server()
        frontend_env(url)
        if agent_url:
            monkeypatch.setenv("AGENT_API_URL", agent_url)
        if health_url:
            monkeypatch.setenv("HEALTH_URL", health_url)
        at = AppTest.from_function(_view_script, kwargs={"module": module, "func": func, "args": args},
                                   default_timeout=15)
        at.session_state["user"] = User(logged_in, logged_in.title(), Role(roles[logged_in]))
        for key, value in (session or {}).items():
            at.session_state[key] = value
        at.base_url = url
        return at

    return make


@pytest.fixture
def shell(mock_server, frontend_env):
    """AppTest around frontend/app.py against a live mock."""
    from dbops_frontend.auth import Role, User
    from streamlit.testing.v1 import AppTest

    roles = dict(USERS)

    def make(logged_in: str | None = None) -> AppTest:
        frontend_env(mock_server())
        at = AppTest.from_file(str(FRONTEND / "app.py"), default_timeout=15)
        if logged_in:
            at.session_state["user"] = User(logged_in, logged_in.title(), Role(roles[logged_in]))
        return at

    return make
