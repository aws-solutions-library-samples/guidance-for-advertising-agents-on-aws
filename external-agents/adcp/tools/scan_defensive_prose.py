#!/usr/bin/env python3
"""Inventory defensive-honesty prose in comments, so the cleanup can be sized and done later.

Scans comment/docstring text only. Code and string literals are left alone: a variable actually named
`fabricated` or a UI string reading "unknown" is not the problem.
"""

import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path("/Users/zellest/repos/adcp/buyer agent")

# Grouped so the report says WHICH habit, not just that a file is guilty.
PATTERNS = {
    "asserts-honesty": [
        r"\bhonest(ly|y)?\b",
        r"\bthe honest (form|thing|answer|figure|state)\b",
        r"\btruthful\b",
    ],
    "narrates-non-fabrication": [
        r"\bfabricat\w+",
        r"\binvent(ed|ing|s)?\b",
        r"\bnever a (fabricated|random|placeholder|hardcoded)\b",
        r"\bnot (a |an )?(placeholder|fabrication|invented|hardcoded default)\b",
        r"\bno-fabricated-data\b",
    ],
    "emphasis-caps-on-truth": [
        # Only caps used to insist on truthfulness. Bare NOT/NEVER are ordinary emphasis
        # ("deliberately NOT send Mcp-Session-Id") and were dropped as noise.
        r"\bREAL\b",
        r"\bNEVER (a |an )?(fabricat|invent|placeholder|guess|random|hardcod)",
        r"\bNOT (a |an )?(fabricat|invent|placeholder|fake|guess)",
    ],
    "moralising": [
        r"\bfalse claim\b",
        r"\bmisreport\w*",
        r"\breassuring\b",
        r"\boverclaim\w*",
        r"\bthe failure mode this .{0,40}exists to avoid\b",
        r"\bwould be (a |the )?(lie|false|dishonest)\b",
    ],
    "defensive-real-hedge": [
        # "genuinely"/"provably" were dropped: usually ordinary emphasis about behaviour, not honesty.
        r"\breal (data|value|values|figure|figures|measurement|measurements|answer)\b",
        r"\bnot (real|really) (data|measured)\b",
    ],
}

COMMENT_RES = [
    re.compile(r"/\*[\s\S]*?\*/"),          # block comments (ts, css, java)
    re.compile(r"(?m)^[ \t]*//.*$"),        # whole-line // comments
    re.compile(r"(?m)[ \t]//.*$"),          # trailing // comments
    re.compile(r"(?m)^[ \t]*#.*$"),         # whole-line # comments (python, yaml)
    # Trailing # comments. Added after a `# noqa: BLE001 - report, don't fabricate a fallback list`
    # went unseen through five passes: only whole-line # was matched.
    re.compile(r"(?m)[ \t]#(?!\w).*$"),
    re.compile(r'"""[\s\S]*?"""'),          # python docstrings
]

SUFFIXES = {".py", ".ts", ".tsx", ".css", ".js"}
SKIP_DIRS = {"node_modules", ".venv", "cdk.out", "__pycache__", "dist", ".git", "aidlc-docs"}

# These two files' docstrings and pattern lists are these words by definition, so they score against
# themselves and the total never reaches its floor.
SKIP_FILES = {"tools/scan_defensive_prose.py", "tools/scan_honesty_identifiers.py"}


def tracked_files() -> list[Path]:
    # `--others --exclude-standard` adds files that exist but are not committed yet, while still
    # honouring .gitignore. Without it a whole directory of new code scores zero by being invisible,
    # which is how `ui/src/lib/journey/` and `agents/governance/` were missed for four passes.
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.split("\n")
    files = []
    seen: set[str] = set()
    for rel in out:
        if rel in seen:
            continue
        seen.add(rel)
        if not rel:
            continue
        path = REPO / rel
        if path.suffix not in SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if rel in SKIP_FILES:
            continue
        if path.exists():
            files.append(path)
    return files


def comment_text(source: str) -> str:
    chunks = []
    for regex in COMMENT_RES:
        chunks.extend(regex.findall(source))
    return "\n".join(chunks)


def main() -> None:
    per_file: dict[str, Counter] = defaultdict(Counter)
    per_category: Counter = Counter()
    examples: dict[str, list[str]] = defaultdict(list)

    files = tracked_files()
    for path in files:
        try:
            source = path.read_text()
        except UnicodeDecodeError:
            continue
        comments = comment_text(source)
        if not comments:
            continue
        rel = str(path.relative_to(REPO))
        for category, patterns in PATTERNS.items():
            hits = 0
            for pattern in patterns:
                found = re.findall(pattern, comments)
                hits += len(found)
            if hits:
                per_file[rel][category] = hits
                per_category[category] += hits
                if len(examples[category]) < 6:
                    for line in comments.splitlines():
                        if any(re.search(p, line) for p in patterns) and len(line.strip()) > 40:
                            examples[category].append(f"{rel}: {line.strip()[:150]}")
                            break

    totals = {rel: sum(c.values()) for rel, c in per_file.items()}
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)

    print(f"# Defensive-honesty prose inventory\n")
    print(f"Scanned {len(files)} source files (committed and uncommitted). "
          f"{len(ranked)} contain at least one match; {sum(totals.values())} matches total.\n")

    print("## By habit\n")
    print("| habit | matches |")
    print("|---|---|")
    for category, count in per_category.most_common():
        print(f"| {category} | {count} |")

    print("\n## Worst files\n")
    print("| file | matches | habits |")
    print("|---|---|---|")
    for rel, count in ranked[:30]:
        habits = ", ".join(f"{k}:{v}" for k, v in per_file[rel].most_common())
        print(f"| `{rel}` | {count} | {habits} |")

    print("\n## Sample lines\n")
    for category in per_category:
        print(f"**{category}**\n")
        for sample in examples[category][:4]:
            print(f"- `{sample}`")
        print()

    print(f"\n## Long tail\n")
    print(f"{len([r for r, c in ranked if c <= 3])} files have 3 or fewer matches.")


if __name__ == "__main__":
    main()
