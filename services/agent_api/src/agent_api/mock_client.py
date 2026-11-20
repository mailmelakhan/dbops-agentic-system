"""Client for the mock backend's internal endpoints (used until part 3 replaces the mock)."""

import logging

import httpx

from agent_api.errors import ApiProblem

log = logging.getLogger(__name__)


class MockClient:
    def __init__(self, base_url: str, service_token: str, transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(base_url=base_url, transport=transport, timeout=10.0)
        self._token = service_token

    def _headers(self, user: str) -> dict[str, str]:
        return {"X-DBOps-Service-Token": self._token, "X-DBOps-Internal": "agent-api", "X-DBOps-User": user}

    def create_change_request(self, body: dict) -> dict:
        try:
            r = self._http.post("/internal/change-requests", json=body, headers=self._headers(body["requested_by"]))
        except httpx.HTTPError as exc:
            raise ApiProblem(502, "upstream_unavailable", "mock-backend unreachable") from exc
        if r.status_code != 201:
            try:
                err = r.json()["error"]
                raise ApiProblem(r.status_code, err["code"], err["message"])
            except (ValueError, KeyError, TypeError):
                raise ApiProblem(502, "upstream_error", f"mock-backend returned {r.status_code}") from None
        return r.json()

    def audit(self, row: dict) -> None:
        try:
            r = self._http.post("/internal/audit", json=row, headers=self._headers(row["user"]))
            if r.status_code != 201:
                log.warning("audit write rejected: %s %s", r.status_code, r.text)
        except Exception:
            log.warning("audit write failed", exc_info=True)
