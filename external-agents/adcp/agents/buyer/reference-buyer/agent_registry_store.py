"""
Durable store for runtime-registered seller and governance agents.

Why this exists
---------------
`seller_agents.py` and `governance_agents.py` build their registries from environment variables
(`SELLER_AGENTS_JSON`, `GOVERNANCE_AGENTS_JSON`) baked into the deployed runtime. Changing which
agents the buyer can reach therefore meant editing `.env` and redeploying. This module holds
registrations an admin creates at runtime from the UI, so a new seller or a re-pointed governance
agent takes effect on the next buyer request with no redeploy.

It stores ONLY the connection resources — url, transport, auth — the buyer resolves before making an
AdCP call. It does not touch the AdCP wire contract in any way: `resolve_seller_agent()` and
`resolve_governance_agent()` keep returning the exact same shape whether an entry came from the
environment or from here. (See aidlc-docs/construction/agent-admin/design.md, FR-0.)

Storage
-------
Reuses the project's existing sessions DynamoDB table (`SESSIONS_TABLE_NAME`, via
`session_records.table()`), so there is no new table and no IAM change — the buyer runtime role
already reads and writes that table. Registration items live under a distinct partition key and,
crucially, carry NO `ttl` attribute: DynamoDB's TTL only deletes items that have the attribute, so
registrations are durable while session records (which set `ttl`) expire. Verified against
`session_records.py`.

    pk                     sk            attributes
    --------------------   -----------   ------------------------------------------------
    AGENTREG#SELLER        <agent_id>    id, name, url, agent_url?, transport, auth_type,
                                          auth_token?, created_at, updated_at, updated_by
    AGENTREG#GOVERNANCE    <agent_id>    id, name, url, transport, auth_type, auth_token?,
                                          created_at, updated_at, updated_by

The bearer token for an external ("static_bearer") agent is stored in `auth_token`. This is a
documented tradeoff (design NFR-3): consistent with this reference project's existing "token in
config" trust model, weaker than AWS Secrets Manager, which is the recorded hardening path. The token
value is only ever read on the server, to build the `Authorization` header; it is never returned to
the browser (see `redact()` and every caller that lists registrations).

Resilience
----------
Read failures never break the AdCP call path. The registry merge in `seller_agents.py` /
`governance_agents.py` wraps `list_with_secrets()` in try/except and falls back to environment-only
entries, so a store outage degrades this admin feature and nothing else (FR-8).
"""

from datetime import datetime, timezone
from typing import Any, Literal

from boto3.dynamodb.conditions import Key

import session_records

KIND_SELLER = "seller"
KIND_GOVERNANCE = "governance"
AgentKind = Literal["seller", "governance"]

_PK_BY_KIND = {
    KIND_SELLER: "AGENTREG#SELLER",
    KIND_GOVERNANCE: "AGENTREG#GOVERNANCE",
}

# Tombstones that HIDE an environment built-in. An env-configured seller/governance agent cannot be
# deleted (it comes from SELLER_AGENTS_JSON etc.), but an admin can remove it from the effective
# registry by hiding it: a marker item here, honored by seller_agents/governance_agents `_load_registry`,
# so the buyer stops fanning out to / resolving it — without a redeploy, and reversibly (unhide).
# Own partition (not mixed with the registration items) so `list_with_secrets` never sees a tombstone.
# No `ttl`, so a tombstone is as durable as a registration.
_HIDE_PK_BY_KIND = {
    KIND_SELLER: "AGENTREGHIDE#SELLER",
    KIND_GOVERNANCE: "AGENTREGHIDE#GOVERNANCE",
}

# Attributes that make up a registration, in the shape the registry loaders consume. `auth_token`
# (static_bearer) and `client_secret` (m2m_oauth) are the secrets and are treated specially everywhere
# they could escape (see `redact`). client_id/token_url/scope/audience are non-secret and pass through.
_STORED_FIELDS = (
    "id", "name", "url", "agent_url", "transport", "auth_type",
    "auth_token",
    "client_id", "client_secret", "token_url", "scope", "audience",
)
# The secret fields, never sent to the browser -- each surfaced only as a has_stored_* boolean.
_SECRET_FIELDS = ("auth_token", "client_secret")


class AgentRegistryStoreError(RuntimeError):
    """Raised when the registration store is unconfigured or a DynamoDB call fails."""


def _pk(kind: str) -> str:
    if kind not in _PK_BY_KIND:
        raise AgentRegistryStoreError(f"Unknown agent kind {kind!r}; expected one of {tuple(_PK_BY_KIND)}.")
    return _PK_BY_KIND[kind]


def _hide_pk(kind: str) -> str:
    if kind not in _HIDE_PK_BY_KIND:
        raise AgentRegistryStoreError(f"Unknown agent kind {kind!r}; expected one of {tuple(_HIDE_PK_BY_KIND)}.")
    return _HIDE_PK_BY_KIND[kind]


