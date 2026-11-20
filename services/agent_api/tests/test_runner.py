import contextlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from agent_api.identity import Caller, Role
from agent_api.proposals import Proposer
from agent_api.runner import Runner
from agent_api.scope import ScopedDb, ScopeSet, ScopeUnavailable
from agent_api.settings import load_settings

_spec = importlib.util.spec_from_file_location("agent_api_test_fakes", Path(__file__).with_name("fakes.py"))
fakes = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = fakes
_spec.loader.exec_module(fakes)
ScriptedModel, fake_call_aws, mcp_error_text = fakes.ScriptedModel, fakes.fake_call_aws, fakes.mcp_error_text

ARN = "arn:aws:rds:us-east-1:123:db:"
CALLERS = {"lakhan": Caller("lakhan", Role.PRIVILEGED), "geeta": Caller("geeta", Role.SAFE_WRITE),
           "amit": Caller("amit", Role.READ)}
DESCRIBE = {"rds describe-db-instances": {"DBInstances": [
    {"DBInstanceIdentifier": "prod-a", "DBInstanceArn": f"{ARN}prod-a", "DBInstanceStatus": "available"},
    {"DBInstanceIdentifier": "other-db", "DBInstanceArn": f"{ARN}other-db", "DBInstanceStatus": "available"},
]}}
SCOPE = ScopeSet([ScopedDb("us-east-1", "prod-a", "instance", "postgres", "db.t3.micro", "available", None, (),
                           f"{ARN}prod-a")])


class FakeScope:
    def __init__(self, fail: bool = False):
        self.fail = fail

    def current(self):
        if self.fail:
            raise ScopeUnavailable("Couldn't read the database list from AWS")
        return SCOPE


class FakeMock:
    def __init__(self):
        self.audits: list[dict] = []
        self.bodies: list[dict] = []

    def create_change_request(self, body):
        self.bodies.append(body)
        return {"id": "CR-2001", "change_type": body["change_type"], "risk_tier": "LOW", "status": "pending"}

    def audit(self, row):
        self.audits.append(row)


class World:
    """One runner over the Task 2 store, a fake scope, a fake mock and a scripted model."""

    def __init__(self, settings, store, turns, *, scope=None, responses=DESCRIBE, on_turn=None):
        self.store = store
        self.scope = scope or FakeScope()
        self.mock = FakeMock()
        self.aws = fake_call_aws(responses)
        self.model = ScriptedModel(turns, on_turn=on_turn)
        proposer = Proposer(self.scope, lambda region: None, self.mock, {})

        @contextlib.contextmanager
        def provider():
            yield [self.aws]

        self.runner = Runner(settings, store, self.scope, proposer, self.mock, provider,
                             model_factory=lambda: self.model)
        self.conversation_id = store.create_conversation("lakhan")["id"]

    def run(self, message="How is prod-a?", user="lakhan", conversation_id=None):
        conv = conversation_id or self.conversation_id
        run_id = self.store.start_run(conv, user, message, message[:60])
        self.runner.start(run_id, CALLERS[user], conv, message)
        assert self.runner.join(run_id, timeout=5)
        return run_id

    def events(self, run_id):
        return self.store.events_after(run_id, 0)

    def status(self, run_id, user="lakhan"):
        return self.store.get_run(run_id, user)["status"]

    def last_message(self):
        return self.store.list_messages(self.conversation_id)[-1]


def types(events):
    return [e["type"] for e in events]


def aws(command):
    return {"tool": "call_aws", "input": {"command": command}}


HAPPY = [[{"text": "Checking. "}, aws("aws rds describe-db-instances")], [{"text": "prod-a is available."}]]


@pytest.fixture
def world(settings, store):
    return lambda turns, **kw: World(settings, store, turns, **kw)


