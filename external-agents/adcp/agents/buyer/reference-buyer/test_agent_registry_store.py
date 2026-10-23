"""Tests for the runtime agent-registration store.

The security-critical property here is that a token value never leaves the server: `redact()` is the
one chokepoint every browser-visible path runs through, so it gets the most attention. The rest
verifies durable storage (no TTL attribute), token round-trip on the server side, and that switching an
entry away from static_bearer does not leave a stale secret behind.

DynamoDB is faked in-process (no moto dependency, matching this package's existing test style) by
substituting `session_records.table`, which the store resolves through.
"""

from __future__ import annotations

import pytest

import agent_registry_store
import session_records


class _ResourceNotFoundException(Exception):
    pass


class _Client:
    class exceptions:  # noqa: N801 - mirrors boto3's attribute shape
        ResourceNotFoundException = _ResourceNotFoundException


class _Meta:
    client = _Client()


class FakeTable:
    """Minimal in-memory stand-in for a boto3 DynamoDB Table, keyed by (pk, sk)."""

    meta = _Meta()

    def __init__(self) -> None:
        self.items: dict[tuple, dict] = {}

    def put_item(self, Item):  # noqa: N803 - boto3 kwarg name
        self.items[(Item["pk"], Item["sk"])] = dict(Item)

    def get_item(self, Key):  # noqa: N803
        item = self.items.get((Key["pk"], Key["sk"]))
        return {"Item": dict(item)} if item else {}

    def delete_item(self, Key):  # noqa: N803
        self.items.pop((Key["pk"], Key["sk"]), None)
        return {}

    def query(self, KeyConditionExpression=None, **_kw):  # noqa: N803
        # The store only ever issues Key("pk").eq(<value>); pull that value out of the condition.
        pk_value = KeyConditionExpression.get_expression()["values"][1]
        return {"Items": [dict(v) for k, v in self.items.items() if k[0] == pk_value]}


@pytest.fixture
def fake_table(monkeypatch):
    table = FakeTable()
    monkeypatch.setattr(session_records, "table", lambda: table)
    return table


SELLER = agent_registry_store.KIND_SELLER
GOV = agent_registry_store.KIND_GOVERNANCE


def _static_seller(**over):
    entry = {
        "id": "acme",
        "name": "Acme Sales Agent",
        "url": "https://acme.example/adcp/mcp",
        "transport": "mcp",
        "auth_type": "static_bearer",
        "auth_token": "secret-token-value",
    }
    entry.update(over)
    return entry


def test_redact_never_exposes_the_token_and_flags_its_presence():
    redacted = agent_registry_store.redact(_static_seller())
    assert "auth_token" not in redacted
    assert redacted["has_stored_token"] is True
    assert redacted["source"] == "stored"
    assert redacted["id"] == "acme"


def test_redact_reports_absent_token_as_false():
    redacted = agent_registry_store.redact(
        {"id": "in-acct", "name": "In-account", "url": "u", "auth_type": "cognito_bearer"}
    )
    assert redacted["has_stored_token"] is False


def test_put_then_get_round_trips_the_token_server_side(fake_table):
    agent_registry_store.put(SELLER, _static_seller(), updated_by="alice")
    got = agent_registry_store.get(SELLER, "acme")
    assert got["auth_token"] == "secret-token-value"
    assert got["updated_by"] == "alice"


def test_public_list_redacts_but_with_secrets_does_not(fake_table):
    agent_registry_store.put(SELLER, _static_seller())
    public = agent_registry_store.list_public(SELLER)
    assert public and "auth_token" not in public[0] and public[0]["has_stored_token"] is True
    with_secrets = agent_registry_store.list_with_secrets(SELLER)
    assert with_secrets[0]["auth_token"] == "secret-token-value"


def test_stored_items_are_durable_no_ttl_attribute(fake_table):
    agent_registry_store.put(SELLER, _static_seller())
    stored_item = fake_table.items[("AGENTREG#SELLER", "acme")]
    assert "ttl" not in stored_item, "registrations must not carry a TTL or DynamoDB would reclaim them"


def test_switching_to_cognito_drops_a_previously_stored_token(fake_table):
    agent_registry_store.put(SELLER, _static_seller())
    agent_registry_store.put(
        SELLER,
        {"id": "acme", "name": "Acme", "url": "https://acme.example/adcp/mcp", "auth_type": "cognito_bearer"},
    )
    got = agent_registry_store.get(SELLER, "acme")
    assert "auth_token" not in got, "stale secret must not survive a switch away from static_bearer"


def _m2m_seller(**over):
    entry = {
        "id": "acme-m2m",
        "name": "Acme M2M Sales Agent",
        "url": "https://acme.example/adcp/mcp",
        "transport": "mcp",
        "auth_type": "m2m_oauth",
        "client_id": "acme-client-123",
        "client_secret": "shhh-client-secret",
        "token_url": "https://auth.acme.example/oauth2/token",
        "scope": "adcp/read",
    }
    entry.update(over)
    return entry


