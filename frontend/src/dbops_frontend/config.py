import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    agent_api_url: str
    executor_url: str
    health_url: str
    service_token: str
    users_file: Path
    executor_simulated: bool = False


def load_settings() -> Settings:
    """Read settings from the environment on every call, so a changed env applies on the next rerun."""
    return Settings(
        agent_api_url=os.environ.get("AGENT_API_URL", "http://localhost:8000"),
        executor_url=os.environ.get("EXECUTOR_URL", "http://localhost:8000"),
        health_url=os.environ.get("HEALTH_URL", "http://localhost:8000"),
        service_token=os.environ.get("DBOPS_SERVICE_TOKEN", ""),
        users_file=Path(os.environ.get("DBOPS_USERS_FILE", "/config/users.yaml")),
        executor_simulated=os.environ.get("EXECUTOR_SIMULATED", "").strip().lower() == "true",
    )
