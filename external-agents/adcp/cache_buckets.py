"""Where each seller's semantic-cache artifact lives: one definition of the bucket name.

    python3 cache_buckets.py                 # show the names for the current credentials
    python3 cache_buckets.py --prefix acme   # show them for an explicit prefix

Stdlib only, and it reaches AWS only when asked to derive a prefix from the live account, so this
module can be imported by a test with no credentials.

## Why the prefix is account-scoped by default, reversing an earlier decision

`cache_pipeline/deploy_cache_infrastructure.py` composes a bucket name as `prefix + suffix` and takes
the prefix from the caller. Its docstring records that the prefix used to be derived from the account
id and that Infrastructure Design Q3=X deliberately replaced that with a caller-supplied value.

That decision is reversed here for the *default only*, and the reason is a failure it caused. The
prefix the whole stack was deployed with is the literal `adcp`, so the buckets were
`adcp-poseidon-seller-slm` and `adcp-gotham-seller-cache`, alongside the now-unmanaged
`adcp-triton-seller-slm`. S3 bucket names are global, so those names are **claimed by one account and
unavailable to every other**. Deploying this stack into a second account produced (verbatim, from the
run that found this):

    head-bucket adcp-triton-seller-slm -> 403 Forbidden

not a 404. `deploy_cache_infrastructure.py` handles that correctly and refuses to continue — but
nothing ran it, because no deploy step did, so the seller runtimes came up `READY`, could not read
`context-cache/latest.json`, fell back to keyword matching, and answered every brief with unranked
results and no relevance score for the rest of time. The buyer UI's inventory constellation draws
nothing without a score, so the visible symptom was a missing graph several layers away from an S3
permission.

For an open-source stack the unsuffixed name is worse still: every person who clones this repo would
collide with the same names on their first deploy.

So the caller-supplied prefix stays — `--stack-prefix` and `CACHE_STACK_PREFIX` still win, which is
what Q3=X was protecting — but the *default* is account-scoped, matching every other bucket in this
project (`adcp-buyer-agent-ui-<account>-<region>`, `adcp-reference-governance-<account>-<region>`).

## Why the region is not in the name

Every other bucket here carries `<account>-<region>`. These do not, and the difference is deliberate:
the suffixes are frozen (see below), and `<prefix>-<account>-<region>-poseidon-seller-slm` is close
enough to the 63-character ceiling that a longer future suffix would fail validation at deploy time
rather than here. A cache artifact is rebuildable from its catalogue, so a same-name bucket in two
regions of one account is a conflict an operator can resolve by overriding the prefix — unlike the
session bucket, where the name has to be unique per region because the data is not reproducible.

## Why the suffixes are what they are

They are not tidy and they are not going to be tidied. `-poseidon-seller-slm` still says `slm` because
that bucket already holds the irreplaceable 9,000-brief corpus, and renaming it would mean copying
unreproducible data for cosmetic reasons. `-gotham-seller-cache` differs in shape for the same
historical reason. The suffix table is therefore a record, not a scheme.
"""

from __future__ import annotations

import argparse
import re
import sys

#: The base of the default prefix. Authored, not derived: it names this project, and it is the part an
#: operator recognises in an S3 console listing.
DEFAULT_PREFIX_BASE = "adcp"

#: One entry per seller that reads a cache artifact. The reference seller has no cache path at all and
#: is deliberately absent — adding it would provision a bucket nothing reads.
#:
#: `deploy_cache_infrastructure.py` carries the same suffixes in its own `SELLER_RESOURCES`, because it
#: runs inside a per-package venv that cannot import this module. That duplication is asserted equal by
#: `gotham_mock/tests/test_cache_buckets.py` rather than removed, on the same reasoning as the AdCP
#: tool allowlist: compare the two real definitions, do not write a third copy to compare them against.
#: The triton seller is deliberately absent. It is no longer deployed by `deploy_all.py`; the poseidon
#: seller -- its anonymised duplicate -- carries the audio inventory now. Removing the entry stops the
#: deploy provisioning and publishing to a bucket nothing reads.
#:
#: **The existing `-triton-seller-slm` bucket is not deleted by this change and must not be.** It holds
#: the only copy of the 9,000-brief audio corpus, which cannot be regenerated. It simply stops being
#: managed here. Poseidon's bucket holds a byte-identical copy of that corpus, so nothing depends on the
#: triton bucket remaining reachable.
SELLER_BUCKET_SUFFIXES: dict[str, str] = {
    "poseidon": "-poseidon-seller-slm",
    "gotham": "-gotham-seller-cache",
}

