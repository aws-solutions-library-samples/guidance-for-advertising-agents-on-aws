# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Google Ad Manager (GAM) data models.

These models represent GAM entities for integration with the ad seller system.
Supports both REST API (reading) and SOAP API (writing) operations.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# Enums
# =============================================================================


class GAMLineItemType(str, Enum):
    """GAM line item types."""

    SPONSORSHIP = "SPONSORSHIP"  # Reserved, percentage-based or CPD
    STANDARD = "STANDARD"  # Reserved, fixed quantity
    NETWORK = "NETWORK"  # House ads
    BULK = "BULK"  # Bulk campaigns
    PRICE_PRIORITY = "PRICE_PRIORITY"  # Remnant/open auction
    HOUSE = "HOUSE"  # House ads
    CLICK_TRACKING = "CLICK_TRACKING"  # Click tracking only
    ADSENSE = "ADSENSE"  # AdSense backfill
    AD_EXCHANGE = "AD_EXCHANGE"  # Ad Exchange backfill
    BUMPER = "BUMPER"  # Video bumper ads
    PREFERRED_DEAL = "PREFERRED_DEAL"  # Non-reserved, fixed price, priority


class GAMOrderStatus(str, Enum):
    """GAM order status values."""

    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    DISAPPROVED = "DISAPPROVED"
    PAUSED = "PAUSED"
    CANCELED = "CANCELED"
    DELETED = "DELETED"


class GAMLineItemStatus(str, Enum):
    """GAM line item status values."""

    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"  # Also means "ready" for delivery
    DISAPPROVED = "DISAPPROVED"
    PAUSED = "PAUSED"
    INACTIVE = "INACTIVE"
    ARCHIVED = "ARCHIVED"
    COMPLETED = "COMPLETED"
    DELIVERING = "DELIVERING"
    READY = "READY"


class GAMCostType(str, Enum):
    """GAM cost/pricing types."""

    CPM = "CPM"  # Cost per mille (1000 impressions)
    CPC = "CPC"  # Cost per click
    CPD = "CPD"  # Cost per day
    CPU = "CPU"  # Cost per unit
    CPCV = "CPCV"  # Cost per completed view
    VCPM = "VCPM"  # Viewable CPM


class GAMGoalType(str, Enum):
    """GAM goal types for line items."""

    NONE = "NONE"
    LIFETIME = "LIFETIME"
    DAILY = "DAILY"


class GAMUnitType(str, Enum):
    """GAM unit types for goals."""

    IMPRESSIONS = "IMPRESSIONS"
    CLICKS = "CLICKS"
    CLICK_THROUGH_CPA_CONVERSIONS = "CLICK_THROUGH_CPA_CONVERSIONS"
    VIEW_THROUGH_CPA_CONVERSIONS = "VIEW_THROUGH_CPA_CONVERSIONS"
    TOTAL_CPA_CONVERSIONS = "TOTAL_CPA_CONVERSIONS"
    VIEWABLE_IMPRESSIONS = "VIEWABLE_IMPRESSIONS"
    VIDEO_STARTS = "VIDEO_STARTS"
    VIDEO_COMPLETIONS = "VIDEO_COMPLETIONS"


class GAMAudienceSegmentType(str, Enum):
    """GAM audience segment types."""

    RULE_BASED = "RULE_BASED_FIRST_PARTY"
    NON_RULE_BASED = "NON_RULE_BASED_FIRST_PARTY"
    THIRD_PARTY = "THIRD_PARTY"


