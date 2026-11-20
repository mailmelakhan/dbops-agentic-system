# DB Ops part 2: agent-api: Design

Date: 2026-11-19
Status: Draft, awaiting approval (builder check done)
Builds on: `2026-11-14-db-ops-frontend-design.md` (the "part 1 spec"), amended by `2026-11-16-fleet-panel-design.md`. Contract: `contracts/agent-api.yaml`.

---

## 1. Intent and why

Part 1 shipped the frontend against a mock backend. Part 2 replaces the mock's **chat** with a real `agent-api`. In it, a real LLM agent answers questions about **real RDS/Aurora databases in the user's AWS account**, using read-only access. It can also propose changes, which go to Approvals as `pending` change requests.

The audience is still stakeholders in a demo. The anti-goal from part 1 still holds and matters more now that real AWS is involved: **nothing reachable from chat may change a real database.** Part 2 also must not leak information about databases outside the demo set.

## 2. Decisions made with the user

- **Demo scope (option 1):** chat works on real AWS. The fleet panel (health-analytics) and the executor stay on the mock until parts 4 and 3. So in the demo the fleet panel and chat show different databases, and the user accepted that.
- **LLM:** DeepSeek API.
- **Agent framework:** Strands Agents (Python SDK).
- **AWS access (approach B):** the agent's AWS tools come from the managed **AWS MCP Server** (the Agent Toolkit for AWS), not from tools we write.
- **Database scope:** the agent sees only databases tagged `dbops:demo=true`. The tag key and value are configurable.

## 3. Goals, non-goals, anti-goals

**Goals**
- `agent-api` implements every endpoint in `contracts/agent-api.yaml`. The frontend's only change is the `AGENT_API_URL` it points at, plus the small banner in §9.
- Chat answers questions about in-scope databases: configuration, status, CloudWatch metrics, alarms, RDS events and snapshots. Answers stream as `text_delta` events, and every AWS call appears as a tool step.
- Chat proposes the four change types from part 1 §6. Each proposal uses real "before" values from AWS. The role rules from part 1 §5 apply.
- Runs survive page switches and browser refreshes (part 1 §8.4).

**Non-goals**
- Real execution of changes (part 3) and real fleet health (part 4).
- Moving change requests, audit and reports into Postgres. They stay in the mock for now (§6).
- Multi-account support. One account, plus a configurable list of regions.

**Anti-goals**
- Any AWS write that can be reached from chat, whether through the MCP server's generic command tool or any other route.
- The model seeing, or quoting, data about a database outside the scope tag.
- Telling stakeholders a change was made when it was only simulated.

## 4. Constraints

- **The contract is fixed.** `contracts/agent-api.yaml` and the event shapes in part 1 §7.1 don't change. The existing frontend tests must keep passing.
- **Data goes to DeepSeek.** Every tool result goes to DeepSeek's API: identifiers, tags, metrics and events of in-scope databases. The scope tag is the control on what leaves the account. The user has been told this and owns the policy question.
- **DeepSeek's API is OpenAI-compatible** (base URL `https://api.deepseek.com`, with tool calling). Model names are changing (`deepseek-chat` now, V4 names announced), so the model id is config.
- **The AWS MCP Server's main tool runs almost any AWS CLI command** (`call_aws` in the self-hosted version, `aws___call_aws` in the managed one). So limiting the tool list is not a safety control. The command guard in §7.3 and the IAM policy in §7.5 are the controls.
- Python 3.12, uv workspace, ruff line length 120. These are the same as part 1.

## 5. Approaches considered

| | Approach | Trade-off | Outcome |
|---|---|---|---|
| A | Our own ~6 read tools written with boto3, each checking the tag; CRs stay in the mock | Read-only access and the tag limit are easy to see and test, and tool steps are clean. We write and maintain the tools. | Recommended by designer, not chosen |
| **B** | **Like A, but the AWS tools come from the AWS MCP Server, behind a command guard and a result filter** | Covers far more of AWS with no tool code. The tag limit and read-only rule have to be enforced on every command and every result, which is easier to get wrong. The model sees a generic tool, so it may pick commands less reliably. Adds a dependency on a service AWS manages. | **Chosen by user** |
| C | Like A, but CRs, audit and reports move to Postgres now and the mock executor is rewired | Cleanest end state. Pulls part 3's data work forward and rewrites a mock that part 3 replaces anyway. | Rejected |

