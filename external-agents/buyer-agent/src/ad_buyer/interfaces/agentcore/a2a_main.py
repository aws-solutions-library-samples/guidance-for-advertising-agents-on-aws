"""Strands A2A entrypoint for the IAB AAMP Buyer Agent.

Adaptation of upstream rkmaws/buyer-agent for **Strands + A2A** (see ADAPTATION.md).
Replaces the CrewAI ``crew_tools.run_campaign_plan`` + ``DealBookingFlow`` path with a single
coordinator agent (Haiku 4.5) that: (1) calls ``allocate_budget`` — which runs the reused
pure-Python brief/audience steps plus one Sonnet-5 ``structured_output`` budget call — then
(2) calls ``search_inventory`` once per funded channel against the seller's A2A runtime, and
(3) emits a media-plan JSON. No CrewAI Flow, no hierarchical delegation. Crewai-free.

Local run:
    python src/ad_buyer/interfaces/agentcore/a2a_main.py
"""

import json
import logging
import os
import sys
from typing import Any

_src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
if os.path.isdir(_src_dir):
    sys.path.insert(0, _src_dir)

os.environ.setdefault("ANTHROPIC_API_KEY", "not-used-with-bedrock")
os.environ.setdefault("STORAGE_TYPE", "sqlite")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from strands import Agent, tool  # noqa: E402
from strands.models import BedrockModel  # noqa: E402
from strands.multiagent.a2a import A2AServer  # noqa: E402

from ad_buyer.interfaces.agentcore.budget_models import BudgetAllocationOutput  # noqa: E402
from ad_buyer.interfaces.agentcore import planning_helpers as ph  # noqa: E402
from ad_buyer.interfaces.agentcore.seller_client import search_seller_inventory  # noqa: E402

logger = logging.getLogger(__name__)

_REGION = os.environ.get("AWS_REGION", "us-east-1")
_BUDGET_MODEL = os.environ.get(
    "BUYER_BUDGET_MODEL", os.environ.get("DEFAULT_LLM_MODEL", "global.anthropic.claude-sonnet-5")
)
_COORDINATOR_MODEL = os.environ.get(
    "BUYER_COORDINATOR_MODEL", "global.anthropic.claude-haiku-4-5-20251001-v1:0"
)
_A2A_PORT = int(os.environ.get("AGENTCORE_A2A_PORT", "9000"))

_CHANNELS = ["branding", "mobile_app", "ctv", "performance"]

COORDINATOR_PROMPT = """You are a media-buying campaign coordinator for an IAB AAMP buyer.
Plan a campaign, do not book anything.

Workflow — follow in order:
1. Call `allocate_budget` exactly once with the user's request. It returns the campaign brief,
   the per-channel budget allocation, and audience coverage/gaps.
2. For EACH channel whose allocated budget > 0, call `search_inventory` once with that channel and
   its budget to get real inventory from the seller. Do not call it for $0 channels.
3. Produce a final media plan as a JSON object with these keys:
   {"campaign_name","total_budget","flight","status":"planned","approval_required":true,
    "budget_allocations":{channel:{"budget","percentage","rationale"}},
    "recommendations":[{"product_id","product_name","channel","publisher","impressions","cpm","cost"}],
    "audience_coverage":{...},"audience_gaps":[...]}

Rules:
- NEVER invent CPMs, product ids, or publishers. Use only inventory returned by search_inventory.
  If the seller returns no pricing for a channel, leave that recommendation's cpm null and note it.
- Keep recommendations grounded in the search_inventory results.
- Output ONLY the final JSON object as your final message.
"""


def _budget_task(brief: dict[str, Any]) -> str:
    return f"""Analyze the campaign brief and allocate budget across channels.

Campaign Name: {brief.get("name", "Unnamed Campaign")}
Objectives: {brief.get("objectives", [])}
Total Budget: ${brief.get("budget", 0):,.2f}
Flight: {brief.get("start_date")} to {brief.get("end_date")}
Target Audience: {brief.get("target_audience", {})}

Split the total budget across: branding (display/video, awareness), mobile_app (app installs),
ctv (premium video reach), performance (remarketing/conversion). Allocate $0 to channels that do
not fit the objectives. Budgets must be non-negative and sum to at most the total budget; provide
a percentage and a short rationale per channel."""


