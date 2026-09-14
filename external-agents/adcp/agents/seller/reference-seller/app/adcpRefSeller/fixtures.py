"""
Deterministic sandbox inventory for the AdCP reference test seller agent.

This is fixture data, not live market data. Every response from this
agent is real AdCP wire traffic (a genuine MCP tool call gets a genuine
MCP response), but the underlying inventory itself is a fixed, hand-authored
catalog meant for conformance testing and demos, the same way AdCP's own
test-agent.adcontextprotocol.org serves seeded data rather than live
programmatic inventory. Every AdCP response this agent returns is marked
sandbox: true via adcp.server.responses builders (see main.py) so nothing
here is mistaken for a live production seller's data. The fixtures are
deterministic and labeled, so a reader can tell where a value came from.

No randomness, no per-call variation: the same brief always surfaces the
same subset of this fixed catalog, filtered deterministically by keyword
overlap (see match_products below).
"""

from typing import Any

# The agent_url every format_id/format entry below points at. In a real
# deployment this would be the seller's own MCP endpoint; formats are
# considered authoritative wherever this URL resolves. Since this is a
# fixed sandbox catalog (not resolved dynamically against a live deploy
# URL), a stable placeholder is used rather than the actual deployed
# AgentCore invoke URL, which is opaque to buyers anyway (they never
# dereference format_ids.agent_url for this agent specifically - it's
# metadata, not a callable path in this fixture).
DEFAULT_AGENT_URL = "https://reference-seller.example/adcp/mcp"

# AdCP's core Product schema requires publisher_properties and
# reporting_capabilities on every product. This reference seller has no
# real publisher relationships (it's a sandbox), so it declares itself as
# its own single "publisher" via a fixed placeholder domain, and a fixed,
# reporting_capabilities block matching what this fixture data
# actually supports (impressions/spend only, no real delivery pipeline).
_PUBLISHER_PROPERTIES = [{"publisher_domain": "reference-seller.example", "selection_type": "all"}]
_REPORTING_CAPABILITIES = {
    "available_reporting_frequencies": ["daily"],
    "expected_delay_minutes": 0,
    "timezone": "UTC",
    "supports_webhooks": False,
    "available_metrics": ["impressions", "spend"],
    "date_range_support": "lifetime_only",
}

