# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Linear TV data models for broadcast, cable, MVPD, and programmatic linear.

Standards alignment:
- TIP v5.1.0: Daypart time fields (TimeWindow), MakegoodTerms (MakegoodGuideline),
  CPP/CPM pricing (PricingMetricOption)
- OpenRTB 2.6: network_name/network_domain (Channel), network_group (Network),
  programmatic_deal_types (Deal dtype: pg=3, pmp=2, preferred=1)
- IAB sellers.json: seller_type (DIRECT/RESELLER/BOTH)
- Nielsen: DMA codes (210 proprietary market identifiers)
"""

import re
import uuid
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field, computed_field, field_validator

from ..constants.dma_codes import DMA_CODES
from .core import DealType, PricingModel
from .flow_state import ProductDefinition

# =============================================================================
# Type Aliases
# =============================================================================

DaypartName = Literal[
    "early_morning",
    "daytime",
    "early_fringe",
    "prime_access",
    "primetime",
    "late_news",
    "late_fringe",
    "overnight",
    "weekend",
]

MediumType = Literal["broadcast", "cable", "syndication", "mvpd_avail"]
CoverageType = Literal["national", "regional", "local"]
SellerType = Literal["DIRECT", "RESELLER", "BOTH"]
MarketType = Literal["upfront", "scatter", "sponsorship", "package"]
ProgrammaticDealType = Literal["pg", "pmp", "preferred"]
MeasurementCurrency = Literal[
    "nielsen_c3",
    "nielsen_c7",
    "nielsen_one",
    "videoamp",
    "ispot",
    "comscore",
    "impression",
    "grp",
    "multi",
]
AddressableType = Literal["mvpd_stb", "acr", "both"]
BuyerType = Literal["holding_company", "independent_agency", "brand_direct", "dsp"]
HoldingCompany = Literal[
    "wpp",
    "omnicom",
    "publicis",
    "ipg",
    "dentsu",
    "havas",
    "independent",
]
DealStatus = Literal[
    "proposed",
    "negotiating",
    "executed",
    "active",
    "completed",
    "cancelled",
    "makegood_pending",
]

# Demo format regex: A25-54, W18-49, M25-54, P2+, HH
DEMO_PATTERN = re.compile(r"^(A|W|M|P)\d{1,2}-\d{2,3}$|^(A|W|M|P)\d{1,2}\+$|^HH$")


# =============================================================================
# Daypart — TIP v5.1.0 TimeWindow compatible
# =============================================================================


class Daypart(BaseModel):
    """Named daypart with TIP-compatible time range fields.

    Time fields follow TIP v5.1.0 TimeWindow pattern (HH:MM:SS military format).
    Days of week follow TIP DayOfWeek structure.
    """

    name: DaypartName
    start_time: str = Field(description="HH:MM:SS military format (TIP TimeWindow)")
    end_time: str = Field(description="HH:MM:SS military format (TIP TimeWindow)")
    days_of_week: list[Literal["M", "T", "W", "Th", "F", "Sa", "Su"]] = Field(
        default_factory=lambda: ["M", "T", "W", "Th", "F"],
    )
    available_units: int = 0
    sold_units: int = 0
    base_rate_cpp: Decimal = Decimal("0")
    base_rate_cpm: Decimal = Decimal("0")

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time_format(cls, v: str) -> str:
        if not re.match(r"^\d{2}:\d{2}:\d{2}$", v):
            raise ValueError(f"Time must be HH:MM:SS format, got '{v}'")
        parts = v.split(":")
        h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
        if not (0 <= h <= 23 and 0 <= m <= 59 and 0 <= s <= 59):
            raise ValueError(f"Invalid time value: {v}")
        return v

    @computed_field
    @property
    def sellthrough_pct(self) -> float:
        if self.available_units == 0:
            return 0.0
        return round((self.sold_units / self.available_units) * 100, 1)


# =============================================================================
# MakegoodTerms — TIP v5.1.0 MakegoodGuideline compliant
# =============================================================================


class MakegoodTerms(BaseModel):
    """Structured makegood terms per TIP v5.1.0 MakegoodGuideline schema.

    Fields map to TIP makegoodsSchemas.yaml:
    - makegood_type → TIP makegoodType
    - sales_element_equivalent → TIP salesElementEquivalent
    - makegood_window → TIP makegoodWindow
    """

    makegood_type: Literal["resolve_preemption", "audience_underdelivery"] = Field(
        description="TIP makegoodType",
    )
    sales_element_equivalent: Literal["same_sales_element", "alternate_sales_element"] = Field(
        description="TIP salesElementEquivalent",
    )
    makegood_window: Literal[
        "original_broadcast_week", "original_broadcast_month", "within_flight_dates"
    ] = Field(description="TIP makegoodWindow")
    makegood_ratio: Optional[int] = Field(
        default=None,
        description="Max makegood spots allowed (TIP)",
    )
    audience_limit_pct: Optional[float] = Field(
        default=None,
        description="% of original primary audience (TIP)",
    )
    external_comment: Optional[str] = None


# =============================================================================
# SupplyPoolEntry — for reseller aggregated supply
# =============================================================================


class SupplyPoolEntry(BaseModel):
    """Contributing network/product in a reseller's aggregated supply pool."""

    source_network: str
    source_product_id: Optional[str] = None
    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    floor_cpm: Optional[Decimal] = None


