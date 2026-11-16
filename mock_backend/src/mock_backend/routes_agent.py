from fastapi import APIRouter

from mock_backend.changes import get_cr, public
from mock_backend.identity import CallerDep
from mock_backend.state import StateDep

router = APIRouter()


@router.get("/change-requests")
def list_change_requests(_: CallerDep, state: StateDep, status: str | None = None, database: str | None = None):
    wanted = {s.strip() for s in status.split(",") if s.strip()} if status else None
    with state.lock:
        crs = [public(cr) for cr in state.change_requests.values()
               if (wanted is None or cr["status"] in wanted)
               and (database is None or cr["database"]["identifier"] == database)]
    return sorted(crs, key=lambda c: (c["created_at"], c["id"]), reverse=True)


@router.get("/change-requests/{id}")
def get_change_request(id: str, _: CallerDep, state: StateDep):
    with state.lock:
        return public(get_cr(state, id))
