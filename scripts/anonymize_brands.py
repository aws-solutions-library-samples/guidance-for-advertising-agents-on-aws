#!/usr/bin/env python3
"""Anonymize real brand names in synthetic data files.

Maps real publisher, agency, and platform names to fictional Meridian Media Group
sub-brands. Run from repo root:

    python scripts/anonymize_brands.py [--dry-run]

With --dry-run, prints what would change without modifying files.
"""

import os
import sys
import argparse
from pathlib import Path

# ── Mapping tables ──

REPLACEMENTS = {
    # Publishers → Meridian Media Group sub-brands
    "ESPN+": "SportsPulse+",
    "ESPN": "SportsPulse",
    "espn.com": "sportspulse.com",
    "Fox Sports App": "Apex Sports App",
    "Fox Sports": "Apex Sports",
    "foxsports.com": "apexsports.com",
    "NBCUniversal": "Apex Media Group",
    "Peacock": "Apex Streaming",
    "peacocktv.com": "apexstreaming.com",
    "Paramount+": "Crestline+",
    "Paramount": "Crestline Entertainment",
    "Warner Bros Discovery": "Horizon Discovery",
    "Warner Bros": "Horizon Discovery",
    "Bleacher Report": "Horizon Sports Digital",
    "Hulu Live Sports": "Crestline Live Sports",
    "Hulu": "Crestline Live",
    "Disney": "Crestline Media",
    "YouTube Sports Shorts": "StreamVault Sports Shorts",
    "YouTube Sports Content": "StreamVault Sports Content",
    "YouTube": "StreamVault",
    "DAZN": "PulseFight",
    "FuboTV": "SportsPulse Premium",
    "Twitch Sports & Esports": "StreamVault Gaming & Esports",
    "Twitch": "StreamVault Gaming",
    "Amazon": "StreamVault Media",
    "The Athletic Premium Video": "GNN Sports Premium Video",
    "The Athletic": "GNN Sports",
    "New York Times": "GNN Media Corp",
    # Agencies
    "Omnicom Media Group": "Atlas Media Group",
    "Omnicom Group": "Atlas Holdings",
    "Omnicom": "Atlas Media",
    "GroupM": "Pinnacle Media",
    "WPP": "Pinnacle Holdings",
    "Publicis Media": "Meridian Agency Partners",
    "Publicis Groupe": "Meridian Agency Holdings",
    "Publicis": "Meridian Agency",
    "IPG Mediabrands": "Compass Media",
    "Interpublic Group": "Compass Holdings",
    "Interpublic": "Compass Holdings",
    "Dentsu International": "Horizon Agency Group",
    "Dentsu Group": "Horizon Agency Holdings",
    "Dentsu": "Horizon Agency",
    # Platforms / DSPs (keep generic where possible)
    "NFL AFC": "Pro Football Conference",
    "NFL": "Pro Football League",
    "NBA": "Pro Basketball League",
    "MLB": "Pro Baseball League",
    "NHL": "National Ice League",
    "NASCAR": "Pro Racing Series",
    "Premier League": "International Soccer League",
    "English Premier League": "International Soccer League",
    "MMA": "Combat Sports",
}

# Files to process (relative to repo root)
TARGET_DIRS = [
    "synthetic_data/mcp_mocks",
    "synthetic_data/advertising-data",
    "synthetic_data/configs",
    "bedrock-adtech-demo/src/assets",
    "agentcore/deployment/agent/agent-instructions-library",
]

TARGET_EXTENSIONS = {".csv", ".json", ".txt", ".md"}

# Files to skip
SKIP_FILES = {"test_adcp_server.py"}


def find_files(repo_root: Path) -> list[Path]:
    """Find all files that should be processed."""
    files = []
    for dir_name in TARGET_DIRS:
        dir_path = repo_root / dir_name
        if not dir_path.exists():
            continue
        for f in dir_path.rglob("*"):
            if f.is_file() and f.suffix in TARGET_EXTENSIONS and f.name not in SKIP_FILES:
                files.append(f)
    return sorted(files)


def apply_replacements(text: str) -> str:
    """Apply all replacements to text. Longer strings replaced first to avoid partial matches."""
    # Sort by length descending so "ESPN+" is replaced before "ESPN"
    sorted_replacements = sorted(REPLACEMENTS.items(), key=lambda x: len(x[0]), reverse=True)
    for old, new in sorted_replacements:
        text = text.replace(old, new)
    return text


def main():
    parser = argparse.ArgumentParser(description="Anonymize brand names in synthetic data")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without modifying files")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    files = find_files(repo_root)

    print(f"Found {len(files)} files to process")
    changed_count = 0

    for filepath in files:
        try:
            original = filepath.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue

        updated = apply_replacements(original)

        if updated != original:
            changed_count += 1
            rel_path = filepath.relative_to(repo_root)
            # Count replacements
            diff_chars = sum(1 for a, b in zip(original, updated) if a != b)
            print(f"  {'[DRY RUN] ' if args.dry_run else ''}Updated: {rel_path}")

            if not args.dry_run:
                filepath.write_text(updated, encoding="utf-8")

    print(f"\n{'Would update' if args.dry_run else 'Updated'} {changed_count} of {len(files)} files")

    if not args.dry_run and changed_count > 0:
        print("\nNext steps:")
        print("  1. Review changes: git diff synthetic_data/")
        print("  2. Re-upload to S3: aws s3 sync synthetic_data/mcp_mocks/ s3://a4a-data-omixaj/mcp_mocks/ --profile genai")
        print("  3. Re-index knowledge base if applicable")
        print("  4. Upload tab configs: python scripts/upload_tab_configs_to_dynamodb.py --table-name a4a-AgentConfig-omixaj --region us-west-2 --profile genai --force")


if __name__ == "__main__":
    main()
