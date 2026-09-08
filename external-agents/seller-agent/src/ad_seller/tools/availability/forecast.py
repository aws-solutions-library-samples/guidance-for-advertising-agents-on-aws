# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Forecast Tool - Forecast inventory availability."""

from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field


class ForecastInput(BaseModel):
    """Input schema for inventory forecast."""

    product_id: str = Field(description="Product ID to forecast")
    period: str = Field(
        default="monthly",
        description="Forecast period: daily, weekly, monthly, quarterly",
    )


class ForecastTool(BaseTool):
    """Tool for forecasting inventory availability.

    Provides projected inventory availability based on
    historical patterns and current bookings.
    """

    name: str = "inventory_forecast"
    description: str = """Forecast inventory availability for a product.
    Returns projected impressions by period."""
    args_schema: Type[BaseModel] = ForecastInput

    def _run(
        self,
        product_id: str,
        period: str = "monthly",
    ) -> str:
        """Execute the forecast."""
        # Simplified forecast
        # In production, this would use historical data and ML models

        # Simulate forecast data
        if period == "monthly":
            forecast = """
Inventory Forecast for {product_id}:
Period: Monthly

| Month    | Projected Avails | Current Bookings | Net Available |
|----------|-----------------|------------------|---------------|
| Jan 2026 | 15,000,000      | 8,500,000        | 6,500,000     |
| Feb 2026 | 14,000,000      | 5,200,000        | 8,800,000     |
| Mar 2026 | 16,000,000      | 3,100,000        | 12,900,000    |
| Q1 Total | 45,000,000      | 16,800,000       | 28,200,000    |

Notes:
- Q1 historically shows 15% higher demand in March
- Current fill rate: 37%
- Target fill rate: 85%
"""
        else:
            forecast = f"""
Inventory Forecast for {product_id}:
Period: {period.title()}

Projected available impressions: 10,000,000 per {period}
Current booking rate: 40%
Confidence: Medium

Note: For detailed forecasts, use monthly period.
"""

        return forecast.format(product_id=product_id).strip()
