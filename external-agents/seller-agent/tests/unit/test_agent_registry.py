# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for Agent Registry models, client, and service."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ad_seller.clients.agent_registry_client import (
    AAMPRegistryClient,
    fetch_agent_card,
)
from ad_seller.models.agent_registry import (
    TRUST_TO_TIER_MAP,
    AgentAuthentication,
    AgentCapabilities,
    AgentCard,
    AgentProvider,
    AgentSkill,
    AgentType,
    RegisteredAgent,
    RegistrySource,
    TrustStatus,
)
from ad_seller.models.buyer_identity import AccessTier, BuyerContext, BuyerIdentity
from ad_seller.registry.agent_registry import AgentRegistryService

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_provider():
    return AgentProvider(
        name="Test Agency DSP",
        url="https://dsp.example.com",
        description="A test DSP for buyer agents",
    )


@pytest.fixture
def sample_agent_card(sample_provider):
    return AgentCard(
        name="Test Buyer Agent",
        description="A buyer agent for programmatic advertising",
        url="https://buyer.example.com",
        version="1.0.0",
        provider=sample_provider,
        capabilities=AgentCapabilities(protocols=["a2a", "opendirect21"]),
        skills=[
            AgentSkill(
                id="media-buying",
                name="Media Buying",
                description="Automated media buying",
                tags=["display", "video"],
            )
        ],
        authentication=AgentAuthentication(schemes=["bearer", "api_key"]),
        inventory_types=["display", "video"],
        supported_deal_types=["preferred_deal", "private_auction"],
    )


@pytest.fixture
def sample_registered_agent(sample_agent_card):
    return RegisteredAgent(
        agent_id="agent-test1234",
        agent_card=sample_agent_card,
        agent_type=AgentType.BUYER,
        trust_status=TrustStatus.REGISTERED,
        registry_sources=[
            RegistrySource(
                registry_id="iab_aamp",
                registry_name="IAB Tech Lab AAMP",
                registry_url="https://tools.iabtechlab.com/agent-registry",
                external_agent_id="aamp-abc123",
                verified_at=datetime(2026, 3, 1),
            )
        ],
        registered_at=datetime(2026, 3, 1),
    )


@pytest.fixture
def mock_storage():
    """Create a mock storage backend."""
    storage = AsyncMock()
    storage.get = AsyncMock(return_value=None)
    storage.set = AsyncMock()
    storage.delete = AsyncMock(return_value=True)
    storage.keys = AsyncMock(return_value=[])
    storage.get_agent = AsyncMock(return_value=None)
    storage.set_agent = AsyncMock()
    storage.delete_agent = AsyncMock(return_value=True)
    storage.list_agents = AsyncMock(return_value=[])
    return storage


@pytest.fixture
def aamp_client():
    return AAMPRegistryClient()


@pytest.fixture
def registry_service(mock_storage, aamp_client):
    return AgentRegistryService(
        storage=mock_storage,
        registry_clients=[aamp_client],
    )


# =============================================================================
# Agent Card & Model Tests
# =============================================================================


class TestAgentCard:
    """Tests for AgentCard model."""

    def test_agent_card_creation(self, sample_agent_card):
        assert sample_agent_card.name == "Test Buyer Agent"
        assert sample_agent_card.url == "https://buyer.example.com"
        assert sample_agent_card.version == "1.0.0"
        assert len(sample_agent_card.skills) == 1
        assert sample_agent_card.skills[0].id == "media-buying"

    def test_agent_card_defaults(self):
        card = AgentCard(
            name="Minimal Agent",
            description="Bare minimum",
            url="https://agent.example.com",
            provider=AgentProvider(name="Test"),
        )
        assert card.version == "1.0.0"
        assert card.capabilities.protocols == ["a2a"]
        assert card.capabilities.streaming is False
        assert card.authentication.schemes == ["api_key"]
        assert card.skills == []
        assert card.inventory_types == []
        assert card.supported_deal_types == []

    def test_agent_card_inventory_types(self, sample_agent_card):
        assert "display" in sample_agent_card.inventory_types
        assert "video" in sample_agent_card.inventory_types

    def test_agent_card_deal_types(self, sample_agent_card):
        assert "preferred_deal" in sample_agent_card.supported_deal_types
        assert "private_auction" in sample_agent_card.supported_deal_types


