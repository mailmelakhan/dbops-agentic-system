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
