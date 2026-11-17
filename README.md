# DB Ops

An agentic assistant for operating AWS RDS and Aurora databases. DBAs ask questions in chat, see fleet health at a
glance, and make a small set of AWS-level changes through an explicit approval step.

This repository currently holds **part 1 of 4** (see the [design spec](docs/superpowers/specs/2026-11-14-db-ops-frontend-design.md)):

| Path | What it is |
|---|---|
| `contracts/` | OpenAPI 3.1 contracts for `agent-api`, `executor` and `health-analytics` |
| `config/users.yaml` | The hardcoded demo users (bcrypt hashes) |
| `mock_backend/` | A FastAPI mock that implements all three contracts in memory |
| `frontend/` | The Streamlit app: Chat, Approvals, Audit & reports, with a fleet health panel on every page |

The real `agent-api`, `executor` and `health-analytics` services come in parts 2–4 and replace the mock by changing
three URLs.

## Run it with Docker Compose

```bash
cp .env.example .env        # then set DBOPS_SERVICE_TOKEN to a secret of your own
docker compose up --build
```

Open http://127.0.0.1:8501. Only the frontend publishes a port, and only on localhost.

## Run it without Docker

```bash
uv sync
# terminal 1: the mock backend
cd mock_backend && DBOPS_USERS_FILE=../config/users.yaml DBOPS_SERVICE_TOKEN=dev \
  uv run uvicorn mock_backend.app:build_from_env --factory --port 8000
# terminal 2: the frontend
cd frontend && DBOPS_USERS_FILE=../config/users.yaml DBOPS_SERVICE_TOKEN=dev \
  AGENT_API_URL=http://localhost:8000 EXECUTOR_URL=http://localhost:8000 HEALTH_URL=http://localhost:8000 \
  uv run streamlit run app.py
```

## Demo users

The demo password for every user is `dbops-demo`. Change the hashes in `config/users.yaml` with
`uv run python scripts/hash_password.py` before showing this to anyone outside your machine.

| User | Role | Use in the demo |
|---|---|---|
| `lakhan` | PRIVILEGED | approver |
| `ankit` | PRIVILEGED | DBA lead; requests resizes (shows the no-self-approval rule) |
| `geeta` | SAFE-WRITE | tags and snapshots |
| `amit` | READ | read-only viewer |

## Configuration

| Variable | Used by | Meaning |
|---|---|---|
| `AGENT_API_URL`, `EXECUTOR_URL`, `HEALTH_URL` | frontend | Base URLs of the three services (all the mock for now) |
| `DBOPS_SERVICE_TOKEN` | all | Shared secret sent as `X-DBOps-Service-Token` |
| `DBOPS_USERS_FILE` | all | Path to `users.yaml` (default `/config/users.yaml`) |
| `MOCK_TIME_SCALE` | mock | `1.0` for realistic delays, `0` to make runs and changes finish instantly |

## Mock behaviour worth knowing for a demo

- Chat messages containing `snapshot`, `tag`, `restore` or `resize` propose that change (if your role allows it);
  `fail` makes the run fail; anything else gets a read-only answer with two tool calls.
- Approving **CR-0139** always reports drift. A restore test of `legacy-reports-db` always ends with cleanup failed.
- CR-0135 stays scheduled until someone cancels it; CR-0136 is waiting for a cleanup retry.
- The fleet panel shows 6 of 14 databases needing attention; legacy-reports-db and prod-orders-db are CRITICAL.

## Tests

```bash
uv sync
uv run pytest          # contracts, mock backend and frontend (Streamlit AppTest against a live mock)
uv run ruff check .
```
