"""Replace live environment values in documentation with placeholders.

    python3 scrub_docs_identity.py            # report
    python3 scrub_docs_identity.py --apply

Documentation in this repo accumulated the identifiers of whatever was deployed at the time: account
numbers, Cognito pool and client ids, CloudFront domains and distribution ids, KMS key ids, and runtime
ids. None of it is a secret, and all of it is wrong for anyone else -- a reader copying a value out of a
README is copying a pointer into an account they cannot reach.

Placeholders keep the shape so the surrounding prose still reads correctly and the format stays obvious.

## Scope

Every `.md` and `.txt` file git would publish. Deliberately includes `aidlc-docs/` and `.kiro/steering/`:
an identifier is just as published there as in the README, and "it is a historical record" does not make
it any less of a live pointer.

Values that are obviously synthetic already (the reserved example account, the example CloudFront
domains) are left alone -- they exist so documentation has something safe to show.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

EXAMPLE_ACCOUNT_ID = "123456789012"
EXAMPLE_CLOUDFRONT_DOMAINS = {"d111111abcdef8.cloudfront.net", "d222222abcdef8.cloudfront.net"}

#: Each entry is (label, pattern, replacement or callable).
RULES: list[tuple[str, re.Pattern[str], object]] = [
    (
        "cognito-user-pool-id",
        # Must run BEFORE the region rule would ever touch it, and before the account rule, because a
        # pool id embeds a region and nothing else.
        re.compile(r"\b[a-z]{2}-[a-z]+-\d_[A-Za-z0-9]{9}\b"),
        "<cognito-user-pool-id>",
    ),
    (
        "cloudfront-domain",
        re.compile(r"\b[a-z0-9]{13,14}\.cloudfront\.net\b"),
        "<cloudfront-domain>",
    ),
    (
        "cloudfront-distribution-id",
        # CloudFront ids are `E` followed by uppercase alphanumerics. Anchored on word boundaries and a
        # length range so ordinary prose in caps is not caught.
        re.compile(r"\bE[A-Z0-9]{12,13}\b"),
        "<cloudfront-distribution-id>",
    ),
    (
        "kms-key-id",
        re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"),
        "<kms-key-id>",
    ),
    (
        "agentcore-runtime-id",
        # `runtime/Name_agentName-EXAMPLE123` -> keep the name, drop the per-deployment suffix.
        re.compile(r"(runtime(?:%2F|/)[A-Za-z_]+)-[A-Za-z0-9]{10}\b"),
        r"\1-<runtime-id>",
    ),
    (
        "aws-account-id",
        re.compile(r"(?<!\d)\d{12}(?!\d)"),
        "<aws-account-id>",
    ),
]

#: Cognito app client ids are 26 lowercase alphanumerics -- too generic to regex safely against prose, so
#: they are matched exactly. Collected from the deployments this repo has had.
KNOWN_CLIENT_IDS = (
    "4ja9m026rj02qhk92knbq68ij6",
    "38m6d3p39imnto14dk6jkpq008",
    "6qs1393gdh4qrm2b6gufqp6nep",
    "1t8pok9pbd4s4arha1fdd3gdhq",
    "2abjcdq4sv0sn7eblbi8bvjd5g",
    "4ceca8m3bo1ed8imgd80l3jfoq",
    "75f5plhv2bofhtsbqjs600knvr",
    "6f99s4fb7r9qlu957nji9veebu",
)


def is_synthetic_account(value: str) -> bool:
    """Already-safe account numbers: the reserved example, or anything built from one or two digits."""
    return value == EXAMPLE_ACCOUNT_ID or len(set(value)) <= 2


def target_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    files = []
    for rel in out:
        if not rel.endswith((".md", ".txt")):
            continue
        path = REPO_ROOT / rel
        if path.is_file():
            files.append(path)
    return sorted(files)


def scrub(text: str) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}

    for client_id in KNOWN_CLIENT_IDS:
        if client_id in text:
            counts["cognito-client-id"] = counts.get("cognito-client-id", 0) + text.count(client_id)
            text = text.replace(client_id, "<cognito-client-id>")

    for label, pattern, replacement in RULES:

        def apply(match: re.Match[str], _label=label, _replacement=replacement) -> str:
            found = match.group(0)
            if _label == "aws-account-id" and is_synthetic_account(found):
                return found
            if _label == "cloudfront-domain" and found in EXAMPLE_CLOUDFRONT_DOMAINS:
                return found
            counts[_label] = counts.get(_label, 0) + 1
            if callable(_replacement):
                return _replacement(match)
            return match.expand(_replacement) if "\\" in str(_replacement) else str(_replacement)

        text = pattern.sub(apply, text)

    return text, counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="rewrite the files; else report only")
    args = parser.parse_args()

    files = target_files()
    total: dict[str, int] = {}
    changed = 0

    for path in files:
        original = path.read_text()
        scrubbed, counts = scrub(original)
        if not counts:
            continue
        changed += 1
        summary = ", ".join(f"{k}x{v}" for k, v in sorted(counts.items()))
        print(f"  {path.relative_to(REPO_ROOT)}   {summary}")
        for key, value in counts.items():
            total[key] = total.get(key, 0) + value
        if args.apply:
            path.write_text(scrubbed)

    print()
    print(f"{changed} of {len(files)} documentation file(s) affected")
    for key, value in sorted(total.items()):
        print(f"  {key:28s} {value}")
    if not args.apply:
        print("\nDry run. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
