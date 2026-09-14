"""
Registry of AdCP seller (sales) agents the buyer agent can target, loaded
from .env. Lets the browser UI offer a dropdown of real seller agents
instead of hardcoding a single seller endpoint, and lets adcp_tools.py
resolve the actual URL + auth headers for whichever seller was selected on
a given request/session.

Configuration lives in SELLER_AGENTS_JSON (a JSON array in .env), e.g.:

    SELLER_AGENTS_JSON=[
      {
        "id": "external-seller",
        "name": "Third-party seller agent (MCP)",
        "url": "https://adcp.example.com/adcp/mcp",
        "transport": "mcp",
        "auth_type": "static_bearer",
        "auth_token_env": "EXTERNAL_SELLER_AUTH_TOKEN"
      },
      {
        "id": "external-seller-a2a",
        "name": "Third-party seller agent (A2A)",
        "url": "https://a2a.example.com/a2a",
        "transport": "a2a",
        "auth_type": "static_bearer",
        "auth_token_env": "EXTERNAL_SELLER_AUTH_TOKEN"
      },
      {
        "id": "reference",
        "name": "AdCP Reference Test Seller (AgentCore)",
        "url": "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/.../invocations?qualifier=DEFAULT",
        "transport": "mcp",
        "auth_type": "cognito_bearer"
      }
    ]

`transport` picks which AdCP wire binding adcp_tools.py uses to reach that
entry's `url`: "mcp" (default if omitted, for backward compatibility with
entries written before this field existed) opens an MCP session
(streamable-http) and calls tools by name. "a2a" sends a JSONRPC
message/send request with a structured `{skill, parameters}` DataPart per
AdCP's A2A binding — the tool name doubles as the skill name, since a
conformant AdCP-over-A2A seller advertises the same task names
(get_products, get_adcp_capabilities, etc.) as skills. `url` for an "a2a"
entry must be the seller's direct JSONRPC endpoint, not its agent-card
location: some sellers serve the agent card from a different path than the
invoke endpoint, so pointing `url` at the card yields a 404 on message/send.

Two auth_type values are supported (independent of transport):
  - "static_bearer": a long-lived bearer token read from the env var named
    by auth_token_env (a third-party seller's own token). Never stored inline
    in the JSON itself, so the token isn't duplicated across .env and this
    blob.
  - "cognito_bearer": mint a fresh Cognito access token per call using this
    project's existing test user (auth.get_test_user_access_token()). Used
    for seller agents deployed to this same AWS account's AgentCore
    Runtime, gated by the same Cognito user pool the buyer agent itself
    uses.

DEFAULT_SELLER_AGENT_ID picks which entry is used when a request doesn't
specify one (e.g. direct API calls, or a UI that hasn't loaded the
dropdown yet).
"""

import json
import os
from typing import Any, Literal, TypedDict

from dotenv import load_dotenv

from auth import get_m2m_access_token, get_test_user_access_token

load_dotenv()


class SellerAgentConfig(TypedDict):
    id: str
    name: str
    url: str
    agent_url: str
    transport: Literal["mcp", "a2a"]
    auth_type: Literal["static_bearer", "cognito_bearer", "m2m_oauth"]
    # A static_bearer entry names EITHER the env var holding its token (`auth_token_env`, the
    # environment-configured shape) OR carries the token value inline (`auth_token`, how a
    # runtime-registered entry from the admin UI stores it). Exactly one is set for static_bearer;
    # both are None for cognito_bearer.
    auth_token_env: str | None
    auth_token: str | None
    # m2m_oauth (OAuth2 client_credentials): client_id + token_url identify the client and its token
    # endpoint; client_secret is the secret (stored at rest exactly like `auth_token`); scope/audience
    # are optional. All None for the other auth types. The access token itself is never stored -- it is
    # minted per call and cached in-process (auth.get_m2m_access_token).
    client_id: str | None
    client_secret: str | None
    token_url: str | None
    scope: str | None
    audience: str | None
    # "env" for entries from SELLER_AGENTS_JSON, "stored" for runtime registrations. Additive: used
    # only by the public listing so the UI can distinguish built-in from custom. The resolve path
    # does not read it, so the resolved shape is unchanged.
    source: str


class SellerAgentError(RuntimeError):
    """Raised when the seller-agent registry is misconfigured or a lookup fails."""


_VALID_TRANSPORTS = ("mcp", "a2a")
_VALID_AUTH_TYPES = ("static_bearer", "cognito_bearer", "m2m_oauth")


