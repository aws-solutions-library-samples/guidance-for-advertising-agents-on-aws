# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Yield Optimizer - Short and long-term yield optimization.

Provides recommendations for the Inventory Manager to maximize:
- Short-term revenue (immediate deal value)
- Long-term yield (advertiser relationships, fill rate, pricing power)
"""

from dataclasses import dataclass
from typing import Any, Optional

from ..config import get_settings
from ..models.buyer_identity import AccessTier, BuyerContext
from ..models.flow_state import ProposalEvaluation


@dataclass
class YieldScore:
    """Yield score for a deal opportunity."""

    overall_score: float  # 0-1 overall attractiveness
    revenue_score: float  # 0-1 immediate revenue value
    relationship_score: float  # 0-1 long-term relationship value
    fill_rate_impact: float  # 0-1 impact on fill rate
    pricing_power_impact: float  # 0-1 impact on pricing power
    recommendation: str  # accept, counter, reject
    rationale: str


@dataclass
class YieldRecommendation:
    """Recommendation from the yield optimizer."""

    action: str  # accept, counter, reject, upsell
    confidence: float  # 0-1 confidence in recommendation
    rationale: str
    counter_terms: Optional[dict[str, Any]] = None
    upsell_opportunity: Optional[str] = None


class YieldOptimizer:
    """Engine for yield optimization decisions.

    Balances multiple objectives:
    1. Revenue: Maximize total yield across all inventory
    2. Fill Rate: Minimize unsold inventory
    3. Pricing Power: Maintain and grow CPMs over time
    4. Relationships: Build long-term partnerships
    5. Diversification: Avoid over-reliance on single buyers

    The optimizer considers:
    - Current inventory utilization
    - Buyer's historical value
    - Deal terms relative to market
    - Strategic fit with portfolio goals
    """

    def __init__(
        self,
        fill_rate_target: float = 0.85,
        revenue_weight: float = 0.4,
        relationship_weight: float = 0.3,
        fill_rate_weight: float = 0.2,
        pricing_power_weight: float = 0.1,
    ) -> None:
        """Initialize the yield optimizer.

        Args:
            fill_rate_target: Target fill rate (0-1)
            revenue_weight: Weight for revenue in overall score
            relationship_weight: Weight for relationship value
            fill_rate_weight: Weight for fill rate impact
            pricing_power_weight: Weight for pricing power impact
        """
        self._settings = get_settings()
        self.fill_rate_target = fill_rate_target
        self.revenue_weight = revenue_weight
        self.relationship_weight = relationship_weight
        self.fill_rate_weight = fill_rate_weight
        self.pricing_power_weight = pricing_power_weight

    def score_deal(
        self,
        evaluation: ProposalEvaluation,
        buyer_context: Optional[BuyerContext] = None,
        current_fill_rate: float = 0.75,
        market_cpm: float = 15.0,
    ) -> YieldScore:
        """Score a deal opportunity for yield optimization.

        Args:
            evaluation: Proposal evaluation results
            buyer_context: Buyer identity context
            current_fill_rate: Current inventory fill rate (0-1)
            market_cpm: Current market CPM for comparison

        Returns:
            YieldScore with detailed scoring and recommendation
        """
        # Calculate revenue score
        revenue_score = self._calculate_revenue_score(
            evaluation.requested_price,
            evaluation.recommended_price,
            market_cpm,
        )

        # Calculate relationship score
        relationship_score = self._calculate_relationship_score(buyer_context)

        # Calculate fill rate impact
        fill_rate_impact = self._calculate_fill_rate_impact(
            current_fill_rate,
            evaluation.impressions_available,
        )

        # Calculate pricing power impact
        pricing_power_impact = self._calculate_pricing_power_impact(
            evaluation.requested_price,
            evaluation.minimum_acceptable_price,
            market_cpm,
        )

        # Calculate overall score
        overall_score = (
            revenue_score * self.revenue_weight
            + relationship_score * self.relationship_weight
            + fill_rate_impact * self.fill_rate_weight
            + pricing_power_impact * self.pricing_power_weight
        )

        # Determine recommendation
        recommendation, rationale = self._make_recommendation(
            overall_score=overall_score,
            evaluation=evaluation,
            revenue_score=revenue_score,
            relationship_score=relationship_score,
        )

        return YieldScore(
            overall_score=overall_score,
            revenue_score=revenue_score,
            relationship_score=relationship_score,
            fill_rate_impact=fill_rate_impact,
            pricing_power_impact=pricing_power_impact,
            recommendation=recommendation,
            rationale=rationale,
        )

    def _calculate_revenue_score(
        self,
        offered_price: float,
        recommended_price: float,
        market_cpm: float,
    ) -> float:
        """Calculate revenue score based on offered vs recommended price.

        Args:
            offered_price: Price offered by buyer
            recommended_price: Seller's recommended price
            market_cpm: Market rate for comparison

        Returns:
            Revenue score (0-1)
        """
        if recommended_price <= 0:
            return 0.5

        # Score based on how close offered is to recommended
        price_ratio = offered_price / recommended_price

        if price_ratio >= 1.0:
            # At or above recommended - great
            return min(1.0, 0.8 + (price_ratio - 1.0) * 0.2)
        elif price_ratio >= 0.9:
            # Within 10% - good
            return 0.6 + (price_ratio - 0.9) * 2.0
        elif price_ratio >= 0.8:
            # Within 20% - acceptable
            return 0.4 + (price_ratio - 0.8) * 2.0
        else:
            # Below 80% - poor
            return max(0.0, price_ratio * 0.5)

    def _calculate_relationship_score(
        self,
        buyer_context: Optional[BuyerContext],
    ) -> float:
        """Calculate relationship score based on buyer identity and history.

        Args:
            buyer_context: Buyer identity context

        Returns:
            Relationship score (0-1)
        """
        if not buyer_context:
            return 0.3  # Unknown buyer

        tier = buyer_context.effective_tier

        # Base score by tier
        tier_scores = {
            AccessTier.PUBLIC: 0.2,
            AccessTier.SEAT: 0.4,
            AccessTier.AGENCY: 0.6,
            AccessTier.ADVERTISER: 0.8,
        }
        score = tier_scores.get(tier, 0.3)

        # Boost for relationship history
        if buyer_context.relationship:
            rel = buyer_context.relationship

            # Boost for historical spend
            if rel.total_historical_spend > 1_000_000:
                score = min(1.0, score + 0.15)
            elif rel.total_historical_spend > 100_000:
                score = min(1.0, score + 0.10)

            # Boost for active deals
            if rel.active_deals > 5:
                score = min(1.0, score + 0.05)

            # Boost for good payment history
            if rel.payment_history == "excellent":
                score = min(1.0, score + 0.05)

        return score

    def _calculate_fill_rate_impact(
        self,
        current_fill_rate: float,
        inventory_available: bool,
    ) -> float:
        """Calculate fill rate impact of accepting the deal.

        Args:
            current_fill_rate: Current fill rate (0-1)
            inventory_available: Whether inventory is available

        Returns:
            Fill rate impact score (0-1)
        """
        if not inventory_available:
            return 0.0  # Can't help fill rate if no inventory

        # High score if below target fill rate (need to fill)
        if current_fill_rate < self.fill_rate_target:
            gap = self.fill_rate_target - current_fill_rate
            return min(1.0, 0.5 + gap * 2)

        # Lower score if already at/above target
        return max(0.3, 1.0 - (current_fill_rate - self.fill_rate_target) * 2)

    def _calculate_pricing_power_impact(
        self,
        offered_price: float,
        floor_price: float,
        market_cpm: float,
    ) -> float:
        """Calculate impact on pricing power.

        Accepting low prices can erode pricing power over time.

        Args:
            offered_price: Price offered by buyer
            floor_price: Minimum acceptable price
            market_cpm: Market rate

        Returns:
            Pricing power impact score (0-1)
        """
        if market_cpm <= 0:
            return 0.5

        # Score based on price relative to market
        market_ratio = offered_price / market_cpm

        if market_ratio >= 1.0:
            # At or above market - strengthens pricing power
            return min(1.0, 0.7 + (market_ratio - 1.0) * 0.3)
        elif market_ratio >= 0.9:
            # Within 10% of market - neutral
            return 0.5 + (market_ratio - 0.9) * 2.0
        else:
            # Below market - could weaken pricing power
            return max(0.2, market_ratio * 0.5)

    def _make_recommendation(
        self,
        overall_score: float,
        evaluation: ProposalEvaluation,
        revenue_score: float,
        relationship_score: float,
    ) -> tuple[str, str]:
        """Make a recommendation based on scores.

        Args:
            overall_score: Combined yield score
            evaluation: Proposal evaluation
            revenue_score: Revenue component
            relationship_score: Relationship component

        Returns:
            Tuple of (recommendation, rationale)
        """
        if not evaluation.is_valid:
            return "reject", f"Invalid proposal: {', '.join(evaluation.validation_errors)}"

        if not evaluation.impressions_available:
            return "reject", "Insufficient inventory availability"

        if overall_score >= 0.7:
            return "accept", (
                f"Strong yield opportunity (score: {overall_score:.2f}). "
                f"Revenue: {revenue_score:.2f}, Relationship: {relationship_score:.2f}"
            )
        elif overall_score >= 0.5:
            if revenue_score < 0.5 and relationship_score >= 0.6:
                return "counter", (
                    "Strategic buyer but price needs improvement. "
                    "Counter with recommended price for better yield."
                )
            else:
                return "accept", (
                    f"Acceptable yield (score: {overall_score:.2f}). Consider upsell opportunities."
                )
        elif overall_score >= 0.3:
            return "counter", (
                f"Below yield threshold (score: {overall_score:.2f}). "
                f"Counter with terms that improve revenue or relationship value."
            )
        else:
            return "reject", (
                f"Poor yield opportunity (score: {overall_score:.2f}). "
                f"Price and/or terms are not acceptable."
            )

    def recommend_counter_terms(
        self,
        evaluation: ProposalEvaluation,
        buyer_context: Optional[BuyerContext] = None,
    ) -> YieldRecommendation:
        """Recommend counter-proposal terms for better yield.

        Args:
            evaluation: Current proposal evaluation
            buyer_context: Buyer identity context

        Returns:
            YieldRecommendation with counter terms
        """
        counter_terms: dict[str, Any] = {}
        rationale_parts = []

        # Price counter
        if not evaluation.price_acceptable:
            counter_terms["price"] = evaluation.recommended_price
            rationale_parts.append(f"Increase price to ${evaluation.recommended_price:.2f} CPM")

        # Volume counter
        if evaluation.requested_impressions > evaluation.available_impressions:
            counter_terms["impressions"] = evaluation.available_impressions
            rationale_parts.append(f"Reduce impressions to {evaluation.available_impressions:,}")

        # Relationship-based flexibility
        if buyer_context and buyer_context.effective_tier in [
            AccessTier.AGENCY,
            AccessTier.ADVERTISER,
        ]:
            # Strategic buyers get more flexibility
            counter_terms["negotiation_room"] = 0.05  # 5% additional discount possible
            rationale_parts.append("Strategic buyer - limited negotiation available")

        return YieldRecommendation(
            action="counter",
            confidence=0.7,
            rationale="; ".join(rationale_parts) if rationale_parts else "Standard counter terms",
            counter_terms=counter_terms,
        )

    def identify_upsell(
        self,
        evaluation: ProposalEvaluation,
        buyer_context: Optional[BuyerContext] = None,
        available_products: Optional[list[str]] = None,
    ) -> YieldRecommendation:
        """Identify upsell opportunities for the deal.

        Args:
            evaluation: Current proposal evaluation
            buyer_context: Buyer identity context
            available_products: List of available product types

        Returns:
            YieldRecommendation with upsell opportunity
        """
        upsell_opportunities = []

        # Volume upsell
        if evaluation.impressions_available:
            upsell_opportunities.append(
                "Volume upgrade: Add 20% more impressions at 10% volume discount"
            )

        # Cross-sell based on product type
        if available_products:
            product_type = evaluation.product_id
            if "display" in product_type and "video" in available_products:
                upsell_opportunities.append(
                    "Cross-sell: Add video for higher engagement and brand lift"
                )
            if "display" in product_type and "ctv" in available_products:
                upsell_opportunities.append("Cross-sell: Extend to CTV for full-funnel coverage")
            if "video" in product_type and "ctv" in available_products:
                upsell_opportunities.append("Cross-sell: Add CTV for household-level reach")

        # Commitment upsell
        if buyer_context and buyer_context.effective_tier != AccessTier.PUBLIC:
            upsell_opportunities.append("Commitment bonus: Lock in Q2 now for preferred pricing")

        if upsell_opportunities:
            return YieldRecommendation(
                action="upsell",
                confidence=0.6,
                rationale="Upsell opportunities identified based on buyer profile and inventory",
                upsell_opportunity=upsell_opportunities[0],  # Top opportunity
            )

        return YieldRecommendation(
            action="none",
            confidence=0.5,
            rationale="No strong upsell opportunities identified",
        )
