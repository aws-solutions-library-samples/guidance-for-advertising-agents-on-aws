"""`sync_governance`: registering the governance agent a buyer wants this seller to consult.

The Bind phase of the buyer's journey. Same moto-mocked-DynamoDB pattern as `test_main.py` -- no live
AWS calls.

The request shape is validated against the SDK's own model rather than a hand-written dict, so a protocol
change breaks these tests instead of being discovered in a deployed agent. The model is
`adcp.types.generated_poc.account.sync_governance_request.SyncGovernanceRequest` from the installed
`adcp==6.6.0`:

    SyncGovernanceRequest  required: idempotency_key, accounts (1..100)
    Account               required: account, governance_agents   (minItems 1, maxItems 1)
    GovernanceAgent       required: url (HTTPS), authentication
    Authentication        required: schemes, credentials
"""

from __future__ import annotations

from aws_region import region

import asyncio

import boto3
import pytest
from adcp.server.base import ToolContext
from adcp.types.generated_poc.account.sync_governance_request import SyncGovernanceRequest
from moto import mock_aws

import main

REGION = region()
TABLE_NAME = "adcp-reference-seller-state"

BRAND = {"domain": "acme.example"}
OPERATOR = "acme-media"
GOV_URL = "https://d111111abcdef8.cloudfront.net/governance"
# credentials must be >= 32 characters. Discovered by validating through the real SDK model rather
# than a hand-written dict -- a shorter value is rejected at the transport boundary.
AUTH = {"schemes": ["Bearer"], "credentials": "gov-token-abc123-padded-to-32-chars"}