def _table():
    """The shared sessions table, surfacing its config error as this module's own type."""
    try:
        return session_records.table()
    except session_records.SessionRecordError as exc:
        raise AgentRegistryStoreError(str(exc)) from exc


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact(entry: dict[str, Any]) -> dict[str, Any]:
    """A registration safe to send to the browser: the token value removed, replaced by a boolean.

    The single chokepoint for the "secrets never reach the browser" rule (FR-7). Every list/response
    path runs through here.
    """
    out = {
        k: entry.get(k)
        for k in _STORED_FIELDS
        if k not in _SECRET_FIELDS and entry.get(k) is not None
    }
    # Each secret is replaced by a boolean presence flag, never its value.
    out["has_stored_token"] = bool(entry.get("auth_token"))
    out["has_stored_secret"] = bool(entry.get("client_secret"))
    out["source"] = "stored"
    # Audit metadata, non-secret, useful in the UI.
    for meta in ("created_at", "updated_at", "updated_by"):
        if entry.get(meta):
            out[meta] = entry[meta]
    return out


def list_with_secrets(kind: str) -> list[dict[str, Any]]:
    """Full stored registrations for a kind, INCLUDING `auth_token`. Server-side use only.

    Used by the registry merge and by `resolve_*` to build auth headers. Never hand the result of this
    to a browser-visible payload — use `list_public()` for that.
    """
    table = _table()
    try:
        resp = table.query(KeyConditionExpression=Key("pk").eq(_pk(kind)))
    except table.meta.client.exceptions.ResourceNotFoundException as exc:
        raise AgentRegistryStoreError(f"Registration table not found: {exc}") from exc
    entries: list[dict[str, Any]] = []
    for item in resp.get("Items", []):
        entry = {k: item[k] for k in _STORED_FIELDS if item.get(k) not in (None, "")}
        for meta in ("created_at", "updated_at", "updated_by"):
            if item.get(meta):
                entry[meta] = item[meta]
        entries.append(entry)
    return entries


def list_public(kind: str) -> list[dict[str, Any]]:
    """Stored registrations with token values redacted, for a browser-visible list."""
    return [redact(e) for e in list_with_secrets(kind)]


def get(kind: str, agent_id: str) -> dict[str, Any] | None:
    """One stored registration WITH its token, or None. Server-side use only."""
    table = _table()
    resp = table.get_item(Key={"pk": _pk(kind), "sk": agent_id})
    item = resp.get("Item")
    if not item:
        return None
    entry = {k: item[k] for k in _STORED_FIELDS if item.get(k) not in (None, "")}
    for meta in ("created_at", "updated_at", "updated_by"):
        if item.get(meta):
            entry[meta] = item[meta]
    return entry


def put(kind: str, entry: dict[str, Any], *, updated_by: str = "") -> dict[str, Any]:
    """Create or replace a registration. `entry` must already be validated by the caller.

    Writes NO `ttl` attribute, so the item is durable. Returns the redacted stored view.
    """
    table = _table()
    agent_id = str(entry.get("id", "")).strip()
    if not agent_id:
        raise AgentRegistryStoreError("A registration must have a non-empty id.")

    existing = get(kind, agent_id)
    now = _now_iso()
    item: dict[str, Any] = {
        "pk": _pk(kind),
        "sk": agent_id,
        "created_at": existing.get("created_at") if existing else now,
        "updated_at": now,
    }
    if updated_by:
        item["updated_by"] = updated_by
    for field in _STORED_FIELDS:
        value = entry.get(field)
        if value not in (None, ""):
            item[field] = value
    # Make sure an edit that switches auth_type does not leave a stale secret behind: only the secret
    # that belongs to the current auth_type may persist. (auth_token for static_bearer, client_secret
    # for m2m_oauth; cognito_bearer carries neither.)
    auth_type = entry.get("auth_type")
    if auth_type != "static_bearer":
        item.pop("auth_token", None)
    if auth_type != "m2m_oauth":
        item.pop("client_secret", None)

    table.put_item(Item=session_records.to_dynamo(item))
    return redact(get(kind, agent_id) or item)


def delete(kind: str, agent_id: str) -> bool:
    """Remove a stored registration. Returns True if one existed. Env built-ins are untouched."""
    table = _table()
    existed = get(kind, agent_id) is not None
    table.delete_item(Key={"pk": _pk(kind), "sk": agent_id})
    return existed


def hide(kind: str, agent_id: str, *, updated_by: str = "") -> None:
    """Tombstone an id so the effective registry excludes it (an env built-in can't be deleted, only
    hidden). Idempotent: hiding an already-hidden id just refreshes the marker. Durable (no ttl)."""
    agent_id = agent_id.strip()
    if not agent_id:
        raise AgentRegistryStoreError("Cannot hide an empty agent id.")
    table = _table()
    item: dict[str, Any] = {"pk": _hide_pk(kind), "sk": agent_id, "hidden_at": _now_iso()}
    if updated_by:
        item["hidden_by"] = updated_by
    table.put_item(Item=item)


def unhide(kind: str, agent_id: str) -> bool:
    """Remove a tombstone, so a hidden built-in returns to the effective registry. True if one existed."""
    table = _table()
    existed = table.get_item(Key={"pk": _hide_pk(kind), "sk": agent_id}).get("Item") is not None
    table.delete_item(Key={"pk": _hide_pk(kind), "sk": agent_id})
    return existed


def hidden_ids(kind: str) -> set[str]:
    """The set of ids currently hidden for this kind. Empty on any store problem (resilient)."""
    table = _table()
    try:
        resp = table.query(KeyConditionExpression=Key("pk").eq(_hide_pk(kind)))
    except table.meta.client.exceptions.ResourceNotFoundException as exc:
        raise AgentRegistryStoreError(f"Registration table not found: {exc}") from exc
    return {str(item["sk"]) for item in resp.get("Items", []) if item.get("sk")}