class TestAgentProvider:
    def test_provider_full(self, sample_provider):
        assert sample_provider.name == "Test Agency DSP"
        assert sample_provider.url == "https://dsp.example.com"

    def test_provider_minimal(self):
        p = AgentProvider(name="Minimal")
        assert p.url is None
        assert p.description is None


class TestAgentCapabilities:
    def test_defaults(self):
        cap = AgentCapabilities()
        assert cap.protocols == ["a2a"]
        assert cap.streaming is False
        assert cap.push_notifications is False

    def test_multi_protocol(self):
        cap = AgentCapabilities(protocols=["a2a", "opendirect21", "openrtb26"])
        assert len(cap.protocols) == 3


class TestAgentType:
    def test_enum_values(self):
        assert AgentType.BUYER.value == "buyer"
        assert AgentType.SELLER.value == "seller"
        assert AgentType.TOOL_PROVIDER.value == "tool_provider"
        assert AgentType.DATA_PROVIDER.value == "data_provider"
        assert AgentType.OTHER.value == "other"


class TestRegistrySource:
    def test_registry_source_creation(self):
        src = RegistrySource(
            registry_id="iab_aamp",
            registry_name="IAB Tech Lab AAMP",
            registry_url="https://tools.iabtechlab.com/agent-registry",
            external_agent_id="aamp-abc123",
            verified_at=datetime(2026, 3, 1),
        )
        assert src.registry_id == "iab_aamp"
        assert src.external_agent_id == "aamp-abc123"

    def test_registry_source_optional_fields(self):
        src = RegistrySource(
            registry_id="custom",
            registry_name="Custom Registry",
            registry_url="https://custom.example.com",
        )
        assert src.external_agent_id is None
        assert src.verified_at is None


# =============================================================================
# Trust Status & Access Tier Mapping Tests
# =============================================================================


class TestTrustStatus:
    def test_enum_values(self):
        assert TrustStatus.UNKNOWN.value == "unknown"
        assert TrustStatus.REGISTERED.value == "registered"
        assert TrustStatus.APPROVED.value == "approved"
        assert TrustStatus.PREFERRED.value == "preferred"
        assert TrustStatus.BLOCKED.value == "blocked"

    def test_trust_to_tier_unknown(self):
        assert TRUST_TO_TIER_MAP[TrustStatus.UNKNOWN] == AccessTier.PUBLIC

    def test_trust_to_tier_registered(self):
        assert TRUST_TO_TIER_MAP[TrustStatus.REGISTERED] == AccessTier.SEAT

    def test_trust_to_tier_approved(self):
        assert TRUST_TO_TIER_MAP[TrustStatus.APPROVED] == AccessTier.ADVERTISER

    def test_trust_to_tier_preferred(self):
        assert TRUST_TO_TIER_MAP[TrustStatus.PREFERRED] == AccessTier.ADVERTISER

    def test_trust_to_tier_blocked(self):
        assert TRUST_TO_TIER_MAP[TrustStatus.BLOCKED] is None

    def test_all_statuses_mapped(self):
        for status in TrustStatus:
            assert status in TRUST_TO_TIER_MAP


# =============================================================================
# RegisteredAgent Tests
# =============================================================================


