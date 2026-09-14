"""
Strands hook provider that records a Buyer Agent conversation's reasoning
steps (tool calls, tool results, interleaved reasoning text, and the final
response) into DynamoDB via session_store.py, as they actually happen.

Wired into every Agent build_agent() constructs (agent.py), so it applies
uniformly regardless of which transport invoked the agent — the local
chat UI's HTTP /invocations path, or an external agent's A2A call — per
R5 of pick_up_external_invocation.md ("regardless of caller").

Every step recorded here reflects a real hook firing on a real agent
invocation (a real model call, a real tool call to a real seller agent).
Nothing is synthesized to "look" like reasoning; if session recording is
unavailable (table not configured, a transient DynamoDB error), steps are
silently dropped rather than faked, and the agent's actual behavior is
unaffected either way.
"""

import logging
import time
from typing import Any

from strands.hooks import (
    AfterInvocationEvent,
    AfterModelCallEvent,
    AfterToolCallEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)

import session_store
from session_store import SessionStoreError

logger = logging.getLogger(__name__)


def _safe(fn, *args, **kwargs) -> None:
    """Run a session_store call, swallowing (and logging) any failure so a
    DynamoDB hiccup or missing table never breaks the actual agent
    invocation — recording reasoning steps is strictly best-effort.
    """
    try:
        fn(*args, **kwargs)
    except SessionStoreError as exc:
        logger.warning("session recording skipped: %s", exc)
    except Exception:  # noqa: BLE001 - never let telemetry recording break the agent
        logger.exception("session recording failed unexpectedly")


class ReasoningRecorder(HookProvider):
    """Records reasoning steps for `session_id` (one recorder per Agent).

    Instances are created in agent.py's build_agent(), so an instance's
    lifetime is that of its Agent — which can span several turns of one
    session when the agent is cached (the A2A entrypoint keeps one agent per
    AgentCore runtime session). That's safe: tool start times are keyed by
    toolUseId and popped when the call finishes. `invoker` is likewise fixed
    at build time, so it reflects whoever opened the session; the session's
    META record is refreshed with the current turn's invoker separately by
    session_store.start_turn().
    """

    def __init__(self, session_id: str, invoker: str) -> None:
        self.session_id = session_id
        self.invoker = invoker
        self._tool_started_at: dict[str, float] = {}

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(BeforeToolCallEvent, self._on_before_tool)
        registry.add_callback(AfterToolCallEvent, self._on_after_tool)
        registry.add_callback(AfterModelCallEvent, self._on_after_model)
        registry.add_callback(AfterInvocationEvent, self._on_after_invocation)

    def _on_before_tool(self, event: BeforeToolCallEvent) -> None:
        tool_use = event.tool_use or {}
        call_id = tool_use.get("toolUseId", "")
        self._tool_started_at[call_id] = time.monotonic()
        _safe(
            session_store.record_step,
            self.session_id,
            "tool_call",
            {"toolName": tool_use.get("name"), "input": tool_use.get("input"), "callId": call_id},
        )

    def _on_after_tool(self, event: AfterToolCallEvent) -> None:
        tool_use = event.tool_use or {}
        call_id = tool_use.get("toolUseId", "")
        started_at = self._tool_started_at.pop(call_id, None)
        duration_ms = int((time.monotonic() - started_at) * 1000) if started_at is not None else None

        result = event.result
        if isinstance(result, BaseException):
            status = "error"
            output: Any = f"{type(result).__name__}: {result}"
        else:
            status = (result or {}).get("status", "unknown")
            content = (result or {}).get("content", [])
            output = ""
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    output = block["text"]
                    break
            else:
                output = content

        _safe(
            session_store.record_step,
            self.session_id,
            "tool_result",
            {
                # Carried on the result as well as the call: a consumer reading
                # a result step (or one that attached mid-session and never saw
                # the matching tool_call) otherwise has only an opaque callId
                # and nothing to name the tool with.
                "toolName": tool_use.get("name"),
                "callId": call_id,
                "status": status,
                "output": output,
                "durationMs": duration_ms,
            },
        )

    def _on_after_model(self, event: AfterModelCallEvent) -> None:
        stop_response = event.stop_response
        if stop_response is None:
            return
        message = stop_response.message or {}
        if message.get("role") != "assistant":
            return

        for block in message.get("content", []) or []:
            if not isinstance(block, dict):
                continue
            reasoning = block.get("reasoningContent")
            if isinstance(reasoning, dict):
                reasoning_text = reasoning.get("reasoningText", {}).get("text")
                if reasoning_text:
                    _safe(session_store.record_step, self.session_id, "thought", {"text": reasoning_text})
                continue
            # A plain text block emitted on a turn that goes on to call a
            # tool (stop_reason == "tool_use") is the model's interleaved
            # commentary before acting — surfaced as a "thought" step. Text
            # emitted on the turn that ends the conversation (end_turn) is
            # the real final answer, recorded once as the "response" step
            # in _on_after_invocation instead, so it isn't duplicated here.
            text = block.get("text")
            if text and stop_response.stop_reason not in ("end_turn", "stop"):
                _safe(session_store.record_step, self.session_id, "thought", {"text": text})

    def _on_after_invocation(self, event: AfterInvocationEvent) -> None:
        result = event.result
        if result is None:
            # Invocation didn't complete normally (raised before producing a
            # result) — the transport layer's own except block is
            # responsible for calling session_store.error_turn() with the
            # real exception message; nothing to record here.
            return

        final_text = ""
        message = getattr(result, "message", None) or {}
        for block in message.get("content", []) or []:
            if isinstance(block, dict) and "text" in block:
                final_text += block["text"]

        _safe(
            session_store.record_step,
            self.session_id,
            "response",
            {"to": self.invoker, "text": final_text},
        )
        _safe(session_store.complete_turn, self.session_id)
