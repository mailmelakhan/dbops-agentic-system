from types import SimpleNamespace

import pytest
from agent_api import main, mcp
from agent_api.settings import load_settings
from fastapi.testclient import TestClient


def tools(*names):
    return [SimpleNamespace(tool_name=n) for n in names]


def names(ts):
    return [t.tool_name for t in ts]


def test_filter_mcp_tools():
    offered = tools("aws___call_aws", "aws___run_script", "aws___suggest_aws_commands", "aws___search_documentation")
    kept = mcp.filter_mcp_tools(offered, load_settings({}).mcp_tool_allowlist)
    assert names(kept) == ["aws___call_aws", "aws___search_documentation"]


def test_no_call_aws_is_error():
    with pytest.raises(RuntimeError, match="offered no call_aws tool"):
        mcp.require_call_aws(tools("aws___search_documentation"))


def test_server_parameters_managed_and_self_hosted():
    managed = mcp.server_parameters(load_settings({"AWS_MCP_MODE": "managed", "AWS_MCP_ENDPOINT": "https://x/mcp"}), {})
    # Final-review fix F3: the image pre-installs both servers; launch the installed binaries, never uvx.
    assert managed.command == "mcp-proxy-for-aws-cli" and managed.args == ["https://x/mcp"]
    own = mcp.server_parameters(load_settings({"AWS_MCP_MODE": "self-hosted", "DBOPS_AWS_REGIONS": "eu-west-1,us-east-1"}),
                                {"AWS_PROFILE": "demo", "PATH": "/bin"})
    assert own.command == "awslabs.aws-api-mcp-server" and own.args == []
    assert own.env["PATH"] == "/bin"
    assert own.env["READ_OPERATIONS_ONLY"] == "true" and own.env["AWS_REGION"] == "eu-west-1"
    assert own.env["AWS_PROFILE"] == "demo"


ECS_ENV = {
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "/v2/credentials/abc",
    "AWS_CONTAINER_CREDENTIALS_FULL_URI": "http://169.254.170.23/v1/credentials",
    "AWS_CONTAINER_AUTHORIZATION_TOKEN": "tok",
    "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE": "/var/run/secrets/token",
    "AWS_WEB_IDENTITY_TOKEN_FILE": "/var/run/secrets/eks/token",
    "AWS_ROLE_ARN": "arn:aws:iam::1:role/dbops-agent",
    "AWS_ROLE_SESSION_NAME": "dbops",
    "AWS_STS_REGIONAL_ENDPOINTS": "regional",
    "AWS_DEFAULT_REGION": "eu-west-1",
    "AWS_CA_BUNDLE": "/etc/ssl/ca.pem",
    "SSL_CERT_FILE": "/etc/ssl/ca.pem",
    "REQUESTS_CA_BUNDLE": "/etc/ssl/ca.pem",
    "HTTPS_PROXY": "http://proxy:3128",
    "HTTP_PROXY": "http://proxy:3128",
    "NO_PROXY": "169.254.170.2,169.254.170.23,localhost",
    "https_proxy": "http://proxy:3128",
    "http_proxy": "http://proxy:3128",
    "no_proxy": "localhost",
}


@pytest.mark.parametrize("mode", ["managed", "self-hosted"])
def test_server_parameters_pass_task_role_region_proxy_and_ca(mode):
    """ECS task roles and EKS IRSA reach the MCP server process; otherwise call_aws has no credentials on Fargate
    and falls back to the instance profile on EC2."""
    params = mcp.server_parameters(load_settings({"AWS_MCP_MODE": mode}), {**ECS_ENV, "UNRELATED_SECRET": "x",
                                                                            "DEEPSEEK_API_KEY": "k"})
    for key, value in ECS_ENV.items():
        assert params.env[key] == value, key
    assert "UNRELATED_SECRET" not in params.env and "DEEPSEEK_API_KEY" not in params.env


