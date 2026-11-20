"""Test doubles for the Strands run worker: a scripted model and a canned `call_aws` tool."""

import copy
import json
import shlex
from collections.abc import AsyncIterable, Callable
from typing import Any

from strands import tool
from strands.models import Model


class ScriptedModel(Model):
    """Plays one scripted turn per model call.

    A turn is a list of `{"text": str}` / `{"tool": name, "input": dict, "id"?: toolUseId}` items, or an exception instance
    (raised from `stream()`). A turn whose list ends with an exception yields the items before it, then raises.
    Each call records the messages, system prompt and tool names it was given.
    """

    def __init__(self, turns: list, on_turn: Callable[[int], None] | None = None):
        self._turns = list(turns)
        self._on_turn = on_turn
        self.calls: list[dict[str, Any]] = []

    def update_config(self, **model_config: Any) -> None:
        pass

    def get_config(self) -> Any:
        return {}

    def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        raise NotImplementedError

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs) -> AsyncIterable[Any]:
        index = len(self.calls)
        self.calls.append({"messages": copy.deepcopy(messages), "system_prompt": system_prompt,
                           "tool_names": sorted(s["name"] for s in tool_specs or [])})
        if self._on_turn is not None:
            self._on_turn(index)
        if index >= len(self._turns):
            raise AssertionError(f"ScriptedModel: no turn {index + 1} in the script")
        turn = self._turns[index]
        if isinstance(turn, BaseException):
            raise turn
        yield {"messageStart": {"role": "assistant"}}
        used_tool = False
        for n, item in enumerate(turn):
            if isinstance(item, BaseException):
                raise item
            if "text" in item:
                yield {"contentBlockStart": {"start": {}}}
                yield {"contentBlockDelta": {"delta": {"text": item["text"]}}}
            else:
                used_tool = True
                use_id = item.get("id", f"tu-{index + 1}-{n + 1}")
                yield {"contentBlockStart": {"start": {"toolUse": {"name": item["tool"], "toolUseId": use_id}}}}
                yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(item["input"])}}}}
            yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use" if used_tool else "end_turn"}}


def mcp_text(body: Any, command: str) -> str:
    """The text block awslabs.aws-api-mcp-server 1.6.1 `call_aws` returns for one successful command."""
    return json.dumps([{
        "cli_command": command,
        "response": {"error": None, "status_code": 200, "error_code": None, "pagination_token": None,
                     "as_json": json.dumps({**body, "ResponseMetadata": {"RequestId": "r-1", "HTTPStatusCode": 200}})},
        "metadata": {"service": "rds", "service_full_name": "Amazon RDS", "operation": "op", "region_name": "us-east-1"},
        "validation_failures": None, "missing_context_failures": None, "failed_constraints": None,
    }])


class RawText(str):
    """A canned `call_aws` response given as the exact text block (e.g. an error CallAWSResponse)."""


def mcp_error_text(command: str, *, code: str | None = None, message: str | None = None,
                   top_level: str | None = None, validation: bool = False) -> RawText:
    """The text block awslabs.aws-api-mcp-server 1.6.1 `call_aws` returns for one failed command.

    `code`/`message`: AWS returned an error (botocore ClientError). `top_level`: the server raised (the
    CallAWSResponse carries only cli_command and error). `validation`: the CLI rejected the command.
    """
    if top_level is not None:
        return RawText(json.dumps([{"cli_command": command, "error": top_level}]))
    if validation:
        return RawText(json.dumps([{
            "cli_command": command, "response": None, "metadata": None,
            "validation_failures": [{"reason": "Invalid choice: 'describe-db-instancez'", "context": {"args": []}}],
            "missing_context_failures": None, "failed_constraints": []}]))
    return RawText(json.dumps([{
        "cli_command": command,
        "response": {"error": message, "status_code": 400, "error_code": code, "pagination_token": None,
                     "as_json": None},
        "metadata": {"service": "rds", "service_full_name": "Amazon RDS", "operation": "op", "region_name": "us-east-1"},
        "validation_failures": None, "missing_context_failures": None, "failed_constraints": None}]))


def fake_call_aws(responses: dict[str, Any]):
    """A Strands tool named `call_aws` returning canned AWS output keyed by "<service> <operation>".

    The result has the MCP ToolResult shape (one text block plus structuredContent holding the raw body),
    so tests can check that the unfiltered structuredContent never reaches the model.
    The commands it ran are in `.commands`, the max_results it was given in `.max_results`.
    """
    commands: list[str] = []
    max_results_seen: list[int | None] = []

    @tool(name="call_aws")
    def call_aws(command: str, max_results: int | None = None) -> dict:
        """Run one AWS CLI command.

        Args:
            command: The full AWS CLI command.
            max_results: Optional limit for number of results.
        """
        commands.append(command)
        max_results_seen.append(max_results)
        words = shlex.split(command)
        body = responses.get(" ".join(words[1:3]))
        if body is None:
            return {"status": "error", "content": [{"text": "no canned response"}], "isError": True}
        text = body if isinstance(body, RawText) else mcp_text(body, command)
        return {"status": "success", "content": [{"text": text}],
                "structuredContent": {"result": [{"raw": body}]}, "isError": False}

    call_aws.commands = commands
    call_aws.max_results = max_results_seen
    return call_aws