class GAMAudienceSegmentStatus(str, Enum):
    """GAM audience segment status."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


# =============================================================================
# Money / Pricing Models
# =============================================================================


class GAMMoney(BaseModel):
    """Represents a monetary amount in GAM."""

    model_config = ConfigDict(populate_by_name=True)

    currency_code: str = Field(default="USD", alias="currencyCode")
    micro_amount: int = Field(alias="microAmount")  # Amount × 1,000,000

    @classmethod
    def from_dollars(cls, amount: float, currency: str = "USD") -> "GAMMoney":
        """Create from dollar amount."""
        return cls(currency_code=currency, micro_amount=int(amount * 1_000_000))

    def to_dollars(self) -> float:
        """Convert to dollar amount."""
        return self.micro_amount / 1_000_000


# =============================================================================
# Size Models
# =============================================================================


class GAMSize(BaseModel):
    """Ad unit or creative size."""

    width: int
    height: int
    is_aspect_ratio: bool = Field(default=False, alias="isAspectRatio")


class GAMAdUnitSize(BaseModel):
    """Size specification for an ad unit."""

    model_config = ConfigDict(populate_by_name=True)

    size: GAMSize
    environment_type: str = Field(default="BROWSER", alias="environmentType")
    companions: list[GAMSize] = Field(default_factory=list)
    full_display_string: Optional[str] = Field(default=None, alias="fullDisplayString")


# =============================================================================
# Targeting Models
# =============================================================================


class GAMAdUnitTargeting(BaseModel):
    """Targeting for a specific ad unit."""

    model_config = ConfigDict(populate_by_name=True)

    ad_unit_id: str = Field(alias="adUnitId")
    include_descendants: bool = Field(default=True, alias="includeDescendants")


class GAMInventoryTargeting(BaseModel):
    """Inventory targeting settings."""

    model_config = ConfigDict(populate_by_name=True)

    targeted_ad_units: list[GAMAdUnitTargeting] = Field(
        default_factory=list, alias="targetedAdUnits"
    )
    excluded_ad_units: list[GAMAdUnitTargeting] = Field(
        default_factory=list, alias="excludedAdUnits"
    )


class GAMAudienceSegmentCriteria(BaseModel):
    """Criteria for audience segment targeting."""

    model_config = ConfigDict(populate_by_name=True)

    operator: str = "IS"  # IS or IS_NOT
    audience_segment_ids: list[int] = Field(alias="audienceSegmentIds")


class GAMCustomCriteriaSet(BaseModel):
    """Custom targeting criteria set."""

    model_config = ConfigDict(populate_by_name=True)

    logical_operator: str = Field(default="AND", alias="logicalOperator")
    children: list[dict[str, Any]] = Field(default_factory=list)


class GAMTargeting(BaseModel):
    """Complete targeting configuration for a line item."""

    model_config = ConfigDict(populate_by_name=True)

    inventory_targeting: Optional[GAMInventoryTargeting] = Field(
        default=None, alias="inventoryTargeting"
    )
    geo_targeting: Optional[dict[str, Any]] = Field(default=None, alias="geoTargeting")
    custom_targeting: Optional[GAMCustomCriteriaSet] = Field(default=None, alias="customTargeting")
    user_domain_targeting: Optional[dict[str, Any]] = Field(
        default=None, alias="userDomainTargeting"
    )
    day_part_targeting: Optional[dict[str, Any]] = Field(default=None, alias="dayPartTargeting")
    technology_targeting: Optional[dict[str, Any]] = Field(
        default=None, alias="technologyTargeting"
    )


# =============================================================================
# Goal Models
# =============================================================================


class GAMGoal(BaseModel):
    """Delivery goal for a line item."""

    model_config = ConfigDict(populate_by_name=True)

    goal_type: GAMGoalType = Field(alias="goalType")
    unit_type: GAMUnitType = Field(alias="unitType")
    units: int = -1  # -1 means unlimited


# =============================================================================
# DateTime Models
# =============================================================================


class GAMDateTime(BaseModel):
    """GAM-specific datetime representation."""

    model_config = ConfigDict(populate_by_name=True)

    date: dict[str, int]  # year, month, day
    hour: int = 0
    minute: int = 0
    second: int = 0
    time_zone_id: str = Field(default="America/New_York", alias="timeZoneId")

    @classmethod
    def from_datetime(cls, dt: datetime, time_zone_id: str = "America/New_York") -> "GAMDateTime":
        """Create from Python datetime."""
        return cls(
            date={"year": dt.year, "month": dt.month, "day": dt.day},
            hour=dt.hour,
            minute=dt.minute,
            second=dt.second,
            time_zone_id=time_zone_id,
        )

    def to_datetime(self) -> datetime:
        """Convert to Python datetime (naive, no timezone)."""
        return datetime(
            year=self.date["year"],
            month=self.date["month"],
            day=self.date["day"],
            hour=self.hour,
            minute=self.minute,
            second=self.second,
        )


# =============================================================================
# Core Entity Models
# =============================================================================


class GAMAdUnit(BaseModel):
    """Google Ad Manager Ad Unit."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    parent_id: Optional[str] = Field(default=None, alias="parentId")
    parent_path: list[dict[str, str]] = Field(default_factory=list, alias="parentPath")
    has_children: bool = Field(default=False, alias="hasChildren")
    description: Optional[str] = None
    ad_unit_code: Optional[str] = Field(default=None, alias="adUnitCode")
    status: str = "ACTIVE"
    ad_unit_sizes: list[GAMAdUnitSize] = Field(default_factory=list, alias="adUnitSizes")
    target_window: str = Field(default="BLANK", alias="targetWindow")
    explicitly_targeted: bool = Field(default=False, alias="explicitlyTargeted")
    external_set_top_box_channel_id: Optional[str] = Field(
        default=None, alias="externalSetTopBoxChannelId"
    )


