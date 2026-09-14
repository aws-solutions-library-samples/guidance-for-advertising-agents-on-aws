"""
Registry of the A2A agents the chat UI can talk to.

Per the multi-agent-chat plan (Q1), every A2A agent is a selectable chat
target: the ones this project deploys, and third-party ones registered here.
Modelled on seller_agents.py — same "config in .env, never hardcoded in the
UI" shape — so adding a further A2A seller is an AGENTS_JSON entry, not a UI
change.

    AGENTS_JSON=[
      {
        "id": "buyer-a2a",
        "name": "AdCP Buyer Agent (A2A)",
        "kind": "buyer",
        "url": "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/.../invocations",
        "auth_type": "cognito_bearer",
        "origin": "internal"
      },
      {
        "id": "external-seller-a2a",
        "name": "Third-Party Seller Agent (A2A)",
        "kind": "seller",
        "url": "https://a2a.example.com/a2a",
        "auth_type": "static_bearer",
        "auth_token_env": "EXTERNAL_SELLER_AUTH_TOKEN",
        "origin": "external"
      }
    ]

Fields
------
id          stable key; also what session records store as agent_id.
name        label shown in the UI.
kind        "buyer" or "seller". A buyer agent talks to a seller on the
            user's behalf, so the UI keeps its seller dropdown active for
            one (Q1: "if they are a buyer agent, you should still be able to
            choose a seller agent"). A seller agent is talked to directly,
            and the seller dropdown is meaningless for it.
url         the A2A endpoint. Every agent here speaks A2A — this registry is
            deliberately not a general agent list; MCP sellers are reached
            *through* a buyer agent, not chatted to (Q1).
auth_type   "cognito_bearer" (this project's own pool — the signed-in user's
            own token is used) or "static_bearer" (a long-lived token read
            from auth_token_env, never inlined here).
origin      "internal" = deployed by this project, so it records reasoning
            sessions we can show. "external" = someone else's deployment;
            we can see our own side of the call only (Q3).

Two derived properties the UI depends on
---------------------------------------
`records_sessions`: true only for internal agents. External agents' internals
are not ours to observe, so the UI says tool activity is unavailable rather
than showing an empty panel that implies no tools were used.

`requires_proxy`: true when the browser must NOT call the endpoint itself.
A cognito_bearer agent is called straight from the browser with the user's
own access token (AWS's invoke endpoint sends permissive CORS headers, which
is how the UI already works). A static_bearer agent's token is a secret that
must never reach a browser, so those calls are relayed through this project's
own authenticated runtime instead (app.py's `a2a_chat` action), which also
sidesteps the external endpoint's CORS policy.
"""

import json
import os
from typing import Any, Literal

from dotenv import load_dotenv

from auth import get_test_user_access_token

load_dotenv()

# Canonical ids for the agents this project deploys. Session records are
# stamped with these (see app.py / a2a_entrypoint.py), and the UI's
# per-agent session filter matches on them, so the registry entry for an
# internal agent MUST use the same id as the runtime that writes it.
BUYER_HTTP_AGENT_ID = "buyer-http"
BUYER_HTTP_AGENT_NAME = "AdCP Buyer Agent (HTTP)"
BUYER_A2A_AGENT_ID = "buyer-a2a"
BUYER_A2A_AGENT_NAME = "AdCP Buyer Agent (A2A)"

AgentKind = Literal["buyer", "seller"]
AgentOrigin = Literal["internal", "external"]

_REQUIRED_FIELDS = ("id", "name", "kind", "url", "auth_type", "origin")
_VALID_KINDS = ("buyer", "seller")
_VALID_ORIGINS = ("internal", "external")
_VALID_AUTH_TYPES = ("cognito_bearer", "static_bearer")


class AgentRegistryError(RuntimeError):
    """Raised when AGENTS_JSON is missing, malformed, or names an unknown agent."""


def _load_registry() -> list[dict[str, Any]]:
    """Parse and validate AGENTS_JSON. Fails loudly rather than silently
    dropping a malformed entry — a chat target that quietly vanishes from the
    dropdown is harder to diagnose than a startup error naming the problem.
    """
    raw = os.environ.get("AGENTS_JSON", "").strip()
    if not raw:
        raise AgentRegistryError(
            "AGENTS_JSON is not set. Add the chat-selectable A2A agents to .env "
            "(see agents_registry.py's docstring for the shape)."
        )
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AgentRegistryError(f"AGENTS_JSON is not valid JSON: {exc}") from exc

    if not isinstance(entries, list) or not entries:
        raise AgentRegistryError("AGENTS_JSON must be a non-empty JSON array.")

    seen_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise AgentRegistryError(f"AGENTS_JSON entries must be objects, got {entry!r}.")
        missing = [f for f in _REQUIRED_FIELDS if not entry.get(f)]
        if missing:
            raise AgentRegistryError(
                f"AGENTS_JSON entry {entry.get('id', '<no id>')!r} is missing: {', '.join(missing)}."
            )
        if entry["kind"] not in _VALID_KINDS:
            raise AgentRegistryError(
                f"AGENTS_JSON entry {entry['id']!r} has kind {entry['kind']!r}; "
                f"expected one of {_VALID_KINDS}."
            )
        if entry["origin"] not in _VALID_ORIGINS:
            raise AgentRegistryError(
                f"AGENTS_JSON entry {entry['id']!r} has origin {entry['origin']!r}; "
                f"expected one of {_VALID_ORIGINS}."
            )
        if entry["auth_type"] not in _VALID_AUTH_TYPES:
            raise AgentRegistryError(
                f"AGENTS_JSON entry {entry['id']!r} has auth_type {entry['auth_type']!r}; "
                f"expected one of {_VALID_AUTH_TYPES}."
            )
        if entry["auth_type"] == "static_bearer" and not entry.get("auth_token_env"):
            raise AgentRegistryError(
                f"AGENTS_JSON entry {entry['id']!r} uses static_bearer but names no auth_token_env."
            )
        if entry["id"] in seen_ids:
            raise AgentRegistryError(f"AGENTS_JSON has duplicate id {entry['id']!r}.")
        seen_ids.add(entry["id"])

    return entries


