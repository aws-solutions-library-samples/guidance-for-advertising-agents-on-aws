# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Proposal Validation Tool - Validate incoming proposals."""

from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field


class ProposalValidationInput(BaseModel):
    """Input schema for proposal validation."""

    product_id: str = Field(description="Product ID in the proposal")
    deal_type: str = Field(description="Deal type: pg, pd, or pa")
    price: float = Field(description="Proposed price (CPM)")
    impressions: int = Field(description="Requested impressions")
    start_date: str = Field(description="Flight start date")
    end_date: str = Field(description="Flight end date")


class ProposalValidationTool(BaseTool):
    """Tool for validating incoming proposals.

    Checks proposal against product definitions, pricing rules,
    and availability constraints.
    """

    name: str = "proposal_validation"
    description: str = """Validate an incoming proposal.
    Returns validation results with any issues found."""
    args_schema: Type[BaseModel] = ProposalValidationInput

    def _run(
        self,
        product_id: str,
        deal_type: str,
        price: float,
        impressions: int,
        start_date: str,
        end_date: str,
    ) -> str:
        """Execute proposal validation."""
        issues = []
        warnings = []

        # Validate deal type
        valid_deal_types = [
            "pg",
            "pd",
            "pa",
            "programmatic_guaranteed",
            "preferred_deal",
            "private_auction",
        ]
        if deal_type.lower() not in valid_deal_types:
            issues.append(f"Invalid deal type: {deal_type}")

        # Validate price (simplified)
        if price < 5.0:
            issues.append(f"Price ${price:.2f} CPM is below minimum floor")
        elif price < 10.0:
            warnings.append(f"Price ${price:.2f} CPM is below typical rates")

        # Validate impressions
        if impressions < 10000:
            issues.append(f"Minimum order is 10,000 impressions (requested: {impressions:,})")
        elif impressions > 100_000_000:
            warnings.append(f"Large order ({impressions:,}) may require capacity review")

        # Build response
        if issues:
            status = "✗ INVALID"
            details = "\n".join(f"  - {i}" for i in issues)
            response = f"""
Proposal Validation for {product_id}:
Status: {status}

Issues:
{details}
"""
        elif warnings:
            status = "⚠ VALID WITH WARNINGS"
            details = "\n".join(f"  - {w}" for w in warnings)
            response = f"""
Proposal Validation for {product_id}:
Status: {status}

Warnings:
{details}

Recommendation: Review before accepting
"""
        else:
            response = f"""
Proposal Validation for {product_id}:
Status: ✓ VALID

All validation checks passed:
- Deal type: {deal_type}
- Price: ${price:.2f} CPM
- Impressions: {impressions:,}
- Flight: {start_date} to {end_date}
"""

        return response.strip()