class GAMCompany(BaseModel):
    """GAM Company (Advertiser, Agency, etc.)."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    type: str  # ADVERTISER, AGENCY, HOUSE_ADVERTISER, etc.
    address: Optional[str] = None
    email: Optional[str] = None
    external_id: Optional[str] = Field(default=None, alias="externalId")
    comment: Optional[str] = None


class GAMOrder(BaseModel):
    """Google Ad Manager Order."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = None  # Read-only, GAM-generated
    name: str
    advertiser_id: str = Field(alias="advertiserId")
    trafficker_id: str = Field(alias="traffickerId")
    agency_id: Optional[str] = Field(default=None, alias="agencyId")
    status: GAMOrderStatus = GAMOrderStatus.DRAFT
    start_date_time: Optional[GAMDateTime] = Field(default=None, alias="startDateTime")
    end_date_time: Optional[GAMDateTime] = Field(default=None, alias="endDateTime")
    unlimited_end_date_time: bool = Field(default=False, alias="unlimitedEndDateTime")
    external_order_id: Optional[str] = Field(default=None, alias="externalOrderId")
    notes: Optional[str] = None
    po_number: Optional[str] = Field(default=None, alias="poNumber")
    is_programmatic: bool = Field(default=False, alias="isProgrammatic")


class GAMLineItem(BaseModel):
    """Google Ad Manager Line Item."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = None  # Read-only, GAM-generated
    order_id: str = Field(alias="orderId")
    name: str
    line_item_type: GAMLineItemType = Field(alias="lineItemType")
    status: GAMLineItemStatus = GAMLineItemStatus.DRAFT
    targeting: GAMTargeting = Field(default_factory=GAMTargeting)
    cost_type: GAMCostType = Field(default=GAMCostType.CPM, alias="costType")
    cost_per_unit: GAMMoney = Field(alias="costPerUnit")
    primary_goal: GAMGoal = Field(alias="primaryGoal")
    start_date_time: Optional[GAMDateTime] = Field(default=None, alias="startDateTime")
    end_date_time: Optional[GAMDateTime] = Field(default=None, alias="endDateTime")
    auto_extension_days: int = Field(default=0, alias="autoExtensionDays")
    unlimited_end_date_time: bool = Field(default=False, alias="unlimitedEndDateTime")
    creative_rotation_type: str = Field(default="EVEN", alias="creativeRotationType")
    external_id: Optional[str] = Field(default=None, alias="externalId")
    notes: Optional[str] = None


# =============================================================================
# Private Auction Models
# =============================================================================


class GAMPrivateAuction(BaseModel):
    """GAM Private Auction (parent container for deals)."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = None  # Read-only, GAM-generated
    name: str
    description: Optional[str] = None
    status: str = "ACTIVE"


