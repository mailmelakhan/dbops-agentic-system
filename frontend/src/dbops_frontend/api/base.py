from typing import Any

import httpx


class ApiError(Exception):
    """The service answered with an error body: {"error": {"code", "message"}}."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class ServiceUnavailable(Exception):
    """The service could not be reached at all."""

    def __init__(self, service: str):
        super().__init__(f"{service} unavailable")
        self.service = service


class ServiceClient:
    service = "service"

    def __init__(self, base_url: str, username: str, service_token: str, *, client: httpx.Client | None = None,
                 timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.headers = {"X-DBOps-User": username, "X-DBOps-Service-Token": service_token}
        self._client = client
        self._timeout = timeout

    def _send(self, method: str, path: str, params: dict | None = None, json: Any = None) -> httpx.Response:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        url = self.base_url + path
        try:
            if self._client is not None:
                response = self._client.request(method, url, params=params, json=json, headers=self.headers)
            else:
                with httpx.Client(timeout=self._timeout) as client:
                    response = client.request(method, url, params=params, json=json, headers=self.headers)
        except httpx.TransportError as exc:
            raise ServiceUnavailable(self.service) from exc
        if response.is_success:
            return response
        try:
            error = response.json()["error"]
            raise ApiError(response.status_code, error["code"], error["message"])
        except (ValueError, KeyError, TypeError):
            raise ApiError(response.status_code, "http_error", response.text[:200]) from None

    def _get(self, path: str, params: dict | None = None) -> Any:
        return self._send("GET", path, params=params).json()

    def _post(self, path: str, json: Any = None) -> Any:
        return self._send("POST", path, json=json).json()

    def _get_bytes(self, path: str, params: dict | None = None) -> bytes:
        return self._send("GET", path, params=params).content
