# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for Ad Seller System engines."""

import pytest

from ad_seller.engines.pricing_rules_engine import PricingRulesEngine
from ad_seller.models.core import PricingModel


class TestPricingRulesEngine:
    """Tests for PricingRulesEngine."""

    @pytest.fixture
    def pricing_engine(self, pricing_config):
        """Create a pricing engine instance."""
        return PricingRulesEngine(config=pricing_config)

    def test_public_pricing(self, pricing_engine, sample_product, public_buyer_context):
        """Test public tier pricing."""
        result = pricing_engine.calculate_price(
            product_id=sample_product.product_id,
            base_price=sample_product.base_cpm,
            buyer_context=public_buyer_context,
        )

        assert result.buyer_tier == "public"
        # Public gets base price (no tier discount)
        assert result.final_price == sample_product.base_cpm

    def test_agency_pricing(self, pricing_engine, sample_product, agency_buyer_context):
        """Test agency tier pricing applies 10% discount."""
        result = pricing_engine.calculate_price(
            product_id=sample_product.product_id,
            base_price=sample_product.base_cpm,
            buyer_context=agency_buyer_context,
        )

        assert result.buyer_tier == "agency"
        # Agency gets 10% discount
        expected_price = sample_product.base_cpm * 0.90
        assert result.final_price == expected_price
        assert result.tier_discount == 0.10

    def test_advertiser_pricing(self, pricing_engine, sample_product, advertiser_buyer_context):
        """Test advertiser tier pricing applies 15% discount."""
        result = pricing_engine.calculate_price(
            product_id=sample_product.product_id,
            base_price=sample_product.base_cpm,
            buyer_context=advertiser_buyer_context,
        )

        assert result.buyer_tier == "advertiser"
        # Advertiser gets 15% discount
        expected_price = sample_product.base_cpm * 0.85
        assert result.final_price == expected_price
        assert result.tier_discount == 0.15

    def test_volume_discount_application(
        self, pricing_engine, sample_product, advertiser_buyer_context
    ):
        """Test volume discounts are applied for large orders."""
        # Small volume - no additional volume discount
        small_result = pricing_engine.calculate_price(
            product_id=sample_product.product_id,
            base_price=sample_product.base_cpm,
            buyer_context=advertiser_buyer_context,
            volume=100000,  # 100K impressions
        )

        # Large volume - should get volume discount (5M+ = 5%, 10M+ = 10%, etc.)
        large_result = pricing_engine.calculate_price(
            product_id=sample_product.product_id,
            base_price=sample_product.base_cpm,
            buyer_context=advertiser_buyer_context,
            volume=10000000,  # 10M impressions
        )

        # Large volume should have lower CPM due to volume discount
        assert large_result.final_price < small_result.final_price
        assert large_result.volume_discount > 0

    def test_floor_price_enforcement(self, pricing_engine, advertiser_buyer_context):
        """Test floor price is never violated."""
        # Use a very low base price to test floor enforcement
        result = pricing_engine.calculate_price(
            product_id="test-product",
            base_price=0.50,  # Very low base price
            buyer_context=advertiser_buyer_context,
            volume=100000000,  # Maximum volume discount
        )

        # Should not go below global floor (default 1.0)
        assert result.final_price >= pricing_engine.config.global_floor_cpm

    def test_price_display_public(self, pricing_engine, public_buyer_context):
        """Test price display for public tier shows ranges."""
        display = pricing_engine.get_price_display(
            base_price=20.0,
            buyer_context=public_buyer_context,
        )

        assert display["type"] == "range"
        assert "low" in display
        assert "high" in display
        # Default variance is 20%, so range should be $16-$24
        assert display["low"] == 16.0
        assert display["high"] == 24.0

    def test_price_display_agency(self, pricing_engine, agency_buyer_context):
        """Test price display for agency tier shows exact price."""
        display = pricing_engine.get_price_display(
            base_price=20.0,
            buyer_context=agency_buyer_context,
        )

        assert display["type"] == "exact"
        # Agency gets 10% discount
        assert display["price"] == 18.0
        assert display["negotiation_enabled"] is True

    def test_is_price_acceptable_above_floor(self, pricing_engine, advertiser_buyer_context):
        """Test price acceptability when above floor."""
        acceptable, reason = pricing_engine.is_price_acceptable(
            offered_price=15.0,
            product_floor=10.0,
            buyer_context=advertiser_buyer_context,
        )

        assert acceptable is True
        assert reason == "Price acceptable"

    def test_is_price_acceptable_below_floor(self, pricing_engine, advertiser_buyer_context):
        """Test price acceptability when below product floor."""
        acceptable, reason = pricing_engine.is_price_acceptable(
            offered_price=5.0,
            product_floor=10.0,
            buyer_context=advertiser_buyer_context,
        )

        assert acceptable is False
        assert "Below product floor" in reason

    def test_is_price_acceptable_below_global_floor(self, pricing_engine, advertiser_buyer_context):
        """Test price acceptability when below global floor."""
        acceptable, reason = pricing_engine.is_price_acceptable(
            offered_price=0.50,
            product_floor=0.25,
            buyer_context=advertiser_buyer_context,
        )

        assert acceptable is False
        assert "Below global floor" in reason


class TestPricingDecisionOutput:
    """Tests for PricingDecision output from engine."""

    @pytest.fixture
    def pricing_engine(self, pricing_config):
        """Create a pricing engine instance."""
        return PricingRulesEngine(config=pricing_config)

    def test_pricing_decision_structure(self, pricing_engine, sample_product, agency_buyer_context):
        """Test PricingDecision has all expected fields."""
        result = pricing_engine.calculate_price(
            product_id=sample_product.product_id,
            base_price=sample_product.base_cpm,
            buyer_context=agency_buyer_context,
        )

        assert result.product_id == sample_product.product_id
        assert result.base_price == sample_product.base_cpm
        assert result.final_price > 0
        assert result.currency == "USD"
        assert result.pricing_model == PricingModel.CPM
        assert result.rationale is not None
        assert len(result.applied_rules) > 0

    def test_pricing_decision_rationale(
        self, pricing_engine, sample_product, advertiser_buyer_context
    ):
        """Test PricingDecision includes readable rationale."""
        result = pricing_engine.calculate_price(
            product_id=sample_product.product_id,
            base_price=sample_product.base_cpm,
            buyer_context=advertiser_buyer_context,
            volume=10000000,  # Trigger volume discount
        )

        # Rationale should mention base price and discounts
        assert "Base price" in result.rationale
        assert "Advertiser tier" in result.rationale
        assert "Volume discount" in result.rationale
        assert "Final price" in result.rationale
