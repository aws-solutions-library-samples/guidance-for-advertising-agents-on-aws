# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for GAM models."""

from datetime import datetime

from ad_seller.models.gam import (
    AudienceSegmentMapping,
    GAMAdUnit,
    GAMAdUnitSize,
    GAMAdUnitTargeting,
    GAMAudienceSegment,
    GAMAudienceSegmentType,
    GAMBookingResult,
    GAMCostType,
    GAMCustomCriteriaSet,
    GAMDateTime,
    GAMGoal,
    GAMGoalType,
    GAMInventoryTargeting,
    GAMLineItem,
    GAMLineItemType,
    GAMMoney,
    GAMOrder,
    GAMOrderStatus,
    GAMPrivateAuction,
    GAMPrivateAuctionDeal,
    GAMSize,
    GAMTargeting,
    GAMUnitType,
)


class TestGAMMoney:
    """Tests for GAMMoney model."""

    def test_money_creation(self):
        """Test basic money creation."""
        money = GAMMoney(currency_code="USD", micro_amount=15_000_000)
        assert money.currency_code == "USD"
        assert money.micro_amount == 15_000_000

    def test_from_dollars(self):
        """Test creating from dollar amount."""
        money = GAMMoney.from_dollars(15.50, "USD")
        assert money.currency_code == "USD"
        assert money.micro_amount == 15_500_000

    def test_to_dollars(self):
        """Test converting to dollar amount."""
        money = GAMMoney(currency_code="USD", micro_amount=25_750_000)
        assert money.to_dollars() == 25.75

    def test_money_alias(self):
        """Test field aliases work."""
        money = GAMMoney(currencyCode="EUR", microAmount=10_000_000)
        assert money.currency_code == "EUR"
        assert money.micro_amount == 10_000_000


class TestGAMSize:
    """Tests for GAMSize model."""

    def test_size_creation(self):
        """Test size creation."""
        size = GAMSize(width=300, height=250)
        assert size.width == 300
        assert size.height == 250
        assert size.is_aspect_ratio is False

    def test_aspect_ratio_size(self):
        """Test aspect ratio size."""
        size = GAMSize(width=16, height=9, isAspectRatio=True)
        assert size.is_aspect_ratio is True


class TestGAMDateTime:
    """Tests for GAMDateTime model."""

    def test_from_datetime(self):
        """Test creating from Python datetime."""
        dt = datetime(2026, 3, 15, 10, 30, 0)
        gam_dt = GAMDateTime.from_datetime(dt)
        assert gam_dt.date["year"] == 2026
        assert gam_dt.date["month"] == 3
        assert gam_dt.date["day"] == 15
        assert gam_dt.hour == 10
        assert gam_dt.minute == 30

    def test_to_datetime(self):
        """Test converting to Python datetime."""
        gam_dt = GAMDateTime(
            date={"year": 2026, "month": 6, "day": 1},
            hour=14,
            minute=0,
            second=0,
        )
        dt = gam_dt.to_datetime()
        assert dt.year == 2026
        assert dt.month == 6
        assert dt.day == 1
        assert dt.hour == 14


class TestGAMEnums:
    """Tests for GAM enum values."""

    def test_line_item_types(self):
        """Test line item type enum values."""
        assert GAMLineItemType.SPONSORSHIP.value == "SPONSORSHIP"
        assert GAMLineItemType.STANDARD.value == "STANDARD"
        assert GAMLineItemType.PREFERRED_DEAL.value == "PREFERRED_DEAL"
        assert GAMLineItemType.PRICE_PRIORITY.value == "PRICE_PRIORITY"

    def test_order_status(self):
        """Test order status enum values."""
        assert GAMOrderStatus.DRAFT.value == "DRAFT"
        assert GAMOrderStatus.APPROVED.value == "APPROVED"
        assert GAMOrderStatus.CANCELED.value == "CANCELED"

    def test_cost_types(self):
        """Test cost type enum values."""
        assert GAMCostType.CPM.value == "CPM"
        assert GAMCostType.CPC.value == "CPC"
        assert GAMCostType.CPCV.value == "CPCV"
        assert GAMCostType.CPD.value == "CPD"

    def test_goal_types(self):
        """Test goal type enum values."""
        assert GAMGoalType.LIFETIME.value == "LIFETIME"
        assert GAMGoalType.DAILY.value == "DAILY"

    def test_unit_types(self):
        """Test unit type enum values."""
        assert GAMUnitType.IMPRESSIONS.value == "IMPRESSIONS"
        assert GAMUnitType.CLICKS.value == "CLICKS"
        assert GAMUnitType.VIEWABLE_IMPRESSIONS.value == "VIEWABLE_IMPRESSIONS"