Common to all three, and my call: a Strands agent per run, run in a background task inside `agent-api`, with events stored in Postgres.

## 6. Architecture

```
 browser ─▶ frontend ──AGENT_API_URL──▶ agent-api ──▶ postgres  (conversations, messages, runs, run events)
               │                          │   │
               │                          │   ├─▶ DeepSeek API   (via Strands OpenAIModel)
               │                          │   ├─▶ AWS MCP Server (via mcp-proxy-for-aws, SigV4, read-only principal)
               │                          │   ├─▶ AWS RDS API    (boto3: scope lookup, proposal "before" values)
               │                          │   └─▶ mock-backend   (CR / audit / report pass-through + internal writes)
               ├──EXECUTOR_URL──────────────────▶ mock-backend   (simulated execution)
               └──HEALTH_URL────────────────────▶ mock-backend   (mock fleet)
```

**Compose changes:**
- Add `postgres` (16) and `agent-api`.
- The frontend's `AGENT_API_URL` points at `agent-api`.
- Only the frontend still publishes a port.
- `agent-api` gets AWS credentials (a read-only profile, mounted, or env vars), `DEEPSEEK_API_KEY`, and the shared service token.

**Who owns which endpoint during part 2:**

| Endpoints | Served by |
|---|---|
| `/conversations*`, `/runs*` (list, events, cancel, start) | `agent-api`, from Postgres |
| `/change-requests*` (list, get, rollback), `/audit*`, `/reports*` | `agent-api` **passes them through** to the mock unchanged. It forwards the method, path, query, body and both identity headers, then returns the mock's status and body. |

The pass-through keeps one address for the frontend. In part 3, `agent-api` replaces it with Postgres tables without changing the frontend.

**New internal mock endpoints (my call).** These are not in the public contract. They require the service token and a new header, `X-DBOps-Internal: agent-api`.
- `POST /internal/change-requests`: the body is the CR fields (`change_type`, `database`, `requested_by`, `conversation_id`, `summary`, `params`, `diff`, `impact`, `rollback_plan`, `maintenance_window`). It returns the created CR. The mock assigns `id`, `risk_tier`, `status: pending` and `created_at`, and writes the `chat.propose` audit row as it does today.
  - `agent-api` owns every descriptive field (`summary`, `impact`, `rollback_plan`, `maintenance_window`), and the mock stores them as given instead of computing them (my call). Part 3 removes the mock, so these fields have to come from `agent-api` anyway. The mock still computes its own fields for seeded CRs and rollbacks.
- `POST /internal/audit`: one audit row with fields `user`, `action`, `tool`, `database`, `result` and `ts`. Rows for `chat.message` and `chat.tool_call` keep the action names the mock uses today.

**Mock changes these need:**
- The mock executor must accept CRs whose database is not in its seeded fleet. Approval, the drift check and the step timers work from the CR itself.
- The mock no longer serves `/conversations*` and `/runs*` once `agent-api` is in compose. Its scripted runs stay available for frontend tests.

## 7. The agent

### 7.1 Run lifecycle

1. `POST /conversations/{id}/runs` stores the user message and creates a `runs` row with status `running`. It starts a background asyncio task and returns `{run_id}`. If the conversation already has a running run, it returns `409 run_active`.
2. The task builds a Strands `Agent`:
   - **Model:** `OpenAIModel` with `client_args={api_key, base_url}` and `model_id` from config.
   - **System prompt:** §7.6.
   - **History:** this conversation's prior turns from Postgres.
   - **Tools:** §7.2.
   - **Hooks:** §7.3 and §7.4.

   It then consumes `agent.stream_async(message)`.
3. Every event is written to `run_events` with a `seq` assigned at insert time, starting at 1 for each run. Text chunks are buffered and flushed as `text_delta` about every 100 ms, so the table doesn't hold one row per token. `GET /runs/{id}/events?after_seq=N` reads from this table.
4. At the end, the task writes the assistant message to `messages`. That row holds the text, the tool steps and the CR ids, in the shape of `GET /conversations/{id}/messages`. It then writes `run_completed`, `run_failed {error}` or `run_cancelled`, and updates the run's status.
5. **Cancel** sets a flag on the run. The stream loop checks it between events and stops the agent. A tool call that is in flight finishes, but the next model call never starts.
6. **Limits (my call):** at most 15 tool calls per run, and 5 minutes of wall-clock time per run. Reaching either ends the run with `run_failed` and a plain message, such as "Stopped after 15 AWS calls".
7. **On startup**, any run still marked `running` is marked `failed` with the error "agent-api restarted". Runs survive a browser refresh but not a container restart (my call).
8. A conversation's title is its first message, cut to 60 characters (my call).

