# Build context: repo root (see docker-compose.yml)
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /bin/
WORKDIR /app
COPY frontend/requirements.lock ./frontend/requirements.lock
RUN uv pip install --system --no-cache -r frontend/requirements.lock
COPY frontend/ ./frontend/
RUN uv pip install --system --no-cache --no-deps ./frontend
WORKDIR /app/frontend
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.port", "8501", "--server.address", "0.0.0.0", "--server.headless", "true"]
