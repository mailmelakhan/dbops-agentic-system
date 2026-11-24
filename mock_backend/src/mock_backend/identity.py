import hmac
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import yaml
from fastapi import Depends, Request

from mock_backend.errors import ApiProblem


class Role(StrEnum):
    READ = "READ"
    SAFE_WRITE = "SAFE-WRITE"
    PRIVILEGED = "PRIVILEGED"

    @property
    def rank(self) -> int:
        return {"READ": 0, "SAFE-WRITE": 1, "PRIVILEGED": 2}[self.value]


@dataclass(frozen=True)
class Caller:
    username: str
    role: Role


def load_roles(path: Path) -> dict[str, Role]:
    users = yaml.safe_load(Path(path).read_text())["users"]
    return {u["username"]: Role(u["role"]) for u in users}


def get_caller(request: Request) -> Caller:
    """Identify the caller from the two identity headers. The role always comes from users.yaml."""
    token = request.headers.get("X-DBOps-Service-Token", "")
    username = request.headers.get("X-DBOps-User", "")
    expected = request.app.state.service_token
    if not hmac.compare_digest(token.encode(), expected.encode()):
        raise ApiProblem(401, "unauthenticated", "Missing or wrong service token")
    role = request.app.state.roles.get(username)
    if role is None:
        raise ApiProblem(401, "unauthenticated", f"Unknown user {username!r}")
    return Caller(username, role)


CallerDep = Annotated[Caller, Depends(get_caller)]


def get_internal_caller(request: Request) -> Caller:
    """Like get_caller, but also requires the agent-api marker header."""
    caller = get_caller(request)
    if request.headers.get("X-DBOps-Internal") != "agent-api":
        raise ApiProblem(401, "unauthenticated", "Missing internal caller header")
    return caller


InternalCallerDep = Annotated[Caller, Depends(get_internal_caller)]
