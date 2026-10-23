"""The variation stage. The validator is the load-bearing part; the generator is plumbing.

If the validator is weak, every quality figure downstream describes something other than what was
asked, and it does so invisibly - the numbers still look like numbers. So most of this file is about
what must be *rejected*, not what is accepted.
"""

from __future__ import annotations

from corpus_kit.corpus_types import CorpusBrief, StatedConstraints
from corpus_kit.variations import (
    VariationGenerator,
    VariationValidator,
    _parse_variations,
)

SEED_CONSTRAINTS = StatedConstraints(
    attributes=(("colour", "red"), ("size", "small")), max_cpm=14.0
)


def _seed(profile):
    return CorpusBrief(
        brief_id="audience_led-0000",
        text="Looking for in red, small units, under $14 CPM",
        brief_class="audience_led",
        expected_product_ids=frozenset({"u00"}),
        constraints=SEED_CONSTRAINTS,
        origin="seed",
    )


# -- what must be accepted ---------------------------------------------------


def test_a_variation_parsing_to_identical_constraints_inherits_the_seeds_ground_truth(
    profile, stub_parser
):
    seed = _seed(profile)
    parser = stub_parser().record(
        "Red smalls, cap $14", {"colour": "red", "size": "small"}, 14.0
    )

    outcome = VariationValidator(parser, profile=profile).validate(seed, ["Red smalls, cap $14"])

    assert len(outcome.accepted) == 1
    accepted = outcome.accepted[0]
    assert accepted.expected_product_ids == seed.expected_product_ids
    assert accepted.constraints == seed.constraints
    assert accepted.origin == "variation"
    assert accepted.seed_brief_id == seed.brief_id


def test_an_accepted_variation_stores_the_seeds_constraints_not_its_own_parse(profile, stub_parser):
    """The two were just proven equal, so storing the variation's parse would be a second copy of one
    fact - and two copies can later disagree through a bug in this very check."""
    seed = _seed(profile)
    parser = stub_parser().record("x", {"size": "small", "colour": "red"}, 14.0)

    accepted = VariationValidator(parser, profile=profile).validate(seed, ["x"]).accepted[0]

    assert accepted.constraints is seed.constraints


# -- what must be rejected ---------------------------------------------------


def test_a_dropped_price_ceiling_is_rejected(profile, stub_parser):
    """The mundane, likely failure: "under $14 CPM" softened to "affordable".

    The variation is then a *broader* request whose correct answer set is strictly larger, so inheriting
    the seed's narrower ground truth would score correct results as wrong - and the obvious conclusion,
    "retrieval got worse", would be exactly backwards.
    """
    seed = _seed(profile)
    parser = stub_parser().record(
        "Affordable red smalls", {"colour": "red", "size": "small"}, None
    )

    outcome = VariationValidator(parser, profile=profile).validate(seed, ["Affordable red smalls"])

    assert not outcome.accepted
    assert outcome.rejected[0].reason == "constraints_differ"


def test_an_added_constraint_is_rejected_too(profile, stub_parser):
    """Identity, not subset. A *narrower* variation is as wrong as a broader one."""
    seed = _seed(profile)
    parser = stub_parser().record(
        "y", {"colour": "red", "size": "small", "section": "front"}, 14.0
    )

    outcome = VariationValidator(parser, profile=profile).validate(seed, ["y"])

    assert outcome.rejected[0].reason == "constraints_differ"


def test_a_paraphrase_that_parses_to_nothing_is_reported_as_unparsed_not_as_differing(
    profile, stub_parser
):
    """A different diagnosis and a different fix: the paraphrase abandoned the catalogue's vocabulary
    rather than stating something else."""
    seed = _seed(profile)
    outcome = VariationValidator(stub_parser(), profile=profile).validate(
        seed, ["Something entirely unlike the catalogue"]
    )

    assert outcome.rejected[0].reason == "unparsed_phrase"


