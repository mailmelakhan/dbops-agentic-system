from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import bcrypt
import yaml

# Compared against when the username is unknown, so a wrong name costs the same time as a wrong password.
_DUMMY_HASH = bcrypt.hashpw(b"dbops-dummy", bcrypt.gensalt(4))


class Role(StrEnum):
    READ = "READ"
    SAFE_WRITE = "SAFE-WRITE"
    PRIVILEGED = "PRIVILEGED"

    @property
    def rank(self) -> int:
        return {"READ": 0, "SAFE-WRITE": 1, "PRIVILEGED": 2}[self.value]


@dataclass(frozen=True)
class User:
    username: str
    display_name: str
    role: Role


def load_users(path: Path) -> dict[str, dict]:
    users = yaml.safe_load(Path(path).read_text())["users"]
    return {u["username"]: u for u in users}


def authenticate(users: dict[str, dict], username: str, password: str) -> User | None:
    record = users.get(username)
    hashed = record["password_hash"].encode() if record else _DUMMY_HASH
    try:
        ok = bcrypt.checkpw(password.encode(), hashed)
    except ValueError:  # bcrypt refuses passwords over 72 bytes
        return None
    if record is None or not ok:
        return None
    return User(record["username"], record["display_name"], Role(record["role"]))
