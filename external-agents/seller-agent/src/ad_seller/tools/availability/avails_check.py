# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Avails Check Tool - Check inventory availability."""

from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field


class AvailsCheckInput(BaseModel):
    """Input schema for availability check."""

    product_id: str = Field(description="Product ID to check availability for")
    start_date: str = Field(description="Flight start date (YYYY-MM-DD)")
    end_date: str = Field(description="Flight end date (YYYY-MM-DD)")
    requested_impressions: int = Field(description="Number of impressions requested")


class AvailsCheckTool(BaseTool):
    """Tool for checking inventory availability.

    Checks if the requested impressions are available for the
    specified product and flight dates.
    """

    name: str = "avails_check"
    description: str = """Check inventory availability for a product.
    Returns whether the requested impressions can be delivered."""
    args_schema: Type[BaseModel] = AvailsCheckInput

    def _run(
        self,
        product_id: str,
        start_date: str,
        end_date: str,
        requested_impressions: int,
    ) -> str:
        """Execute the availability check."""
        # Simplified availability check
        # In production, this would query the ad server or avails system

        # Simulate available inventory (placeholder)
        available = 10_000_000  # 10M impressions available

        if requested_impressions <= available:
            fill_pct = (requested_impressions / available) * 100
            return f"""
Availability Check for {product_id}:
- Flight: {start_date} to {end_date}
- Requested: {requested_impressions:,} impressions
- Available: {available:,} impressions
- Status: ✓ AVAILABLE
- Fill Impact: {fill_pct:.1f}% of available inventory
- Confidence: High
""".strip()
        else:
            return f"""
Availability Check for {product_id}:
- Flight: {start_date} to {end_date}
- Requested: {requested_impressions:,} impressions
- Available: {available:,} impressions
- Status: ✗ INSUFFICIENT INVENTORY
- Recommendation: Reduce to {available:,} impressions or extend flight dates
""".strip()
