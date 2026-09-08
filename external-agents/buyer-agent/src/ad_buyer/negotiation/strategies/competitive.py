# Author: Agent Range
# Donated to IAB Tech Lab

"""Competitive negotiation strategy (stub).

Future implementation: aggressive strategy for when the buyer is
shopping multiple sellers simultaneously. Leverages competition
to drive harder bargains.
"""

from ..strategy import NegotiationContext, NegotiationStrategy


class CompetitiveStrategy(NegotiationStrategy):
    """Aggressive strategy when shopping multiple sellers. Not yet implemented."""

    def should_accept(self, seller_price: float, context: NegotiationContext) -> bool:
        raise NotImplementedError("CompetitiveStrategy is not yet implemented")

    def next_offer(self, seller_price: float, context: NegotiationContext) -> float:
        raise NotImplementedError("CompetitiveStrategy is not yet implemented")

    def should_walk_away(self, seller_price: float, context: NegotiationContext) -> bool:
        raise NotImplementedError("CompetitiveStrategy is not yet implemented")
