from datetime import datetime

from mock_backend.clock import parse_iso
from mock_backend.errors import ApiProblem

MAX_LIMIT = 200


def _matches(row: dict, user, tool, database, from_: datetime | None, to: datetime | None) -> bool:
    if user is not None and row["user"] != user:
        return False
    if tool is not None and row["tool"] != tool:
        return False
    if database is not None and row["database"] != database:
        return False
    ts = parse_iso(row["ts"])
    return (from_ is None or ts >= from_) and (to is None or ts <= to)


def _parse_bound(text: str | None, name: str) -> datetime | None:
    if text is None:
        return None
    try:
        return parse_iso(text)
    except ValueError as exc:
        raise ApiProblem(422, "validation_error", f"{name}: not an ISO-8601 timestamp") from exc


def filter_audit(rows: list[dict], *, user=None, tool=None, database=None, from_=None, to=None) -> list[dict]:
    lo, hi = _parse_bound(from_, "from"), _parse_bound(to, "to")
    matched = [r for r in rows if _matches(r, user, tool, database, lo, hi)]
    return sorted(matched, key=lambda r: (r["ts"], r["id"]), reverse=True)


def query_audit(rows: list[dict], *, user=None, tool=None, database=None, from_=None, to=None, limit: int = 50,
                cursor: str | None = None) -> dict:
    limit = max(1, min(limit, MAX_LIMIT))
    try:
        offset = int(cursor) if cursor else 0
    except ValueError as exc:
        raise ApiProblem(422, "validation_error", "cursor: invalid") from exc
    matched = filter_audit(rows, user=user, tool=tool, database=database, from_=from_, to=to)
    page = matched[offset:offset + limit]
    more = offset + limit < len(matched)
    return {"items": page, "next_cursor": str(offset + limit) if more else None}


def audit_facets(rows: list[dict]) -> dict:
    return {
        "users": sorted({r["user"] for r in rows}),
        "tools": sorted({r["tool"] for r in rows if r["tool"]}),
        "databases": sorted({r["database"] for r in rows if r["database"]}),
    }