# =============================================================================
# LinearTVProduct — Composition wrapping ProductDefinition
# =============================================================================


class LinearTVProduct(BaseModel):
    """Linear TV product extending the core ProductDefinition.

    Composition pattern: wraps a ProductDefinition and adds linear-TV-specific
    fields for broadcast, cable, MVPD, and programmatic linear inventory.

    Standards alignment:
    - medium: no IAB standard; TIP mediaOutletType maps partially
    - network_name/network_domain: OpenRTB 2.6 Channel object
    - network_group: OpenRTB 2.6 Network object
    - seller_type: IAB sellers.json (DIRECT/RESELLER/BOTH)
    - programmatic_deal_types: OpenRTB 2.6 Deal dtype (pg=3, pmp=2, preferred=1)
    - daypart times: TIP v5.1.0 TimeWindow
    - CPP/CPM: TIP PricingMetricOption
    - dma_codes: Nielsen proprietary (de facto standard)
    """

    product_id: str = Field(default_factory=lambda: f"ltv-{uuid.uuid4().hex[:8]}")
    name: str
    description: Optional[str] = None

    # Linear TV classification
    medium: MediumType
    network_name: str = Field(description="OpenRTB 2.6 Channel name")
    network_domain: Optional[str] = Field(
        default=None,
        description="OpenRTB 2.6 Channel domain for disambiguation",
    )
    network_group: str = Field(description="OpenRTB 2.6 Network name (parent group)")
    station_group: Optional[str] = Field(
        default=None,
        description="Local broadcast station group (Nexstar, Sinclair, etc.)",
    )

    # Seller classification — IAB sellers.json
    seller_type: SellerType = "DIRECT"

    # Geographic
    coverage: CoverageType = "national"
    dma_codes: list[int] = Field(default_factory=list)
    dma_names: list[str] = Field(default_factory=list)

    # Dayparts
    dayparts: list[Daypart] = Field(default_factory=list)

    # Demographics
    primary_demo: str = Field(description="Target demo e.g. A25-54, W18-49, HH")
    secondary_demos: list[str] = Field(default_factory=list)

    # Pricing — TIP PricingMetricOption (CPP and CPM both recognized)
    rate_card_cpp: Decimal = Decimal("0")
    rate_card_cpm: Decimal = Decimal("0")
    floor_cpp: Decimal = Decimal("0")
    floor_cpm: Decimal = Decimal("0")
    upfront_rate_cpp: Optional[Decimal] = None
    scatter_rate_multiplier: float = Field(
        default=1.15, description="e.g. 1.15 = 15% scatter premium"
    )

    # Measurement
    measurement_currency: MeasurementCurrency = "nielsen_c3"

    # Addressable TV — Go Addressable guidelines
    addressable_enabled: bool = False
    addressable_type: Optional[AddressableType] = None
    addressable_hh_count: Optional[int] = None

    # Programmatic — OpenRTB 2.6
    programmatic_enabled: bool = False
    market_types: list[MarketType] = Field(default_factory=lambda: ["scatter"])
    programmatic_deal_types: list[ProgrammaticDealType] = Field(default_factory=list)

    # Traffic/ad server
    traffic_system: Optional[str] = None
    ad_server: Optional[str] = None

    # Reseller supply pool
    supply_pool: Optional[list[SupplyPoolEntry]] = None

    # Cross-platform
    companion_product_ids: Optional[list[str]] = None

    @field_validator("primary_demo")
    @classmethod
    def validate_demo_format(cls, v: str) -> str:
        if not DEMO_PATTERN.match(v):
            raise ValueError(f"Demo must match format like A25-54, W18-49, M25+, HH. Got: '{v}'")
        return v

    @field_validator("secondary_demos")
    @classmethod
    def validate_secondary_demos(cls, v: list[str]) -> list[str]:
        for demo in v:
            if not DEMO_PATTERN.match(demo):
                raise ValueError(f"Invalid demo format: '{demo}'")
        return v

    @field_validator("dma_codes")
    @classmethod
    def validate_dma_codes(cls, v: list[int]) -> list[int]:
        invalid = [c for c in v if c not in DMA_CODES]
        if invalid:
            raise ValueError(f"Invalid DMA codes: {invalid}")
        return v

    def to_product_definition(self) -> ProductDefinition:
        """Adapt to core ProductDefinition for compatibility with existing system."""
        deal_types = []
        for pdt in self.programmatic_deal_types:
            if pdt == "pg":
                deal_types.append(DealType.PROGRAMMATIC_GUARANTEED)
            elif pdt == "pmp":
                deal_types.append(DealType.PRIVATE_AUCTION)
            elif pdt == "preferred":
                deal_types.append(DealType.PREFERRED_DEAL)
        if not deal_types:
            deal_types = [DealType.PROGRAMMATIC_GUARANTEED]

        return ProductDefinition(
            product_id=self.product_id,
            name=self.name,
            description=self.description,
            inventory_type="linear_tv",
            supported_deal_types=deal_types,
            supported_pricing_models=[PricingModel.CPM],
            base_cpm=float(self.rate_card_cpm),
            floor_cpm=float(self.floor_cpm),
        )


