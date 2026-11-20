# Build context: repo root (see docker-compose.yml)
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /bin/
# The AWS MCP servers, pre-installed so the first run doesn't download them inside the MCP startup timeout.
# Versions must match MCP_PROXY_VERSION / SELF_HOSTED_VERSION in src/agent_api/mcp.py.
ENV UV_TOOL_DIR=/opt/uv-tools UV_TOOL_BIN_DIR=/opt/uv-tools/bin
ENV PATH="/opt/uv-tools/bin:${PATH}"
RUN uv tool install --no-cache awslabs.aws-api-mcp-server==1.6.1 \
 && uv tool install --no-cache mcp-proxy-for-aws-cli==2.0.0
WORKDIR /app
COPY services/agent_api/requirements.lock ./services/agent_api/requirements.lock
RUN uv pip install --system --no-cache -r services/agent_api/requirements.lock
COPY services/agent_api/ ./services/agent_api/
RUN uv pip install --system --no-cache --no-deps ./services/agent_api
COPY scripts/smoke_agent_api.py ./scripts/smoke_agent_api.py
EXPOSE 8000
CMD ["uvicorn", "agent_api.main:build_from_env", "--factory", "--host", "0.0.0.0", "--port", "8000"]
