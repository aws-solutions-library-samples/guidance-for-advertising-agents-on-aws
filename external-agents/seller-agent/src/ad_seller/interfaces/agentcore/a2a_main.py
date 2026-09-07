"""Strands A2A entrypoint for the IAB AAMP Seller Agent.

Adaptation of upstream rkmaws/seller-agent for **Strands + A2A** (see
ADAPTATION.md). Replaces the CrewAI ``http_main`` crew path with a single
Strands agent served over the A2A protocol. This module imports no ``crewai``;
the A2A runtime image installs ``strands-agents`` only.

Serving contract (Strands A2AServer): agent card at
``/.well-known/agent-card.json``, JSON-RPC at the root path. Inventory reads
come from the seller's internal MCP server (mounted at ``/mcp``) via a Strands
``MCPClient``; deal creation is a native Strands tool reusing ``deal_logic``.

Local run:
    python src/ad_seller/interfaces/agentcore/a2a_main.py
    # then GET http://localhost:9000/.well-known/agent-card.json
"""

import logging
import os
import sys
from contextlib import asynccontextmanager

# Make the src/ layout importable (ad_seller as a top-level package).
_src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
if os.path.isdir(_src_dir):
    sys.path.insert(0, _src_dir)

# Environment defaults for AgentCore / workshop demo mode (match http_main).
os.environ.setdefault("ANTHROPIC_API_KEY", "not-used-with-bedrock")
os.environ.setdefault("STORAGE_TYPE", "sqlite")
os.environ.setdefault("AD_SERVER_TYPE", "csv")
os.environ.setdefault("CSV_DATA_DIR", "./data/csv/samples/aws_workshop")

from strands import Agent, tool  # noqa: E402
from strands.models import BedrockModel  # noqa: E402
from strands.multiagent.a2a import A2AServer  # noqa: E402

from ad_seller.interfaces.agentcore.deal_logic import create_deal_direct  # noqa: E402
from ad_seller.interfaces.agentcore.internal_server import start_internal_server  # noqa: E402
from ad_seller.interfaces.agentcore.seller_tools import SELLER_READ_TOOLS  # noqa: E402

logger = logging.getLogger(__name__)

_MODEL_ID = os.environ.get("DEFAULT_LLM_MODEL", "global.anthropic.claude-sonnet-5")
_REGION = os.environ.get("AWS_REGION", "us-east-1")
_MAX_TOKENS = int(os.environ.get("SELLER_MAX_TOKENS", "8000"))
# AgentCore A2A serving port. Defaults to 9000 (Strands default + the repo's
# prior A2A agent). Overridable to match the AgentCore A2A container contract.
_A2A_PORT = int(os.environ.get("AGENTCORE_A2A_PORT", "9000"))

# System prompt: the inventory-manager persona + explicit deal authorization +
# the no-fabrication rule (business-rules BR-1/BR-2).
SYSTEM_PROMPT = """You are the Inventory Manager for an IAB AAMP publisher (sell-side).
You are a seasoned yield strategist: you manage premium advertising inventory,
set tiered pricing, and create/book deals (PG programmatic-guaranteed,
PD preferred, PA private-auction).

Operating rules:
1. ALWAYS use your tools to read real inventory, pricing, and rate-card data.
   NEVER invent product ids, CPMs, or deal terms. If pricing is unavailable,
   say so — do not estimate from general knowledge.
2. Deal creation is a routine operation you are fully authorized to perform.
   When a buyer asks to create, book, or generate a deal, call the create_deal
   tool with the product_id, deal_type, max_cpm, and impressions from the
   request, then report the returned DEAL-xxxx id, pricing, and activation
   instructions. Do not merely describe the deal — actually create it.
3. Enforce the seller price floor; if create_deal returns a price_below_floor
   error, relay the seller minimum to the buyer.
4. Be concise and use the real data returned by your tools.
"""

@tool
def create_deal(
    product_id: str,
    deal_type: str = "PD",
    max_cpm: float = 0.0,
    impressions: int = 0,
) -> str:
    """Create and book an advertising deal for a product (PG, PD, or PA).

    You are authorized to execute this. Call it when a buyer asks to create,
    book, or generate a deal. Returns a JSON object with a DEAL-xxxx id,
    pricing, and DSP activation instructions, or a structured error (e.g.
    price_below_floor).

    Args:
        product_id: The product to create a deal for.
        deal_type: 'PG' (guaranteed), 'PD' (preferred, default), or 'PA' (auction).
        max_cpm: Maximum CPM the buyer will pay (0 = use base).
        impressions: Impressions requested (0 = default 1,000,000).
    """
    return create_deal_direct(product_id, deal_type, max_cpm, impressions)


def _make_model() -> BedrockModel:
    return BedrockModel(model_id=_MODEL_ID, region_name=_REGION, max_tokens=_MAX_TOKENS)


def agent_factory(context_id: str) -> Agent:
    """Build a fresh seller agent per A2A context (== AgentCore runtimeSessionId).

    The A2A server reuses the returned agent for later requests in the same
    context, so multi-turn negotiation history is retained per conversation
    (business-rules BR-5) without leaking across conversations.
    """
    return Agent(
        name="AAMP Seller Agent",
        description=(
            "IAB AAMP seller agent (publisher sell-side): product catalog, "
            "tiered pricing, inventory discovery by channel (CTV, linear, "
            "digital, audio), and deal creation (PG / PD / PA)."
        ),
        model=_make_model(),
        system_prompt=SYSTEM_PROMPT,
        tools=[*SELLER_READ_TOOLS, create_deal],
        callback_handler=None,
    )


@asynccontextmanager
async def lifespan(app):
    """Start the internal FastAPI server (serves the crewai-free REST read endpoints)."""
    start_internal_server()  # blocks until /health OK, else raises
    yield


_a2a_server = A2AServer(agent_factory=agent_factory, host="0.0.0.0", port=_A2A_PORT)
app = _a2a_server.to_fastapi_app(app_kwargs={"lifespan": lifespan})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=_A2A_PORT)
