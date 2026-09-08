# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unified client providing a common interface for OpenDirect 2.1 and A2A protocols.

Supports OpenDirect 2.1 (agentic-direct) and A2A for natural language interactions
through a common interface with automatic protocol detection.
"""

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel

from ..config import get_settings


class Protocol(str, Enum):
    """Supported communication protocols."""

    OPENDIRECT_21 = "opendirect21"
    A2A = "a2a"


class UnifiedResult(BaseModel):
    """Unified result from any protocol."""

    success: bool
    data: Any = None
    error: Optional[str] = None
    protocol: Protocol
    raw: Any = None


class UnifiedClient:
    """Unified client for seller operations across protocols.

    Provides a common interface for:
    - OpenDirect 2.1 via agentic-direct MCP server
    - A2A for natural language interactions

    Usage:
        async with UnifiedClient() as client:
            # Use default protocol
            products = await client.list_products()

            # Use specific protocol
            result = await client.send_natural_language(
                "What CTV inventory is available?",
                protocol=Protocol.A2A
            )
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        protocol: Optional[Protocol] = None,
    ):
        """Initialize the unified client.

        Args:
            base_url: Base URL for the OpenDirect server
            protocol: Default protocol to use (auto-detected if not specified)
        """
        settings = get_settings()
        self.base_url = base_url or settings.opendirect_base_url

        # Determine default protocol
        if protocol:
            self.default_protocol = protocol
        elif settings.default_protocol == "a2a":
            self.default_protocol = Protocol.A2A
        else:
            self.default_protocol = Protocol.OPENDIRECT_21

        # Protocol clients (initialized lazily)
        self._od21_client: Optional[Any] = None
        self._a2a_client: Optional[Any] = None

    async def __aenter__(self) -> "UnifiedClient":
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.disconnect()

    async def connect(self, protocol: Optional[Protocol] = None) -> None:
        """Connect to the specified protocol(s).

        Args:
            protocol: Specific protocol to connect, or None for default
        """
        target = protocol or self.default_protocol

        if target == Protocol.OPENDIRECT_21:
            from .opendirect21_client import OpenDirect21Client

            self._od21_client = OpenDirect21Client(self.base_url)
            await self._od21_client.connect()
        elif target == Protocol.A2A:
            from .a2a_client import A2AClient

            self._a2a_client = A2AClient(self.base_url, agent_type="seller")
            await self._a2a_client.connect()

    async def connect_all(self) -> None:
        """Connect to all available protocols."""
        await self.connect(Protocol.OPENDIRECT_21)
        await self.connect(Protocol.A2A)

    async def disconnect(self) -> None:
        """Disconnect from all protocols."""
        if self._od21_client:
            await self._od21_client.disconnect()
            self._od21_client = None
        if self._a2a_client:
            await self._a2a_client.disconnect()
            self._a2a_client = None

    def _get_client(self, protocol: Optional[Protocol] = None) -> Any:
        """Get the appropriate client for the protocol."""
        target = protocol or self.default_protocol

        if target == Protocol.OPENDIRECT_21:
            if not self._od21_client:
                raise RuntimeError("OpenDirect 2.1 client not connected")
            return self._od21_client
        elif target == Protocol.A2A:
            if not self._a2a_client:
                raise RuntimeError("A2A client not connected")
            return self._a2a_client
        else:
            raise ValueError(f"Unknown protocol: {target}")

    # =========================================================================
    # Organization Operations
    # =========================================================================

    async def list_organizations(
        self,
        role: Optional[str] = None,
        status: Optional[str] = None,
        protocol: Optional[Protocol] = None,
    ) -> UnifiedResult:
        """List organizations."""
        client = self._get_client(protocol)
        try:
            result = await client.list_organizations(role=role, status=status)
            return UnifiedResult(
                success=True,
                data=result,
                protocol=protocol or self.default_protocol,
                raw=result,
            )
        except Exception as e:
            return UnifiedResult(
                success=False,
                error=str(e),
                protocol=protocol or self.default_protocol,
            )

    async def create_organization(
        self,
        name: str,
        role: str,
        organization_id: Optional[str] = None,
        status: str = "active",
        metadata: Optional[dict[str, Any]] = None,
        protocol: Optional[Protocol] = None,
    ) -> UnifiedResult:
        """Create an organization."""
        client = self._get_client(protocol)
        try:
            result = await client.create_organization(
                name=name,
                role=role,
                organization_id=organization_id,
                status=status,
                metadata=metadata,
            )
            return UnifiedResult(
                success=True,
                data=result,
                protocol=protocol or self.default_protocol,
                raw=result,
            )
        except Exception as e:
            return UnifiedResult(
                success=False,
                error=str(e),
                protocol=protocol or self.default_protocol,
            )

    # =========================================================================
    # Product Operations
    # =========================================================================

    async def list_products(
        self,
        seller_organization_id: Optional[str] = None,
        protocol: Optional[Protocol] = None,
    ) -> UnifiedResult:
        """List products."""
        client = self._get_client(protocol)
        try:
            result = await client.list_products(seller_organization_id=seller_organization_id)
            return UnifiedResult(
                success=True,
                data=result,
                protocol=protocol or self.default_protocol,
                raw=result,
            )
        except Exception as e:
            return UnifiedResult(
                success=False,
                error=str(e),
                protocol=protocol or self.default_protocol,
            )

    async def create_product(
        self,
        name: str,
        seller_organization_id: str,
        inventory_segments: list[str],
        product_id: Optional[str] = None,
        description: Optional[str] = None,
        audience_targeting: Optional[dict[str, Any]] = None,
        ad_product_targeting: Optional[dict[str, Any]] = None,
        content_targeting: Optional[dict[str, Any]] = None,
        commercial_terms: Optional[dict[str, Any]] = None,
        protocol: Optional[Protocol] = None,
    ) -> UnifiedResult:
        """Create a product."""
        client = self._get_client(protocol)
        try:
            result = await client.create_product(
                name=name,
                seller_organization_id=seller_organization_id,
                inventory_segments=inventory_segments,
                product_id=product_id,
                description=description,
                audience_targeting=audience_targeting,
                ad_product_targeting=ad_product_targeting,
                content_targeting=content_targeting,
                commercial_terms=commercial_terms,
            )
            return UnifiedResult(
                success=True,
                data=result,
                protocol=protocol or self.default_protocol,
                raw=result,
            )
        except Exception as e:
            return UnifiedResult(
                success=False,
                error=str(e),
                protocol=protocol or self.default_protocol,
            )

    # =========================================================================
    # Proposal Operations
    # =========================================================================

    async def list_proposals(
        self,
        account_id: Optional[str] = None,
        status: Optional[str] = None,
        protocol: Optional[Protocol] = None,
    ) -> UnifiedResult:
        """List proposals."""
        client = self._get_client(protocol)
        try:
            result = await client.list_proposals(account_id=account_id, status=status)
            return UnifiedResult(
                success=True,
                data=result,
                protocol=protocol or self.default_protocol,
                raw=result,
            )
        except Exception as e:
            return UnifiedResult(
                success=False,
                error=str(e),
                protocol=protocol or self.default_protocol,
            )

    async def update_proposal(
        self,
        proposal_id: str,
        status: Optional[str] = None,
        revision_type: Optional[str] = None,
        reason: Optional[str] = None,
        protocol: Optional[Protocol] = None,
        **kwargs: Any,
    ) -> UnifiedResult:
        """Update a proposal (accept, reject, counter)."""
        client = self._get_client(protocol)
        try:
            result = await client.update_proposal(
                proposal_id=proposal_id,
                status=status,
                revision_type=revision_type,
                reason=reason,
                **kwargs,
            )
            return UnifiedResult(
                success=True,
                data=result,
                protocol=protocol or self.default_protocol,
                raw=result,
            )
        except Exception as e:
            return UnifiedResult(
                success=False,
                error=str(e),
                protocol=protocol or self.default_protocol,
            )

    # =========================================================================
    # Execution Operations
    # =========================================================================

    async def create_execution_order(
        self,
        proposal_id: str,
        execution_order_id: Optional[str] = None,
        status: str = "draft",
        external_ids: Optional[dict[str, Any]] = None,
        protocol: Optional[Protocol] = None,
    ) -> UnifiedResult:
        """Create an execution order from an accepted proposal."""
        client = self._get_client(protocol)
        try:
            result = await client.create_execution_order(
                proposal_id=proposal_id,
                execution_order_id=execution_order_id,
                status=status,
                external_ids=external_ids or {},
            )
            return UnifiedResult(
                success=True,
                data=result,
                protocol=protocol or self.default_protocol,
                raw=result,
            )
        except Exception as e:
            return UnifiedResult(
                success=False,
                error=str(e),
                protocol=protocol or self.default_protocol,
            )

    # =========================================================================
    # Ad Server Operations
    # =========================================================================

    async def sync_inventory_from_ad_server(
        self,
        config_id: str,
        organization_id: str,
        update_existing: bool = True,
        protocol: Optional[Protocol] = None,
    ) -> UnifiedResult:
        """Sync inventory from ad server."""
        client = self._get_client(protocol)
        try:
            result = await client.sync_inventory_from_ad_server(
                config_id=config_id,
                organization_id=organization_id,
                update_existing=update_existing,
            )
            return UnifiedResult(
                success=True,
                data=result,
                protocol=protocol or self.default_protocol,
                raw=result,
            )
        except Exception as e:
            return UnifiedResult(
                success=False,
                error=str(e),
                protocol=protocol or self.default_protocol,
            )

    async def sync_proposal_to_ad_server(
        self,
        config_id: str,
        proposal_id: str,
        protocol: Optional[Protocol] = None,
    ) -> UnifiedResult:
        """Sync accepted proposal to ad server."""
        client = self._get_client(protocol)
        try:
            result = await client.sync_proposal_to_ad_server(
                config_id=config_id,
                proposal_id=proposal_id,
            )
            return UnifiedResult(
                success=True,
                data=result,
                protocol=protocol or self.default_protocol,
                raw=result,
            )
        except Exception as e:
            return UnifiedResult(
                success=False,
                error=str(e),
                protocol=protocol or self.default_protocol,
            )

    # =========================================================================
    # Natural Language (A2A)
    # =========================================================================

    async def send_natural_language(
        self,
        request: str,
        context: Optional[dict[str, Any]] = None,
    ) -> UnifiedResult:
        """Send a natural language request via A2A protocol."""
        if not self._a2a_client:
            # Auto-connect to A2A if not connected
            from .a2a_client import A2AClient

            self._a2a_client = A2AClient(self.base_url, agent_type="seller")
            await self._a2a_client.connect()

        try:
            result = await self._a2a_client.send_request(request, context=context)
            return UnifiedResult(
                success=True,
                data=result.data,
                protocol=Protocol.A2A,
                raw=result,
            )
        except Exception as e:
            return UnifiedResult(
                success=False,
                error=str(e),
                protocol=Protocol.A2A,
            )