def records_sessions(entry: dict[str, Any]) -> bool:
    """Whether this project can show the agent's reasoning steps.

    Only agents we deploy write to our sessions table, so only they have a
    progression to show (Q3). This is a fact about who runs the agent, not a
    setting — hence derived, not configurable.
    """
    return entry["origin"] == "internal"


def requires_proxy(entry: dict[str, Any]) -> bool:
    """Whether the browser must route this agent's calls through our runtime.

    True for static_bearer agents, whose token must never be sent to a
    browser. See this module's docstring.
    """
    return entry["auth_type"] == "static_bearer"


def list_agents_public() -> list[dict[str, Any]]:
    """The subset of registry data safe to expose to the browser.

    Includes the endpoint URL (an address, not a credential) for agents the
    browser calls itself, and omits it for proxied ones since the browser
    never uses it. Never includes auth_token_env or any token value.
    """
    public = []
    for entry in _load_registry():
        proxied = requires_proxy(entry)
        public.append(
            {
                "id": entry["id"],
                "name": entry["name"],
                "kind": entry["kind"],
                "origin": entry["origin"],
                "transport": "a2a",
                "records_sessions": records_sessions(entry),
                "requires_proxy": proxied,
                "url": "" if proxied else entry["url"],
            }
        )
    return public


def get_default_agent_id() -> str:
    """DEFAULT_AGENT_ID if set and valid, else the first registered agent."""
    entries = _load_registry()
    configured = os.environ.get("DEFAULT_AGENT_ID", "").strip()
    if configured:
        if not any(e["id"] == configured for e in entries):
            raise AgentRegistryError(
                f"DEFAULT_AGENT_ID is {configured!r}, which is not in AGENTS_JSON."
            )
        return configured
    return entries[0]["id"]


def this_buyer_agent_url() -> str | None:
    """This buyer's own canonical agent URL, or None when the registry declares no internal buyer.

    AdCP's `check_governance` needs it for `caller`, which the spec defines as "the buyer-side
    orchestrator making the governance check" (and the SDK as "URL of the agent making the request").
    The seller a check is *about* goes in `target_agent`, a separate field.

    Read from the registry rather than composed from an ARN, because `AGENTS_JSON` is already in the
    deployed runtime's environment and already holds this agent's real, addressable URL --
    `AGENT_RUNTIME_ARN` is not passed to the container at all (see runtime_env.build_env_vars).

    Returns None rather than substituting something else. A caller that cannot name itself should say
    so; inventing an identity here would put an unresolvable URL into a signed audit trail.
    """
    for entry in _load_registry():
        if entry["kind"] == "buyer" and entry["origin"] == "internal":
            return str(entry["url"])
    return None


def resolve_agent(agent_id: str | None) -> dict[str, Any]:
    """Resolve an agent id into its endpoint and ready-to-use auth headers.

    Server-side only — the returned headers contain a real credential.
    """
    entries = _load_registry()
    target_id = agent_id or get_default_agent_id()
    for entry in entries:
        if entry["id"] == target_id:
            return {
                "id": entry["id"],
                "name": entry["name"],
                "kind": entry["kind"],
                "origin": entry["origin"],
                "url": entry["url"],
                "records_sessions": records_sessions(entry),
                "requires_proxy": requires_proxy(entry),
                "headers": _build_auth_headers(entry),
            }
    valid = ", ".join(e["id"] for e in entries)
    raise AgentRegistryError(f"Unknown agent_id {target_id!r}. Valid ids: {valid}")


def _build_auth_headers(entry: dict[str, Any]) -> dict[str, str]:
    if entry["auth_type"] == "static_bearer":
        env_var = entry["auth_token_env"]
        token = os.environ.get(env_var, "")
        if not token:
            raise AgentRegistryError(
                f"Agent {entry['id']!r} needs {env_var} set but it's empty."
            )
        return {"Authorization": f"Bearer {token}"}

    # cognito_bearer: mint a token for this project's own Cognito user, the
    # same mechanism seller_agents.py uses for our own deployed runtimes.
    return {"Authorization": f"Bearer {get_test_user_access_token()}"}
