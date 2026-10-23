"""Both buyer runtimes get the same environment.

This exists because they did not. `GOVERNANCE_AGENT_MCP_URL` was added to `deploy_launch.py`'s
hand-written env dict and not to `deploy_buyer_agent_a2a.py`'s, so the deployed A2A agent told a real
user `GOVERNANCE_AGENT_MCP_URL is not set` while the HTTP agent registered plans normally.

Nothing failed. The tools were correct, the unit tests passed, and the live end-to-end check
(`verify_governance_journey_live.py`) passed -- because it invokes `AGENT_RUNTIME_ARN`, the HTTP
runtime. One of two runtimes verified is not the pair verified.

The A2A script already carried a comment asking the reader to keep the two lists in step. The
comment was correct and did not work. These tests are the mechanism that replaces it: they read both
deploy scripts as text and assert neither one builds its own env dict.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

HERE = Path(__file__).parent
HTTP_DEPLOY = HERE / "deploy_launch.py"
A2A_DEPLOY = HERE / "deploy_buyer_agent_a2a.py"

#: Every variable `build_env_vars` must supply. Deliberately written out rather than derived from the
#: function, so deleting one from the source is a test failure instead of a silently smaller set.
EXPECTED_KEYS = {
    "AGENTS_JSON",
    "AWS_REGION",
    "BEDROCK_MODEL_ID",
    "COGNITO_ADDITIONAL_CLIENT_IDS",
    "COGNITO_CLIENT_ID",
    "COGNITO_DISCOVERY_URL",
    "COGNITO_REGION",
    "COGNITO_TEST_USERNAME",
    "COGNITO_TEST_USER_PASSWORD",
    "COGNITO_USER_POOL_ID",
    "DEFAULT_AGENT_ID",
    "DEFAULT_SELLER_AGENT_ID",
    "GOVERNANCE_AGENTS_JSON",
    "BUYER_REALLOCATION_THRESHOLD_PCT",
    "SELLER_AGENTS_JSON",
    "SESSIONS_TABLE_NAME",
    "SESSION_STORAGE_BUCKET",
    "SESSION_STORAGE_PREFIX",
}

#: The values with no safe default. A missing one must raise at deploy time, not launch a container
#: that half-works.
REQUIRED_IN_ENVIRONMENT = {
    "AGENTS_JSON",
    "COGNITO_CLIENT_ID",
    "COGNITO_DISCOVERY_URL",
    "COGNITO_REGION",
    "COGNITO_TEST_USERNAME",
    "COGNITO_TEST_USER_PASSWORD",
    "COGNITO_USER_POOL_ID",
    "SELLER_AGENTS_JSON",
    "SESSIONS_TABLE_NAME",
    "SESSION_STORAGE_BUCKET",
}


@pytest.fixture()
def full_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A complete environment, so `build_env_vars` can be exercised without a real `.env`."""
    for key in EXPECTED_KEYS:
        monkeypatch.setenv(key, f"value-for-{key}")


class TestBothScriptsShareOneDefinition:
    """The structural guarantee. A second hand-written dict is the defect, so its absence is asserted."""

    @pytest.mark.parametrize("script", [HTTP_DEPLOY, A2A_DEPLOY], ids=["http", "a2a"])
    def test_script_imports_the_shared_builder(self, script: Path) -> None:
        source = script.read_text()
        assert "from runtime_env import build_env_vars" in source
        assert "build_env_vars()" in source

    @pytest.mark.parametrize("script", [HTTP_DEPLOY, A2A_DEPLOY], ids=["http", "a2a"])
    def test_script_does_not_build_its_own_env_dict(self, script: Path) -> None:
        # `env_vars = {` is what both scripts used to have. Either reappearing means the two
        # environments can drift again, which is the whole failure this module prevents.
        source = script.read_text()
        assert not re.search(r"^env_vars\s*=\s*\{", source, re.MULTILINE), (
            f"{script.name} builds its own env_vars dict again. Add the variable to "
            "runtime_env.build_env_vars() instead, so both runtimes receive it."
        )


class TestTheEnvironmentItself:
    def test_supplies_every_expected_variable(self, full_environment: None) -> None:
        from runtime_env import build_env_vars

        assert set(build_env_vars()) == EXPECTED_KEYS

    def test_includes_the_governance_registry_that_was_missing(self, full_environment: None) -> None:
        # The specific regression. Named on its own so a failure says what broke.
        from runtime_env import build_env_vars

        assert "GOVERNANCE_AGENTS_JSON" in build_env_vars()

    @pytest.mark.parametrize("key", sorted(REQUIRED_IN_ENVIRONMENT))
    def test_required_variable_fails_loudly_when_unset(
        self, full_environment: None, monkeypatch: pytest.MonkeyPatch, key: str
    ) -> None:
        from runtime_env import build_env_vars

        monkeypatch.delenv(key, raising=False)
        with pytest.raises(KeyError):
            build_env_vars()

    def test_optional_variable_resolves_empty_rather_than_raising(
        self, full_environment: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An absent governance agent must not stop the buyer deploying: the tool reports the missing
        # configuration at call time, which is what the user actually saw.
        from runtime_env import build_env_vars

        monkeypatch.delenv("GOVERNANCE_AGENTS_JSON", raising=False)
        assert build_env_vars()["GOVERNANCE_AGENTS_JSON"] == ""

    def test_extra_is_merged_for_genuinely_runtime_specific_values(
        self, full_environment: None
    ) -> None:
        from runtime_env import build_env_vars

        env = build_env_vars({"ONLY_ONE_RUNTIME": "x"})
        assert env["ONLY_ONE_RUNTIME"] == "x"
        # And it does not remove anything shared.
        assert EXPECTED_KEYS.issubset(set(env))


class TestReporting:
    def test_names_empty_variables_without_printing_any_value(
        self, full_environment: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from runtime_env import build_env_vars, report

        monkeypatch.setenv("COGNITO_TEST_USER_PASSWORD", "super-secret-token")
        monkeypatch.delenv("GOVERNANCE_AGENTS_JSON", raising=False)
        env = build_env_vars()
        report(env, "HTTP runtime")

        out = capsys.readouterr().out
        # The empty one is named, so an unconfigured governance agent is visible in the deploy log
        # rather than only in a chat transcript.
        assert "EMPTY: GOVERNANCE_AGENTS_JSON" in out
        # Credentials are never echoed.
        assert "super-secret-token" not in out
