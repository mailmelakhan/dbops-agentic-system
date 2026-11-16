import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src/dbops_frontend"


@pytest.mark.xfail(strict=True, reason="views/approvals.py uses the executor from Task 11")
def test_only_approvals_view_uses_executor():
    users = {p.relative_to(SRC).as_posix() for p in SRC.rglob("*.py")
             if re.search(r"executor_client|ExecutorApi", p.read_text())}
    assert users <= {"clients.py", "api/__init__.py", "api/executor.py", "views/approvals.py"}
    assert "views/approvals.py" in users


def test_chat_view_has_no_execute_control():
    text = (SRC / "views/chat.py").read_text()
    assert not re.search(r"approve|execute", text, re.IGNORECASE)
