# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for Ad Seller System models."""

from ad_seller.models.buyer_identity import (
    AccessTier,
    BuyerIdentity,
    IdentityLevel,
)
from ad_seller.models.core import (
    DealType,
    Organization,
    OrganizationRole,
    PricingModel,
    Product,
    ProposalStatus,
)
from ad_seller.models.pricing_tiers import (
    DiscountType,
    PricingTier,
    VolumeDiscount,
)


class TestBuyerIdentity:
    """Tests for BuyerIdentity model."""

    def test_anonymous_identity(self):
        """Test anonymous buyer has public tier."""
        identity = BuyerIdentity()
        assert identity.access_tier == AccessTier.PUBLIC
        assert identity.identity_level == IdentityLevel.ANONYMOUS
        assert identity.agency_id is None
        assert identity.advertiser_id is None

    def test_seat_identity(self):
        """Test seat-level buyer."""
        identity = BuyerIdentity(
            seat_id="seat-001",
            seat_name="Test Seat",
        )
        assert identity.access_tier == AccessTier.SEAT
        assert identity.identity_level == IdentityLevel.SEAT_ONLY

    def test_agency_identity(self):
        """Test agency-level buyer."""
        identity = BuyerIdentity(
            agency_id="agency-001",
            agency_name="Test Agency",
        )
        assert identity.access_tier == AccessTier.AGENCY
        assert identity.identity_level == IdentityLevel.AGENCY_ONLY
        assert identity.agency_id == "agency-001"

    def test_advertiser_identity(self):
        """Test advertiser-level buyer."""
        identity = BuyerIdentity(
            agency_id="agency-001",
            agency_name="Test Agency",
            advertiser_id="adv-001",
            advertiser_name="Test Advertiser",
        )
        assert identity.access_tier == AccessTier.ADVERTISER
        assert identity.identity_level == IdentityLevel.AGENCY_AND_ADVERTISER
        assert identity.advertiser_id == "adv-001"


class TestBuyerContext:
    """Tests for BuyerContext model."""

    def test_unauthenticated_context(self, public_buyer_context):
        """Test unauthenticated buyer context."""
        assert public_buyer_context.is_authenticated is False
        assert public_buyer_context.effective_tier == AccessTier.PUBLIC

    def test_authenticated_agency_context(self, agency_buyer_context):
        """Test authenticated agency context."""
        assert agency_buyer_context.is_authenticated is True
        assert agency_buyer_context.effective_tier == AccessTier.AGENCY

    def test_authenticated_advertiser_context(self, advertiser_buyer_context):
        """Test authenticated advertiser context."""
        assert advertiser_buyer_context.is_authenticated is True
        assert advertiser_buyer_context.effective_tier == AccessTier.ADVERTISER

    def test_pricing_key_generation(
        self, public_buyer_context, agency_buyer_context, advertiser_buyer_context
    ):
        """Test pricing key generation."""
        # Public context
        assert public_buyer_context.get_pricing_key() == "public"

        # Agency context
        assert agency_buyer_context.get_pricing_key() == "agency:test-agency-001"

        # Advertiser context
        assert advertiser_buyer_context.get_pricing_key() == "advertiser:test-advertiser-001"

    def test_negotiation_eligibility(
        self, public_buyer_context, agency_buyer_context, advertiser_buyer_context
    ):
        """Test negotiation eligibility based on tier."""
        assert public_buyer_context.eligible_for_negotiation is False
        assert agency_buyer_context.eligible_for_negotiation is True
        assert advertiser_buyer_context.eligible_for_negotiation is True


class TestTieredPricingConfig:
    """Tests for TieredPricingConfig model."""

    def test_default_tiers_created(self, pricing_config):
        """Test default pricing tiers are created."""
        assert len(pricing_config.tiers) == 4  # PUBLIC, SEAT, AGENCY, ADVERTISER
        assert AccessTier.PUBLIC in pricing_config.tiers
        assert AccessTier.AGENCY in pricing_config.tiers
        assert AccessTier.ADVERTISER in pricing_config.tiers

    def test_get_tier_config(self, pricing_config):
        """Test getting tier configuration."""
        public_tier = pricing_config.get_tier_config(AccessTier.PUBLIC)
        assert public_tier is not None
        assert public_tier.tier == AccessTier.PUBLIC
        assert public_tier.show_exact_price is False

        agency_tier = pricing_config.get_tier_config(AccessTier.AGENCY)
        assert agency_tier is not None
        assert agency_tier.tier == AccessTier.AGENCY
        assert agency_tier.tier_discount == 0.10  # 10% discount

        advertiser_tier = pricing_config.get_tier_config(AccessTier.ADVERTISER)
        assert advertiser_tier is not None
        assert advertiser_tier.tier == AccessTier.ADVERTISER
        assert advertiser_tier.tier_discount == 0.15  # 15% discount


