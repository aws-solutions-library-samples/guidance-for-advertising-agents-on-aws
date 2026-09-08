# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Booking modules for deal creation and pricing.

This package consolidates deal-booking logic that was previously
duplicated across unified_client.py, request_deal.py, and get_pricing.py.

Public API:
    PricingCalculator - Calculate tiered and volume-discounted pricing
    PricingResult - Result dataclass from pricing calculations
    generate_deal_id - Generate unique deal IDs
    QuoteFlowClient - Quote-then-book flow for deal creation
    QuoteNormalizer - Normalizes quotes from different sellers for comparison
    TemplateFlowClient - Template-based booking (stub)
"""

from .deal_id import generate_deal_id
from .pricing import PricingCalculator, PricingResult
from .quote_flow import QuoteFlowClient
from .quote_normalizer import NormalizedQuote, QuoteNormalizer, SupplyPathInfo
from .template_flow import TemplateFlowClient

__all__ = [
    "NormalizedQuote",
    "PricingCalculator",
    "PricingResult",
    "QuoteFlowClient",
    "QuoteNormalizer",
    "SupplyPathInfo",
    "TemplateFlowClient",
    "generate_deal_id",
]
