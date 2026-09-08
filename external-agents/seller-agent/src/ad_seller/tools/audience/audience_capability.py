# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Audience Capability Tool - Report audience capabilities for products."""

from typing import Optional, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...models.ucp import AudienceCapability, SignalType


class AudienceCapabilityInput(BaseModel):
    """Input schema for audience capability tool."""

    product_id: str = Field(description="Product ID to report capabilities for")
    inventory_type: Optional[str] = Field(
        default=None,
        description="Inventory type: display, video, ctv, mobile_app, native",
    )


class AudienceCapabilityTool(BaseTool):
    """Report audience capabilities for products.

    This tool generates a capability report for a product, showing
    available audience signals, coverage, and UCP compatibility.
    """

    name: str = "report_audience_capabilities"
    description: str = """Generate an audience capability report for a product.
    Returns available signal types (identity, contextual, reinforcement),
    coverage percentages, and UCP compatibility. Use this to understand
    what audience targeting is available for inventory."""
    args_schema: Type[BaseModel] = AudienceCapabilityInput

    def _run(
        self,
        product_id: str,
        inventory_type: Optional[str] = None,
    ) -> str:
        """Execute the capability report."""
        capabilities = self._get_capabilities(product_id, inventory_type)
        return self._format_report(capabilities, product_id, inventory_type)

    def _get_capabilities(
        self,
        product_id: str,
        inventory_type: Optional[str],
    ) -> list[AudienceCapability]:
        """Get capabilities for a product."""
        # Base capabilities available for all inventory
        capabilities = [
            AudienceCapability(
                capability_id=f"{product_id}_geo",
                name="Geographic Targeting",
                description="Country, region, DMA, city, zip targeting",
                signal_type=SignalType.CONTEXTUAL,
                coverage_percentage=98.0,
                available_segments=["country", "region", "dma", "city", "zip"],
                taxonomy="geo-standard",
                ucp_compatible=True,
                embedding_dimension=512,
            ),
            AudienceCapability(
                capability_id=f"{product_id}_device",
                name="Device Targeting",
                description="Device type, OS, browser targeting",
                signal_type=SignalType.CONTEXTUAL,
                coverage_percentage=99.0,
                available_segments=["desktop", "mobile", "tablet", "ctv"],
                taxonomy="device-standard",
                ucp_compatible=True,
                embedding_dimension=512,
            ),
            AudienceCapability(
                capability_id=f"{product_id}_content",
                name="Content Categories",
                description="IAB content category targeting",
                signal_type=SignalType.CONTEXTUAL,
                coverage_percentage=95.0,
                available_segments=["IAB1", "IAB2", "IAB3", "IAB4", "IAB5", "IAB6"],
                taxonomy="IAB-2.2",
                ucp_compatible=True,
                embedding_dimension=512,
            ),
            AudienceCapability(
                capability_id=f"{product_id}_keywords",
                name="Keyword Targeting",
                description="Page keyword and topic targeting",
                signal_type=SignalType.CONTEXTUAL,
                coverage_percentage=90.0,
                available_segments=[],
                taxonomy="custom",
                ucp_compatible=True,
                embedding_dimension=512,
            ),
        ]

        # Demographics (identity signals)
        capabilities.append(
            AudienceCapability(
                capability_id=f"{product_id}_demo_age",
                name="Age Demographics",
                description="Modeled age-based targeting",
                signal_type=SignalType.IDENTITY,
                coverage_percentage=72.0,
                available_segments=["18-24", "25-34", "35-44", "45-54", "55-64", "65+"],
                taxonomy="demo-standard",
                minimum_match_rate=0.5,
                ucp_compatible=True,
                embedding_dimension=512,
            )
        )
        capabilities.append(
            AudienceCapability(
                capability_id=f"{product_id}_demo_gender",
                name="Gender Demographics",
                description="Modeled gender targeting",
                signal_type=SignalType.IDENTITY,
                coverage_percentage=68.0,
                available_segments=["male", "female"],
                taxonomy="demo-standard",
                minimum_match_rate=0.5,
                ucp_compatible=True,
                embedding_dimension=512,
            )
        )
        capabilities.append(
            AudienceCapability(
                capability_id=f"{product_id}_demo_income",
                name="Household Income",
                description="Modeled HHI targeting",
                signal_type=SignalType.IDENTITY,
                coverage_percentage=55.0,
                available_segments=["<50k", "50k-75k", "75k-100k", "100k-150k", "150k+"],
                taxonomy="demo-standard",
                minimum_match_rate=0.4,
                ucp_compatible=True,
                embedding_dimension=512,
            )
        )

        # Behavioral/Intent signals
        capabilities.append(
            AudienceCapability(
                capability_id=f"{product_id}_intent",
                name="In-Market Intent",
                description="Purchase intent signals",
                signal_type=SignalType.REINFORCEMENT,
                coverage_percentage=35.0,
                available_segments=[
                    "auto-buyers",
                    "travel-intenders",
                    "finance-seekers",
                    "retail-shoppers",
                ],
                taxonomy="intent-standard",
                minimum_match_rate=0.3,
                ucp_compatible=True,
                embedding_dimension=512,
            )
        )
        capabilities.append(
            AudienceCapability(
                capability_id=f"{product_id}_retargeting",
                name="Retargeting Pools",
                description="Advertiser retargeting audiences",
                signal_type=SignalType.REINFORCEMENT,
                coverage_percentage=20.0,
                available_segments=["site-visitors", "cart-abandoners", "past-buyers"],
                taxonomy="custom",
                minimum_match_rate=0.2,
                ucp_compatible=True,
                embedding_dimension=512,
            )
        )

        # Channel-specific capabilities
        if inventory_type == "ctv":
            capabilities.append(
                AudienceCapability(
                    capability_id=f"{product_id}_acr",
                    name="ACR Data",
                    description="Automatic content recognition for CTV",
                    signal_type=SignalType.CONTEXTUAL,
                    coverage_percentage=45.0,
                    available_segments=["genre", "network", "show"],
                    taxonomy="acr-standard",
                    ucp_compatible=True,
                    embedding_dimension=512,
                )
            )
        elif inventory_type == "mobile_app":
            capabilities.append(
                AudienceCapability(
                    capability_id=f"{product_id}_maid",
                    name="Mobile Ad IDs",
                    description="IDFA/GAID based targeting",
                    signal_type=SignalType.IDENTITY,
                    coverage_percentage=40.0,  # Lower due to ATT
                    available_segments=["idfa", "gaid"],
                    taxonomy="device-id",
                    ucp_compatible=True,
                    embedding_dimension=512,
                )
            )

        return capabilities

    def _format_report(
        self,
        capabilities: list[AudienceCapability],
        product_id: str,
        inventory_type: Optional[str],
    ) -> str:
        """Format capabilities as a report."""
        output = f"## Audience Capabilities for {product_id}\n"
        if inventory_type:
            output += f"Inventory Type: {inventory_type}\n"
        output += "\n"

        # Group by signal type
        by_signal: dict[str, list[AudienceCapability]] = {}
        for cap in capabilities:
            signal = cap.signal_type.value
            if signal not in by_signal:
                by_signal[signal] = []
            by_signal[signal].append(cap)

        # Signal type order
        signal_order = ["contextual", "identity", "reinforcement"]

        for signal in signal_order:
            if signal not in by_signal:
                continue

            caps = by_signal[signal]
            output += f"### {signal.upper()} SIGNALS\n\n"

            for cap in sorted(caps, key=lambda x: -x.coverage_percentage):
                ucp_badge = "[UCP]" if cap.ucp_compatible else "[NO-UCP]"
                output += f"**{cap.name}** {ucp_badge}\n"
                output += f"   ID: {cap.capability_id}\n"
                output += f"   Coverage: {cap.coverage_percentage:.0f}%\n"

                if cap.description:
                    output += f"   Description: {cap.description}\n"

                if cap.available_segments:
                    segs = cap.available_segments[:6]
                    more = len(cap.available_segments) - 6
                    output += f"   Segments: {', '.join(segs)}"
                    if more > 0:
                        output += f" (+{more} more)"
                    output += "\n"

                if cap.minimum_match_rate > 0:
                    output += f"   Min Match Rate: {cap.minimum_match_rate:.0%}\n"

                output += "\n"

        # Summary
        total = len(capabilities)
        ucp_count = sum(1 for c in capabilities if c.ucp_compatible)
        avg_coverage = sum(c.coverage_percentage for c in capabilities) / total if total else 0

        output += "---\n"
        output += f"**Summary:** {total} capabilities, "
        output += f"{ucp_count} UCP-compatible, "
        output += f"avg coverage {avg_coverage:.0f}%\n\n"

        # Signal type coverage
        output += "**Coverage by Signal Type:**\n"
        for signal in signal_order:
            if signal in by_signal:
                caps = by_signal[signal]
                max_cov = max(c.coverage_percentage for c in caps)
                output += f"   {signal}: up to {max_cov:.0f}%\n"

        return output
