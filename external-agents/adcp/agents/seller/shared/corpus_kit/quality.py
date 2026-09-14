"""G-16 `QualityReporter` — retrieval quality per brief class, with the baseline that makes the
dual-vector claim a number rather than an assertion.

**This module reports. It does not gate** (BR-2.18). There is no threshold in it and no pass/fail
field on `QualityReport`. Feature 1 established the refusal and the reasoning holds: no prior
measurement exists from which a bar could be set, and an arbitrary gate is worse than none —
it prints PASS against a number nobody chose on evidence. A human reads the numbers.

Four metrics, not one
---------------------
Inherited from Feature 1's `cache_pipeline/benchmark.py`, along with the reasoning:

| Metric | The question | The failure it exposes |
|---|---|---|
| `recall@10` | were the right units found at all | retrieval misses them entirely |
| `MRR` | were they ranked usefully | found, but buried below noise |
| `filter_precision` | were stated constraints honoured | returns inventory the buyer excluded |
| `empty_answer_rate` | how often nothing came back | the `negative` class tests exactly this |

`empty_answer_rate` earns its place because a retriever that never returns empty would score well on
recall while being wrong about every `negative` case. Without it, the aggregate flatters precisely the
wrong behaviour.

**Recall over an empty expectation is undefined, not zero.** A `negative` case contributes to
`empty_answer_rate` and to nothing else. Averaging a 0.0 into recall would report a case for behaving
*correctly* as a retrieval failure — the single most likely way to build a metric suite that lies.

The baseline (US-14 criterion 2, US-2 criterion 3)
--------------------------------------------------
The `audience_led` class is measured twice: once fused, once with the **audience axis withheld from
the same retrieval path** (BR-2.17). Not against a separately-written single-vector retriever, which
would measure the difference between two codebases rather than the contribution of one axis.

Withholding is done by passing `audience_weight=0.0`, which the fusion weight honours as a buyer
override — so the content vector orders the survivors alone, through the same filter, the same
ranker and the same artifact.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from corpus_kit.corpus_types import (
    ClassMetrics,
    CorpusBrief,
    CorpusError,
    QualityReport,
    StatedConstraints,
)

_log = logging.getLogger(__name__)

#: Rank cutoff for recall and MRR. Matches the seller's own default `limit`, so the measured number
#: describes the answer a buyer actually receives rather than a deeper list nobody sees.
CUTOFF = 10


@dataclass
class _Accumulator:
    """Running totals for one class, or for the corpus as a whole.

    Deliberately the same shape as `cache_pipeline.benchmark._Accumulator`, including the separate
    `expects_nothing_count`. Reimplemented rather than imported because that module is private to the
    audio seller's pipeline package and reaching into a private name across packages would couple two
    deploy units through an underscore. The *definitions* are shared by being identical and by this
    note; the equality is asserted in `tests/test_quality.py` against the same fixtures.
    """

    case_count: int = 0
    recall_sum: float = 0.0
    #: `found / min(CUTOFF, |expected|)` — "of the slots that *could* have been filled correctly, how
    #: many were". Reported alongside classic recall rather than instead of it, because the two answer
    #: different questions and classic recall has a ceiling this corpus routinely hits.
    #:
    #: A brief whose ground truth is 694 units caps classic `recall@10` at 0.014 for a **perfect**
    #: retriever. Reporting only that would read as catastrophic retrieval where it is arithmetic.
    #: Reporting only the achievable figure would hide that a brief matched far more inventory than a
    #: buyer can see. Both, named clearly.
    achievable_recall_sum: float = 0.0
    reciprocal_rank_sum: float = 0.0
    filter_hits: int = 0
    filter_total: int = 0
    empty_answers: int = 0
    expects_nothing_count: int = 0
    correct_empty: int = 0
    #: Summed ground-truth sizes, so a reader can see whether classic recall was ceiling-bound.
    expected_size_sum: int = 0

    def add(
        self, brief: CorpusBrief, returned: Sequence[str], filter_ok: bool | None
    ) -> None:
        self.case_count += 1
        if not returned:
            self.empty_answers += 1

        if brief.expects_nothing:
            self.expects_nothing_count += 1
            if not returned:
                self.correct_empty += 1
            # No recall or MRR contribution: both are undefined against an empty expectation.
            # `empty_answer_rate` is where this case's behaviour shows up.
            return

        expected = set(brief.expected_product_ids)
        top = list(returned)[:CUTOFF]
        found = len(expected.intersection(top))
        self.recall_sum += found / len(expected)
        self.achievable_recall_sum += found / min(CUTOFF, len(expected))
        self.expected_size_sum += len(expected)
        for rank, product_id in enumerate(top, start=1):
            if product_id in expected:
                self.reciprocal_rank_sum += 1.0 / rank
                break

        if filter_ok is not None:
            self.filter_total += 1
            self.filter_hits += 1 if filter_ok else 0

    @property
    def scored_cases(self) -> int:
        return self.case_count - self.expects_nothing_count

    def score(self, brief_class: str) -> ClassMetrics:
        return ClassMetrics(
            brief_class=brief_class,
            cases=self.case_count,
            scored_cases=self.scored_cases,
            recall_at_10=_ratio(self.recall_sum, self.scored_cases),
            achievable_recall_at_10=_ratio(self.achievable_recall_sum, self.scored_cases),
            mrr=_ratio(self.reciprocal_rank_sum, self.scored_cases),
            filter_precision=_ratio(self.filter_hits, self.filter_total),
            empty_answer_rate=_ratio(self.empty_answers, self.case_count),
            filter_judged_cases=self.filter_total,
            mean_expected_size=_ratio(float(self.expected_size_sum), self.scored_cases),
        )


def _ratio(numerator: float, denominator: int) -> float:
    """A ratio, or `0.0` when there is nothing to divide by.

    Returning `0.0` rather than raising keeps a class with no scorable cases — the `negative` class
    has none for recall — from aborting the whole run. The denominator stays visible through
    `cases` / `scored_cases` / `filter_judged_cases`, and `ClassMetrics.unmeasured` names which
    figures were not measured, so a `0.0` cannot be mistaken for a measured failure (BR-2.19).
    """
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


def satisfies(row: Mapping[str, Any], constraints: StatedConstraints) -> bool:
    """Whether one returned record honours every constraint the brief stated.

    Reads the **artifact's own row**, so `filter_precision` measures what the buyer was actually
    handed rather than what the catalogue says about it.

    A multi-valued attribute arrives as a sequence, because five of Gotham's filterable attributes
    live in join tables rather than in `records` and the caller merges them in (see
    `ContextCache.join_values_for`). Membership is tested against that sequence — a substring test
    would count `"news"` as satisfying `"news-obsessives"`. JSON text is also accepted, for a sidecar
    that stores a list in a column.

    **`attribute not in row` is a hard False, and that has already misreported once.** The five
    join-table attributes were absent from every row this function was handed, so any brief naming
    one scored a failure irrespective of the result: 47 of 62 judgeable cases returned units that
    were *entirely* ground truth while `filter_precision` reported a miss, and `content_led` read
    0.0000 across the class. The hard False is still correct — a constraint that cannot be checked
    must not be assumed satisfied — so the fix belongs in the caller, which must supply the join
    values rather than in a `continue` here.
    """
    import json as _json

    for attribute, value in constraints.attributes:
        if attribute not in row:
            # A constraint the row cannot speak to is not satisfied. Skipping it would inflate
            # precision by ignoring exactly the constraints the retriever might be getting wrong.
            return False
        stored = row[attribute]
        if isinstance(stored, (list, tuple, set, frozenset)):
            if value not in stored:
                return False
        elif isinstance(stored, str) and stored.startswith("["):
            try:
                values = _json.loads(stored)
            except _json.JSONDecodeError:
                return False
            if value not in values:
                return False
        elif str(stored) != value:
            return False

    if constraints.max_cpm is not None:
        try:
            if float(row["cpm"]) > constraints.max_cpm:
                return False
        except (KeyError, TypeError, ValueError):
            return False
    return True


class QualityReporter:
    """Scores a corpus against a retrieval callable.

    `search` is injected — `(brief, audience_weight) -> ranked product ids` — which is what makes the
    metrics themselves testable: the same scoring code measures a real artifact, a deliberately
    perfect retriever and a deliberately empty one. A reporter that constructed its own retriever
    could only be tested against the thing it was measuring.
    """

    def __init__(
        self,
        *,
        search: Callable[[CorpusBrief, float | None], Sequence[str]],
        rows_for: Callable[[Sequence[str]], Mapping[str, Mapping[str, Any]]] | None = None,
        baseline_class: str | None = "audience_led",
    ) -> None:
        self._search = search
        self._rows_for = rows_for
        #: Which class to re-measure with the second axis withheld. A seller with one vector, or one
        #: whose classes are named differently, passes `None` or its own name. Defaulted rather than
        #: required so the existing measurement keeps its meaning without every caller restating it.
        self._baseline_class = baseline_class

    def measure(
        self,
        briefs: Sequence[CorpusBrief],
        *,
        artifact_version: str,
        corpus_composition: Mapping[str, int] | None = None,
        measure_baseline: bool = True,
        progress: Callable[[int, int], None] | None = None,
    ) -> QualityReport:
        if not briefs:
            raise CorpusError("no briefs to measure")

        overall = _Accumulator()
        per_class: dict[str, _Accumulator] = defaultdict(_Accumulator)

        for done, brief in enumerate(briefs, start=1):
            returned = list(self._search(brief, None))
            filter_ok = self._filters_honoured(brief, returned)
            overall.add(brief, returned, filter_ok)
            per_class[brief.brief_class].add(brief, returned, filter_ok)
            if progress is not None:
                progress(done, len(briefs))

        baseline: ClassMetrics | None = None
        if measure_baseline:
            baseline = self._content_only_baseline(briefs)

        return QualityReport(
            artifact_version=artifact_version,
            measured_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            per_class=tuple(
                accumulator.score(name) for name, accumulator in sorted(per_class.items())
            ),
            aggregate=overall.score("aggregate"),
            audience_led_content_only_baseline=baseline,
            corpus_composition=dict(corpus_composition or {}),
        )

    def _content_only_baseline(self, briefs: Sequence[CorpusBrief]) -> ClassMetrics | None:
        """The baseline class with the second axis withheld.

        `None` — not a zeroed `ClassMetrics` — when the profile declares no baseline class or there are
        no briefs in it. An absent comparison and a comparison that scored zero are opposite facts, and
        a zeroed baseline would make the dual-vector axis look infinitely valuable.
        """
        if self._baseline_class is None:
            return None
        audience_led = [
            brief for brief in briefs if brief.brief_class == self._baseline_class
        ]
        if not audience_led:
            return None

        accumulator = _Accumulator()
        for brief in audience_led:
            # `audience_weight=0.0` is honoured as a buyer override and escapes the derived weight's
            # clamp, so the content vector orders the survivors alone — through the same filter, the
            # same ranker and the same artifact (BR-2.17).
            returned = list(self._search(brief, 0.0))
            accumulator.add(brief, returned, self._filters_honoured(brief, returned))
        return accumulator.score(f"{self._baseline_class}_content_only")

    def _filters_honoured(
        self, brief: CorpusBrief, returned: Sequence[str]
    ) -> bool | None:
        """Whether every returned unit honours every stated constraint.

        `None` — meaning "no judgement" — when there is nothing to judge: no constraints, no results,
        or no way to read the rows. `None` keeps the case out of the denominator entirely, which is
        what stops an unjudgeable case from being counted as a pass (BR-2.19).
        """
        if brief.constraints.is_empty or not returned or self._rows_for is None:
            return None
        try:
            rows = self._rows_for(returned)
        except Exception:  # noqa: BLE001 - unreadable rows are no judgement, never a pass
            _log.warning("could not read rows for filter precision", exc_info=True)
            return None
        if not rows:
            return None
        return all(
            satisfies(row, brief.constraints)
            for product_id, row in rows.items()
            if product_id in set(returned)
        )


def render_report(
    report: QualityReport, *, title: str = "Retrieval quality", baseline_class: str = "audience_led"
) -> str:
    """A human-readable report. Unmeasured figures are labelled, not printed as zeros."""
    lines = [
        "=" * 78,
        title,
        f"  artifact : {report.artifact_version}",
        f"  measured : {report.measured_at}",
        "=" * 78,
        "",
        f"{'class':<30}{'cases':>6}{'recall':>9}{'achv':>8}{'MRR':>8}"
        f"{'filter':>8}{'empty':>7}{'|GT|':>8}",
        "-" * 78,
    ]

    def row(metrics: ClassMetrics) -> str:
        def figure(name: str, value: float) -> str:
            return "  n/a" if name in metrics.unmeasured else f"{value:.4f}"

        return (
            f"{metrics.brief_class:<30}{metrics.cases:>6}"
            f"{figure('recall_at_10', metrics.recall_at_10):>9}"
            f"{figure('achievable_recall_at_10', metrics.achievable_recall_at_10):>8}"
            f"{figure('mrr', metrics.mrr):>8}"
            f"{figure('filter_precision', metrics.filter_precision):>8}"
            f"{figure('empty_answer_rate', metrics.empty_answer_rate):>7}"
            f"{metrics.mean_expected_size:>8.1f}"
        )

    for metrics in report.per_class:
        lines.append(row(metrics))
    lines.append("-" * 78)
    lines.append(row(report.aggregate))

    if report.audience_led_content_only_baseline is not None:
        baseline = report.audience_led_content_only_baseline
        fused = next(
            (m for m in report.per_class if m.brief_class == baseline_class), None
        )
        lines.extend(
            ["", "Second axis: does it earn its place?", "-" * 78]
        )
        lines.append(row(baseline))
        if fused is not None:
            lines.append(
                f"{'delta (fused - content only)':<30}{'':>6}"
                f"{fused.recall_at_10 - baseline.recall_at_10:>+9.4f}"
                f"{fused.achievable_recall_at_10 - baseline.achievable_recall_at_10:>+8.4f}"
                f"{fused.mrr - baseline.mrr:>+8.4f}"
            )
            lines.append(
                "  A positive delta is the second axis contributing. A zero or negative one is "
                "the answer too, and is reported as found."
            )
            # A zero delta has been misread here once, so the caveat is printed rather than left to
            # be rediscovered. Gotham's baseline came back +0.0000 on all three figures while the
            # two rankings were in fact different for 17 of 17 briefs: `achv` and `MRR` were already
            # at 1.0000 and mean |GT| was 99 units against 10 slots, so any ten correct units score
            # identically. The delta measures the metrics' headroom as much as the axis.
            if (
                abs(fused.achievable_recall_at_10 - baseline.achievable_recall_at_10) < 1e-9
                and fused.achievable_recall_at_10 > 0.99
            ):
                lines.append(
                    "  NOTE: this delta is ceiling-bound, not necessarily an inert axis. "
                    f"achievable recall is {fused.achievable_recall_at_10:.4f} and mean ground "
                    f"truth is {fused.mean_expected_size:.1f} units against a cutoff of {CUTOFF}, "
                    "so any correct ten score the same. Compare the rankings themselves before "
                    "concluding the axis does nothing."
                )

    if report.corpus_composition:
        lines.extend(["", "Corpus composition", "-" * 78])
        for key, value in sorted(report.corpus_composition.items()):
            lines.append(f"  {key:<40}{value:>10}")
        lines.append(
            "  Discard counts are part of the report: a quality figure is only interpretable "
            "next to how many variations were dropped to get it."
        )

    lines.extend(
        [
            "",
            "No verdict. There is no threshold in this report and no pass/fail field: no prior",
            "measurement exists from which a bar could be set, and an arbitrary gate would",
            "print PASS against a number nobody chose on evidence.",
            "",
        ]
    )
    return "\n".join(lines)


__all__ = ["CUTOFF", "QualityReporter", "render_report", "satisfies"]
