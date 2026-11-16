from datetime import UTC, datetime
from pathlib import Path

import bcrypt
import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from mock_backend.app import create_app
from mock_backend.clock import FakeClock

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
TOKEN = "test-token"
USERS = [("priya", "PRIVILEGED"), ("arjun", "PRIVILEGED"), ("sam", "SAFE-WRITE"), ("rita", "READ")]


@pytest.fixture
def users_file(tmp_path):
    h = bcrypt.hashpw(b"pw", bcrypt.gensalt(4)).decode()
    users = [{"username": u, "display_name": u.title(), "role": r, "password_hash": h} for u, r in USERS]
    path = tmp_path / "users.yaml"
    path.write_text(yaml.safe_dump({"users": users}))
    return path


@pytest.fixture
def clock():
    return FakeClock(datetime(2026, 11, 16, 10, 0, tzinfo=UTC))


@pytest.fixture
def app(users_file, clock):
    return create_app(users_file, TOKEN, clock=clock, time_scale=1.0)


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
