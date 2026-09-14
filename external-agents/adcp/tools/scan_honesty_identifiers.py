#!/usr/bin/env python3
"""Identifiers on the honesty theme, which the comment-only scanner cannot see.

This is how a local variable literally named `honest` in run_pipeline.py survived until pass 2.
Strips comments and docstrings first, so only code, string literals and names are searched.
"""
import importlib.util
import re
from collections import defaultdict
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "scan", str(Path(__file__).with_name("scan_defensive_prose.py")))
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)

REPO = Path(__file__).resolve().parent.parent
# `invent` is word-bounded because `inventory` is ordinary domain vocabulary and would otherwise
# swamp the result: 355 hits, almost all of them "inventory".
NEEDLE = re.compile(
    r"honest|fabricat|\binvent(ed|ing|s|ion)?\b|_invent|invent_|truthful|misreport|reassur", re.I)

# The scanner's own pattern list is these words by definition.
SKIP = ["tools/scan_defensive_prose.py", "tools/scan_honesty_identifiers.py"]

per_file = defaultdict(list)
for path in scan.tracked_files():
    rel = str(path.relative_to(REPO))
    if any(s in rel for s in SKIP):
        continue
    try:
        source = path.read_text()
    except UnicodeDecodeError:
        continue
    # Blank out comments/docstrings so only code remains, keeping line numbers intact.
    stripped = source
    for regex in scan.COMMENT_RES:
        stripped = regex.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), stripped)
    for n, line in enumerate(stripped.splitlines(), 1):
        if NEEDLE.search(line):
            per_file[rel].append((n, source.splitlines()[n - 1].rstrip()))

total = sum(len(v) for v in per_file.values())
for rel in sorted(per_file):
    print(f"\n=== {rel}")
    for n, line in per_file[rel]:
        print(f"{n:5d}| {line.strip()[:150]}")
print(f"\n\nTOTAL {total} code lines across {len(per_file)} files")
