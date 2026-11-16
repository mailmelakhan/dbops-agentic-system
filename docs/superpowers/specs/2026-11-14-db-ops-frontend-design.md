# DB Ops: System Split, API Contracts and Streamlit Frontend: Design

Date: 2026-11-14
Status: Draft, awaiting approval
Scope of this doc: part 1 of 4: overall architecture, API contracts between containers, the Streamlit frontend, and a mock backend. `agent-api`, `executor` and `health-analytics` each get their own design later, built against the contracts here.

---

## 1. Intent and why

DB Ops is an agentic assistant for operating **real AWS databases** (RDS and Aurora, for now). A DBA team asks questions in natural language, sees fleet health at a glance, and makes a small set of AWS-level changes through an explicit approval step.

The immediate audience is **stakeholders in a demo**. Because the demo touches real AWS databases, the system must actually be safe, not just look safe. **Anti-goal:** an unapproved or wrongly approved change to a real database during the demo. A demo where everything else works but this happens is a failure.

Facts about AWS (instance details, metrics, alarms, events) come through the **AWS Agent Toolkit**.

## 2. Goals, non-goals, anti-goals

**Goals**
- Four pages in one Streamlit app: **Health** (default landing page), **Chat**, **Approvals**, **Audit & reports**.
- Chat answers stream in. Each tool call shows as a collapsed step with its arguments and result.
- Changes are only ever *proposed* from chat. Only the Approvals page can execute, and the backend enforces this, not just the UI.
- Three roles: READ, SAFE-WRITE, PRIVILEGED. Users are hardcoded for now.
- Agent runs are not tied to a Streamlit rerun or to the browser tab. A long run survives clicks, page switches and a browser refresh. After a refresh the user logs in again (see §8.1), and the run then picks up where it left off.
- The frontend can be demoed early against a mock backend that implements the same contracts.

**Non-goals (for now)**
- SQL or data changes inside databases. Every change is an AWS control-plane API call.
- Non-database AWS services.
- Real identity (SSO/IAM Identity Center). The user list is hardcoded.
- PDF reports.
- Deep restore validation (connecting to the restored instance and running queries).

**Anti-goals**
- Any execution path that bypasses the Approvals page or the executor's checks.
- Restored copies of production data left running after a restore test.
- Colour as the only signal of status.

## 3. Approaches considered

| | Approach | Trade-off | Outcome |
|---|---|---|---|
| A | Two apps: Streamlit + one backend (agent, health, executor, audit) + Postgres | Simplest. But the process running the LLM also holds write credentials, so only one bug or one prompt injection separates a chat message from a real change. | Rejected |
| **B** | **Split by trust boundary:** `frontend`, `agent-api` (read-only IAM), `health-analytics`, `executor` (the only write credentials), `postgres` | More containers and contracts. "Only Approvals can execute" is enforced by the infrastructure. | **Chosen by user** |
| C | B, plus separate audit and reports services | Overhead with no benefit for a demo | Dropped |

**A change from the original idea:** the background worker that runs the agent lives in `agent-api`, not inside the Streamlit process. Streamlit still starts a run once, keeps the `run_id` in `st.session_state`, and reads events into `st.session_state`. It fetches the events from `agent-api` instead of from a local thread.

**Build order (agreed):** (1) this doc: contracts, frontend and mock backend → (2) `agent-api` → (3) `executor` → (4) `health-analytics`.

## 4. Architecture

```
                 ┌──────────────┐
  browser ─────▶ │  frontend    │  Streamlit; no AWS credentials
                 └──┬───┬────┬──┘
        REST (poll) │   │    │ REST
          ┌─────────┘   │    └──────────────┐
          ▼             ▼                   ▼
   ┌────────────┐ ┌──────────────────┐ ┌────────────┐
   │ agent-api  │ │ health-analytics │ │  executor  │
   │ read-only  │ │ read-only IAM    │ │ write IAM  │
   │ IAM, LLM   │ │ scheduled polling│ │ (only one) │
   └─────┬──────┘ └────────┬─────────┘ └─────┬──────┘
         └────────────┬────┴─────────────────┘
                      ▼
                ┌──────────┐
                │ postgres │  conversations, run events, change requests,
                └──────────┘  audit log, reports, fleet snapshots
```

All services run in one Docker Compose project. Only `frontend` publishes a port to the host.