PRODUCTS: list[dict[str, Any]] = [
    {
        "product_id": "ref-ctv-sports-01",
        "name": "Reference CTV Sports Pre-roll",
        "description": (
            "Sandbox CTV pre-roll inventory on a sports streaming app. "
            "Fixture data for AdCP conformance testing."
        ),
        "delivery_type": "non_guaranteed",
        "channels": ["ctv"],
        "publisher_properties": _PUBLISHER_PROPERTIES,
        "reporting_capabilities": _REPORTING_CAPABILITIES,
        "format_ids": [{"id": "video_16x9_30s", "agent_url": DEFAULT_AGENT_URL}],
        "pricing_options": [
            {
                "pricing_option_id": "cpm-ref-ctv-sports-01",
                "pricing_model": "cpm",
                "currency": "USD",
                "fixed_price": 24.0,
            }
        ],
        "keywords": ["ctv", "sports", "streaming", "pre-roll", "video"],
    },
    {
        "product_id": "ref-mobile-rewarded-01",
        "name": "Reference Mobile Rewarded Video",
        "description": (
            "Sandbox rewarded video inventory in a casual mobile game. "
            "Fixture data for AdCP conformance testing."
        ),
        "delivery_type": "non_guaranteed",
        "channels": ["gaming"],
        "publisher_properties": _PUBLISHER_PROPERTIES,
        "reporting_capabilities": _REPORTING_CAPABILITIES,
        "format_ids": [{"id": "rewarded_video_15s", "agent_url": DEFAULT_AGENT_URL}],
        "pricing_options": [
            {
                "pricing_option_id": "cpm-ref-mobile-rewarded-01",
                "pricing_model": "cpm",
                "currency": "USD",
                "fixed_price": 14.5,
            }
        ],
        "keywords": ["mobile", "rewarded", "gaming", "casual", "video", "app"],
    },
    {
        "product_id": "ref-display-news-01",
        "name": "Reference News Site Display",
        "description": (
            "Sandbox 300x250 display inventory on a news publisher site. "
            "Fixture data for AdCP conformance testing."
        ),
        "delivery_type": "non_guaranteed",
        "channels": ["display"],
        "publisher_properties": _PUBLISHER_PROPERTIES,
        "reporting_capabilities": _REPORTING_CAPABILITIES,
        "format_ids": [{"id": "display_300x250", "agent_url": DEFAULT_AGENT_URL}],
        "pricing_options": [
            {
                "pricing_option_id": "cpm-ref-display-news-01",
                "pricing_model": "cpm",
                "currency": "USD",
                "fixed_price": 6.0,
            }
        ],
        "keywords": ["display", "news", "banner", "web", "desktop"],
    },
    {
        # The SECOND format with a real, fetchable creative fixture (`clean_leaderboard.png`), so the
        # journey's Creative scan can show a genuine verdict on more than one card. The three features
        # the governance evaluator computes are all image measurements, so they apply here unchanged.
        "product_id": "ref-display-leaderboard-01",
        "name": "Reference News Site Leaderboard",
        "description": (
            "Sandbox 728x90 leaderboard inventory above the fold on a news publisher site. "
            "Fixture data for AdCP conformance testing."
        ),
        "delivery_type": "non_guaranteed",
        "channels": ["display"],
        "publisher_properties": _PUBLISHER_PROPERTIES,
        "reporting_capabilities": _REPORTING_CAPABILITIES,
        "format_ids": [{"id": "display_728x90", "agent_url": DEFAULT_AGENT_URL}],
        "pricing_options": [
            {
                "pricing_option_id": "cpm-ref-display-leaderboard-01",
                "pricing_model": "cpm",
                "currency": "USD",
                "fixed_price": 4.5,
            }
        ],
        "keywords": ["display", "news", "leaderboard", "banner", "web", "desktop"],
    },
    {
        "product_id": "ref-audio-podcast-01",
        "name": "Reference Podcast Mid-roll Audio",
        "description": (
            "Sandbox mid-roll audio inventory across a podcast network. "
            "Fixture data for AdCP conformance testing."
        ),
        "delivery_type": "non_guaranteed",
        "channels": ["podcast"],
        "publisher_properties": _PUBLISHER_PROPERTIES,
        "reporting_capabilities": _REPORTING_CAPABILITIES,
        "format_ids": [{"id": "audio_30s", "agent_url": DEFAULT_AGENT_URL}],
        "pricing_options": [
            {
                "pricing_option_id": "cpm-ref-audio-podcast-01",
                "pricing_model": "cpm",
                "currency": "USD",
                "fixed_price": 18.0,
            }
        ],
        "keywords": ["audio", "podcast", "mid-roll", "streaming"],
    },
]

