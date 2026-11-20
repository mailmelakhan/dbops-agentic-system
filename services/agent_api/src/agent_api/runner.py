"""The run worker: drives one Strands agent per run and records its events (design §7.1)."""

import asyncio
import contextlib
import logging
import threading
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from agent_api.agent_factory import build_agent
from agent_api.hooks import RunHooks
from agent_api.identity import Caller
from agent_api.mock_client import MockClient
from agent_api.proposals import Proposer
from agent_api.scope import ScopeResolver, ScopeUnavailable
from agent_api.settings import Settings
from agent_api.store import Store
from agent_api.tools import build_tools

log = logging.getLogger(__name__)
TEXT_FLUSH_S = 0.1
# Harmless documentation tools; every other AWS MCP tool goes through the command guard and result filter.
UNGUARDED_AWS_TOOLS = frozenset({"search_documentation", "aws___search_documentation",
                                 "read_documentation", "aws___read_documentation"})
_TERMINAL = {"run_completed": "completed", "run_failed": "failed", "run_cancelled": "cancelled"}


def duration_text(seconds: float) -> str:
    if seconds >= 60 and seconds % 60 == 0:
        minutes = int(seconds // 60)
        return f"{minutes} minute{'' if minutes == 1 else 's'}"
    n = int(seconds) if float(seconds).is_integer() else seconds
    return f"{n} second{'' if n == 1 else 's'}"


def _short(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {text[:200]}" if text else type(exc).__name__


def assistant_message(run_id: str, events: list[dict]) -> dict:
    """Same rules as mock_backend.runs._assistant_message."""
    text, steps, crs = "", {}, []
    for e in events:
        match e["type"]:
            case "text_delta":
                text += e["text"]
            case "tool_call_started":
                steps[e["tool_call_id"]] = {"tool_call_id": e["tool_call_id"], "name": e["name"], "args": e["args"],
                                            "status": "error", "result": None, "truncated": False, "duration_ms": 0}
            case "tool_call_finished":
                steps[e["tool_call_id"]].update(status=e["status"], result=e["result"],
                                                truncated=e.get("truncated", False), duration_ms=e["duration_ms"])
            case "change_proposed":
                crs.append(e["change_request_id"])
            case "run_failed":
                text += f"\n\n⛔ {e['error']}" if text else f"⛔ {e['error']}"
    return {"role": "assistant", "text": text, "tool_steps": list(steps.values()), "change_request_ids": crs,
            "run_id": run_id}


class _Run:
    """Event recording for one run: text is buffered and flushed before any other event."""

    def __init__(self, store: Store, run_id: str):
        self.store = store
        self.run_id = run_id
        self.events: list[dict] = []
        self._text = ""
        self._text_since = 0.0
        self._lock = threading.Lock()  # sync tools run in worker threads and may emit

    def text(self, chunk: str) -> None:
        with self._lock:
            if not self._text:
                self._text_since = time.monotonic()
            self._text += chunk
            if time.monotonic() - self._text_since >= TEXT_FLUSH_S:
                self._flush()

    def emit(self, event: dict) -> None:
        with self._lock:
            self._flush()
            self.events.append(self.store.append_event(self.run_id, event))

    def flush(self) -> None:
        with self._lock:
            self._flush()

    def _flush(self) -> None:
        if self._text:
            text, self._text = self._text, ""
            self.events.append(self.store.append_event(self.run_id, {"type": "text_delta", "text": text}))


class Runner:
    def __init__(self, settings: Settings, store: Store, scope: ScopeResolver, proposer: Proposer, mock: MockClient,
                 aws_tools_provider: Callable[[], AbstractContextManager[list]],
                 model_factory: Callable[[], Any] | None = None):
        self._settings = settings
        self._store = store
        self._scope = scope
        self._proposer = proposer
        self._mock = mock
        self._aws_tools_provider = aws_tools_provider
        self._model_factory = model_factory
        self._threads: dict[str, threading.Thread] = {}
        self._thread_cls = threading.Thread

    def start(self, run_id: str, caller: Caller, conversation_id: str, message: str) -> None:
        """Never raises: the run is already committed, so a launch failure is recorded as a failed run."""
        try:
            def work() -> None:
                try:
                    asyncio.run(self._drive(run_id, caller, conversation_id, message))
                finally:
                    self._threads.pop(run_id, None)

            thread = self._thread_cls(target=work, name=f"run-{run_id}", daemon=True)
            self._threads[run_id] = thread
            thread.start()
        except Exception as exc:
            log.exception("could not start run %s", run_id)
            try:
                error = f"Couldn't start the agent: {_short(exc)}"
                self._store.append_event(run_id, {"type": "run_failed", "error": error})
                self._store.finish_run(run_id, "failed", error)
            except Exception:
                log.exception("could not record the failed start of run %s", run_id)

    def join(self, run_id: str, timeout: float | None = None) -> bool:
        """Test helper: wait for a run's worker; True if it finished."""
        thread = self._threads.get(run_id)
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    async def _drive(self, run_id: str, caller: Caller, conversation_id: str, message: str) -> None:
        run = _Run(self._store, run_id)
        terminal: dict | None = None
        try:
            try:
                scope_set = self._scope.current()
            except ScopeUnavailable as exc:
                terminal = {"type": "run_failed", "error": str(exc)}
                return
            run.emit({"type": "run_started"})
            self._mock.audit({"user": caller.username, "action": "chat.message", "tool": None, "database": None,
                              "result": "ok", "detail": message[:200]})
            history = self._store.list_messages(conversation_id)
            if history and history[-1]["role"] == "user" and history[-1]["text"] == message:
                history = history[:-1]  # the current message; stream_async adds it
            with self._aws_tools_provider() as aws_tools:
                hooks = RunHooks(
                    run_id, caller, scope_set, self._settings.aws_regions,
                    aws_tool_names={t.tool_name for t in aws_tools} - UNGUARDED_AWS_TOOLS,
                    emit=run.emit, audit=self._mock.audit, max_tool_calls=self._settings.run_max_tool_calls,
                    cancelled=lambda: self._store.cancel_requested(run_id))
                tools = build_tools(caller, conversation_id, self._scope, self._proposer, run.emit)
                agent = build_agent(self._settings, caller, conversation_id, history, aws_tools, hooks, tools,
                                    model=self._model_factory() if self._model_factory else None)
                terminal = await self._consume(run, agent, hooks, message)
        except Exception as exc:  # every failure must end the run, never leave it running
            log.exception("run %s failed", run_id)
            if terminal is None:  # an error closing the AWS tools after the run ended doesn't change its outcome
                terminal = {"type": "run_failed", "error": f"Couldn't start the agent: {_short(exc)}"}
        finally:
            if terminal is None:  # e.g. cancelled from outside the worker
                terminal = {"type": "run_failed", "error": "The run stopped unexpectedly"}
            self._finish(run, conversation_id, terminal)

    async def _consume(self, run: _Run, agent: Any, hooks: RunHooks, message: str) -> dict:
        settings = self._settings
        deadline = time.monotonic() + settings.run_timeout_s
        timed_out = {"type": "run_failed", "error": f"Stopped after {duration_text(settings.run_timeout_s)}"}

        def stop_reason() -> dict | None:
            if self._store.cancel_requested(run.run_id):
                return {"type": "run_cancelled"}
            if hooks.limit_reached:
                return {"type": "run_failed", "error": f"Stopped after {settings.run_max_tool_calls} AWS calls"}
            if time.monotonic() >= deadline:
                return timed_out
            return None

        try:
            async with contextlib.aclosing(agent.stream_async(message)) as stream:
                async with asyncio.timeout(max(0.0, deadline - time.monotonic())):
                    async for event in stream:
                        if isinstance(event, dict) and isinstance(event.get("data"), str):
                            run.text(event["data"])
                        if (stop := stop_reason()) is not None:
                            agent.cancel()
                            return stop
        except TimeoutError:
            return timed_out
        except Exception as exc:  # includes openai.APIError
            log.warning("model call failed in run %s", run.run_id, exc_info=True)
            return stop_reason() or {"type": "run_failed", "error": f"The language model call failed: {_short(exc)}"}
        return stop_reason() or {"type": "run_completed"}

    def _finish(self, run: _Run, conversation_id: str, terminal: dict) -> None:
        try:
            run.flush()
            run.emit(terminal)
            self._store.add_message(conversation_id, assistant_message(run.run_id, run.events))
        except Exception:
            log.exception("could not record the end of run %s", run.run_id)
        finally:
            self._store.finish_run(run.run_id, _TERMINAL[terminal["type"]], terminal.get("error"))
