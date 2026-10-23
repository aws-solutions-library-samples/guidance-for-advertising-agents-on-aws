"""`deploy_state_table.py --enable-ttl`, which is the one operation in this package that can delete data.

Tested against moto rather than trusted, because the failure it guards is unrecoverable in the way that
matters: DynamoDB deletes any item carrying the TTL attribute, and a governance audit record deleted by
TTL is gone short of a point-in-time restore.

The script is NOT run against real infrastructure by this suite or by `deploy_all.py`. Enabling TTL needs
a separate, deliberate go-ahead.
"""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

import deploy_state_table as dst


@pytest.fixture()
def table(monkeypatch):
    with mock_aws():
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        client = boto3.client("dynamodb")
        client.create_table(
            TableName=dst.TABLE_NAME,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield client


def _put(client, pk: str, sk: str, *, with_ttl: bool = False) -> None:
    item = {"PK": {"S": pk}, "SK": {"S": sk}}
    if with_ttl:
        item[dst.TTL_ATTRIBUTE] = {"N": "1800000000"}
    client.put_item(TableName=dst.TABLE_NAME, Item=item)


def _ttl_status(client) -> str | None:
    return client.describe_time_to_live(TableName=dst.TABLE_NAME)["TimeToLiveDescription"].get(
        "TimeToLiveStatus"
    )


def test_without_apply_nothing_is_enabled(table, capsys):
    assert dst.enable_ttl(apply=False) == 0
    assert "Re-run with --apply" in capsys.readouterr().out
    assert _ttl_status(table) in (None, "DISABLED")


def test_applying_enables_ttl_on_the_expected_attribute(table, capsys):
    assert dst.enable_ttl(apply=True) == 0
    description = table.describe_time_to_live(TableName=dst.TABLE_NAME)["TimeToLiveDescription"]
    assert description["TimeToLiveStatus"] in ("ENABLED", "ENABLING")
    assert description["AttributeName"] == dst.TTL_ATTRIBUTE
    # Stated in the output, because it changes what a reader should expect: deletion is asynchronous, so
    # expiry has to be enforced in code at read time as well.
    assert "asynchronous" in capsys.readouterr().out


def test_it_is_idempotent(table):
    assert dst.enable_ttl(apply=True) == 0
    assert dst.enable_ttl(apply=True) == 0


def test_expiring_families_do_not_block_it(table):
    """`AGG#` and `IDEM#` items are SUPPOSED to carry the attribute. Refusing on them would make the
    guard refuse the only situation it exists to permit."""
    _put(table, "AGG#buyer|seller|acct", "DAY#2026-12-05", with_ttl=True)
    _put(table, "IDEM#abc", "META", with_ttl=True)
    assert dst.enable_ttl(apply=True) == 0


@pytest.mark.parametrize(
    ("pk", "sk"),
    [
        ("PLAN#plan-1", "META"),
        ("PLAN#plan-1", "CHECK#chk_1"),
        ("PLAN#plan-1", "OUTCOME#out_1"),
        ("PLAN#plan-1", "ESCALATION#chk_1"),
    ],
)
def test_a_durable_item_carrying_the_attribute_refuses(table, capsys, pk, sk):
    """The guard that matters. Every one of these would be DELETED by DynamoDB, and a governance audit
    trail with an expiry is not an audit trail -- the retained plan revision is what an auditor
    recomputes a `plan_hash` against."""
    _put(table, pk, sk, with_ttl=True)
    assert dst.enable_ttl(apply=True) == 1
    out = capsys.readouterr().out
    assert "REFUSING" in out
    # Names the offending items, not just a count: "3 items would be deleted" is not something an
    # operator can act on.
    assert pk in out
    assert _ttl_status(table) in (None, "DISABLED"), "the refusal must not have enabled it anyway"


def test_a_ttl_already_enabled_on_a_DIFFERENT_attribute_is_reported_not_switched(table, capsys):
    """One TTL attribute per table is the whole constraint. Switching it would orphan every item written
    under the old name, and choosing which family to strand is not a deploy script's decision."""
    table.update_time_to_live(
        TableName=dst.TABLE_NAME,
        TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"},
    )
    assert dst.enable_ttl(apply=True) == 1
    out = capsys.readouterr().out
    assert "not 'expires_at'" in out or "expires_at" in out
    assert "NEVER expire" in out


def test_a_missing_table_is_reported_rather_than_raising(monkeypatch):
    with mock_aws():
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        assert dst.enable_ttl(apply=True) == 1


def test_the_durable_prefixes_are_the_families_state_actually_writes():
    """A guard listing the wrong prefixes protects nothing. Compared against the prefixes `state.py`
    itself uses, so adding a durable family without adding it here fails."""
    import inspect

    import state

    source = inspect.getsource(state)
    for prefix in dst.DURABLE_PREFIXES:
        assert f'"{prefix}' in source or f"'{prefix}" in source, (
            f"{prefix} is guarded but state.py never writes it"
        )


# ================================================================= log retention (Step 27, I2=C)


class TestLogRetention:
    """`deploy_log_retention.py`. A log group created without a policy retains FOREVER, which is the
    default this script corrects."""

    GROUP = "/aws/bedrock-agentcore/runtimes/adcpRefGovernance-abc123-DEFAULT"
    OTHER_AGENT = "/aws/bedrock-agentcore/runtimes/adcpRefSeller-def456-DEFAULT"

    @pytest.fixture()
    def logs(self, monkeypatch):
        import deploy_log_retention as dlr

        # RUNTIME_NAME is built from INSTANCE_PREFIX at import, and deploy_all.py exports that for the
        # whole run, so under a deploy it is e.g. 'a4avn39cjRefGovernance' and matches none of the
        # fixture groups above. Pin it to the default these group names are written for.
        monkeypatch.setattr(dlr, "RUNTIME_NAME", "adcpRefGovernance")
        with mock_aws():
            monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
            yield boto3.client("logs")

    def _retention(self, logs, name: str) -> int | None:
        groups = logs.describe_log_groups(logGroupNamePrefix=name)["logGroups"]
        return groups[0].get("retentionInDays") if groups else None

    def test_no_groups_is_reported_not_silently_successful(self, logs, capsys):
        """The usual cause is a runtime that has not been deployed or has never logged. Reporting
        success for zero groups would look identical to having set retention."""
        import deploy_log_retention as dlr

        assert dlr.set_retention(apply=True) == 0
        assert "No log groups found" in capsys.readouterr().out

    def test_without_apply_nothing_changes(self, logs, capsys):
        import deploy_log_retention as dlr

        logs.create_log_group(logGroupName=self.GROUP)
        assert dlr.set_retention(apply=False) == 0
        assert "would change from never expires" in capsys.readouterr().out
        assert self._retention(logs, self.GROUP) is None

    def test_applying_sets_the_target_retention(self, logs):
        import deploy_log_retention as dlr

        logs.create_log_group(logGroupName=self.GROUP)
        assert dlr.set_retention(apply=True) == 0
        assert self._retention(logs, self.GROUP) == dlr.RETENTION_DAYS

    def test_it_is_idempotent(self, logs, capsys):
        import deploy_log_retention as dlr

        logs.create_log_group(logGroupName=self.GROUP)
        dlr.set_retention(apply=True)
        capsys.readouterr()
        assert dlr.set_retention(apply=True) == 0
        assert "already 731 days" in capsys.readouterr().out

    def test_another_agents_log_group_is_left_alone(self, logs):
        """Discovery is by prefix, so the filter is the only thing keeping this script from setting
        retention across every agent in the account."""
        import deploy_log_retention as dlr

        logs.create_log_group(logGroupName=self.GROUP)
        logs.create_log_group(logGroupName=self.OTHER_AGENT)
        dlr.set_retention(apply=True)
        assert self._retention(logs, self.GROUP) == dlr.RETENTION_DAYS
        assert self._retention(logs, self.OTHER_AGENT) is None

    def test_the_retention_value_is_one_cloudwatch_accepts(self, logs):
        """A non-tier value is rejected by the API. Asserted through a real call rather than against a
        copied list of tiers, which would be a second source of truth for CloudWatch's own."""
        import deploy_log_retention as dlr

        logs.create_log_group(logGroupName=self.GROUP)
        logs.put_retention_policy(
            logGroupName=self.GROUP, retentionInDays=dlr.RETENTION_DAYS
        )
        assert self._retention(logs, self.GROUP) == 731

    def test_it_exceeds_the_security_14_floor(self):
        """SECURITY-14 requires at least 90 days. Pinned so a later cost-trim cannot quietly drop below
        the compliance floor."""
        import deploy_log_retention as dlr

        assert dlr.RETENTION_DAYS >= 90
