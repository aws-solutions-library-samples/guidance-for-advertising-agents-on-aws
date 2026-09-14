"""Superseded by the repo-root `render_agentcore_auth.py`.

This file used to render this seller's Cognito auth block on its own, and `triton-seller` carried a
near-identical copy. Two consequences made that worth collapsing into one script:

* Both pinned `allowedClients` to a single app client, so a second client added by hand was reverted on
  the next render.
* `gotham-seller`, `gotham-reach-service` and `reference-governance` had no renderer at all, so their
  auth blocks were hand-maintained and drifted silently.

Kept as a delegating shim rather than deleted, because `deploy_all.py` invocations and shell history
reference this path. It now forwards to the single implementation so there is no second source of truth.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
TARGET = REPO_ROOT / "render_agentcore_auth.py"

if __name__ == "__main__":
    print(f"[shim] delegating to {TARGET}")
    sys.exit(subprocess.call([sys.executable, str(TARGET), *sys.argv[1:]]))