# =============================================================================
# LinearDeal — Negotiated/proposed deal
# =============================================================================


class LinearDeal(BaseModel):
    """Negotiated linear TV deal.

    Supports dual currency (CPP + CPM) and maps to both traditional
    and programmatic buying workflows.

    Standards:
    - market_type: no standard; linear TV market structure
    - programmatic_deal_type: OpenRTB 2.6 Deal dtype (pg=3, pmp=2, preferred=1)
    - makegood_terms: TIP v5.1.0 MakegoodGuideline compliant
    """

    deal_id: str = Field(default_factory=lambda: f"ldeal-{uuid.uuid4().hex[:8]}")

    # Market classification
    market_type: MarketType
    programmatic_deal_type: Optional[ProgrammaticDealType] = Field(
        default=None,
        description="OpenRTB 2.6 Deal dtype. Only for programmatic-enabled deals.",
    )

    # Buyer
    buyer_type: BuyerType
    holding_company: Optional[HoldingCompany] = None
    buyer_name: Optional[str] = None
    agency_name: Optional[str] = None

    # Inventory selection
    networks: list[str] = Field(default_factory=list)
    dayparts: list[DaypartName] = Field(default_factory=list)
    dma_codes: list[int] = Field(default_factory=list)

    # Dual currency delivery — traditional + modern
    guaranteed_grps: Optional[float] = None
    guaranteed_impressions: Optional[int] = None
    negotiated_cpp: Optional[Decimal] = None
    negotiated_cpm: Optional[Decimal] = None

    # Deal economics
    total_value: Optional[Decimal] = None
    cancellation_window_days: int = 0
    rate_of_change_pct: Optional[float] = Field(
        default=None,
        description="Season-over-season CPM/CPP change %. Upfront-specific.",
    )

    # Makegood — TIP v5.1.0 MakegoodGuideline
    makegood_terms: Optional[MakegoodTerms] = None

    # Measurement
    measurement_currency: MeasurementCurrency = "nielsen_c3"
    reporting_frequency: Literal["daily", "weekly", "monthly", "quarterly"] = "weekly"

    # Status
    status: DealStatus = "proposed"

    # Flight
    start_date: Optional[str] = None
    end_date: Optional[str] = None

    # External linkage
    freewheel_deal_id: Optional[str] = Field(
        default=None,
        description="Links to FreeWheel when 1A integration is active",
    )
    product_ids: list[str] = Field(default_factory=list)

    @field_validator("dma_codes")
    @classmethod
    def validate_dma_codes(cls, v: list[int]) -> list[int]:
        invalid = [c for c in v if c not in DMA_CODES]
        if invalid:
            raise ValueError(f"Invalid DMA codes: {invalid}")
        return v