### 7.2 Tools

**From the AWS MCP Server.** Strands' `MCPClient` connects through `mcp-proxy-for-aws`, which runs as a stdio subprocess and signs requests with SigV4.
- Only the generic command tool (`call_aws`) is exposed to the model.
- The documentation-search tools are exposed too, if the server offers them, because they are harmless.
- Every other tool the server offers is filtered out by name, including script runners and command suggestion. An allowlist of tool names is in config, and anything not on it is dropped.

**Our own tools.** These are Strands `@tool` functions.
- `list_demo_databases()`: returns the in-scope databases, each with its identifier, kind, engine, class, region and status. It is the model's starting point, and it avoids broad describe calls.
- **Propose tools,** registered by role:

  | Role | Propose tools registered |
  |---|---|
  | READ | none |
  | SAFE-WRITE | `propose_modify_tags(identifier, add: dict, remove: list)`, `propose_create_snapshot(identifier)` |
  | PRIVILEGED | the SAFE-WRITE tools, plus `propose_restore_test(identifier, snapshot_id?)` and `propose_modify_instance_class(identifier, to_class)` |

  Each propose tool does the following:
  - re-checks the caller's role (defence in depth) and that the database is in scope
  - reads the real "before" values with boto3: tags, current class, `PreferredMaintenanceWindow`, the latest snapshot for a restore test
  - builds the diff, impact and rollback plan as part 1 §6 describes
  - calls the mock's `POST /internal/change-requests`
  - emits `change_proposed`

  Propose tools never call AWS write APIs.

- **Aurora clusters and plain RDS (decided by user: the demo set is a mix).**
  - **Resize** (`propose_modify_instance_class`) always targets **one instance**. The CR's `database` is that instance (`kind: instance`), and its `params` include `cluster_identifier` when the instance is an Aurora member. If the model names a cluster, the tool returns the cluster's members with their roles (writer or reader) and classes, and the model asks the user which one. The impact line says whether the instance is the writer (failover expected) or a reader.
  - **Restore test** on a plain instance works as part 1 §6 describes. On an Aurora cluster, the CR's `database` is the cluster (`kind: cluster`). The CR describes restoring the cluster from its latest cluster snapshot plus **one** instance of the smallest class the engine allows, found with `describe-orderable-db-instance-options`. The diff lists the temporary cluster and instance identifiers.
  - **Cost estimate (my call):** the IAM policy has no pricing access, so the estimate comes from a small price table in config (`services/agent_api/prices.yaml`, on-demand $/hour for the smallest classes per engine and region). It is shown as "≈ $0.08/h (approx.)". A class missing from the table shows "cost estimate unavailable".
  - **Tags and snapshots** work on either kind. A cluster snapshot is taken for a cluster, and an instance snapshot for an instance.

### 7.3 Command guard (`BeforeToolCallEvent`)

Every call to `call_aws` is checked before it runs. A failed check sets `cancel_tool` with a plain reason, which the model sees as an error tool result. The step also shows `status: error`. **It fails closed:** anything the guard can't parse is refused.

1. **One command only.** The command is parsed with `shlex`. Pipes, `;`, `&&`, redirection, subshells and more than one `aws` command are refused.
2. **Allowlisted operations.** The command must be on this list (config):
   - `rds`: `describe-db-instances`, `describe-db-clusters`, `describe-db-snapshots`, `describe-db-cluster-snapshots`, `describe-events`, `describe-pending-maintenance-actions`, `describe-orderable-db-instance-options`, `list-tags-for-resource`
   - `cloudwatch`: `get-metric-statistics`, `get-metric-data`, `list-metrics`, `describe-alarms`
3. **Forbidden flags:** `--endpoint-url`, `--profile`, `--cli-input-json`, `--cli-input-yaml`, and anything starting with `file://` or `fileb://`. A `--region` outside the configured regions is refused.
4. **Scope.** Every database reference in the arguments must be in the scope set (§7.4):
   - `--db-instance-identifier` and `--db-cluster-identifier`
   - `--source-identifier`
   - an ARN in `--resource-name`
   - `DBInstanceIdentifier` and `DBClusterIdentifier` in CloudWatch `--dimensions` or `--metric-data-queries`

   A reference outside scope is refused with "unknown database". The wording is the same whether or not the database exists, so the model learns nothing about it.