def test_redact_never_exposes_the_client_secret_and_flags_its_presence():
    redacted = agent_registry_store.redact(_m2m_seller())
    assert "client_secret" not in redacted
    assert redacted["has_stored_secret"] is True
    # Non-secret m2m fields ARE surfaced (the UI prefills them on edit).
    assert redacted["client_id"] == "acme-client-123"
    assert redacted["token_url"] == "https://auth.acme.example/oauth2/token"
    assert redacted["scope"] == "adcp/read"


def test_redact_reports_absent_client_secret_as_false():
    redacted = agent_registry_store.redact(_static_seller())
    assert redacted["has_stored_secret"] is False


def test_m2m_put_then_get_round_trips_the_client_secret_server_side(fake_table):
    agent_registry_store.put(SELLER, _m2m_seller())
    got = agent_registry_store.get(SELLER, "acme-m2m")
    assert got["client_secret"] == "shhh-client-secret"
    assert got["client_id"] == "acme-client-123"


def test_m2m_public_list_redacts_the_secret(fake_table):
    agent_registry_store.put(SELLER, _m2m_seller())
    public = agent_registry_store.list_public(SELLER)
    assert public and "client_secret" not in public[0] and public[0]["has_stored_secret"] is True


def test_switching_away_from_m2m_drops_a_previously_stored_client_secret(fake_table):
    agent_registry_store.put(SELLER, _m2m_seller())
    agent_registry_store.put(
        SELLER,
        {"id": "acme-m2m", "name": "Acme", "url": "https://acme.example/adcp/mcp", "auth_type": "cognito_bearer"},
    )
    got = agent_registry_store.get(SELLER, "acme-m2m")
    assert "client_secret" not in got, "stale secret must not survive a switch away from m2m_oauth"


def test_switching_static_to_m2m_drops_the_old_bearer_token(fake_table):
    agent_registry_store.put(SELLER, _static_seller(id="switcher"))
    agent_registry_store.put(SELLER, _m2m_seller(id="switcher"))
    got = agent_registry_store.get(SELLER, "switcher")
    assert "auth_token" not in got, "the old static bearer token must not survive a switch to m2m_oauth"
    assert got["client_secret"] == "shhh-client-secret"


def test_delete_reports_existence_and_removes(fake_table):
    agent_registry_store.put(SELLER, _static_seller())
    assert agent_registry_store.delete(SELLER, "acme") is True
    assert agent_registry_store.get(SELLER, "acme") is None
    assert agent_registry_store.delete(SELLER, "acme") is False


def test_kinds_are_isolated_by_partition(fake_table):
    agent_registry_store.put(SELLER, _static_seller(id="s1"))
    agent_registry_store.put(GOV, _static_seller(id="g1"))
    seller_ids = [e["id"] for e in agent_registry_store.list_with_secrets(SELLER)]
    gov_ids = [e["id"] for e in agent_registry_store.list_with_secrets(GOV)]
    assert seller_ids == ["s1"] and gov_ids == ["g1"]


def test_unknown_kind_is_rejected():
    with pytest.raises(agent_registry_store.AgentRegistryStoreError):
        agent_registry_store.list_with_secrets("nonsense")


def test_hide_then_hidden_ids_lists_it_then_unhide_clears_it(fake_table):
    # An env built-in cannot be deleted, only hidden. The tombstone is durable and reversible.
    assert agent_registry_store.hidden_ids(SELLER) == set()
    agent_registry_store.hide(SELLER, "reference", updated_by="alice")
    assert agent_registry_store.hidden_ids(SELLER) == {"reference"}
    # Idempotent: hiding again does not duplicate.
    agent_registry_store.hide(SELLER, "reference")
    assert agent_registry_store.hidden_ids(SELLER) == {"reference"}
    assert agent_registry_store.unhide(SELLER, "reference") is True
    assert agent_registry_store.hidden_ids(SELLER) == set()
    # Unhiding something not hidden reports it did not exist.
    assert agent_registry_store.unhide(SELLER, "reference") is False


def test_hide_tombstone_carries_no_ttl_and_lives_in_its_own_partition(fake_table):
    agent_registry_store.hide(SELLER, "reference")
    item = fake_table.items[("AGENTREGHIDE#SELLER", "reference")]
    assert "ttl" not in item, "a tombstone must be durable or the removal would silently expire"
    # A hide must not appear as a registration in list_with_secrets (separate partition).
    assert agent_registry_store.list_with_secrets(SELLER) == []


def test_hide_is_scoped_per_kind(fake_table):
    agent_registry_store.hide(SELLER, "shared-id")
    assert agent_registry_store.hidden_ids(SELLER) == {"shared-id"}
    assert agent_registry_store.hidden_ids(GOV) == set()
