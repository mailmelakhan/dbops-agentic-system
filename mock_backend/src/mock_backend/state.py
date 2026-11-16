import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated

from fastapi import Depends, Request


@dataclass
class MockState:
    fleet: list[dict]
    alarms: dict[str, list[dict]]
    as_of: datetime
    last_refresh: datetime | None = None
    change_requests: dict[str, dict] = field(default_factory=dict)
    next_cr_number: int = 145
    conversations: dict[str, dict] = field(default_factory=dict)
    runs: dict[str, dict] = field(default_factory=dict)
    audit: list[dict] = field(default_factory=list)
    reports: dict[str, dict] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock)


def advance(state: MockState, now: datetime, time_scale: float) -> None:
    """Bring every timed behaviour up to `now`. Called under state.lock before each request."""
    from mock_backend.changes import advance_change_requests
    from mock_backend.runs import advance_runs

    advance_runs(state, now, time_scale)
    advance_change_requests(state, now, time_scale)


def get_state(request: Request) -> MockState:
    """FastAPI dependency: advance the mock to the clock's now, then hand the state to the route."""
    app = request.app
    state: MockState = app.state.mock
    with state.lock:
        advance(state, app.state.clock.now(), app.state.time_scale)
    return state


def now_of(request: Request) -> datetime:
    return request.app.state.clock.now()


StateDep = Annotated[MockState, Depends(get_state)]
