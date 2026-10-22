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


def _sandbox_product(
    product_id: str,
    name: str,
    inventory: str,
    channel: str,
    format_id: str,
    cpm: float,
    keywords: list[str],
) -> dict[str, Any]:
    """One catalog entry, in exactly the shape of the hand-written entries below.

    `channel` is an AdCP `MediaChannel` value and `format_id` must name one of
    CREATIVE_FORMATS. Pricing is a single fixed-price USD CPM option, because
    that is the only kind main.py's booking and delivery code reads
    (`_pricing_option_cpm` and `_impressions_from_cpm` use `fixed_price`).
    """
    return {
        "product_id": product_id,
        "name": name,
        "description": f"Sandbox {inventory}. Fixture data for AdCP conformance testing.",
        "delivery_type": "non_guaranteed",
        "channels": [channel],
        "publisher_properties": _PUBLISHER_PROPERTIES,
        "reporting_capabilities": _REPORTING_CAPABILITIES,
        "format_ids": [{"id": format_id, "agent_url": DEFAULT_AGENT_URL}],
        "pricing_options": [
            {
                "pricing_option_id": f"cpm-{product_id}",
                "pricing_model": "cpm",
                "currency": "USD",
                "fixed_price": cpm,
            }
        ],
        "keywords": keywords,
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

# The wider sandbox catalog. Every entry reuses one of the five CREATIVE_FORMATS below, so no
# product points at a format this seller does not declare. Display products use display_300x250 or
# display_728x90, the two formats with published creative fixtures, so the governance creative scan
# can evaluate any display product here.
PRODUCTS.extend(
    [
        # CTV (video_16x9_30s)
        _sandbox_product(
            "ref-ctv-news-01", "Reference CTV News Pre-roll",
            "CTV pre-roll inventory on a live news streaming app",
            "ctv", "video_16x9_30s", 20.0,
            ["ctv", "news", "streaming", "pre-roll", "video"],
        ),
        _sandbox_product(
            "ref-ctv-live-sports-01", "Reference CTV Live Sports Mid-roll",
            "CTV mid-roll inventory in live game streams on a sports streaming app",
            "ctv", "video_16x9_30s", 32.0,
            ["ctv", "live", "sports", "streaming", "mid-roll", "video"],
        ),
        _sandbox_product(
            "ref-ctv-entertainment-01", "Reference CTV Entertainment Mid-roll",
            "CTV mid-roll inventory in scripted drama and comedy series",
            "ctv", "video_16x9_30s", 26.0,
            ["ctv", "entertainment", "drama", "comedy", "streaming", "mid-roll", "video"],
        ),
        _sandbox_product(
            "ref-ctv-movies-01", "Reference CTV Movies On Demand",
            "CTV pre-roll inventory on an ad-supported on-demand film library",
            "ctv", "video_16x9_30s", 28.0,
            ["ctv", "movies", "film", "on-demand", "streaming", "pre-roll", "video"],
        ),
        _sandbox_product(
            "ref-ctv-reality-01", "Reference CTV Reality Series",
            "CTV mid-roll inventory in unscripted reality series",
            "ctv", "video_16x9_30s", 23.0,
            ["ctv", "reality", "entertainment", "streaming", "mid-roll", "video"],
        ),
        _sandbox_product(
            "ref-ctv-kids-01", "Reference CTV Kids & Family Pre-roll",
            "CTV pre-roll inventory on a kids and family streaming profile",
            "ctv", "video_16x9_30s", 16.0,
            ["ctv", "kids", "family", "streaming", "pre-roll", "video"],
        ),
        _sandbox_product(
            "ref-ctv-documentary-01", "Reference CTV Documentary",
            "CTV pre-roll inventory in nature and science documentaries",
            "ctv", "video_16x9_30s", 22.0,
            ["ctv", "documentary", "nature", "science", "streaming", "pre-roll", "video"],
        ),
        _sandbox_product(
            "ref-ctv-food-01", "Reference CTV Food & Cooking",
            "CTV mid-roll inventory in cooking competition and recipe shows",
            "ctv", "video_16x9_30s", 21.0,
            ["ctv", "food", "cooking", "lifestyle", "streaming", "mid-roll", "video"],
        ),
        _sandbox_product(
            "ref-ctv-fast-01", "Reference CTV FAST Channels",
            "CTV inventory across free ad-supported streaming TV channels",
            "ctv", "video_16x9_30s", 14.0,
            ["ctv", "fast", "free", "streaming", "channels", "video"],
        ),
        # Online video (video_16x9_30s)
        _sandbox_product(
            "ref-olv-news-01", "Reference News Site Instream Video",
            "instream pre-roll video inventory on a news publisher site",
            "olv", "video_16x9_30s", 12.0,
            ["olv", "online", "news", "instream", "pre-roll", "video", "web"],
        ),
        _sandbox_product(
            "ref-olv-sports-01", "Reference Sports Highlights Pre-roll",
            "pre-roll video inventory on sports highlight clips",
            "olv", "video_16x9_30s", 13.0,
            ["olv", "online", "sports", "highlights", "pre-roll", "video", "web"],
        ),
        _sandbox_product(
            "ref-olv-tech-01", "Reference Tech Reviews Pre-roll",
            "pre-roll video inventory on consumer technology review videos",
            "olv", "video_16x9_30s", 11.0,
            ["olv", "online", "tech", "technology", "reviews", "gadgets", "pre-roll", "video", "web"],
        ),
        _sandbox_product(
            "ref-olv-finance-01", "Reference Finance Video Pre-roll",
            "pre-roll video inventory on markets and personal finance videos",
            "olv", "video_16x9_30s", 15.0,
            ["olv", "online", "finance", "business", "markets", "pre-roll", "video", "web"],
        ),
        _sandbox_product(
            "ref-olv-travel-01", "Reference Travel Video Pre-roll",
            "pre-roll video inventory on destination and travel guide videos",
            "olv", "video_16x9_30s", 10.0,
            ["olv", "online", "travel", "pre-roll", "video", "web"],
        ),
        _sandbox_product(
            "ref-olv-auto-01", "Reference Automotive Reviews Pre-roll",
            "pre-roll video inventory on new-car review videos",
            "olv", "video_16x9_30s", 14.0,
            ["olv", "online", "auto", "automotive", "cars", "reviews", "pre-roll", "video", "web"],
        ),
        # Gaming (rewarded_video_15s)
        _sandbox_product(
            "ref-mobile-rewarded-puzzle-01", "Reference Puzzle Game Rewarded Video",
            "rewarded video inventory in a mobile puzzle game",
            "gaming", "rewarded_video_15s", 12.0,
            ["mobile", "rewarded", "gaming", "puzzle", "video", "app"],
        ),
        _sandbox_product(
            "ref-mobile-rewarded-strategy-01", "Reference Strategy Game Rewarded Video",
            "rewarded video inventory in a mobile strategy game",
            "gaming", "rewarded_video_15s", 16.0,
            ["mobile", "rewarded", "gaming", "strategy", "video", "app"],
        ),
        _sandbox_product(
            "ref-mobile-rewarded-sports-01", "Reference Sports Game Rewarded Video",
            "rewarded video inventory in a mobile sports simulation game",
            "gaming", "rewarded_video_15s", 15.0,
            ["mobile", "rewarded", "gaming", "sports", "video", "app"],
        ),
        _sandbox_product(
            "ref-mobile-rewarded-word-01", "Reference Word Game Rewarded Video",
            "rewarded video inventory in a mobile word game",
            "gaming", "rewarded_video_15s", 11.0,
            ["mobile", "rewarded", "gaming", "word", "casual", "video", "app"],
        ),
        _sandbox_product(
            "ref-mobile-rewarded-arcade-01", "Reference Arcade Game Rewarded Video",
            "rewarded video inventory in a mobile arcade game",
            "gaming", "rewarded_video_15s", 10.5,
            ["mobile", "rewarded", "gaming", "arcade", "casual", "video", "app"],
        ),
        # Display, 300x250 (display_300x250)
        _sandbox_product(
            "ref-display-sports-01", "Reference Sports Site Display",
            "300x250 display inventory on a sports scores and news site",
            "display", "display_300x250", 5.0,
            ["display", "sports", "banner", "web", "desktop"],
        ),
        _sandbox_product(
            "ref-display-finance-01", "Reference Finance Site Display",
            "300x250 display inventory on a markets and personal finance site",
            "display", "display_300x250", 8.0,
            ["display", "finance", "business", "markets", "banner", "web", "desktop"],
        ),
        _sandbox_product(
            "ref-display-tech-01", "Reference Tech Site Display",
            "300x250 display inventory on a consumer technology site",
            "display", "display_300x250", 6.5,
            ["display", "tech", "technology", "gadgets", "banner", "web", "desktop"],
        ),
        _sandbox_product(
            "ref-display-travel-01", "Reference Travel Site Display",
            "300x250 display inventory on a travel planning site",
            "display", "display_300x250", 5.5,
            ["display", "travel", "banner", "web", "desktop"],
        ),
        _sandbox_product(
            "ref-display-food-01", "Reference Recipe Site Display",
            "300x250 display inventory on a recipe and cooking site",
            "display", "display_300x250", 4.0,
            ["display", "food", "recipes", "cooking", "banner", "web", "desktop"],
        ),
        _sandbox_product(
            "ref-display-auto-01", "Reference Automotive Site Display",
            "300x250 display inventory on a car research site",
            "display", "display_300x250", 7.0,
            ["display", "auto", "automotive", "cars", "banner", "web", "desktop"],
        ),
        _sandbox_product(
            "ref-display-health-01", "Reference Health & Wellness Site Display",
            "300x250 display inventory on a health and wellness site",
            "display", "display_300x250", 6.0,
            ["display", "health", "wellness", "fitness", "banner", "web", "desktop"],
        ),
        _sandbox_product(
            "ref-display-weather-01", "Reference Weather Site Display",
            "300x250 display inventory on a local weather forecast site",
            "display", "display_300x250", 3.5,
            ["display", "weather", "local", "banner", "web", "desktop"],
        ),
        # Display, 728x90 (display_728x90)
        _sandbox_product(
            "ref-display-sports-leaderboard-01", "Reference Sports Site Leaderboard",
            "728x90 leaderboard inventory above the fold on a sports scores and news site",
            "display", "display_728x90", 4.0,
            ["display", "sports", "leaderboard", "banner", "web", "desktop"],
        ),
        _sandbox_product(
            "ref-display-finance-leaderboard-01", "Reference Finance Site Leaderboard",
            "728x90 leaderboard inventory above the fold on a markets and personal finance site",
            "display", "display_728x90", 6.0,
            ["display", "finance", "business", "markets", "leaderboard", "banner", "web", "desktop"],
        ),
        _sandbox_product(
            "ref-display-tech-leaderboard-01", "Reference Tech Site Leaderboard",
            "728x90 leaderboard inventory above the fold on a consumer technology site",
            "display", "display_728x90", 5.0,
            ["display", "tech", "technology", "gadgets", "leaderboard", "banner", "web", "desktop"],
        ),
        _sandbox_product(
            "ref-display-travel-leaderboard-01", "Reference Travel Site Leaderboard",
            "728x90 leaderboard inventory above the fold on a travel planning site",
            "display", "display_728x90", 4.0,
            ["display", "travel", "leaderboard", "banner", "web", "desktop"],
        ),
        _sandbox_product(
            "ref-display-auto-leaderboard-01", "Reference Automotive Site Leaderboard",
            "728x90 leaderboard inventory above the fold on a car research site",
            "display", "display_728x90", 5.5,
            ["display", "auto", "automotive", "cars", "leaderboard", "banner", "web", "desktop"],
        ),
        # Podcast (audio_30s)
        _sandbox_product(
            "ref-audio-podcast-news-01", "Reference News Podcast Pre-roll",
            "pre-roll audio inventory in a daily news podcast",
            "podcast", "audio_30s", 20.0,
            ["audio", "podcast", "news", "pre-roll"],
        ),
        _sandbox_product(
            "ref-audio-podcast-true-crime-01", "Reference True Crime Podcast Mid-roll",
            "mid-roll audio inventory across true crime podcasts",
            "podcast", "audio_30s", 22.0,
            ["audio", "podcast", "true", "crime", "mid-roll"],
        ),
        _sandbox_product(
            "ref-audio-podcast-business-01", "Reference Business Podcast Mid-roll",
            "mid-roll audio inventory across business and investing podcasts",
            "podcast", "audio_30s", 25.0,
            ["audio", "podcast", "business", "finance", "markets", "mid-roll"],
        ),
        _sandbox_product(
            "ref-audio-podcast-comedy-01", "Reference Comedy Podcast Mid-roll",
            "mid-roll audio inventory across comedy podcasts",
            "podcast", "audio_30s", 16.0,
            ["audio", "podcast", "comedy", "entertainment", "mid-roll"],
        ),
        _sandbox_product(
            "ref-audio-podcast-sports-01", "Reference Sports Podcast Mid-roll",
            "mid-roll audio inventory across sports talk podcasts",
            "podcast", "audio_30s", 18.0,
            ["audio", "podcast", "sports", "mid-roll"],
        ),
        # Streaming audio (audio_30s)
        _sandbox_product(
            "ref-audio-music-01", "Reference Music Streaming Audio",
            "audio spots between tracks on a music streaming service",
            "streaming_audio", "audio_30s", 12.0,
            ["audio", "music", "streaming", "playlists"],
        ),
        _sandbox_product(
            "ref-audio-radio-01", "Reference Internet Radio Audio",
            "audio spots on internet radio station streams",
            "streaming_audio", "audio_30s", 9.0,
            ["audio", "radio", "internet", "streaming"],
        ),
        _sandbox_product(
            "ref-audio-workout-01", "Reference Workout Playlist Audio",
            "audio spots in workout and fitness playlists on a music streaming service",
            "streaming_audio", "audio_30s", 11.0,
            ["audio", "music", "workout", "fitness", "streaming", "playlists"],
        ),
    ]
)

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
