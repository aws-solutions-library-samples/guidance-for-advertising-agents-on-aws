# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Curator registry — manages registered curators and their access.

Publishers configure which curators can create deals against their
inventory. Each curator gets their own pricing tier and schain node.

Agent Range is pre-registered as the day-one curator.
"""

import logging
from typing import Any, Optional

from ..models.curator import (
    AGENT_RANGE_CURATOR,
    CuratedDeal,
    Curator,
)
from ..models.supply_chain import SchainNode

logger = logging.getLogger(__name__)


class CuratorRegistry:
    """Registry for managing curators and their inventory access.

    Usage:
        registry = CuratorRegistry()
        registry.register(AGENT_RANGE_CURATOR)  # Auto-registered

        curator = registry.get("agent-range")
        curated_deal = registry.create_curated_deal(
            curator_id="agent-range",
            deal_id="IAB-ABC123",
            base_cpm=25.0,
        )
    """

    def __init__(self, auto_register_defaults: bool = True) -> None:
        self._curators: dict[str, Curator] = {}
        if auto_register_defaults:
            self.register(AGENT_RANGE_CURATOR)

    def register(self, curator: Curator) -> None:
        """Register a curator."""
        self._curators[curator.curator_id] = curator
        logger.info("Registered curator: %s (%s)", curator.name, curator.curator_id)

    def unregister(self, curator_id: str) -> None:
        """Remove a curator."""
        self._curators.pop(curator_id, None)

    def get(self, curator_id: str) -> Curator:
        """Get a curator by ID."""
        if curator_id not in self._curators:
            raise KeyError(f"Curator '{curator_id}' not registered. Available: {self.list_ids()}")
        return self._curators[curator_id]

    def list_all(self) -> list[Curator]:
        """List all registered curators."""
        return list(self._curators.values())

    def list_active(self) -> list[Curator]:
        """List active curators only."""
        return [c for c in self._curators.values() if c.is_active]

    def list_ids(self) -> list[str]:
        """List registered curator IDs."""
        return list(self._curators.keys())

    def authenticate(self, api_key: str) -> Optional[Curator]:
        """Find a curator by API key. Returns None if not found."""
        for curator in self._curators.values():
            if curator.api_key and curator.api_key == api_key:
                return curator
        return None

    def build_schain_node(self, curator_id: str) -> SchainNode:
        """Build an OpenRTB schain node for a curator."""
        curator = self.get(curator_id)
        return SchainNode(
            asi=curator.domain,
            sid=curator.curator_id,
            hp=1,  # Payment flows through curator
            name=curator.name,
            domain=curator.domain,
        )

    def create_curated_deal(
        self,
        curator_id: str,
        deal_id: str,
        base_cpm: float,
        *,
        audience_segments: Optional[list[str]] = None,
        content_categories: Optional[list[str]] = None,
        impressions: int = 0,
    ) -> CuratedDeal:
        """Create a curated deal with curator fee and schain node.

        Args:
            curator_id: Registered curator ID
            deal_id: The deal to curate
            base_cpm: Publisher's base CPM
            audience_segments: Curator's audience segments to apply
            content_categories: Curator's content categories to apply
            impressions: Expected impressions (for fee calculation)

        Returns:
            CuratedDeal with pricing breakdown and schain node.
        """
        curator = self.get(curator_id)

        # Calculate curator fee
        curator_fee_cpm = curator.fee.calculate_fee(base_cpm)
        total_cpm = curator.fee.calculate_curated_cpm(base_cpm)

        # Build schain node
        schain_node = self.build_schain_node(curator_id)

        return CuratedDeal(
            deal_id=deal_id,
            curator_id=curator.curator_id,
            curator_name=curator.name,
            curator_domain=curator.domain,
            base_cpm=base_cpm,
            curator_fee_cpm=round(curator_fee_cpm, 4),
            total_cpm=round(total_cpm, 4),
            curator_audience_segments=audience_segments or curator.audience_segments,
            curator_content_categories=content_categories or curator.content_categories,
            curator_schain_node=schain_node.model_dump(),
        )


def build_curator_registry(settings: Any = None) -> CuratorRegistry:
    """Build a CuratorRegistry from application settings.

    Agent Range is auto-registered. Additional curators can be
    configured via environment/settings in the future.
    """
    registry = CuratorRegistry(auto_register_defaults=True)

    # Future: load additional curators from config/database
    # if settings and settings.curator_config_path:
    #     ...

    return registry