**Who owns which data (writes):**
- `agent-api`: conversations, runs, run events, change requests (creates them with status `pending` only), and audit rows for chat and tool calls.
- `executor`: change-request status changes after `pending` (approved, rejected, scheduled, executing, succeeded, failed), audit rows for approvals and executions, and restore-test reports.
- `health-analytics`: fleet snapshots, alarms, and health reports.
- Everyone can read. Reads go through each owner's API. The frontend never talks to Postgres.

**IAM (my call, details belong to the later service designs):** `agent-api` and `health-analytics` get read-only RDS/CloudWatch permissions. `executor` gets only the write actions it needs: `rds:CreateDBSnapshot`, `rds:RestoreDBInstanceFromDBSnapshot`, `rds:RestoreDBClusterFromSnapshot`, `rds:ModifyDBInstance`, `rds:AddTagsToResource`, `rds:RemoveTagsFromResource`, `rds:DeleteDBInstance` and `rds:DeleteDBCluster`. The two delete actions are restricted by a condition to resources tagged `dbops:restore-test=true`.

## 5. Identity and roles

**Hardcoded users** live in `config/users.yaml`, which is mounted read-only into every service. Passwords are stored as bcrypt hashes. For the demo there are four users (my call):

| User | Role | Purpose in the demo |
|---|---|---|
| `priya` | PRIVILEGED | approver |
| `arjun` | PRIVILEGED | DBA Lead; requests resizes (two PRIVILEGED users are needed so the no-self-approval rule can be shown) |
| `sam` | SAFE-WRITE | tags and snapshots |
| `rita` | READ | read-only viewer |

**How services know who is calling (my call):** the frontend logs the user in against `users.yaml` and sends two headers on every request: `X-DBOps-User: <username>` and `X-DBOps-Service-Token: <shared secret from env>`. Each service **looks up the role itself** in its own copy of `users.yaml` and never trusts a role sent by the caller. This is a demo-grade setup; replacing it with real identity later only changes this layer.

**Permissions**

| Capability | READ | SAFE-WRITE | PRIVILEGED |
|---|---|---|---|
| View Health, generate a health report | ✔ | ✔ | ✔ |
| Chat with read-only tools | ✔ | ✔ | ✔ |
| Chat: propose LOW-risk changes (tags, snapshot) | ✘ | ✔ | ✔ |
| Chat: propose MEDIUM/HIGH changes (restore test, resize) | ✘ | ✘ | ✔ |
| View Approvals | ✔ (read only) | ✔ | ✔ |
| Approve/execute LOW | ✘ | ✔ | ✔ |
| Approve/execute MEDIUM/HIGH | ✘ | ✘ | ✔ |
| Approve your own request | ✘ | ✘ | ✘ |
| Withdraw your own pending request | n/a | ✔ | ✔ |
| Cancel a `scheduled` HIGH change | ✘ | ✘ | ✔ (any PRIVILEGED user) |
| Propose a rollback of a succeeded change | ✘ | ✔ if the rollback is LOW | ✔ |
| Retry cleanup of a stuck restore test | ✘ | ✘ | ✔ |
| View the audit log and reports, download reports | ✔ | ✔ | ✔ (audit is visible to everyone for the demo) |

`agent-api` enforces the propose rules: the propose tool is not given to the agent, or it refuses, if the caller's role is too low. `executor` enforces the approve and execute rules. The frontend only hides or disables controls to match.

## 6. Changes

All changes are AWS control-plane operations on RDS/Aurora. Four types:

| `change_type` | Risk tier | Diff shows | Rollback plan (generated with the request) | Maintenance-window check |
|---|---|---|---|---|
| `modify_tags` | LOW | tags before → after (added, changed, removed) | re-apply the previous tag set | no |
| `create_snapshot` | LOW | snapshot identifier, source, engine | delete the snapshot | no |
| `restore_test` | MEDIUM | temporary instance identifier, smallest allowed class, source snapshot, estimated cost per hour | delete the temporary instance (also done automatically) | no |
| `modify_instance_class` | HIGH | `instance_class: db.r6g.large → db.r6g.xlarge`, expected downtime | automatic pre-change snapshot `pre-<cr_id>`, then modify back to the old class | **yes** |

**Maintenance window (HIGH only).** The executor reads the instance's `PreferredMaintenanceWindow`. The approver chooses one of:
- **Schedule for next window.** The executor sends the change with `ApplyImmediately=false`, and the CR becomes `scheduled` until AWS applies it.
- **Apply now.** Allowed without extra input if the current time is inside the window. Outside the window, it needs a PRIVILEGED approver and a written override reason, which is stored in the audit log.

