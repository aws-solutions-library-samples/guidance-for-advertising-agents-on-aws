"""The class rule, and the deploy step's three verification checks.

The class rule moved off `CorpusBrief` onto the profile when the kit was extracted. That is only safe if
it fires on every path a brief arrives by, so both directions are asserted here and the seed generator
and variation validator are covered in their own files.

The deploy checks matter in inverse order of how obvious they are. A truncated corpus is loud. A
modified corpus is quiet. A corpus whose *catalogue* moved is silent and produces plausible numbers.
"""

from __future__ import annotations

import json

import pytest

from corpus_kit import build as corpus_build
from corpus_kit.corpus_types import CorpusBrief, CorpusError, StatedConstraints, read_briefs
from corpus_kit.deploy import CorpusNotVerified, ensure, inspect
from corpus_kit.seeds import SeedBriefGenerator


def _brief(brief_class, expected):
    return CorpusBrief(
        brief_id=f"{brief_class}-0000",
        text="Looking for in red",
        brief_class=brief_class,
        expected_product_ids=frozenset(expected),
        constraints=StatedConstraints(attributes=(("colour", "red"),)),
        origin="seed",
    )


# -- the class rule ----------------------------------------------------------


def test_an_empty_expecting_class_that_matches_something_is_rejected(profile):
    """A `negative` case that actually matches inventory scores a correct retriever as wrong."""
    with pytest.raises(CorpusError, match="must expect nothing"):
        profile.validate(_brief("negative", {"u00"}))


def test_a_normal_class_that_matches_nothing_is_rejected(profile):
    """It would measure empty-answer behaviour while claiming to measure retrieval."""
    with pytest.raises(CorpusError, match="no expected products"):
        profile.validate(_brief("audience_led", set()))


def test_a_class_the_profile_does_not_know_is_rejected(profile):
    with pytest.raises(CorpusError, match="unknown brief class"):
        profile.validate(_brief("invented_class", {"u00"}))


def test_reading_a_corpus_re_applies_the_class_rule(profile, tmp_path):
    """Reading is the path that crosses a process boundary, so it is where a hand-edited corpus shows
    up. `CorpusBrief.__post_init__` cannot check this without a profile, so `read_briefs` does."""
    path = tmp_path / "corpus.jsonl"
    record = _brief("audience_led", {"u00"}).to_dict()
    record["expected_product_ids"] = []  # a hand-edit that empties a class that must not be empty
    path.write_text(json.dumps(record) + "\n")

    assert read_briefs(path) is not None  # no profile: structural read only
    with pytest.raises(CorpusError, match="no expected products"):
        read_briefs(path, profile=profile)


def test_a_variation_must_name_its_seed(profile):
    """Seller-independent, so this one stays on the record type itself."""
    with pytest.raises(CorpusError, match="must name the seed"):
        CorpusBrief(
            brief_id="x-v00",
            text="text",
            brief_class="audience_led",
            expected_product_ids=frozenset({"u00"}),
            constraints=StatedConstraints(),
            origin="variation",
        )


# -- the deploy step ---------------------------------------------------------


def _write_corpus(directory, profile, units, per_class=2):
    briefs = SeedBriefGenerator(profile=profile, seed=7).generate(units, per_class)
    result = corpus_build.BuildResult(
        briefs=tuple(briefs),
        rejected=(),
        seed_count=len(briefs),
        variations_requested=0,
        composition={"seeds": len(briefs)},
    )
    return corpus_build.write(
        result,
        directory,
        profile=profile,
        provenance={"catalogue_fingerprint": "abc123"},
        echo=lambda _line: None,
    )


def test_an_absent_corpus_is_a_state_not_a_failure(tmp_path):
    state = inspect(tmp_path / "nothing")

    assert state.present is False
    assert state.verified is None
    assert "absent" in state.summary


def test_report_only_against_an_absent_corpus_builds_nothing(tmp_path):
    calls = []
    state = ensure(
        tmp_path / "nothing", build=lambda: calls.append(1), apply=False, echo=lambda _: None
    )

    assert calls == []
    assert state.present is False


def test_apply_builds_when_the_corpus_is_absent(tmp_path, profile, units):
    directory = tmp_path / "corpus"
    ensure(
        directory,
        build=lambda: _write_corpus(directory, profile, units),
        apply=True,
        echo=lambda _: None,
    )

    assert (directory / "corpus.jsonl").exists()
    assert inspect(directory).verified is True