def test_self_hosted_region_pinned_over_environment():
    params = mcp.server_parameters(load_settings({"DBOPS_AWS_REGIONS": "us-east-1"}), {"AWS_REGION": "ap-south-1"})
    assert params.env["AWS_REGION"] == "us-east-1"  # the guard checks scope in regions[0]; the server must match


def test_mcp_client_startup_timeout(monkeypatch):
    built = {}

    class FakeClient:
        def __init__(self, transport, **kwargs):
            built.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def list_tools_sync(self, pagination_token=None):
            return tools("aws___call_aws")

    monkeypatch.setattr(mcp, "MCPClient", FakeClient)
    with mcp.aws_tools_provider(load_settings({}))() as offered:
        assert names(offered) == ["aws___call_aws"]
    assert built["startup_timeout"] == 90


def test_build_from_env(monkeypatch, users_file, tmp_path):
    import boto3
    monkeypatch.setattr(boto3, "client", lambda *a, **k: pytest.fail("boto3 client built at startup"))
    prices = tmp_path / "prices.yaml"
    prices.write_text("postgres: {}\n")
    env = {"DATABASE_URL": f"sqlite:///{tmp_path / 'a.db'}", "DEEPSEEK_API_KEY": "dummy",
           "DBOPS_USERS_FILE": str(users_file), "DBOPS_SERVICE_TOKEN": "tok", "DBOPS_PRICES_FILE": str(prices)}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with TestClient(main.build_from_env()) as client:
        assert client.get("/healthz").status_code == 200


@pytest.mark.parametrize("env_extra", [{}, {"DEEPSEEK_API_KEY": ""}])
def test_build_from_env_fails_fast_without_llm_key(monkeypatch, users_file, tmp_path, env_extra):
    prices = tmp_path / "prices.yaml"
    prices.write_text("postgres: {}\n")
    for k in ("DEEPSEEK_API_KEY", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(k, raising=False)
    env = {"DATABASE_URL": f"sqlite:///{tmp_path / 'a.db'}", "DBOPS_USERS_FILE": str(users_file),
           "DBOPS_SERVICE_TOKEN": "tok", "DBOPS_PRICES_FILE": str(prices), **env_extra}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(RuntimeError, match="LLM_MODEL deepseek/deepseek-chat needs DEEPSEEK_API_KEY"):
        main.build_from_env()


def test_build_from_env_llm_api_key_is_enough(monkeypatch, users_file, tmp_path):
    import boto3
    monkeypatch.setattr(boto3, "client", lambda *a, **k: pytest.fail("boto3 client built at startup"))
    prices = tmp_path / "prices.yaml"
    prices.write_text("postgres: {}\n")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    env = {"DATABASE_URL": f"sqlite:///{tmp_path / 'a.db'}", "LLM_API_KEY": "k", "DBOPS_USERS_FILE": str(users_file),
           "DBOPS_SERVICE_TOKEN": "tok", "DBOPS_PRICES_FILE": str(prices)}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with TestClient(main.build_from_env()) as client:
        assert client.get("/healthz").status_code == 200


@pytest.mark.parametrize(("model", "env", "error"), [
    ("deepseek/deepseek-chat", {"DEEPSEEK_API_KEY": "k"}, None),
    ("deepseek/deepseek-chat", {}, "needs DEEPSEEK_API_KEY"),
    ("anthropic/claude-sonnet-5-5", {}, "needs ANTHROPIC_API_KEY"),
    ("bedrock/anthropic.claude-sonnet-5-5", {}, None),  # AWS credential chain: not checkable here
])
def test_check_llm_credentials(monkeypatch, model, env, error):
    from agent_api.llm_check import check_llm_credentials
    for k in ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    if error is None:
        check_llm_credentials(model, "", env)
    else:
        with pytest.raises(RuntimeError, match=error):
            check_llm_credentials(model, "", env)


def test_check_llm_credentials_api_key_override():
    from agent_api.llm_check import check_llm_credentials
    check_llm_credentials("anthropic/claude-sonnet-5-5", "k", {})