**Restore test lifecycle:**
1. Restore the source snapshot (or a fresh snapshot, if the CR says so) to `restoretest-<source>-<yyyymmddhhmm>`. It's tagged `dbops:restore-test=true` and `dbops:cr=<cr_id>` and uses the smallest instance class the engine allows.
2. Wait for status `available`, with no failure events in RDS events. That is **passed**. A timeout (60 minutes) or an error is **failed**.
3. Delete the temporary instance automatically, whether the test passed or failed, without a final snapshot.
4. Write a restore-test report.

Progress steps shown in the UI: `restoring → available → verified → cleaned_up`, plus `failed` at any step. The executor retries a failed cleanup automatically 3 times, with backoff. If it still fails, the CR ends as `failed_cleanup_pending`. That status is shown prominently (red), kept at the top of the In progress tab, and given a **Retry cleanup** button for PRIVILEGED users. *(My call.)*

Progress steps for the other change types *(my call)*:
- `modify_tags`: `applying → done`
- `create_snapshot`: `creating → available`
- `modify_instance_class`: `pre_snapshot → modifying → available`, or `pre_snapshot → scheduled → applied` when scheduled for the next window

**Rollback (decided by user).** Nothing rolls back automatically. On a `succeeded` change, an eligible user can click **Propose rollback**. That creates a **new pending CR** that reverses the change: it carries `rollback_of: <cr_id>`, its diff swaps before and after, and it gets its own risk tier, so rolling back a resize is HIGH and rolling back tags is LOW. It then goes through Approvals like any other CR. That way the system keeps a single path for executing changes. Rollback CRs per type:
- tags: re-apply the previous tag set
- snapshot: delete the snapshot (LOW)
- resize: modify back to the old class (HIGH, with the window check)

A restore test has no rollback, because cleanup is already part of it.

**Stopping a change (decided by user).**
- The **requester** can **withdraw** their own `pending` CR.
- **Any PRIVILEGED user** can **cancel** a `scheduled` HIGH change. The executor clears the pending modification in AWS by sending `ModifyDBInstance` with the current values (and `ApplyImmediately=false`), then confirms that `PendingModifiedValues` is empty before marking it `cancelled`.
- Once a CR is `executing`, it cannot be stopped from the app.

**Change-request statuses:**
- `pending → approved → executing → succeeded | failed`
- `pending → rejected`
- `pending → withdrawn`
- `approved → scheduled → succeeded | failed | cancelled` (HIGH, next window)
- `failed_cleanup_pending` (restore test only) `→ succeeded | failed_cleanup_pending` after a retry

## 7. API contracts

JSON over HTTP. All endpoints require the two identity headers (§5). Timestamps are ISO-8601 UTC. Errors use `{ "error": { "code": str, "message": str } }`, with `403 forbidden_role`, `403 self_approval`, `409 invalid_state` and `422 window_override_reason_required`.

Each service publishes an OpenAPI file in `contracts/`. The mock backend and the real services must both follow them.

### 7.1 agent-api

