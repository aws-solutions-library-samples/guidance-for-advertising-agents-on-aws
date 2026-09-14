"""`VariationGenerator` and `VariationValidator`.

Seed briefs are templated and stilted on purpose. Buyers do not write like that, so quality measured
only against seeds measures a phrasing nobody uses. The generator produces natural phrasings; the
validator decides which of them may keep their seed's ground truth.

Identical constraints, not similar ones - the load-bearing rule of the whole corpus
----------------------------------------------------------------------------------
A variation inherits its seed's ground truth **only if the runtime's own parser produces an identical
constraint set from it**. If that check is weak, every quality figure downstream describes something
other than what was asked, and it does so invisibly - the numbers still look like numbers.

The failure it prevents is mundane and likely: a paraphrase turning "under $14 CPM" into "affordable"
drops a constraint. The variation is then a *broader* request whose correct answer set is strictly
larger than the seed's, so inheriting the seed's narrower ground truth would score correct results as
wrong. Recall would fall and the obvious conclusion - "retrieval got worse" - would be exactly
backwards.

Hence identity: identical `attributes` and identical `max_cpm`. Not a subset, not a superset, not
"close enough".

**The validator uses the runtime's parser, not a copy.** A parser emitting `group_name` measured
against a corpus naming the dimension `group` once reported `filter_precision` of 0.0000 across 4,406
constraints while extracting every one of them correctly. Two implementations of "what does this brief
ask for" will disagree, and the disagreement will be read as a quality result.

The LLM never sees the ground truth
-----------------------------------
The generator is given a brief's *text* and returns *text*. It is not told which products are correct,
does not assign a class, and cannot influence what counts as right. Everything it produces is subject
to the validator's check, so the worst an unhelpful model can do is waste variations - never corrupt a
score.
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Sequence

from corpus_kit.corpus_types import (
    CorpusBrief,
    RejectedVariation,
    RejectionReason,
    StatedConstraints,
    ValidationOutcome,
)

_log = logging.getLogger(__name__)

#: Bedrock model used for phrasing. A small, cheap model is the right tool: the task is rewording a
#: sentence, and the LLM cost is accepted as once-off precisely because it is small.
DEFAULT_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

#: Concurrent Bedrock calls during a build.
#:
#: Sequential was the original shape and it made a 1,000-seed build take ~35 minutes at roughly two
#: seconds a call, which is too slow to sit inside a deploy step. The calls are network-bound, so
#: threads are the right tool and the number is bounded to stay well clear of on-demand throttling
#: rather than to saturate it - a throttled call costs variations silently (see `vary`), so pushing
#: the limit trades a slower build for a quieter, worse corpus.
DEFAULT_WORKERS = 8

#: The instruction. Two things it must not do are stated as prohibitions rather than left implicit,
#: because a model asked to "make this sound natural" will helpfully soften a hard constraint - which
#: is the one edit that silently invalidates the ground truth.
PROMPT = """Rewrite this advertising inventory brief in {count} different ways, as a media buyer \
would actually write it.

Brief: {brief}

Rules:
- Keep EVERY stated constraint exactly. Do not drop, soften, generalise or add one.
- A price ceiling must stay an explicit number. Never turn "under $14 CPM" into "affordable" or \
"cheap".
- Keep the exact category, segment and section names. They are literal values from a catalogue, not \
descriptions to paraphrase.
- Vary sentence structure, tone and length. Some terse, some conversational.

