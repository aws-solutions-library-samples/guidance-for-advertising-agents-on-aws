"""Cross-agent progress milestones for the AAMP buyer.

Over A2A, the only thing a caller observes while a task runs is the model's own text
deltas — there is no separate channel for "what am I doing right now" that the caller can
tell apart from the answer itself. So instead of inferring progress, each tool appends a
milestone row to a small DynamoDB table keyed by the A2A context id, and the calling
agent polls that key while it waits.

Only real events are recorded: every ``emit`` call sits next to the work it describes, so
a milestone appearing means that step actually ran. Rows are transactional and
short-lived (TTL on ``expires_at``).

Every function here is best-effort. Recording progress must never fail a campaign plan,
so all failures are swallowed and logged at debug level.

Environment:
    AAMP_PROGRESS_TABLE        DynamoDB table name. Unset/empty disables emission.
    AAMP_PROGRESS_TTL_SECONDS  Row lifetime, default 3600.
    AAMP_PROGRESS_AGENT_NAME   Attribution shown in the caller's UI, default AAMPBuyerAgent.
"""

from __future__ import annotations

import itertools
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

# A single A2A task should produce a handful of milestones. The cap keeps a runaway loop
# from writing unbounded rows; it is far above the ~10 a real plan emits.
_MAX_MILESTONES = 60

_DEFAULT_TTL_SECONDS = 3600
_DEFAULT_AGENT_NAME = "AAMPBuyerAgent"

_table_lock = threading.Lock()
_table = None  # cached boto3 DynamoDB Table resource


def _progress_table():
    """Return the cached DynamoDB Table, or None when progress is not configured."""
    global _table
    name = (os.environ.get("AAMP_PROGRESS_TABLE") or "").strip()
    if not name:
        return None
    with _table_lock:
        if _table is None:
            import boto3

            region = os.environ.get("AWS_REGION", "us-east-1")
            _table = boto3.resource("dynamodb", region_name=region).Table(name)
        return _table


class ProgressEmitter:
    """Append-only milestone writer for one A2A context.

    Sort keys are zero-padded from a per-emitter counter, so a lexicographic Query
    returns milestones in the order they happened.
    """

    def __init__(self, context_id: str) -> None:
        self.context_id = (context_id or "").strip()
        self._seq = itertools.count(1)
        self._lock = threading.Lock()
        self._emitted = 0

    @property
    def enabled(self) -> bool:
        return bool(self.context_id) and _progress_table() is not None

    def emit(self, message: str) -> None:
        """Record one milestone. Never raises."""
        try:
            message = (message or "").strip()
            if not message or not self.context_id:
                return
            table = _progress_table()
            if table is None:
                return
            with self._lock:
                if self._emitted >= _MAX_MILESTONES:
                    return
                self._emitted += 1
                seq = next(self._seq)

            ttl = int(os.environ.get("AAMP_PROGRESS_TTL_SECONDS", _DEFAULT_TTL_SECONDS))
            now = time.time()
            table.put_item(
                Item={
                    "pk": f"PROGRESS#{self.context_id}",
                    "sk": f"{seq:05d}",
                    "message": message[:400],
                    "agent_name": os.environ.get("AAMP_PROGRESS_AGENT_NAME", _DEFAULT_AGENT_NAME),
                    "ts": int(now * 1000),
                    "expires_at": int(now) + ttl,
                }
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("progress emit failed (ignored): %s: %s", type(e).__name__, e)