| Method & path | Purpose |
|---|---|
| `GET /conversations` | the caller's conversations: `[{id, title, created_at, updated_at}]` |
| `POST /conversations` | create one: `{id}` |
| `GET /conversations/{id}/messages` | past turns, for rebuilding the chat after a refresh: `[{role, text, tool_steps[], change_request_ids[], run_id}]` |
| `POST /conversations/{id}/runs` body `{message}` | start a run: `{run_id}`. Returns `409` if the conversation already has an active run. |
| `GET /runs/{run_id}/events?after_seq=N` | events with `seq > N`: `{events: [...], run_status: running|completed|failed|cancelled}` |
| `POST /runs/{run_id}/cancel` | ask the run to stop |
| `GET /change-requests?status=&database=` | list CRs |
| `GET /change-requests/{id}` | full CR (shape below) |
| `GET /audit?user=&tool=&database=&from=&to=&limit=&cursor=` | audit rows, newest first, with cursor paging |
| `GET /audit/facets` | distinct users, tools and databases, for the filter dropdowns |
| `GET /reports` | `[{id, kind: restore_test|fleet_health|change_history, title, status: generating|ready|failed, error?, created_at, created_by, formats: [csv|md]}]` |
| `GET /reports/{id}` | one report's metadata, including `status`. The frontend polls this while the report is `generating`. |
| `GET /reports/{id}/download?format=` | file bytes. Returns `409 not_ready` until `ready`. |
| `POST /reports/change-history` body = audit filters | starts generating a change-history report in the background: `202 {report_id}` |
| `POST /change-requests/{id}/rollback` | creates a pending rollback CR for a `succeeded` CR: `201 {change_request_id}` (role rules in §5; `409` if the CR isn't `succeeded` or a rollback is already pending) |
| `GET /runs?conversation_id=&status=running` | finds a run still in progress after a refresh |

Reports are generated **in the background** *(my call)*. A fleet report built from real AWS data won't always be instant, so every report goes `generating → ready | failed`.

`agent-api` serves the audit log and the report list because they live in Postgres and it already has a read API. The executor and health-analytics write their rows and reports to the shared tables.

**Run events** (ordered by `seq`, an integer starting at 1):

```json
{"seq": 1, "type": "run_started", "ts": "..."}
{"seq": 2, "type": "text_delta", "text": "Yes. prod-orders-db has "}
{"seq": 3, "type": "tool_call_started", "tool_call_id": "t1", "name": "cloudwatch.get_metric_statistics", "args": {...}}
{"seq": 4, "type": "tool_call_finished", "tool_call_id": "t1", "status": "ok|error", "result": {...}, "duration_ms": 1200}
{"seq": 5, "type": "change_proposed", "change_request_id": "CR-0142", "change_type": "create_snapshot", "risk_tier": "LOW"}
{"seq": 6, "type": "run_completed"}            // or run_failed {error} / run_cancelled
```

`result` may be truncated by the server to 20 KB, with `"truncated": true`.

**Change request shape:**
```json
{
  "id": "CR-0143", "change_type": "modify_instance_class", "risk_tier": "HIGH",
  "database": {"identifier": "prod-orders-db", "kind": "instance|cluster", "engine": "aurora-postgresql", "region": "us-east-1"},
  "requested_by": "arjun", "requested_via": "chat", "conversation_id": "...", "created_at": "...",
  "summary": "Resize prod-orders-db to db.r6g.xlarge",
  "params": {"from_class": "db.r6g.large", "to_class": "db.r6g.xlarge"},
  "diff": [{"field": "instance_class", "before": "db.r6g.large", "after": "db.r6g.xlarge"}],
  "impact": "~5–10 min downtime (Multi-AZ failover)",
  "rollback_plan": ["Snapshot pre-CR-0143 taken automatically", "Modify back to db.r6g.large"],
  "maintenance_window": {"required": true, "window": "sun:03:00-sun:03:30", "inside_now": false, "next_start": "..."},
  "status": "pending", "approval": null,
  "progress": [{"step": "restoring", "state": "done|active|pending|failed", "at": "..."}],
  "result": null
}
```

### 7.2 executor

| Method & path | Purpose |
|---|---|
| `POST /change-requests/{id}/approve` body `{mode: "now"|"next_window", override_reason?}` | Re-checks role, the self-approval rule, current status, the window rule and that the target still matches the diff's `before` values (returns `409 drifted` if not). Then starts execution in the background: `202 {status}`. |
| `POST /change-requests/{id}/reject` body `{reason}` | `200 {status: "rejected"}` |
| `POST /change-requests/{id}/withdraw` | requester only, from `pending`: `200 {status: "withdrawn"}` |
| `POST /change-requests/{id}/cancel` body `{reason}` | PRIVILEGED, from `scheduled`: clears the pending AWS modification. Returns `202`, and the CR becomes `cancelled` once AWS confirms. |
| `POST /change-requests/{id}/retry-cleanup` | PRIVILEGED, from `failed_cleanup_pending`: `202` |

Progress is read through `agent-api GET /change-requests/{id}`. Both services read the same table, so the frontend has one place to read from.

### 7.3 health-analytics

| Method & path | Purpose |
|---|---|
| `GET /fleet?env=&engine=` | `{as_of, databases: [{identifier, kind, engine, instance_class, env, region, health_score (0–100), storage: {free_gb, trend_gb_per_day, days_to_full|null}, open_alarms: int}]}` |
| `GET /fleet/summary` | `{as_of, total, unhealthy, under_30_days_to_full, open_alarms}` |
| `GET /fleet/{identifier}/alarms` | `[{name, metric, state, since, threshold, description}]` |
| `POST /refresh` | asks for a re-poll now: `202` (rate-limited to once a minute) |
| `POST /reports/fleet-health` body `{env?}` | starts generating in the background: `202 {report_id}`. Status is read through `agent-api GET /reports/{id}`. |

How the health score is calculated and how often polling runs belong to the health-analytics design. The frontend only displays them. Score bands for colouring are ≥80 green, 50–79 amber and <50 red. Days to full is ≤14 red, 15–30 amber, otherwise neutral.

## 8. Frontend

**Stack:** Python 3.12, Streamlit (a version that supports `st.navigation`, `st.fragment(run_every=...)` and custom theme fonts, i.e. headingFont and fontFaces), `httpx`, `pandas`, `bcrypt`, `PyYAML`. It runs as its own container.

### 8.1 Layout and navigation
- One app. `st.navigation` puts the page list in the **left sidebar**, and clicking a page shows it in the main area.
- Order: **Health (default)**, Chat, Approvals (with a pending-count badge), Audit & reports.
- The top of the sidebar shows the user, their role and a Log out button. Only on Chat, the sidebar also shows "+ New chat" and the user's past conversations.
- Before login, only the login form is shown, using `users.yaml` and bcrypt. The logged-in user lives in `st.session_state`.
- **A browser refresh means logging in again** *(my call)*. Keeping a login across a refresh would need a signed session cookie. That's more than a hardcoded-user demo needs, and a weak token would let someone impersonate an approver. After logging back in, Chat opens the most recently updated conversation and resumes any run that is still running (found through `GET /runs?conversation_id=…&status=running`).

### 8.2 Visual rules
- **Fonts:** Space Grotesk for headings, Inter for body text, JetBrains Mono for code, diffs and tool arguments, set in `.streamlit/config.toml` (theme fonts). Numbers in tables use tabular figures where possible.
- **Status colours** (bright and bold, readable on a projector): green `#22c55e`, amber `#f59e0b`, red `#ef4444`. Risk badges are solid fills: LOW `#16a34a` with white text, MEDIUM `#f59e0b` with dark text, HIGH `#dc2626` with white text. Diff lines: old value red, new value green.
- **Never colour alone:** every coloured status also has a word or icon (`HIGH`, `⚠ Outside window`, `✅`, `⏳`).
- **Clickable things must look clickable:** use only Streamlit's built-in controls (buttons, tabs, expanders, selects, page links). They show the hand pointer and hover state by default. Never style text to look like a button.

### 8.3 Health (default page)
- A row of summary numbers: Databases, Unhealthy, < 30 days to full, Open alarms.
- "Data as of <time> (health-analytics)", a **Refresh** button and a **Generate health report** button.
- Filters: env and engine.
- A table with columns database, engine, class, score, days to full and alarms, with the colour bands from §7.3. Selecting a row opens an expander with that database's alarms.
- Data comes only from health-analytics, never from chat.

### 8.4 Chat
- The past messages of the current conversation, rebuilt from `GET /conversations/{id}/messages`, plus a chat input.
- **Run lifecycle (the rerun-safe pattern):**
  1. On submit, call `POST /conversations/{id}/runs` once. Store `active_run = {run_id, last_seq: 0, events: []}` in `st.session_state[conv_id]`. Disable the input while a run is active.
  2. A `@st.fragment(run_every=0.5s)` polls `GET /runs/{run_id}/events?after_seq=last_seq`, adds the new events to session state and redraws only the in-progress assistant message. Interactions elsewhere rerun the script but don't start a new run, because the run_id is already in session state.
  3. When `run_status` is no longer `running`, the polling stops, the turn is moved into history and the input is enabled again.
  4. After a browser refresh (when session state is lost), the page reloads history from the API and resumes polling any run still marked `running`.
- **How an assistant turn is drawn:** text deltas are joined into a single markdown block. Each tool call is an `st.expander` that starts **collapsed**, titled `🔧 <tool name> · <duration> · ok|error`, and shows the arguments and the result as JSON in JetBrains Mono. A `change_proposed` event shows `📝 Proposed <change_type> → CR-xxxx` with a risk badge and a link to Approvals. **Chat has no execute control.**
- A **Stop** button while running calls cancel.

### 8.5 Approvals
- Tabs: **Pending**, **In progress** (approved, executing, scheduled, failed_cleanup_pending) and **Done** (succeeded, failed, rejected).
- Each CR is an expander titled `CR-id · change_type · database`, with a risk badge and "requested by X via chat at <time>". Inside:
  - The diff (before in red, after in green, monospace) and the impact line.
  - The rollback plan as a list.
  - For HIGH only, the maintenance window: inside or outside (`✅` or `⚠`), the window and when the next one starts; a radio for **Schedule for next window** or **Apply now**, plus an override reason input that appears when Apply now is chosen outside the window.
  - **Approve & execute** and **Reject**. Reject asks for a reason. Both are disabled, with the reason shown, when the user's role is too low or it's their own request.
  - A confirmation step before execute: a dialog that repeats the database, the change and the mode.
  - **Withdraw**, shown only to the requester on their own pending CR.
  - Rollback CRs show "↩ Rollback of CR-xxxx" in the title.
- **In progress:** a step tracker for each CR (steps listed in §6), refreshed by a fragment every 5 seconds. A `scheduled` CR shows the next window's start time and, for PRIVILEGED users, a **Cancel** button that asks for a reason and confirmation. A `failed_cleanup_pending` CR shows **Retry cleanup** for PRIVILEGED users.
- **Done:** a `succeeded` CR (other than a restore test) shows **Propose rollback** to eligible users. Clicking it creates the rollback CR and switches to the Pending tab with that CR open.
- Errors from the executor (`drifted`, `self_approval`, and so on) are shown inline in plain words.
- This is the **only** page that calls `executor`.

### 8.6 Audit & reports
- Tabs: **Audit log** and **Reports**.
- Audit log filters: user, tool, database (options from `/audit/facets`) and a date range, defaulting to the last 7 days. A paged table with time, user, action or tool, database, result and CR id. **Export CSV** calls `POST /reports/change-history`. The new report appears at the top of Reports as "⏳ generating…", and its download button appears once it's ready.
- Reports: a table with title, kind, status, generated time and author, and a download button for each format when ready. While any report is generating, a fragment polls every 3 seconds. Failed reports show their error. The Health page's **Generate health report** behaves the same way and links to the Reports tab.

### 8.7 Errors and empty states
- Each page handles its own backend being unreachable with a clear banner ("health-analytics unavailable, showing nothing rather than stale data"). The other pages keep working.
- Empty states: no databases, no pending approvals, no reports.

## 9. Mock backend

One small FastAPI app (`mock-backend`) that implements all three contracts on one port, with canned, believable fleet data (around 14 databases, a couple unhealthy), seeded CRs of all four types, and audit rows. Its chat runs replay a scripted sequence of events with realistic delays, including tool calls and a `change_proposed`. Approving a CR moves it through its steps on a timer. The frontend picks real or mock services through three base-URL environment variables (`AGENT_API_URL`, `EXECUTOR_URL`, `HEALTH_URL`), so switching later only means changing config.

## 10. Repository layout (my call)

```
db-ops/
  contracts/                 agent-api.yaml, executor.yaml, health-analytics.yaml (OpenAPI 3.1)
  config/users.yaml          hardcoded users (bcrypt hashes)
  frontend/                  Streamlit app, Dockerfile, .streamlit/config.toml
  mock_backend/              FastAPI mock, Dockerfile
  services/agent_api/        later (part 2)
  services/executor/         later (part 3)
  services/health_analytics/ later (part 4)
  docker-compose.yml         part 1 runs frontend + mock-backend; later services are added as their parts land
```

## 11. Calls made by the designer (not discussed with the user; check these)
- The demo users and their roles (§5): there are two PRIVILEGED users so that no-self-approval can be shown.
- Identity headers, with each service looking up the role itself (§5).
- A browser refresh means logging in again (§8.1).
- Restore-test cleanup: 3 automatic retries, then a Retry cleanup button (§6).
- Reports are generated in the background with a status (§7.1).
- The temporary restore instance uses the smallest class, with a 60-minute timeout (§6).
- The date filter on the audit log (§8.6).
- The executor's IAM actions, with delete limited to restore-test-tagged resources (§4).
- The colour bands for score and days to full (§7.3).

## 12. Left to the builder
- Exact Streamlit and library versions, plus project tooling (uv or pip, ruff, pytest).
- The frontend's internal module structure: a thin API client layer per service is expected so it can be tested against the mock.
- Polling intervals within reason (chat about 0.5 s, approvals about 5 s).
- Mock data content.
- How the health table row selection is implemented in Streamlit.

- Contract fields not spelled out here: audit row fields, the cursor paging response, the `approval` object and `tool_steps`. They must be written into the OpenAPI files before the frontend is built.

## 13. Decided later, in the service designs
- The LLM, the agent framework and how the AWS Agent Toolkit is wired in (part 2).
- Run-worker technology and how run events are persisted (part 2).
- Executor internals, retries and idempotency (part 3).
- The health-score formula and polling cadence (part 4).