class TestRegisteredAgent:
    def test_creation(self, sample_registered_agent):
        assert sample_registered_agent.agent_id == "agent-test1234"
        assert sample_registered_agent.agent_type == AgentType.BUYER
        assert sample_registered_agent.trust_status == TrustStatus.REGISTERED
        assert sample_registered_agent.interaction_count == 0

    def test_effective_access_ceiling_registered(self, sample_registered_agent):
        assert sample_registered_agent.effective_access_ceiling == AccessTier.SEAT

    def test_effective_access_ceiling_blocked(self, sample_agent_card):
        agent = RegisteredAgent(
            agent_id="blocked-agent",
            agent_card=sample_agent_card,
            trust_status=TrustStatus.BLOCKED,
        )
        assert agent.effective_access_ceiling is None

    def test_effective_access_ceiling_approved(self, sample_agent_card):
        agent = RegisteredAgent(
            agent_id="approved-agent",
            agent_card=sample_agent_card,
            trust_status=TrustStatus.APPROVED,
        )
        assert agent.effective_access_ceiling == AccessTier.ADVERTISER

    def test_is_blocked_true(self, sample_agent_card):
        agent = RegisteredAgent(
            agent_id="blocked",
            agent_card=sample_agent_card,
            trust_status=TrustStatus.BLOCKED,
        )
        assert agent.is_blocked is True

    def test_is_blocked_false(self, sample_registered_agent):
        assert sample_registered_agent.is_blocked is False

    def test_multiple_registry_sources(self, sample_agent_card):
        agent = RegisteredAgent(
            agent_id="multi-reg",
            agent_card=sample_agent_card,
            registry_sources=[
                RegistrySource(
                    registry_id="iab_aamp",
                    registry_name="IAB Tech Lab AAMP",
                    registry_url="https://tools.iabtechlab.com/agent-registry",
                ),
                RegistrySource(
                    registry_id="vendor_xyz",
                    registry_name="Vendor XYZ Registry",
                    registry_url="https://registry.vendor-xyz.com",
                ),
            ],
        )
        assert len(agent.registry_sources) == 2
        assert agent.registry_sources[0].registry_id == "iab_aamp"
        assert agent.registry_sources[1].registry_id == "vendor_xyz"

    def test_serialization_roundtrip(self, sample_registered_agent):
        data = sample_registered_agent.model_dump(mode="json")
        restored = RegisteredAgent(**data)
        assert restored.agent_id == sample_registered_agent.agent_id
        assert restored.trust_status == sample_registered_agent.trust_status
        assert restored.agent_card.name == sample_registered_agent.agent_card.name


# =============================================================================
# BuyerContext Effective Tier Capping Tests
# =============================================================================