# AdCP's core Format schema requires format_id to be a structured
# {agent_url, id} object, not a bare string - see format-id.json.
CREATIVE_FORMATS: list[dict[str, Any]] = [
    {
        "format_id": {"id": "video_16x9_30s", "agent_url": DEFAULT_AGENT_URL},
        "name": "Video 16:9 30s",
        "dimensions": {"width": 1920, "height": 1080},
        "requirements": {"max_duration_ms": 30000},
    },
    {
        "format_id": {"id": "rewarded_video_15s", "agent_url": DEFAULT_AGENT_URL},
        "name": "Rewarded Video 15s",
        "dimensions": {"width": 1080, "height": 1920},
        "requirements": {"max_duration_ms": 15000},
    },
    {
        "format_id": {"id": "display_300x250", "agent_url": DEFAULT_AGENT_URL},
        "name": "Display 300x250",
        "dimensions": {"width": 300, "height": 250},
        # U4/Part 1: real asset-slot declaration for get_creative_features (R22). One required image
        # slot -- `asset_id` is the manifest key a real submission MUST use (core/format.json's own
        # `baseIndividualAsset.asset_id` docstring); the format-level `requirements.file_types` this
        # entry used to carry moved INTO this slot's own `requirements`, since AdCP's schema scopes
        # asset requirements (dimensions, formats, dpi, etc) per-asset, not at the format level -- a
        # format's top-level `requirements` (see the other three fixtures below) is a different,
        # coarser field (max_duration_ms) with no defined `file_types` property of its own.
        "assets": [
            {
                "item_type": "individual",
                "asset_id": "main_image",
                "asset_type": "image",
                "required": True,
                "requirements": {
                    "min_width": 300,
                    "max_width": 300,
                    "min_height": 250,
                    "max_height": 250,
                    "formats": ["jpg", "jpeg", "png", "gif"],
                },
            }
        ],
    },
    {
        # The second evaluable display format. Same asset-slot shape as `display_300x250` above, at this
        # format's own dimensions -- `min_*`/`max_*` pinned equal, so the governance evaluator's
        # `dimension_conformance` check has an exact requirement to measure against rather than a range
        # that any leaderboard-ish image would satisfy.
        #
        # `asset_id` is `main_image` for the same reason it is there: that is the manifest key a real
        # submission uses, and the evaluator reads `assets.main_image.url`.
        "format_id": {"id": "display_728x90", "agent_url": DEFAULT_AGENT_URL},
        "name": "Display 728x90 Leaderboard",
        "dimensions": {"width": 728, "height": 90},
        "assets": [
            {
                "item_type": "individual",
                "asset_id": "main_image",
                "asset_type": "image",
                "required": True,
                "requirements": {
                    "min_width": 728,
                    "max_width": 728,
                    "min_height": 90,
                    "max_height": 90,
                    "formats": ["jpg", "jpeg", "png", "gif"],
                },
            }
        ],
    },
    {
        "format_id": {"id": "audio_30s", "agent_url": DEFAULT_AGENT_URL},
        "name": "Audio 30s",
        "requirements": {"max_duration_ms": 30000, "file_types": ["mp3"]},
    },
]


def match_products(brief: str | None) -> list[dict[str, Any]]:
    """Deterministic keyword-overlap filter over the fixed PRODUCTS catalog.

    No LLM, no randomness: the same brief text always returns the same
    subset in the same order. Products carry a "keywords" field (stripped
    before returning, since it's not part of the AdCP Product schema) used
    only for this matching, not surfaced on the wire.

    Empty/missing brief (e.g. wholesale buying_mode) returns the full
    catalog, matching the AdCP spec's wholesale semantics.
    """
    if not brief:
        return [_without_keywords(p) for p in PRODUCTS]

    words = {w.strip(".,!?").lower() for w in brief.split()}
    matched = [p for p in PRODUCTS if words & set(p["keywords"])]
    return [_without_keywords(p) for p in (matched or PRODUCTS)]


def find_product(product_id: str) -> dict[str, Any] | None:
    """Look up a single product by exact `product_id` in the fixed
    PRODUCTS catalog.

    Used by the mutating media-buy tasks (`create_media_buy`,
    `update_media_buy` — tasks 3-4 of
    `.kiro/specs/seller-agent-adcp-compliance/design.md`) to validate a
    buyer-supplied `product_id` before creating/updating a media buy.
    Returns `None` when `product_id` isn't in
    the catalog, so callers can return a `PRODUCT_NOT_FOUND` error rather
    than a success response — same discipline as
    `match_products`.
    """
    for product in PRODUCTS:
        if product["product_id"] == product_id:
            return _without_keywords(product)
    return None


def _without_keywords(product: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in product.items() if k != "keywords"}