def test_happy_path_events(world):
    w = world(HAPPY)
    run_id = w.run()
    events = w.events(run_id)
    assert types(events) == ["run_started", "text_delta", "tool_call_started", "tool_call_finished", "text_delta",
                             "run_completed"]
    assert [e["seq"] for e in events] == [1, 2, 3, 4, 5, 6]
    started, finished = events[2], events[3]
    assert started["name"] == "rds.describe_db_instances"
    assert started["args"] == {"command": "aws rds describe-db-instances --region us-east-1"}  # as sent
    assert finished["status"] == "ok" and finished["tool_call_id"] == started["tool_call_id"]
    assert [r["DBInstanceIdentifier"] for r in finished["result"]["DBInstances"]] == ["prod-a"]
    assert "other-db" not in json.dumps(finished["result"])
    # The model's next turn saw only the filtered result: no structuredContent, nothing about other-db.
    assert "other-db" not in json.dumps(w.model.calls[1]["messages"])
    assert w.status(run_id) == "completed"
    msg = w.last_message()
    assert msg["role"] == "assistant" and msg["run_id"] == run_id
    assert msg["text"] == "Checking. prod-a is available."
    assert len(msg["tool_steps"]) == 1 and msg["tool_steps"][0]["name"] == "rds.describe_db_instances"
    assert msg["tool_steps"][0]["status"] == "ok"
    audits = w.mock.audits
    assert audits[0] == {"user": "lakhan", "action": "chat.message", "tool": None, "database": None,
                         "result": "ok", "detail": "How is prod-a?"}
    assert {k: audits[1][k] for k in ("user", "action", "tool", "database", "result")} == {
        "user": "lakhan", "action": "chat.tool_call", "tool": "rds.describe_db_instances", "database": None,
        "result": "ok"}


def test_refused_command_is_error_step(world):
    w = world([[aws("aws rds delete-db-instance --db-instance-identifier prod-a")], [{"text": "I can't."}]])
    run_id = w.run()
    finished = next(e for e in w.events(run_id) if e["type"] == "tool_call_finished")
    assert finished["status"] == "error"
    assert "operation not allowed" in json.dumps(finished["result"])
    assert w.aws.commands == []
    row = next(a for a in w.mock.audits if a["action"] == "chat.tool_call")
    assert row["result"] == "refused" and row["tool"] == "rds.delete_db_instance" and row["database"] == "prod-a"
    assert w.status(run_id) == "completed"


def test_out_of_scope_not_leaked(world):
    w = world([[aws("aws rds describe-db-instances --db-instance-identifier other-db")], [{"text": "Unknown."}]])
    run_id = w.run()
    finished = next(e for e in w.events(run_id) if e["type"] == "tool_call_finished")
    assert finished["status"] == "error" and "unknown database" in json.dumps(finished["result"])
    assert w.aws.commands == []


def test_aws_tool_input_must_be_one_command(world):
    w = world([[{"tool": "call_aws", "input": {"command": "aws rds describe-db-instances",
                                               "cli_command": "aws rds delete-db-instance"}}],
               [{"text": "ok"}]])
    run_id = w.run()
    finished = next(e for e in w.events(run_id) if e["type"] == "tool_call_finished")
    assert finished["status"] == "error"
    assert w.aws.commands == []


@pytest.mark.parametrize(("user", "extra"), [
    ("amit", set()),
    ("geeta", {"propose_modify_tags", "propose_create_snapshot"}),
    ("lakhan", {"propose_modify_tags", "propose_create_snapshot", "propose_restore_test",
                "propose_modify_instance_class"}),
])
def test_read_user_has_no_propose_tools(world, store, user, extra):
    w = world([[{"text": "Hi."}]])
    conv = store.create_conversation(user)["id"]
    w.run("hello", user=user, conversation_id=conv)
    assert set(w.model.calls[0]["tool_names"]) == {"call_aws", "list_demo_databases"} | extra


def test_list_demo_databases(world):
    w = world([[{"tool": "list_demo_databases", "input": {}}], [{"text": "One database."}]])
    run_id = w.run()
    started, finished = [e for e in w.events(run_id) if e["type"].startswith("tool_call")]
    assert started["name"] == "list_demo_databases" and finished["status"] == "ok"
    assert finished["result"] == [{"identifier": "prod-a", "kind": "instance", "engine": "postgres",
                                   "instance_class": "db.t3.micro", "region": "us-east-1", "status": "available",
                                   "cluster": None, "members": []}]


def test_proposal_emits_change_proposed(world):
    w = world([[{"tool": "propose_create_snapshot", "input": {"identifier": "prod-a"}}], [{"text": "Proposed."}]])
    run_id = w.run()
    events = w.events(run_id)
    proposed = next(e for e in events if e["type"] == "change_proposed")
    assert proposed["change_request_id"] == "CR-2001"
    assert proposed["change_type"] == "create_snapshot" and proposed["risk_tier"] == "LOW"
    finished = next(e for e in events if e["type"] == "tool_call_finished")
    assert finished["status"] == "ok"
    assert finished["result"] == {"change_request_id": "CR-2001", "status": "pending",
                                  "note": "Needs approval on the Approvals page"}
    assert w.mock.bodies[0]["conversation_id"] == w.conversation_id
    assert w.last_message()["change_request_ids"] == ["CR-2001"]