class GAMPrivateAuctionDeal(BaseModel):
    """GAM Private Auction Deal."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = None  # Read-only, GAM-generated
    private_auction_id: str = Field(alias="privateAuctionId")
    buyer_account_id: str = Field(alias="buyerAccountId")
    external_deal_id: Optional[str] = Field(default=None, alias="externalDealId")
    floor_price: GAMMoney = Field(alias="floorPrice")
    status: str = "ACTIVE"
    targeting: Optional[GAMTargeting] = None
    end_time: Optional[GAMDateTime] = Field(default=None, alias="endTime")


# =============================================================================
# Audience Segment Models
# =============================================================================


class GAMAudienceSegment(BaseModel):
    """GAM Audience Segment."""

    model_config = ConfigDict(populate_by_name=True)

    id: int
    name: str
    type: GAMAudienceSegmentType
    status: GAMAudienceSegmentStatus = GAMAudienceSegmentStatus.ACTIVE
    description: Optional[str] = None
    size: Optional[int] = None  # Number of users in segment
    data_provider_name: Optional[str] = Field(default=None, alias="dataProviderName")
    membership_expiration_days: int = Field(default=30, alias="membershipExpirationDays")


class AudienceSegmentMapping(BaseModel):
    """Maps audience definitions to GAM audience segments.

    Supports multiple audience identifier systems:
    - UCP (User Context Protocol) audience IDs
    - IAB Audience Taxonomy 1.1 IDs (standard categories)
    - Custom/third-party segment identifiers

    Used to translate between OpenDirect/buyer audience requests
    and GAM segment IDs for line item targeting.
    """

    model_config = ConfigDict(populate_by_name=True)

    # GAM identifiers (always present)
    gam_segment_id: int = Field(alias="gamSegmentId")
    gam_segment_name: str = Field(alias="gamSegmentName")
    segment_type: str = Field(
        alias="segmentType"
    )  # "rule-based" | "non-rule-based" | "third-party"

    # UCP mapping (optional - for embedding-based audiences)
    ucp_audience_id: Optional[str] = Field(default=None, alias="ucpAudienceId")

    # IAB Audience Taxonomy 1.1 mapping (optional - for standard categories)
    # Format: "1-1" = Demographics > Age, "2-1" = Interest > Arts & Entertainment, etc.
    # Reference: https://github.com/InteractiveAdvertisingBureau/Taxonomies
    iab_audience_taxonomy_id: Optional[str] = Field(default=None, alias="iabAudienceTaxonomyId")

    # Third-party data provider info (optional)
    data_provider: Optional[str] = Field(default=None, alias="dataProvider")

    # Sync metadata
    last_synced: datetime = Field(alias="lastSynced")
    estimated_size: Optional[int] = Field(default=None, alias="estimatedSize")


# =============================================================================
# Booking Result Models
# =============================================================================


class GAMBookingResult(BaseModel):
    """Result of booking a deal in GAM."""

    model_config = ConfigDict(populate_by_name=True)

    success: bool
    order_id: Optional[str] = Field(default=None, alias="orderId")
    line_item_id: Optional[str] = Field(default=None, alias="lineItemId")
    line_item_ids: list[str] = Field(default_factory=list, alias="lineItemIds")
    private_auction_deal_id: Optional[str] = Field(default=None, alias="privateAuctionDealId")
    deal_id: Optional[str] = Field(default=None, alias="dealId")  # OpenDirect deal reference
    status: Optional[str] = None  # "BOOKED", "FAILED", etc.
    message: Optional[str] = None
    error: Optional[str] = None
    gam_order_url: Optional[str] = Field(default=None, alias="gamOrderUrl")
    external_deal_id: Optional[str] = Field(default=None, alias="externalDealId")
