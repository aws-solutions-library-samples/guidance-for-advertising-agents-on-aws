# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""UCP (User Context Protocol) Client for audience validation.

This client handles the exchange of embeddings between buyer and seller agents
following the IAB Tech Lab UCP specification.
"""

import logging
import math
from typing import Any, Optional

from ..models.ucp import (
    AudienceCapability,
    AudienceValidationResult,
    EmbeddingType,
    SignalType,
    SimilarityMetric,
    UCPConsent,
    UCPEmbedding,
    UCPModelDescriptor,
)

logger = logging.getLogger(__name__)

# UCP Content-Type header
UCP_CONTENT_TYPE = "application/vnd.ucp.embedding+json; v=1"


class UCPExchangeResult:
    """Result of a UCP embedding exchange."""

    def __init__(
        self,
        success: bool,
        similarity_score: Optional[float] = None,
        buyer_embedding: Optional[UCPEmbedding] = None,
        seller_embedding: Optional[UCPEmbedding] = None,
        matched_capabilities: list[str] | None = None,
        error: Optional[str] = None,
    ):
        self.success = success
        self.similarity_score = similarity_score
        self.buyer_embedding = buyer_embedding
        self.seller_embedding = seller_embedding
        self.matched_capabilities = matched_capabilities or []
        self.error = error


class UCPClient:
    """Client for UCP embedding exchange and audience validation.

    Handles:
    - Receiving embeddings from buyers
    - Computing similarity between embeddings
    - Validating audience requests against capabilities
    - Reporting audience capabilities
    """

    def __init__(
        self,
        default_dimension: int = 512,
        minimum_coverage_threshold: float = 50.0,
    ):
        """Initialize the UCP client.

        Args:
            default_dimension: Default embedding dimension to use
            minimum_coverage_threshold: Minimum coverage % for targeting_compatible=True
        """
        self._default_dimension = default_dimension
        self._minimum_coverage_threshold = minimum_coverage_threshold

    def compute_similarity(
        self,
        emb1: UCPEmbedding,
        emb2: UCPEmbedding,
        metric: Optional[SimilarityMetric] = None,
    ) -> float:
        """Compute similarity between two embeddings.

        Args:
            emb1: First embedding
            emb2: Second embedding
            metric: Similarity metric to use (defaults to model's recommendation)

        Returns:
            Similarity score (0-1 for cosine, unbounded for dot/L2)
        """
        if emb1.dimension != emb2.dimension:
            logger.warning(f"Dimension mismatch: {emb1.dimension} vs {emb2.dimension}")
            return 0.0

        # Use recommended metric from model descriptor, or cosine as default
        if metric is None:
            metric = emb1.model_descriptor.metric

        v1 = emb1.vector
        v2 = emb2.vector

        if metric == SimilarityMetric.COSINE:
            return self._cosine_similarity(v1, v2)
        elif metric == SimilarityMetric.DOT:
            return self._dot_product(v1, v2)
        elif metric == SimilarityMetric.L2:
            return self._l2_distance(v1, v2)
        else:
            return self._cosine_similarity(v1, v2)

    def _cosine_similarity(self, v1: list[float], v2: list[float]) -> float:
        """Compute cosine similarity."""
        dot = sum(a * b for a, b in zip(v1, v2))
        norm1 = math.sqrt(sum(a * a for a in v1))
        norm2 = math.sqrt(sum(b * b for b in v2))

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return dot / (norm1 * norm2)

    def _dot_product(self, v1: list[float], v2: list[float]) -> float:
        """Compute dot product."""
        return sum(a * b for a, b in zip(v1, v2))

    def _l2_distance(self, v1: list[float], v2: list[float]) -> float:
        """Compute L2 (Euclidean) distance.

        Note: Returns distance, not similarity. Lower is more similar.
        """
        return math.sqrt(sum((a - b) ** 2 for a, b in zip(v1, v2)))

    def create_embedding(
        self,
        vector: list[float],
        embedding_type: EmbeddingType,
        signal_type: SignalType,
        consent: Optional[UCPConsent] = None,
        model_id: str = "ucp-embedding-v1",
        model_version: str = "1.0.0",
    ) -> UCPEmbedding:
        """Create a UCPEmbedding from a vector.

        Helper method to construct properly formatted embeddings.

        Args:
            vector: The embedding vector
            embedding_type: Type of embedding
            signal_type: UCP signal type
            consent: Consent information (required)
            model_id: Model identifier
            model_version: Model version

        Returns:
            Properly formatted UCPEmbedding
        """
        dimension = len(vector)

        if consent is None:
            # Create default consent with minimal permissions
            consent = UCPConsent(
                framework="IAB-TCFv2",
                permissible_uses=["measurement"],
                ttl_seconds=3600,
            )

        model_descriptor = UCPModelDescriptor(
            id=model_id,
            version=model_version,
            dimension=dimension,
            metric=SimilarityMetric.COSINE,
        )

        return UCPEmbedding(
            embedding_type=embedding_type,
            signal_type=signal_type,
            vector=vector,
            dimension=dimension,
            model_descriptor=model_descriptor,
            consent=consent,
        )

    def create_inventory_embedding(
        self,
        product_characteristics: dict[str, Any],
        consent: Optional[UCPConsent] = None,
    ) -> UCPEmbedding:
        """Create an inventory embedding from product characteristics.

        Generates a synthetic embedding representing the inventory's
        audience characteristics. In production, this would use a trained model.

        Args:
            product_characteristics: Product/inventory characteristics
            consent: Consent information

        Returns:
            UCPEmbedding representing the inventory audience
        """
        vector = self._generate_synthetic_embedding(
            product_characteristics,
            self._default_dimension,
        )

        return self.create_embedding(
            vector=vector,
            embedding_type=EmbeddingType.INVENTORY,
            signal_type=SignalType.CONTEXTUAL,
            consent=consent,
        )

    def _generate_synthetic_embedding(
        self,
        characteristics: dict[str, Any],
        dimension: int,
    ) -> list[float]:
        """Generate a synthetic embedding from characteristics.

        This is a placeholder - in production, use a trained embedding model.
        """
        import hashlib
        import random

        # Create a deterministic seed from characteristics
        char_str = str(sorted(characteristics.items()))
        seed = int(hashlib.sha256(char_str.encode()).hexdigest()[:8], 16)

        # Generate pseudo-random but deterministic vector
        random.seed(seed)

        # Generate normalized vector
        vector = [random.gauss(0, 1) for _ in range(dimension)]
        norm = math.sqrt(sum(v * v for v in vector))
        if norm > 0:
            vector = [v / norm for v in vector]

        return vector

    def validate_buyer_audience(
        self,
        buyer_embedding: UCPEmbedding,
        product_embedding: UCPEmbedding,
        capabilities: list[AudienceCapability],
        audience_requirements: Optional[dict[str, Any]] = None,
    ) -> AudienceValidationResult:
        """Validate a buyer's audience request against product capabilities.

        Args:
            buyer_embedding: Buyer's query embedding
            product_embedding: Product's inventory embedding
            capabilities: Available audience capabilities
            audience_requirements: Original audience requirements for gap analysis

        Returns:
            AudienceValidationResult with coverage and gaps
        """
        # Check consent
        if not buyer_embedding.consent or not buyer_embedding.consent.permissible_uses:
            return AudienceValidationResult(
                validation_status="invalid",
                targeting_compatible=False,
                validation_notes=["Missing or invalid consent"],
            )

        # Compute similarity
        similarity = self.compute_similarity(buyer_embedding, product_embedding)

        # Match capabilities
        matched_caps = []
        for cap in capabilities:
            if cap.ucp_compatible and cap.coverage_percentage > 0:
                matched_caps.append(cap.capability_id)

        # Determine coverage
        coverage_percentage = similarity * 100

        # Analyze gaps
        gaps = []
        alternatives = []
        if audience_requirements:
            gaps, alternatives = self._analyze_gaps(audience_requirements, capabilities)

        # Determine status
        if coverage_percentage >= self._minimum_coverage_threshold:
            status = "valid"
            compatible = True
        elif coverage_percentage >= 30:
            status = "partial_match"
            compatible = coverage_percentage >= self._minimum_coverage_threshold
        elif coverage_percentage > 0:
            status = "partial_match"
            compatible = False
        else:
            status = "no_match"
            compatible = False

        # Estimate reach based on coverage
        estimated_reach = None
        if capabilities:
            total_inventory = sum(1000000 for cap in capabilities if cap.coverage_percentage > 0)
            estimated_reach = int(total_inventory * (coverage_percentage / 100))

        return AudienceValidationResult(
            validation_status=status,
            overall_coverage_percentage=coverage_percentage,
            matched_capabilities=matched_caps,
            gaps=gaps,
            alternatives=alternatives,
            ucp_similarity_score=similarity,
            targeting_compatible=compatible,
            estimated_reach=estimated_reach,
            validation_notes=[
                f"UCP similarity: {similarity:.2f}",
                f"Coverage: {coverage_percentage:.1f}%",
                f"Matched {len(matched_caps)} of {len(capabilities)} capabilities",
            ],
        )

    def _analyze_gaps(
        self,
        requirements: dict[str, Any],
        capabilities: list[AudienceCapability],
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """Analyze gaps between requirements and capabilities.

        Args:
            requirements: Audience targeting requirements
            capabilities: Available capabilities

        Returns:
            Tuple of (gaps, alternatives)
        """
        gaps = []
        alternatives = []

        # Check for demographic gaps
        demographics = requirements.get("demographics", {})
        if demographics:
            has_demo_cap = any(cap.signal_type == SignalType.IDENTITY for cap in capabilities)
            if not has_demo_cap:
                gaps.append("demographic_targeting")
                alternatives.append(
                    {
                        "gap": "demographic_targeting",
                        "suggestion": "Use contextual signals as proxy for demographics",
                    }
                )

        # Check for interest gaps
        interests = requirements.get("interests", [])
        if interests:
            has_contextual = any(cap.signal_type == SignalType.CONTEXTUAL for cap in capabilities)
            if not has_contextual:
                gaps.append("interest_targeting")

        # Check for behavioral gaps
        behaviors = requirements.get("behaviors", [])
        if behaviors:
            has_reinforcement = any(
                cap.signal_type == SignalType.REINFORCEMENT for cap in capabilities
            )
            if not has_reinforcement:
                gaps.append("behavioral_targeting")
                alternatives.append(
                    {
                        "gap": "behavioral_targeting",
                        "suggestion": "Use contextual signals with frequency capping",
                    }
                )

        return gaps, alternatives

    def handle_embedding_request(
        self,
        buyer_embedding_data: dict[str, Any],
        product_embedding: UCPEmbedding,
        capabilities: list[AudienceCapability],
    ) -> dict[str, Any]:
        """Handle an incoming UCP embedding request from a buyer.

        Args:
            buyer_embedding_data: Raw embedding data from buyer
            product_embedding: Pre-computed product embedding
            capabilities: Available audience capabilities

        Returns:
            Response with seller embedding and validation result
        """
        try:
            buyer_embedding = UCPEmbedding.model_validate(buyer_embedding_data)
        except Exception as e:
            logger.error(f"Failed to parse buyer embedding: {e}")
            return {
                "error": "Invalid embedding format",
                "details": str(e),
            }

        # Validate audience
        validation = self.validate_buyer_audience(
            buyer_embedding=buyer_embedding,
            product_embedding=product_embedding,
            capabilities=capabilities,
        )

        return {
            "embedding": product_embedding.model_dump(by_alias=True, mode="json"),
            "validation": validation.model_dump(by_alias=True, mode="json"),
            "matched_capabilities": validation.matched_capabilities,
            "similarity_score": validation.ucp_similarity_score,
        }

    def calculate_coverage(
        self,
        targeting: dict[str, Any],
        capabilities: list[AudienceCapability],
        total_impressions: int = 1000000,
    ) -> dict[str, Any]:
        """Calculate audience coverage for a targeting specification.

        Args:
            targeting: Targeting criteria
            capabilities: Available audience capabilities
            total_impressions: Total available impressions

        Returns:
            Coverage calculation result
        """
        # Find matching capabilities
        matched = []
        coverage_factors = []

        for cap in capabilities:
            matches_signal_type = True  # Simplified matching

            if matches_signal_type and cap.coverage_percentage > 0:
                matched.append(cap)
                coverage_factors.append(cap.coverage_percentage / 100)

        if not matched:
            return {
                "coverage_percentage": 0.0,
                "estimated_impressions": 0,
                "matched_capabilities": [],
                "confidence": "low",
            }

        # Multiply coverage factors (assuming independence)
        combined_coverage = 1.0
        for factor in coverage_factors:
            combined_coverage *= factor

        # Apply a minimum threshold
        combined_coverage = max(combined_coverage, 0.01)

        estimated_impressions = int(total_impressions * combined_coverage)

        return {
            "coverage_percentage": combined_coverage * 100,
            "estimated_impressions": estimated_impressions,
            "matched_capabilities": [cap.capability_id for cap in matched],
            "confidence": "high" if len(matched) > 1 else "medium",
            "limiting_factors": [cap.name for cap in matched if cap.coverage_percentage < 50],
        }

    def report_capabilities(
        self,
        capabilities: list[AudienceCapability],
    ) -> dict[str, Any]:
        """Generate a capability report for buyers.

        Args:
            capabilities: Available audience capabilities

        Returns:
            Capability report for discovery
        """
        by_signal_type = {}
        for cap in capabilities:
            signal = cap.signal_type.value
            if signal not in by_signal_type:
                by_signal_type[signal] = []
            by_signal_type[signal].append(
                {
                    "capability_id": cap.capability_id,
                    "name": cap.name,
                    "coverage_percentage": cap.coverage_percentage,
                    "ucp_compatible": cap.ucp_compatible,
                }
            )

        return {
            "capabilities": [cap.model_dump(by_alias=True, mode="json") for cap in capabilities],
            "by_signal_type": by_signal_type,
            "total_capabilities": len(capabilities),
            "ucp_compatible_count": sum(1 for cap in capabilities if cap.ucp_compatible),
        }
