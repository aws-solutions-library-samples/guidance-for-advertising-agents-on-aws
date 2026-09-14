"""
A2A entrypoint for the Buyer Agent — makes it genuinely invokable by other
agents over the Agent-to-Agent protocol, not just callable from this
project's own chat UI over HTTP. This is a separate AgentCore Runtime
deployment from app.py's HTTP runtime (AgentCore Runtime deploys one
protocol per runtime: HTTP on port 8080 mounted at /invocations, A2A on
port 9000 mounted at /) — see deploy_buyer_agent_a2a.py.

Both entrypoints build the exact same underlying agent (agent.py's
build_agent(), same tools, same seller registry, same reasoning-step
recording via ReasoningRecorder) — this file only adapts the A2A wire
protocol to that shared agent, per R5 of pick_up_external_invocation.md:
"add IoT [here: DynamoDB] publishing at each reasoning checkpoint" in "the
runtime code that handles A2A invocations", "regardless of caller".

Per R5's guidance to use the prompt already in hand rather than a separate
lookup: the invoking agent's declared name and message text are read
directly from the A2A request (params.metadata.invokerAgent and the
message's text parts via context.get_user_input()) with no additional
round-trip.

Sessions are AgentCore Runtime's, not this module's. The conversation key
is the runtimeSessionId the platform already carries on every invocation
(the X-Amzn-Bedrock-AgentCore-Runtime-Session-Id header, which the A2A
client sets and AgentCore validates at >= 33 chars) — the same id the HTTP
entrypoint uses via context.session_id in app.py. See
_resolve_session_id() for why A2A's own context_id cannot serve as that
key, and _align_a2a_context_id() for how the A2A layer is repointed at it.
"""

import logging
import os
from contextvars import ContextVar
from dataclasses import dataclass

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from bedrock_agentcore.runtime import BedrockAgentCoreContext, serve_a2a
from dotenv import load_dotenv
from strands.multiagent.a2a.executor import StrandsA2AExecutor

import session_store
from agent import build_agent
from agents_registry import BUYER_A2A_AGENT_ID, BUYER_A2A_AGENT_NAME
from seller_agents import SellerAgentError, get_default_seller_agent_id
from session_store import SessionStoreError

load_dotenv()
logger = logging.getLogger(__name__)

# AgentCore Runtime enforces runtimeSessionId >= 33 chars on the invoke API,
# so a real platform-supplied id always clears this. The padding below only
# matters for the local-dev fallback path, where an id can come from a
# caller-chosen A2A context_id instead — see agent.py::MIN_SESSION_ID_LENGTH,
# which rejects anything shorter.
_MIN_SESSION_ID_LENGTH = 33


@dataclass(frozen=True)
class _Turn:
    """Per-request facts the strands agent_factory needs but isn't handed.

    strands calls the factory with just an A2A context_id, while the
    runtime session id, the invoker's declared name and the requested
    seller all come off the incoming request. This is request-scoped data
    only — the durable conversation lives in the AgentCore session and its
    S3-backed Strands session, not here.
    """

    session_id: str
    invoker: str
    seller_agent_id: str | None


# Set in execute(), read in _build_agent_for_context(). A ContextVar (rather
# than an instance dict keyed by context_id) because the factory is called
# synchronously inside the same asyncio task as the execute() that set it,
# so nothing needs to be retained after the turn ends.
_current_turn: ContextVar[_Turn | None] = ContextVar("adcp_buyer_a2a_turn", default=None)


def _pad_session_id(raw: str) -> str:
    return raw if len(raw) >= _MIN_SESSION_ID_LENGTH else raw.ljust(_MIN_SESSION_ID_LENGTH, "0")


def _resolve_session_id(context: RequestContext) -> tuple[str, str]:
    """Return (session_id, source) for this turn's conversation.

    Prefers AgentCore Runtime's own runtimeSessionId, which bedrock_agentcore's
    BedrockCallContextBuilder reads off the
    X-Amzn-Bedrock-AgentCore-Runtime-Session-Id header and puts on the A2A
    ServerCallContext state (and in a contextvar). That id is what the caller
    holds stable across turns, and it's the id AgentCore itself uses to route
    every turn of a conversation to the same isolated session.

    A2A's context_id is deliberately NOT the first choice: a2a-sdk generates
    a fresh UUID4 context_id in RequestContext.__init__ for any request whose
    caller didn't supply one, which is every plain `message/send`. Keying the
    conversation on it made each turn a brand-new agent with a brand-new
    Strands session — the "every turn is a new conversation" symptom.

    Falls back to context_id only when there is no runtime session id at all
    (running this A2A server directly, outside AgentCore Runtime, e.g. local
    dev), and to a freshly generated id if even that is missing.
    """
    state = getattr(context.call_context, "state", None) or {}
    native = state.get("session_id") or BedrockAgentCoreContext.get_session_id()
    if native:
        return _pad_session_id(native), "runtimeSessionId"

    context_id = context.context_id
    if context_id:
        return _pad_session_id(context_id), "a2a-context_id (no runtimeSessionId on request)"

    return session_store.new_session_id(prefix="adcp-buyer-agent-a2a"), "generated (no session id on request)"