def test_a_build_that_writes_nothing_is_reported_rather_than_passing(tmp_path):
    """A step that reports success while producing nothing is the worst failure available."""
    with pytest.raises(CorpusError, match="wrote no corpus.jsonl"):
        ensure(tmp_path / "corpus", build=lambda: None, apply=True, echo=lambda _: None)


def test_a_present_and_intact_corpus_verifies(tmp_path, profile, units):
    directory = tmp_path / "corpus"
    _write_corpus(directory, profile, units)

    state = ensure(
        directory,
        build=lambda: pytest.fail("must not rebuild an intact corpus"),
        apply=True,
        catalogue_fingerprint="abc123",
        echo=lambda _: None,
    )

    assert state.verified is True


def test_a_modified_corpus_is_a_hard_stop_and_is_not_rebuilt(tmp_path, profile, units):
    """Rebuilding would replace the ground truth every recorded figure refers to rather than restore
    it, which is how a bad merge becomes a silent new baseline."""
    directory = tmp_path / "corpus"
    _write_corpus(directory, profile, units)
    # A change that keeps the file valid JSONL, so only the digest can catch it.
    with open(directory / "corpus.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(_brief("audience_led", {"u00"}).to_dict()) + "\n")

    with pytest.raises(CorpusNotVerified, match="records"):
        ensure(
            directory,
            build=lambda: pytest.fail("must not rebuild"),
            apply=True,
            echo=lambda _: None,
        )


def test_a_corpus_whose_catalogue_moved_is_a_hard_stop(tmp_path, profile, units):
    """The quiet one. The corpus parses, hashes correctly, and now scores against inventory that may
    not exist - the only failure here that produces plausible numbers."""
    directory = tmp_path / "corpus"
    _write_corpus(directory, profile, units)

    with pytest.raises(CorpusNotVerified, match="catalogue has moved"):
        ensure(
            directory,
            build=lambda: pytest.fail("must not rebuild"),
            apply=True,
            catalogue_fingerprint="a-different-catalogue",
            echo=lambda _: None,
        )


def test_a_corpus_without_provenance_is_unverifiable_not_corrupt(tmp_path, profile, units):
    """`None` and `False` are different facts. A corpus written before provenance existed has no
    recorded digest to disagree with."""
    directory = tmp_path / "corpus"
    _write_corpus(directory, profile, units)
    (directory / "provenance.json").unlink()

    state = inspect(directory)

    assert state.present is True
    assert state.verified is None
    assert any("cannot be verified" in note for note in state.notes)


def test_the_written_digest_is_of_the_bytes_on_disk(tmp_path, profile, units):
    """Hashing the in-memory records instead would produce a figure that agrees with itself and with
    nothing a later verification can re-derive."""
    directory = tmp_path / "corpus"
    digests = _write_corpus(directory, profile, units)

    assert digests["corpus.jsonl"] == corpus_build.digest_of(directory / "corpus.jsonl")
    assert inspect(directory).digest == digests["corpus.jsonl"]


def test_provenance_carries_the_profile_so_two_corpora_can_be_compared(tmp_path, profile, units):
    directory = tmp_path / "corpus"
    _write_corpus(directory, profile, units)

    provenance = json.loads((directory / "provenance.json").read_text())

    assert provenance["profile"]["seller"] == "fixture"
    assert [spec["name"] for spec in provenance["profile"]["classes"]] == list(profile.class_names)


# -- throttling, which the generator deliberately swallows --------------------


def test_lost_calls_are_computed_so_a_short_corpus_cannot_look_complete(profile, units):
    """A throttled call leaves no trace in `rejected.jsonl`. Its only signature is this arithmetic."""
    briefs = SeedBriefGenerator(profile=profile, seed=7).generate(units, 2)
    result = corpus_build.BuildResult(
        briefs=tuple(briefs),
        rejected=(),
        seed_count=len(briefs),
        variations_requested=3,
        composition={},
    )

    assert result.offered == 0
    assert result.lost_calls == len(briefs) * 3


def test_no_variations_requested_means_no_lost_calls(profile, units):
    briefs = SeedBriefGenerator(profile=profile, seed=7).generate(units, 2)
    result = corpus_build.BuildResult(
        briefs=tuple(briefs), rejected=(), seed_count=len(briefs), variations_requested=0
    )

    assert result.lost_calls == 0
