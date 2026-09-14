"""
Registry of AdCP campaign governance agents this buyer can bind to, loaded
from .env. Mirrors seller_agents.py's shape and resolution logic exactly
(BR-U2-1) so the two config surfaces stay consistent for anyone reading
either.

Configuration lives in GOVERNANCE_AGENTS_JSON (a JSON array in .env), e.g.:

    GOVERNANCE_AGENTS_JSON=[
      {
        "id": "boltive",
        "name": "Boltive Campaign Governance",
        "url": "https://governance.boltive.example/adcp/mcp",
        "transport": "mcp",
        "auth_type": "static_bearer",
        "auth_token_env": "BOLTIVE_AUTH_TOKEN"
      }
    ]

Unlike SELLER_AGENTS_JSON, exactly one entry is resolved per account
(BR-U2-2) — AdCP's own account.governance_agents carries maxItems: 1, so
one agent owns an account's plans. resolve_governance_agent() therefore
takes no id argument and always returns the configured entry (or the
first one, if more than one is ever listed, though the schema does not
expect that).

This module REPLACES adcp_tools.py's old GOVERNANCE_AGENT_MCP_URL_ENV /
resolve_governance_agent() mechanism (BR-U2-3/BR-U2-4), which could only
express a single in-account AgentCore URL and had no way to express
Boltive's external static_bearer shape. GOVERNANCE_AGENT_MCP_URL becomes
one GOVERNANCE_AGENTS_JSON entry with auth_type "cognito_bearer" instead.

Two auth_type values, matching seller_agents.py:
  - "static_bearer": a long-lived bearer token read from the env var
    named by auth_token_env (e.g. Boltive's token, or a mock governance
    service's).
  - "cognito_bearer": mint a fresh Cognito access token per call using
    this project's existing test user. Used for a governance agent
    deployed to this same AWS account's AgentCore Runtime.
"""

import json
import os
from typing import Any, Literal, TypedDict

from dotenv import load_dotenv

from auth import get_m2m_access_token, get_test_user_access_token

load_dotenv()


class GovernanceAgentConfig(TypedDict):
    id: str
    name: str
    url: str
    transport: Literal["mcp", "a2a"]
    auth_type: Literal["static_bearer", "cognito_bearer", "m2m_oauth"]
    # static_bearer names EITHER the env var holding its token (`auth_token_env`) OR carries the token
    # value inline (`auth_token`, how a runtime-registered entry stores it). Mirrors seller_agents.
    auth_token_env: str | None
    auth_token: str | None
    # m2m_oauth (OAuth2 client_credentials): client_id + token_url + client_secret (stored at rest like
    # auth_token); scope/audience optional. Access token minted per call and cached in-process. Mirrors
    # seller_agents.
    client_id: str | None
    client_secret: str | None
    token_url: str | None
    scope: str | None
    audience: str | None
    # "env" or "stored"; additive, used only by the public listing. Resolve does not read it.
    source: str


class GovernanceAgentError(RuntimeError):
    """Raised when the governance-agent registry is misconfigured or unset."""


_VALID_TRANSPORTS = ("mcp", "a2a")
_VALID_AUTH_TYPES = ("static_bearer", "cognito_bearer", "m2m_oauth")


def validate_governance_entry(entry: dict[str, Any]) -> GovernanceAgentConfig:
    """Validate one governance registration and normalise it into a GovernanceAgentConfig.

    Shared by the environment parser and the admin registration path so a stored entry passes exactly
    the checks an env entry does. A stored static_bearer entry carries its token inline in
    `auth_token`; an environment entry names the env var in `auth_token_env`.
    """
    if not isinstance(entry, dict):
        raise GovernanceAgentError("A governance agent entry must be an object.")
    for key in ("id", "name", "url", "auth_type"):
        if not str(entry.get(key, "")).strip():
            raise GovernanceAgentError(f"Governance agent entry is missing required key {key!r}.")
    auth_type = entry["auth_type"]
    if auth_type not in _VALID_AUTH_TYPES:
        raise GovernanceAgentError(
            f"Governance agent {entry['id']!r} has auth_type {auth_type!r}; expected one of {_VALID_AUTH_TYPES}."
        )
    if auth_type == "static_bearer" and not (entry.get("auth_token_env") or entry.get("auth_token")):
        raise GovernanceAgentError(
            f"Governance agent {entry['id']!r} uses static_bearer but supplies neither a token nor an "
            "auth_token_env naming one."
        )
    if auth_type == "m2m_oauth" and not (
        str(entry.get("client_id", "")).strip()
        and str(entry.get("token_url", "")).strip()
        and str(entry.get("client_secret", "")).strip()
    ):
        raise GovernanceAgentError(
            f"Governance agent {entry['id']!r} uses m2m_oauth but is missing one of client_id, "
            "token_url or client_secret (scope and audience are optional)."
        )
    transport = entry.get("transport", "mcp")
    if transport not in _VALID_TRANSPORTS:
        raise GovernanceAgentError(
            f"Governance agent {entry['id']!r} has transport {transport!r}; expected one of {_VALID_TRANSPORTS}."
        )
    return GovernanceAgentConfig(
        id=str(entry["id"]),
        name=str(entry["name"]),
        url=str(entry["url"]),
        transport=transport,
        auth_type=auth_type,
        auth_token_env=entry.get("auth_token_env"),
        auth_token=entry.get("auth_token"),
        client_id=entry.get("client_id"),
        client_secret=entry.get("client_secret"),
        token_url=entry.get("token_url"),
        scope=entry.get("scope"),
        audience=entry.get("audience"),
        source=str(entry.get("source") or "env"),
    )


