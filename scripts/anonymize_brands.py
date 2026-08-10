#!/usr/bin/env python3
"""
Anonymize real company/product/site names across the codebase.
Replaces real names with fictional but similar-sounding alternatives.
"""
import os
import re
import sys

# Mapping: real name -> fictional replacement
REPLACEMENTS = {
    # Supply vendors
    "Google AdX": "Goliath AdX",
    "Magnite": "Magnetar",
    "PubMatic": "PubMatrix",
    "OpenX": "OrbitX",
    "Index Exchange": "Apex Exchange",
    "Xandr": "Zephyr",
    "Amazon TAM": "Nimbus TAM",
    "SpotX": "SpectraX",
    "FreeWheel": "FluxWheel",

    # Sites (domains) - order matters: longer/more specific first
    "washingtonpost.com": "capitolpress.com",
    "theguardian.com": "thesentinel.com",
    "nytimes.com": "metrotimes.com",
    "foxnews.com": "falconnews.com",
    "nbcnews.com": "novabroadcast.com",
    "usatoday.com": "nationdaily.com",
    "weather.com": "skycast.com",
    "espn.com": "sportspulse.com",
    "bbc.com": "globalrelay.com",
    "cnn.com": "claritynews.com",

    # Apps - order matters: longer/more specific first
    "Weather Channel": "SkyCast Channel",
    "Fox News App": "Falcon News App",
    "USA Today": "Nation Daily",
    "BBC News": "Global Relay News",
    "NBC News": "Nova Broadcast News",
    "ESPN App": "SportsPulse App",
    "CNN App": "ClarityNews App",
    "NYT App": "MetroTimes App",
    "WaPo App": "CapPress App",
    "Guardian": "Sentinel",

    # Viewability vendors
    "DoubleVerify": "DualCheck",
    "ComScore": "CoreMetric",
}

# These need word-boundary-aware replacement to avoid false positives
WORD_BOUNDARY_REPLACEMENTS = {
    "IAS": "AdShield",
    "Moat": "Beacon",
    "ESPN": "SportsPulse",
    "CNN": "ClarityNews",
}

# File extensions to process
EXTENSIONS = {'.py', '.json', '.csv', '.ts', '.txt', '.md', '.html', '.scss', '.yaml', '.yml'}

# Directories to skip
SKIP_DIRS = {'node_modules', '.git', '.venv', '.venv-deployment', '__pycache__', 'venv', 'dist', '.angular', 'system reference'}


def should_process(filepath):
    """Check if file should be processed based on extension and path."""
    _, ext = os.path.splitext(filepath)
    if ext.lower() not in EXTENSIONS:
        return False
    # Skip the anonymize script itself
    if 'anonymize_brands.py' in filepath:
        return False
    for skip in SKIP_DIRS:
        if f'/{skip}/' in filepath or filepath.startswith(f'{skip}/'):
            return False
    return True


def replace_in_content(content):
    """Apply all replacements to content string. Returns (new_content, changes_made)."""
    original = content
    
    # Apply direct string replacements (order-sensitive, longer strings first)
    for real, fake in REPLACEMENTS.items():
        content = content.replace(real, fake)
    
    # Apply word-boundary replacements using regex
    for real, fake in WORD_BOUNDARY_REPLACEMENTS.items():
        # Match the word only when surrounded by word boundaries or common delimiters
        # But avoid matching inside longer words
        pattern = r'(?<![A-Za-z])' + re.escape(real) + r'(?![A-Za-z])'
        content = re.sub(pattern, fake, content)
    
    return content, content != original


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    changed_files = []
    
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skip directories
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            relpath = os.path.relpath(filepath, root)
            
            if not should_process(relpath):
                continue
            
            try:
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
            except (IOError, UnicodeDecodeError):
                continue
            
            new_content, changed = replace_in_content(content)
            
            if changed:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                changed_files.append(relpath)
                print(f"  ✓ {relpath}")
    
    print(f"\nDone. {len(changed_files)} files updated.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