class TestBuyerContextTierCapping:
    """Tests for BuyerContext.effective_tier with max_access_tier ceiling."""

    def test_no_ceiling_advertiser(self):
        """No ceiling → full ADVERTISER access."""
        ctx = BuyerContext(
            identity=BuyerIdentity(
                agency_id="a1",
                agency_name="Agency",
                advertiser_id="adv1",
                advertiser_name="Adv",
            ),
            is_authenticated=True,
        )
        assert ctx.effective_tier == AccessTier.ADVERTISER

    def test_seat_ceiling_caps_advertiser(self):
        """SEAT ceiling + ADVERTISER claim → SEAT."""
        ctx = BuyerContext(
            identity=BuyerIdentity(
                agency_id="a1",
                agency_name="Agency",
                advertiser_id="adv1",
                advertiser_name="Adv",
            ),
            is_authenticated=True,
            max_access_tier=AccessTier.SEAT,
        )
        assert ctx.effective_tier == AccessTier.SEAT

    def test_advertiser_ceiling_agency_claim(self):
        """ADVERTISER ceiling + AGENCY claim → AGENCY (within ceiling)."""
        ctx = BuyerContext(
            identity=BuyerIdentity(
                agency_id="a1",
                agency_name="Agency",
            ),
            is_authenticated=True,
            max_access_tier=AccessTier.ADVERTISER,
        )
        assert ctx.effective_tier == AccessTier.AGENCY

    def test_unauthenticated_ignores_ceiling(self):
        """Unauthenticated always → PUBLIC regardless of ceiling."""
        ctx = BuyerContext(
            identity=BuyerIdentity(
                agency_id="a1",
                agency_name="Agency",
            ),
            is_authenticated=False,
            max_access_tier=AccessTier.ADVERTISER,
        )
        assert ctx.effective_tier == AccessTier.PUBLIC

    def test_public_ceiling_caps_agency(self):
        """PUBLIC ceiling + AGENCY claim → PUBLIC."""
        ctx = BuyerContext(
            identity=BuyerIdentity(
                agency_id="a1",
                agency_name="Agency",
            ),
            is_authenticated=True,
            max_access_tier=AccessTier.PUBLIC,
        )
        assert ctx.effective_tier == AccessTier.PUBLIC

    def test_agency_ceiling_caps_advertiser(self):
        """AGENCY ceiling + ADVERTISER claim → AGENCY."""
        ctx = BuyerContext(
            identity=BuyerIdentity(
                agency_id="a1",
                agency_name="Agency",
                advertiser_id="adv1",
                advertiser_name="Adv",
            ),
            is_authenticated=True,
            max_access_tier=AccessTier.AGENCY,
        )
        assert ctx.effective_tier == AccessTier.AGENCY

    def test_same_tier_and_ceiling(self):
        """SEAT ceiling + SEAT claim → SEAT."""
        ctx = BuyerContext(
            identity=BuyerIdentity(seat_id="s1", seat_name="Seat"),
            is_authenticated=True,
            max_access_tier=AccessTier.SEAT,
        )
        assert ctx.effective_tier == AccessTier.SEAT

    def test_agent_context_fields(self):
        """Agent registry context fields on BuyerContext."""
        ctx = BuyerContext(
            identity=BuyerIdentity(),
            is_authenticated=True,
            agent_url="https://buyer.example.com",
            agent_trust_status="registered",
            max_access_tier=AccessTier.SEAT,
        )
        assert ctx.agent_url == "https://buyer.example.com"
        assert ctx.agent_trust_status == "registered"
        assert ctx.max_access_tier == AccessTier.SEAT

    def test_negotiation_eligibility_respects_ceiling(self):
        """Negotiation eligibility uses effective_tier (capped)."""
        # ADVERTISER claim but SEAT ceiling → SEAT → no negotiation
        ctx = BuyerContext(
            identity=BuyerIdentity(
                agency_id="a1",
                agency_name="Agency",
                advertiser_id="adv1",
                advertiser_name="Adv",
            ),
            is_authenticated=True,
            max_access_tier=AccessTier.SEAT,
        )
        assert ctx.eligible_for_negotiation is False

    def test_negotiation_eligibility_within_ceiling(self):
        """ADVERTISER ceiling + AGENCY claim → AGENCY → eligible."""
        ctx = BuyerContext(
            identity=BuyerIdentity(
                agency_id="a1",
                agency_name="Agency",
            ),
            is_authenticated=True,
            max_access_tier=AccessTier.ADVERTISER,
        )
        assert ctx.eligible_for_negotiation is True


# =============================================================================
# AAMP Registry Client Tests
# =============================================================================


class TestAAMPRegistryClient:
    def test_client_creation(self, aamp_client):
        assert aamp_client.registry_id == "iab_aamp"
        assert aamp_client.registry_name == "IAB Tech Lab AAMP"

    @pytest.mark.asyncio
    async def test_verify_known_agent(self, aamp_client):
        """Known AAMP test URL returns registered."""
        is_reg, ext_id = await aamp_client.verify_registration(
            "https://agentic-direct-server-hwgrypmndq-uk.a.run.app"
        )
        assert is_reg is True
        assert ext_id is not None
        assert ext_id.startswith("aamp-")

    @pytest.mark.asyncio
    async def test_verify_unknown_agent(self, aamp_client):
        """Unknown URL returns not registered."""
        is_reg, ext_id = await aamp_client.verify_registration("https://unknown-agent.example.com")
        assert is_reg is False
        assert ext_id is None

    @pytest.mark.asyncio
    async def test_lookup_aamp_agent(self, aamp_client):
        """Lookup with aamp- prefix returns mock data."""
        result = await aamp_client.lookup_agent("aamp-abc123")
        assert result is not None
        assert result["agent_id"] == "aamp-abc123"
        assert result["status"] == "active"

    @pytest.mark.asyncio
    async def test_lookup_unknown_agent(self, aamp_client):
        """Lookup without aamp- prefix returns None."""
        result = await aamp_client.lookup_agent("custom-xyz")
        assert result is None

    @pytest.mark.asyncio
    async def test_search_agents_stub(self, aamp_client):
        """Search returns empty list (stub)."""
        results = await aamp_client.search_agents(agent_type="buyer")
        assert results == []


