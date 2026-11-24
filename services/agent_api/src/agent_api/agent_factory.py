"""Builds the Strands agent for one run (design §7.1)."""

from typing import Any

from strands import Agent
from strands.models.litellm import LiteLLMModel
from strands.tools.executors import SequentialToolExecutor

from agent_api.hooks import RunHooks
from agent_api.identity import Caller
from agent_api.prompt import system_prompt
from agent_api.settings import Settings


def history_messages(history: list[dict]) -> list[dict]:
    """Prior turns as Strands messages: user and assistant text only (tool steps are not replayed)."""
    return [{"role": m["role"], "content": [{"text": m["text"]}]}
            for m in history if m["role"] in ("user", "assistant") and m["text"].strip()]


def llm_client_args(settings: Settings) -> dict[str, str]:
    """Only the overrides that are set; otherwise LiteLLM reads the provider's own key variable."""
    args = {"api_key": settings.llm_api_key, "api_base": settings.llm_api_base}
    return {k: v for k, v in args.items() if v}


def build_agent(settings: Settings, caller: Caller, conversation_id: str, history: list[dict], aws_tools: list,
                hooks: RunHooks, extra_tools: list, model: Any = None) -> Agent:
    if model is None:
        model = LiteLLMModel(client_args=llm_client_args(settings), model_id=settings.llm_model)
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
