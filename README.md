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

## Part 2: agent-api

`agent-api` runs the chat agent (DeepSeek through the Strands SDK) against your real AWS account, read-only, and
keeps conversations in Postgres. Change requests, audit and reports are still served by the mock backend, and
approving a change does **not** touch AWS yet (Approvals shows an "Execution is simulated" banner).

1. **Tag the databases the agent may see** with `dbops:demo=true` (key and value are `DBOPS_SCOPE_TAG_KEY` and
   `DBOPS_SCOPE_TAG_VALUE`). Untagged databases are invisible to the agent. A tagged Aurora cluster brings in its members.
2. **Create a dedicated read-only principal** (IAM user or role) and attach
   [`services/agent_api/iam-policy.json`](services/agent_api/iam-policy.json) to it: `rds:Describe*`,
   `rds:ListTagsForResource` and four read-only CloudWatch actions, no write actions at all. Put its credentials in a
   profile in `~/.aws` and set `AWS_PROFILE` in `.env`. The directory is mounted read-only into the container.
3. **Data leaves your machine.** Chat messages and the AWS results the agent reads (database names, status, metrics,
   tags) are sent to DeepSeek. Only tag databases you are happy to show it.
4. Copy `.env.example` to `.env`, set `DEEPSEEK_API_KEY`, `AWS_PROFILE` and `DBOPS_SERVICE_TOKEN`, then:

   ```bash
   docker compose up --build
   ```

   The frontend is at http://localhost:8501; agent-api is only reachable inside the compose network.
5. **AWS MCP server mode** (`AWS_MCP_MODE`):
   - `self-hosted` (default, the verified path): runs `awslabs.aws-api-mcp-server@1.6.1` with `READ_OPERATIONS_ONLY=true`
     and `AWS_REGION` set to the first entry of `DBOPS_AWS_REGIONS`.
   - `managed`: the AWS MCP Server at `AWS_MCP_ENDPOINT` through `mcp-proxy-for-aws-cli@2.0.0` (SigV4 with the same
     profile). Its tool-result shape has not been verified against a real account, and the extra IAM action it
     needs to be invoked is **not** in `iam-policy.json` because it could not be confirmed from the AWS docs: check
     the "AWS MCP Server" IAM page, add that action yourself, and run the smoke script before relying on it.
   The command guard and result filter are the same in both modes. Both servers are pre-installed in the agent-api
   image (`uv tool install`), so nothing is downloaded at run time; the container still needs internet access to
   reach AWS and DeepSeek. Every AWS command is sent with an explicit `--region` (the first entry of
   `DBOPS_AWS_REGIONS` unless the command names one).
6. **Smoke script.** With the stack up, run it once against your real account before a demo:

   ```bash
   docker compose exec agent-api python /app/scripts/smoke_agent_api.py --base-url http://localhost:8000 \
     --user lakhan --question "List my databases" --propose-snapshot my-tagged-db
   ```

   It prints the run's events and exits non-zero if the run fails. It is not part of the test suite.
7. The **fleet panel is still mock data** until part 4 (health-analytics); only Chat and the change-request
   proposals come from agent-api.

Postgres tests are skipped unless you point them at a database:
`DBOPS_TEST_PG_URL=postgresql+psycopg://user:pw@host/db uv run pytest services/agent_api/tests/test_store_postgres.py`.

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
| `AGENT_API_URL`, `EXECUTOR_URL`, `HEALTH_URL` | frontend | Base URLs of the three services (agent-api and the mock) |
| `EXECUTOR_SIMULATED` | frontend | `true` shows the "Execution is simulated" banner on Approvals (compose sets it) |
| `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL`, `AWS_PROFILE`, `AWS_MCP_MODE`, `AWS_MCP_ENDPOINT`, `DBOPS_SCOPE_TAG_KEY`, `DBOPS_SCOPE_TAG_VALUE`, `DBOPS_AWS_REGIONS` | agent-api | See Part 2 and `.env.example` |
| `DBOPS_SERVICE_TOKEN` | all | Shared secret sent as `X-DBOps-Service-Token` |
| `DBOPS_USERS_FILE` | all | Path to `users.yaml` (default `/config/users.yaml`) |
| `MOCK_TIME_SCALE` | mock | `1.0` for realistic delays, `0` to make runs and changes finish instantly |

## Mock behaviour worth knowing for a demo

- Chat messages containing `snapshot`, `tag`, `restore` or `resize` propose that change (if your role allows it);
  `fail` makes the run fail; anything else gets a read-only answer with two tool calls.
- Approving **CR-0139** always reports drift. A restore test of `legacy-reports-db` always ends with cleanup failed.
- CR-0135 stays scheduled until someone cancels it; CR-0136 is waiting for a cleanup retry.
- The fleet panel shows 6 of 14 databases needing attention; legacy-reports-db and prod-orders-db are CRITICAL.
- The old Health page is gone; a bookmarked /health URL shows "page not found" and opens Chat.

## Tests

```bash
uv sync
uv run pytest          # contracts, mock backend and frontend (Streamlit AppTest against a live mock)
uv run ruff check .
```
