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
