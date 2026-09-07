# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Counter Proposal Tool - Generate counter-proposal terms."""

from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field


class CounterProposalInput(BaseModel):
    """Input schema for counter proposal generation."""

    original_price: float = Field(description="Original proposed price (CPM)")
    recommended_price: float = Field(description="Recommended/target price (CPM)")
    original_impressions: int = Field(description="Original requested impressions")
    available_impressions: int = Field(description="Actually available impressions")
    reason: str = Field(description="Reason for counter (price, availability, etc.)")


class CounterProposalTool(BaseTool):
    """Tool for generating counter-proposal terms.

    Creates a counter-proposal with adjusted terms that work
    for both buyer and seller.
    """

    name: str = "counter_proposal"
    description: str = """Generate counter-proposal terms.
    Returns suggested counter terms with rationale."""
    args_schema: Type[BaseModel] = CounterProposalInput

    def _run(
        self,
        original_price: float,
        recommended_price: float,
        original_impressions: int,
        available_impressions: int,
        reason: str,
    ) -> str:
        """Generate counter proposal."""
        counter_terms = []
        rationale = []

        # Price counter
        if original_price < recommended_price:
            price_gap = recommended_price - original_price
            # Offer a compromise
            counter_price = original_price + (price_gap * 0.7)
            counter_terms.append(f"Price: ${counter_price:.2f} CPM (from ${original_price:.2f})")
            rationale.append("Price adjustment to meet minimum yield requirements")

        # Impressions counter
        if original_impressions > available_impressions:
            counter_terms.append(
                f"Impressions: {available_impressions:,} (from {original_impressions:,})"
            )
            rationale.append("Reduced to match available inventory")

        # Add sweetener
        sweeteners = []
        if counter_terms:
            sweeteners.append("Include premium positioning at no extra cost")
            sweeteners.append("Priority delivery guarantee")

        response = f"""
Counter-Proposal Terms:

Original Request:
- Price: ${original_price:.2f} CPM
- Impressions: {original_impressions:,}

Counter Terms:
"""
        for term in counter_terms:
            response += f"- {term}\n"

        response += """
Rationale:
"""
        for r in rationale:
            response += f"- {r}\n"

        if sweeteners:
            response += """
Value Additions:
"""
            for s in sweeteners:
                response += f"- {s}\n"

        response += f"""
Note: {reason}
"""

        return response.strip()
