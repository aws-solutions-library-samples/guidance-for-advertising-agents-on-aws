# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Agent Registry Client — query AAMP and other registries.

Provides a base interface for agent registry interactions and a concrete
IAB Tech Lab AAMP implementation. Additional registry providers can be
added by subclassing BaseRegistryClient.

fetch_agent_card() is functional now (fetches real .well-known/agent.json).
AAMP-specific lookup/verify/search methods are stubbed pending public API spec.
"""

import hashlib
import logging
from abc import ABC, abstractmethod
from typing import Optional

import httpx
from pydantic import ValidationError

from ..models.agent_registry import AgentCard

logger = logging.getLogger(__name__)


# =============================================================================
# Base Registry Client (extensible for future registries)
# =============================================================================


class BaseRegistryClient(ABC):
    """Abstract base for agent registry clients.

    Subclass this to integrate with vendor-specific registries,
    private enterprise registries, or future IAB standards.
    """

    def __init__(self, registry_id: str, registry_name: str, registry_url: str):
        self.registry_id = registry_id
        self.registry_name = registry_name
        self.registry_url = registry_url.rstrip("/")

    @abstractmethod
    async def verify_registration(self, agent_url: str) -> tuple[bool, Optional[str]]:
        """Check if an agent URL is registered in this registry.

        Returns:
            (is_registered, external_agent_id) — external_agent_id is the
            ID assigned by this registry, or None if not registered.
        """

    @abstractmethod
    async def lookup_agent(self, agent_id: str) -> Optional[dict]:
        """Look up an agent by its registry-assigned ID.

        Returns raw registry data or None if not found.
        """

    @abstractmethod
    async def search_agents(
        self,
        agent_type: Optional[str] = None,
        inventory_types: Optional[list[str]] = None,
    ) -> list[dict]:
        """Search registry for agents matching criteria."""


# =============================================================================
# Agent Card Fetcher (shared utility, works with any agent)
# =============================================================================


async def fetch_agent_card(agent_url: str, timeout: float = 15.0) -> Optional[AgentCard]:
    """Fetch an agent's card from its .well-known endpoint.

    This is registry-independent — any A2A-compliant agent can serve
    an agent card at {url}/.well-known/agent.json.

    Args:
        agent_url: Base URL of the agent (e.g. https://seller.example.com)
        timeout: HTTP timeout in seconds

    Returns:
        AgentCard if successfully fetched and parsed, None otherwise.
    """
    url = f"{agent_url.rstrip('/')}/.well-known/agent.json"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
            if response.status_code == 200:
                return AgentCard(**response.json())
    except (httpx.HTTPError, ValidationError, ValueError) as e:
        logger.debug("Failed to fetch agent card from %s: %s", url, e)
    return None


# =============================================================================
# IAB Tech Lab AAMP Registry Client
# =============================================================================

# Known AAMP test agent URLs for stub verification
_AAMP_TEST_AGENTS = {
    "https://agentic-direct-server-hwgrypmndq-uk.a.run.app",
}


class AAMPRegistryClient(BaseRegistryClient):
    """Client for the IAB Tech Lab AAMP Agent Registry.

    The AAMP registry is the primary trust layer for agentic advertising.
    Launched March 2026 as part of IAB Tech Lab's Tools Portal.

    verify_registration() and lookup_agent() are stubbed pending the
    public AAMP API specification. They return realistic mock data.
    fetch_agent_card() is functional (shared utility above).

    TODO: Replace stubs when AAMP publishes registry API spec
    TODO: Add webhook support for registry update notifications
    """

    def __init__(
        self,
        registry_url: str = "https://tools.iabtechlab.com/agent-registry",
    ):
        super().__init__(
            registry_id="iab_aamp",
            registry_name="IAB Tech Lab AAMP",
            registry_url=registry_url,
        )

    async def verify_registration(self, agent_url: str) -> tuple[bool, Optional[str]]:
        """Check if an agent URL is registered in AAMP.

        STUB: Returns True for known IAB Tech Lab test URLs.
        Real implementation will query the AAMP registry API.
        """
        normalized = agent_url.rstrip("/")

        # Stub: known test agents are "registered"
        if normalized in _AAMP_TEST_AGENTS:
            ext_id = f"aamp-{hashlib.sha256(normalized.encode()).hexdigest()[:12]}"
            return True, ext_id

        # Stub: unknown agents are not registered
        logger.debug("[STUB] AAMP verify_registration(%s) → not registered", agent_url)
        return False, None

    async def lookup_agent(self, agent_id: str) -> Optional[dict]:
        """Look up an agent by AAMP registry ID.

        STUB: Returns mock data for any ID starting with 'aamp-'.
        """
        if agent_id.startswith("aamp-"):
            return {
                "agent_id": agent_id,
                "registry": "iab_aamp",
                "status": "active",
                "verified": True,
                "note": "[STUB] Pending AAMP API integration",
            }
        return None

    async def search_agents(
        self,
        agent_type: Optional[str] = None,
        inventory_types: Optional[list[str]] = None,
    ) -> list[dict]:
        """Search AAMP registry for agents.

        STUB: Returns empty list. Real implementation will query
        the AAMP search API with filters.
        """
        logger.debug(
            "[STUB] AAMP search_agents(type=%s, inventory=%s) → []",
            agent_type,
            inventory_types,
        )
        return []
