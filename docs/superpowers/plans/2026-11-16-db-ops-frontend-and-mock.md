# DB Ops Part 1: Contracts, Mock Backend and Streamlit Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the three OpenAPI contracts, a FastAPI mock backend that implements all of them, and the four-page Streamlit frontend (Health, Chat, Approvals, Audit & reports) running against the mock in Docker Compose.

**Architecture:** A uv workspace with two members, `mock_backend` and `frontend`. The mock keeps all state in memory and advances it **lazily** on each request from an injectable clock (no background threads), so every timed behaviour (run playback, CR progress, report generation) is deterministic under test with `FakeClock` or `time_scale=0`. The frontend has a thin `httpx` client per service, pure modules for formatting, permissions and chat-event folding, and one view module per page that `st.navigation` mounts as callables.

**Tech Stack:** Python 3.12, uv, Streamlit ≥1.50, httpx, pandas, bcrypt, PyYAML, FastAPI, uvicorn, pytest, ruff, openapi-spec-validator, jsonschema, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-11-14-db-ops-frontend-design.md`

## Global Constraints

- Python 3.12. `streamlit>=1.50,<2` (needs `st.navigation`, `st.fragment(run_every=...)`, theme `headingFont` and `fontFaces`).
- Frontend runtime deps are exactly: `streamlit`, `httpx`, `pandas`, `bcrypt`, `PyYAML`.
- Every request carries `X-DBOps-User: <username>` and `X-DBOps-Service-Token: <secret>`. Services look up the role in their own `users.yaml` and never trust a role sent by the caller.
- Env vars: `AGENT_API_URL`, `EXECUTOR_URL`, `HEALTH_URL`, `DBOPS_SERVICE_TOKEN`, `DBOPS_USERS_FILE` (default `/config/users.yaml`), mock only: `MOCK_TIME_SCALE` (default `1.0`).
- Errors are `{"error": {"code": str, "message": str}}`. Timestamps are ISO-8601 UTC with a `Z` suffix.
- Role strings: `READ`, `SAFE-WRITE`, `PRIVILEGED`. Risk tiers: `modify_tags` LOW, `create_snapshot` LOW, `delete_snapshot` LOW, `restore_test` MEDIUM, `modify_instance_class` HIGH.
- Colours: status green `#22c55e`, amber `#f59e0b`, red `#ef4444`. Risk badges are solid fills: LOW `#16a34a` with white text, MEDIUM `#f59e0b` with dark text (`#111827`), HIGH `#dc2626` with white text. Diff: before red, after green.
- Never use colour alone: every coloured status also has a word or icon (`HIGH`, `⚠ Outside window`, `✅`, `⏳`, `⛔`).
- Clickable things are Streamlit built-in controls only. Never style text to look like a button. Raw HTML (`unsafe_allow_html`) is allowed only for non-interactive badges and diffs, and every interpolated value is `html.escape`d.
- Fonts: Space Grotesk for headings, Inter for body text, JetBrains Mono for code, diffs and tool arguments, set as theme fonts in `frontend/.streamlit/config.toml`.
- The frontend never talks to Postgres or AWS. **Only the Approvals view calls the executor.** Chat has no execute control.
- Only `frontend` publishes a host port (8501).
- Polling: chat 0.5 s, Approvals "In progress" 5 s, Reports 3 s (only while any report is `generating`).

## Decisions this plan adds (spec §12 left them to the builder)

- **Contract fields not in the spec** are defined in Task 1 (audit rows, cursor paging, `approval`, `tool_steps`, run listing, extra error codes).
- **Extra error codes:** `401 unauthenticated`, `404 not_found`, `403 not_requester`, `409 run_active`, `409 drifted`, `409 not_ready`, `422 validation_error`, `429 rate_limited`.
- **`delete_snapshot`** is a fifth `change_type`, used only for the rollback of a `create_snapshot` (spec §6 says "snapshot: delete the snapshot (LOW)" but lists no type for it).
- **`GET /change-requests?status=`** accepts a comma-separated list (the Approvals tabs each cover several statuses).
- **Approvals and Audit & reports use `st.segmented_control` (key-bound) instead of `st.tabs`**, because the spec needs programmatic switching ("switches to the Pending tab with that CR open", "links to the Reports tab"), which `st.tabs` doesn't support. It's still a built-in clickable control.
- **Demo password** for all four users is `dbops-demo` (README says so). The UI is only reachable on localhost.

## Review Focus

1. **Overlapping event pages.** A fragment rerun or a retry asks for `after_seq` twice and gets events it already has. Each event must be applied exactly once, with no doubled text or tool steps. *(Test in Task 10: `test_apply_events_ignores_already_seen_seq`.)*
2. **agent-api goes away mid-run.** Polling raises `ServiceUnavailable`. The chat shows "agent-api unreachable, retrying…", keeps `active_run` and resumes when the service is back. It must not crash or lose the run. *(Task 10: `test_chat_keeps_run_when_agent_api_unreachable`.)*
3. **Hostile or odd strings in CR data.** Tag values, summaries and database names containing `<script>`, `<img onerror=…>` or markdown must render literally in diffs, titles and badges. *(Task 7: `test_diff_html_escapes_values`.)*
4. **Stale CR state.** Another approver already acted, so approve or reject returns `409 invalid_state`. Show it in plain words, refresh the list, and never show a traceback. *(Task 11: `test_invalid_state_shows_plain_words_and_refreshes`.)*
5. **Band edges and missing values.** Scores 80, 79, 50 and 49; days to full 14, 15, 30, 31 and `null`. Each lands in the right band, and `null` shows `—` in the neutral style. *(Task 7: `test_score_band_edges`, `test_days_band_edges_and_null`.)*

---

## File Structure

```
pyproject.toml                       uv workspace root; dev group: pytest, ruff, openapi-spec-validator, jsonschema
.gitignore  README.md  docker-compose.yml
contracts/agent-api.yaml  executor.yaml  health-analytics.yaml   (OpenAPI 3.1)
config/users.yaml                    4 demo users, bcrypt hashes
scripts/hash_password.py             prints a bcrypt hash for a password
tests/test_contracts.py              contracts are valid and cover the spec's endpoints
mock_backend/
  pyproject.toml  Dockerfile
  src/mock_backend/
    app.py            create_app(), build_from_env(); request middleware that calls advance()
    clock.py          Clock protocol, SystemClock, FakeClock
    identity.py       Role, Caller, load_roles(), caller dependency (401s)
    errors.py         ApiProblem exception + handlers (incl. 422 validation_error)
    state.py          MockState (all collections + lock), advance(state, now)
    seed.py           seed_state(now) -> MockState: fleet, alarms, CRs, audit, reports
    changes.py        CR rules, steps, lifecycle, rollback
    runs.py           scripted runs, playback, truncate_result()
    audit.py          audit query + cursor paging, facets
    reports.py        report generation (csv/md content)
    routes_health.py  routes_agent.py  routes_executor.py
  tests/conftest.py   test_identity.py  test_health_routes.py  test_change_requests.py
        test_executor.py  test_runs.py  test_audit_reports.py  test_rollback.py  test_route_coverage.py
frontend/
  pyproject.toml  Dockerfile  app.py  .streamlit/config.toml  static/fonts/*.woff2
  src/dbops_frontend/
    config.py         Settings, load_settings()
    auth.py           Role, User, load_users(), authenticate()
    api/base.py       ServiceClient, ApiError, ServiceUnavailable
    api/agent.py  api/executor.py  api/health.py
    clients.py        agent_client(), health_client(), executor_client() from session user + settings
    format.py         bands, labels, badge/diff HTML, plain_error()
    permissions.py    Decision, can_* rules
    chat_state.py     ActiveRun, Turn, ToolStep, apply_events(), build_turn(), turn_from_message()
    navigation.py     approvals_title(), build_navigation()
    views/common.py   service_banner(), risk_badge(), status_line()
    views/login.py  views/health.py  views/chat.py  views/approvals.py  views/audit_reports.py
  tests/conftest.py   test_api_clients.py  test_auth.py  test_format.py  test_permissions.py
        test_shell.py  test_health_view.py  test_chat_state.py  test_chat_view.py
        test_approvals_view.py  test_audit_reports_view.py  test_architecture.py
```

Run all tests from the repo root with `uv run pytest`. The root `pyproject.toml` sets `[tool.pytest.ini_options] testpaths = ["tests", "mock_backend/tests", "frontend/tests"]` and `addopts = "--import-mode=importlib"`. Test file names are unique across directories.

---

### Task 1: Workspace, demo users and OpenAPI contracts

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `config/users.yaml`, `scripts/hash_password.py`, `contracts/agent-api.yaml`, `contracts/executor.yaml`, `contracts/health-analytics.yaml`
- Test: `tests/test_contracts.py`

**Interfaces:**
- Produces: `config/users.yaml` with format `users: [{username, display_name, role, password_hash}]`. Three contract files whose `components.schemas` names are used by the conformance checker in Task 2: `Error`, `Conversation`, `Message`, `ToolStep`, `RunEvent`, `EventsPage`, `Run`, `ChangeRequest`, `Approval`, `ProgressStep`, `MaintenanceWindow`, `CRResult`, `AuditRow`, `AuditPage`, `AuditFacets`, `AuditFilters`, `Report`, `ApproveBody`, `ReasonBody`, `StatusResponse`, `FleetDatabase`, `Fleet`, `FleetSummary`, `Alarm`.

Schemas the spec leaves open (write these exactly; everything else is copied from spec §7):

| Schema | Fields |
|---|---|
| `ToolStep` | `tool_call_id`, `name`, `args` (object), `status` (`ok`\|`error`), `result` (any), `truncated` (bool), `duration_ms` (int) |
| `Message` | `role` (`user`\|`assistant`), `text`, `tool_steps` [ToolStep], `change_request_ids` [str], `run_id` (str\|null) |
| `RunEvent` | `oneOf` discriminated on `type`; all variants carry `seq` (int ≥1), `type` and `ts`. Variants per spec §7.1. `run_failed` has `error` (str), and `tool_call_finished` has optional `truncated`. |
| `Run` | `run_id`, `conversation_id`, `status` (`running`\|`completed`\|`failed`\|`cancelled`), `started_at` |
| `ChangeRequest` | spec §7.1 shape, plus `rollback_of` (str\|null) and `status_reason` (str\|null, the reject or cancel reason). `maintenance_window` is always an object: `{required: bool, window: str\|null, inside_now: bool\|null, next_start: str\|null}`. `status` enum: `pending, approved, rejected, withdrawn, executing, scheduled, succeeded, failed, cancelled, failed_cleanup_pending`. |
| `Approval` | `decision` (`approved`\|`rejected`), `decided_by`, `decided_at`, `mode` (`now`\|`next_window`\|null), `override_reason` (str\|null), `reason` (str\|null) |
| `CRResult` | `outcome` (`succeeded`\|`failed`), `message` (str), `report_id` (str\|null) |
| `AuditRow` | `id`, `ts`, `user`, `action`, `tool` (str\|null), `database` (str\|null), `result` (`ok`\|`error`\|`denied`), `change_request_id` (str\|null), `detail` (str) |
| `AuditPage` | `items` [AuditRow], `next_cursor` (str\|null) |
| `AuditFacets` | `users` [str], `tools` [str], `databases` [str] |
| `AuditFilters` | optional `user`, `tool`, `database`, `from`, `to` |
| `ApproveBody` / `ReasonBody` | `{mode, override_reason?}` / `{reason: str, minLength 1}` |
| `Alarm` | `name`, `metric`, `state` (`OK`\|`ALARM`\|`INSUFFICIENT_DATA`), `since`, `threshold` (number), `description` |

Each contract declares two `apiKey` header security schemes (`X-DBOps-User`, `X-DBOps-Service-Token`), requires both through a top-level `security` entry, and documents every error status listed under Decisions on the operations that can return it.

- [ ] **Step 1: Initialise the repo and workspace.** Run `git init`. Create the root `pyproject.toml` (uv workspace with members `mock_backend` and `frontend`, `requires-python = ">=3.12,<3.13"`, a dev dependency group with `pytest`, `ruff`, `openapi-spec-validator`, `jsonschema`, `bcrypt`, `PyYAML`, and pytest options as described under File Structure). Create `.gitignore` covering `.venv/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/` and `.superpowers/`.

- [ ] **Step 2: Write the failing tests** in `tests/test_contracts.py`:

```python
ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "agent-api": {
        ("get", "/conversations"), ("post", "/conversations"),
        ("get", "/conversations/{id}/messages"), ("post", "/conversations/{id}/runs"),
        ("get", "/runs"), ("get", "/runs/{run_id}/events"), ("post", "/runs/{run_id}/cancel"),
        ("get", "/change-requests"), ("get", "/change-requests/{id}"),
        ("post", "/change-requests/{id}/rollback"),
        ("get", "/audit"), ("get", "/audit/facets"),
        ("get", "/reports"), ("get", "/reports/{id}"), ("get", "/reports/{id}/download"),
        ("post", "/reports/change-history"),
    },
    "executor": {("post", f"/change-requests/{{id}}/{a}")
                 for a in ("approve", "reject", "withdraw", "cancel", "retry-cleanup")},
    "health-analytics": {
        ("get", "/fleet"), ("get", "/fleet/summary"), ("get", "/fleet/{identifier}/alarms"),
        ("post", "/refresh"), ("post", "/reports/fleet-health"),
    },
}

@pytest.mark.parametrize("name", EXPECTED)
def test_contract_is_valid_openapi_31(name):
    doc = yaml.safe_load((ROOT / "contracts" / f"{name}.yaml").read_text())
    assert doc["openapi"].startswith("3.1")
    openapi_spec_validator.validate(doc)

@pytest.mark.parametrize("name", EXPECTED)
def test_contract_paths_match_spec(name):
    doc = yaml.safe_load((ROOT / "contracts" / f"{name}.yaml").read_text())
    ops = {(m, p) for p, item in doc["paths"].items() for m in item if m in {"get", "post"}}
    assert ops == EXPECTED[name]

@pytest.mark.parametrize("name", EXPECTED)
def test_contract_requires_both_identity_headers(name):
    doc = yaml.safe_load((ROOT / "contracts" / f"{name}.yaml").read_text())
    names = {s["name"] for s in doc["components"]["securitySchemes"].values()}
    assert names == {"X-DBOps-User", "X-DBOps-Service-Token"}
    assert len(doc["security"]) == 1 and len(doc["security"][0]) == 2

def test_users_file_has_four_demo_users():
    users = yaml.safe_load((ROOT / "config/users.yaml").read_text())["users"]
    assert {u["username"]: u["role"] for u in users} == {
        "priya": "PRIVILEGED", "arjun": "PRIVILEGED", "sam": "SAFE-WRITE", "rita": "READ"}
    assert all(bcrypt.checkpw(b"dbops-demo", u["password_hash"].encode()) for u in users)
```

- [ ] **Step 3: Run the tests to verify they fail.** Run `uv sync && uv run pytest tests/test_contracts.py -v`. Expected: FAIL (files not found).

- [ ] **Step 4: Write `scripts/hash_password.py`** (reads a password with `getpass` and prints `bcrypt.hashpw(pw, bcrypt.gensalt(12))`). Use it to create `config/users.yaml`. Display names: Priya, Arjun, Sam, Rita.

- [ ] **Step 5: Write the three contracts** with the schemas above, the endpoint tables from spec §7, and the error responses listed under Decisions.

- [ ] **Step 6: Run the tests to verify they pass.** Run `uv run pytest tests/test_contracts.py -v`. Expected: 10 passed.

- [ ] **Step 7: Commit.**

```bash
git add -A && git commit -m "feat: add workspace, demo users and OpenAPI contracts"
```

---

### Task 2: Mock backend foundation and health-analytics routes

**Files:**
- Create: `mock_backend/pyproject.toml` (deps: `fastapi>=0.115`, `uvicorn>=0.32`, `PyYAML>=6`), `mock_backend/src/mock_backend/{__init__,app,clock,identity,errors,state,seed,routes_health}.py`
- Test: `mock_backend/tests/conftest.py`, `mock_backend/tests/test_identity.py`, `mock_backend/tests/test_health_routes.py`

**Interfaces:**
- Produces:
  - `create_app(users_file: Path, service_token: str, clock: Clock | None = None, time_scale: float = 1.0) -> FastAPI`. State lives on `app.state.mock: MockState`. A middleware takes `state.lock` and calls `advance(state, clock.now(), time_scale)` before every request.
  - `build_from_env() -> FastAPI`, used as `uvicorn mock_backend.app:build_from_env --factory`.
  - `class Clock(Protocol): def now(self) -> datetime`. `SystemClock()`. `FakeClock(start: datetime)` with `.advance(seconds: float) -> None`.
  - `class Role(StrEnum): READ = "READ"; SAFE_WRITE = "SAFE-WRITE"; PRIVILEGED = "PRIVILEGED"`, with `Role.rank -> int` (0, 1, 2). `Caller(username: str, role: Role)`. FastAPI dependency `get_caller(request) -> Caller`.
  - `class ApiProblem(Exception): __init__(status: int, code: str, message: str)`.
  - `MockState` dataclass with fields `lock: threading.Lock`, `fleet: list[dict]`, `alarms: dict[str, list[dict]]`, `last_refresh: datetime | None`, `change_requests: dict[str, dict]`, `next_cr_number: int`, `conversations: dict[str, dict]`, `runs: dict[str, dict]`, `audit: list[dict]`, `reports: dict[str, dict]`. `advance(state, now, time_scale) -> None` starts as a no-op hook. Tasks 3–5 add their `advance_*` calls to it.
  - `seed_state(now: datetime) -> MockState`.
  - `GET /healthz` (no auth): `{"ok": true}`.
  - Test fixtures in `mock_backend/tests/conftest.py`: `users_file` (a temp file with the four users at bcrypt rounds=4), `clock` (`FakeClock(datetime(2026, 11, 16, 10, 0, tzinfo=UTC))`), `app` (`time_scale=1.0`), `client(username) -> TestClient` with both headers set (token `"test-token"`), and `contract` with `.check(service: str, method: str, path: str, status: int, body) -> None`.

The conformance checker resolves `#/components/...` refs by validating against a root that carries the contract's components:

```python
schema = doc["paths"][path][method]["responses"][str(status)]["content"]["application/json"]["schema"]
Draft202012Validator({**schema, "components": doc["components"]}).validate(body)
```

**Seed fleet (pinned; tests rely on it):** exactly 14 databases across `env` ∈ {prod, staging, dev} and engines aurora-postgresql, aurora-mysql, postgres and mysql, all in `us-east-1`. Exactly two have `health_score < 50`:
- `prod-orders-db`: aurora-postgresql, `db.r6g.large`, prod, score 42, `days_to_full` 9, `open_alarms` 2, with 2 alarms in state `ALARM`
- `legacy-reports-db`: mysql, `db.m5.large`, staging, score 38, `days_to_full` null, `open_alarms` 1

Also include `prod-payments-db` (prod, aurora-postgresql, score 91, `days_to_full` 120) and `staging-legacy-db` (staging, postgres, score 74, `days_to_full` 22). Exactly three databases have a non-null `days_to_full < 30`. The other identifiers are up to the implementer.

- [ ] **Step 1: Write the failing tests.**

```python
# test_identity.py
def test_missing_token_is_401(app):
    r = TestClient(app).get("/fleet", headers={"X-DBOps-User": "priya"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthenticated"

def test_unknown_user_is_401(app):
    r = TestClient(app).get("/fleet", headers={"X-DBOps-User": "mallory", "X-DBOps-Service-Token": "test-token"})
    assert r.status_code == 401

def test_healthz_needs_no_auth(app):
    assert TestClient(app).get("/healthz").json() == {"ok": True}

# test_health_routes.py
def test_fleet_matches_contract_and_seed(client, contract):
    body = client("rita").get("/fleet").json()
    contract.check("health-analytics", "get", "/fleet", 200, body)
    assert len(body["databases"]) == 14
    assert sorted(d["identifier"] for d in body["databases"] if d["health_score"] < 50) == ["legacy-reports-db", "prod-orders-db"]

def test_fleet_filters_by_env_and_engine(client):
    dbs = client("rita").get("/fleet", params={"env": "prod", "engine": "aurora-postgresql"}).json()["databases"]
    assert dbs and all(d["env"] == "prod" and d["engine"] == "aurora-postgresql" for d in dbs)

def test_summary_counts(client, contract):
    body = client("rita").get("/fleet/summary").json()
    contract.check("health-analytics", "get", "/fleet/summary", 200, body)
    assert (body["total"], body["unhealthy"], body["under_30_days_to_full"]) == (14, 2, 3)

def test_alarms_for_known_and_unknown_db(client, contract):
    body = client("rita").get("/fleet/prod-orders-db/alarms").json()
    contract.check("health-analytics", "get", "/fleet/{identifier}/alarms", 200, body)
    assert len(body) == 2
    r = client("rita").get("/fleet/nope/alarms")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"

def test_refresh_is_rate_limited_to_once_a_minute(client, clock):
    c = client("rita")
    assert c.post("/refresh").status_code == 202
    r = c.post("/refresh")
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    clock.advance(61)
    assert c.post("/refresh").status_code == 202
```

(`POST /reports/fleet-health` is tested in Task 5.)

- [ ] **Step 2: Run the tests to verify they fail.** Run `uv sync && uv run pytest mock_backend/tests -v`. Expected: FAIL (`ModuleNotFoundError: mock_backend`).

- [ ] **Step 3: Implement** `clock.py`, `identity.py` (role lookup from the users file, constant-time token compare with `hmac.compare_digest`), `errors.py` (handlers turning `ApiProblem` and `RequestValidationError` into the error body, the latter as `422 validation_error`), `state.py`, `seed.py` (fleet and alarms only for now; `as_of` = now), `routes_health.py` and `app.py`. A `POST /refresh` sets `as_of` to now.

- [ ] **Step 4: Run the tests to verify they pass.** Run `uv run pytest mock_backend/tests -v`. Expected: all pass.

- [ ] **Step 5: Commit.**

```bash
git add -A && git commit -m "feat(mock): app foundation, identity and health-analytics routes"
```

---

### Task 3: Mock change requests and executor routes

**Files:**
- Create: `mock_backend/src/mock_backend/changes.py`, `mock_backend/src/mock_backend/routes_agent.py` (CR read routes only), `mock_backend/src/mock_backend/routes_executor.py`
- Modify: `seed.py` (add CRs), `state.py` (`advance` calls `advance_change_requests`)
- Test: `mock_backend/tests/test_change_requests.py`, `mock_backend/tests/test_executor.py`

**Interfaces:**
- Consumes: `MockState`, `Caller`, `Role`, `ApiProblem`, the `client`, `clock` and `contract` fixtures (Task 2).
- Produces:
  - `RISK_TIER: dict[str, str]` (values as in Global Constraints).
  - `STEPS: dict[str, list[str]]`: `modify_tags: [applying, done]`, `create_snapshot: [creating, available]`, `delete_snapshot: [deleting, deleted]`, `restore_test: [restoring, available, verified, cleaned_up]`, `modify_instance_class: [pre_snapshot, modifying, available]`, plus the next-window path `[pre_snapshot, scheduled, applied]`.
  - `new_change_request(state, *, change_type: str, database: dict, requested_by: str, params: dict, diff: list[dict], summary: str, now: datetime, conversation_id: str | None = None, rollback_of: str | None = None) -> dict`. It assigns `CR-%04d` from `state.next_cr_number`, sets the risk tier, impact, rollback plan, maintenance window (HIGH only), `status: pending`, all steps `pending`, and appends an audit row with action `change.proposed`.
  - `advance_change_requests(state, now, time_scale) -> None`. Each step takes `2 s × time_scale`. Next-window changes apply `60 s × time_scale` after approval. Cancel and retry-cleanup confirm after `2 s × time_scale`.
  - `audit_row(state, *, now, user, action, result="ok", tool=None, database=None, change_request_id=None, detail="") -> None` appends to `state.audit` (Task 5 serves it).

**Seed CRs (pinned):**

| id | type | database | requested_by | status | notes |
|---|---|---|---|---|---|
| CR-0130 | modify_tags | prod-payments-db | sam | succeeded | diff `owner: payments → payments-core` |
| CR-0131 | create_snapshot | prod-orders-db | sam | succeeded | |
| CR-0132 | modify_instance_class | staging-legacy-db | arjun | succeeded | `db.t3.medium → db.t3.large` |
| CR-0133 | create_snapshot | legacy-reports-db | sam | failed | `result.message` set |
| CR-0134 | modify_tags | prod-orders-db | sam | rejected | `status_reason` "Wrong owner tag" |
| CR-0135 | modify_instance_class | prod-payments-db | arjun | scheduled | approved by priya, next_window; stays scheduled until cancelled |
| CR-0136 | restore_test | legacy-reports-db | arjun | failed_cleanup_pending | `cleaned_up` step `failed` |
| CR-0137 | restore_test | prod-payments-db | priya | succeeded | |
| CR-0139 | modify_tags | staging-legacy-db | sam | pending | approving it always returns `409 drifted` |
| CR-0140 | modify_tags | prod-orders-db | sam | pending | |
| CR-0141 | create_snapshot | prod-payments-db | priya | pending | |
| CR-0142 | restore_test | prod-payments-db | arjun | pending | |
| CR-0143 | modify_instance_class | prod-orders-db | arjun | pending | `db.r6g.large → db.r6g.xlarge`, window `sun:03:00-sun:03:30`, `inside_now: false` |
| CR-0144 | modify_instance_class | staging-legacy-db | priya | pending | `inside_now: true` |

