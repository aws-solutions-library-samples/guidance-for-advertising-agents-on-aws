"""The two-stage build, and the files it insists on writing.

    stage one: deterministic seeds, offline, no credentials
    stage two: LLM phrasing variations, opt-in, costs money

Stage two is **opt-in**: `variations=0` is the default and a corpus of seeds alone is a complete,
usable corpus. That ordering is deliberate - a build that silently required Bedrock would make the
deterministic half unreachable to anyone without it, which is the state a fresh clone and a CI runner
are both in.

Three files, and the third is not optional
------------------------------------------
- `corpus.jsonl` - accepted briefs: seeds plus validated variations
- `rejected.jsonl` - quarantined variations, **with both constraint sets and a reason**
- `composition.json` - the counts, which travel with every quality figure

A build that wrote only the corpus would leave nobody able to say how many variations were discarded
to produce it, and a quality figure without that number is uninterpretable.

`provenance.json` is written alongside when the caller supplies the facts for it. It carries the
profile itself, because a corpus is only comparable to another corpus built from the same recipe, and
a recipe that lives only in whichever code happened to produce it cannot be compared to anything.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from corpus_kit.profile import CorpusProfile
from corpus_kit.seeds import CatalogueUnit, SeedBriefGenerator
from corpus_kit.corpus_types import CorpusBrief, RejectedVariation, write_jsonl
from corpus_kit.variations import (
    DEFAULT_MODEL_ID,
    DEFAULT_WORKERS,
    VariationGenerator,
    VariationValidator,
)

CORPUS_FILE = "corpus.jsonl"
REJECTED_FILE = "rejected.jsonl"
COMPOSITION_FILE = "composition.json"
PROVENANCE_FILE = "provenance.json"


@dataclass(frozen=True)
class BuildResult:
    """What a build produced, and enough counts to interpret it."""

    briefs: tuple[CorpusBrief, ...]
    rejected: tuple[RejectedVariation, ...]
    seed_count: int
    variations_requested: int
    composition: Mapping[str, int] = field(default_factory=dict)

    @property
    def accepted_variations(self) -> int:
        return len(self.briefs) - self.seed_count

    @property
    def offered(self) -> int:
        return self.accepted_variations + len(self.rejected)

    @property
    def lost_calls(self) -> int:
        """Variations that were requested and never came back at all.

        A throttled or failed Bedrock call returns no text and is not a rejection, so it leaves no
        trace in `rejected.jsonl`. Its only signature is `offered` falling short of
        `seeds x variations`, which is why that arithmetic is a named property rather than something a
        reader is left to do. A build that quietly produced half a corpus would otherwise look like a
        build that produced a corpus.
        """
        if self.variations_requested <= 0:
            return 0
        return max(0, self.seed_count * self.variations_requested - self.offered)


def build(
    *,
    profile: CorpusProfile,
    units: Sequence[CatalogueUnit],
    parser: Any,
    per_class: int,
    seed: int,
    variations: int = 0,
    model_id: str | None = None,
    workers: int = DEFAULT_WORKERS,
    generator: VariationGenerator | None = None,
    echo: Callable[[str], None] = print,
) -> BuildResult:
    """Seeds, then optionally variations, then the counts.

    `parser` must be the **runtime's** `BriefParser`, configured from the same vocabulary the artifact
    carries. `generator` is injectable so a test can run the whole two-stage build with no network.
    """
    echo("=== stage one: deterministic seed briefs ===")
    seeds = SeedBriefGenerator(profile=profile, seed=seed).generate(units, per_class)
    for spec in profile.classes:
        sizes = [
            len(brief.expected_product_ids)
            for brief in seeds
            if brief.brief_class == spec.name
        ]
        span = f"{min(sizes)}-{max(sizes)}" if sizes else "0"
        echo(f"  {spec.name:<16}{len(sizes):>5} briefs, ground truth {span} units")
    echo(f"  total{'':<12}{len(seeds):>5}")

    accepted: list[CorpusBrief] = list(seeds)
    rejected: list[RejectedVariation] = []

    if variations > 0:
        vary = generator or VariationGenerator(
            model_id=model_id or DEFAULT_MODEL_ID, workers=workers
        )
        # Blank line as its own call, not "\n=== ...". A caller that indents by wrapping `echo` gets
        # the indent applied to the empty first line and lost on the real one otherwise.
        echo("")
        echo(
            f"=== stage two: {variations} LLM phrasings per seed, "
            f"{vary.workers} concurrent, {vary.model_id} ==="
        )
        validator = VariationValidator(parser, profile=profile)

        def progress(done: int, total: int) -> None:
            if done % 25 == 0 or done == total:
                echo(f"  {done}/{total} seeds phrased")

        # Aligned to `seeds` order, which is what keeps the output file byte-stable across runs even
        # though the calls complete out of order. See `vary_many`.
        phrasings = vary.vary_many(seeds, variations, progress=progress)

        for brief, texts in zip(seeds, phrasings):
            outcome = validator.validate(brief, texts)
            accepted.extend(outcome.accepted)
            rejected.extend(outcome.rejected)

        reasons = Counter(entry.reason for entry in rejected)
        for reason, count in sorted(reasons.items()):
            echo(f"  rejected ({reason}): {count}")
    else:
        echo("")
        echo("=== stage two skipped (variations=0) ===")
        echo("  The deterministic corpus is complete and usable on its own.")

    composition = {
        "seeds": len(seeds),
        "variations_accepted": len(accepted) - len(seeds),
        "variations_rejected": len(rejected),
        "variations_offered": (len(accepted) - len(seeds)) + len(rejected),
        **{
            f"rejected_{reason}": count
            for reason, count in Counter(entry.reason for entry in rejected).items()
        },
    }

    result = BuildResult(
        briefs=tuple(accepted),
        rejected=tuple(rejected),
        seed_count=len(seeds),
        variations_requested=variations,
        composition=composition,
    )

    if result.lost_calls:
        # Not an exception. A short corpus is usable and the alternative -- failing a deploy step
        # because Bedrock throttled -- is worse. But it is said out loud, because the composition
        # counts alone do not distinguish "the model declined" from "the call never returned".
        echo(
            f"  !! {result.lost_calls} of {len(seeds) * variations} requested phrasings never "
            "returned (throttling or a failed call). The corpus is smaller than asked for; "
            "re-running extends it."
        )

    return result


def write(
    result: BuildResult,
    out_dir: Path,
    *,
    profile: CorpusProfile,
    provenance: Mapping[str, Any] | None = None,
    echo: Callable[[str], None] = print,
) -> dict[str, str]:
    """The three files, plus provenance. Returns `{filename: sha256}` for the two JSONL files.

    Digests are computed from the bytes just written rather than from the in-memory records, so the
    figure recorded is the figure a later verification will re-derive. Hashing the objects instead
    would produce a number that agrees with itself and with nothing on disk.
    """
    import hashlib

    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl([brief.to_dict() for brief in result.briefs], out_dir / CORPUS_FILE)
    write_jsonl([entry.to_dict() for entry in result.rejected], out_dir / REJECTED_FILE)
    (out_dir / COMPOSITION_FILE).write_text(
        json.dumps(dict(result.composition), indent=2, sort_keys=True) + "\n"
    )

    digests: dict[str, str] = {}
    for name in (CORPUS_FILE, REJECTED_FILE):
        digests[name] = hashlib.sha256((out_dir / name).read_bytes()).hexdigest()

    if provenance is not None:
        record = {
            **dict(provenance),
            "composition": dict(result.composition),
            "corpus_sha256": digests[CORPUS_FILE],
            "rejected_sha256": digests[REJECTED_FILE],
            "total_briefs": len(result.briefs),
            # The recipe travels with the corpus. Two corpora are only comparable if their profiles
            # match, and a profile that lives only in code cannot be compared after the code changes.
            "profile": profile.to_dict(),
        }
        (out_dir / PROVENANCE_FILE).write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n"
        )

    echo("")
    echo("=== written ===")
    for name in (CORPUS_FILE, REJECTED_FILE, COMPOSITION_FILE, PROVENANCE_FILE):
        path = out_dir / name
        if path.exists():
            echo(f"  {name:<20}{path.stat().st_size:>12,} bytes")
    echo("")
    echo(f"  {len(result.briefs)} briefs, {len(result.rejected)} quarantined")
    echo(f"  corpus sha256 {digests[CORPUS_FILE]}")
    echo(
        "  The corpus stays on disk and in version control. It is never written to the seller's "
        "bucket: a retriever must not be able to read the ground truth it is scored against."
    )
    return digests


def digest_of(path: Path) -> str:
    import hashlib

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


__all__ = [
    "COMPOSITION_FILE",
    "CORPUS_FILE",
    "PROVENANCE_FILE",
    "REJECTED_FILE",
    "BuildResult",
    "build",
    "digest_of",
    "write",
]
