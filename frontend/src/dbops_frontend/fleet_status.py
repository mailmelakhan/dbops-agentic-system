"""Fleet panel status: CRITICAL / WARNING / OK per database, a one-line reason, and worst-first order."""

from enum import StrEnum

from dbops_frontend.format import Band, days_band, score_band


class Status(StrEnum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    OK = "OK"


_RANK = {Status.CRITICAL: 0, Status.WARNING: 1, Status.OK: 2}
_BAND_STATUS = {Band.RED: Status.CRITICAL, Band.AMBER: Status.WARNING}


def db_status(db: dict) -> Status:
    bands = (score_band(db["health_score"]), days_band(db["storage"]["days_to_full"]))
    statuses = [_BAND_STATUS[b] for b in bands if b in _BAND_STATUS]
    if db["open_alarms"] > 0:
        statuses.append(Status.WARNING)
    return min(statuses, key=_RANK.__getitem__, default=Status.OK)


def _storage_text(days: int) -> str:
    if days == 0:
        return "Storage full"
    return f"Storage full in {days} day{'' if days == 1 else 's'}"


def status_reason(db: dict) -> str:
    """The causes behind the status, red before amber; with no cause, just the score."""
    score, days, alarms = db["health_score"], db["storage"]["days_to_full"], db["open_alarms"]
    causes: list[tuple[Band, str]] = []
    if days_band(days) in _BAND_STATUS:
        causes.append((days_band(days), _storage_text(days)))
    if score_band(score) in _BAND_STATUS:
        causes.append((score_band(score), f"Score {score}"))
    if alarms > 0:
        causes.append((Band.AMBER, f"{alarms} alarm{'' if alarms == 1 else 's'}"))
    if not causes:
        return f"Score {score}"
    causes.sort(key=lambda c: c[0] is not Band.RED)
    return " · ".join(text for _, text in causes)


def sort_fleet(dbs: list[dict]) -> list[dict]:
    return sorted(dbs, key=lambda d: (_RANK[db_status(d)], d["health_score"], d["identifier"]))


def attention_count(dbs: list[dict]) -> int:
    return sum(db_status(d) is not Status.OK for d in dbs)


def worst_status(dbs: list[dict]) -> Status | None:
    return min((db_status(d) for d in dbs), key=_RANK.__getitem__, default=None)