`next_cr_number` starts at 145. The mock rule for failure: a `restore_test` on `legacy-reports-db` ends `failed_cleanup_pending`, with `cleaned_up` failed.

**Approve checks, in this order:** `404 not_found` → `409 invalid_state` (not `pending`) → `403 self_approval` → `403 forbidden_role` (LOW needs SAFE-WRITE+, MEDIUM/HIGH need PRIVILEGED) → HIGH with `mode: now`, `inside_now: false` and an empty `override_reason` gives `422 window_override_reason_required` → CR-0139 gives `409 drifted` → `202 {status}` (`executing`, or `scheduled` for HIGH `next_window`). `mode` is ignored for non-HIGH changes. The other actions: `reject` needs SAFE-WRITE+ for LOW and PRIVILEGED otherwise, plus the self rule. `withdraw` returns `403 not_requester` unless the caller is the requester. `cancel` needs PRIVILEGED and status `scheduled`. `retry-cleanup` needs PRIVILEGED and status `failed_cleanup_pending`. A wrong status gives `409 invalid_state`. Every action writes an audit row.

- [ ] **Step 1: Write the failing tests.**

```python
# test_change_requests.py
def test_list_by_comma_separated_status(client, contract):
    body = client("rita").get("/change-requests", params={"status": "scheduled,failed_cleanup_pending"}).json()
    assert {c["id"] for c in body} == {"CR-0135", "CR-0136"}
    for cr in body: contract.check("agent-api", "get", "/change-requests/{id}", 200, cr)

def test_pending_seed(client):
    ids = {c["id"] for c in client("rita").get("/change-requests", params={"status": "pending"}).json()}
    assert ids == {"CR-0139", "CR-0140", "CR-0141", "CR-0142", "CR-0143", "CR-0144"}

def test_high_cr_carries_window(client):
    cr = client("rita").get("/change-requests/CR-0143").json()
    assert cr["risk_tier"] == "HIGH" and cr["maintenance_window"]["required"] is True
    assert cr["maintenance_window"]["inside_now"] is False

# test_executor.py
def approve(c, cr, **body): return c.post(f"/change-requests/{cr}/approve", json={"mode": "now", **body})

def test_self_approval_refused(client):
    r = approve(client("arjun"), "CR-0143", override_reason="x")
    assert (r.status_code, r.json()["error"]["code"]) == (403, "self_approval")

def test_role_from_users_file_not_header(client):
    c = client("rita"); c.headers["X-DBOps-Role"] = "PRIVILEGED"
    assert approve(c, "CR-0140").json()["error"]["code"] == "forbidden_role"

def test_safe_write_cannot_approve_medium(client):
    assert approve(client("sam"), "CR-0142").json()["error"]["code"] == "forbidden_role"

def test_outside_window_needs_reason_then_runs_through_steps(client, clock):
    c = client("priya")
    r = approve(c, "CR-0143")
    assert (r.status_code, r.json()["error"]["code"]) == (422, "window_override_reason_required")
    r = approve(c, "CR-0143", override_reason="Incident INC-77")
    assert (r.status_code, r.json()) == (202, {"status": "executing"})
    clock.advance(6.1)
    cr = c.get("/change-requests/CR-0143").json()
    assert cr["status"] == "succeeded"
    assert [p["state"] for p in cr["progress"]] == ["done", "done", "done"]
    assert cr["approval"]["override_reason"] == "Incident INC-77"

def test_next_window_schedules(client):
    r = client("priya").post("/change-requests/CR-0143/approve", json={"mode": "next_window"})
    assert r.json() == {"status": "scheduled"}
    cr = client("priya").get("/change-requests/CR-0143").json()
    assert [(p["step"], p["state"]) for p in cr["progress"]][:2] == [("pre_snapshot", "done"), ("scheduled", "active")]

def test_drifted(client):
    assert approve(client("priya"), "CR-0139").json()["error"]["code"] == "drifted"

def test_not_pending_is_invalid_state(client):
    assert approve(client("priya"), "CR-0134").status_code == 409

def test_reject_needs_reason(client):
    r = client("priya").post("/change-requests/CR-0140/reject", json={"reason": ""})
    assert (r.status_code, r.json()["error"]["code"]) == (422, "validation_error")

def test_withdraw_only_by_requester(client):
    assert client("priya").post("/change-requests/CR-0140/withdraw").json()["error"]["code"] == "not_requester"
    assert client("sam").post("/change-requests/CR-0140/withdraw").json() == {"status": "withdrawn"}

def test_cancel_scheduled(client, clock):
    assert client("sam").post("/change-requests/CR-0135/cancel", json={"reason": "r"}).status_code == 403
    assert client("priya").post("/change-requests/CR-0135/cancel", json={"reason": "freeze"}).status_code == 202
    clock.advance(2.1)
    assert client("priya").get("/change-requests/CR-0135").json()["status"] == "cancelled"

def test_retry_cleanup(client, clock):
    assert client("priya").post("/change-requests/CR-0136/retry-cleanup").status_code == 202
    clock.advance(2.1)
    cr = client("priya").get("/change-requests/CR-0136").json()
    assert cr["status"] == "succeeded" and cr["progress"][-1]["state"] == "done"

def test_restore_test_on_legacy_db_ends_cleanup_pending(app, client, clock):
    state = app.state.mock
    with state.lock:
        cr = new_change_request(state, change_type="restore_test", database=state_db(state, "legacy-reports-db"),
                                requested_by="arjun", params={}, diff=[], summary="t", now=clock.now())
    approve(client("priya"), cr["id"]); clock.advance(10)
    assert client("priya").get(f"/change-requests/{cr['id']}").json()["status"] == "failed_cleanup_pending"
```

(`state_db` is a small test helper that builds the CR `database` object from the fleet entry.)