def _load_env_entries() -> list[GovernanceAgentConfig]:
    """Governance entries from GOVERNANCE_AGENTS_JSON.

    Returns [] when the env var is unset -- unlike before, this no longer raises, because a governance
    agent may now be supplied entirely at runtime through the registration store. "Nothing configured"
    is decided by `_load_registry()` seeing zero entries from either source. Per-entry validation is
    delegated to `validate_governance_entry` so env and stored entries pass identical checks.
    """
    raw = os.environ.get("GOVERNANCE_AGENTS_JSON", "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GovernanceAgentError(f"GOVERNANCE_AGENTS_JSON is not valid JSON: {exc}") from exc

    if not isinstance(parsed, list) or not parsed:
        raise GovernanceAgentError("GOVERNANCE_AGENTS_JSON must be a non-empty JSON array.")

    seen_ids: set[str] = set()
    entries: list[GovernanceAgentConfig] = []
    for i, entry in enumerate(parsed):
        if not isinstance(entry, dict):
            raise GovernanceAgentError(f"GOVERNANCE_AGENTS_JSON[{i}] must be an object.")
        if entry.get("id") in seen_ids:
            raise GovernanceAgentError(f"Duplicate governance agent id '{entry.get('id')}' in GOVERNANCE_AGENTS_JSON.")
        try:
            config = validate_governance_entry({**entry, "source": "env"})
        except GovernanceAgentError as exc:
            raise GovernanceAgentError(f"GOVERNANCE_AGENTS_JSON[{i}]: {exc}") from exc
        seen_ids.add(config["id"])
        entries.append(config)
    return entries


def _load_stored_entries() -> list[GovernanceAgentConfig]:
    """Runtime-registered governance entries from the store, resilient (see seller_agents equivalent).

    Any store problem yields [] so the environment-configured governance path is never broken by the
    overlay (design FR-8 / NFR-1).
    """
    try:
        import agent_registry_store

        out: list[GovernanceAgentConfig] = []
        for raw in agent_registry_store.list_with_secrets(agent_registry_store.KIND_GOVERNANCE):
            try:
                out.append(validate_governance_entry({**raw, "source": "stored"}))
            except GovernanceAgentError:
                continue
        return out
    except Exception:  # noqa: BLE001 - overlay must never break the env-configured path
        return []


def _load_hidden_ids() -> set[str]:
    """Ids an admin has removed from the effective governance registry. Resilient: any store problem
    yields an empty set, so removal degrades to "not removed" rather than breaking the AdCP path."""
    try:
        import agent_registry_store

        return agent_registry_store.hidden_ids(agent_registry_store.KIND_GOVERNANCE)
    except Exception:  # noqa: BLE001 - the hide overlay must never break the env-configured path
        return set()


def _load_registry(include_hidden: bool = False) -> list[GovernanceAgentConfig]:
    """The effective governance registry: env entries, then stored entries merged on top (by id),
    minus any the admin has hidden. `include_hidden=True` is for the admin view, which must still show
    a removed built-in so it can be restored."""
    by_id = {e["id"]: e for e in _load_env_entries()}
    for stored in _load_stored_entries():
        by_id[stored["id"]] = stored
    if not include_hidden:
        for hidden_id in _load_hidden_ids():
            by_id.pop(hidden_id, None)
    merged = list(by_id.values())
    if not merged:
        raise GovernanceAgentError(
            "No governance agent is configured. Set GOVERNANCE_AGENTS_JSON in .env, or register one "
            "from the admin screen."
        )
    return merged


def governance_configured() -> bool:
    """Whether a governance agent is EFFECTIVELY configured (from either source, and not removed).

    Used to decide "not governed" (proceed, BR-U2-11) vs an actual call. Reads the effective registry
    so a governance agent removed from the admin screen makes the buyer stop governing — the same way
    adding one makes it start — without a redeploy. Resilient: a store failure falls back to the env
    answer so the AdCP path is never broken by the overlay.
    """
    try:
        return len(_load_registry()) > 0
    except GovernanceAgentError:
        return False
    except Exception:  # noqa: BLE001 - overlay/store failure: fall back to the env-only answer
        return bool(os.environ.get("GOVERNANCE_AGENTS_JSON", "").strip())


def list_governance_agents_public(include_hidden: bool = False) -> list[dict[str, str]]:
    """Public metadata for the configured governance agents: id, name, url, transport, source.

    Never includes a token value. Used by /config so the admin UI can display governance registrations
    the way it already displays sellers. Returns [] when nothing is configured, rather than raising.
    `include_hidden=True` returns removed built-ins too, for the admin view's restore affordance.
    """
    try:
        registry = _load_registry(include_hidden=include_hidden)
    except GovernanceAgentError:
        return []
    return [
        {
            "id": e["id"],
            "name": e["name"],
            "url": e["url"],
            "transport": e["transport"],
            "source": e.get("source", "env"),
        }
        for e in registry
    ]


def resolve_governance_agent() -> dict[str, Any]:
    """Resolve the configured governance agent into {"id", "name", "url", "transport", "headers"}.

    Same shape _call_seller_tool already takes (see adcp_tools.py), so the governance agent reuses the
    buyer's existing MCP/A2A transport. Exactly one agent is resolved (BR-U2-2); if more than one entry is
    ever listed, the first is used and that is a configuration choice for the operator to avoid.

    Raises GovernanceAgentError if GOVERNANCE_AGENTS_JSON is unset, malformed, or a static_bearer entry's
    token env var is empty.
    """
    registry = _load_registry()
    entry = registry[0]
    headers = _build_auth_headers(entry)
    return {
        "id": entry["id"],
        "name": entry["name"],
        "url": entry["url"],
        "transport": entry["transport"],
        "headers": headers,
    }


def _build_auth_headers(entry: GovernanceAgentConfig) -> dict[str, str]:
    if entry["auth_type"] == "static_bearer":
        # A runtime-registered entry carries its token inline; an environment entry names the env var
        # that holds it. Either produces the identical Authorization header.
        inline = entry.get("auth_token")
        if inline:
            return {"Authorization": f"Bearer {inline}"}
        env_var = entry.get("auth_token_env")
        if not env_var:
            raise GovernanceAgentError(
                f"Governance agent '{entry['id']}' uses static_bearer but has neither a stored token "
                "nor an auth_token_env."
            )
        token = os.environ.get(env_var, "")
        if not token:
            raise GovernanceAgentError(
                f"Governance agent '{entry['id']}' needs {env_var} set in .env but it's empty."
            )
        return {"Authorization": f"Bearer {token}"}

    if entry["auth_type"] == "m2m_oauth":
        # OAuth2 client_credentials: mint (or reuse a cached) access token from this registration's
        # own client_id / client_secret / token_url. Mirrors seller_agents. Secret stored at rest;
        # access token ephemeral + cached in-process.
        client_id = entry.get("client_id")
        client_secret = entry.get("client_secret")
        token_url = entry.get("token_url")
        if not (client_id and client_secret and token_url):
            raise GovernanceAgentError(
                f"Governance agent '{entry['id']}' uses m2m_oauth but is missing client_id, "
                "client_secret or token_url."
            )
        token = get_m2m_access_token(
            token_url,
            client_id,
            client_secret,
            scope=entry.get("scope"),
            audience=entry.get("audience"),
        )
        return {"Authorization": f"Bearer {token}"}

    # cognito_bearer: mint a fresh access token for this project's own Cognito test user, the same
    # mechanism seller_agents.py uses for in-account sellers.
    token = get_test_user_access_token()
    return {"Authorization": f"Bearer {token}"}
