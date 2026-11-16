import os
from pathlib import Path

from fastapi import FastAPI

from mock_backend import routes_agent, routes_executor, routes_health
from mock_backend.clock import Clock, SystemClock
from mock_backend.errors import install_error_handlers
from mock_backend.identity import load_roles
from mock_backend.seed import seed_state


def create_app(users_file: Path, service_token: str, clock: Clock | None = None, time_scale: float = 1.0) -> FastAPI:
    app = FastAPI(title="DB Ops mock backend")
    app.state.clock = clock or SystemClock()
    app.state.time_scale = time_scale
    app.state.service_token = service_token
    app.state.roles = load_roles(users_file)
    app.state.mock = seed_state(app.state.clock.now())
    install_error_handlers(app)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    app.include_router(routes_health.router)
    app.include_router(routes_agent.router)
    app.include_router(routes_executor.router)
    return app


def build_from_env() -> FastAPI:
    return create_app(
        users_file=Path(os.environ.get("DBOPS_USERS_FILE", "/config/users.yaml")),
        service_token=os.environ["DBOPS_SERVICE_TOKEN"],
        time_scale=float(os.environ.get("MOCK_TIME_SCALE", "1.0")),
    )