class TestGAMAdUnit:
    """Tests for GAMAdUnit model."""

    def test_ad_unit_creation(self):
        """Test ad unit creation."""
        ad_unit = GAMAdUnit(
            id="12345",
            name="Homepage Banner",
            status="ACTIVE",
        )
        assert ad_unit.id == "12345"
        assert ad_unit.name == "Homepage Banner"
        assert ad_unit.status == "ACTIVE"

    def test_ad_unit_with_sizes(self):
        """Test ad unit with sizes."""
        size = GAMSize(width=728, height=90)
        ad_unit_size = GAMAdUnitSize(size=size)
        ad_unit = GAMAdUnit(
            id="12345",
            name="Leaderboard",
            ad_unit_sizes=[ad_unit_size],
        )
        assert len(ad_unit.ad_unit_sizes) == 1
        assert ad_unit.ad_unit_sizes[0].size.width == 728


class TestGAMOrder:
    """Tests for GAMOrder model."""

    def test_order_creation(self):
        """Test order creation."""
        order = GAMOrder(
            name="Q1 Campaign",
            advertiser_id="111",
            trafficker_id="222",
        )
        assert order.name == "Q1 Campaign"
        assert order.advertiser_id == "111"
        assert order.trafficker_id == "222"
        assert order.status == GAMOrderStatus.DRAFT

    def test_order_with_external_id(self):
        """Test order with external reference."""
        order = GAMOrder(
            name="Campaign",
            advertiser_id="111",
            trafficker_id="222",
            external_order_id="OD-DEAL-123",
            is_programmatic=True,
        )
        assert order.external_order_id == "OD-DEAL-123"
        assert order.is_programmatic is True


class TestGAMLineItem:
    """Tests for GAMLineItem model."""

    def test_line_item_creation(self):
        """Test line item creation."""
        line_item = GAMLineItem(
            order_id="999",
            name="Display Campaign",
            line_item_type=GAMLineItemType.STANDARD,
            cost_per_unit=GAMMoney.from_dollars(15.0),
            primary_goal=GAMGoal(
                goal_type=GAMGoalType.LIFETIME,
                unit_type=GAMUnitType.IMPRESSIONS,
                units=1_000_000,
            ),
        )
        assert line_item.order_id == "999"
        assert line_item.line_item_type == GAMLineItemType.STANDARD
        assert line_item.cost_per_unit.to_dollars() == 15.0
        assert line_item.primary_goal.units == 1_000_000

    def test_preferred_deal_line_item(self):
        """Test preferred deal line item."""
        line_item = GAMLineItem(
            order_id="999",
            name="Preferred Deal",
            line_item_type=GAMLineItemType.PREFERRED_DEAL,
            cost_type=GAMCostType.CPM,
            cost_per_unit=GAMMoney.from_dollars(20.0),
            primary_goal=GAMGoal(
                goal_type=GAMGoalType.NONE,
                unit_type=GAMUnitType.IMPRESSIONS,
                units=-1,  # Unlimited
            ),
        )
        assert line_item.line_item_type == GAMLineItemType.PREFERRED_DEAL
        assert line_item.primary_goal.units == -1


class TestGAMTargeting:
    """Tests for GAM targeting models."""

    def test_inventory_targeting(self):
        """Test inventory targeting."""
        targeting = GAMInventoryTargeting(
            targeted_ad_units=[
                GAMAdUnitTargeting(ad_unit_id="123", include_descendants=True),
                GAMAdUnitTargeting(ad_unit_id="456", include_descendants=False),
            ]
        )
        assert len(targeting.targeted_ad_units) == 2
        assert targeting.targeted_ad_units[0].ad_unit_id == "123"

    def test_full_targeting(self):
        """Test complete targeting configuration."""
        targeting = GAMTargeting(
            inventory_targeting=GAMInventoryTargeting(
                targeted_ad_units=[GAMAdUnitTargeting(ad_unit_id="123")]
            ),
            custom_targeting=GAMCustomCriteriaSet(
                logical_operator="AND",
                children=[],
            ),
        )
        assert targeting.inventory_targeting is not None
        assert targeting.custom_targeting is not None


