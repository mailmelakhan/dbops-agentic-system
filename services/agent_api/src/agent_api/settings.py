from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MCP_TOOL_ALLOWLIST = (
    "call_aws,aws___call_aws,search_documentation,aws___search_documentation,"
    "read_documentation,aws___read_documentation"
)


def _csv(value: str) -> tuple[str, ...]:
    return tuple(p.strip() for p in value.split(",") if p.strip())


@dataclass(frozen=True)
class Settings:
    database_url: str
    llm_model: str
    llm_api_key: str
    llm_api_base: str
    scope_tag_key: str
    scope_tag_value: str
    aws_regions: tuple[str, ...]
    aws_mcp_endpoint: str
    aws_mcp_mode: str
    mcp_tool_allowlist: tuple[str, ...]
    mock_backend_url: str
    users_file: Path
    service_token: str
    run_max_tool_calls: int
    run_timeout_s: float
    prices_file: Path


def load_settings(env: Mapping[str, str]) -> Settings:
    return Settings(
        database_url=env.get("DATABASE_URL", ""),
        # A LiteLLM model string: <provider>/<model>. The provider's key comes from its usual variable
        # (DEEPSEEK_API_KEY, ANTHROPIC_API_KEY, ...) unless LLM_API_KEY overrides it.
        llm_model=env.get("LLM_MODEL", "deepseek/deepseek-chat").strip(),
        llm_api_key=env.get("LLM_API_KEY", ""),
        llm_api_base=env.get("LLM_API_BASE", ""),
        scope_tag_key=env.get("DBOPS_SCOPE_TAG_KEY", "dbops:demo"),
        scope_tag_value=env.get("DBOPS_SCOPE_TAG_VALUE", "true"),
        aws_regions=_csv(env.get("DBOPS_AWS_REGIONS", "us-east-1")),
        aws_mcp_endpoint=env.get("AWS_MCP_ENDPOINT", "https://aws-mcp.us-east-1.api.aws/mcp"),
        aws_mcp_mode=env.get("AWS_MCP_MODE", "self-hosted").strip().lower(),
        mcp_tool_allowlist=_csv(env.get("MCP_TOOL_ALLOWLIST", DEFAULT_MCP_TOOL_ALLOWLIST)),
        mock_backend_url=env.get("MOCK_BACKEND_URL", "http://mock-backend:8000"),
        users_file=Path(env.get("DBOPS_USERS_FILE", "/config/users.yaml")),
        service_token=env.get("DBOPS_SERVICE_TOKEN", ""),
        run_max_tool_calls=int(env.get("RUN_MAX_TOOL_CALLS", "15")),
        run_timeout_s=float(env.get("RUN_TIMEOUT_S", "300")),
        prices_file=Path(env.get("DBOPS_PRICES_FILE", "/config/prices.yaml")),
    )
