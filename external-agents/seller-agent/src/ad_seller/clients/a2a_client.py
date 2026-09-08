# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""A2A (Agent-to-Agent) client for natural language interactions.

Implements the A2A protocol for conversational interactions with
the seller agent via JSON-RPC 2.0.
"""

import uuid
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field

from ..config import get_settings


class A2APart(BaseModel):
    """A part of an A2A response."""

    type: str  # text, data
    content: Any


class A2AResponse(BaseModel):
    """Response from an A2A request."""

    task_id: str = Field(alias="taskId")
    context_id: Optional[str] = Field(default=None, alias="contextId")
    parts: list[A2APart] = Field(default_factory=list)
    status: str = "completed"

    @property
    def text(self) -> str:
        """Get concatenated text content from response."""
        return "\n".join(part.content for part in self.parts if part.type == "text")

    @property
    def data(self) -> list[Any]:
        """Get data parts from response."""
        return [part.content for part in self.parts if part.type == "data"]

    @classmethod
    def from_result(cls, result: dict[str, Any]) -> "A2AResponse":
        """Create response from JSON-RPC result."""
        parts = []
        for part in result.get("parts", []):
            parts.append(A2APart(type=part.get("type", "text"), content=part.get("content")))

        return cls(
            taskId=result.get("taskId", ""),
            contextId=result.get("contextId"),
            parts=parts,
            status=result.get("status", "completed"),
        )


class A2AClient:
    """Client for A2A (Agent-to-Agent) protocol.

    A2A enables natural language interactions with the seller agent.
    The agent interprets requests and selects appropriate tools.

    Usage:
        async with A2AClient() as client:
            response = await client.send_request(
                "What CTV inventory do you have available for Q1?"
            )
            print(response.text)
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        agent_type: str = "seller",
    ):
        """Initialize the A2A client.

        Args:
            base_url: Base URL for the A2A server
            agent_type: Type of agent to interact with (seller, buyer)
        """
        settings = get_settings()
        self.base_url = base_url or settings.opendirect_base_url
        self.agent_type = agent_type
        self.jsonrpc_url = f"{self.base_url}/a2a/{agent_type}/jsonrpc"
        self.agent_card_url = f"{self.base_url}/a2a/{agent_type}/.well-known/agent-card.json"

        self._http_client: Optional[httpx.AsyncClient] = None
        self._agent_card: Optional[dict[str, Any]] = None
        self._context_id: Optional[str] = None

    async def __aenter__(self) -> "A2AClient":
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.disconnect()

    async def connect(self) -> None:
        """Connect to the A2A server and fetch agent card."""
        self._http_client = httpx.AsyncClient(timeout=60.0)

        # Fetch agent card for capabilities discovery
        try:
            response = await self._http_client.get(self.agent_card_url)
            if response.status_code == 200:
                self._agent_card = response.json()
        except Exception:
            # Agent card is optional
            self._agent_card = None

    async def disconnect(self) -> None:
        """Disconnect from the server."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    @property
    def agent_card(self) -> Optional[dict[str, Any]]:
        """Get the agent card with capabilities."""
        return self._agent_card

    async def send_request(
        self,
        request: str,
        context: Optional[dict[str, Any]] = None,
        context_id: Optional[str] = None,
    ) -> A2AResponse:
        """Send a natural language request to the agent.

        Args:
            request: Natural language request
            context: Additional context for the request
            context_id: Optional context ID for conversation continuity

        Returns:
            A2AResponse with the agent's response
        """
        if not self._http_client:
            raise RuntimeError("Client not connected")

        # Use provided context_id or maintain session context
        effective_context_id = context_id or self._context_id

        # Build JSON-RPC 2.0 request
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "call",
            "params": {
                "request": request,
            },
        }

        if effective_context_id:
            payload["params"]["contextId"] = effective_context_id

        if context:
            payload["params"]["context"] = context

        # Send request
        response = await self._http_client.post(
            self.jsonrpc_url,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()

        result = response.json()

        # Handle JSON-RPC error
        if "error" in result:
            error = result["error"]
            raise RuntimeError(f"A2A error: {error.get('message', 'Unknown error')}")

        # Parse response
        a2a_response = A2AResponse.from_result(result.get("result", {}))

        # Update session context
        if a2a_response.context_id:
            self._context_id = a2a_response.context_id

        return a2a_response

    async def discovery_query(
        self,
        query: str,
        buyer_context: Optional[dict[str, Any]] = None,
    ) -> A2AResponse:
        """Send a discovery query about inventory.

        Buyers can use this to explore inventory without commitment.

        Args:
            query: Natural language query about inventory
            buyer_context: Optional buyer identity/context

        Returns:
            A2AResponse with inventory information
        """
        context = {"query_type": "discovery"}
        if buyer_context:
            context["buyer"] = buyer_context

        return await self.send_request(query, context=context)

    async def pricing_inquiry(
        self,
        product_ids: list[str],
        buyer_context: Optional[dict[str, Any]] = None,
        volume: Optional[int] = None,
    ) -> A2AResponse:
        """Request pricing for specific products.

        Args:
            product_ids: Products to get pricing for
            buyer_context: Buyer identity for tiered pricing
            volume: Optional impression volume for volume discounts

        Returns:
            A2AResponse with pricing information
        """
        request = f"What is the pricing for products: {', '.join(product_ids)}"
        if volume:
            request += f" for {volume:,} impressions"

        context = {
            "query_type": "pricing",
            "product_ids": product_ids,
        }
        if buyer_context:
            context["buyer"] = buyer_context
        if volume:
            context["volume"] = volume

        return await self.send_request(request, context=context)

    async def availability_check(
        self,
        product_id: str,
        start_date: str,
        end_date: str,
        impressions: int,
        buyer_context: Optional[dict[str, Any]] = None,
    ) -> A2AResponse:
        """Check availability for a product.

        Args:
            product_id: Product to check
            start_date: Flight start date (ISO 8601)
            end_date: Flight end date (ISO 8601)
            impressions: Requested impressions
            buyer_context: Optional buyer identity

        Returns:
            A2AResponse with availability information
        """
        request = (
            f"Is product {product_id} available for {impressions:,} impressions "
            f"from {start_date} to {end_date}?"
        )

        context = {
            "query_type": "availability",
            "product_id": product_id,
            "start_date": start_date,
            "end_date": end_date,
            "impressions": impressions,
        }
        if buyer_context:
            context["buyer"] = buyer_context

        return await self.send_request(request, context=context)

    async def submit_proposal(
        self,
        proposal_details: dict[str, Any],
        buyer_context: dict[str, Any],
    ) -> A2AResponse:
        """Submit a proposal for seller review.

        Args:
            proposal_details: Proposal specification
            buyer_context: Buyer identity (required for proposals)

        Returns:
            A2AResponse with proposal status
        """
        request = "I would like to submit a proposal for the following inventory"

        context = {
            "query_type": "proposal",
            "proposal": proposal_details,
            "buyer": buyer_context,
        }

        return await self.send_request(request, context=context)

    async def negotiate(
        self,
        proposal_id: str,
        counter_terms: dict[str, Any],
        buyer_context: dict[str, Any],
    ) -> A2AResponse:
        """Continue negotiation on a proposal.

        Args:
            proposal_id: Proposal to negotiate
            counter_terms: Buyer's counter terms
            buyer_context: Buyer identity

        Returns:
            A2AResponse with negotiation result
        """
        request = f"I would like to counter proposal {proposal_id}"

        context = {
            "query_type": "negotiation",
            "proposal_id": proposal_id,
            "counter_terms": counter_terms,
            "buyer": buyer_context,
        }

        return await self.send_request(request, context=context)

    async def request_deal_id(
        self,
        proposal_id: str,
        buyer_context: dict[str, Any],
        dsp_platform: Optional[str] = None,
    ) -> A2AResponse:
        """Request a Deal ID for DSP activation.

        Used for non-agentic DSP workflows where buyer needs
        a Deal ID to activate in traditional DSPs.

        Args:
            proposal_id: Accepted proposal to generate deal for
            buyer_context: Buyer identity
            dsp_platform: Target DSP platform (ttd, amazon, dv360)

        Returns:
            A2AResponse with Deal ID and activation details
        """
        request = f"Please generate a Deal ID for accepted proposal {proposal_id}"
        if dsp_platform:
            request += f" for activation in {dsp_platform}"

        context = {
            "query_type": "deal_generation",
            "proposal_id": proposal_id,
            "buyer": buyer_context,
        }
        if dsp_platform:
            context["dsp_platform"] = dsp_platform

        return await self.send_request(request, context=context)