- [ ] **Step 2: Run the tests to verify they fail.** Run `uv run pytest mock_backend/tests/test_change_requests.py mock_backend/tests/test_executor.py -v`. Expected: FAIL (404s, since the routes don't exist yet).

- [ ] **Step 3: Implement** `changes.py`, the seed CRs, the CR routes (`GET /change-requests` filtered by `status` and `database`, ordered by `created_at` descending; `GET /change-requests/{id}`) and `routes_executor.py`. The executor routes return 202 for approve, cancel and retry-cleanup, and 200 for reject and withdraw.

- [ ] **Step 4: Run the tests to verify they pass.** Run `uv run pytest mock_backend/tests -v`. Expected: all pass.

- [ ] **Step 5: Commit.**

```bash
git add -A && git commit -m "feat(mock): change requests, lifecycle and executor routes"
```

---

### Task 4: Mock conversations and scripted agent runs

**Files:**
- Create: `mock_backend/src/mock_backend/runs.py`
- Modify: `routes_agent.py` (conversations and runs), `state.py` (`advance` calls `advance_runs` before `advance_change_requests`)
- Test: `mock_backend/tests/test_runs.py`

**Interfaces:**
- Consumes: `new_change_request`, `audit_row`, `RISK_TIER` (Task 3).
- Produces:
  - `pick_script(message: str, role: Role) -> list[ScriptStep]`. `ScriptStep` is a dataclass with `delay_s: float` and `event: dict` (without `seq`/`ts`), or a `propose: dict` that becomes a `change_proposed` event when it's played.
  - `advance_runs(state, now, time_scale) -> None`. It emits every step whose cumulative delay × time_scale ≤ elapsed time and assigns contiguous `seq` from 1. A `propose` step calls `new_change_request(..., conversation_id=...)`. A `tool_call_finished` step writes an audit row (action `chat.tool_call`, `tool` = name). On a terminal event it appends the assistant `Message` to the conversation and updates `updated_at`.
  - `truncate_result(result: Any, limit: int = 20_480) -> tuple[Any, bool]`. If the JSON-encoded result exceeds `limit` bytes, it returns `({"preview": <first limit chars of the JSON>}, True)`.

**Script routing** (on `message.lower()`, first match wins):
- `"fail"`: a text delta, then `run_failed` with error `"Bedrock throttled the request"`.
- `"snapshot"`: `create_snapshot` on prod-orders-db (needs SAFE-WRITE).
- `"tag"`: `modify_tags` (SAFE-WRITE).
- `"restore"`: `restore_test` (PRIVILEGED).
- `"resize"`: `modify_instance_class` on prod-orders-db, `db.r6g.large → db.r6g.xlarge` (PRIVILEGED).
- anything else: a read-only answer.

Every script starts with `run_started` and includes at least one tool call pair (`rds.describe_db_instances`; the read-only answer adds `cloudwatch.get_metric_statistics`). If the caller's role is too low for the proposal, the `propose` step is replaced by a text delta: `"Your role ({role}) can't propose this change, so I haven't created a change request."` Delays: 0.15 s per text delta, 1.2 s per tool call, 0.3 s otherwise. A new conversation's title is `"New chat"`. The first run sets it to `message[:40]`. The user `Message` is stored when the run starts.

- [ ] **Step 1: Write the failing tests.**

```python
def start(c, text):
    conv = c.post("/conversations").json()["id"]
    return conv, c.post(f"/conversations/{conv}/runs", json={"message": text}).json()["run_id"]

def events(c, run_id, after=0): return c.get(f"/runs/{run_id}/events", params={"after_seq": after}).json()

def test_run_plays_back_contiguous_events(client, clock, contract):
    c = client("priya"); _, run = start(c, "how is prod-orders-db?")
    assert [e["type"] for e in events(c, run)["events"]] == ["run_started"]
    clock.advance(30)
    page = events(c, run)
    contract.check("agent-api", "get", "/runs/{run_id}/events", 200, page)
    assert page["run_status"] == "completed"
    assert [e["seq"] for e in page["events"]] == list(range(1, len(page["events"]) + 1))
    assert page["events"][-1]["type"] == "run_completed"

def test_after_seq_returns_only_newer(client, clock):
    c = client("priya"); _, run = start(c, "hi"); clock.advance(30)
    assert all(e["seq"] > 3 for e in events(c, run, after=3)["events"])

def test_second_run_while_active_is_409(client):
    c = client("priya"); conv, _ = start(c, "hi")
    r = c.post(f"/conversations/{conv}/runs", json={"message": "again"})
    assert (r.status_code, r.json()["error"]["code"]) == (409, "run_active")

def test_snapshot_proposal_creates_pending_cr(client, clock):
    c = client("sam"); conv, run = start(c, "take a snapshot of prod-orders-db"); clock.advance(30)
    prop = [e for e in events(c, run)["events"] if e["type"] == "change_proposed"][0]
    cr = c.get(f"/change-requests/{prop['change_request_id']}").json()
    assert (cr["status"], cr["requested_by"], cr["requested_via"], cr["conversation_id"]) == ("pending", "sam", "chat", conv)

def test_read_user_cannot_propose(client, clock):
    c = client("rita"); _, run = start(c, "resize prod-orders-db"); clock.advance(30)
    evs = events(c, run)["events"]
    assert not [e for e in evs if e["type"] == "change_proposed"]
    assert "can't propose" in "".join(e.get("text", "") for e in evs)

def test_cancel_mid_run(client, clock):
    c = client("priya"); _, run = start(c, "how is the fleet?"); clock.advance(0.5)
    assert c.post(f"/runs/{run}/cancel").status_code == 202
    clock.advance(30)
    page = events(c, run)
    assert page["run_status"] == "cancelled" and page["events"][-1]["type"] == "run_cancelled"

def test_failed_run(client, clock):
    c = client("priya"); _, run = start(c, "please fail"); clock.advance(30)
    page = events(c, run)
    assert page["run_status"] == "failed" and page["events"][-1]["error"] == "Bedrock throttled the request"

def test_messages_and_title_after_completion(client, clock, contract):
    c = client("sam"); conv, run = start(c, "take a snapshot of prod-orders-db"); clock.advance(30); events(c, run)
    msgs = c.get(f"/conversations/{conv}/messages").json()
    for m in msgs: contract.check("agent-api", "get", "/conversations/{id}/messages", 200, [m])
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["run_id"] == run and len(msgs[1]["change_request_ids"]) == 1 and msgs[1]["tool_steps"]
    titles = {x["id"]: x["title"] for x in c.get("/conversations").json()}
    assert titles[conv] == "take a snapshot of prod-orders-db"

def test_running_run_lookup(client, clock):
    c = client("priya"); conv, run = start(c, "hi")
    assert [r["run_id"] for r in c.get("/runs", params={"conversation_id": conv, "status": "running"}).json()] == [run]
    clock.advance(30)
    assert c.get("/runs", params={"conversation_id": conv, "status": "running"}).json() == []

def test_conversations_are_per_user(client):
    conv, _ = start(client("sam"), "hi")
    assert conv not in {x["id"] for x in client("priya").get("/conversations").json()}

def test_truncate_result():
    out, truncated = truncate_result({"rows": "x" * 30_000})
    assert truncated and len(json.dumps(out)) < 21_000
    assert truncate_result({"a": 1}) == ({"a": 1}, False)
```

- [ ] **Step 2: Run the tests to verify they fail.** Run `uv run pytest mock_backend/tests/test_runs.py -v`. Expected: FAIL.

- [ ] **Step 3: Implement** `runs.py` and the conversation and run routes (`POST /runs/{id}/cancel` returns `202 {}` and is idempotent; cancelling someone else's run gives `404 not_found`). Conversation ids are `conv-<uuid4 hex[:8]>` and run ids are `run-<uuid4 hex[:8]>`.

- [ ] **Step 4: Run the tests to verify they pass.** Run `uv run pytest mock_backend/tests -v`. Expected: all pass.

- [ ] **Step 5: Commit.**

```bash
git add -A && git commit -m "feat(mock): conversations and scripted agent runs"
```

---

### Task 5: Mock audit, reports, rollback and route coverage

**Files:**
- Create: `mock_backend/src/mock_backend/audit.py`, `mock_backend/src/mock_backend/reports.py`
- Modify: `routes_agent.py` (audit, reports, rollback), `routes_health.py` (fleet-health report), `changes.py` (rollback; a finished restore test creates a `restore_test` report and sets `result.report_id`), `seed.py` (about 40 audit rows spread over the last 10 days across all four users, plus three reports: one `restore_test` ready, one `fleet_health` ready, and one `change_history` failed with error `"Timed out reading CloudWatch"`), `state.py` (`advance` calls `advance_reports`)
- Test: `mock_backend/tests/test_audit_reports.py`, `mock_backend/tests/test_rollback.py`, `mock_backend/tests/test_route_coverage.py`

**Interfaces:**
- Consumes: `audit_row`, `new_change_request`, `RISK_TIER` (Task 3).
- Produces:
  - `query_audit(rows, *, user=None, tool=None, database=None, from_=None, to=None, limit=50, cursor=None) -> dict`. Rows are newest first, `limit` is capped at 200, and the cursor is the stringified offset (opaque to callers).
  - `audit_facets(rows) -> dict` with sorted distinct values.
  - `GET /reports` is ordered by `created_at`, newest first.
  - `start_report(state, *, kind, title, created_by, now, build: Callable[[], dict[str, str]]) -> str`. It creates a report with status `generating` and id `rep-<hex8>`. `advance_reports` flips it to `ready` after `3 s × time_scale` and stores `build()` output keyed by format.
  - The change-history CSV header is exactly `ts,user,action,tool,database,result,change_request_id`. Markdown reports start with `# <title>`. Download serves `text/csv` or `text/markdown`.
  - `propose_rollback(state, cr_id: str, caller: Caller, now) -> dict`, the new CR. Rules: the CR must be `succeeded` and not `restore_test`, with no pending CR whose `rollback_of` equals it; otherwise `409 invalid_state`. The rollback type is `modify_tags` → `modify_tags` (before and after swapped), `create_snapshot` → `delete_snapshot`, `modify_instance_class` → `modify_instance_class` (from and to swapped). The caller needs SAFE-WRITE for a LOW rollback and PRIVILEGED for a HIGH one; otherwise `403 forbidden_role`. The new CR has `requested_via: "approvals"`.

- [ ] **Step 1: Write the failing tests.**

```python
# test_audit_reports.py
def test_audit_paging_newest_first(client, contract):
    c = client("rita")
    p1 = c.get("/audit", params={"limit": 5}).json()
    contract.check("agent-api", "get", "/audit", 200, p1)
    p2 = c.get("/audit", params={"limit": 5, "cursor": p1["next_cursor"]}).json()
    ts = [r["ts"] for r in p1["items"] + p2["items"]]
    assert ts == sorted(ts, reverse=True) and len({r["id"] for r in p1["items"] + p2["items"]}) == 10

def test_audit_filters(client):
    rows = client("rita").get("/audit", params={"user": "sam", "from": "2026-11-12T00:00:00Z"}).json()["items"]
    assert rows and all(r["user"] == "sam" and r["ts"] >= "2026-11-12T00:00:00Z" for r in rows)

def test_facets(client, contract):
    body = client("rita").get("/audit/facets").json()
    contract.check("agent-api", "get", "/audit/facets", 200, body)
    assert {"priya", "arjun", "sam", "rita"} <= set(body["users"])

def test_change_history_report_lifecycle(client, clock):
    c = client("rita")
    r = c.post("/reports/change-history", json={"user": "sam"})
    assert r.status_code == 202; rid = r.json()["report_id"]
    assert c.get(f"/reports/{rid}").json()["status"] == "generating"
    r = c.get(f"/reports/{rid}/download", params={"format": "csv"})
    assert (r.status_code, r.json()["error"]["code"]) == (409, "not_ready")
    clock.advance(3.1)
    assert c.get(f"/reports/{rid}").json()["status"] == "ready"
    csv = c.get(f"/reports/{rid}/download", params={"format": "csv"}).text
    assert csv.splitlines()[0] == "ts,user,action,tool,database,result,change_request_id"
    assert c.get(f"/reports/{rid}/download", params={"format": "md"}).text.startswith("# ")

def test_fleet_health_report_listed(client, contract):
    rid = client("rita").post("/reports/fleet-health", json={}).json()["report_id"]
    reports = client("rita").get("/reports").json()
    contract.check("agent-api", "get", "/reports", 200, reports)
    assert reports[0]["id"] == rid and reports[0]["kind"] == "fleet_health"

def test_seeded_failed_report_has_error(client):
    failed = [r for r in client("rita").get("/reports").json() if r["status"] == "failed"]
    assert failed[0]["error"] == "Timed out reading CloudWatch"

def test_restore_test_links_report(client, clock):
    client("priya").post("/change-requests/CR-0142/approve", json={"mode": "now"}); clock.advance(10)
    cr = client("priya").get("/change-requests/CR-0142").json()
    assert cr["result"]["report_id"] in {r["id"] for r in client("priya").get("/reports").json()}

# test_rollback.py
def rb(c, cr): return c.post(f"/change-requests/{cr}/rollback")

def test_tag_rollback_swaps_diff(client):
    r = rb(client("sam"), "CR-0130")
    assert r.status_code == 201
    new = client("sam").get(f"/change-requests/{r.json()['change_request_id']}").json()
    old = client("sam").get("/change-requests/CR-0130").json()
    assert (new["rollback_of"], new["risk_tier"], new["status"]) == ("CR-0130", "LOW", "pending")
    assert [(d["before"], d["after"]) for d in new["diff"]] == [(d["after"], d["before"]) for d in old["diff"]]
    assert rb(client("sam"), "CR-0130").status_code == 409

def test_resize_rollback_needs_privileged(client):
    assert rb(client("sam"), "CR-0132").json()["error"]["code"] == "forbidden_role"
    cid = rb(client("priya"), "CR-0132").json()["change_request_id"]
    assert client("priya").get(f"/change-requests/{cid}").json()["risk_tier"] == "HIGH"

def test_snapshot_rollback_is_delete_snapshot(client):
    cid = rb(client("sam"), "CR-0131").json()["change_request_id"]
    assert client("sam").get(f"/change-requests/{cid}").json()["change_type"] == "delete_snapshot"

@pytest.mark.parametrize("cr", ["CR-0137", "CR-0140"])  # restore test; pending
def test_rollback_not_allowed(client, cr):
    assert rb(client("priya"), cr).status_code == 409

# test_route_coverage.py
def test_mock_routes_equal_contract_paths(app):
    contract_ops = {...}  # union of (method, path) over the three contracts, loaded from contracts/*.yaml
    mock_ops = {(m.lower(), r.path) for r in app.routes if isinstance(r, APIRoute) for m in r.methods}
    assert mock_ops - {("get", "/healthz")} == contract_ops
```

- [ ] **Step 2: Run the tests to verify they fail.** Run `uv run pytest mock_backend/tests -v`. Expected: the new tests fail.

- [ ] **Step 3: Implement** `audit.py`, `reports.py`, rollback in `changes.py`, the routes and the seed additions.

- [ ] **Step 4: Run the tests to verify they pass.** Run `uv run pytest mock_backend/tests -v && uv run ruff check mock_backend`. Expected: all pass, ruff clean.

- [ ] **Step 5: Commit.**

```bash
git add -A && git commit -m "feat(mock): audit, reports, rollback; mock routes match contracts"
```

---

### Task 6: Frontend API clients

**Files:**
- Create: `frontend/pyproject.toml` (deps: `streamlit>=1.50,<2`, `httpx>=0.28`, `pandas>=2.2`, `bcrypt>=4.2`, `PyYAML>=6`; package `dbops_frontend` under `src/`), `frontend/src/dbops_frontend/{__init__,config}.py`, `frontend/src/dbops_frontend/api/{__init__,base,agent,executor,health}.py`
- Test: `frontend/tests/conftest.py`, `frontend/tests/test_api_clients.py`

**Interfaces:**
- Consumes: `create_app` from `mock_backend.app` (tests only; the root dev group depends on both workspace members).
- Produces:
  - `Settings(agent_api_url: str, executor_url: str, health_url: str, service_token: str, users_file: Path)` and `load_settings() -> Settings` (reads the env vars in Global Constraints on every call, with no caching, so a changed env takes effect on the next rerun).
  - `class ApiError(Exception)` with `status: int`, `code: str`, `message: str`. `class ServiceUnavailable(Exception)` with `service: str`.
  - `ServiceClient(base_url: str, username: str, service_token: str, *, service: str, client: httpx.Client | None = None, timeout: float = 5.0)` with `_get(path, params=None) -> Any`, `_post(path, json=None) -> Any` and `_get_bytes(path, params=None) -> bytes`. Params whose value is `None` are dropped. `httpx.TransportError` becomes `ServiceUnavailable(service)`. A non-2xx response with an error body becomes `ApiError(status, code, message)`; otherwise it becomes `ApiError(status, "http_error", text[:200])`. An injected `client` (for example FastAPI's `TestClient`) is used as-is, with the headers added per request.
  - The subclasses fix `service` themselves, so their constructor is `(base_url, username, service_token, *, client=None, timeout=5.0)`.
  - `AgentApi(ServiceClient)` with `service="agent-api"`: `list_conversations() -> list[dict]`, `create_conversation() -> str`, `get_messages(conv_id) -> list[dict]`, `start_run(conv_id, message) -> str`, `get_events(run_id, after_seq: int) -> dict`, `cancel_run(run_id) -> None`, `running_runs(conv_id) -> list[dict]`, `list_change_requests(statuses: list[str] | None = None, database: str | None = None) -> list[dict]`, `get_change_request(cr_id) -> dict`, `propose_rollback(cr_id) -> str`, `list_audit(filters: dict, cursor: str | None = None, limit: int = 50) -> dict`, `audit_facets() -> dict`, `list_reports() -> list[dict]`, `get_report(report_id) -> dict`, `download_report(report_id, fmt: str) -> bytes`, `start_change_history_report(filters: dict) -> str`.
  - `ExecutorApi(ServiceClient)` with `service="executor"`: `approve(cr_id, mode: str, override_reason: str | None = None) -> dict`, `reject(cr_id, reason) -> dict`, `withdraw(cr_id) -> dict`, `cancel(cr_id, reason) -> dict`, `retry_cleanup(cr_id) -> dict`.
  - `HealthApi(ServiceClient)` with `service="health-analytics"`: `fleet(env: str | None = None, engine: str | None = None) -> dict`, `summary() -> dict`, `alarms(identifier) -> list[dict]`, `refresh() -> None`, `start_fleet_report(env: str | None = None) -> str`.
  - Fixtures in `frontend/tests/conftest.py`: `users_file` (same as the mock's), `mock_client(username) -> TestClient` (mock at `time_scale=0`), `api(cls, username) -> cls` (a client built on `mock_client`), and `mock_server(time_scale=0.0) -> str` (a factory that runs uvicorn in a daemon thread on a free port and returns the base URL; servers are stopped at teardown), plus `frontend_env(base_url)`, which monkeypatches all five env vars (all three service URLs point at the one mock).

- [ ] **Step 1: Write the failing tests.**

```python
def test_fleet_filter(api):
    assert all(d["env"] == "prod" for d in api(HealthApi, "rita").fleet(env="prod")["databases"])

def test_api_error_mapping(api):
    with pytest.raises(ApiError) as e:
        api(ExecutorApi, "arjun").approve("CR-0143", "now", override_reason="x")
    assert (e.value.status, e.value.code) == (403, "self_approval")

def test_unreachable_is_service_unavailable():
    with pytest.raises(ServiceUnavailable) as e:
        HealthApi("http://127.0.0.1:9", "rita", "t", timeout=0.5).summary()
    assert e.value.service == "health-analytics"

def test_identity_headers_sent():
    seen = {}
    def handler(req): seen.update(req.headers); return httpx.Response(200, json={"as_of": "x", "total": 0, "unhealthy": 0, "under_30_days_to_full": 0, "open_alarms": 0})
    HealthApi("http://h", "sam", "s3cret", client=httpx.Client(transport=httpx.MockTransport(handler))).summary()
    assert (seen["x-dbops-user"], seen["x-dbops-service-token"]) == ("sam", "s3cret")

def test_list_change_requests_multi_status(api):
    crs = api(AgentApi, "rita").list_change_requests(["scheduled", "failed_cleanup_pending"])
    assert {c["id"] for c in crs} == {"CR-0135", "CR-0136"}

def test_report_download_bytes(api):
    a = api(AgentApi, "rita")
    rid = a.start_change_history_report({})
    a.get_report(rid)  # time_scale=0 → ready on next read
    assert a.download_report(rid, "csv").startswith(b"ts,user,action")
```

- [ ] **Step 2: Run the tests to verify they fail.** Run `uv sync && uv run pytest frontend/tests/test_api_clients.py -v`. Expected: FAIL (`ModuleNotFoundError: dbops_frontend`).

- [ ] **Step 3: Implement** `config.py`, `api/base.py`, `api/agent.py`, `api/executor.py` and `api/health.py`. `list_change_requests` joins the statuses with `","`.

- [ ] **Step 4: Run the tests to verify they pass.** Run `uv run pytest frontend/tests/test_api_clients.py -v`. Expected: 6 passed.

- [ ] **Step 5: Commit.**

```bash
git add -A && git commit -m "feat(frontend): API clients for agent-api, executor, health-analytics"
```

---

### Task 7: Frontend auth, formatting and permission rules

**Files:**
- Create: `frontend/src/dbops_frontend/{auth,format,permissions}.py`
- Test: `frontend/tests/test_auth.py`, `frontend/tests/test_format.py`, `frontend/tests/test_permissions.py`

**Interfaces:**
- Consumes: `ApiError` (Task 6).
- Produces:
  - `auth.Role` (same `StrEnum` values and `rank` as the mock's), `User(username: str, display_name: str, role: Role)` (frozen dataclass), `load_users(path: Path) -> dict[str, dict]`, `authenticate(users: dict[str, dict], username: str, password: str) -> User | None`. Unknown users still pay for a bcrypt check against a dummy hash.
  - `format.Band(StrEnum)`: `GREEN`, `AMBER`, `RED`, `NEUTRAL`. `BAND_COLOR = {GREEN: "#22c55e", AMBER: "#f59e0b", RED: "#ef4444", NEUTRAL: ""}`.
  - `score_band(score: int) -> Band` (≥80 green, 50–79 amber, <50 red) and `score_label(score) -> str`: `"✅ 92"`, `"⚠ 64"`, `"⛔ 31"`.
  - `days_band(days: int | None) -> Band` (≤14 red, 15–30 amber, otherwise or None neutral) and `days_label(days) -> str`: `"⛔ 9 d"`, `"⚠ 22 d"`, `"41 d"`, `"—"`.
  - `risk_badge_html(tier: str) -> str`: a `<span>` with the solid fill and text colours from Global Constraints and the tier word as text.
  - `diff_html(diff: list[dict]) -> str`: one monospace line per entry, `field: <before in red> → <after in green>`, with every value `html.escape`d (non-strings `json.dumps`ed first).
  - `status_label(status: str) -> str`: `pending "⏳ Pending"`, `approved "👍 Approved"`, `executing "⏳ Executing"`, `scheduled "🗓 Scheduled"`, `succeeded "✅ Succeeded"`, `failed "⛔ Failed"`, `rejected "✋ Rejected"`, `withdrawn "↩ Withdrawn"`, `cancelled "✖ Cancelled"`, `failed_cleanup_pending "⛔ Cleanup failed"`.
  - `format_duration(ms: int) -> str`: `"850 ms"` below 1000, otherwise `"1.2 s"`.
  - `tool_step_title(step: dict) -> str`: `"🔧 rds.describe_db_instances · 1.2 s · ok"`. A step that hasn't finished yet shows `"🔧 <name> · ⏳ running"`.
  - `cr_title(cr: dict) -> str`: `"CR-0143 · modify_instance_class · prod-orders-db"`, prefixed with `"↩ Rollback of CR-0130 · "` when `rollback_of` is set.
  - `plain_error(err: ApiError) -> str` with these exact strings:
    - `drifted`: "The database no longer matches this request's 'before' values, so nothing was changed. Ask for a new request."
    - `self_approval`: "You can't approve your own request."
    - `forbidden_role`: "Your role doesn't allow this action."
    - `invalid_state`: "This request changed state since the page loaded. The list has been refreshed."
    - `window_override_reason_required`: "Applying now outside the maintenance window needs a written reason."
    - `not_requester`: "Only the requester can withdraw this request."
    - `rate_limited`: "A refresh was already requested in the last minute."
    - `run_active`: "This conversation already has a run in progress."
    - anything else: `f"{err.message} ({err.code})"`
  - `permissions.Decision(allowed: bool, reason: str | None)`.
  - `can_decide(user, cr) -> Decision` (approve and reject share this rule): self → `"You can't approve your own request"`; LOW needs SAFE-WRITE+ and MEDIUM/HIGH need PRIVILEGED, else `f"Your role ({role}) can't approve {tier} changes"`; status not pending → `"Not pending"`.
  - `can_withdraw(user, cr) -> bool`, `can_cancel(user, cr) -> bool`, `can_retry_cleanup(user, cr) -> bool`.
  - `rollback_tier(cr) -> str | None`: `modify_tags` LOW, `create_snapshot` LOW, `modify_instance_class` HIGH, anything else None.
  - `can_propose_rollback(user, cr) -> bool`: the CR is `succeeded`, `rollback_tier` is not None, and the role meets that tier.
  - `can_propose(role, tier) -> bool`.

- [ ] **Step 1: Write the failing tests.**

```python
# test_format.py
@pytest.mark.parametrize("score,band", [(80, Band.GREEN), (79, Band.AMBER), (50, Band.AMBER), (49, Band.RED)])
def test_score_band_edges(score, band): assert score_band(score) is band

@pytest.mark.parametrize("days,band,label", [(14, Band.RED, "⛔ 14 d"), (15, Band.AMBER, "⚠ 15 d"),
    (30, Band.AMBER, "⚠ 30 d"), (31, Band.NEUTRAL, "31 d"), (None, Band.NEUTRAL, "—")])
def test_days_band_edges_and_null(days, band, label):
    assert (days_band(days), days_label(days)) == (band, label)

def test_score_label(): assert [score_label(s) for s in (92, 64, 31)] == ["✅ 92", "⚠ 64", "⛔ 31"]

def test_risk_badge_has_word_and_exact_colours():
    h = risk_badge_html("HIGH"); assert "#dc2626" in h and "HIGH" in h and "#ffffff" in h.lower()
    m = risk_badge_html("MEDIUM"); assert "#f59e0b" in m and "#111827" in m

def test_diff_html_escapes_values():
    out = diff_html([{"field": "tag:<b>", "before": "<img src=x onerror=alert(1)>", "after": "**bold**"}])
    assert "<img" not in out and "&lt;img" in out and "**bold**" in out and "&lt;b&gt;" in out
    assert "#ef4444" in out and "#22c55e" in out

def test_tool_step_title():
    assert tool_step_title({"name": "rds.x", "duration_ms": 1200, "status": "ok"}) == "🔧 rds.x · 1.2 s · ok"
    assert tool_step_title({"name": "rds.x", "status": None}) == "🔧 rds.x · ⏳ running"

def test_cr_title_rollback():
    assert cr_title({"id": "CR-0150", "change_type": "modify_tags", "database": {"identifier": "db1"},
                     "rollback_of": "CR-0130"}) == "↩ Rollback of CR-0130 · CR-0150 · modify_tags · db1"

def test_plain_error_drifted():
    assert plain_error(ApiError(409, "drifted", "x")).startswith("The database no longer matches")

# test_permissions.py
SAM, PRIYA, ARJUN, RITA = (User(n, n, r) for n, r in [("sam", Role.SAFE_WRITE), ("priya", Role.PRIVILEGED), ("arjun", Role.PRIVILEGED), ("rita", Role.READ)])
cr = lambda **kw: {"id": "CR-1", "status": "pending", "risk_tier": "LOW", "requested_by": "arjun", "change_type": "modify_tags", **kw}

def test_self_approval(): assert can_decide(ARJUN, cr()).reason == "You can't approve your own request"
def test_role_too_low(): assert can_decide(SAM, cr(risk_tier="HIGH")) == Decision(False, "Your role (SAFE-WRITE) can't approve HIGH changes")
def test_read_cannot_approve_low(): assert not can_decide(RITA, cr()).allowed
def test_priya_can_approve(): assert can_decide(PRIYA, cr(risk_tier="HIGH")) == Decision(True, None)
def test_withdraw_only_requester(): assert can_withdraw(ARJUN, cr()) and not can_withdraw(PRIYA, cr())
def test_cancel_scheduled_privileged_only():
    assert can_cancel(PRIYA, cr(status="scheduled")) and not can_cancel(SAM, cr(status="scheduled"))
def test_rollback_rules():
    assert can_propose_rollback(SAM, cr(status="succeeded"))
    assert not can_propose_rollback(SAM, cr(status="succeeded", change_type="modify_instance_class"))
    assert not can_propose_rollback(PRIYA, cr(status="succeeded", change_type="restore_test"))

# test_auth.py
def test_authenticate(users_file):
    users = load_users(users_file)
    assert authenticate(users, "priya", "pw") == User("priya", "Priya", Role.PRIVILEGED)
    assert authenticate(users, "priya", "nope") is None and authenticate(users, "mallory", "pw") is None
```

(The `users_file` fixture's password for every user is `"pw"`.)

- [ ] **Step 2: Run the tests to verify they fail.** Run `uv run pytest frontend/tests/test_auth.py frontend/tests/test_format.py frontend/tests/test_permissions.py -v`. Expected: FAIL (import errors).

- [ ] **Step 3: Implement** `auth.py`, `format.py` and `permissions.py`.

- [ ] **Step 4: Run the tests to verify they pass.** Same command. Expected: all pass.

- [ ] **Step 5: Commit.**

```bash
git add -A && git commit -m "feat(frontend): auth, formatting and permission rules"
```

---

### Task 8: App shell: login, navigation, sidebar, theme and fonts

**Files:**
- Create: `frontend/app.py`, `frontend/.streamlit/config.toml`, `frontend/static/fonts/` (font files), `frontend/src/dbops_frontend/{clients,navigation}.py`, `frontend/src/dbops_frontend/views/{__init__,common,login}.py`, placeholder `render()` functions in `views/{health,chat,approvals,audit_reports}.py` that show only the page header
- Test: `frontend/tests/test_shell.py`, `frontend/tests/test_architecture.py`

**Interfaces:**
- Consumes: `load_settings`, `authenticate`, `load_users`, the API classes, `ServiceUnavailable`.
- Produces:
  - Session keys: `st.session_state["user"]: User`. Later tasks own their own keys, all prefixed with the view name (`chat_`, `approvals_`, `audit_`, `health_`).
  - `clients.agent_client() -> AgentApi`, `clients.health_client() -> HealthApi` and `clients.executor_client() -> ExecutorApi`, each built from `load_settings()` and `st.session_state["user"]`.
  - `navigation.approvals_title(pending: int) -> str`: `"Approvals (6)"`, or `"Approvals"` when 0.
  - `navigation.build_navigation(pending: int)`: returns `st.navigation([...])` with `st.Page(render, title=..., icon=..., url_path=...)` in this order: Health (`default=True`, url `health`), Chat (`chat`), Approvals (`approvals`), Audit & reports (`audit`). Page objects are kept in `st.session_state["pages"]` keyed by `"health" | "chat" | "approvals" | "audit"` so views can `st.switch_page(st.session_state["pages"]["approvals"])`.
  - `views.common.service_banner(service: str) -> None`: `st.error(f"{service} unavailable, showing nothing rather than stale data")`.
  - `views.common.risk_badge(tier) -> None`: renders `risk_badge_html` via `st.markdown(..., unsafe_allow_html=True)`.
  - `views.common.sidebar_user() -> None`: shows `"**{display_name}** · {role}"` and a "Log out" button that clears session state and reruns.
  - Test fixture `view_app(module: str, *, logged_in: str, base_url: str | None = None, agent_url: str | None = None, health_url: str | None = None, session: dict | None = None) -> AppTest` in `frontend/tests/conftest.py`. It builds `AppTest.from_function` around `from dbops_frontend.views.<module> import render; render()` (`default_timeout=10`). It starts a `mock_server()` when no `base_url` is given, applies `frontend_env`, then overrides `AGENT_API_URL` or `HEALTH_URL` when given. It pre-sets `session_state["user"]` and every key in `session`, and exposes the server URL as `at.base_url`. The page fixtures in Tasks 9–12 are thin wrappers: `health_view(**kw)`, `chat_view(**kw)`, `approvals_view(view=None, confirm=None, **kw)` (setting `approvals_view` and `approvals_confirm`) and `audit_view(view=None, **kw)` (setting `audit_view`).
  - `app.py` flow: `st.set_page_config(page_title="DB Ops", layout="wide")`. If there's no user, call `views.login.render()` and stop. Otherwise call `sidebar_user()`, fetch the pending count (`agent_client().list_change_requests(["pending"])`, cached for 5 s with `st.cache_data(ttl=5)` keyed on the username; on `ServiceUnavailable` the count is 0), then `build_navigation(count).run()`. Inject one `<style>` with `font-variant-numeric: tabular-nums` on `[data-testid="stMetricValue"]` and on tables.

`config.toml`:
```toml
[server]
enableStaticServing = true

[theme]
font = "Inter, sans-serif"
headingFont = "'Space Grotesk', sans-serif"
codeFont = "'JetBrains Mono', monospace"
# one [[theme.fontFaces]] entry per file: family, url = "app/static/fonts/<file>", weight
```
Font files (latin, woff2) come from `https://cdn.jsdelivr.net/npm/@fontsource/<pkg>@5.1.0/files/<file>`: `inter-latin-400-normal.woff2`, `inter-latin-600-normal.woff2`, `space-grotesk-latin-500-normal.woff2`, `space-grotesk-latin-700-normal.woff2`, `jetbrains-mono-latin-400-normal.woff2`.

- [ ] **Step 1: Download the fonts.** For each file, run `curl -fsSL -o frontend/static/fonts/<file> <url>`, then `file frontend/static/fonts/*`. Expected: every file reports `Web Open Font Format (Version 2)`.

- [ ] **Step 2: Write the failing tests.**

```python
# test_shell.py   (AppTest.from_file(str(FRONTEND / "app.py")), default_timeout=10)
def test_login_required(shell):
    at = shell(); at.run()
    assert [t.label for t in at.text_input] == ["Username", "Password"]

def test_wrong_password(shell):
    at = shell(); at.run()
    at.text_input[0].input("priya"); at.text_input[1].input("bad"); at.button[0].click().run()
    assert at.error[0].value == "Wrong username or password"

def test_login_shows_health_and_user(shell):
    at = shell(); at.run()
    at.text_input[0].input("priya"); at.text_input[1].input("pw"); at.button[0].click().run()
    assert at.session_state["user"].username == "priya"
    assert any("Priya" in m.value and "PRIVILEGED" in m.value for m in at.sidebar.markdown)
    assert at.header[0].value == "Health"

def test_logout(shell):
    at = shell(logged_in="priya"); at.run()
    next(b for b in at.sidebar.button if b.label == "Log out").click().run()
    assert "user" not in at.session_state

def test_approvals_title(): assert (approvals_title(6), approvals_title(0)) == ("Approvals (6)", "Approvals")

def test_theme_fonts_configured():
    cfg = tomllib.loads((FRONTEND / ".streamlit/config.toml").read_text())
    assert "Space Grotesk" in cfg["theme"]["headingFont"] and "JetBrains Mono" in cfg["theme"]["codeFont"]
    for face in cfg["theme"]["fontFaces"]:
        assert (FRONTEND / face["url"].removeprefix("app/")).stat().st_size > 1000

# test_architecture.py
def test_only_approvals_view_uses_executor():
    src = FRONTEND / "src/dbops_frontend"
    users = {p.relative_to(src).as_posix() for p in src.rglob("*.py")
             if re.search(r"executor_client|ExecutorApi", p.read_text())}
    assert users <= {"clients.py", "api/__init__.py", "api/executor.py", "views/approvals.py"}
    assert "views/approvals.py" in users

def test_chat_view_has_no_execute_control():
    text = (FRONTEND / "src/dbops_frontend/views/chat.py").read_text()
    assert not re.search(r"approve|execute", text, re.I)
```

The `shell` fixture (in conftest) starts `mock_server()`, applies `frontend_env`, sets `DBOPS_USERS_FILE` to the `users_file` fixture, returns `AppTest.from_file(...)`, and with `logged_in=` pre-sets `session_state["user"]`. `test_only_approvals_view_uses_executor` fails until Task 11 adds `views/approvals.py` usage; mark it `xfail(strict=True)` in this task and remove the mark in Task 11.

- [ ] **Step 3: Run the tests to verify they fail.** Run `uv run pytest frontend/tests/test_shell.py frontend/tests/test_architecture.py -v`. Expected: FAIL (`app.py` missing); the architecture test XFAILs.

- [ ] **Step 4: Implement** `config.toml`, `clients.py`, `navigation.py`, `views/common.py`, `views/login.py` (an `st.form` with the "Username" and "Password" inputs and a "Log in" submit button; on failure `st.error("Wrong username or password")`), the placeholder views (each `st.header("<Title>")`) and `app.py`.

- [ ] **Step 5: Run the tests to verify they pass.** Same command. Expected: pass (architecture: xfail).

- [ ] **Step 6: Check it by eye.** Run `cd frontend && DBOPS_USERS_FILE=../config/users.yaml AGENT_API_URL=http://localhost:8000 EXECUTOR_URL=http://localhost:8000 HEALTH_URL=http://localhost:8000 DBOPS_SERVICE_TOKEN=dev uv run streamlit run app.py`, with the mock running in another terminal: `cd mock_backend && DBOPS_USERS_FILE=../config/users.yaml DBOPS_SERVICE_TOKEN=dev uv run uvicorn mock_backend.app:build_from_env --factory --port 8000`. Log in as priya / dbops-demo. Expected: the sidebar lists Health, Chat, Approvals (6) and Audit & reports; headings are in Space Grotesk.

- [ ] **Step 7: Commit.**

```bash
git add -A && git commit -m "feat(frontend): app shell with login, navigation, theme fonts"
```

---

### Task 9: Health page

**Files:**
- Modify: `frontend/src/dbops_frontend/views/health.py`
- Test: `frontend/tests/test_health_view.py`

**Interfaces:**
- Consumes: `health_client()`, `score_label`, `score_band`, `days_label`, `days_band`, `BAND_COLOR`, `service_banner`, `plain_error`, `st.session_state["pages"]`.
- Produces:
  - `fleet_frame(databases: list[dict]) -> pd.DataFrame` with columns exactly `["Database", "Engine", "Class", "Score", "Days to full", "Alarms"]`. Score and Days to full hold the label strings, and the order is by score ascending (worst first).
  - `style_fleet(df, databases) -> pandas Styler`, which applies `background-color` from `BAND_COLOR` (with dark text for amber) to the Score and Days to full cells.
  - `render_alarms(identifier: str) -> None`, an expander titled `f"Alarms · {identifier}"`, expanded, listing alarms (`"⛔ ALARM"`, `"✅ OK"`, `"⚠ INSUFFICIENT_DATA"` prefix plus name, metric and threshold), or `"No open alarms."`.
  - `render() -> None`, top to bottom:
    - `st.header("Health")`
    - four `st.metric`s labelled "Databases", "Unhealthy", "< 30 days to full" and "Open alarms"
    - a caption `f"Data as of {as_of} (health-analytics)"`
    - "Refresh" and "Generate health report" buttons
    - env and engine `st.selectbox`es, with "All" first and options taken from the unfiltered fleet
    - `st.dataframe(style_fleet(...), on_select="rerun", selection_mode="single-row", hide_index=True, key="health_table")`, then `render_alarms` for the selected row
    - empty filtered result: `st.info("No databases match these filters.")`; empty fleet: `st.info("No databases yet.")`
    - on `ServiceUnavailable`: `service_banner("health-analytics")` and nothing else below the header
    - Generate: start the report, then `st.session_state["audit_view"] = "Reports"`, `st.success("Health report is generating.")` and a "Open Reports" button that calls `st.switch_page(pages["audit"])`
    - `ApiError` from Refresh: `st.info(plain_error(e))`

- [ ] **Step 1: Write the failing tests.** (`health_view` wraps `view_app("health", logged_in="rita", ...)` from Task 8.)

```python
def test_summary_metrics(health_view):
    at = health_view(); at.run()
    assert {m.label: m.value for m in at.metric} == {"Databases": "14", "Unhealthy": "2", "< 30 days to full": "3", "Open alarms": ANY}
    assert at.caption[0].value.startswith("Data as of ") and at.caption[0].value.endswith("(health-analytics)")

def test_fleet_frame_columns_and_order():
    df = fleet_frame([{"identifier": "a", "engine": "e", "instance_class": "c", "health_score": 90, "storage": {"days_to_full": None}, "open_alarms": 0},
                      {"identifier": "b", "engine": "e", "instance_class": "c", "health_score": 40, "storage": {"days_to_full": 9}, "open_alarms": 2}])
    assert list(df.columns) == ["Database", "Engine", "Class", "Score", "Days to full", "Alarms"]
    assert df["Database"].tolist() == ["b", "a"] and df["Days to full"].tolist() == ["⛔ 9 d", "—"]

def test_env_filter(health_view):
    at = health_view(); at.run()
    at.selectbox(key="health_env").select("prod").run()
    assert set(at.dataframe[0].value["Database"]) >= {"prod-orders-db"} and "legacy-reports-db" not in set(at.dataframe[0].value["Database"])

def test_unreachable_banner(health_view):
    at = health_view(health_url="http://127.0.0.1:9"); at.run()
    assert at.error[0].value == "health-analytics unavailable, showing nothing rather than stale data"
    assert len(at.metric) == 0

def test_refresh_twice_plain_words(health_view):
    at = health_view(); at.run()
    at.button(key="health_refresh").click().run(); at.button(key="health_refresh").click().run()
    assert at.info[0].value == "A refresh was already requested in the last minute."

def test_generate_report_points_to_reports(health_view):
    at = health_view(); at.run(); at.button(key="health_report").click().run()
    assert at.session_state["audit_view"] == "Reports" and at.success[0].value == "Health report is generating."

def test_alarms_panel(alarms_view):  # from_function around render_alarms("prod-orders-db")
    at = alarms_view(); at.run()
    assert at.expander[0].label == "Alarms · prod-orders-db" and "⛔ ALARM" in at.markdown[0].value
```

- [ ] **Step 2: Run the tests to verify they fail.** Run `uv run pytest frontend/tests/test_health_view.py -v`. Expected: FAIL.

- [ ] **Step 3: Implement** `views/health.py` with widget keys `health_env`, `health_engine`, `health_refresh`, `health_report` and `health_table`.

- [ ] **Step 4: Run the tests to verify they pass.** Same command. Expected: 7 passed.

- [ ] **Step 5: Commit.**

```bash
git add -A && git commit -m "feat(frontend): Health page"
```

---

### Task 10: Chat page

**Files:**
- Create: `frontend/src/dbops_frontend/chat_state.py`
- Modify: `frontend/src/dbops_frontend/views/chat.py`
- Test: `frontend/tests/test_chat_state.py`, `frontend/tests/test_chat_view.py`

**Interfaces:**
- Consumes: `agent_client()`, `tool_step_title`, `risk_badge_html`, `plain_error`, `ServiceUnavailable`, `ApiError`, `st.session_state["pages"]`.
- Produces:
  - `ActiveRun(run_id: str, last_seq: int = 0, events: list[dict] = [])` (dataclass, `field(default_factory=list)`).
  - `ToolStep(tool_call_id, name, args, status: str | None, result, truncated: bool, duration_ms: int | None)`.
  - `Turn(role: str, text: str, tool_steps: list[ToolStep], proposals: list[dict], status: str, error: str | None)`. `status` is one of `running`, `completed`, `failed`, `cancelled`.
  - `apply_events(run: ActiveRun, events: list[dict]) -> None`: appends only events with `seq > run.last_seq`, in `seq` order, and updates `last_seq`.
  - `build_turn(events: list[dict]) -> Turn`: joins `text_delta` texts; pairs `tool_call_started` and `tool_call_finished` by `tool_call_id`, keeping start order; `change_proposed` events become proposals; the terminal event sets status and error.
  - `turn_from_message(msg: dict) -> Turn`.
  - Session keys: `chat_conv_id: str | None`, `chat_runs: dict[conv_id, ActiveRun]`, `chat_unreachable: bool`.
  - `render() -> None`:
    - In the sidebar, a "+ New chat" button plus one button per conversation (title, most recent first; the current one is `type="primary"`).
    - On first render after login, select the most recently updated conversation and adopt any running run from `running_runs(conv_id)`.
    - Draw history via `turn_from_message`.
    - Draw the in-progress turn inside `@st.fragment(run_every=0.5)`, which polls `get_events`, applies them, redraws, and calls `st.rerun()` once `run_status != "running"` (this pops `chat_runs[conv]`).
    - `st.chat_input("Ask about your databases", disabled=<run active>)`. On submit, create the conversation if there isn't one, call `start_run` exactly once, and store the `ActiveRun`. A 409 shows `plain_error`.
    - A "Stop" button inside the fragment calls `cancel_run`.
    - On `ServiceUnavailable` while polling: `st.warning("agent-api unreachable, retrying…")`; keep the `ActiveRun`.
    - On `ServiceUnavailable` on load: `service_banner("agent-api")`.
  - Turn drawing (`render_turn(turn)`): `st.chat_message(role)`, then `st.markdown(text)`. Each tool step is `st.expander(tool_step_title(step), expanded=False)` with "Arguments" `st.json(args)` and "Result" `st.json(result)`, plus `st.caption("Result truncated to 20 KB")` when truncated. Each proposal renders `f"📝 Proposed {change_type} → {change_request_id}"`, the risk badge, and a button "Open in Approvals" (key `chat_open_<cr_id>`) that sets `st.session_state["approvals_focus"] = cr_id`, sets `st.session_state["approvals_view"] = "Pending"` and switches to the Approvals page. A failed turn shows `st.error(error)`; a cancelled one shows `st.caption("⏹ Stopped")`.

- [ ] **Step 1: Write the failing tests.**

```python
# test_chat_state.py
E = [{"seq": 1, "type": "run_started"}, {"seq": 2, "type": "text_delta", "text": "Hello "},
     {"seq": 3, "type": "tool_call_started", "tool_call_id": "t1", "name": "rds.x", "args": {"a": 1}},
     {"seq": 4, "type": "text_delta", "text": "world"},
     {"seq": 5, "type": "tool_call_finished", "tool_call_id": "t1", "status": "ok", "result": {}, "duration_ms": 1200},
     {"seq": 6, "type": "change_proposed", "change_request_id": "CR-0150", "change_type": "create_snapshot", "risk_tier": "LOW"},
     {"seq": 7, "type": "run_completed"}]

def test_apply_events_ignores_already_seen_seq():
    run = ActiveRun("r"); apply_events(run, E[:4]); apply_events(run, E[2:])
    assert [e["seq"] for e in run.events] == [1, 2, 3, 4, 5, 6, 7] and run.last_seq == 7

def test_apply_events_sorts_out_of_order_page():
    run = ActiveRun("r"); apply_events(run, [E[1], E[0]]); assert [e["seq"] for e in run.events] == [1, 2]

def test_build_turn():
    t = build_turn(E)
    assert (t.text, t.status, len(t.tool_steps), t.tool_steps[0].duration_ms) == ("Hello world", "completed", 1, 1200)
    assert t.proposals[0]["change_request_id"] == "CR-0150"

def test_build_turn_running_and_failed():
    assert build_turn(E[:3]).status == "running" and build_turn(E[:3]).tool_steps[0].status is None
    t = build_turn(E[:2] + [{"seq": 3, "type": "run_failed", "error": "boom"}])
    assert (t.status, t.error) == ("failed", "boom")

# test_chat_view.py  (chat_view wraps view_app("chat", ...); its mock runs at time_scale=0 unless base_url is given)
def test_send_message_completes_turn(chat_view):
    at = chat_view(logged_in="sam"); at.run()
    at.chat_input[0].set_value("take a snapshot of prod-orders-db").run(); at.run()
    assert any(e.label.startswith("🔧 ") for e in at.expander)
    assert any(m.value.startswith("📝 Proposed create_snapshot → CR-") for m in at.markdown)
    assert not at.chat_input[0].disabled

def test_history_reloads_most_recent_conversation(chat_view, mock_server):
    url = mock_server()
    at = chat_view(logged_in="sam", base_url=url); at.run(); at.chat_input[0].set_value("hello there").run(); at.run()
    fresh = chat_view(logged_in="sam", base_url=url); fresh.run()
    assert any("hello there" in m.value for m in fresh.markdown)

def test_resume_running_run_after_refresh(chat_view, mock_server):
    url = mock_server(time_scale=1.0)
    agent = AgentApi(url, "priya", "test-token")
    conv = agent.create_conversation(); run = agent.start_run(conv, "how is the fleet?")
    at = chat_view(logged_in="priya", base_url=url); at.run()
    assert at.session_state["chat_runs"][conv].run_id == run and at.chat_input[0].disabled

def test_chat_keeps_run_when_agent_api_unreachable(chat_view, mock_server, monkeypatch):
    url = mock_server(time_scale=1.0)
    at = chat_view(logged_in="priya", base_url=url); at.run()
    at.chat_input[0].set_value("how is the fleet?").run()
    monkeypatch.setenv("AGENT_API_URL", "http://127.0.0.1:9"); at.run()
    assert at.warning[0].value == "agent-api unreachable, retrying…"
    conv = at.session_state["chat_conv_id"]; assert at.session_state["chat_runs"][conv].run_id
```

- [ ] **Step 2: Run the tests to verify they fail.** Run `uv run pytest frontend/tests/test_chat_state.py frontend/tests/test_chat_view.py -v`. Expected: FAIL.

- [ ] **Step 3: Implement** `chat_state.py`. `apply_events` is the algorithm the tests pin:

```python
def apply_events(run: ActiveRun, events: list[dict]) -> None:
    for e in sorted(events, key=lambda e: e["seq"]):
        if e["seq"] > run.last_seq:
            run.events.append(e)
            run.last_seq = e["seq"]
```

- [ ] **Step 4: Implement `views/chat.py`** as specified in Interfaces. Keep the words "approve" and "execute" out of the module (the Task 8 architecture test checks this).

- [ ] **Step 5: Run the tests to verify they pass.** Run `uv run pytest frontend/tests -v`. Expected: all pass (architecture test still xfail).

- [ ] **Step 6: Commit.**

```bash
git add -A && git commit -m "feat(frontend): Chat page with rerun-safe run polling"
```

---

### Task 11: Approvals page

**Files:**
- Modify: `frontend/src/dbops_frontend/views/approvals.py`, `frontend/tests/test_architecture.py` (remove the xfail)
- Test: `frontend/tests/test_approvals_view.py`

**Interfaces:**
- Consumes: `agent_client()`, `executor_client()`, `can_decide`, `can_withdraw`, `can_cancel`, `can_retry_cleanup`, `can_propose_rollback`, `cr_title`, `diff_html`, `status_label`, `risk_badge`, `plain_error`, `service_banner`.
- Produces:
  - `VIEWS = {"Pending": ["pending"], "In progress": ["approved", "executing", "scheduled", "failed_cleanup_pending"], "Done": ["succeeded", "failed", "rejected"]}`.
  - `order_in_progress(crs: list[dict]) -> list[dict]`: `failed_cleanup_pending` first, then the rest by `created_at` descending.
  - Session keys: `approvals_view` (bound to `st.segmented_control("View", list(VIEWS), key="approvals_view")`, default "Pending"), `approvals_focus: str | None` (expands that CR's expander), `approvals_confirm: dict | None` (`{cr_id, mode, override_reason}`), `approvals_flash: str | None` (a plain-words message shown once at the top).
  - `render_confirm(cr: dict, mode: str, override_reason: str | None) -> None`: the body of `@st.dialog("Confirm execution")`. It shows `"**Database:** {identifier}"`, `"**Change:** {summary}"` and `"**Mode:** Apply now"` or `"Schedule for next window"`, and has "Execute" (calls `executor.approve`) and "Cancel" buttons.
  - `render() -> None`:
    - `st.header("Approvals")`, then the segmented control.
    - One `st.expander(f"{cr_title(cr)} · {cr['risk_tier']}", expanded=cr["id"] == focus)` per CR, with the risk badge and the caption `f"Requested by {requested_by} via {requested_via} at {created_at}"`.
    - Inside: `diff_html`, then `impact`, then "Rollback plan" as a bullet list.
    - HIGH only: `"✅ Inside window"` or `"⚠ Outside window"`, plus the window and `next_start`. A radio "Execution" with ["Schedule for next window", "Apply now"]. When "Apply now" is chosen and `inside_now` is false, a text input "Override reason".
    - "Approve & execute" (key `approve_<id>`) and "Reject" (key `reject_<id>`), each `disabled=not decision.allowed`, with `st.caption(decision.reason)` when disabled. Reject opens a "Reject reason" text input and a "Confirm reject" button.
    - "Withdraw" (key `withdraw_<id>`) only when `can_withdraw`.
    - In progress: the view body is `@st.fragment(run_every=5)`. Per CR there's a step tracker: one line per progress step, `"✅ step"` done, `"⏳ step"` active, `"▫️ step"` pending, `"⛔ step"` failed. `failed_cleanup_pending` gets `st.error("⛔ Cleanup failed: the temporary restore instance is still running")` and, for PRIVILEGED users, "Retry cleanup" (key `retry_<id>`). `scheduled` shows `f"Next window starts {next_start}"` and, for PRIVILEGED users, "Cancel change" (key `cancel_<id>`), a reason input and a "Confirm cancel" button.
    - Done: `status_label(status)` per CR. "Propose rollback" (key `rollback_<id>`) when `can_propose_rollback`. Clicking it calls `propose_rollback`, then sets `approvals_focus` to the new id and `approvals_view` to "Pending", and reruns.
    - Every `ApiError` sets `approvals_flash = plain_error(e)` and reruns, so the list is re-fetched.
    - Empty Pending: `st.info("No pending approvals.")`.

- [ ] **Step 1: Write the failing tests.** (The `approvals_view` fixture works like `chat_view`, at time_scale=0.)

```python
def pending_titles(at): return [e.label for e in at.expander]

def test_pending_list_titles(approvals_view):
    at = approvals_view(logged_in="sam"); at.run()
    assert "CR-0143 · modify_instance_class · prod-orders-db · HIGH" in pending_titles(at)
    assert len(at.expander) == 6

def test_role_too_low_disables_with_reason(approvals_view):
    at = approvals_view(logged_in="sam"); at.run()
    assert at.button(key="approve_CR-0143").disabled
    assert "Your role (SAFE-WRITE) can't approve HIGH changes" in [c.value for c in at.caption]

def test_own_request_disabled_and_withdraw_shown(approvals_view):
    at = approvals_view(logged_in="arjun"); at.run()
    assert at.button(key="approve_CR-0143").disabled
    assert "You can't approve your own request" in [c.value for c in at.caption]
    assert at.button(key="withdraw_CR-0143") and not [b for b in at.button if b.key == "withdraw_CR-0140"]

def test_override_reason_only_when_apply_now_outside_window(approvals_view):
    at = approvals_view(logged_in="priya"); at.run()
    assert not [t for t in at.text_input if t.key == "override_CR-0143"]
    at.radio(key="mode_CR-0143").set_value("Apply now").run()
    assert at.text_input(key="override_CR-0143")
    at.radio(key="mode_CR-0144").set_value("Apply now").run()
    assert not [t for t in at.text_input if t.key == "override_CR-0144"]  # inside window

def test_approve_click_opens_confirm(approvals_view):
    at = approvals_view(logged_in="priya"); at.run()
    at.button(key="approve_CR-0140").click().run()
    assert at.session_state["approvals_confirm"] == {"cr_id": "CR-0140", "mode": "now", "override_reason": None}

def test_confirm_body_repeats_details(confirm_view):  # from_function around render_confirm(<CR-0143 dict>, "now", "INC-77")
    at = confirm_view(); at.run()
    text = " ".join(m.value for m in at.markdown)
    assert "prod-orders-db" in text and "Apply now" in text

def test_drifted_shown_in_plain_words(approvals_view):
    at = approvals_view(logged_in="priya", confirm={"cr_id": "CR-0139", "mode": "now", "override_reason": None}); at.run()
    at.button(key="confirm_execute").click().run()
    assert at.session_state["approvals_flash"].startswith("The database no longer matches")

def test_invalid_state_shows_plain_words_and_refreshes(approvals_view):
    at = approvals_view(logged_in="priya"); at.run()
    ExecutorApi(at.base_url, "sam", "test-token").withdraw("CR-0140")   # another user acts after page load
    at.button(key="reject_CR-0140").click().run()
    at.text_input(key="reject_reason_CR-0140").input("no").run(); at.button(key="confirm_reject_CR-0140").click().run()
    assert at.warning[0].value == "This request changed state since the page loaded. The list has been refreshed."
    assert "CR-0140" not in " ".join(pending_titles(at))

def test_in_progress_cleanup_first_and_retry_privileged_only(approvals_view):
    at = approvals_view(logged_in="priya", view="In progress"); at.run()
    assert at.expander[0].label.startswith("CR-0136")
    assert at.button(key="retry_CR-0136") and at.button(key="cancel_CR-0135")
    sam = approvals_view(logged_in="sam", view="In progress"); sam.run()
    assert not [b for b in sam.button if b.key in {"retry_CR-0136", "cancel_CR-0135"}]

def test_propose_rollback_switches_to_pending_with_focus(approvals_view):
    at = approvals_view(logged_in="sam", view="Done"); at.run()
    at.button(key="rollback_CR-0130").click().run()
    assert at.session_state["approvals_view"] == "Pending"
    focus = at.session_state["approvals_focus"]
    assert any(e.label.startswith(f"↩ Rollback of CR-0130 · {focus}") for e in at.expander)

def test_read_user_sees_everything_disabled(approvals_view):
    at = approvals_view(logged_in="rita"); at.run()
    assert all(b.disabled for b in at.button if b.key and b.key.startswith(("approve_", "reject_")))
```

Flash messages render via `st.warning`. `confirm_view` builds `AppTest.from_function` around `render_confirm` with CR-0143 fetched from a live mock.

- [ ] **Step 2: Run the tests to verify they fail.** Run `uv run pytest frontend/tests/test_approvals_view.py -v`. Expected: FAIL.

- [ ] **Step 3: Implement `views/approvals.py`** as specified. Widget keys: `mode_<id>`, `override_<id>`, `approve_<id>`, `reject_<id>`, `reject_reason_<id>`, `confirm_reject_<id>`, `withdraw_<id>`, `retry_<id>`, `cancel_<id>`, `cancel_reason_<id>`, `confirm_cancel_<id>`, `rollback_<id>`, `confirm_execute`. The approve button stores `approvals_confirm` and reruns. When `approvals_confirm` is set, `render` opens the dialog, which calls `render_confirm`. A successful execute clears it, sets `approvals_view` to "In progress" and reruns.

- [ ] **Step 4: Remove the xfail** in `test_architecture.py`.

- [ ] **Step 5: Run the tests to verify they pass.** Run `uv run pytest frontend/tests -v`. Expected: all pass, including the architecture test.

- [ ] **Step 6: Commit.**

```bash
git add -A && git commit -m "feat(frontend): Approvals page with confirm, rollback, cancel, retry cleanup"
```

---

### Task 12: Audit & reports page

**Files:**
- Modify: `frontend/src/dbops_frontend/views/audit_reports.py`
- Test: `frontend/tests/test_audit_reports_view.py`

**Interfaces:**
- Consumes: `agent_client()`, `service_banner`, `ServiceUnavailable`.
- Produces:
  - `audit_frame(items: list[dict]) -> pd.DataFrame` with columns exactly `["Time", "User", "Action / tool", "Database", "Result", "CR"]`. "Action / tool" shows `tool` when set, else `action`. "Result" is `"✅ ok"`, `"⛔ error"` or `"✋ denied"`.
  - `report_status_label(r) -> str`: `"⏳ generating…"`, `"✅ ready"`, or `f"⛔ failed: {error}"`.
  - `render_reports_table(reports: list[dict]) -> None`: one row per report (title, kind, status label, `created_at`, `created_by`), plus one `st.download_button(f"Download {fmt.upper()}", key=f"dl_{id}_{fmt}")` per format when ready. Empty: `st.info("No reports yet.")`.
  - Session keys: `audit_view` (segmented control "View", ["Audit log", "Reports"], default "Audit log"), `audit_cursor_stack: list[str | None]` (paging).
  - `render()`:
    - `st.header("Audit & reports")`.
    - Audit log: selectboxes "User", "Tool" and "Database" (keys `audit_user`, `audit_tool`, `audit_db`), each with an "All" option and options from `audit_facets()`. A `st.date_input("Date range", value=(today - 7 days, today), key="audit_dates")`. A `st.dataframe(audit_frame(...))` at 50 rows per page, with "Previous" and "Next page" buttons (keys `audit_prev`, `audit_next`) driven by the cursor stack. "Export CSV" (key `audit_export`) calls `start_change_history_report(filters)` and then sets `audit_view` to "Reports".
    - Reports: the body is `st.fragment(..., run_every=3 if any_generating else None)`. When nothing is generating any more, the fragment calls `st.rerun()` to stop polling.
    - Filters are sent as ISO `from` (start of day UTC) and `to` (end of day UTC).

- [ ] **Step 1: Write the failing tests.**

```python
def test_default_date_range_is_last_7_days(audit_view):
    at = audit_view(logged_in="rita"); at.run()
    start, end = at.date_input(key="audit_dates").value
    assert (end - start).days == 7 and end == date.today()

def test_filter_by_user(audit_view):
    at = audit_view(logged_in="rita"); at.run()
    at.selectbox(key="audit_user").select("sam").run()
    assert set(at.dataframe[0].value["User"]) == {"sam"}

def test_audit_frame_columns():
    df = audit_frame([{"id": "a1", "ts": "t", "user": "sam", "action": "chat.tool_call", "tool": "rds.x",
                       "database": "db", "result": "ok", "change_request_id": None, "detail": ""}])
    assert list(df.columns) == ["Time", "User", "Action / tool", "Database", "Result", "CR"]
    assert df.iloc[0]["Action / tool"] == "rds.x" and df.iloc[0]["Result"] == "✅ ok"

def test_export_shows_generating_at_top(audit_view, mock_server):
    at = audit_view(logged_in="rita", base_url=mock_server(time_scale=1.0)); at.run()
    at.button(key="audit_export").click().run()
    assert at.session_state["audit_view"] == "Reports"
    first_report_line = next(m.value for m in at.markdown if any(s in m.value for s in ("⏳", "✅ ready", "⛔ failed")))
    assert "⏳ generating…" in first_report_line

def test_ready_report_has_download_buttons(audit_view):
    at = audit_view(logged_in="rita", view="Reports"); at.run()
    assert [k for k in (w.key for w in at.get("download_button")) if k.endswith("_csv")]

def test_failed_report_shows_error(audit_view):
    at = audit_view(logged_in="rita", view="Reports"); at.run()
    assert any("⛔ failed: Timed out reading CloudWatch" in m.value for m in at.markdown)

def test_empty_reports(reports_table_view):  # from_function around render_reports_table([])
    at = reports_table_view(); at.run(); assert at.info[0].value == "No reports yet."

def test_unreachable_banner(audit_view):
    at = audit_view(logged_in="rita", agent_url="http://127.0.0.1:9"); at.run()
    assert at.error[0].value == "agent-api unavailable, showing nothing rather than stale data"
```

- [ ] **Step 2: Run the tests to verify they fail.** Run `uv run pytest frontend/tests/test_audit_reports_view.py -v`. Expected: FAIL.

- [ ] **Step 3: Implement `views/audit_reports.py`** as specified. Render reports as one `st.container(border=True)` per report, holding a markdown line with the title and status label and then the download buttons. That keeps the download buttons inline, which a dataframe can't do.

- [ ] **Step 4: Run the tests to verify they pass.** Run `uv run pytest frontend/tests -v`. Expected: all pass.

- [ ] **Step 5: Commit.**

```bash
git add -A && git commit -m "feat(frontend): Audit & reports page"
```

---

### Task 13: Docker Compose, README and end-to-end smoke check

**Files:**
- Create: `mock_backend/Dockerfile`, `frontend/Dockerfile`, `docker-compose.yml`, `README.md`, `.env.example`

**Interfaces:**
- Consumes: `build_from_env` (Task 2) and `frontend/app.py` (Task 8).
- Produces:
  - Compose services `mock-backend` and `frontend`. Both mount `./config/users.yaml:/config/users.yaml:ro` and read `DBOPS_SERVICE_TOKEN` from `.env`.
  - `mock-backend`: build context `.`, `expose: ["8000"]` and no `ports`. Healthcheck: `python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/healthz')"`.
  - `frontend`: `ports: ["127.0.0.1:8501:8501"]`. The three URLs are `http://mock-backend:8000`. It has `depends_on: {mock-backend: {condition: service_healthy}}`.
  - Images are `python:3.12-slim`, with uv copied from `ghcr.io/astral-sh/uv:0.9` and members installed with `uv pip install --system ./<member>`. The frontend workdir is `/app/frontend` so `.streamlit/` and `static/` resolve, and its command is `streamlit run app.py --server.port 8501 --server.address 0.0.0.0`.
  - README covers: what part 1 is, running locally (compose and bare uv), the demo users and the `dbops-demo` password, the env vars, how to run the tests, and a link to the spec.

- [ ] **Step 1: Write the Dockerfiles, compose file, `.env.example`** (`DBOPS_SERVICE_TOKEN=change-me`, `MOCK_TIME_SCALE=1.0`) **and the README.**

- [ ] **Step 2: Build and start the stack.** Run `cp .env.example .env && docker compose up --build -d && docker compose ps`. Expected: both services are `running`, and mock-backend is `healthy`.

- [ ] **Step 3: Check that only the frontend publishes a port.** Run `curl -fsS http://127.0.0.1:8501/_stcore/health; docker compose port mock-backend 8000`. Expected: `ok`, then an error or empty output for the mock port.

- [ ] **Step 4: Run the demo path by hand** at http://127.0.0.1:8501 and record what you see:
  1. As rita: Health shows 14 databases and red `⛔` scores for prod-orders-db and legacy-reports-db. Approvals buttons are disabled with reasons.
  2. As sam: Chat "take a snapshot of prod-orders-db" streams, shows a collapsed `🔧` step and `📝 Proposed create_snapshot → CR-0145`, and "Open in Approvals" opens it expanded.
  3. As arjun: CR-0143 shows "You can't approve your own request".
  4. As priya: approve CR-0143 with "Apply now". The override reason is required. Confirm, and the In progress tracker reaches `✅ available`.
  5. As sam: in Done, Propose rollback on CR-0130 lands in Pending with "↩ Rollback of CR-0130".
  6. Audit & reports: Export CSV shows `⏳ generating…`, then a Download CSV button.
  7. Run `docker compose stop mock-backend`. Every page shows its "unavailable" banner and none of them crash. Then run `docker compose start mock-backend`.

- [ ] **Step 5: Run the full test suite and lint.** Run `uv run pytest && uv run ruff check .`. Expected: all pass, ruff clean.

- [ ] **Step 6: Commit.**

```bash
git add -A && git commit -m "chore: docker compose stack, README and demo smoke path"
```
