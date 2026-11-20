"""Strands hooks for one run: command guard, call limit, result filter, tool-step events and audit (§7.3, §7.4, §7.7)."""

import json
import logging
import shlex
import time
from collections.abc import Callable
from typing import Any

from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookProvider, HookRegistry

from agent_api.guard import UNPARSEABLE, check_command, step_name, target_region
from agent_api.identity import Caller
from agent_api.result_filter import filter_result, truncate, unwrap_tool_result
from agent_api.scope import ScopeSet

log = logging.getLogger(__name__)
# The AWS API MCP server names its command argument cli_command; our fakes and older docs say command.
COMMAND_KEYS = ("command", "cli_command")
_DB_FLAGS = ("--db-instance-identifier", "--db-cluster-identifier", "--source-identifier", "--resource-name")
LIMIT_REASON = "tool call limit reached for this run"
CANCELLED_REASON = "run cancelled"


def aws_command(tool_input: Any) -> str | None:
    """The single command string of an AWS tool call, or None if the input is anything else."""
    if not isinstance(tool_input, dict) or len(tool_input) != 1:
        return None
    key, value = next(iter(tool_input.items()))
    return value if key in COMMAND_KEYS and isinstance(value, str) else None


def first_db_reference(command: str) -> str | None:
    """For the audit row only (the guard makes the safety decisions)."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    for i, tok in enumerate(tokens):
        name, eq, value = tok.partition("=")
        if name in _DB_FLAGS:
            if eq:
                return value
            if i + 1 < len(tokens):
                return tokens[i + 1]
    return None


def _content_value(tool_result: dict) -> Any:
    """What a step shows: the parsed JSON of a single text block, else the text, else the raw content."""
    content = tool_result.get("content") or []
    if len(content) == 1 and isinstance(content[0], dict) and isinstance(content[0].get("text"), str):
        try:
            return json.loads(content[0]["text"])
        except ValueError:
            return content[0]["text"]
    return content


class RunHooks(HookProvider):
    def __init__(self, run_id: str, caller: Caller, scope_set: ScopeSet, regions: tuple[str, ...],
                 aws_tool_names: set[str], emit: Callable[[dict], None], audit: Callable[[dict], None],
                 max_tool_calls: int, cancelled: Callable[[], bool], clock: Callable[[], float] = time.monotonic):
        self.run_id = run_id
        self.caller = caller
        self.scope_set = scope_set
        self.regions = tuple(regions)
        self.aws_tool_names = set(aws_tool_names)
        self.emit = emit
        self.audit = audit
        self.max_tool_calls = max_tool_calls
        self.cancelled = cancelled
        self.clock = clock
        self.calls = 0
        self.limit_reached = False
        self._started: dict[str, float] = {}
        self._refused: set[str] = set()

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before_tool)
        registry.add_callback(AfterToolCallEvent, self.after_tool)

    def _step(self, name: str, tool_input: Any) -> str:
        if name not in self.aws_tool_names:
            return name
        command = aws_command(tool_input)
        return step_name(command) if command is not None else "aws"

    def before_tool(self, event: BeforeToolCallEvent) -> None:
        tool_use = event.tool_use
        name, tool_input, call_id = tool_use["name"], tool_use.get("input"), str(tool_use["toolUseId"])
        self.calls += 1
        reason = None
        if self.cancelled():
            reason = CANCELLED_REASON
            event.agent.cancel()
        elif self.calls > self.max_tool_calls:
            reason = LIMIT_REASON
            self.limit_reached = True
            event.agent.cancel()  # no further model call
        elif name in self.aws_tool_names:
            command = aws_command(tool_input)
            reason = UNPARSEABLE if command is None else check_command(command, self.scope_set, self.regions)
        if reason is not None:
            event.cancel_tool = reason
            self._refused.add(call_id)
        self.emit({"type": "tool_call_started", "tool_call_id": call_id, "name": self._step(name, tool_input),
                   "args": tool_input if isinstance(tool_input, dict) else {"input": tool_input}})
        self._started[call_id] = self.clock()

    def after_tool(self, event: AfterToolCallEvent) -> None:
        tool_use = event.tool_use
        name, tool_input, call_id = tool_use["name"], tool_use.get("input"), str(tool_use["toolUseId"])
        refused = call_id in self._refused
        original = event.result
        ok = original.get("status") == "success"
        if name in self.aws_tool_names and not refused:
            command = aws_command(tool_input)
            region = target_region(command, self.regions)
            shown, ok = filter_result(unwrap_tool_result(original), self.scope_set, region)
            replace = True
        else:
            shown, replace = _content_value(original), False
        shown, truncated = truncate(shown)
        if replace or truncated:
            # A fresh ToolResult: the original (and any unfiltered structuredContent) never reaches history.
            event.result = {"toolUseId": original["toolUseId"], "status": "success" if ok else "error",
                            "content": [{"text": json.dumps(shown)}]}
        started = self._started.pop(call_id, self.clock())
        finished = {"type": "tool_call_finished", "tool_call_id": call_id, "status": "ok" if ok else "error",
                    "result": shown, "duration_ms": max(0, int((self.clock() - started) * 1000))}
        if truncated:
            finished["truncated"] = True
        self.emit(finished)
        if name in self.aws_tool_names:
            command = aws_command(tool_input)
            database = first_db_reference(command) if command is not None else None
        else:
            database = tool_input.get("identifier") if isinstance(tool_input, dict) else None
        self.audit({"user": self.caller.username, "action": "chat.tool_call", "tool": self._step(name, tool_input),
                    "database": database if isinstance(database, str) else None,
                    "result": "refused" if refused else ("ok" if ok else "error"),
                    "detail": json.dumps(tool_input)[:200]})
