# Build context: repo root (see docker-compose.yml)
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /bin/
WORKDIR /app
COPY mock_backend/ ./mock_backend/
RUN uv pip install --system --no-cache ./mock_backend
EXPOSE 8000
CMD ["uvicorn", "mock_backend.app:build_from_env", "--factory", "--host", "0.0.0.0", "--port", "8000"]
