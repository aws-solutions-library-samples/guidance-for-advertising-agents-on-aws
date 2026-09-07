# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Audience Validation Tool - Validate buyer audience requests."""

from typing import Any, Optional, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...clients.ucp_client import UCPClient
from ...models.ucp import (
    AudienceCapability,
    AudienceValidationResult,
    EmbeddingType,
    SignalType,
    UCPConsent,
)


class AudienceValidationInput(BaseModel):
    """Input schema for audience validation tool."""

    audience_targeting: dict[str, Any] = Field(
        description="Buyer's audience targeting requirements"
    )
    product_id: str = Field(description="Product ID to validate against")
    product_characteristics: Optional[dict[str, Any]] = Field(
        default=None,
        description="Product characteristics for embedding generation",
    )


class AudienceValidationTool(BaseTool):
    """Validate buyer audience requests against product capabilities.

    This tool validates whether the seller's inventory can fulfill
    the buyer's audience targeting requirements, using UCP for
    similarity matching.
    """

    name: str = "validate_audience_request"
    description: str = """Validate a buyer's audience targeting request against
    product capabilities. Returns validation status (valid, partial_match, no_match),
    coverage percentage, UCP similarity score, and any gaps or alternatives.
    Use this to evaluate whether a proposal's audience can be fulfilled."""
    args_schema: Type[BaseModel] = AudienceValidationInput

    def _run(
        self,
        audience_targeting: dict[str, Any],
        product_id: str,
        product_characteristics: Optional[dict[str, Any]] = None,
    ) -> str:
        """Execute the audience validation."""
        if not audience_targeting:
            return "Error: No audience targeting provided."

        # Get or generate product characteristics
        if not product_characteristics:
            product_characteristics = self._get_default_characteristics(product_id)

        # Get capabilities
        capabilities = self._get_product_capabilities(product_id)

        # Create UCP client and embeddings
        client = UCPClient()

        # Create buyer query embedding
        buyer_embedding = client.create_embedding(
            vector=client._generate_synthetic_embedding(
                audience_targeting,
                512,
            ),
            embedding_type=EmbeddingType.QUERY,
            signal_type=SignalType.CONTEXTUAL,
            consent=UCPConsent(
                framework="IAB-TCFv2",
                permissible_uses=["personalization", "measurement"],
            ),
        )

        # Create product embedding
        product_embedding = client.create_inventory_embedding(
            product_characteristics=product_characteristics,
        )

        # Validate
        result = client.validate_buyer_audience(
            buyer_embedding=buyer_embedding,
            product_embedding=product_embedding,
            capabilities=capabilities,
            audience_requirements=audience_targeting,
        )

        return self._format_result(result, product_id, audience_targeting)

    def _get_default_characteristics(self, product_id: str) -> dict[str, Any]:
        """Get default product characteristics."""
        return {
            "product_id": product_id,
            "inventory_type": "display",
            "content_categories": ["news", "entertainment"],
            "demographics": {
                "age_range": "25-54",
                "gender_mix": "balanced",
            },
        }

    def _get_product_capabilities(self, product_id: str) -> list[AudienceCapability]:
        """Get audience capabilities for a product."""
        # In production, this would query the product catalog
        return [
            AudienceCapability(
                capability_id=f"{product_id}_demo",
                name="Demographics",
                signal_type=SignalType.IDENTITY,
                coverage_percentage=70.0,
                available_segments=["18-24", "25-34", "35-44", "45-54", "55+"],
                ucp_compatible=True,
                embedding_dimension=512,
            ),
            AudienceCapability(
                capability_id=f"{product_id}_ctx",
                name="Contextual",
                signal_type=SignalType.CONTEXTUAL,
                coverage_percentage=95.0,
                available_segments=["news", "sports", "entertainment", "business"],
                ucp_compatible=True,
                embedding_dimension=512,
            ),
            AudienceCapability(
                capability_id=f"{product_id}_intent",
                name="Intent",
                signal_type=SignalType.REINFORCEMENT,
                coverage_percentage=35.0,
                available_segments=["in-market-auto", "in-market-travel"],
                ucp_compatible=True,
                embedding_dimension=512,
            ),
        ]

    def _format_result(
        self,
        result: AudienceValidationResult,
        product_id: str,
        targeting: dict[str, Any],
    ) -> str:
        """Format validation result as human-readable output."""
        output = f"## Audience Validation for {product_id}\n\n"

        # Status
        status_emoji = {
            "valid": "PASS",
            "partial_match": "PARTIAL",
            "no_match": "FAIL",
            "invalid": "ERROR",
        }
        status = status_emoji.get(result.validation_status, "UNKNOWN")

        output += f"**Status: {status}**\n"
        output += f"   Validation: {result.validation_status}\n"
        output += f"   Targeting Compatible: {'Yes' if result.targeting_compatible else 'No'}\n"
        output += "\n"

        # UCP Metrics
        output += "**UCP Analysis:**\n"
        if result.ucp_similarity_score is not None:
            score = result.ucp_similarity_score
            if score >= 0.7:
                quality = "strong"
            elif score >= 0.5:
                quality = "moderate"
            elif score >= 0.3:
                quality = "weak"
            else:
                quality = "poor"
            output += f"   Similarity Score: {score:.2f} ({quality})\n"

        output += f"   Coverage: {result.overall_coverage_percentage:.1f}%\n"

        if result.estimated_reach:
            output += f"   Estimated Reach: {result.estimated_reach:,} impressions\n"
        output += "\n"

        # Matched capabilities
        if result.matched_capabilities:
            output += f"**Matched Capabilities ({len(result.matched_capabilities)}):**\n"
            for cap in result.matched_capabilities:
                output += f"   - {cap}\n"
            output += "\n"

        # Gaps
        if result.gaps:
            output += "**Gaps (Cannot Fulfill):**\n"
            for gap in result.gaps:
                output += f"   - {gap}\n"
            output += "\n"

        # Alternatives
        if result.alternatives:
            output += "**Suggested Alternatives:**\n"
            for alt in result.alternatives:
                output += f"   - {alt.get('gap', '')}: {alt.get('suggestion', '')}\n"
            output += "\n"

        # Validation notes
        if result.validation_notes:
            output += "**Notes:**\n"
            for note in result.validation_notes:
                output += f"   - {note}\n"
            output += "\n"

        # Recommendation
        output += "---\n"
        output += "**Recommendation:** "

        if (
            result.targeting_compatible
            and result.ucp_similarity_score
            and result.ucp_similarity_score >= 0.7
        ):
            output += "Accept - audience can be fulfilled with high confidence."
        elif result.targeting_compatible:
            output += "Accept with caveats - partial coverage, set expectations appropriately."
        elif result.gaps:
            output += "Counter - propose alternative targeting to fill gaps."
        else:
            output += "Reject or counter - cannot fulfill audience requirements."

        return output