def validate_seller_entry(entry: dict[str, Any]) -> SellerAgentConfig:
    """Validate one seller registration and normalise it into a SellerAgentConfig.

    The single validator shared by the environment parser and the admin registration path, so a
    stored entry passes exactly the checks an env entry does (design C-3). Raises SellerAgentError
    with a human-readable message on any problem.

    A stored (admin-created) static_bearer entry carries its token inline in `auth_token`; an
    environment entry names the env var in `auth_token_env`. Either satisfies static_bearer.
    """
    if not isinstance(entry, dict):
        raise SellerAgentError("A seller agent entry must be an object.")
    for key in ("id", "name", "url", "auth_type"):
        if not str(entry.get(key, "")).strip():
            raise SellerAgentError(f"Seller agent entry is missing required key {key!r}.")
    auth_type = entry["auth_type"]
    if auth_type not in _VALID_AUTH_TYPES:
        raise SellerAgentError(
            f"Seller agent {entry['id']!r} has auth_type {auth_type!r}; expected one of {_VALID_AUTH_TYPES}."
        )
    if auth_type == "static_bearer" and not (entry.get("auth_token_env") or entry.get("auth_token")):
        raise SellerAgentError(
            f"Seller agent {entry['id']!r} uses static_bearer but supplies neither a token nor an "
            "auth_token_env naming one."
        )
    if auth_type == "m2m_oauth" and not (
        str(entry.get("client_id", "")).strip()
        and str(entry.get("token_url", "")).strip()
        and str(entry.get("client_secret", "")).strip()
    ):
        raise SellerAgentError(
            f"Seller agent {entry['id']!r} uses m2m_oauth but is missing one of client_id, token_url "
            "or client_secret (scope and audience are optional)."
        )
    transport = entry.get("transport", "mcp")
    if transport not in _VALID_TRANSPORTS:
        raise SellerAgentError(
            f"Seller agent {entry['id']!r} has transport {transport!r}; expected one of {_VALID_TRANSPORTS}."
        )
    return SellerAgentConfig(
        id=str(entry["id"]),
        name=str(entry["name"]),
        url=str(entry["url"]),
        agent_url=str(entry.get("agent_url") or entry["url"]),
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


def _load_env_registry() -> list[SellerAgentConfig]:
    """Seller entries from SELLER_AGENTS_JSON.

    Unchanged in meaning from before runtime registration existed: same required-key and array
    checks, same duplicate-id rule, same fallback of `agent_url` to `url`. Per-entry field validation
    is delegated to `validate_seller_entry` so an env entry and a stored entry pass identical checks.

    `agent_url` note (unchanged): a seller's canonical AdCP identity -- the value its OWN code compares
    an incoming governance_context's `aud` claim against -- is deliberately NOT the per-deployment
    credential-bearing invoke `url` for AgentCore-hosted sellers. It falls back to `url` for sellers
    (a third-party seller, typically) whose invoke URL IS their canonical identity.
    """
    raw = os.environ.get("SELLER_AGENTS_JSON", "").strip()
    if not raw:
        raise SellerAgentError(
            "SELLER_AGENTS_JSON is not set. Copy .env.example to .env and fill it in "
            "with at least one seller agent."
        )
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SellerAgentError(f"SELLER_AGENTS_JSON is not valid JSON: {exc}") from exc

    if not isinstance(parsed, list) or not parsed:
        raise SellerAgentError("SELLER_AGENTS_JSON must be a non-empty JSON array.")

    seen_ids: set[str] = set()
    entries: list[SellerAgentConfig] = []
    for i, entry in enumerate(parsed):
        if not isinstance(entry, dict):
            raise SellerAgentError(f"SELLER_AGENTS_JSON[{i}] must be an object.")
        if entry.get("id") in seen_ids:
            raise SellerAgentError(f"Duplicate seller agent id '{entry.get('id')}' in SELLER_AGENTS_JSON.")
        try:
            config = validate_seller_entry({**entry, "source": "env"})
        except SellerAgentError as exc:
            raise SellerAgentError(f"SELLER_AGENTS_JSON[{i}]: {exc}") from exc
        seen_ids.add(config["id"])
        entries.append(config)
    return entries


def _load_stored_entries() -> list[SellerAgentConfig]:
    """Runtime-registered seller entries from the registration store.

    Resilient by contract: any store problem (unconfigured table, DynamoDB error, a malformed stored
    entry) is swallowed and an empty list is returned, so the buyer keeps working against its
    environment-configured sellers. The AdCP call path must never break because this overlay is
    unavailable (design FR-8 / NFR-1). Import is local so a checkout without boto3 configured can
    still import this module.
    """
    try:
        import agent_registry_store

        out: list[SellerAgentConfig] = []
        for raw in agent_registry_store.list_with_secrets(agent_registry_store.KIND_SELLER):
            try:
                out.append(validate_seller_entry({**raw, "source": "stored"}))
            except SellerAgentError:
                # Skip a single bad stored entry rather than failing the whole registry.
                continue
        return out
    except Exception:  # noqa: BLE001 - overlay must never break the env-configured path
        return []


def _load_hidden_ids() -> set[str]:
    """Ids an admin has removed from the effective registry (env built-ins can't be deleted, only
    hidden). Resilient: any store problem yields an empty set, so removal degrades to "not removed"
    rather than breaking the AdCP path — same contract as `_load_stored_entries`."""
    try:
        import agent_registry_store

        return agent_registry_store.hidden_ids(agent_registry_store.KIND_SELLER)
    except Exception:  # noqa: BLE001 - the hide overlay must never break the env-configured path
        return set()


def _load_registry(include_hidden: bool = False) -> list[SellerAgentConfig]:
    """The effective seller registry: environment entries, then stored entries merged on top, minus
    any the admin has hidden.

    A stored entry whose id matches an env entry overrides it (so a built-in can be edited from the
    admin UI); a stored entry with a new id is an addition. A hidden id is dropped entirely, so the
    buyer stops fanning out to and resolving it — this is what makes "Remove" in the admin UI change
    the configuration the buyer actually uses, not just the display. `include_hidden=True` is for the
    admin view, which must still show a removed built-in so it can be restored.
    """
    entries = _load_env_registry()
    by_id = {e["id"]: e for e in entries}
    for stored in _load_stored_entries():
        by_id[stored["id"]] = stored
    if not include_hidden:
        for hidden_id in _load_hidden_ids():
            by_id.pop(hidden_id, None)
    return list(by_id.values())


def list_seller_agents() -> list[SellerAgentConfig]:
    """Full registry, including auth_token_env names (not secret values)."""
    return _load_registry()


def get_default_seller_agent_id() -> str:
    """The seller agent id used when a request doesn't specify one.

    Defaults to the first entry in SELLER_AGENTS_JSON if
    DEFAULT_SELLER_AGENT_ID is unset, so a single-entry registry works with
    no extra configuration.
    """
    configured = os.environ.get("DEFAULT_SELLER_AGENT_ID", "").strip()
    if configured:
        return configured
    registry = _load_registry()
    if not registry:
        raise SellerAgentError(
            "No seller agent is available: every configured seller has been removed. Restore a "
            "built-in or add a seller from the admin screen."
        )
    return registry[0]["id"]


def list_seller_agents_public(include_hidden: bool = False) -> list[dict[str, str]]:
    """The subset of registry data safe to expose to the browser: id, name,
    url, transport. Never includes auth_token_env or any secret value —
    the browser only needs to know which sellers exist and label them; the
    actual server-side agent process resolves auth per call.

    `include_hidden=True` returns removed built-ins too, for the admin view that offers to restore
    them; the default excludes them, matching the effective registry the buyer uses.
    """
    return [
        {
            "id": entry["id"],
            "name": entry["name"],
            "url": entry["url"],
            "transport": entry["transport"],
            # Additive field: lets the UI distinguish a built-in (env) seller from a custom
            # (runtime-registered) one. Never includes any token.
            "source": entry.get("source", "env"),
        }
        for entry in _load_registry(include_hidden=include_hidden)
    ]


def resolve_seller_agent(seller_agent_id: str | None) -> dict[str, Any]:
    """Resolve a seller agent id into its URL, transport, and ready-to-use auth headers.

    Returns {"id", "name", "url", "transport", "headers"}. Raises
    SellerAgentError if the id doesn't exist in the registry, or if a
    static_bearer entry's token env var is unset, or if a cognito_bearer
    entry can't mint a token.
    """
    registry = _load_registry()
    target_id = seller_agent_id or get_default_seller_agent_id()

    for entry in registry:
        if entry["id"] == target_id:
            headers = _build_auth_headers(entry)
            return {
                "id": entry["id"],
                "name": entry["name"],
                "url": entry["url"],
                "agent_url": entry["agent_url"],
                "transport": entry["transport"],
                "headers": headers,
            }

    valid_ids = ", ".join(e["id"] for e in registry)
    raise SellerAgentError(f"Unknown seller_agent_id '{target_id}'. Valid ids: {valid_ids}")


def _build_auth_headers(entry: SellerAgentConfig) -> dict[str, str]:
    if entry["auth_type"] == "static_bearer":
        # A runtime-registered entry carries its token inline; an environment entry names the env var
        # that holds it. Either produces the identical Authorization header.
        inline = entry.get("auth_token")
        if inline:
            return {"Authorization": f"Bearer {inline}"}
        env_var = entry.get("auth_token_env")
        if not env_var:
            raise SellerAgentError(
                f"Seller agent '{entry['id']}' uses static_bearer but has neither a stored token "
                "nor an auth_token_env."
            )
        token = os.environ.get(env_var, "")
        if not token:
            raise SellerAgentError(
                f"Seller agent '{entry['id']}' needs {env_var} set in .env but it's empty."
            )
        return {"Authorization": f"Bearer {token}"}

    if entry["auth_type"] == "m2m_oauth":
        # OAuth2 client_credentials: mint (or reuse a cached) access token from this registration's
        # own client_id / client_secret / token_url. The secret is stored at rest by the registry
        # (like a static bearer token); the access token is ephemeral and cached in-process.
        client_id = entry.get("client_id")
        client_secret = entry.get("client_secret")
        token_url = entry.get("token_url")
        if not (client_id and client_secret and token_url):
            raise SellerAgentError(
                f"Seller agent '{entry['id']}' uses m2m_oauth but is missing client_id, "
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

    # cognito_bearer: mint a fresh access token for this project's own
    # Cognito test user, the same one the buyer agent's own login uses.
    # This is what lets the reference seller agent (deployed to this same
    # AWS account's AgentCore Runtime, gated by the same Cognito pool)
    # be called without a second, separately managed credential.
    token = get_test_user_access_token()
    return {"Authorization": f"Bearer {token}"}