class TestFetchAgentCard:
    @pytest.mark.asyncio
    async def test_fetch_card_http_error(self):
        """HTTP error returns None."""
        with patch("ad_seller.clients.agent_registry_client.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
            mock_cls.return_value = mock_client

            result = await fetch_agent_card("https://unreachable.example.com")
            assert result is None

    @pytest.mark.asyncio
    async def test_fetch_card_success(self):
        """Successful fetch returns AgentCard."""
        card_data = {
            "name": "Remote Agent",
            "description": "A remote buyer agent",
            "url": "https://remote.example.com",
            "provider": {"name": "Remote Co"},
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = card_data

        with patch("ad_seller.clients.agent_registry_client.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_cls.return_value = mock_client

            result = await fetch_agent_card("https://remote.example.com")
            assert result is not None
            assert result.name == "Remote Agent"
            assert result.url == "https://remote.example.com"

    @pytest.mark.asyncio
    async def test_fetch_card_404(self):
        """404 response returns None."""
        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch("ad_seller.clients.agent_registry_client.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_cls.return_value = mock_client

            result = await fetch_agent_card("https://no-card.example.com")
            assert result is None


# =============================================================================
# AgentRegistryService Tests
# =============================================================================


class TestAgentRegistryService:
    @pytest.mark.asyncio
    async def test_register_new_agent(self, registry_service, mock_storage, sample_agent_card):
        """Register a new agent in local registry."""
        agent = await registry_service.register_agent(
            agent_card=sample_agent_card,
            agent_type=AgentType.BUYER,
            trust_status=TrustStatus.REGISTERED,
        )
        assert agent.agent_id.startswith("agent-")
        assert agent.trust_status == TrustStatus.REGISTERED
        assert agent.agent_card.name == "Test Buyer Agent"
        mock_storage.set_agent.assert_called()

    @pytest.mark.asyncio
    async def test_register_updates_existing(
        self, registry_service, mock_storage, sample_agent_card, sample_registered_agent
    ):
        """Re-registering an agent updates the existing entry."""
        # Simulate existing agent found by URL
        agent_data = sample_registered_agent.model_dump(mode="json")
        mock_storage.get.side_effect = lambda key: (
            sample_registered_agent.agent_id if key.startswith("agent_url_index:") else None
        )
        mock_storage.get_agent.return_value = agent_data

        updated_card = sample_agent_card.model_copy(update={"description": "Updated description"})
        agent = await registry_service.register_agent(
            agent_card=updated_card,
            trust_status=TrustStatus.APPROVED,
        )
        assert agent.trust_status == TrustStatus.APPROVED
        assert agent.agent_card.description == "Updated description"

    @pytest.mark.asyncio
    async def test_get_agent(self, registry_service, mock_storage, sample_registered_agent):
        """Retrieve agent by ID."""
        mock_storage.get_agent.return_value = sample_registered_agent.model_dump(mode="json")
        agent = await registry_service.get_agent("agent-test1234")
        assert agent is not None
        assert agent.agent_id == "agent-test1234"

    @pytest.mark.asyncio
    async def test_get_agent_not_found(self, registry_service, mock_storage):
        """Non-existent agent returns None."""
        mock_storage.get_agent.return_value = None
        agent = await registry_service.get_agent("nonexistent")
        assert agent is None

    @pytest.mark.asyncio
    async def test_list_agents_filter_by_type(
        self, registry_service, mock_storage, sample_registered_agent
    ):
        """List agents filtered by type."""
        mock_storage.list_agents.return_value = [sample_registered_agent.model_dump(mode="json")]
        agents = await registry_service.list_agents(agent_type=AgentType.BUYER)
        assert len(agents) == 1
        assert agents[0].agent_type == AgentType.BUYER

    @pytest.mark.asyncio
    async def test_list_agents_filter_by_trust(
        self, registry_service, mock_storage, sample_registered_agent
    ):
        """List agents filtered by trust status."""
        mock_storage.list_agents.return_value = [sample_registered_agent.model_dump(mode="json")]
        # Filter for APPROVED — should return empty since agent is REGISTERED
        agents = await registry_service.list_agents(trust_status=TrustStatus.APPROVED)
        assert len(agents) == 0

        # Filter for REGISTERED
        agents = await registry_service.list_agents(trust_status=TrustStatus.REGISTERED)
        assert len(agents) == 1

    @pytest.mark.asyncio
    async def test_update_trust_status(
        self, registry_service, mock_storage, sample_registered_agent
    ):
        """Update an agent's trust status."""
        mock_storage.get_agent.return_value = sample_registered_agent.model_dump(mode="json")
        agent = await registry_service.update_trust_status(
            "agent-test1234", TrustStatus.APPROVED, notes="Manually verified"
        )
        assert agent is not None
        assert agent.trust_status == TrustStatus.APPROVED
        assert agent.notes == "Manually verified"

    @pytest.mark.asyncio
    async def test_update_trust_not_found(self, registry_service, mock_storage):
        """Update trust for non-existent agent returns None."""
        mock_storage.get_agent.return_value = None
        result = await registry_service.update_trust_status("nonexistent", TrustStatus.BLOCKED)
        assert result is None

    @pytest.mark.asyncio
    async def test_remove_agent(self, registry_service, mock_storage, sample_registered_agent):
        """Remove agent from registry."""
        mock_storage.get_agent.return_value = sample_registered_agent.model_dump(mode="json")
        result = await registry_service.remove_agent("agent-test1234")
        assert result is True
        mock_storage.delete.assert_called()
        mock_storage.delete_agent.assert_called_with("agent-test1234")

    @pytest.mark.asyncio
    async def test_remove_agent_not_found(self, registry_service, mock_storage):
        """Remove non-existent agent returns False."""
        mock_storage.get_agent.return_value = None
        result = await registry_service.remove_agent("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_record_interaction(
        self, registry_service, mock_storage, sample_registered_agent
    ):
        """Record interaction bumps count and last_seen."""
        mock_storage.get_agent.return_value = sample_registered_agent.model_dump(mode="json")
        await registry_service.record_interaction("agent-test1234")
        mock_storage.set_agent.assert_called()


class TestResolveAgentAccess:
    """Tests for the resolve_agent_access key method."""

    @pytest.mark.asyncio
    async def test_resolve_known_local_agent(
        self, registry_service, mock_storage, sample_registered_agent
    ):
        """Known local agent returns cached result."""
        agent_data = sample_registered_agent.model_dump(mode="json")
        mock_storage.get.side_effect = lambda key: (
            sample_registered_agent.agent_id if key.startswith("agent_url_index:") else None
        )
        mock_storage.get_agent.return_value = agent_data

        agent, tier = await registry_service.resolve_agent_access("https://buyer.example.com")
        assert agent is not None
        assert tier == AccessTier.SEAT  # REGISTERED → SEAT

    @pytest.mark.asyncio
    async def test_resolve_blocked_agent(self, registry_service, mock_storage, sample_agent_card):
        """Blocked agent returns None tier."""
        blocked = RegisteredAgent(
            agent_id="blocked-1",
            agent_card=sample_agent_card,
            trust_status=TrustStatus.BLOCKED,
        )
        mock_storage.get.side_effect = lambda key: (
            "blocked-1" if key.startswith("agent_url_index:") else None
        )
        mock_storage.get_agent.return_value = blocked.model_dump(mode="json")

        agent, tier = await registry_service.resolve_agent_access("https://buyer.example.com")
        assert agent is not None
        assert tier is None  # Blocked → None

    @pytest.mark.asyncio
    async def test_resolve_unknown_agent_no_card(self, registry_service, mock_storage):
        """Agent with no .well-known card gets PUBLIC access."""
        # No local record, no remote card
        mock_storage.get.return_value = None
        mock_storage.get_agent.return_value = None

        with patch(
            "ad_seller.registry.agent_registry.fetch_agent_card",
            return_value=None,
        ):
            agent, tier = await registry_service.resolve_agent_access("https://mystery.example.com")
            assert agent is None
            assert tier == AccessTier.PUBLIC

    @pytest.mark.asyncio
    async def test_resolve_aamp_registered_agent(
        self, registry_service, mock_storage, sample_agent_card
    ):
        """Agent found in AAMP gets REGISTERED status → SEAT tier."""
        mock_storage.get.return_value = None
        mock_storage.get_agent.return_value = None

        with patch(
            "ad_seller.registry.agent_registry.fetch_agent_card",
            return_value=sample_agent_card,
        ):
            # Mock AAMP client to report registered
            registry_service._registry_clients[0].verify_registration = AsyncMock(
                return_value=(True, "aamp-ext123")
            )

            agent, tier = await registry_service.resolve_agent_access("https://buyer.example.com")
            assert agent is not None
            assert agent.trust_status == TrustStatus.REGISTERED
            assert tier == AccessTier.SEAT

    @pytest.mark.asyncio
    async def test_resolve_unregistered_agent_with_card(
        self, registry_service, mock_storage, sample_agent_card
    ):
        """Agent with card but not in AAMP gets UNKNOWN → PUBLIC."""
        mock_storage.get.return_value = None
        mock_storage.get_agent.return_value = None

        with patch(
            "ad_seller.registry.agent_registry.fetch_agent_card",
            return_value=sample_agent_card,
        ):
            registry_service._registry_clients[0].verify_registration = AsyncMock(
                return_value=(False, None)
            )

            agent, tier = await registry_service.resolve_agent_access(
                "https://newagent.example.com"
            )
            assert agent is not None
            assert agent.trust_status == TrustStatus.UNKNOWN
            assert tier == AccessTier.PUBLIC


class TestComputeEffectiveTier:
    """Tests for the static compute_effective_tier method."""

    def test_registered_claims_advertiser_capped_to_seat(self):
        result = AgentRegistryService.compute_effective_tier(
            TrustStatus.REGISTERED, AccessTier.ADVERTISER
        )
        assert result == AccessTier.SEAT

    def test_approved_claims_agency_stays_agency(self):
        result = AgentRegistryService.compute_effective_tier(
            TrustStatus.APPROVED, AccessTier.AGENCY
        )
        assert result == AccessTier.AGENCY

    def test_unknown_claims_advertiser_capped_to_public(self):
        result = AgentRegistryService.compute_effective_tier(
            TrustStatus.UNKNOWN, AccessTier.ADVERTISER
        )
        assert result == AccessTier.PUBLIC

    def test_preferred_claims_advertiser_gets_advertiser(self):
        result = AgentRegistryService.compute_effective_tier(
            TrustStatus.PREFERRED, AccessTier.ADVERTISER
        )
        assert result == AccessTier.ADVERTISER

    def test_blocked_returns_none(self):
        result = AgentRegistryService.compute_effective_tier(
            TrustStatus.BLOCKED, AccessTier.ADVERTISER
        )
        assert result is None

    def test_registered_claims_seat_stays_seat(self):
        result = AgentRegistryService.compute_effective_tier(
            TrustStatus.REGISTERED, AccessTier.SEAT
        )
        assert result == AccessTier.SEAT

    def test_registered_claims_public_stays_public(self):
        result = AgentRegistryService.compute_effective_tier(
            TrustStatus.REGISTERED, AccessTier.PUBLIC
        )
        assert result == AccessTier.PUBLIC
