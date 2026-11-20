from pathlib import Path

import bcrypt
import pytest
import yaml
from agent_api.app import create_app
from agent_api.settings import load_settings
from agent_api.store import Store
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"
TOKEN = "test-token"
USERS = [("lakhan", "PRIVILEGED"), ("ankit", "PRIVILEGED"), ("geeta", "SAFE-WRITE"), ("amit", "READ")]


@pytest.fixture
def users_file(tmp_path):
    h = bcrypt.hashpw(b"pw", bcrypt.gensalt(4)).decode()
    users = [{"username": u, "display_name": u.title(), "role": r, "password_hash": h} for u, r in USERS]
    path = tmp_path / "users.yaml"
    path.write_text(yaml.safe_dump({"users": users}))
    return path


@pytest.fixture
def settings(users_file, tmp_path):
    return load_settings({"DBOPS_USERS_FILE": str(users_file), "DBOPS_SERVICE_TOKEN": TOKEN,
                          "DATABASE_URL": f"sqlite:///{tmp_path / 'agent.db'}"})


@pytest.fixture
def store(settings):
    return Store(settings.database_url)


class FakeRunner:
    def __init__(self):
        self.calls = []

    def start(self, run_id, caller, conversation_id, message):
        self.calls.append((run_id, caller.username, conversation_id, message))


@pytest.fixture
def fake_runner():
    return FakeRunner()


@pytest.fixture
def app(settings, store, fake_runner):
    return create_app(settings, store, fake_runner)


@pytest.fixture
def client(app):
    def make(username: str) -> TestClient:
        return TestClient(app, headers={"X-DBOps-User": username, "X-DBOps-Service-Token": TOKEN})

    return make


class ContractChecker:
    def __init__(self):
        self.docs = {n: yaml.safe_load((CONTRACTS / f"{n}.yaml").read_text())
                     for n in ("agent-api", "executor", "health-analytics")}

    def check(self, service: str, method: str, path: str, status: int, body) -> None:
        doc = self.docs[service]
        schema = doc["paths"][path][method]["responses"][str(status)]["content"]["application/json"]["schema"]
        Draft202012Validator({**schema, "components": doc["components"]}).validate(body)


@pytest.fixture(scope="session")
def contract():
    return ContractChecker()
