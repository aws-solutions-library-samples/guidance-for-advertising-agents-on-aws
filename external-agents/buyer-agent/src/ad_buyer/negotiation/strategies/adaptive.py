# Author: Agent Range
# Donated to IAB Tech Lab

"""Adaptive negotiation strategy (stub).

Future implementation: adjusts concession behavior based on
observed seller patterns (e.g., if seller concedes large amounts,
we hold firm; if seller is firm, we concede faster).
"""

from ..strategy import NegotiationContext, NegotiationStrategy


class AdaptiveStrategy(NegotiationStrategy):
    """Adapts based on seller behavior patterns. Not yet implemented."""

    def should_accept(self, seller_price: float, context: NegotiationContext) -> bool:
        raise NotImplementedError("AdaptiveStrategy is not yet implemented")

    def next_offer(self, seller_price: float, context: NegotiationContext) -> float:
        raise NotImplementedError("AdaptiveStrategy is not yet implemented")

    def should_walk_away(self, seller_price: float, context: NegotiationContext) -> bool:
        raise NotImplementedError("AdaptiveStrategy is not yet implemented")
