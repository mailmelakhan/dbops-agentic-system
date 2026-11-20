from typing import Protocol

from fastapi import FastAPI

from agent_api import routes_conversations
from agent_api.errors import install_error_handlers
from agent_api.identity import Caller, load_roles
from agent_api.settings import Settings
from agent_api.store import Store


class RunnerProtocol(Protocol):
    def start(self, run_id: str, caller: Caller, conversation_id: str, message: str) -> None: ...


def create_app(settings: Settings, store: Store, runner: RunnerProtocol) -> FastAPI:
    app = FastAPI(title="DB Ops agent-api")
    app.state.settings = settings
    app.state.service_token = settings.service_token
    app.state.roles = load_roles(settings.users_file)
    app.state.store = store
    app.state.runner = runner
    install_error_handlers(app)
    store.fail_running_runs("agent-api restarted")

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    app.include_router(routes_conversations.router)
    return app