def _align_a2a_context_id(context: RequestContext, session_id: str) -> None:
    """Point A2A's conversation id at the AgentCore runtime session id.

    StrandsA2AExecutor isolates and caches one Agent per A2A context_id, so
    leaving a per-request context_id in place would keep rebuilding the agent
    even once the session key itself is correct. Repointing it makes one
    AgentCore session == one A2A context == one Agent == one Strands/S3
    session == one reasoning session, and echoes that id back to the caller as
    the Task's contextId, so a spec-compliant A2A client naturally continues
    the same context on its next turn.

    a2a-sdk exposes no setter for context_id — it's generated in
    RequestContext.__init__, which has already run before any executor is
    called — so the private attribute is assigned alongside the public
    message field. Both writes are guarded: if a future SDK version drops
    these attributes, the session key resolved above is still correct and only
    the A2A-level id goes unaligned, rather than the request failing.
    """
    message = context.message
    if message is not None and hasattr(message, "context_id"):
        message.context_id = session_id
    if hasattr(context, "_context_id"):
        context._context_id = session_id


class BuyerAgentA2AExecutor(AgentExecutor):
    """Wraps StrandsA2AExecutor to bind each turn to AgentCore's own session
    and to record session start/incoming_request before delegating, since the
    runtime session id, invoker name and prompt text are only available on the
    incoming A2A request, not inside agent_factory (which strands calls with
    just a context_id).
    """

    def __init__(self) -> None:
        self._delegate = StrandsA2AExecutor(
            agent_factory=self._build_agent_for_context,
            enable_a2a_compliant_streaming=True,
        )

    def _build_agent_for_context(self, context_id: str):
        turn = _current_turn.get()
        if turn is None:
            # The factory is only reachable from inside execute(), which always
            # sets this. Fail loudly rather than building a session-less agent
            # that would silently start yet another detached conversation.
            raise RuntimeError(
                f"agent_factory called for context_id={context_id!r} outside an active A2A turn, "
                "so this request's AgentCore runtime session id is unknown."
            )
        return build_agent(
            session_id=turn.session_id,
            seller_agent_id=turn.seller_agent_id,
            invoker=turn.invoker,
        )

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        session_id, session_source = _resolve_session_id(context)
        metadata = context.metadata or {}
        invoker = metadata.get("invokerAgent") or "External Agent"
        seller_agent_id = metadata.get("sellerAgentId")
        prompt = context.get_user_input()

        logger.info(
            "A2A turn: session_id=%s (source=%s, a2a_context_id=%s) invoker=%s",
            session_id,
            session_source,
            context.context_id,
            invoker,
        )
        _align_a2a_context_id(context, session_id)

        turn_token = _current_turn.set(
            _Turn(session_id=session_id, invoker=invoker, seller_agent_id=seller_agent_id)
        )
        try:
            try:
                session_store.start_turn(
                    session_id,
                    invoker=invoker,
                    mode="agent",
                    seller_agent_id=seller_agent_id or get_default_seller_agent_id(),
                    request_preview=prompt,
                    # Which agent RAN this turn, as opposed to who asked
                    # (invoker). Lets the UI label and filter sessions by
                    # agent, and distinguishes this runtime's sessions from
                    # the HTTP runtime's even though both are the same agent.
                    agent_id=BUYER_A2A_AGENT_ID,
                    agent_name=BUYER_A2A_AGENT_NAME,
                )
                session_store.record_step(session_id, "incoming_request", {"from": invoker, "text": prompt})
            except (SessionStoreError, SellerAgentError):
                pass  # best-effort; see reasoning_hooks.py's _safe()

            try:
                await self._delegate.execute(context, event_queue)
            except Exception as exc:  # noqa: BLE001 - record, then let StrandsA2AExecutor's own handling proceed
                try:
                    session_store.error_turn(session_id, f"{type(exc).__name__}: {exc}")
                except SessionStoreError:
                    pass
                raise
        finally:
            _current_turn.reset(turn_token)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await self._delegate.cancel(context, event_queue)


def _build_agent_card() -> AgentCard:
    runtime_url = os.environ.get("AGENTCORE_RUNTIME_URL", "http://localhost:9000/")
    return AgentCard(
        name="AdCP Buyer Agent",
        description=(
            "An AdCP buyer agent that discovers and evaluates advertising inventory "
            "from a connected AdCP seller agent (get_products, get_adcp_capabilities, "
            "list_creative_formats). "
            "Read-only: it does not create media buys or spend money."
        ),
        url=runtime_url,
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=True),
        skills=[
            AgentSkill(
                id="get_products",
                name="Discover inventory",
                description="Find advertising inventory matching a natural-language brief.",
                tags=["adcp", "inventory", "discovery"],
            ),
            AgentSkill(
                id="get_adcp_capabilities",
                name="Check capabilities",
                description="Report the connected seller agent's AdCP capabilities.",
                tags=["adcp", "capabilities"],
            ),
            AgentSkill(
                id="list_creative_formats",
                name="List creative formats",
                description="List creative formats the connected seller supports.",
                tags=["adcp", "creative-formats"],
            ),
        ],
        default_input_modes=["text"],
        default_output_modes=["text"],
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    serve_a2a(BuyerAgentA2AExecutor(), _build_agent_card())
