"""A synthetic catalogue and a matching profile, so the kit is testable without a seller.

The catalogue here is deliberately tiny and hand-written rather than drawn from a generator. Every test
below asserts something about *which* units a constraint selects, and a fixture whose contents you can
hold in your head is the difference between a test that fails informatively and one that fails.

`corpus_kit` is not installed, so the package's parent goes on `sys.path`. Pytest inserts the directory
holding the test file (there is no `__init__.py` here, deliberately), which is one level too deep.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest  # noqa: E402

from corpus_kit.profile import BriefClassSpec, CorpusProfile  # noqa: E402
from corpus_kit.seeds import CatalogueUnit  # noqa: E402

#: Ten units over two axes. `colour` and `size` describe the *audience*; `topic` (multi-valued) and
#: `section` describe the *content*. Prices ascend so a ceiling selects a known prefix.
UNITS = tuple(
    CatalogueUnit(
        product_id=f"u{index:02d}",
        values={
            "colour": ("red", "blue")[index % 2],
            "size": ("small", "large")[index % 2],
            "topic": (("news", "sport") if index < 5 else ("sport",)),
            "section": ("front", "back")[index // 5],
        },
        price=5.0 + index,
    )
    for index in range(10)
)


@pytest.fixture
def units():
    return UNITS


@pytest.fixture
def profile():
    """Two single-axis classes, one blended, one empty-expecting. Gotham's shape, minus the size."""
    return CorpusProfile(
        seller="fixture",
        axes={"audience": ("colour", "size"), "content": ("topic", "section")},
        phrasings={
            "colour": "in {value}",
            "size": "{value} units",
            "topic": "about {value}",
            "section": "in the {value} section",
        },
        classes=(
            BriefClassSpec(name="audience_led", draws=(("audience", 2, 2),)),
            BriefClassSpec(name="content_led", draws=(("content", 2, 2),)),
            BriefClassSpec(name="blended", draws=(("audience", 1, 1), ("content", 1, 1))),
            BriefClassSpec(
                name="negative",
                draws=(("audience", 2, 2), ("content", 2, 2)),
                expects_empty=True,
                budget="floor",
            ),
        ),
        cpm_ceilings=(6.0, 9.0, 20.0),
    )


class StubParser:
    """Stands in for the runtime's `BriefParser`.

    Maps a text to a recorded parse. Anything unrecorded parses to nothing, which is how the
    "the paraphrase abandoned the vocabulary" rejection is reached.
    """

    def __init__(self, parses=None, raises_on=()):
        self._parses = dict(parses or {})
        self._raises_on = set(raises_on)

    def parse(self, text):
        if text in self._raises_on:
            raise ValueError("stub was asked to fail")
        return self._parses.get(text, _Parsed({}, None))

    def record(self, text, attributes, max_cpm=None):
        self._parses[text] = _Parsed(dict(attributes), max_cpm)
        return self


class _Parsed:
    """The two fields `StatedConstraints.from_parsed` reads. Not the runtime type, on purpose:
    duck-typing here keeps the tests free of the runtime package."""

    def __init__(self, attributes, max_cpm):
        self.attributes = attributes
        self.max_cpm = max_cpm


@pytest.fixture
def stub_parser():
    return StubParser
