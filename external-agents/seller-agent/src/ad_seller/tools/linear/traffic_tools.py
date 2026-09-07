# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Linear TV Traffic & Operations Tools.

Three tools for linear TV order management:
- LinearOrderTool: Generate structured IO documents (TIP-compatible)
- AirtimeReportingTool: Retrieve airtime confirmation data
- LinearBillingReconciliationTool: Reconcile billing vs delivery

IO generation is functional now (produces a structured JSON artifact).
Submission and reporting are stubbed pending traffic system credentials.
TODO: Integrate with WideOrbit WO Traffic API for order submission
TODO: Integrate with WideOrbit WO Airtimes for airtime confirmations
TODO: Integrate with Mediaocean / Hudson MX for billing reconciliation
"""

import json
import uuid
from datetime import datetime
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

# =============================================================================
# LinearOrderTool — TIP-compatible IO generation
# =============================================================================


class LinearOrderInput(BaseModel):
    """Input for linear TV order/IO generation."""

    deal_id: str = Field(description="Linear deal ID")
    advertiser_name: str = Field(description="Advertiser name")
    agency_name: str = Field(default="", description="Agency name")
    networks: list[str] = Field(description="Networks in the order")
    dayparts: list[str] = Field(description="Dayparts in the order")
    flight_start: str = Field(description="Flight start date (YYYY-MM-DD)")
    flight_end: str = Field(description="Flight end date (YYYY-MM-DD)")
    total_spots: int = Field(description="Total spots ordered")
    total_value: float = Field(description="Total dollar value")
    demo: str = Field(default="A25-54", description="Target demographic")
    spot_length: int = Field(default=30, description="Spot length in seconds")


class LinearOrderTool(BaseTool):
    """Generate a structured IO document for linear TV orders.

    Produces a TIP-compatible JSON structure that can be submitted to
    traffic systems. The IO generation is functional; submission is
    stubbed pending WideOrbit/FreeWheel credentials.

    TODO: Submit to WideOrbit WO Traffic API
    TODO: Submit to FreeWheel for programmatic deals
    """

    name: str = "linear_order"
    description: str = """Generate a structured insertion order (IO) for linear TV.
    Returns TIP-compatible JSON that can be submitted to traffic systems."""
    args_schema: Type[BaseModel] = LinearOrderInput

    def _run(
        self,
        deal_id: str,
        advertiser_name: str,
        agency_name: str = "",
        networks: list[str] | None = None,
        dayparts: list[str] | None = None,
        flight_start: str = "",
        flight_end: str = "",
        total_spots: int = 0,
        total_value: float = 0.0,
        demo: str = "A25-54",
        spot_length: int = 30,
    ) -> str:
        networks = networks or []
        dayparts = dayparts or []

        order_id = f"IO-{uuid.uuid4().hex[:8].upper()}"

        # Build TIP-compatible order structure
        order = {
            "orderId": order_id,
            "dealReference": deal_id,
            "orderType": "national_spot",
            "status": "draft",
            "createdAt": datetime.utcnow().isoformat(),
            "advertiser": {
                "name": advertiser_name,
                "agency": agency_name or None,
            },
            "flightDates": {
                "startDate": flight_start,
                "endDate": flight_end,
            },
            "salesElements": [
                {
                    "salesElementId": f"SE-{uuid.uuid4().hex[:6].upper()}",
                    "network": network,
                    "daypart": daypart,
                    "spotLength": spot_length,
                    "demo": demo,
                    "spots": max(1, total_spots // (len(networks) * len(dayparts))),
                }
                for network in networks
                for daypart in dayparts
            ],
            "totalSpots": total_spots,
            "totalValue": total_value,
            "currency": "USD",
            "submissionStatus": "NOT_SUBMITTED",
            "submissionNote": "Pending traffic system integration (WideOrbit/FreeWheel)",
        }

        order_json = json.dumps(order, indent=2)

        return f"""
