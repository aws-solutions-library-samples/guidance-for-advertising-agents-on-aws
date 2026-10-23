"""The evaluable display formats, and the fixture assets that make them evaluable.

The journey's Creative scan shows a real verdict only for a format the governance evaluator can actually
measure, which means a format needs three things to line up: a declaration in `CREATIVE_FORMATS`, an asset
slot whose required dimensions are exact, and a published fixture image of exactly those dimensions.

Nothing asserted that alignment before. `display_728x90` was added as a second evaluable format, and these
tests exist so a fixture resize, a dimension edit, or a forgotten publish-list entry fails here rather
than showing up as an unexplained `dimension_conformance: false` in a live session.
"""

from __future__ import annotations

from pathlib import Path

import struct

import pytest

from deploy_creative_fixtures import FIXTURE_FILES, FIXTURES_DIR
from fixtures import CREATIVE_FORMATS, DEFAULT_AGENT_URL, PRODUCTS


def png_size(path: Path) -> tuple[int, int]:
    """A PNG's real pixel dimensions, read from its IHDR header.

    Parsed rather than read via Pillow because this seller has no image dependency and does not need one
    -- only the governance agent decodes creatives. Adding Pillow to the seller so a test could call it
    would put an image library in a container that never opens an image.

    The signature is checked too, so a file that is not a PNG fails here rather than yielding four bytes
    of something else as a plausible-looking size.
    """
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} is not a PNG"
    assert header[12:16] == b"IHDR", f"{path.name} has no IHDR chunk where one is required"
    width, height = struct.unpack(">II", header[16:24])
    return width, height

#: The display formats with a real, fetchable asset, and the fixture that is a conformant submission for
#: each. Deliberately NOT derived from the format list: this is the claim being checked, so restating it
#: here is the point -- a format that loses its asset should fail, not quietly drop out of the set.
EVALUABLE = {
    "display_300x250": ("clean_banner.png", (300, 250)),
    "display_728x90": ("clean_leaderboard.png", (728, 90)),
}


def format_by_id(format_id: str) -> dict:
    for entry in CREATIVE_FORMATS:
        if entry.get("format_id", {}).get("id") == format_id:
            return entry
    raise AssertionError(f"{format_id} is not declared in CREATIVE_FORMATS")


@pytest.mark.parametrize("format_id", sorted(EVALUABLE))
class TestEvaluableFormats:
    def test_the_format_is_declared(self, format_id):
        entry = format_by_id(format_id)
        # `agent_url` is what scopes the id: AdCP's format reference is the {id, agent_url} pair, and an
        # id alone is only meaningful inside its defining agent's namespace.
        assert entry["format_id"]["agent_url"] == DEFAULT_AGENT_URL

    def test_the_declared_dimensions_match_the_fixture(self, format_id):
        _, expected = EVALUABLE[format_id]
        dimensions = format_by_id(format_id)["dimensions"]
        assert (dimensions["width"], dimensions["height"]) == expected

    def test_the_asset_slot_pins_an_exact_size(self, format_id):
        """A range would let any roughly-right image pass `dimension_conformance`, which is the one
        feature whose whole value is catching a mis-sized asset."""
        _, (width, height) = EVALUABLE[format_id]
        assets = format_by_id(format_id)["assets"]
        slot = next(a for a in assets if a["asset_id"] == "main_image")
        requirements = slot["requirements"]
        assert (requirements["min_width"], requirements["max_width"]) == (width, width)
        assert (requirements["min_height"], requirements["max_height"]) == (height, height)
        assert slot["asset_type"] == "image"
        assert slot["required"] is True

    def test_the_fixture_exists_at_exactly_the_declared_size(self, format_id):
        filename, expected = EVALUABLE[format_id]
        path = Path(FIXTURES_DIR) / filename
        assert path.exists(), f"{filename} is missing; run fixtures/creatives/gen_fixtures.py"
        assert png_size(path) == expected

    def test_the_fixture_is_in_the_publish_list(self, format_id):
        """Present on disk but absent from `deploy_creative_fixtures.FIXTURE_FILES` means the evaluator
        gets a 404 for an asset that looks fine locally."""
        filename, _ = EVALUABLE[format_id]
        assert filename in {name for name, _ in FIXTURE_FILES}

    def test_a_product_offers_the_format(self, format_id):
        """An undiscoverable format cannot be booked, so the Creative panel would never show its card."""
        offering = [
            product
            for product in PRODUCTS
            if any(f.get("id") == format_id for f in product.get("format_ids", []))
        ]
        assert offering, f"no product offers {format_id}"


class TestTheTwoFormatsAreDistinct:
    def test_they_are_different_sizes(self):
        """The reason a second format was added: one card animating is not a scan, it is a single result.
        Two formats of the same size would add a card without adding a distinguishable measurement."""
        sizes = {size for _, size in EVALUABLE.values()}
        assert len(sizes) == len(EVALUABLE)

    def test_every_declared_format_id_is_unique(self):
        ids = [entry["format_id"]["id"] for entry in CREATIVE_FORMATS]
        assert len(ids) == len(set(ids)), f"duplicate format ids: {ids}"

    def test_pricing_option_ids_are_unique_across_products(self):
        """Copy-pasting a product is how two products end up sharing a pricing_option_id, which makes a
        booking ambiguous about what was actually priced."""
        option_ids = [
            option["pricing_option_id"]
            for product in PRODUCTS
            for option in product.get("pricing_options", [])
        ]
        assert len(option_ids) == len(set(option_ids)), option_ids
