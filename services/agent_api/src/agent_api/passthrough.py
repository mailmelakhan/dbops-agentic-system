"""Forward change-request, audit and report endpoints to the mock backend unchanged."""

import httpx
from fastapi import APIRouter, Depends, Request, Response

from agent_api.errors import ApiProblem
from agent_api.identity import CallerDep

_FORWARD_HEADERS = ("X-DBOps-Service-Token", "X-DBOps-User")
_RETURN_HEADERS = ("Content-Type", "Content-Disposition")


async def _raw_body(request: Request) -> bytes:
    return await request.body()


def router(mock_base_url: str, transport: httpx.BaseTransport | None = None) -> APIRouter:
    http = httpx.Client(base_url=mock_base_url, transport=transport, timeout=30.0)
    r = APIRouter()

    def forward(request: Request, body: bytes) -> Response:
        headers = {h: request.headers[h] for h in _FORWARD_HEADERS if h in request.headers}
        if body and "content-type" in request.headers:
            headers["Content-Type"] = request.headers["content-type"]
        try:
            up = http.request(request.method, request.url.path, params=request.query_params.multi_items(),
                              content=body or None, headers=headers)
        except httpx.HTTPError as exc:
            raise ApiProblem(502, "upstream_unavailable", "mock-backend unreachable") from exc
        out = {h: up.headers[h] for h in _RETURN_HEADERS if h in up.headers}
        return Response(content=up.content, status_code=up.status_code, headers=out)

    def route(method: str, path: str) -> None:
        def endpoint(request: Request, _: CallerDep, body: bytes = Depends(_raw_body)) -> Response:
            return forward(request, body)

        endpoint.__name__ = f"passthrough_{method.lower()}_{path.strip('/').replace('/', '_').replace('-', '_')}"
        r.add_api_route(path, endpoint, methods=[method], include_in_schema=False)

    for method, path in [
        ("GET", "/change-requests"), ("GET", "/change-requests/{id}"), ("POST", "/change-requests/{id}/rollback"),
        ("GET", "/audit"), ("GET", "/audit/facets"),
        ("GET", "/reports"), ("GET", "/reports/{id}"), ("GET", "/reports/{id}/download"),
        ("POST", "/reports/change-history"),
    ]:
        route(method, path)
    return r