5. **Calls with no identifier** are allowed only for the list-type operations, such as describe-db-instances with no identifier or describe-alarms. Their results go through the filter in §7.4.

### 7.4 Scope set and result filter (`AfterToolCallEvent`)

**Scope set.** `agent-api` resolves it itself, not the model.
- It uses boto3 `describe_db_instances` and `describe_db_clusters` in each configured region, keeping the resources whose tags include `DBOPS_SCOPE_TAG_KEY=DBOPS_SCOPE_TAG_VALUE` (default `dbops:demo=true`).
- **Aurora (my call).** Tagging an Aurora cluster brings all of its member instances into scope. Most Aurora metrics and events are keyed by the member instance, and AWS doesn't copy cluster tags to members. Tagging only a member instance brings in that instance alone, not its cluster or sibling members.
- The scope set is keyed by (region, identifier) and records each member's cluster, so the guard and filter treat members and their cluster as one in-scope unit.
- The result is cached for 60 seconds.
- If the lookup fails, the run fails with "Couldn't read the database list from AWS". It is not treated as an empty or unlimited set.

**Result filter.** Every successful `call_aws` result passes through it before the model sees it:
- `DBInstances` and `DBClusters` keep only in-scope identifiers.
- Snapshots keep only those whose source instance or cluster is in scope.
- `Events` keep only those whose `SourceIdentifier` is in scope, or whose source is a snapshot of an in-scope database.
- `MetricAlarms`, `CompositeAlarms` and `list-metrics` keep only entries whose DB dimension is in scope.
- **Fail closed:** if a result can't be parsed into one of these known shapes, it is replaced with an error. The original is kept only in the server log.

After filtering, the result is cut to 20 KB with `"truncated": true`, as part 1 §7.1 requires.

### 7.5 IAM (the backstop)

`agent-api` runs as a principal with read-only access and nothing else:
- `rds:Describe*` and `rds:ListTagsForResource`
- `cloudwatch:GetMetricData`, `cloudwatch:GetMetricStatistics`, `cloudwatch:ListMetrics` and `cloudwatch:DescribeAlarms`
- the permissions the AWS MCP Server itself requires (to be confirmed by the builder)

No write actions at all. If the guard has a bug, AWS still refuses the write. The policy ships as `services/agent_api/iam-policy.json`, and the README tells the user to attach it to a dedicated IAM user or role.

### 7.6 System prompt (outline; exact wording left to the builder)

- **Role:** a DBA assistant for the databases in the demo set. Start from `list_demo_databases`.
- **Read-only:** you can't change anything. Changes are only proposed with the propose tools, and a person approves them on the Approvals page. Never say a change was made.
- **Commands:** run one AWS CLI command per `call_aws`, from the read operations listed. Name the database explicitly.
- If a tool is refused, explain that plainly rather than retrying variants.
- Be concise, and cite the numbers you used.

### 7.7 How events look in the chat

- **Tool step names.** A `call_aws` step is named after the command, for example `rds.describe_db_instances` or `cloudwatch.get_metric_statistics`, matching the style of the mock's tool names. Its `args` show the full CLI command (my call). Our own tools keep their Python names.
- **Durations.** `duration_ms` is measured between the before and after hooks.
- **Audit.** Each user message and each tool call (including refused ones, with `result: refused`) is posted to the mock's `/internal/audit`. If an audit write fails, it is logged and the run continues (my call; the audit belongs to part 3's Postgres move).

## 8. Persistence