@tool
def allocate_budget(prompt: str) -> str:
    """Parse the campaign brief, plan audience coverage, and allocate budget across channels.

    Call this first. Returns a JSON object with the brief, per-channel budget allocations
    (branding/mobile_app/ctv/performance), audience coverage, and gaps.

    Args:
        prompt: The user's natural-language campaign request.
    """
    brief = ph.parse_brief_from_prompt(prompt)
    errors = ph.validate_brief(brief)
    if errors:
        return json.dumps({"error": "invalid_brief", "details": errors})

    target_audience = brief.get("target_audience", {})
    if isinstance(target_audience, str):
        target_audience = {"description": target_audience}

    coverage = ph.estimate_channel_coverage(target_audience)
    gaps = ph.identify_audience_gaps(target_audience, coverage)

    budget_agent = Agent(
        model=BedrockModel(model_id=_BUDGET_MODEL, region_name=_REGION, max_tokens=2000),
        system_prompt=(
            "You are a senior media buyer allocating a campaign budget across channels. "
            "Be disciplined: allocate only to channels that fit the objectives."
        ),
        callback_handler=None,
    )
    try:
        allocation: BudgetAllocationOutput = budget_agent.structured_output(
            BudgetAllocationOutput, _budget_task(brief)
        )
    except Exception as e:  # noqa: BLE001
        logger.error("budget allocation failed: %s", e)
        return json.dumps({"error": "budget_allocation_failed", "detail": str(e)})

    total = float(brief.get("budget", 0) or 0)
    # BR-2 clamp is a pure, property-tested helper in planning_helpers.
    raw = {
        ch: {"budget": getattr(allocation, ch).budget, "rationale": getattr(allocation, ch).rationale}
        for ch in _CHANNELS
    }
    allocations = ph.clamp_allocations(raw, total)

    return json.dumps(
        {
            "campaign_name": brief["name"],
            "total_budget": total,
            "flight": f"{brief['start_date']} to {brief['end_date']}",
            "budget_allocations": allocations,
            "audience_coverage": coverage,
            "audience_gaps": gaps,
        },
        default=str,
    )


def _build_search_inventory(context_id: str):
    """Build a search_inventory tool bound to this A2A context (for the seller session)."""

    @tool
    def search_inventory(
        channel: str,
        budget: float,
        format: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        targeting: str | None = None,
    ) -> str:
        """Search the seller's inventory for one funded channel (call once per funded channel).

        Returns real inventory (products, pricing, availability) from the seller's A2A runtime.
        Do not call for channels with $0 budget.

        Args:
            channel: branding | mobile_app | ctv | performance
            budget: allocated budget for this channel (USD)
            format: optional ad format (banner, video, interstitial, rewarded)
            min_price: optional minimum CPM
            max_price: optional maximum CPM
            targeting: optional targeting description
        """
        parts = [
            f"Find available advertising inventory for the {channel} channel",
            f"with a budget of ${budget:,.0f}.",
        ]
        if format:
            parts.append(f"Ad format: {format}.")
        if min_price is not None or max_price is not None:
            parts.append(f"CPM range: {min_price or 0}-{max_price or 'any'}.")
        if targeting:
            parts.append(f"Targeting: {targeting}.")
        parts.append(
            "Return matching products with product_id, publisher, CPM pricing, and available "
            "impressions. Do not invent pricing."
        )
        return search_seller_inventory(" ".join(parts), context_id=context_id)

    return search_inventory


def agent_factory(context_id: str) -> Agent:
    """Build the coordinator agent per A2A context (== AgentCore runtimeSessionId, BR-5)."""
    return Agent(
        name="AAMP Buyer Agent",
        description=(
            "IAB AAMP buyer agent: takes a campaign brief (budget, dates, objectives, audience), "
            "allocates budget across channels, and returns a media plan for approval "
            "(no deals booked without approval)."
        ),
        model=BedrockModel(model_id=_COORDINATOR_MODEL, region_name=_REGION, max_tokens=8000),
        system_prompt=COORDINATOR_PROMPT,
        tools=[allocate_budget, _build_search_inventory(context_id)],
        callback_handler=None,
    )


_a2a_server = A2AServer(agent_factory=agent_factory, host="0.0.0.0", port=_A2A_PORT)
app = _a2a_server.to_fastapi_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=_A2A_PORT)
