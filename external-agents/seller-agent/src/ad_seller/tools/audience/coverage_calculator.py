# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Coverage Calculator Tool - Calculate audience coverage for targeting."""

from typing import Any, Optional, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field


class CoverageCalculatorInput(BaseModel):
    """Input schema for coverage calculator tool."""

    targeting: dict[str, Any] = Field(
        description="Targeting specification to calculate coverage for"
    )
    product_id: str = Field(description="Product ID to calculate coverage against")
    total_inventory: Optional[int] = Field(
        default=10000000,
        ge=0,
        description="Total available inventory impressions",
    )


class CoverageCalculatorTool(BaseTool):
    """Calculate audience coverage for targeting combinations.

    This tool calculates the expected coverage and reach for a
    targeting specification against product inventory.
    """

    name: str = "calculate_audience_coverage"
    description: str = """Calculate audience coverage for a targeting specification.
    Returns estimated impressions, coverage percentage, confidence level,
    and limiting factors. Use this to validate that a deal's targeting
    can deliver the requested impressions."""
    args_schema: Type[BaseModel] = CoverageCalculatorInput

    def _run(
        self,
        targeting: dict[str, Any],
        product_id: str,
        total_inventory: Optional[int] = 10000000,
    ) -> str:
        """Execute the coverage calculation."""
        if not targeting:
            return "Error: No targeting specification provided."

        result = self._calculate_coverage(
            targeting,
            product_id,
            total_inventory or 10000000,
        )

        return self._format_result(result, targeting, product_id)

    def _calculate_coverage(
        self,
        targeting: dict[str, Any],
        product_id: str,
        total_inventory: int,
    ) -> dict[str, Any]:
        """Calculate coverage for targeting."""
        # Coverage factors by targeting type
        # These represent the percentage of inventory that can be targeted
        coverage_factors = {
            # Contextual (high coverage)
            "geography": 0.95,
            "geo": 0.95,
            "device": 0.99,
            "device_type": 0.99,
            "content_categories": 0.90,
            "keywords": 0.85,
            "language": 0.98,
            "daypart": 1.0,
            "time_of_day": 1.0,
            # Identity (moderate coverage)
            "demographics": 0.70,
            "age": 0.72,
            "gender": 0.68,
            "income": 0.55,
            "education": 0.50,
            # Behavioral (lower coverage)
            "behaviors": 0.35,
            "interests": 0.45,
            "intent": 0.35,
            "in_market": 0.35,
            "retargeting": 0.20,
            "custom_audience": 0.25,
        }

        # Calculate combined coverage
        active_factors = []
        targeting_breakdown = {}
        limiting_factors = []

        for key, value in targeting.items():
            if value:  # Only count non-empty targeting
                key_lower = key.lower().replace("-", "_")

                # Find matching factor
                factor = None
                for factor_key, factor_value in coverage_factors.items():
                    if factor_key in key_lower or key_lower in factor_key:
                        factor = factor_value
                        break

                if factor is None:
                    factor = 0.80  # Default for unknown targeting

                active_factors.append(factor)
                targeting_breakdown[key] = {
                    "factor": factor,
                    "coverage_percent": factor * 100,
                }

                if factor < 0.5:
                    limiting_factors.append(f"{key} ({factor * 100:.0f}% coverage)")

        if not active_factors:
            # No targeting = full coverage
            combined_coverage = 1.0
        else:
            # Multiply factors (assuming independence)
            # This is a simplification - real systems use overlap models
            combined_coverage = 1.0
            for factor in active_factors:
                combined_coverage *= factor

            # Apply a floor to prevent unrealistically low estimates
            combined_coverage = max(combined_coverage, 0.01)

        # Calculate reach
        estimated_impressions = int(total_inventory * combined_coverage)

        # Determine confidence
        if len(active_factors) <= 2:
            confidence = "high"
        elif len(active_factors) <= 4:
            confidence = "medium"
        else:
            confidence = "low"

        # Check if coverage is sufficient for typical deals
        is_deliverable = combined_coverage >= 0.05  # At least 5% coverage

        return {
            "combined_coverage": combined_coverage,
            "coverage_percentage": combined_coverage * 100,
            "estimated_impressions": estimated_impressions,
            "total_inventory": total_inventory,
            "confidence": confidence,
            "targeting_breakdown": targeting_breakdown,
            "limiting_factors": limiting_factors,
            "num_targeting_layers": len(active_factors),
            "is_deliverable": is_deliverable,
        }

    def _format_result(
        self,
        result: dict[str, Any],
        targeting: dict[str, Any],
        product_id: str,
    ) -> str:
        """Format result as human-readable output."""
        output = f"## Coverage Calculation for {product_id}\n\n"

        # Overall metrics
        coverage = result["coverage_percentage"]
        impressions = result["estimated_impressions"]
        confidence = result["confidence"]

        if coverage >= 50:
            coverage_indicator = "[HIGH]"
        elif coverage >= 20:
            coverage_indicator = "[MODERATE]"
        elif coverage >= 5:
            coverage_indicator = "[LOW]"
        else:
            coverage_indicator = "[VERY LOW]"

        output += f"**Overall Coverage: {coverage:.1f}%** {coverage_indicator}\n"
        output += f"   Estimated Impressions: {impressions:,}\n"
        output += f"   Total Inventory: {result['total_inventory']:,}\n"
        output += f"   Confidence: {confidence}\n"
        output += (
            f"   Deliverable: {'Yes' if result['is_deliverable'] else 'No - coverage too low'}\n"
        )
        output += "\n"

        # Targeting breakdown
        output += f"**Targeting Breakdown ({result['num_targeting_layers']} layers):**\n"

        breakdown = result["targeting_breakdown"]
        for key, data in sorted(breakdown.items(), key=lambda x: -x[1]["factor"]):
            factor = data["factor"]
            if factor >= 0.7:
                indicator = "[high]"
            elif factor >= 0.4:
                indicator = "[med]"
            else:
                indicator = "[low]"

            output += f"   {key}: {data['coverage_percent']:.0f}% {indicator}\n"
        output += "\n"

        # Limiting factors
        if result["limiting_factors"]:
            output += "**Limiting Factors:**\n"
            for factor in result["limiting_factors"]:
                output += f"   - {factor}\n"
            output += "\n"

        # Recommendations
        output += "---\n"
        output += "**Assessment:**\n"

        if coverage >= 50:
            output += "- Coverage is strong - targeting is deliverable at scale\n"
        elif coverage >= 20:
            output += (
                "- Coverage is moderate - delivery is feasible but may require extended flight\n"
            )
        elif coverage >= 5:
            output += "- Coverage is limited - consider relaxing targeting constraints\n"
            output += "- Recommend discussing reach vs. precision tradeoff with buyer\n"
        else:
            output += "- Coverage is insufficient for reliable delivery\n"
            output += "- Recommend counter-proposal with broader targeting\n"

        if result["limiting_factors"]:
            output += f"\nMain constraint(s): {', '.join(f.split('(')[0].strip() for f in result['limiting_factors'])}\n"

        # Suggestion for low coverage
        if coverage < 20 and result["limiting_factors"]:
            output += "\n**Suggested Alternatives:**\n"
            for factor in result["limiting_factors"]:
                name = factor.split("(")[0].strip()
                if "behavior" in name.lower() or "intent" in name.lower():
                    output += f"   - Replace {name} with contextual targeting as proxy\n"
                elif "income" in name.lower() or "education" in name.lower():
                    output += f"   - Consider using geo-based proxies for {name}\n"
                elif "retarget" in name.lower() or "custom" in name.lower():
                    output += f"   - Expand {name} with lookalike audiences\n"

        return output
