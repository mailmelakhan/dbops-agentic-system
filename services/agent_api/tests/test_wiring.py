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
    assert managed.command == "uvx" and managed.args == [f"mcp-proxy-for-aws-cli@{mcp.MCP_PROXY_VERSION}", "https://x/mcp"]
    own = mcp.server_parameters(load_settings({"AWS_MCP_MODE": "self-hosted", "DBOPS_AWS_REGIONS": "eu-west-1,us-east-1"}),
                                {"AWS_PROFILE": "demo", "PATH": "/bin"})
    assert own.args == [f"awslabs.aws-api-mcp-server@{mcp.SELF_HOSTED_VERSION}"]
    assert own.env["READ_OPERATIONS_ONLY"] == "true" and own.env["AWS_REGION"] == "eu-west-1"
    assert own.env["AWS_PROFILE"] == "demo"


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
