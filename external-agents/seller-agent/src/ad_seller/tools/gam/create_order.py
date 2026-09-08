# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Create GAM Order Tool - Create orders in Google Ad Manager."""

from typing import Optional, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...config import get_settings


class CreateGAMOrderInput(BaseModel):
    """Input schema for GAM order creation."""

    name: str = Field(
        description="Order name (typically campaign name)",
    )
    advertiser_name: str = Field(
        description="Advertiser company name (will be looked up or created)",
    )
    agency_name: Optional[str] = Field(
        default=None,
        description="Agency name if applicable",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Order notes (e.g., OpenDirect proposal reference)",
    )
    external_order_id: Optional[str] = Field(
        default=None,
        description="External reference ID (e.g., OpenDirect execution order ID)",
    )


class CreateGAMOrderTool(BaseTool):
    """Tool for creating orders in Google Ad Manager.

    Creates a new order (container for line items) with the specified
    advertiser. Used for Programmatic Guaranteed and Preferred Deal booking.
    """

    name: str = "create_gam_order"
    description: str = """Create a new order in Google Ad Manager.
    Orders are containers for line items. Returns the created order ID."""
    args_schema: Type[BaseModel] = CreateGAMOrderInput

    def _run(
        self,
        name: str,
        advertiser_name: str,
        agency_name: Optional[str] = None,
        notes: Optional[str] = None,
        external_order_id: Optional[str] = None,
    ) -> str:
        """Execute order creation."""
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

            client = GAMSoapClient(
                network_code=settings.gam_network_code,
                credentials_path=settings.gam_json_key_path,
            )
            client.connect()

            try:
                # Get or create advertiser company
                advertiser_id = client.get_or_create_advertiser(advertiser_name)

                # Create the order
                order = client.create_order(
                    name=name,
                    advertiser_id=advertiser_id,
                    notes=notes,
                    external_order_id=external_order_id,
                    is_programmatic=True,
                )

                # Format response
                lines = [
                    "Order created successfully:\n",
                    f"- Order ID: {order.id}",
                    f"- Name: {order.name}",
                    f"- Advertiser ID: {order.advertiser_id}",
                    f"- Status: {order.status.value}",
                ]

                if order.external_order_id:
                    lines.append(f"- External ID: {order.external_order_id}")

                lines.append(
                    f"\nNext step: Create line items using create_gam_line_item "
                    f"with order_id={order.id}"
                )

                return "\n".join(lines)

            finally:
                client.disconnect()

        except ImportError as e:
            return f"GAM SOAP client dependencies not installed: {e}"
        except ValueError as e:
            return f"Configuration error: {e}"
        except Exception as e:
            return f"Error creating order: {e}"
