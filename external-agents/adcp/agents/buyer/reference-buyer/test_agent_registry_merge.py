"""Tests for merging runtime registrations over the environment-configured registries.

The behaviours that matter for "don't break AdCP compliance": stored entries are additive, a stored
entry overrides an env entry of the same id, a store outage falls back to env-only (never an error on
the AdCP path), and an inline stored token produces the identical Authorization header an env
`auth_token_env` entry would.
"""

from __future__ import annotations

import json

import pytest

import agent_registry_store
import governance_agents
import seller_agents

_ENV_SELLERS = [
    {
        "id": "reference",
        "name": "Reference Seller",
        "url": "https://ref.example/adcp/mcp",
        "transport": "mcp",
        "auth_type": "cognito_bearer",
    }
]


@pytest.fixture
def env_sellers(monkeypatch):
    monkeypatch.setenv("SELLER_AGENTS_JSON", json.dumps(_ENV_SELLERS))


def _stored(entries, monkeypatch, module):
    monkeypatch.setattr(module, "_load_stored_entries", lambda: [
        module.validate_seller_entry({**e, "source": "stored"})
        if module is seller_agents
        else module.validate_governance_entry({**e, "source": "stored"})
        for e in entries
    ])


def test_stored_seller_is_added_to_env_sellers(env_sellers, monkeypatch):
    _stored(
        [{"id": "acme", "name": "Acme", "url": "https://acme.example/adcp/mcp",
          "auth_type": "static_bearer", "auth_token": "t"}],
        monkeypatch,
        seller_agents,
    )
    ids = [e["id"] for e in seller_agents.list_seller_agents_public()]
    assert set(ids) == {"reference", "acme"}


def test_stored_seller_overrides_env_seller_of_same_id(env_sellers, monkeypatch):
    _stored(
        [{"id": "reference", "name": "Overridden", "url": "https://new.example/adcp/mcp",
          "auth_type": "cognito_bearer"}],
        monkeypatch,
        seller_agents,
    )
    resolved = seller_agents.resolve_seller_agent("reference")
    assert resolved["url"] == "https://new.example/adcp/mcp"
    assert resolved["name"] == "Overridden"


def test_store_failure_falls_back_to_env_only(env_sellers, monkeypatch):
    # Simulate the store being unreachable: the real _load_stored_entries swallows and returns [].
    def boom():
        raise RuntimeError("dynamo down")

    monkeypatch.setattr(agent_registry_store, "list_with_secrets", lambda kind: boom())
    ids = [e["id"] for e in seller_agents.list_seller_agents_public()]
    assert ids == ["reference"], "a store outage must leave the env-configured sellers working"


def test_inline_stored_token_becomes_a_bearer_header(env_sellers, monkeypatch):
    _stored(
        [{"id": "acme", "name": "Acme", "url": "https://acme.example/adcp/mcp",
          "auth_type": "static_bearer", "auth_token": "inline-secret"}],
        monkeypatch,
        seller_agents,
    )
    resolved = seller_agents.resolve_seller_agent("acme")
    assert resolved["headers"]["Authorization"] == "Bearer inline-secret"


def test_stored_m2m_seller_mints_and_becomes_a_bearer_header(env_sellers, monkeypatch):
    # A stored m2m_oauth entry resolves by minting a client_credentials token (mint helper stubbed so
    # there is no network hop), and the minted token becomes the Authorization header.
    calls = {}

    def fake_mint(token_url, client_id, client_secret, scope=None, audience=None):
        calls.update(
            token_url=token_url, client_id=client_id, client_secret=client_secret, scope=scope, audience=audience
        )
        return "minted-access-token"

    monkeypatch.setattr(seller_agents, "get_m2m_access_token", fake_mint)
    _stored(
        [{"id": "acme", "name": "Acme", "url": "https://acme.example/adcp/mcp",
          "auth_type": "m2m_oauth", "client_id": "cid", "client_secret": "csec",
          "token_url": "https://auth.acme.example/oauth2/token", "scope": "adcp/read"}],
        monkeypatch,
        seller_agents,
    )
    resolved = seller_agents.resolve_seller_agent("acme")
    assert resolved["headers"]["Authorization"] == "Bearer minted-access-token"
    assert calls == {
        "token_url": "https://auth.acme.example/oauth2/token",
        "client_id": "cid",
        "client_secret": "csec",
        "scope": "adcp/read",
        "audience": None,
    }


