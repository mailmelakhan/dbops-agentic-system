"""The AWS MCP tools for one run: a Strands MCPClient over stdio, filtered to the allowlist (design §7.2, §14)."""

import contextlib
import os
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import AbstractContextManager

from mcp import StdioServerParameters, stdio_client
from strands.tools.mcp import MCPClient

from agent_api.settings import Settings

# Pinned releases, pre-installed in the image with `uv tool install` (see services/agent_api/Dockerfile, which
# must pin the same versions); bump both together. Launched by their console-script names, never through uvx,
# so the first run doesn't download a server inside the startup timeout.
MCP_PROXY_VERSION = "2.0.0"  # mcp-proxy-for-aws-cli (the version-pinned CLI build of mcp-proxy-for-aws): SigV4 bridge to the managed AWS MCP Server
SELF_HOSTED_VERSION = "1.6.1"  # awslabs.aws-api-mcp-server: the verified fallback
MCP_PROXY_BINARY = "mcp-proxy-for-aws-cli"
SELF_HOSTED_BINARY = "awslabs.aws-api-mcp-server"
STARTUP_TIMEOUT_SECONDS = 90
CALL_AWS_NAMES = frozenset({"call_aws", "aws___call_aws"})
# Only what the MCP server needs to reach AWS; app secrets (LLM keys, the service token) are never passed.
_PASSTHROUGH_ENV = (
    "PATH", "HOME",
    # Local / compose: a profile in the mounted ~/.aws, or static keys.
    "AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "AWS_SHARED_CREDENTIALS_FILE", "AWS_CONFIG_FILE",
    # ECS task role (incl. Fargate) and EKS Pod Identity: the container credentials endpoint.
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    "AWS_CONTAINER_AUTHORIZATION_TOKEN", "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE",
    # EKS IAM roles for service accounts (web identity).
    "AWS_WEB_IDENTITY_TOKEN_FILE", "AWS_ROLE_ARN", "AWS_ROLE_SESSION_NAME", "AWS_STS_REGIONAL_ENDPOINTS",
    # Region (self-hosted mode overrides AWS_REGION with regions[0] below).
    "AWS_REGION", "AWS_DEFAULT_REGION",
    # Corporate proxy and private CA.
    "AWS_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE",
    "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy",
)


def filter_mcp_tools(tools: Iterable, allowlist: Iterable[str]) -> list:
    allowed = set(allowlist)
    return [t for t in tools if t.tool_name in allowed]


def require_call_aws(tools: list) -> list:
    if not any(t.tool_name in CALL_AWS_NAMES for t in tools):
        raise RuntimeError("AWS MCP Server offered no call_aws tool")
    return tools


def server_parameters(settings: Settings, environ: Mapping[str, str] | None = None) -> StdioServerParameters:
    environ = os.environ if environ is None else environ
    env = {k: environ[k] for k in _PASSTHROUGH_ENV if k in environ}
    if settings.aws_mcp_mode == "self-hosted":
        env["READ_OPERATIONS_ONLY"] = "true"
        env["AWS_REGION"] = settings.aws_regions[0] if settings.aws_regions else "us-east-1"
        return StdioServerParameters(command=SELF_HOSTED_BINARY, args=[], env=env)
    return StdioServerParameters(command=MCP_PROXY_BINARY, args=[settings.aws_mcp_endpoint], env=env)


def _list_all(client: MCPClient) -> list:
    tools, token = [], None
    while True:
        page = client.list_tools_sync(pagination_token=token)
        tools.extend(page)
        token = getattr(page, "pagination_token", None)
        if not token:
            return tools


def aws_tools_provider(settings: Settings) -> Callable[[], AbstractContextManager[list]]:
    @contextlib.contextmanager
    def provide() -> Iterator[list]:
        client = MCPClient(lambda: stdio_client(server_parameters(settings)), startup_timeout=STARTUP_TIMEOUT_SECONDS)
        with client:
            yield require_call_aws(filter_mcp_tools(_list_all(client), settings.mcp_tool_allowlist))

    return provide