class TestPricingTier:
    """Tests for PricingTier model."""

    def test_public_tier_shows_ranges(self):
        """Test public tier shows price ranges."""
        tier = PricingTier(
            tier=AccessTier.PUBLIC,
            tier_name="Public",
            description="General catalog",
            show_exact_price=False,
            price_range_variance=0.2,
        )
        assert tier.show_exact_price is False
        assert tier.price_range_variance == 0.2

    def test_agency_tier_shows_exact_price(self):
        """Test agency tier shows exact prices."""
        tier = PricingTier(
            tier=AccessTier.AGENCY,
            tier_name="Agency",
            description="Agency pricing",
            show_exact_price=True,
            tier_discount=0.10,
            negotiation_enabled=True,
        )
        assert tier.show_exact_price is True
        assert tier.tier_discount == 0.10
        assert tier.negotiation_enabled is True


class TestVolumeDiscount:
    """Tests for VolumeDiscount model."""

    def test_volume_discount_creation(self):
        """Test volume discount tier creation."""
        discount = VolumeDiscount(
            min_impressions=5_000_000,
            max_impressions=10_000_000,
            discount_type=DiscountType.PERCENTAGE,
            discount_value=0.05,
        )
        assert discount.min_impressions == 5_000_000
        assert discount.max_impressions == 10_000_000
        assert discount.discount_value == 0.05

    def test_volume_discount_no_max(self):
        """Test volume discount without max impressions."""
        discount = VolumeDiscount(
            min_impressions=50_000_000,
            discount_type=DiscountType.PERCENTAGE,
            discount_value=0.20,
        )
        assert discount.max_impressions is None
        assert discount.discount_value == 0.20


class TestProductDefinition:
    """Tests for ProductDefinition model."""

    def test_product_creation(self, sample_product):
        """Test product definition creation."""
        assert sample_product.product_id == "test-product-001"
        assert sample_product.name == "Test Display Product"
        assert sample_product.inventory_type == "display"
        assert sample_product.base_cpm == 15.0
        assert sample_product.floor_cpm == 10.0

    def test_product_deal_types(self, sample_product):
        """Test product supported deal types."""
        assert DealType.PREFERRED_DEAL in sample_product.supported_deal_types
        assert DealType.PRIVATE_AUCTION in sample_product.supported_deal_types

    def test_product_pricing_models(self, sample_product):
        """Test product supported pricing models."""
        assert PricingModel.CPM in sample_product.supported_pricing_models

    def test_multiple_products(self, sample_products):
        """Test multiple product definitions."""
        assert len(sample_products) == 3
        assert "test-product-001" in sample_products
        assert "test-product-002" in sample_products
        assert "test-product-003" in sample_products

        # Verify CTV product has higher CPM
        ctv_product = sample_products["test-product-003"]
        assert ctv_product.inventory_type == "ctv"
        assert ctv_product.base_cpm == 35.0


class TestOpenDirect3Models:
    """Tests for core ad tech models."""

    def test_organization_creation(self):
        """Test Organization model creation."""
        org = Organization(
            organizationid="org-001",
            name="Test Publisher",
            role=OrganizationRole.SELLER,
        )
        assert org.organization_id == "org-001"
        assert org.name == "Test Publisher"
        assert org.role == OrganizationRole.SELLER

    def test_product_model(self):
        """Test Product model creation."""
        product = Product(
            productid="prod-001",
            name="Premium Display",
            sellerorganizationid="org-001",
            inventorysegments=["segment-001"],
        )
        assert product.product_id == "prod-001"
        assert product.seller_organization_id == "org-001"
        assert product.inventory_segments == ["segment-001"]

    def test_proposal_status_enum(self):
        """Test ProposalStatus enum values."""
        assert ProposalStatus.DRAFT.value == "draft"
        assert ProposalStatus.SENT.value == "sent"
        assert ProposalStatus.ACCEPTED.value == "accepted"
        assert ProposalStatus.REJECTED.value == "rejected"
        assert ProposalStatus.COUNTERED.value == "countered"

    def test_deal_type_enum(self):
        """Test DealType enum values."""
        assert DealType.PROGRAMMATIC_GUARANTEED.value == "programmaticguaranteed"
        assert DealType.PREFERRED_DEAL.value == "preferreddeal"
        assert DealType.PRIVATE_AUCTION.value == "privateauction"

    def test_pricing_model_enum(self):
        """Test PricingModel enum values."""
        assert PricingModel.CPM.value == "cpm"
        assert PricingModel.CPC.value == "cpc"
        assert PricingModel.CPCV.value == "cpcv"
        assert PricingModel.FLAT_FEE.value == "flat_fee"