#: Per-seller log group, `{prefix}` substituted. Note `-seller` with no `-slm`/`-cache` tail: log group
#: names have no global namespace, so these were never distorted by the history above.
SELLER_LOG_GROUP_TEMPLATES: dict[str, str] = {
    "poseidon": "/aws/context-cache/{prefix}-poseidon-seller",
    "gotham": "/aws/context-cache/{prefix}-gotham-seller",
}

#: S3's own rules, checked locally so a bad prefix fails before any network round-trip.
_PREFIX_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
BUCKET_MIN_LENGTH = 3
BUCKET_MAX_LENGTH = 63


class CacheBucketError(ValueError):
    """A prefix or seller that cannot produce a usable bucket name."""


def sellers() -> tuple[str, ...]:
    """Every seller with a cache bucket, in a stable order."""
    return tuple(sorted(SELLER_BUCKET_SUFFIXES))


def derive_stack_prefix(account_id: str) -> str:
    """The default prefix for an account: `adcp-<account>`.

    Takes the account rather than resolving it, so this module has no import-time dependency on
    credentials and a test can pass `123456789012`.
    """
    if not re.fullmatch(r"\d{12}", account_id or ""):
        raise CacheBucketError(
            f"expected a 12-digit AWS account id, got {account_id!r}. The default cache bucket "
            "prefix is account-scoped because S3 bucket names are global."
        )
    return f"{DEFAULT_PREFIX_BASE}-{account_id}"


def validate_prefix(prefix: str) -> str:
    """Check the prefix, and the longest bucket name it would produce, against S3's rules."""
    if not _PREFIX_PATTERN.match(prefix or ""):
        raise CacheBucketError(
            f"cache bucket prefix {prefix!r} is not valid for an S3 bucket name: lowercase letters, "
            "digits and hyphens only, starting and ending with a letter or digit."
        )
    # The longest suffix, not the one being asked for. A prefix that works for gotham and overflows for
    # poseidon is a prefix that fails halfway through provisioning, with one bucket already created.
    longest = max(SELLER_BUCKET_SUFFIXES.values(), key=len)
    candidate = f"{prefix}{longest}"
    if not (BUCKET_MIN_LENGTH <= len(candidate) <= BUCKET_MAX_LENGTH):
        raise CacheBucketError(
            f"prefix {prefix!r} produces {candidate!r} at {len(candidate)} characters; S3 allows "
            f"{BUCKET_MIN_LENGTH}-{BUCKET_MAX_LENGTH}. Choose a "
            f"{'shorter' if len(candidate) > BUCKET_MAX_LENGTH else 'longer'} prefix."
        )
    return prefix


def _suffix(seller: str) -> str:
    try:
        return SELLER_BUCKET_SUFFIXES[seller]
    except KeyError:
        raise CacheBucketError(
            f"unknown seller {seller!r}; expected one of {list(sellers())}. The reference seller has "
            "no cache path and deliberately has no bucket."
        ) from None


def bucket_name(prefix: str, seller: str) -> str:
    """The cache bucket for one seller under one prefix."""
    return f"{validate_prefix(prefix)}{_suffix(seller)}"


def log_group_name(prefix: str, seller: str) -> str:
    """The cache log group for one seller under one prefix."""
    _suffix(seller)  # reject an unknown seller with the same message as bucket_name
    return SELLER_LOG_GROUP_TEMPLATES[seller].format(prefix=validate_prefix(prefix))


def all_bucket_names(prefix: str) -> dict[str, str]:
    """Every seller's bucket under one prefix, keyed by seller."""
    return {seller: bucket_name(prefix, seller) for seller in sellers()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Show the semantic-cache bucket names.")
    parser.add_argument(
        "--prefix",
        help="Use this prefix instead of deriving one from the current credentials.",
    )
    args = parser.parse_args()

    prefix = args.prefix
    if not prefix:
        import aws_identity

        try:
            prefix = derive_stack_prefix(aws_identity.account_id())
        except aws_identity.IdentityError as exc:
            print(f"!!! {exc}", file=sys.stderr)
            return 1

    try:
        names = all_bucket_names(prefix)
    except CacheBucketError as exc:
        print(f"!!! {exc}", file=sys.stderr)
        return 1

    print(f"prefix  {prefix}")
    for seller, bucket in names.items():
        print(f"  {seller:<9} s3://{bucket}   {log_group_name(prefix, seller)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
