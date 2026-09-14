"""Deterministic seed briefs whose ground truth is exact by construction.

The point of generating briefs from the catalogue's **own attribute values** is that the correct
answer needs no annotation: pick constraints, apply them to the catalogue, and the set of units that
survive *is* the ground truth. No labelling pass, no judgement, and nothing for an LLM to get wrong.

Two rules keep that accurate, and both are easy to lose:

**Ground truth is computed with the same strict-AND rule the runtime's filter applies.** A ground
truth computed more laxly than the retriever filters would score the retriever against a question
nobody asked - it would look like a recall failure when it is a disagreement about what the brief
meant.

**A brief's class must describe what it measures.** An `audience_led` brief that matches nothing
measures empty-answer behaviour while claiming to measure audience retrieval, so it is discarded and
another is drawn. Only a class the profile declares `expects_empty` may match nothing, and its
emptiness is **verified by computing it** rather than assumed from the look of the combination.

Phrasing
--------
Seed text is templated, not natural. It exists to state constraints the parser can extract;
naturalness is the variation stage's job. A seed that read beautifully but parsed to a different
constraint set than it was built from would poison its own ground truth. The profile's phrasings
deliberately use the catalogue's exact vocabulary values, because those are what the parser matches.

The random stream is per class, and that is a feature
----------------------------------------------------
Each class draws from `random.Random(f"{seed}:{class}")`. Raising `per_class` therefore **extends**
each class rather than regenerating it, so a larger corpus is a superset of the smaller one and
figures measured against the smaller remain comparable. A single shared stream would reshuffle every
class whenever any count changed.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from corpus_kit.profile import CorpusProfile, ProfileError
from corpus_kit.corpus_types import CorpusBrief, CorpusError, StatedConstraints

#: `unit -> CatalogueUnit`. The one seller-specific function the seed stage needs, and the reason
#: this module never imports a catalogue generator.
CatalogueAdapter = Callable[[Any], "CatalogueUnit"]


@dataclass(frozen=True)
class CatalogueUnit:
    """One inventory unit, flattened to what constraint evaluation needs.

    `values` maps an attribute name to either a single value or a tuple of them. The distinction is
    not cosmetic: a brief naming one topic must match a unit carrying that topic among several, which
    is a membership test rather than an equality test. Collapsing both into strings would make a
    multi-valued attribute unsatisfiable.
    """

    product_id: str
    values: Mapping[str, str | tuple[str, ...]]
    price: float

    def satisfies(self, constraints: StatedConstraints) -> bool:
        """Strict AND over every stated constraint.

        The same rule the runtime's filter applies: every constraint must hold, and none is relaxed
        to find something. A single `or` here would make the ground truth broader than the
        retriever's own answer set and every score meaningless.
        """
        for attribute, wanted in constraints.attributes:
            if attribute not in self.values:
                # An attribute the catalogue does not carry cannot be satisfied. Returning False
                # rather than ignoring it: ignoring would silently widen the ground truth to include
                # units that do not meet a constraint the brief stated.
                return False
            held = self.values[attribute]
            if isinstance(held, str):
                if held != wanted:
                    return False
            elif wanted not in held:
                return False
        if constraints.max_cpm is not None and self.price > constraints.max_cpm:
            return False
        return True


def build_vocabulary(units: Sequence[CatalogueUnit]) -> dict[str, tuple[str, ...]]:
    """Every value each attribute actually carries, sorted.

    Sorted because the generator draws from these lists and determinism requires the lists themselves
    to be stable - a set's iteration order is not.
    """
    collected: dict[str, set[str]] = {}
    for unit in units:
        for attribute, held in unit.values.items():
            if isinstance(held, str):
                collected.setdefault(attribute, set()).add(held)
            else:
                collected.setdefault(attribute, set()).update(held)
    return {attribute: tuple(sorted(values)) for attribute, values in collected.items()}


def expected_for(
    constraints: StatedConstraints, units: Sequence[CatalogueUnit]
) -> frozenset[str]:
    """The units satisfying `constraints`, under the same strict AND.

    Exposed so a test can compute ground truth independently of the generator and compare - the
    oracle property. A generator that computed its own ground truth with a bug would otherwise agree
    with itself perfectly.
    """
    return frozenset(unit.product_id for unit in units if unit.satisfies(constraints))


class SeedBriefGenerator:
    """Deterministic seed briefs, one class at a time, ground truth by construction.

    Stateless apart from the seed and the profile. Two instances with the same pair produce identical
    corpora, which is asserted rather than assumed.
    """

    def __init__(self, *, profile: CorpusProfile, seed: int) -> None:
        self._profile = profile
        self._seed = seed

    @property
    def seed(self) -> int:
        return self._seed

    @property
    def profile(self) -> CorpusProfile:
        return self._profile

    def generate(
        self, units: Sequence[CatalogueUnit], per_class: int
    ) -> list[CorpusBrief]:
        """`per_class` briefs in each of the profile's classes, in profile order.

        Raises rather than returning a short corpus if a class cannot be filled: a corpus quietly
        missing a class would report an aggregate that looked fine while measuring nothing about the
        axis that class exists to test.
        """
        if per_class <= 0:
            raise CorpusError("per_class must be positive")
        if not units:
            raise CorpusError("an empty catalogue has no ground truth to compute")

        vocabulary = build_vocabulary(units)
        briefs: list[CorpusBrief] = []

        for spec in self._profile.classes:
            # A separate stream per class, seeded from the class name, so adding a class or changing
            # `per_class` for one does not shift the briefs in the others.
            rng = random.Random(f"{self._seed}:{spec.name}")
            drawn = self._draw_class(spec, units, vocabulary, per_class, rng)
            if len(drawn) < per_class:
                raise CorpusError(
                    f"could only build {len(drawn)} of {per_class} {spec.name} briefs after "
                    f"{self._profile.max_draws_per_brief} draws each. A short class would report an "
                    "aggregate that looked fine while measuring nothing about that class."
                )
            briefs.extend(drawn)
        return briefs

    def _draw_class(
        self,
        spec,
        units: Sequence[CatalogueUnit],
        vocabulary: Mapping[str, tuple[str, ...]],
        count: int,
        rng: random.Random,
    ) -> list[CorpusBrief]:
        briefs: list[CorpusBrief] = []
        seen_constraints: set[StatedConstraints] = set()

        for index in range(count):
            for _ in range(self._profile.max_draws_per_brief):
                constraints = self._draw_constraints(spec, vocabulary, rng)
                if constraints in seen_constraints:
                    # A duplicate constraint set is a duplicate case. Two identical briefs would
                    # double-count one measurement, which quietly weights whatever they happen to
                    # test.
                    continue
                matched = expected_for(constraints, units)
                if bool(matched) == spec.expects_empty:
                    continue
                seen_constraints.add(constraints)
                brief = CorpusBrief(
                    brief_id=f"{spec.name}-{index:04d}",
                    text=self._profile.compose(constraints),
                    brief_class=spec.name,
                    expected_product_ids=matched,
                    constraints=constraints,
                    origin="seed",
                )
                # The class rule, applied at the point of creation. `expects_empty` was just checked
                # against a computed match set, so this can only fire if the profile disagrees with
                # itself - which is exactly the case worth failing on.
                self._profile.validate(brief)
                briefs.append(brief)
                break
        return briefs

    def _draw_constraints(
        self,
        spec,
        vocabulary: Mapping[str, tuple[str, ...]],
        rng: random.Random,
    ) -> StatedConstraints:
        """Constraints for one draw, following the class's declared draws in order.

        An `expects_empty` class reaches an empty intersection by drawing *more* constraints than the
        others plus the tightest ceiling on the list - rather than by composing an impossible value,
        which would test the parser's handling of unknown terms instead of retrieval.
        """
        attributes: dict[str, str] = {}
        for axis, minimum, maximum in spec.draws:
            attributes.update(
                self._pick(
                    self._profile.attributes_for(axis),
                    vocabulary,
                    rng,
                    minimum=minimum,
                    maximum=maximum,
                )
            )

        max_cpm: float | None = None
        if spec.budget == "floor":
            # **Deliberately does not touch `rng`.** Expressing this as `probabilistic` with p=1.0
            # would consume a draw, and every brief after it in this class would change. See
            # `BudgetRule`.
            max_cpm = min(self._profile.cpm_ceilings)
        elif spec.budget == "probabilistic":
            # `rng.random()` unconditionally, then `rng.choice` only on success, so the stream
            # advances by the same amount whether or not a budget is stated.
            if rng.random() < self._profile.budget_probability:
                max_cpm = rng.choice(self._profile.cpm_ceilings)

        return StatedConstraints(
            attributes=tuple(sorted(attributes.items())),
            max_cpm=max_cpm,
        )

    @staticmethod
    def _pick(
        pool: Iterable[str],
        vocabulary: Mapping[str, tuple[str, ...]],
        rng: random.Random,
        *,
        minimum: int,
        maximum: int,
    ) -> dict[str, str]:
        """`minimum`..`maximum` attributes from `pool`, each with a value the catalogue carries.

        Attributes the catalogue has no values for are skipped rather than drawn with a substituted
        value: a constraint naming a value no unit carries would make every brief in that class match
        nothing, and the class label would then be a lie.
        """
        available = sorted(attribute for attribute in pool if vocabulary.get(attribute))
        if len(available) < minimum:
            raise ProfileError(
                f"the catalogue carries values for only {len(available)} of the requested "
                f"attributes {sorted(pool)!r}; cannot state {minimum}"
            )
        how_many = rng.randint(minimum, min(maximum, len(available)))
        chosen = rng.sample(available, how_many)
        return {attribute: rng.choice(vocabulary[attribute]) for attribute in sorted(chosen)}


__all__ = [
    "CatalogueAdapter",
    "CatalogueUnit",
    "SeedBriefGenerator",
    "build_vocabulary",
    "expected_for",
]