class TestGAMPrivateAuction:
    """Tests for private auction models."""

    def test_private_auction_creation(self):
        """Test private auction creation."""
        auction = GAMPrivateAuction(
            name="Premium CTV Auction",
            description="High-value CTV inventory",
        )
        assert auction.name == "Premium CTV Auction"
        assert auction.status == "ACTIVE"

    def test_private_auction_deal(self):
        """Test private auction deal creation."""
        deal = GAMPrivateAuctionDeal(
            private_auction_id="PA-001",
            buyer_account_id="BUYER-123",
            floor_price=GAMMoney.from_dollars(25.0),
            external_deal_id="OD-LINE-456",
        )
        assert deal.private_auction_id == "PA-001"
        assert deal.floor_price.to_dollars() == 25.0
        assert deal.external_deal_id == "OD-LINE-456"


class TestGAMAudienceSegment:
    """Tests for audience segment models."""

    def test_audience_segment_creation(self):
        """Test audience segment creation."""
        segment = GAMAudienceSegment(
            id=12345,
            name="Sports Enthusiasts",
            type=GAMAudienceSegmentType.RULE_BASED,
            size=500_000,
        )
        assert segment.id == 12345
        assert segment.name == "Sports Enthusiasts"
        assert segment.size == 500_000

    def test_third_party_segment(self):
        """Test third-party segment."""
        segment = GAMAudienceSegment(
            id=99999,
            name="BlueKai - Auto Intenders",
            type=GAMAudienceSegmentType.THIRD_PARTY,
            data_provider_name="BlueKai",
        )
        assert segment.type == GAMAudienceSegmentType.THIRD_PARTY
        assert segment.data_provider_name == "BlueKai"


class TestAudienceSegmentMapping:
    """Tests for audience segment mapping model."""

    def test_mapping_with_iab_taxonomy(self):
        """Test mapping with IAB Audience Taxonomy ID."""
        mapping = AudienceSegmentMapping(
            gam_segment_id=12345,
            gam_segment_name="Auto Intenders - New Car",
            segment_type="third-party",
            iab_audience_taxonomy_id="3-1",  # Purchase Intent > Auto Intenders
            data_provider="Oracle Data Cloud",
            last_synced=datetime.now(),
            estimated_size=2_000_000,
        )
        assert mapping.iab_audience_taxonomy_id == "3-1"
        assert mapping.data_provider == "Oracle Data Cloud"

    def test_mapping_with_ucp(self):
        """Test mapping with UCP audience ID."""
        mapping = AudienceSegmentMapping(
            gam_segment_id=54321,
            gam_segment_name="Custom - High Value Users",
            segment_type="rule-based",
            ucp_audience_id="ucp-audience-hvusers-001",
            last_synced=datetime.now(),
        )
        assert mapping.ucp_audience_id == "ucp-audience-hvusers-001"
        assert mapping.iab_audience_taxonomy_id is None

    def test_mapping_minimal(self):
        """Test minimal mapping (GAM segment only)."""
        mapping = AudienceSegmentMapping(
            gam_segment_id=11111,
            gam_segment_name="Site Visitors",
            segment_type="non-rule-based",
            last_synced=datetime.now(),
        )
        assert mapping.gam_segment_id == 11111
        assert mapping.ucp_audience_id is None
        assert mapping.iab_audience_taxonomy_id is None


class TestGAMBookingResult:
    """Tests for booking result model."""

    def test_successful_booking(self):
        """Test successful booking result."""
        result = GAMBookingResult(
            success=True,
            order_id="ORD-123",
            line_item_id="LI-456",
            deal_id="OD-DEAL-789",
            status="BOOKED",
            message="Deal successfully booked in GAM",
        )
        assert result.success is True
        assert result.order_id == "ORD-123"
        assert result.line_item_id == "LI-456"
        assert result.status == "BOOKED"

    def test_failed_booking(self):
        """Test failed booking result."""
        result = GAMBookingResult(
            success=False,
            error="Advertiser company not found",
            deal_id="OD-DEAL-789",
        )
        assert result.success is False
        assert result.error == "Advertiser company not found"
        assert result.order_id is None