@pytest.fixture()
def dynamo_table(monkeypatch):
    with mock_aws():
        client = boto3.client("dynamodb", region_name=REGION)
        client.create_table(
            TableName=TABLE_NAME,
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        monkeypatch.setenv("STATE_TABLE_NAME", TABLE_NAME)
        monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
        yield client


@pytest.fixture()
def agent():
    return main.ReferenceSellerAgent()


def params(**overrides):
    """A request that the real SDK model accepts, so the fixture cannot drift from the protocol."""
    body = {
        "idempotency_key": "sync-gov-key-0000000001",
        "accounts": [
            {
                "account": {"brand": BRAND, "operator": OPERATOR},
                "governance_agents": [{"url": GOV_URL, "authentication": AUTH}],
            }
        ],
    }
    body.update(overrides)
    # Round-trips through the SDK model: if the protocol changes shape, this raises here rather than
    # letting a stale hand-written dict keep passing.
    return SyncGovernanceRequest.model_validate(body).model_dump(mode="json", exclude_none=True)


def call(agent, body):
    return asyncio.run(agent.sync_governance(body, ToolContext(caller_identity="buyer-a")))


def declare_account(agent):
    """sync_governance binds to an EXISTING account, so create it the way a buyer would."""
    main.account_store.upsert(BRAND["domain"], OPERATOR)


class TestSuccess:
    def test_stores_the_agent_on_the_account_and_reports_synced(self, agent, dynamo_table):
        declare_account(agent)
        result = call(agent, params())

        assert "errors" not in result, result
        entry = result["accounts"][0]
        assert entry["status"] == "synced"
        assert entry["governance_agents"][0]["url"] == GOV_URL

        # Persisted, including the credentials the seller must present when calling the agent.
        record = main.account_store.get(BRAND["domain"], OPERATOR)
        stored = record["governance_agents"][0]
        assert stored["url"] == GOV_URL
        assert stored["authentication"]["credentials"] == AUTH["credentials"]

    def test_never_echoes_the_credentials(self, agent, dynamo_table):
        # Write-only. Checked on the whole serialised response rather than one field, because a leak
        # would most likely arrive by something spreading the input entry onto the output.
        declare_account(agent)
        result = call(agent, params())
        assert AUTH["credentials"] not in repr(result)

    def test_omits_categories_when_the_request_does_not_carry_them(self, agent, dynamo_table):
        # The seller cannot know what an agent evaluates without asking it, and the request does
        # not say, so the field is omitted.
        declare_account(agent)
        result = call(agent, params())
        assert "categories" not in result["accounts"][0]["governance_agents"][0]

    def test_re_syncing_replaces_the_agent(self, agent, dynamo_table):
        declare_account(agent)
        call(agent, params())
        second = "https://d111111abcdef8.cloudfront.net/governance-2"
        call(
            agent,
            params(
                idempotency_key="sync-gov-key-0000000002",
                accounts=[
                    {
                        "account": {"brand": BRAND, "operator": OPERATOR},
                        "governance_agents": [{"url": second, "authentication": AUTH}],
                    }
                ],
            ),
        )
        stored = main.account_store.get(BRAND["domain"], OPERATOR)["governance_agents"]
        # Replaced, not appended: one agent owns the account.
        assert len(stored) == 1
        assert stored[0]["url"] == second


class TestRejections:
    def test_rejects_two_governance_agents(self, agent, dynamo_table):
        # maxItems: 1 is load-bearing per the SDK's own field description. Taking the first silently
        # would leave the buyer believing both were registered.
        declare_account(agent)
        body = params()
        body["accounts"][0]["governance_agents"] = [
            {"url": GOV_URL, "authentication": AUTH},
            {"url": GOV_URL + "-other", "authentication": AUTH},
        ]
        result = call(agent, body)
        assert "errors" in result
        assert "exactly one" in repr(result["errors"])

    def test_rejects_a_non_https_url(self, agent, dynamo_table):
        declare_account(agent)
        body = params()
        body["accounts"][0]["governance_agents"][0]["url"] = "http://insecure.example/governance"
        result = call(agent, body)
        assert "errors" in result
        assert "HTTPS" in repr(result["errors"])

    def test_rejects_missing_credentials(self, agent, dynamo_table):
        # An agent the seller cannot authenticate to is one it cannot consult. Refusing beats storing a
        # binding whose check can never run.
        declare_account(agent)
        body = params()
        body["accounts"][0]["governance_agents"][0]["authentication"] = {"schemes": ["Bearer"]}
        result = call(agent, body)
        assert "errors" in result
        assert "credentials" in repr(result["errors"])

    def test_rejects_an_unknown_account(self, agent, dynamo_table):
        # No declare_account() here: binding to an account nobody declared would create a record no
        # later call could find.
        result = call(agent, params())
        assert "errors" in result
        assert "ACCOUNT_NOT_FOUND" in repr(result["errors"])

    def test_rejects_a_short_idempotency_key(self, agent, dynamo_table):
        declare_account(agent)
        body = params()
        body["idempotency_key"] = "tooshort"
        result = call(agent, body)
        assert "errors" in result
        assert "idempotency_key" in repr(result["errors"])

    def test_rejects_an_empty_accounts_array(self, agent, dynamo_table):
        result = call(agent, {"idempotency_key": "sync-gov-key-0000000003", "accounts": []})
        assert "errors" in result

    def test_rejects_account_id_only_references(self, agent, dynamo_table):
        # Documented limitation shared with sync_accounts: accounts are keyed by (brand, operator).
        declare_account(agent)
        body = params()
        body["accounts"][0]["account"] = {"account_id": "acct-123"}
        result = call(agent, body)
        assert "errors" in result
        assert "UNSUPPORTED_PROVISIONING" in repr(result["errors"])


class TestSyncGovernanceIsActuallyReachable:
    """Implementing the handler does not make the tool callable.

    `advertised_tools` is what `tools/list` publishes and what the dispatcher will accept. A handler
    absent from it answers `Unknown tool: sync_governance` no matter how complete it is -- and that is
    what shipped: the handler and every test above it passed while the deployed agent rejected the call,
    because these tests reach `agent.sync_governance` directly and never cross the dispatcher.

    So this asserts against the SDK's OWN view (`get_tools_for_handler`, the same function the MCP
    server builds `tools/list` from) rather than re-listing names by hand, which would only restate the
    set that was already wrong.
    """

    def test_sync_governance_is_advertised(self, agent):
        assert "sync_governance" in agent.advertised_tools

    def test_advertised_tools_matches_dispatcher(self, agent):
        from adcp.server.mcp_tools import get_tools_for_handler

        dispatched = {tool["name"] for tool in get_tools_for_handler(agent)}
        # Equality, not a subset: an advertised-but-undispatched tool is a promise the agent cannot
        # keep, and a dispatched-but-unadvertised one is invisible to discovery. Both are defects.
        assert dispatched == set(agent.advertised_tools)