Postgres tables, created with plain SQL migrations at startup (the tool is the builder's choice):
- `conversations(id, username, title, created_at, updated_at)`
- `messages(id, conversation_id, role, text, tool_steps jsonb, change_request_ids jsonb, run_id, created_at)`
- `runs(run_id, conversation_id, username, status, error, started_at, ended_at)`
- `run_events(run_id, seq, payload jsonb, created_at)`, with primary key `(run_id, seq)`

Every read is filtered by the caller, so conversations and runs belong to their user. Other users get `404 not_found`, as the mock does.

## 9. Frontend change

One addition, which is my call. When the frontend env `EXECUTOR_SIMULATED=true` is set (the compose default in part 2), Approvals shows an info banner: "Execution is simulated: approving a change does not touch AWS yet." That covers the third anti-goal, and part 3 removes the flag.

## 10. Configuration (`agent-api` env)

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | none | Postgres connection |
| `DEEPSEEK_API_KEY` | required | LLM key |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | |
| `DEEPSEEK_MODEL` | `deepseek-chat` | |
| `DBOPS_SCOPE_TAG_KEY` / `DBOPS_SCOPE_TAG_VALUE` | `dbops:demo` / `true` | scope tag |
| `DBOPS_AWS_REGIONS` | `us-east-1` | comma-separated |
| `AWS_MCP_ENDPOINT` | the managed AWS MCP Server URL | |
| `MOCK_BACKEND_URL` | `http://mock-backend:8000` | pass-through and internal writes |
| `DBOPS_USERS_FILE`, `DBOPS_SERVICE_TOKEN` | as part 1 | identity |
| `RUN_MAX_TOOL_CALLS` / `RUN_TIMEOUT_S` | `15` / `300` | run limits |

## 11. Testing

Nothing in CI talks to real AWS or DeepSeek.
- **Contract conformance (my call):** a new test suite in `services/agent_api/tests/`. It calls each endpoint that `agent-api` serves natively (conversations, messages, runs, events and cancel) and validates the response body and status against the schemas in `contracts/agent-api.yaml`, using `jsonschema`, which is already a dev dependency. It uses a fake model and a fake MCP server. The pass-through endpoints get one test each against the real mock app. The mock's own tests stay as they are.
- **Guard tests:** a table of commands, each allowed or refused with the reason. It covers:
  - each allowlisted operation
  - write operations (`modify-db-instance`, `delete-db-instance`, `create-db-snapshot`)
  - pipes and `;`
  - `--endpoint-url`, `--profile` and `file://`
  - out-of-scope identifiers in each argument form
  - out-of-scope regions
  - unparseable input
- **Filter tests:** each known result shape with a mix of in-scope and out-of-scope entries, plus an unknown shape (expect an error).
- **Run tests:** a scripted fake Strands model drives a run. The tests check the event order, `seq` continuity, the cancel and limit paths, the startup recovery of running runs, and that a READ user's agent has no propose tools.
- **Propose tests:** with a stubbed boto3 (botocore Stubber), each propose tool sends the right CR body to the internal endpoint.
- **Manual smoke script** (`scripts/smoke_agent_api.py`): one real question and one real proposal against the user's account, run by hand before the demo.

## 12. Repository layout

`services/agent_api/` is a new uv workspace member containing `pyproject.toml`, `Dockerfile`, `iam-policy.json`, `src/agent_api/…` and `tests/`. Module structure is left to the builder. Expected modules: API routes, the run worker, the agent factory, the guard, the filter, scope, propose tools, the pass-through, and the database layer.

## 13. Calls made by the designer (check these)

- The pass-through of CR, audit and report endpoints to the mock, plus the two internal mock endpoints (§6).
- The "Execution is simulated" banner behind `EXECUTOR_SIMULATED` (§9).
- Only `call_aws` (and the docs tools) exposed from the MCP server. The operation allowlist and forbidden flags (§7.3).
- "Unknown database" wording for out-of-scope references (§7.3).
- `list_demo_databases` as our own starting-point tool (§7.2).
- Run limits of 15 tool calls and 5 minutes. Runs fail on an `agent-api` restart (§7.1).
- Tool step names derived from the CLI command (§7.7).
- Audit writes are best effort during part 2 (§7.7).
- Aurora scope: a tagged cluster brings in its members (§7.4).
- `agent-api` owns every descriptive CR field it sends to the mock (§6).
- The restore-test cost comes from a config price table, shown as approximate (§7.2).
- A new contract conformance suite for `agent-api` (§11).
- The scope cache is 60 seconds, and a failed scope lookup fails the run (§7.4).

## 14. Left to the builder

- Exact versions of `strands-agents`, the MCP proxy, and the Postgres driver (`psycopg` 3 or `asyncpg`).
- Confirming the managed AWS MCP Server's endpoint, auth (`mcp-proxy-for-aws`), exact tool names and required IAM permissions. **If the managed server can't be reached from a container,** use the self-hosted `awslabs.aws-api-mcp-server` with `READ_OPERATIONS_ONLY=true`, which has the same `call_aws` tool, and note the switch in the README. The guard and filter don't change.
- How text is buffered into `text_delta` events, within about 100 ms.
- The migration tool, module layout and the system prompt's exact wording.
