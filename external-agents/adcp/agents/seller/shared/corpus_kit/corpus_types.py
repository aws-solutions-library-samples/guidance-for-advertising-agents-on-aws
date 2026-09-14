"""The corpus record and the report shapes. Types only - no generation, no measurement.

**Offline package.** Nothing here is importable from a request path and none of it ships in a
container image. A retriever must not be able to read the ground truth it is scored against.

Why the record carries its own provenance
-----------------------------------------
`origin` and `seed_brief_id` are fields, not metadata kept somewhere alongside. A corpus where
provenance lived in a separate manifest would let a record be copied, filtered or re-sorted into a
state where nobody could say whether its ground truth was computed or inherited - and an inherited
ground truth is only as good as the validation that let it be inherited.

What moved out of here, and why it is not a weakening
----------------------------------------------------
`CorpusBrief` used to hard-code four class names and the rule that only `negative` may expect
nothing. Both are statements about a *seller's catalogue*, not facts about corpora, so they now live
on `CorpusProfile` and are enforced by `CorpusProfile.validate`.

The rule still fires on every path a brief can arrive by - the seed generator, the variation
validator, and `read_briefs` on the way back in - which is why moving it costs nothing. What it buys
is a second seller that does not have to add its class names to a shared enum before it can start.
`__post_init__` keeps the checks that hold for any corpus regardless of seller.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence

#: The classes Gotham established, offered as a starting point rather than a constraint. A profile
#: names its own; these are re-exported so a seller with the same shape can reuse them instead of
#: retyping them, and so the retail seller's corpus stays comparable to Gotham's if it chooses to.
CONVENTIONAL_BRIEF_CLASSES: tuple[str, ...] = (
    "audience_led",
    "content_led",
    "blended",
    "negative",
)

RejectionReason = Literal["constraints_differ", "unparsed_phrase", "empty"]


class CorpusError(Exception):
    """A corpus record could not be built or read."""


@dataclass(frozen=True)
class StatedConstraints:
    """What a brief states, in the parser's own terms.

    Mirrors `context_cache.brief_parser.BriefConstraints`'s *comparable* content - `attributes` and
    `max_cpm` - and deliberately **not** its `all_matches` field. `all_matches` is a diagnostic
    record of everything the parser saw including duplicates it dropped; comparing it would reject a
    variation for matching the same term twice, which is a phrasing difference and not a constraint
    difference.

    Frozen and hashable so equality is the whole of the comparison variation validation turns on.
    """

    attributes: tuple[tuple[str, str], ...] = ()
    max_cpm: float | None = None

    @classmethod
    def from_parsed(cls, parsed: Any) -> StatedConstraints:
        """From a `BriefConstraints`.

        `attributes` is sorted, so two parses that found the same constraints in a different order
        compare equal. Without the sort, "parses to the same constraint set" would depend on the
        order terms happen to appear in a sentence - which is exactly the phrasing difference
        variations exist to explore.
        """
        attributes = dict(getattr(parsed, "attributes", {}) or {})
        max_cpm = getattr(parsed, "max_cpm", None)
        return cls(
            attributes=tuple(sorted((str(key), str(value)) for key, value in attributes.items())),
            max_cpm=None if max_cpm is None else float(max_cpm),
        )

    @property
    def attribute_map(self) -> dict[str, str]:
        return dict(self.attributes)

    @property
    def stated_count(self) -> int:
        """How many constraints were stated. The budget counts as one, as the parser does."""
        return len(self.attributes) + (1 if self.max_cpm is not None else 0)

    @property
    def is_empty(self) -> bool:
        return self.stated_count == 0

    def to_dict(self) -> dict[str, Any]:
        return {"attributes": self.attribute_map, "max_cpm": self.max_cpm}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> StatedConstraints:
        attributes = payload.get("attributes") or {}
        if not isinstance(attributes, Mapping):
            raise CorpusError("constraints.attributes must be an object")
        max_cpm = payload.get("max_cpm")
        return cls(
            attributes=tuple(sorted((str(key), str(value)) for key, value in attributes.items())),
            max_cpm=None if max_cpm is None else float(max_cpm),
        )


@dataclass(frozen=True)
class CorpusBrief:
    """One scoring case: a brief, its class, and the exact set of products that satisfy it.

    `expected_product_ids` may legitimately be **empty** - that is an empty-expecting class, and an
    empty expectation is a meaningful statement rather than missing data. Whether *this* brief's
    class may be empty is the profile's call; see `CorpusProfile.validate`.
    """

    brief_id: str
    text: str
    brief_class: str
    expected_product_ids: frozenset[str]
    constraints: StatedConstraints
    origin: Literal["seed", "variation"] = "seed"
    seed_brief_id: str | None = None

    def __post_init__(self) -> None:
        # Only the seller-independent invariants. The class rule needs a profile and is applied by
        # every caller that has one.
        if not self.brief_id.strip():
            raise CorpusError("a brief must have an id")
        if not self.text.strip():
            raise CorpusError(f"{self.brief_id}: a brief must have text")
        if self.origin == "variation" and not self.seed_brief_id:
            raise CorpusError(f"{self.brief_id}: a variation must name the seed it came from")
        if self.origin == "seed" and self.seed_brief_id is not None:
            raise CorpusError(f"{self.brief_id}: a seed cannot name a seed of its own")

    @property
    def expects_nothing(self) -> bool:
        return not self.expected_product_ids

    def to_dict(self) -> dict[str, Any]:
        """A JSON-ready record. Ids **sorted**, so the corpus file is byte-stable."""
        return {
            "brief_id": self.brief_id,
            "text": self.text,
            "brief_class": self.brief_class,
            "expected_product_ids": sorted(self.expected_product_ids),
            "constraints": self.constraints.to_dict(),
            "origin": self.origin,
            "seed_brief_id": self.seed_brief_id,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> CorpusBrief:
        """Required keys are **indexed, not fetched with a default**.

        Defaulting a missing `constraints` to an empty set would score every case as having no
        constraints and report a filter precision of 1.0 - a perfect number produced by measuring
        nothing. A missing key must fail loudly.
        """
        try:
            return cls(
                brief_id=str(payload["brief_id"]),
                text=str(payload["text"]),
                brief_class=str(payload["brief_class"]),
                expected_product_ids=frozenset(
                    str(pid) for pid in payload["expected_product_ids"]
                ),
                constraints=StatedConstraints.from_dict(payload["constraints"]),
                origin=payload["origin"],
                seed_brief_id=payload.get("seed_brief_id"),
            )
        except KeyError as exc:
            raise CorpusError(
                f"corpus record is missing required key {exc}; defaulting it would score the case "
                "against nothing"
            ) from exc


@dataclass(frozen=True)
class RejectedVariation:
    """A variation that did not inherit its seed's ground truth, and why.

    **Both constraint sets are kept**, so a rejection can be inspected rather than trusted. Without
    them the quarantine file would say "rejected: constraints_differ" and nobody could tell whether
    the parser or the paraphrase was at fault.
    """

    seed_brief_id: str
    text: str
    reason: RejectionReason
    seed_constraints: StatedConstraints
    variation_constraints: StatedConstraints

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed_brief_id": self.seed_brief_id,
            "text": self.text,
            "reason": self.reason,
            "seed_constraints": self.seed_constraints.to_dict(),
            "variation_constraints": self.variation_constraints.to_dict(),
        }


@dataclass(frozen=True)
class ValidationOutcome:
    accepted: tuple[CorpusBrief, ...] = ()
    rejected: tuple[RejectedVariation, ...] = ()

    @property
    def offered(self) -> int:
        """Every variation is either accepted or rejected. Nothing is silently dropped."""
        return len(self.accepted) + len(self.rejected)


@dataclass(frozen=True)
class ClassMetrics:
    """The four metrics for one brief class, or for the corpus as a whole.

    `cases` is part of the record, not a footnote: an unmeasured figure must be visibly unmeasured,
    and a `0.0` next to `cases: 0` reads correctly where a bare `0.0` does not.

    `scored_cases` is separate from `cases` because recall and MRR are **undefined** over an empty
    expectation, so empty-expecting cases contribute to `empty_answer_rate` and to nothing else.
    """

    brief_class: str
    cases: int
    scored_cases: int
    recall_at_10: float
    mrr: float
    filter_precision: float
    empty_answer_rate: float
    #: `found / min(10, |expected|)`. Reported **alongside** classic recall, not instead of it.
    #:
    #: Classic `recall@10` has a hard ceiling a catalogue-derived corpus routinely hits: a brief
    #: whose ground truth is 694 units caps it at 0.014 for a *perfect* retriever, and a report
    #: showing only that reads as catastrophic retrieval where it is arithmetic. Showing only the
    #: achievable figure would hide that the brief matched far more inventory than a buyer can see.
    #: Both, named.
    achievable_recall_at_10: float = 0.0
    #: How many cases contributed a filter judgement. `filter_precision` over zero of them is not a
    #: measured 0.0, and this is how a reader can tell.
    filter_judged_cases: int = 0
    #: Mean ground-truth size over scored cases, so a reader can see whether classic recall was
    #: ceiling-bound rather than having to guess.
    mean_expected_size: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "brief_class": self.brief_class,
            "cases": self.cases,
            "scored_cases": self.scored_cases,
            "recall_at_10": self.recall_at_10,
            "achievable_recall_at_10": self.achievable_recall_at_10,
            "mrr": self.mrr,
            "filter_precision": self.filter_precision,
            "empty_answer_rate": self.empty_answer_rate,
            "filter_judged_cases": self.filter_judged_cases,
            "mean_expected_size": self.mean_expected_size,
            "unmeasured": self.unmeasured,
        }

    @property
    def unmeasured(self) -> list[str]:
        """Which of these figures had nothing to measure.

        Named explicitly rather than left to the reader to infer from a zero denominator. A `0.0`
        that means "measured, and bad" and a `0.0` that means "nothing to measure" are opposite
        facts wearing the same number.
        """
        unmeasured: list[str] = []
        if self.scored_cases == 0:
            unmeasured.extend(["recall_at_10", "achievable_recall_at_10", "mrr"])
        if self.filter_judged_cases == 0:
            unmeasured.append("filter_precision")
        if self.cases == 0:
            unmeasured.append("empty_answer_rate")
        return unmeasured


@dataclass(frozen=True)
class QualityReport:
    """The report. **No verdict.**

    There is no threshold anywhere in this type and no pass/fail field. No prior measurement exists
    from which a bar could be set, and an arbitrary gate is worse than none: it prints PASS against a
    number nobody chose on evidence. A human reads the numbers.
    """

    artifact_version: str
    measured_at: str
    per_class: tuple[ClassMetrics, ...]
    aggregate: ClassMetrics
    #: One class measured through a deliberately narrowed retrieval path, to answer "does this axis
    #: earn its place". `None` when the artifact carries no second axis, in which case the comparison
    #: is meaningless rather than zero.
    audience_led_content_only_baseline: ClassMetrics | None = None
    #: Seeds, accepted variations, rejected variations, and rejections by reason. Part of the report
    #: because a quality figure is only interpretable next to how many variations were discarded to
    #: get it.
    corpus_composition: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_version": self.artifact_version,
            "measured_at": self.measured_at,
            "aggregate": self.aggregate.to_dict(),
            "per_class": [metrics.to_dict() for metrics in self.per_class],
            "audience_led_content_only_baseline": (
                self.audience_led_content_only_baseline.to_dict()
                if self.audience_led_content_only_baseline is not None
                else None
            ),
            "corpus_composition": dict(self.corpus_composition),
            "note": (
                "This report has no verdict. There is no threshold in it and no pass/fail field: no "
                "prior measurement exists from which a bar could be set, and an arbitrary gate "
                "would print PASS against a number nobody chose on evidence."
            ),
        }


def write_jsonl(records: Sequence[Mapping[str, Any]], path) -> None:
    """One JSON object per line, keys sorted.

    `sort_keys=True` and a trailing newline per record, so regenerating the same corpus produces a
    byte-identical file. Without the sort, byte-stability would depend on dict insertion order and
    the determinism claim would be true only by accident.
    """
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")


def read_jsonl(path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise CorpusError(f"{path} line {line_number} is not valid JSON: {exc}") from exc
    return records


def read_briefs(path, profile=None) -> list[CorpusBrief]:
    """Corpus records back into briefs, **re-validated against the profile** when one is given.

    Reading is the third path a brief can arrive by, after seed generation and variation validation,
    and it is the one that crosses a process boundary - so it is the one where a hand-edited or
    stale-schema corpus shows up. Validating here is what lets `CorpusBrief.__post_init__` stay
    seller-agnostic without the class rule going unchecked on the way in.

    `profile=None` reads without the class check, for tooling that inspects a corpus it has no
    profile for. Measurement always passes one.
    """
    briefs = [CorpusBrief.from_dict(record) for record in read_jsonl(path)]
    if profile is not None:
        for brief in briefs:
            profile.validate(brief)
    return briefs


__all__ = [
    "CONVENTIONAL_BRIEF_CLASSES",
    "ClassMetrics",
    "CorpusBrief",
    "CorpusError",
    "QualityReport",
    "RejectedVariation",
    "RejectionReason",
    "StatedConstraints",
    "ValidationOutcome",
    "read_briefs",
    "read_jsonl",
    "write_jsonl",
]