def test_a_parser_that_raises_is_a_rejection_not_a_crash(profile, stub_parser):
    seed = _seed(profile)
    parser = stub_parser(raises_on=["boom"])

    outcome = VariationValidator(parser, profile=profile).validate(seed, ["boom"])

    assert outcome.rejected[0].reason == "unparsed_phrase"


def test_empty_text_is_rejected_as_empty(profile, stub_parser):
    outcome = VariationValidator(stub_parser(), profile=profile).validate(_seed(profile), ["   "])

    assert outcome.rejected[0].reason == "empty"


def test_nothing_is_silently_dropped(profile, stub_parser):
    """`accepted + rejected == offered`, as a property rather than a comment."""
    seed = _seed(profile)
    parser = stub_parser().record("keep", {"colour": "red", "size": "small"}, 14.0)
    offered = ["keep", "drop", "", "also drop"]

    outcome = VariationValidator(parser, profile=profile).validate(seed, offered)

    assert outcome.offered == len(offered)
    assert len(outcome.accepted) + len(outcome.rejected) == len(offered)


def test_a_rejection_keeps_both_constraint_sets_so_it_can_be_inspected(profile, stub_parser):
    seed = _seed(profile)
    parser = stub_parser().record("z", {"colour": "blue", "size": "small"}, 14.0)

    rejected = VariationValidator(parser, profile=profile).validate(seed, ["z"]).rejected[0]

    assert rejected.seed_constraints == seed.constraints
    assert rejected.variation_constraints.attribute_map["colour"] == "blue"


# -- ordering, which is what keeps the output byte-stable ---------------------


def test_vary_many_returns_results_aligned_to_the_input_order(profile):
    """Completion order would make the corpus file depend on which network call finished first.

    The slowest seed is submitted first, so a completion-ordered implementation would visibly reorder.
    """
    import time

    seeds = [
        CorpusBrief(
            brief_id=f"audience_led-{index:04d}",
            text=f"brief {index}",
            brief_class="audience_led",
            expected_product_ids=frozenset({"u00"}),
            constraints=SEED_CONSTRAINTS,
            origin="seed",
        )
        for index in range(6)
    ]

    def invoke(prompt: str) -> str:
        # Keyed off `Brief: `, not the word "brief" - the prompt's own first line contains it
        # ("Rewrite this advertising inventory brief in N different ways"), which a looser split picks
        # up instead and turns every call into a swallowed failure.
        index = int(prompt.split("Brief: brief ")[1].split("\n")[0])
        time.sleep(0.05 if index == 0 else 0.0)
        return f'["phrasing of {index}"]'

    generator = VariationGenerator(invoke=invoke, workers=4)
    results = generator.vary_many(seeds, 1)

    assert [texts[0] for texts in results] == [f"phrasing of {index}" for index in range(6)]


def test_a_failing_call_costs_variations_not_the_build(profile):
    seeds = [
        CorpusBrief(
            brief_id="audience_led-0000",
            text="brief 0",
            brief_class="audience_led",
            expected_product_ids=frozenset({"u00"}),
            constraints=SEED_CONSTRAINTS,
            origin="seed",
        )
    ]

    def invoke(prompt: str) -> str:
        raise RuntimeError("throttled")

    assert VariationGenerator(invoke=invoke, workers=2).vary_many(seeds, 3) == [[]]


# -- response parsing --------------------------------------------------------


def test_a_fenced_json_array_is_parsed():
    assert _parse_variations('```json\n["a", "b"]\n```', 2) == ["a", "b"]


def test_json_wrapped_in_prose_is_parsed():
    assert _parse_variations('Sure! ["a"] Hope that helps.', 1) == ["a"]


def test_more_strings_than_asked_for_are_truncated():
    assert _parse_variations('["a","b","c"]', 2) == ["a", "b"]


def test_unparseable_output_yields_nothing_rather_than_raising():
    """Safe to be tolerant here: every extracted string still faces the validator, so a mis-parse
    costs variations rather than corrupting ground truth."""
    assert _parse_variations("no json at all", 2) == []
    assert _parse_variations('{"not": "a list"}', 2) == []
    assert _parse_variations("", 2) == []
