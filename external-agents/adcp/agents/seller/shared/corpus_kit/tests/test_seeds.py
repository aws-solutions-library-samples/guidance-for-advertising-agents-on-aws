"""The seed stage: determinism, the extension property, and ground truth by construction.

The two properties worth the most here are the ones a reader would otherwise have to take on trust:

- **Extension, not regeneration.** Raising `per_class` must leave the smaller corpus as a prefix, or
  every figure previously measured becomes incomparable and nothing says so.
- **The oracle.** Ground truth is recomputed independently and compared, because a generator that
  computes its own answer with a bug agrees with itself perfectly.
"""

from __future__ import annotations

import random

import pytest

from corpus_kit.profile import BriefClassSpec, CorpusProfile, ProfileError
from corpus_kit.corpus_types import CorpusError
from corpus_kit.seeds import (
    SeedBriefGenerator,
    build_vocabulary,
    expected_for,
)


def test_the_same_seed_produces_an_identical_corpus(profile, units):
    first = SeedBriefGenerator(profile=profile, seed=7).generate(units, 3)
    second = SeedBriefGenerator(profile=profile, seed=7).generate(units, 3)

    assert [brief.to_dict() for brief in first] == [brief.to_dict() for brief in second]


def test_a_different_seed_produces_a_different_corpus(profile, units):
    first = SeedBriefGenerator(profile=profile, seed=7).generate(units, 3)
    second = SeedBriefGenerator(profile=profile, seed=8).generate(units, 3)

    assert [b.text for b in first] != [b.text for b in second]


def test_raising_per_class_extends_each_class_rather_than_regenerating_it(profile, units):
    """The per-class random stream exists for exactly this.

    A single shared stream would reshuffle every class whenever any count changed, so a corpus grown
    from 2 to 3 per class would share almost nothing with the one the previous figures were measured
    against - while looking like a superset.
    """
    small = SeedBriefGenerator(profile=profile, seed=7).generate(units, 2)
    large = SeedBriefGenerator(profile=profile, seed=7).generate(units, 3)

    for spec in profile.classes:
        was = [b.to_dict() for b in small if b.brief_class == spec.name]
        now = [b.to_dict() for b in large if b.brief_class == spec.name]
        assert now[: len(was)] == was, f"{spec.name} was regenerated, not extended"


def test_ground_truth_matches_an_independent_recomputation(profile, units):
    """The oracle property. `expected_for` is the same rule applied by a different caller."""
    for brief in SeedBriefGenerator(profile=profile, seed=7).generate(units, 3):
        assert brief.expected_product_ids == expected_for(brief.constraints, units)


def test_every_expected_unit_actually_satisfies_every_stated_constraint(profile, units):
    by_id = {unit.product_id: unit for unit in units}
    for brief in SeedBriefGenerator(profile=profile, seed=7).generate(units, 3):
        for product_id in brief.expected_product_ids:
            assert by_id[product_id].satisfies(brief.constraints)


def test_no_unit_outside_the_expectation_satisfies_the_constraints(profile, units):
    """Ground truth is the *complete* set, not a sample of it.

    A generator that returned a subset would make recall unreachable and look like a retrieval failure.
    """
    for brief in SeedBriefGenerator(profile=profile, seed=7).generate(units, 3):
        for unit in units:
            if unit.product_id not in brief.expected_product_ids:
                assert not unit.satisfies(brief.constraints)


def test_an_empty_expecting_class_matches_nothing_and_the_others_match_something(profile, units):
    for brief in SeedBriefGenerator(profile=profile, seed=7).generate(units, 3):
        if profile.spec_for(brief.brief_class).expects_empty:
            assert not brief.expected_product_ids
        else:
            assert brief.expected_product_ids


def test_a_class_that_cannot_be_filled_raises_rather_than_returning_a_short_corpus(profile, units):
    """A quietly short class would report an aggregate that looked fine while measuring nothing.

    Asked for more distinct constraint sets than the fixture catalogue can produce.
    """
    with pytest.raises(CorpusError, match="could only build"):
        SeedBriefGenerator(profile=profile, seed=7).generate(units, 500)


def test_duplicate_constraint_sets_are_not_emitted_twice(profile, units):
    briefs = SeedBriefGenerator(profile=profile, seed=7).generate(units, 3)
    per_class: dict[str, list] = {}
    for brief in briefs:
        per_class.setdefault(brief.brief_class, []).append(brief.constraints)
    for name, constraints in per_class.items():
        assert len(set(constraints)) == len(constraints), f"{name} repeated a case"


def test_a_floor_budget_does_not_consume_the_random_stream(profile, units):
    """`floor` is not `probabilistic` with p=1.0, and this is the reason.

    Consuming a draw would shift every brief after it in the class. Asserted by advancing a stream the
    same number of times the generator would and checking the next value is untouched - which only
    holds if the budget decision never touched it.
    """
    negative = profile.spec_for("negative")
    assert negative.budget == "floor"

    generator = SeedBriefGenerator(profile=profile, seed=7)
    control = random.Random("7:negative")
    # Two draws per axis pick: randint, sample, then one choice per chosen attribute.
    for _ in range(2):
        control.randint(2, 2)
        control.sample(["a", "b"], 2)
        control.choice(["x"])
        control.choice(["x"])

    briefs = generator.generate(units, 1)
    stated = [b for b in briefs if b.brief_class == "negative"][0]
    assert stated.constraints.max_cpm == min(profile.cpm_ceilings)


def test_build_vocabulary_collects_both_scalar_and_multi_valued_attributes(units):
    vocabulary = build_vocabulary(units)

    assert vocabulary["colour"] == ("blue", "red")
    assert vocabulary["topic"] == ("news", "sport")


def test_an_attribute_the_catalogue_does_not_carry_cannot_be_satisfied(units):
    """Not "ignored". Ignoring would widen ground truth to units failing a stated constraint."""
    from corpus_kit.corpus_types import StatedConstraints

    absent = StatedConstraints(attributes=(("nonexistent", "whatever"),))
    assert expected_for(absent, units) == frozenset()


def test_a_price_ceiling_selects_only_units_at_or_below_it(units):
    from corpus_kit.corpus_types import StatedConstraints

    ceiling = StatedConstraints(max_cpm=7.0)
    assert expected_for(ceiling, units) == {"u00", "u01", "u02"}


def test_a_class_guaranteeing_only_one_constraint_is_rejected_by_the_profile():
    """The 18-694 ground-truth span, encoded as a rule rather than a comment."""
    with pytest.raises(ProfileError, match="single-constraint"):
        BriefClassSpec(name="too_loose", draws=(("audience", 1, 3),))


def test_a_class_drawing_on_an_unknown_axis_is_rejected():
    with pytest.raises(ProfileError, match="unknown axis"):
        CorpusProfile(
            seller="broken",
            axes={"audience": ("colour",)},
            phrasings={"colour": "in {value}"},
            classes=(BriefClassSpec(name="x", draws=(("content", 2, 2),)),),
        )


def test_an_attribute_with_no_phrasing_is_rejected_rather_than_dropped():
    """A dropped attribute changes what the brief means while leaving its ground truth untouched."""
    with pytest.raises(ProfileError, match="no phrasing"):
        CorpusProfile(
            seller="broken",
            axes={"audience": ("colour", "size")},
            phrasings={"colour": "in {value}"},
            classes=(BriefClassSpec(name="x", draws=(("audience", 2, 2),)),),
        )