def test_m2m_missing_client_secret_is_rejected_by_validation(monkeypatch):
    with pytest.raises(seller_agents.SellerAgentError):
        seller_agents.validate_seller_entry(
            {"id": "bad", "name": "Bad", "url": "https://x.example/adcp/mcp", "auth_type": "m2m_oauth",
             "client_id": "cid", "token_url": "https://auth.example/token", "source": "stored"}
        )


def test_stored_m2m_governance_mints_a_bearer_header(monkeypatch):
    monkeypatch.delenv("GOVERNANCE_AGENTS_JSON", raising=False)
    monkeypatch.setattr(
        governance_agents, "get_m2m_access_token",
        lambda token_url, client_id, client_secret, scope=None, audience=None: "gov-minted-token",
    )
    _stored(
        [{"id": "gov", "name": "Gov", "url": "https://gov.example/adcp/mcp",
          "auth_type": "m2m_oauth", "client_id": "gid", "client_secret": "gsec",
          "token_url": "https://auth.gov.example/oauth2/token"}],
        monkeypatch,
        governance_agents,
    )
    resolved = governance_agents.resolve_governance_agent()
    assert resolved["headers"]["Authorization"] == "Bearer gov-minted-token"


def test_public_seller_list_carries_source_and_no_token(env_sellers, monkeypatch):
    _stored(
        [{"id": "acme", "name": "Acme", "url": "https://acme.example/adcp/mcp",
          "auth_type": "static_bearer", "auth_token": "inline-secret"}],
        monkeypatch,
        seller_agents,
    )
    public = seller_agents.list_seller_agents_public()
    by_id = {e["id"]: e for e in public}
    assert by_id["reference"]["source"] == "env"
    assert by_id["acme"]["source"] == "stored"
    for entry in public:
        assert "auth_token" not in entry and "auth_token_env" not in entry


def test_governance_configured_true_from_store_when_env_empty(monkeypatch):
    monkeypatch.delenv("GOVERNANCE_AGENTS_JSON", raising=False)
    _stored(
        [{"id": "gov", "name": "Gov", "url": "https://gov.example/adcp/mcp",
          "auth_type": "static_bearer", "auth_token": "t"}],
        monkeypatch,
        governance_agents,
    )
    assert governance_agents.governance_configured() is True
    resolved = governance_agents.resolve_governance_agent()
    assert resolved["url"] == "https://gov.example/adcp/mcp"
    assert resolved["headers"]["Authorization"] == "Bearer t"


def test_governance_not_configured_when_env_empty_and_store_empty(monkeypatch):
    monkeypatch.delenv("GOVERNANCE_AGENTS_JSON", raising=False)
    monkeypatch.setattr(governance_agents, "_load_stored_entries", lambda: [])
    assert governance_agents.governance_configured() is False
    with pytest.raises(governance_agents.GovernanceAgentError):
        governance_agents.resolve_governance_agent()


def _hidden(ids, monkeypatch, module):
    monkeypatch.setattr(module, "_load_hidden_ids", lambda: set(ids))


def test_hidden_env_seller_drops_from_effective_registry_but_shows_in_admin_view(env_sellers, monkeypatch):
    _stored([], monkeypatch, seller_agents)
    _hidden({"reference"}, monkeypatch, seller_agents)

    # Effective registry (fan-out, chat dropdown, resolve) no longer has it.
    assert [e["id"] for e in seller_agents.list_seller_agents_public()] == []
    with pytest.raises(seller_agents.SellerAgentError):
        seller_agents.resolve_seller_agent("reference")

    # Admin view keeps it (include_hidden) so it can be restored.
    assert [e["id"] for e in seller_agents.list_seller_agents_public(include_hidden=True)] == ["reference"]


def test_hidden_governance_makes_it_not_configured(monkeypatch):
    monkeypatch.setenv(
        "GOVERNANCE_AGENTS_JSON",
        json.dumps([
            {"id": "gov", "name": "Gov", "url": "https://gov.example/adcp/mcp",
             "auth_type": "static_bearer", "auth_token": "t"}
        ]),
    )
    monkeypatch.setattr(governance_agents, "_load_stored_entries", lambda: [])
    _hidden({"gov"}, monkeypatch, governance_agents)

    # Removing the only governance agent means the buyer stops governing (BR-U2-11), reversibly.
    assert governance_agents.governance_configured() is False
    with pytest.raises(governance_agents.GovernanceAgentError):
        governance_agents.resolve_governance_agent()
    # But the admin view still shows it for restore.
    assert [e["id"] for e in governance_agents.list_governance_agents_public(include_hidden=True)] == ["gov"]
