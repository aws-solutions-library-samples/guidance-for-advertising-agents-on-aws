"""Fail if any publishable file hardcodes an AWS account or account-specific endpoint.

    python3 check_no_hardcoded_identity.py
    python3 check_no_hardcoded_identity.py --list-scanned

Exit 0 when clean, 1 with a report otherwise. Stdlib only, so this runs with no venv and no AWS access.

## Why this is enforced rather than reviewed

Every account-scoped value in this repo is meant to be derived from the credentials a deploy runs
under (`aws_identity.py`, `render_aws_config.py`, `governance_origin.py`). That property is invisible
in a diff: adding one literal ARN to make a script work locally looks like a fix, deploys cleanly,
and only misbehaves later, in another account. It also cannot be caught by the type checker or the
unit tests, because a literal account number is perfectly well-typed and every test still passes.

So the invariant is asserted here instead. The same reason the repo asserts the AgentCore tool
allowlist against the SDK's dispatcher rather than against a second hand-written list.

## What counts as publishable

`git ls-files --cached --others --exclude-standard`: tracked files plus new files that are not
ignored. Untracked-but-unignored matters -- a newly added file is exactly where a fresh literal
appears, and it is not in `--cached` yet.

Generated files are ignored by git and therefore out of scope by construction: `aws-targets.json` and
the rendered `*-policy.json` documents legitimately contain the account, which is why they are not
committed.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

#: The AWS docs' reserved example account. Fixtures and documentation should use this.
EXAMPLE_ACCOUNT_ID = "123456789012"


#: Example distribution domains, safe to use in tests and docs. `d111111abcdef8` is the one AWS's own
#: CloudFront documentation uses; the second follows its shape so a test needing two distinct origins
#: (a buyer UI origin and a governance origin, say) can tell them apart.
EXAMPLE_CLOUDFRONT_DOMAINS = frozenset(
    {
        "d111111abcdef8.cloudfront.net",
        "d222222abcdef8.cloudfront.net",
    }
)

#: Suffix to use where a fixture needs a runtime ARN to look structurally real. Ten characters, matching
#: the shape AgentCore generates, but unmistakably not one.
EXAMPLE_RUNTIME_SUFFIX = "EXAMPLE123"


def is_obviously_synthetic(account: str) -> bool:
    """True for account numbers no real account could plausibly be.

    Existing tests use `111111111111`, `222222222222`, `999999999991` and similar to distinguish one
    fixture from another, which is clearer than twelve copies of the example account. Anything built
    from one or two distinct digits is unmistakably invented, so it is not what this check is looking
    for -- the target is a *real* account number copied out of a working deployment.
    """
    return account == EXAMPLE_ACCOUNT_ID or len(set(account)) <= 2

#: Nothing is excluded by path or extension any more.
#:
#: Documentation used to be, on the reasoning that an account number in a historical audit entry is a
#: record rather than a configuration value. That was wrong: a published README, plan or steering file
#: leaks an identifier exactly as much as a published `.py` does, and a reader copying a value out of
#: prose is copying a pointer into an account they cannot reach. `scrub_docs_identity.py` replaced those
#: values with shape-preserving placeholders.
EXCLUDED_PREFIXES: tuple[str, ...] = ()
EXCLUDED_SUFFIXES: tuple[str, ...] = ()

#: Third-party or machine-generated content whose digits are not our identifiers.
EXCLUDED_NAMES = ("package-lock.json", "uv.lock", "motion.js")

PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "aws-account",
        re.compile(r"(?<!\d)\d{12}(?!\d)"),
        "a 12-digit AWS account number -- derive it from aws_identity.account_id() or, in a "
        f"fixture, use the reserved example account {EXAMPLE_ACCOUNT_ID}",
    ),
    (
        "cloudfront-domain",
        re.compile(r"\b[a-z0-9]{13,14}\.cloudfront\.net\b"),
        "a CloudFront distribution domain, which exists only in the account that created it -- "
        "resolve it from the distribution instead (see governance_origin.origin_url())",
    ),
    (
        "cognito-pool-id",
        re.compile(r"\b[a-z]{2}-[a-z]+-\d_[A-Za-z0-9]{9}\b"),
        "a Cognito user pool id -- read COGNITO_USER_POOL_ID from .env, which "
        "deploy_cognito_setup.py writes",
    ),
    (
        "cloudfront-distribution-id",
        re.compile(r"\bE[A-Z0-9]{12,13}\b"),
        "a CloudFront distribution id -- resolve it from the distribution, or use the "
        "<cloudfront-distribution-id> placeholder in documentation",
    ),
    (
        "agentcore-runtime-id",
        # The per-deployment suffix on a runtime name. Matching the suffix rather than a bare token
        # keeps this from firing on the runtime *names*, which are stable and legitimately written down.
        re.compile(r"runtime(?:%2F|/)[A-Za-z_]+-[A-Za-z0-9]{10}\b"),
        "a deployed AgentCore runtime id -- these change per deployment; read the ARN from .env, or "
        "use the -<runtime-id> placeholder in documentation",
    ),
)


def publishable_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    paths = []
    for rel in result.stdout.splitlines():
        if not rel:
            continue
        if rel.startswith(EXCLUDED_PREFIXES) or rel.endswith(EXCLUDED_SUFFIXES):
            continue
        path = REPO_ROOT / rel
        if path.name in EXCLUDED_NAMES or not path.is_file():
            continue
        paths.append(path)
    return paths


def scan(path: Path) -> list[tuple[str, int, str, str]]:
    try:
        text = path.read_text()
    except (UnicodeDecodeError, OSError):
        return []  # Binary or unreadable: no source literals to find.

    findings = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for name, pattern, advice in PATTERNS:
            for match in pattern.finditer(line):
                if name == "aws-account" and is_obviously_synthetic(match.group(0)):
                    continue
                if name == "cloudfront-domain" and match.group(0) in EXAMPLE_CLOUDFRONT_DOMAINS:
                    continue
                if name == "agentcore-runtime-id" and match.group(0).endswith(
                    EXAMPLE_RUNTIME_SUFFIX
                ):
                    continue
                findings.append((name, lineno, match.group(0), advice))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Check for hardcoded AWS identity in source.")
    parser.add_argument(
        "--list-scanned", action="store_true", help="print the files scanned, then the result"
    )
    args = parser.parse_args()

    files = publishable_files()
    if args.list_scanned:
        for path in files:
            print(f"  scanned {path.relative_to(REPO_ROOT)}")
        print()

    total = 0
    advice_seen: dict[str, str] = {}
    for path in sorted(files):
        findings = scan(path)
        if not findings:
            continue
        print(f"{path.relative_to(REPO_ROOT)}")
        for name, lineno, value, advice in findings:
            print(f"  line {lineno}: [{name}] {value}")
            advice_seen[name] = advice
            total += 1

    print()
    print(f"scanned {len(files)} publishable file(s)")
    if not total:
        print("clean: no hardcoded AWS account or account-specific endpoint found")
        return 0

    print(f"FAILED: {total} hardcoded identity value(s)")
    for name, advice in sorted(advice_seen.items()):
        print(f"  {name}: {advice}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
