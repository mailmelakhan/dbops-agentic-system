"""Builds the Strands agent for one run (design §7.1)."""

from typing import Any

from strands import Agent
from strands.models.openai import OpenAIModel
from strands.tools.executors import SequentialToolExecutor

from agent_api.hooks import RunHooks
from agent_api.identity import Caller
from agent_api.prompt import system_prompt
from agent_api.settings import Settings


def history_messages(history: list[dict]) -> list[dict]:
    """Prior turns as Strands messages: user and assistant text only (tool steps are not replayed)."""
    return [{"role": m["role"], "content": [{"text": m["text"]}]}
            for m in history if m["role"] in ("user", "assistant") and m["text"].strip()]


def build_agent(settings: Settings, caller: Caller, conversation_id: str, history: list[dict], aws_tools: list,
                hooks: RunHooks, extra_tools: list, model: Any = None) -> Agent:
    if model is None:
        model = OpenAIModel(client_args={"api_key": settings.deepseek_api_key, "base_url": settings.deepseek_base_url},
                            model_id=settings.deepseek_model)
    return Agent(
        model=model,
        messages=history_messages(history),
        tools=[*aws_tools, *extra_tools],
        system_prompt=system_prompt(caller.role, hooks.scope_set),
        callback_handler=None,
        hooks=[hooks],
        # One tool at a time: the call limit, cancel checks and step order stay simple to reason about.
        tool_executor=SequentialToolExecutor(),
        name=f"dbops-{conversation_id}",
    )
