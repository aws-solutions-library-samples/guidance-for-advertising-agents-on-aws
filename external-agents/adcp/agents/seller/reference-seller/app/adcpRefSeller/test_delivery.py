"""
Unit tests for delivery.py's pure `derive_delivery_figures` (task 6 of
.kiro/specs/seller-agent-adcp-compliance/tasks.md, R4).

No DynamoDB, no moto, no ToolContext - this is a pure function with no
side effects, so these tests call it directly with pinned inputs.

Validates: Requirements R4, R8 (task 6 of
.kiro/specs/seller-agent-adcp-compliance/tasks.md).
"""

from datetime import datetime, timedelta, timezone

from delivery import DeliveryFigures, derive_delivery_figures

CREATED_AT = "2026-05-01T00:00:00+00:00"


class TestDeterminism:
    def test_same_inputs_at_the_same_simulated_now_always_produce_the_same_output(self):
        """Task 6's own instruction: same inputs at the same simulated
        'now' always produce the same output - asserted as exact
        equality across repeated calls, not just 'close enough'."""
        now = datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)

        first = derive_delivery_figures(budget=10000.0, cpm=24.0, created_at=CREATED_AT, now=now)
        second = derive_delivery_figures(budget=10000.0, cpm=24.0, created_at=CREATED_AT, now=now)
        third = derive_delivery_figures(budget=10000.0, cpm=24.0, created_at=CREATED_AT, now=now)

        assert first == second == third
        assert isinstance(first, DeliveryFigures)

    def test_matches_the_documented_formula(self):
        """impressions = floor(budget / cpm * 1000 * elapsed_fraction);
        spend = impressions * cpm / 1000 - computed by hand and compared
        against the function's output, not just self-consistency."""
        created_at = "2026-05-01T00:00:00+00:00"
        now = datetime(2026, 5, 16, 0, 0, 0, tzinfo=timezone.utc)  # 15 days elapsed

        result = derive_delivery_figures(budget=9000.0, cpm=30.0, created_at=created_at, now=now)

        elapsed_fraction = 15 / 30  # 15 days elapsed of a 30-day assumed duration
        expected_impressions = int((9000.0 / 30.0 * 1000 * elapsed_fraction) // 1)
        expected_spend = expected_impressions * 30.0 / 1000

        assert result.impressions == expected_impressions == 150000
        assert result.spend == expected_spend == 4500.0


class TestElapsedFractionClamping:
    def test_now_far_past_assumed_duration_caps_at_full_budget_spent(self):
        """now far past assumed_campaign_duration -> elapsed_fraction
        capped at 1.0, i.e. full budget spent / full impressions - task
        6's own instruction."""
        now = datetime(2027, 1, 1, tzinfo=timezone.utc)  # ~8 months after created_at

        capped = derive_delivery_figures(budget=10000.0, cpm=25.0, created_at=CREATED_AT, now=now)
        exactly_at_boundary = derive_delivery_figures(
            budget=10000.0,
            cpm=25.0,
            created_at=CREATED_AT,
            now=datetime(2026, 5, 31, 0, 0, 0, tzinfo=timezone.utc),
        )

        # Both should be identical: 100% of budget spent as impressions.
        assert capped == exactly_at_boundary
        assert capped.impressions == int(10000.0 / 25.0 * 1000)
        assert capped.spend == capped.impressions * 25.0 / 1000

    def test_now_before_created_at_clamps_to_zero_not_negative(self):
        """Clock skew / now slightly before created_at should floor
        elapsed_fraction at 0.0, not go negative and produce a nonsensical
        negative impressions count."""
        now = datetime(2026, 4, 1, tzinfo=timezone.utc)  # before created_at

        result = derive_delivery_figures(budget=5000.0, cpm=10.0, created_at=CREATED_AT, now=now)

        assert result.impressions == 0
        assert result.spend == 0.0

    def test_now_equal_to_created_at_produces_zero_delivery(self):
        now = datetime.fromisoformat(CREATED_AT)
        result = derive_delivery_figures(budget=5000.0, cpm=10.0, created_at=CREATED_AT, now=now)
        assert result.impressions == 0
        assert result.spend == 0.0

    def test_custom_assumed_campaign_duration_changes_the_fraction(self):
        now = datetime(2026, 5, 8, 0, 0, 0, tzinfo=timezone.utc)  # 7 days elapsed

        result = derive_delivery_figures(
            budget=7000.0,
            cpm=10.0,
            created_at=CREATED_AT,
            now=now,
            assumed_campaign_duration=timedelta(days=7),
        )

        # 7 days elapsed of a 7-day assumed duration -> fully delivered.
        assert result.impressions == 700000
        assert result.spend == 7000.0


class TestNonPositiveCpm:
    def test_zero_cpm_returns_zero_delivery(self):
        now = datetime(2026, 5, 15, tzinfo=timezone.utc)
        result = derive_delivery_figures(budget=10000.0, cpm=0.0, created_at=CREATED_AT, now=now)
        assert result == DeliveryFigures(impressions=0, spend=0.0)

    def test_negative_cpm_returns_zero_delivery(self):
        now = datetime(2026, 5, 15, tzinfo=timezone.utc)
        result = derive_delivery_figures(budget=10000.0, cpm=-5.0, created_at=CREATED_AT, now=now)
        assert result == DeliveryFigures(impressions=0, spend=0.0)
