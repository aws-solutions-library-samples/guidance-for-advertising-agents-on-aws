"""Make a corpus a deployment concern: build it the first time, verify it every time after.

A benchmark corpus is not a build artifact and not a runtime input. It is **source** - it cannot be
regenerated identically once an LLM has phrased half of it, and every quality figure a seller has ever
published is only interpretable against it. So the deploy step's job is not "produce a corpus", it is:

    absent  -> build it, once, and tell the operator to commit it
    present -> prove it is the corpus the recorded figures were measured against

Those are different actions with different failure modes, which is why this is one module rather than
two flags on a build script.

What "prove" means here
-----------------------
Three checks, and the third is the one that actually bites:

1. **The corpus parses.** A truncated JSONL fails on the line it was cut at rather than silently
   scoring fewer cases.
2. **The digest matches** what `provenance.json` recorded when the corpus was written. Catches a
   hand-edit, a bad merge, a partial checkout.
3. **The catalogue fingerprint matches.** Every expected product id was computed against one specific
   catalogue. Move the catalogue and the corpus still parses, still hashes correctly, and is now
   scoring against inventory that no longer exists - the one failure that produces plausible numbers.
   `catalogue_sha256` exists on the cache manifest for the same reason.

Check 3 has no equivalent in the artifact pipeline's corpus gate, which verifies bytes only. It is
included because the corpus's dependency on the catalogue is stronger than the artifact's: an artifact
rebuilt against a new catalogue is simply a new artifact, while a corpus rebuilt against a new
catalogue is a new *baseline* and invalidates every figure measured before it.

Why absent-and-not-applying is not an error
-------------------------------------------
`--apply` gates the build because a first build costs money and minutes. A report-only run against a
missing corpus says so and exits 0: it is a legitimate question to ask, and a deploy harness that
refused to describe its own plan would be worse than one that cannot build.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from corpus_kit.build import (
    CORPUS_FILE,
    PROVENANCE_FILE,
    digest_of,
)
from corpus_kit.corpus_types import CorpusError, read_jsonl


class CorpusNotVerified(Exception):
    """The corpus on disk is not the corpus the recorded figures were measured against."""


@dataclass(frozen=True)
class CorpusState:
    """What is on disk, and whether it can be trusted.

    `verified` is deliberately three-valued. `True` and `False` are the digest comparison; `None` means
    no digest was recorded to compare against, which is a different fact from a mismatch and must not
    be reported as one. A corpus written before provenance existed is unverifiable, not corrupt.
    """

    directory: Path
    present: bool
    briefs: int = 0
    digest: str | None = None
    expected_digest: str | None = None
    catalogue_fingerprint: str | None = None
    notes: tuple[str, ...] = ()

    @property
    def verified(self) -> bool | None:
        if not self.present or self.expected_digest is None:
            return None
        return self.digest == self.expected_digest

    @property
    def summary(self) -> str:
        if not self.present:
            return f"absent ({self.directory})"
        if self.verified is None:
            return f"present, {self.briefs} briefs, no recorded digest to verify against"
        if self.verified:
            return f"present and verified, {self.briefs} briefs, sha256 {self.digest[:12]}"
        return (
            f"present but MODIFIED, {self.briefs} briefs, sha256 {self.digest[:12]} "
            f"!= recorded {self.expected_digest[:12]}"
        )


def inspect(directory: Path) -> CorpusState:
    """Read the corpus and its provenance without building anything.

    Never raises for an absent corpus - that is a state, not a failure. Does raise if the corpus is
    present and unreadable, because "there is a file there and it is not a corpus" is a fault rather
    than a state.
    """
    directory = Path(directory)
    corpus_path = directory / CORPUS_FILE
    if not corpus_path.exists():
        return CorpusState(directory=directory, present=False)

    records = read_jsonl(corpus_path)
    digest = digest_of(corpus_path)

    expected: str | None = None
    fingerprint: str | None = None
    notes: list[str] = []
    provenance_path = directory / PROVENANCE_FILE
    if provenance_path.exists():
        try:
            provenance = json.loads(provenance_path.read_text())
        except json.JSONDecodeError as exc:
            raise CorpusError(f"{provenance_path} is not valid JSON: {exc}") from exc
        expected = provenance.get("corpus_sha256")
        fingerprint = provenance.get("catalogue_fingerprint")
        if expected is None:
            notes.append(f"{PROVENANCE_FILE} records no corpus_sha256")
        if fingerprint is None:
            notes.append(f"{PROVENANCE_FILE} records no catalogue_fingerprint")
    else:
        notes.append(f"no {PROVENANCE_FILE}; the corpus cannot be verified")

    return CorpusState(
        directory=directory,
        present=True,
        briefs=len(records),
        digest=digest,
        expected_digest=expected,
        catalogue_fingerprint=fingerprint,
        notes=tuple(notes),
    )


def ensure(
    directory: Path,
    *,
    build: Callable[[], None],
    apply: bool = False,
    catalogue_fingerprint: str | None = None,
    echo: Callable[[str], None] = print,
) -> CorpusState:
    """The deploy step. Build when absent (with `apply`), verify when present.

    `build` is a zero-argument callable so this module never learns what a catalogue is. `deploy_all`
    calls a seller's own script, which supplies it.

    Raises `CorpusNotVerified` when the corpus is present and demonstrably wrong. That is a hard stop
    rather than a rebuild: rebuilding would replace the only copy of the ground truth every recorded
    figure refers to, and doing so automatically because a digest failed is how a bad merge becomes a
    silent new baseline. A human decides.
    """
    state = inspect(directory)

    if not state.present:
        if not apply:
            echo(f"  corpus is {state.summary}")
            echo("  --apply would build it. Nothing built.")
            return state
        echo(f"  no corpus at {directory}; building it now.")
        echo(
            "  This is a first-time build: it calls Bedrock once per seed and takes minutes. "
            "The result is source and should be committed."
        )
        build()
        state = inspect(directory)
        if not state.present:
            raise CorpusError(
                f"the build reported success but wrote no {CORPUS_FILE} to {directory}"
            )
        echo(f"  built: {state.summary}")
        echo(
            "  ACTION REQUIRED: commit this corpus. It cannot be rebuilt identically -- the phrasing "
            "stage is sampled, so a re-run produces different briefs and a different baseline."
        )
        return state

    echo(f"  corpus is {state.summary}")
    for note in state.notes:
        echo(f"  note: {note}")

    if state.verified is False:
        raise CorpusNotVerified(
            f"{directory / CORPUS_FILE} hashes to {state.digest} but "
            f"{PROVENANCE_FILE} records {state.expected_digest}. Every quality figure recorded "
            "against this corpus refers to the recorded one. Deliberately not rebuilt: a rebuild "
            "would replace the ground truth rather than restore it. Restore the file from version "
            "control, or -- if the change was intended -- rebuild explicitly and re-measure."
        )

    if catalogue_fingerprint is not None and state.catalogue_fingerprint is not None:
        if catalogue_fingerprint != state.catalogue_fingerprint:
            raise CorpusNotVerified(
                "the catalogue has moved under the corpus. Ground truth was computed against "
                f"fingerprint {state.catalogue_fingerprint} and the catalogue now fingerprints "
                f"{catalogue_fingerprint}. The corpus still parses and still hashes correctly, and "
                "every expected product id in it now refers to inventory that may not exist -- which "
                "is the one failure here that produces plausible numbers. Rebuild the corpus (a new "
                "baseline) or restore the catalogue."
            )
        echo(f"  catalogue fingerprint matches ({catalogue_fingerprint[:12]})")

    return state


__all__ = ["CorpusNotVerified", "CorpusState", "ensure", "inspect"]