def test_proposal_error_is_error_step(world):
    w = world([[{"tool": "propose_create_snapshot", "input": {"identifier": "other-db"}}], [{"text": "No."}]])
    run_id = w.run()
    events = w.events(run_id)
    finished = next(e for e in events if e["type"] == "tool_call_finished")
    assert finished["status"] == "error" and finished["result"] == {"error": "unknown database"}
    assert "change_proposed" not in types(events)


def test_tool_limit(settings, store):
    limited = load_settings({"DBOPS_USERS_FILE": str(settings.users_file), "DATABASE_URL": settings.database_url,
                             "RUN_MAX_TOOL_CALLS": "2"})
    call = aws("aws rds describe-db-instances")
    w = World(limited, store, [[call], [call], [call], [{"text": "never"}]])
    run_id = w.run()
    events = w.events(run_id)
    assert w.status(run_id) == "failed"
    assert events[-1]["type"] == "run_failed" and events[-1]["error"] == "Stopped after 2 AWS calls"
    assert len(w.aws.commands) == 2
    assert len(w.model.calls) == 3
    assert w.last_message()["text"].endswith("⛔ Stopped after 2 AWS calls")


def test_cancel(world, store):
    holder = {}
    w = world([[aws("aws rds describe-db-instances")], [{"text": "never shown"}]],
              on_turn=lambda i: i == 1 and store.request_cancel(holder["run_id"]))
    run_id = store.start_run(w.conversation_id, "lakhan", "go", "go")
    holder["run_id"] = run_id
    w.runner.start(run_id, CALLERS["lakhan"], w.conversation_id, "go")
    assert w.runner.join(run_id, timeout=5)
    events = w.events(run_id)
    assert events[-1]["type"] == "run_cancelled"
    assert "never shown" not in json.dumps(events)
    assert w.status(run_id) == "cancelled"
    assert w.last_message()["role"] == "assistant"


def test_model_error_mid_stream(world):
    w = world([[{"text": "Partial "}, RuntimeError("connection reset")]])
    run_id = w.run()
    events = w.events(run_id)
    assert events[-1]["type"] == "run_failed"
    assert events[-1]["error"].startswith("The language model call failed")
    assert "connection reset" in events[-1]["error"]
    assert w.status(run_id) == "failed"
    assert w.last_message()["text"].startswith("Partial ")


def test_model_api_error_fails_run(world):
    import httpx
    import openai

    error = openai.APIConnectionError(request=httpx.Request("POST", "https://api.deepseek.com/chat/completions"))
    w = world([error])
    run_id = w.run()
    assert w.events(run_id)[-1]["error"].startswith("The language model call failed: APIConnectionError")


def test_scope_unavailable_fails_run(world):
    w = world([[{"text": "never"}]], scope=FakeScope(fail=True))
    run_id = w.run()
    events = w.events(run_id)
    assert types(events) == ["run_failed"]
    assert events[0]["error"] == "Couldn't read the database list from AWS"
    assert w.model.calls == []
    assert w.status(run_id) == "failed"


def test_timeout(settings, store):
    quick = load_settings({"DBOPS_USERS_FILE": str(settings.users_file), "DATABASE_URL": settings.database_url,
                           "RUN_TIMEOUT_S": "0"})
    w = World(quick, store, [[{"text": "slow"}]])
    run_id = w.run()
    last = w.events(run_id)[-1]
    assert (last["type"], last["error"]) == ("run_failed", "Stopped after 0 seconds")
    assert w.status(run_id) == "failed"


@pytest.mark.parametrize(("seconds", "text"), [(300, "5 minutes"), (60, "1 minute"), (90, "90 seconds"),
                                               (1, "1 second")])
def test_duration_text(seconds, text):
    from agent_api.runner import duration_text

    assert duration_text(seconds) == text


def test_history_passed_to_model(world):
    w = world([[{"text": "First answer."}], [{"text": "Second answer."}]])
    w.run("first question")
    w.run("second question")
    second = w.model.calls[1]["messages"]
    assert [(m["role"], m["content"]) for m in second] == [
        ("user", [{"text": "first question"}]),
        ("assistant", [{"text": "First answer."}]),
        ("user", [{"text": "second question"}]),
    ]


