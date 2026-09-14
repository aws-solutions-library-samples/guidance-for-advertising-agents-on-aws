"""`corpus_kit` - build and score a benchmark corpus for any inventory seller.

One source, vendored into each seller's tree at deploy time the way `cache_contract` and
`context_cache` already are. A seller supplies a `CorpusProfile` and a catalogue adapter; everything
else here is seller-agnostic.

    from corpus_kit import build, profile, quality, seeds, types, variations

What a new seller has to write
------------------------------
1. A `CorpusProfile`: its brief classes, the axes those classes draw from, a phrasing per attribute.
2. An adapter turning one catalogue unit into a `CatalogueUnit`.
3. A thin CLI that loads its catalogue, builds the runtime's `BriefParser`, and calls `build.build`.

Nothing else. The seed generator, the LLM variation stage, the validation rule that decides which
phrasings may inherit a seed's ground truth, and the metric suite are all here.

**Offline only.** Nothing in this package may be imported from a request path and none of it belongs
in a container image: a retriever must not be able to read the ground truth it is scored against.
"""

__all__ = ["build", "profile", "quality", "seeds", "corpus_types", "variations"]
