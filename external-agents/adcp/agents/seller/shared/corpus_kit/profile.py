"""What a seller has to say about itself for the kit to build it a corpus.

Everything in `corpus_kit` other than this module is seller-agnostic. A new seller writes one
`CorpusProfile` and gets seed generation, LLM phrasing variations, validation and scoring without
touching the engine — which is the whole reason the engine was lifted out of the Gotham seller's
tree in the first place.

Data, not callables
-------------------
`phrasings` holds format strings (`"in the {value} section"`) rather than lambdas, and a class is a
tuple of `(axis, minimum, maximum)` draws rather than a branch in an `if`. Both were lambdas and
branches in the original; both are now values. A profile that is data can be printed into a
provenance record, compared between runs, and read by someone deciding whether two sellers' corpora
are comparable. A profile made of code can only be executed.

The class vocabulary belongs here, not in `types`
-------------------------------------------------
Gotham's four classes are a reasonable default for an inventory seller and the retail seller will
very likely reuse them, but "which classes exist, and which of them expect an empty answer" is a
statement about a seller's catalogue rather than a fact about corpora. `types.CorpusBrief` therefore
validates only what is universally true — a brief has text, a variation names its seed — and the
class rule is enforced here, by `validate`, at every point a brief is created or read back.

That split is deliberate and it is not a weakening: the rule fires in the seed generator, in the
variation validator and in the corpus reader, which is every path a brief can arrive by. What it
stops is a second seller having to add its class names to a shared enum before it can begin.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping, Sequence

#: How a class states a price ceiling.
#:
#: `floor` exists for the `negative` class, where a deliberately tight ceiling is one of the things
#: that makes the constraint intersection empty. It is **not** `probabilistic` with p=1.0: that would
#: consume a draw from the random stream, and every brief in every later class would shift. The
#: distinction is load-bearing for reproducibility, not stylistic.
BudgetRule = Literal["never", "probabilistic", "floor"]


class ProfileError(Exception):
    """A profile could not be used to build a corpus."""


@dataclass(frozen=True)
class BriefClassSpec:
    """One brief class: what it draws, whether it should match nothing, and how it prices.

    `draws` is **ordered**, and the order is part of the reproducibility contract. Each entry
    consumes a known number of values from the class's random stream, so reordering two draws
    produces a different corpus from the same seed. Gotham's `blended` draws audience then content;
    swapping those two lines would silently invalidate every figure previously measured against it.
    """

    name: str
    #: `(axis, minimum, maximum)` per draw, in the order the random stream sees them.
    draws: tuple[tuple[str, int, int], ...]
    #: Whether this class's ground truth must be **empty**. Verified by evaluation against the
    #: catalogue, never assumed from the look of the constraints.
    expects_empty: bool = False
    budget: BudgetRule = "probabilistic"

    def __post_init__(self) -> None:
        if not self.name:
            raise ProfileError("a brief class needs a name")
        if not self.draws:
            raise ProfileError(
                f"{self.name}: a class with no draws would state no constraints, and a brief with "
                "no constraints matches the whole catalogue"
            )
        for axis, minimum, maximum in self.draws:
            if minimum < 1 or maximum < minimum:
                raise ProfileError(
                    f"{self.name}: draw on {axis!r} has an impossible range {minimum}..{maximum}"
                )
        # **Two constraints minimum across the whole class, not one.**
        #
        # A one-constraint brief matches enormously. Gotham's first run drew audience-led briefs whose
        # ground truth spanned 18-694 of 2,166 units, which makes classic `recall@10` arithmetic
        # rather than a measurement: a *perfect* retriever answering 694 correct units with 10 slots
        # scores 0.014, and the report reads as catastrophic retrieval. Two is also closer to what a
        # buyer writes -- nobody briefs "reaching news-obsessives" and stops.
        if sum(minimum for _, minimum, _ in self.draws) < 2:
            raise ProfileError(
                f"{self.name}: the draws guarantee only "
                f"{sum(minimum for _, minimum, _ in self.draws)} constraint(s). A single-constraint "
                "brief matches so much inventory that recall@10 becomes arithmetic rather than a "
                "measurement -- see the 18-694 ground-truth span this floor was added for."
            )


@dataclass(frozen=True)
class CorpusProfile:
    """A seller's corpus recipe.

    `axes` groups a catalogue's filterable attributes into the dimensions a brief reasons about
    (Gotham: who it reaches, what it is about). A class then draws from named axes, which is what
    makes `blended` expressible as data rather than as a special case.
    """

    seller: str
    #: Axis name -> the attribute names it contains.
    axes: Mapping[str, tuple[str, ...]]
    #: Attribute name -> a format string containing `{value}`. Every attribute a class can draw MUST
    #: appear here; a missing phrasing is an error rather than a skipped constraint, because a
    #: dropped constraint changes what the brief means while leaving its ground truth untouched.
    phrasings: Mapping[str, str]
    classes: tuple[BriefClassSpec, ...]
    #: Ceilings a brief may state, as round numbers a buyer would say. Drawn from a list rather than
    #: generated so the text reads "under $14 CPM" and not "under $13.87 CPM".
    cpm_ceilings: tuple[float, ...] = (8.0, 12.0, 14.0, 18.0, 25.0, 40.0)
    #: Chance a `probabilistic` class states a budget.
    budget_probability: float = 0.33
    opening: str = "Looking for "
    #: Text for a brief that states no attributes at all. Reachable only through a profile whose
    #: draws are all optional, which `BriefClassSpec` forbids -- kept so composition never returns "".
    empty_text: str = "Looking for inventory"
    #: Rendered with `{value:g}`, so `14.0` reads as `$14` rather than `$14.0`.
    budget_phrase: str = ", under ${value:g} CPM"
    #: Draw attempts before a class is declared unfillable. Needed because a randomly drawn
    #: constraint pair can legitimately match nothing, and for an `expects_empty` class the opposite.
    max_draws_per_brief: int = 200

    def __post_init__(self) -> None:
        if not self.classes:
            raise ProfileError(f"{self.seller}: a profile needs at least one brief class")
        names = [spec.name for spec in self.classes]
        if len(set(names)) != len(names):
            raise ProfileError(f"{self.seller}: duplicate brief class names in {names}")
        for spec in self.classes:
            for axis, _, _ in spec.draws:
                if axis not in self.axes:
                    raise ProfileError(
                        f"{self.seller}: class {spec.name!r} draws on unknown axis {axis!r}; "
                        f"axes are {sorted(self.axes)}"
                    )
        for axis, attributes in self.axes.items():
            for attribute in attributes:
                if attribute not in self.phrasings:
                    raise ProfileError(
                        f"{self.seller}: axis {axis!r} carries attribute {attribute!r} with no "
                        "phrasing. A brief cannot state a constraint the profile has no words for -- "
                        "add a phrasing rather than dropping the attribute, because a dropped "
                        "attribute changes what the brief means."
                    )
        if not self.cpm_ceilings and any(
            spec.budget != "never" for spec in self.classes
        ):
            raise ProfileError(
                f"{self.seller}: a class prices its briefs but the profile lists no cpm_ceilings"
            )

    # -- lookups -------------------------------------------------------------

    @property
    def class_names(self) -> tuple[str, ...]:
        """In profile order, which is the order briefs are emitted in.

        Emission order is part of byte-stability (BR-2.1): the corpus file is the classes
        concatenated, so reordering this tuple rewrites the file without changing its content.
        """
        return tuple(spec.name for spec in self.classes)

    @property
    def empty_expecting_classes(self) -> frozenset[str]:
        return frozenset(spec.name for spec in self.classes if spec.expects_empty)

    def spec_for(self, brief_class: str) -> BriefClassSpec:
        for spec in self.classes:
            if spec.name == brief_class:
                return spec
        raise ProfileError(
            f"{self.seller}: unknown brief class {brief_class!r}; have {list(self.class_names)}"
        )

    def attributes_for(self, axis: str) -> tuple[str, ...]:
        try:
            return tuple(self.axes[axis])
        except KeyError as exc:
            raise ProfileError(f"{self.seller}: unknown axis {axis!r}") from exc

    # -- phrasing ------------------------------------------------------------

    def phrase(self, attribute: str, value: str) -> str:
        try:
            template = self.phrasings[attribute]
        except KeyError as exc:
            raise ProfileError(
                f"{self.seller}: no phrasing for attribute {attribute!r}"
            ) from exc
        return template.format(value=value)

    def compose(self, constraints) -> str:
        """A brief's text from its constraints.

        Attributes in sorted order, so the same constraint set always produces the same sentence.
        That is what makes the corpus byte-stable across runs rather than stable-by-accident.
        """
        parts = [self.phrase(attribute, value) for attribute, value in constraints.attributes]
        text = self.opening + ", ".join(parts) if parts else self.empty_text
        if constraints.max_cpm is not None:
            text += self.budget_phrase.format(value=constraints.max_cpm)
        return text

    # -- validation ----------------------------------------------------------

    def validate(self, brief) -> None:
        """The class rule: a class that expects nothing must match nothing, and vice versa.

        Raises `CorpusError` rather than `ProfileError` — the brief is the thing that is wrong, and
        callers already handle corpus errors. Imported locally to keep this module free of a
        dependency on `types`, so a profile can be built and checked without the engine present.
        """
        from corpus_kit.corpus_types import CorpusError

        if brief.brief_class not in self.class_names:
            raise CorpusError(
                f"{brief.brief_id}: unknown brief class {brief.brief_class!r} for seller "
                f"{self.seller}; have {list(self.class_names)}"
            )
        expects_empty = self.spec_for(brief.brief_class).expects_empty
        if expects_empty and brief.expected_product_ids:
            raise CorpusError(
                f"{brief.brief_id}: a {brief.brief_class} brief must expect nothing, got "
                f"{len(brief.expected_product_ids)} products. A class declared to match nothing "
                "that actually matches inventory scores a correct retriever as wrong."
            )
        if not expects_empty and not brief.expected_product_ids:
            raise CorpusError(
                f"{brief.brief_id}: a {brief.brief_class} brief with no expected products would "
                "measure empty-answer behaviour while claiming to measure retrieval. Emit it as an "
                "empty-expecting class or discard it."
            )

    def to_dict(self) -> dict:
        """For the provenance record.

        A corpus is only comparable to another corpus built from the same recipe, so the recipe
        travels with it rather than living only in the code that happened to produce it.
        """
        return {
            "seller": self.seller,
            "axes": {axis: list(attributes) for axis, attributes in sorted(self.axes.items())},
            "phrasings": dict(sorted(self.phrasings.items())),
            "classes": [
                {
                    "name": spec.name,
                    "draws": [list(draw) for draw in spec.draws],
                    "expects_empty": spec.expects_empty,
                    "budget": spec.budget,
                }
                for spec in self.classes
            ],
            "cpm_ceilings": list(self.cpm_ceilings),
            "budget_probability": self.budget_probability,
        }


def axes_from_attributes(**axes: Sequence[str]) -> dict[str, tuple[str, ...]]:
    """Keyword sugar: `axes_from_attributes(audience=(...), content=(...))`."""
    return {name: tuple(attributes) for name, attributes in axes.items()}


__all__ = [
    "BriefClassSpec",
    "BudgetRule",
    "CorpusProfile",
    "ProfileError",
    "axes_from_attributes",
]