def test_system_prompt_follows_role(world, store):
    w = world([[{"text": "Hi."}], [{"text": "Hi."}]])
    w.run("hello")
    w.run("hello", user="amit", conversation_id=store.create_conversation("amit")["id"])
    privileged, read = w.model.calls[0]["system_prompt"], w.model.calls[1]["system_prompt"]
    assert "list_demo_databases" in privileged and "prod-a" in privileged
    assert "propose_" in privileged and "Approvals page" in privileged
    assert "can't propose" in read


def test_start_never_raises(world, store):
    w = world([[{"text": "x"}]])

    class Broken:
        def __init__(self, *a, **kw):
            raise RuntimeError("no threads")

    w.runner._thread_cls = Broken
    run_id = store.start_run(w.conversation_id, "lakhan", "go", "go")
    w.runner.start(run_id, CALLERS["lakhan"], w.conversation_id, "go")
    assert w.status(run_id) == "failed"
    assert w.events(run_id)[-1]["type"] == "run_failed"


def test_events_validate_against_contract(world, contract):
    w = world(HAPPY)
    run_id = w.run()
    contract.check("agent-api", "get", "/runs/{run_id}/events", 200,
                   {"events": w.events(run_id), "run_status": w.status(run_id)})
    contract.check("agent-api", "get", "/conversations/{id}/messages", 200,
                   w.store.list_messages(w.conversation_id))


def test_default_model_is_deepseek(settings):
    from agent_api.agent_factory import build_agent
    from agent_api.hooks import RunHooks
    from strands.models.openai import OpenAIModel

    hooks = RunHooks("run-1", CALLERS["amit"], SCOPE, ("us-east-1",), {"call_aws"}, lambda e: None, lambda r: None,
                     15, lambda: False)
    agent = build_agent(settings, CALLERS["amit"], "conv-1", [], [], hooks, [])
    assert isinstance(agent.model, OpenAIModel)
    assert agent.model.get_config()["model_id"] == "deepseek-chat"
    assert agent.model.client_args == {"api_key": "", "base_url": "https://api.deepseek.com"}


@pytest.mark.parametrize(("tool_input", "command"), [
    ({"command": "aws rds describe-db-instances"}, "aws rds describe-db-instances"),
    ({"cli_command": "aws rds describe-db-instances"}, "aws rds describe-db-instances"),
    ({"cli_command": ["aws rds describe-db-instances"]}, None),
    ({"cli_command": "aws rds describe-db-instances", "max_results": 5}, "aws rds describe-db-instances"),
    ({"command": "aws rds describe-db-instances", "max_results": None}, "aws rds describe-db-instances"),
    ({"command": "aws rds describe-db-instances", "max_results": 1000}, "aws rds describe-db-instances"),
    ({"command": "aws rds describe-db-instances", "max_results": 0}, None),
    ({"command": "aws rds describe-db-instances", "max_results": 1001}, None),
    ({"command": "aws rds describe-db-instances", "max_results": "5"}, None),
    ({"command": "aws rds describe-db-instances", "max_results": True}, None),
    ({"command": "aws rds describe-db-instances", "other": 1}, None),
    ({"max_results": 5}, None),
    ({}, None),
    ("aws rds describe-db-instances", None),
])
def test_aws_command_input(tool_input, command):
    from agent_api.hooks import aws_command

    assert aws_command(tool_input) == command


DELETE = "aws rds delete-db-instance --db-instance-identifier prod-a"
DESCRIBE_ALL = "aws rds describe-db-instances"


@pytest.mark.parametrize("turns", [
    pytest.param([[{**aws(DELETE), "id": "call_0"}], [{**aws(DESCRIBE_ALL), "id": "call_0"}], [{"text": "Done."}]],
                 id="later-turn"),
    pytest.param([[{**aws(DELETE), "id": "call_0"}, {**aws(DESCRIBE_ALL), "id": "call_0"}], [{"text": "Done."}]],
                 id="same-turn"),
])
def test_duplicate_tool_use_id_still_filtered(world, turns):
    w = world(turns)
    run_id = w.run()
    events = w.events(run_id)
    finished = [e for e in events if e["type"] == "tool_call_finished"]
    assert [f["status"] for f in finished] == ["error", "ok"]
    assert [r["DBInstanceIdentifier"] for r in finished[1]["result"]["DBInstances"]] == ["prod-a"]
    assert "other-db" not in json.dumps(events)
    assert "other-db" not in json.dumps(w.model.calls[-1]["messages"])
    assert "other-db" not in json.dumps(w.store.list_messages(w.conversation_id))
    assert w.aws.commands == [f"{DESCRIBE_ALL} --region us-east-1"]
    assert [a["result"] for a in w.mock.audits if a["action"] == "chat.tool_call"] == ["refused", "ok"]


