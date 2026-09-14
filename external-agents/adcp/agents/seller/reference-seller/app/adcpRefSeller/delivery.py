"""
Deterministic delivery-figure derivation for `get_media_buy_delivery` (R4 of
`.kiro/specs/seller-agent-adcp-compliance/{requirements,design}.md`, task 6).

This module is deliberately pure: no DynamoDB access, no `self`, no
`datetime.now()` call inside the derivation itself — `now` is always an
explicit parameter, so the same inputs always produce the exact same
output (no `random`, no hidden clock read). `main.py`'s
`get_media_buy_delivery` override (the AgentCore-facing side) is the only
caller that ever supplies a *real* `now`; every unit test in
`test_delivery.py` pins `now` to a fixed value instead, which is what
makes "same inputs at the same simulated now always produce the same
output" testable at all.

## The formula (design.md, R4)

    elapsed_fraction = min(1.0, (now - created_at) / assumed_campaign_duration)
    impressions      = floor(budget / cpm * 1000 * elapsed_fraction)
    spend            = impressions * cpm / 1000

Same "same input -> same output, no randomness" property `fixtures.py`'s
`match_products` already guarantees for `get_products` — this is the
media-buy-lifecycle equivalent of that discipline, not a random or
per-call-varying simulation. `sandbox: true` on the response this feeds
(`main.py`'s `delivery_response(..., sandbox=True)`) discloses that these
are simulated figures derived from stored state, not real ad-server
delivery data — there is no real ad server behind this reference seller.

## Why a fixed 30-day assumed campaign duration

`design.md`'s own pseudocode names `assumed_campaign_duration` as a fixed
constant, and this implementation keeps it that way rather than threading
`start_time`/`end_time` through from `create_media_buy`. The reason isn't
a shortcut of convenience — it's that `create_media_buy`'s stored
`MediaBuyStore` record (task 3, `main.py`/`state.py`) never actually
persists `start_time`/`end_time` at all: `create_media_buy`'s handler only
extracts `product_id`, `pricing_option_id`, `budget`, and `packages` from
the request into `media_buy_store.create(...)` — the request's
`start_time`/`end_time` fields (visible in `test_main.py`'s
`_base_params`) are read by Pydantic validation upstream but dropped on
the floor before they ever reach the store. Threading them through now
would mean reopening task 3/4's `create_media_buy`/`update_media_buy`
handlers and `MediaBuyStore.create`'s signature — out of this task's
scope per its own instructions ("don't rewrite task 3/4's stored record").

**Flagged gap, not silently accepted:** because of this, every media
buy's delivery curve is computed against a flight length this module
assumes (30 days) rather than the flight length the buyer actually
requested — a media buy the buyer flighted for 7 days will still be
reported as "even further from 100% delivered" than reality at, say, day
5, since this module doesn't know the buy was only ever meant to run a
week. This is a documented simplification (task 6's own instructions
explicitly permit it) — the derivation is still fully deterministic and
disclosed as sandbox data. A follow-up task that
adds `start_time`/`end_time` to `MediaBuyStore`'s record would let this
module compute a real per-buy `assumed_campaign_duration` instead of this
shared constant.

30 days is chosen (not, say, 7 or 90) because it's a plausible average
flight length for the kind of non-guaranteed sandbox inventory this
seller's fixture catalog models (CTV/mobile/display/audio spot buys, not
year-long upfronts) — there's no "correct" answer without real per-buy
dates, so this picks a value clearly documented as a placeholder rather
than one relied upon for precision.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

# Fixed assumed campaign duration used to compute elapsed_fraction when no
# real per-media-buy flight length is available (see module docstring for
# why this is a documented placeholder, not a derived value).
ASSUMED_CAMPAIGN_DURATION = timedelta(days=30)


@dataclass(frozen=True)
class DeliveryFigures:
    """Result of `derive_delivery_figures` — an immutable, easily
    equality-asserted pair, so unit tests can assert exact equality
    (`DeliveryFigures(...) == DeliveryFigures(...)`) rather than just
    "close enough" float comparisons."""

    impressions: int
    spend: float


def _parse_iso(value: str) -> datetime:
    """Parse an ISO 8601 timestamp (the shape `state.py::_now_iso` writes,
    e.g. `2026-05-01T00:00:00+00:00`) into an aware `datetime`. Raises
    `ValueError` on malformed input rather than silently defaulting —
    a malformed stored `created_at` is a data integrity bug this module
    should surface, not paper over with a substituted fallback timestamp.
    """
    return datetime.fromisoformat(value)


def derive_delivery_figures(
    *,
    budget: float,
    cpm: float,
    created_at: str,
    now: datetime,
    assumed_campaign_duration: timedelta = ASSUMED_CAMPAIGN_DURATION,
) -> DeliveryFigures:
    """Deterministically derive simulated delivery figures for one budget
    line (a media buy's total budget, or a single package's budget — this
    function doesn't care which, it just needs one (budget, cpm) pair and
    the media buy's real `created_at`).

    Args:
        budget: Budget for this line, in the pricing option's currency.
        cpm: The matched pricing option's `fixed_price` (cost per 1000
            impressions). Must be > 0 — see below for the `cpm <= 0` case.
        created_at: The stored record's real `created_at` (ISO 8601,
            timezone-aware) — i.e. when this media buy was actually
            created.
        now: The current time, injected by the caller rather than read
            internally via `datetime.now()` — this is what makes the
            function pure and its output reproducible in tests (pin `now`
            to a fixed value and the result never varies across calls).
        assumed_campaign_duration: See module docstring for why this
            defaults to a fixed 30-day constant rather than a real
            per-buy flight length.

    Returns:
        A `DeliveryFigures(impressions, spend)` — `impressions` is always
        a non-negative `int` (via `math.floor`, never `random`), `spend`
        is always a non-negative `float` derived arithmetically from
        `impressions` and `cpm` (never independently rounded/estimated),
        so `spend` never disagrees with `impressions` for the same call.

    `elapsed_fraction` is clamped to `[0.0, 1.0]`: `now` before
    `created_at` (clock skew, or a record created moments ago) floors to
    `0.0` rather than going negative; `now` past
    `created_at + assumed_campaign_duration` (a media buy older than the
    assumed flight length) caps at `1.0` — full budget considered spent,
    matching design.md's `min(1.0, ...)` clamp exactly.
    """
    if cpm <= 0:
        # No valid pricing option matched (or a zero/negative fixed_price,
        # which shouldn't occur in this fixture catalog but is guarded
        # rather than dividing by zero) — zero delivery is the correct
        # answer, not a non-zero placeholder.
        return DeliveryFigures(impressions=0, spend=0.0)

    created_at_dt = _parse_iso(created_at)
    elapsed_seconds = (now - created_at_dt).total_seconds()
    duration_seconds = assumed_campaign_duration.total_seconds()
    elapsed_fraction = max(0.0, min(1.0, elapsed_seconds / duration_seconds))

    impressions = math.floor(budget / cpm * 1000 * elapsed_fraction)
    spend = impressions * cpm / 1000
    return DeliveryFigures(impressions=impressions, spend=spend)