Linear TV Order Generated — {order_id}
Deal: {deal_id}
Advertiser: {advertiser_name}
Flight: {flight_start} to {flight_end}
Networks: {", ".join(networks)}
Dayparts: {", ".join(dayparts)}
Total Spots: {total_spots} × :{spot_length}
Total Value: ${total_value:,.2f}

Status: DRAFT (not yet submitted to traffic system)

TIP-Compatible Order JSON:
{order_json}
""".strip()


# =============================================================================
# AirtimeReportingTool — Airtime confirmations
# =============================================================================


class AirtimeReportingInput(BaseModel):
    """Input for airtime reporting."""

    order_id: str = Field(description="Order/IO ID to pull airtimes for")
    report_date: str = Field(default="", description="Report date (YYYY-MM-DD)")


class AirtimeReportingTool(BaseTool):
    """Retrieve airtime confirmation data for linear TV orders.

    Stub returns mock airtime data.

    TODO: Integrate with WideOrbit WO Airtimes API
    TODO: Integrate with FreeWheel delivery reporting
    """

    name: str = "airtime_reporting"
    description: str = """Retrieve airtime confirmation data for a linear TV order.
    Shows which spots actually aired and their delivery status."""
    args_schema: Type[BaseModel] = AirtimeReportingInput

    def _run(
        self,
        order_id: str,
        report_date: str = "",
    ) -> str:
        report_date = report_date or datetime.utcnow().strftime("%Y-%m-%d")

        return f"""
Airtime Report — {order_id}
Report Date: {report_date}

[STUB — Pending WideOrbit WO Airtimes integration]

Mock Airtime Summary:
  Ordered Spots: 120
  Aired Spots: 115
  Preempted: 5
  Delivery Rate: 95.8%

  By Network:
    NBC: 40/40 aired (100%)
    Bravo: 38/40 aired (95%) — 2 preempted
    USA: 37/40 aired (92.5%) — 3 preempted

  Preemption Details:
    - Bravo Wed 9:00 PM: Breaking news coverage
    - Bravo Thu 8:30 PM: Schedule change
    - USA Mon 10:00 PM: Live event overrun
    - USA Tue 9:00 PM: Technical issue
    - USA Wed 10:00 PM: Schedule change

  Makegood Required: 5 spots (audience underdelivery ~4.2%)
""".strip()


# =============================================================================
# LinearBillingReconciliationTool
# =============================================================================


class BillingReconciliationInput(BaseModel):
    """Input for billing reconciliation."""

    order_id: str = Field(description="Order/IO ID to reconcile")
    billing_period: str = Field(
        default="",
        description="Billing period (YYYY-MM)",
    )


class LinearBillingReconciliationTool(BaseTool):
    """Reconcile billing vs delivery for linear TV orders.

    Stub returns mock reconciliation report.

    TODO: Integrate with Mediaocean for agency billing reconciliation
    TODO: Integrate with Hudson MX for modern billing workflows
    """

    name: str = "linear_billing_reconciliation"
    description: str = """Reconcile billing against delivery for a linear TV order.
    Compares ordered vs delivered vs billed amounts."""
    args_schema: Type[BaseModel] = BillingReconciliationInput

    def _run(
        self,
        order_id: str,
        billing_period: str = "",
    ) -> str:
        billing_period = billing_period or datetime.utcnow().strftime("%Y-%m")

        return f"""
Billing Reconciliation — {order_id}
Period: {billing_period}

[STUB — Pending Mediaocean / Hudson MX integration]

Mock Reconciliation:
  Ordered Value: $850,000.00
  Delivered Value: $815,000.00
  Billed Amount: $815,000.00
  Variance: -$35,000.00 (4.1% under-delivery)

  Breakdown:
    NBC Primetime: $400,000 ordered / $395,000 delivered ✓
    Bravo Cable: $250,000 ordered / $235,000 delivered (6% under)
    USA Cable: $200,000 ordered / $185,000 delivered (7.5% under)

  Makegood Credit: $35,000 (pending resolution)
  Net Billable: $815,000.00

  Status: PENDING MAKEGOOD RESOLUTION
""".strip()