# ---- Final-review fixes F1, F2, F4

REGIONAL = "aws rds describe-db-instances --region us-east-1"
SECRET = "Cannot use other-db with prod-a in 123456789012"


def test_aws_error_result_readable(world):
    command = "aws rds describe-db-instances --db-instance-identifier prod-a"
    responses = {"rds describe-db-instances": mcp_error_text(
        command, code="InvalidParameterCombination",
        message=f"An error occurred (InvalidParameterCombination) when calling the DescribeDBInstances operation: {SECRET}")}
    w = world([[aws(command)], [{"text": "AWS said no."}]], responses=responses)
    run_id = w.run()
    events = w.events(run_id)
    finished = next(e for e in events if e["type"] == "tool_call_finished")
    assert finished["status"] == "error"
    assert finished["result"] == {"error": "AWS returned an error", "code": "InvalidParameterCombination"}
    for seen in (json.dumps(events), json.dumps(w.model.calls[1]["messages"]),
                 json.dumps(w.store.list_messages(w.conversation_id))):
        assert SECRET not in seen and "other-db" not in seen
    assert "AWS returned an error" in json.dumps(w.model.calls[1]["messages"])
    row = next(a for a in w.mock.audits if a["action"] == "chat.tool_call")
    assert row["result"] == "error"


@pytest.mark.parametrize(("error_text", "code"), [
    ("Error while executing the command: An error occurred (AccessDenied) when calling X: user other-db", "AccessDenied"),
    ("Error while executing the command: No AWS credentials found.", None),
])
def test_aws_top_level_error_code_parsed(world, error_text, code):
    command = "aws rds describe-db-instances"
    w = world([[aws(command)], [{"text": "AWS said no."}]],
              responses={"rds describe-db-instances": mcp_error_text(command, top_level=error_text)})
    finished = next(e for e in w.events(w.run()) if e["type"] == "tool_call_finished")
    expected = {"error": "AWS returned an error"} | ({"code": code} if code else {})
    assert finished["status"] == "error" and finished["result"] == expected
    assert "other-db" not in json.dumps(w.model.calls[1]["messages"])


def test_aws_validation_failure_readable(world):
    command = "aws rds describe-db-instances"
    w = world([[aws(command)], [{"text": "Bad syntax."}]],
              responses={"rds describe-db-instances": mcp_error_text(command, validation=True)})
    run_id = w.run()
    finished = next(e for e in w.events(run_id) if e["type"] == "tool_call_finished")
    assert finished["status"] == "error"
    assert finished["result"] == {"error": "The AWS CLI rejected the command syntax"}
    assert "Invalid choice" not in json.dumps(w.model.calls[1]["messages"])


def test_max_results_accepted_and_stripped(world):
    w = world([[{"tool": "call_aws", "input": {"command": "aws rds describe-db-instances", "max_results": 5}}],
               [{"text": "prod-a is available."}]])
    run_id = w.run()
    finished = next(e for e in w.events(run_id) if e["type"] == "tool_call_finished")
    assert finished["status"] == "ok"
    assert [r["DBInstanceIdentifier"] for r in finished["result"]["DBInstances"]] == ["prod-a"]
    assert w.aws.commands == [REGIONAL]
    assert w.aws.max_results == [None]  # stripped before the server saw it


@pytest.mark.parametrize("max_results", [0, 1001, "5", True])
def test_bad_max_results_refused(world, max_results):
    w = world([[{"tool": "call_aws", "input": {"command": "aws rds describe-db-instances",
                                               "max_results": max_results}}], [{"text": "ok"}]])
    finished = next(e for e in w.events(w.run()) if e["type"] == "tool_call_finished")
    assert finished["status"] == "error" and "could not parse command" in json.dumps(finished["result"])
    assert w.aws.commands == []


@pytest.mark.parametrize(("command", "sent"), [
    ("aws rds describe-db-instances", REGIONAL),
    ("aws rds describe-db-instances --region us-east-1", REGIONAL),
    ("aws rds describe-db-instances --region=us-east-1", "aws rds describe-db-instances --region=us-east-1"),
])
def test_region_injected_when_absent(world, command, sent):
    w = world([[aws(command)], [{"text": "ok"}]])
    finished = next(e for e in w.events(w.run()) if e["type"] == "tool_call_finished")
    assert finished["status"] == "ok"
    assert w.aws.commands == [sent]
