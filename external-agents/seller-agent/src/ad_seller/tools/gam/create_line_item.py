# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Create GAM Line Item Tool - Create line items in Google Ad Manager."""

from datetime import datetime
from typing import Optional, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...config import get_settings
from ...models.gam import (
    GAMAdUnitTargeting,
    GAMCostType,
    GAMGoal,
    GAMGoalType,
    GAMInventoryTargeting,
    GAMLineItemType,
    GAMMoney,
    GAMTargeting,
    GAMUnitType,
)


class CreateGAMLineItemInput(BaseModel):
    """Input schema for GAM line item creation."""

    order_id: str = Field(
        description="The GAM order ID to create the line item under",
    )
    name: str = Field(
        description="Line item name",
    )
    ad_unit_ids: list[str] = Field(
        description="List of GAM ad unit IDs to target",
    )
    impressions: int = Field(
        description="Goal number of impressions to deliver",
    )
    cpm_rate: float = Field(
        description="CPM rate in dollars (e.g., 15.00 for $15 CPM)",
    )
    start_date: str = Field(
        description="Start date in YYYY-MM-DD format",
    )
    end_date: str = Field(
        description="End date in YYYY-MM-DD format",
    )
    line_item_type: str = Field(
        default="STANDARD",
        description="Line item type: SPONSORSHIP, STANDARD, or PREFERRED_DEAL",
    )
    currency: str = Field(
        default="USD",
        description="Currency code (ISO 4217)",
    )
    audience_segment_ids: Optional[list[int]] = Field(
        default=None,
        description="GAM audience segment IDs for targeting",
    )
    external_id: Optional[str] = Field(
        default=None,
        description="External reference ID (e.g., OpenDirect proposal line ID)",
    )


class CreateGAMLineItemTool(BaseTool):
    """Tool for creating line items in Google Ad Manager.

    Creates a line item within an existing order. Used for
    Programmatic Guaranteed and Preferred Deal booking.
    """

    name: str = "create_gam_line_item"
    description: str = """Create a line item in a Google Ad Manager order.
    Line items define targeting, pricing, and delivery goals."""
    args_schema: Type[BaseModel] = CreateGAMLineItemInput

    def _run(
        self,
        order_id: str,
        name: str,
        ad_unit_ids: list[str],
        impressions: int,
        cpm_rate: float,
        start_date: str,
        end_date: str,
        line_item_type: str = "STANDARD",
        currency: str = "USD",
        audience_segment_ids: Optional[list[int]] = None,
        external_id: Optional[str] = None,
    ) -> str:
        """Execute line item creation."""
        settings = get_settings()

        if not settings.gam_enabled:
            return "GAM integration is not enabled. Set GAM_ENABLED=true in your environment."

        if not settings.gam_network_code or not settings.gam_json_key_path:
            return (
                "GAM credentials not configured. Set GAM_NETWORK_CODE and "
                "GAM_JSON_KEY_PATH environment variables."
            )

        try:
            from ...clients import GAMSoapClient

            # Parse dates
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")

            # Map line item type
            type_map = {
                "SPONSORSHIP": GAMLineItemType.SPONSORSHIP,
                "STANDARD": GAMLineItemType.STANDARD,
                "PREFERRED_DEAL": GAMLineItemType.PREFERRED_DEAL,
                "NETWORK": GAMLineItemType.NETWORK,
                "PRICE_PRIORITY": GAMLineItemType.PRICE_PRIORITY,
            }
            li_type = type_map.get(line_item_type.upper(), GAMLineItemType.STANDARD)

            # Build targeting
            inventory_targeting = GAMInventoryTargeting(
                targeted_ad_units=[
                    GAMAdUnitTargeting(ad_unit_id=uid, include_descendants=True)
                    for uid in ad_unit_ids
                ]
            )

            targeting = GAMTargeting(inventory_targeting=inventory_targeting)

            # Add audience targeting if provided
            if audience_segment_ids:
                from ...models.gam import GAMAudienceSegmentCriteria, GAMCustomCriteriaSet

                audience_criteria = GAMAudienceSegmentCriteria(
                    operator="IS",
                    audience_segment_ids=audience_segment_ids,
                )
                targeting.custom_targeting = GAMCustomCriteriaSet(
                    logical_operator="AND",
                    children=[audience_criteria],
                )

            # Build cost
            cost_per_unit = GAMMoney(
                currency_code=currency,
                micro_amount=int(cpm_rate * 1_000_000),
            )

            # Build goal
            goal = GAMGoal(
                goal_type=GAMGoalType.LIFETIME,
                unit_type=GAMUnitType.IMPRESSIONS,
                units=impressions,
            )

            client = GAMSoapClient(
                network_code=settings.gam_network_code,
                credentials_path=settings.gam_json_key_path,
            )
            client.connect()

            try:
                line_item = client.create_line_item(
                    order_id=order_id,
                    name=name,
                    line_item_type=li_type,
                    targeting=targeting,
                    cost_per_unit=cost_per_unit,
                    goal=goal,
                    start_time=start_dt,
                    end_time=end_dt,
                    cost_type=GAMCostType.CPM,
                    external_id=external_id,
                )

                # Format response
                lines = [
                    "Line item created successfully:\n",
                    f"- Line Item ID: {line_item.id}",
                    f"- Name: {line_item.name}",
                    f"- Order ID: {line_item.order_id}",
                    f"- Type: {line_item.line_item_type.value}",
                    f"- Status: {line_item.status.value}",
                    f"- Cost: ${cpm_rate:.2f} {currency} CPM",
                    f"- Goal: {impressions:,} impressions",
                    f"- Flight: {start_date} to {end_date}",
                    f"- Targeted ad units: {len(ad_unit_ids)}",
                ]

                if audience_segment_ids:
                    lines.append(f"- Audience segments: {len(audience_segment_ids)}")

                if line_item.external_id:
                    lines.append(f"- External ID: {line_item.external_id}")

                return "\n".join(lines)

            finally:
                client.disconnect()

        except ImportError as e:
            return f"GAM SOAP client dependencies not installed: {e}"
        except ValueError as e:
            return f"Input error: {e}"
        except Exception as e:
            return f"Error creating line item: {e}"