Return ONLY a JSON array of {count} strings, nothing else."""


class VariationGenerator:
    """Phrasing variations for a seed brief. **Text only.**

    Ground truth is not this component's to assign - its return type is `list[str]`, so it
    structurally cannot. A generator that returned `CorpusBrief`s could attach a wrong ground truth,
    and the validator would then be checking work it had been handed rather than deciding.

    `invoke` is injected so the whole class is testable without Bedrock, and so a run can be replayed
    from recorded output.
    """

    def __init__(
        self,
        *,
        invoke: Callable[[str], str] | None = None,
        model_id: str = DEFAULT_MODEL_ID,
        region: str = "us-east-1",
        workers: int = DEFAULT_WORKERS,
    ) -> None:
        self._invoke = invoke
        self._model_id = model_id
        self._region = region
        self._workers = max(1, workers)
        self._client = None

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def workers(self) -> int:
        return self._workers

    def _bedrock_client(self):
        """One client, reused across calls.

        boto3 clients are thread-safe for calls, and constructing one per invocation re-resolves
        credentials and re-parses the service model - measurable overhead once there are thousands of
        calls, and pointless when a single client serves the whole pool.
        """
        if self._client is None:
            import boto3

            self._client = boto3.client("bedrock-runtime", region_name=self._region)
        return self._client

    def _bedrock_invoke(self, prompt: str) -> str:
        response = self._bedrock_client().invoke_model(
            modelId=self._model_id,
            body=json.dumps(
                {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 2000,
                    # Deterministic-ish. Not 0.0, because identical phrasings across seeds would
                    # defeat the point of variation; low, because the task is rewording and
                    # creativity here means constraint drift.
                    "temperature": 0.4,
                    "messages": [{"role": "user", "content": prompt}],
                }
            ),
        )
        payload = json.loads(response["body"].read())
        return payload["content"][0]["text"]

    def vary(self, seed: CorpusBrief, count: int) -> list[str]:
        """`count` phrasings of `seed.text`, or fewer.

        Returns `[]` rather than raising on any failure. A corpus build must not die because one model
        call was throttled - the seed remains in the corpus, it simply gains no variations, and the
        composition counts record how many were produced.

        That silence has a cost worth knowing about: a throttled run produces a smaller corpus and
        says so only through `variations_offered` falling short of `seeds x count`, which is why the
        build reports that comparison explicitly rather than leaving a reader to notice.
        """
        if count <= 0:
            return []
        invoke = self._invoke or self._bedrock_invoke
        try:
            raw = invoke(PROMPT.format(count=count, brief=seed.text))
        except Exception:  # noqa: BLE001 - a failed call costs variations, never the build
            _log.warning("variation generation failed for %s", seed.brief_id, exc_info=True)
            return []
        return _parse_variations(raw, count)

    def vary_many(
        self,
        seeds: Sequence[CorpusBrief],
        count: int,
        *,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[list[str]]:
        """`vary` across a worker pool, returning results **aligned to `seeds` order**.

        Order alignment is not incidental. The corpus file is the seeds followed by their accepted
        variations, so results arriving in completion order would make the output depend on which
        network call finished first - and the byte-stability the seed stage works to guarantee would
        end at the variation stage. `ThreadPoolExecutor.map` yields in submission order, which is what
        makes this safe; a `as_completed` loop would not be.
        """
        if count <= 0 or not seeds:
            return [[] for _ in seeds]
        if self._workers == 1:
            results = []
            for done, seed in enumerate(seeds, start=1):
                results.append(self.vary(seed, count))
                if progress is not None:
                    progress(done, len(seeds))
            return results

        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            results = []
            for done, texts in enumerate(
                pool.map(lambda seed: self.vary(seed, count), seeds), start=1
            ):
                results.append(texts)
                if progress is not None:
                    progress(done, len(seeds))
        return results


def _parse_variations(raw: str, count: int) -> list[str]:
    """A JSON array of strings out of a model response.

    Tolerant of the two things models actually do - wrapping JSON in prose, and fencing it in markdown
    - and intolerant of anything else. Being tolerant here is safe in a way it would not be elsewhere:
    every string extracted still has to pass the validator, so a mis-parse costs variations rather
    than corrupting ground truth.
    """
    if not raw or not raw.strip():
        return []
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    if not text.startswith("["):
        bracketed = re.search(r"\[.*\]", text, re.DOTALL)
        if not bracketed:
            return []
        text = bracketed.group(0)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [entry.strip() for entry in parsed if isinstance(entry, str) and entry.strip()][:count]


class VariationValidator:
    """Decides which variations may inherit their seed's ground truth.

    Holds the **runtime's** `BriefParser` - the same object `ContextCache` builds from the artifact's
    vocabulary. Injected rather than constructed here so this class cannot accidentally be given a
    differently-configured parser: the caller has to hand over the one it means.

    The profile is held too, so an accepted variation is validated against the class rule exactly as a
    seed is. Without it, the class check would apply on one of the two paths a brief is created by.
    """

    def __init__(self, parser: Any, *, profile: Any = None) -> None:
        self._parser = parser
        self._profile = profile

    def validate(
        self, seed: CorpusBrief, variations: Sequence[str]
    ) -> ValidationOutcome:
        """Sort `variations` into accepted briefs and quarantined rejections.

        Every variation ends up in exactly one of the two lists - nothing is silently dropped, which is
        asserted as a property (`accepted + rejected == offered`).

        An accepted variation is a full `CorpusBrief` carrying the **seed's** ground truth and
        constraints, with `origin="variation"` and `seed_brief_id` set. It keeps the seed's constraints
        rather than its own parse *because the two were just proven equal* - storing the variation's
        parse would be storing a second copy of the same fact, and the two copies could later disagree
        through a bug in this very check.
        """
        accepted: list[CorpusBrief] = []
        rejected: list[RejectedVariation] = []

        for index, text in enumerate(variations):
            reason, parsed = self._judge(seed, text)
            if reason is None:
                brief = CorpusBrief(
                    brief_id=f"{seed.brief_id}-v{index:02d}",
                    text=text.strip(),
                    brief_class=seed.brief_class,
                    expected_product_ids=seed.expected_product_ids,
                    constraints=seed.constraints,
                    origin="variation",
                    seed_brief_id=seed.brief_id,
                )
                if self._profile is not None:
                    self._profile.validate(brief)
                accepted.append(brief)
                continue
            rejected.append(
                RejectedVariation(
                    seed_brief_id=seed.brief_id,
                    text=text,
                    reason=reason,
                    seed_constraints=seed.constraints,
                    variation_constraints=parsed,
                )
            )

        return ValidationOutcome(accepted=tuple(accepted), rejected=tuple(rejected))

    def _judge(
        self, seed: CorpusBrief, text: str
    ) -> tuple[RejectionReason | None, StatedConstraints]:
        """`(reason_or_None, the_variation's_own_parse)`.

        The parse is returned even on rejection, because the quarantine record keeps both constraint
        sets so a rejection can be inspected rather than trusted.
        """
        if not text or not text.strip():
            return "empty", StatedConstraints()

        try:
            parsed = StatedConstraints.from_parsed(self._parser.parse(text))
        except Exception:  # noqa: BLE001 - an unparseable variation is a rejection, not a crash
            _log.warning("variation could not be parsed", exc_info=True)
            return "unparsed_phrase", StatedConstraints()

        if parsed.is_empty and not seed.constraints.is_empty:
            # Distinguished from `constraints_differ` deliberately. "The parser found nothing at all"
            # usually means the paraphrase abandoned the catalogue's literal vocabulary, which is a
            # different diagnosis - and a different fix - from "it found something else".
            return "unparsed_phrase", parsed

        if parsed != seed.constraints:
            return "constraints_differ", parsed

        return None, parsed


__all__ = [
    "DEFAULT_MODEL_ID",
    "DEFAULT_WORKERS",
    "PROMPT",
    "VariationGenerator",
    "VariationValidator",
]
