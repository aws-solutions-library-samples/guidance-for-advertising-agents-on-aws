"""The environment both buyer runtimes are launched with.

One definition, imported by `deploy_launch.py` (HTTP) and `deploy_buyer_agent_a2a.py` (A2A), because
the two runtimes are built from the **same modules** -- `agent.py`, `adcp_tools.py` and `auth.py` in
`a2a_runtime/` are symlinks to the copies here. A variable one runtime needs is therefore a variable
both need, and any divergence is a defect rather than a configuration choice.

## Why this file exists

`deploy_buyer_agent_a2a.py` used to carry its own hand-written copy of this dict, with a comment
saying the two were "kept in step ... a divergent environment between two runtimes built from the
same modules is how the next surprise gets made". The surprise arrived anyway:
`GOVERNANCE_AGENT_MCP_URL` was added to the HTTP list and not the A2A one, so the deployed A2A agent
answered every governance request with `GOVERNANCE_AGENT_MCP_URL is not set` while the HTTP agent
registered plans normally.

Note what did *not* catch it. The tools worked. The tests passed. `verify_governance_journey_live.py`
passed -- because it invokes the runtime named by `AGENT_RUNTIME_ARN`, which is the HTTP one. A live
end-to-end check against one of two runtimes says nothing about the other, and the failure surfaced
only when a real caller used the A2A path.

A comment asking future readers to keep two lists in step is not a mechanism. This is.

## Adding a variable

Add it here once. Both runtimes get it. If a variable ever genuinely belongs to only one runtime,
put it in that script's own `extra` argument and say why -- do not reintroduce a second copy of the
shared set.
"""


from aws_region import region
import os
from typing import Any


def build_env_vars(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Every env var the buyer's container needs, resolved from this process's environment.

    `os.environ[...]` for values with no safe default -- a missing one fails the deploy loudly rather
    than launching a container that half-works. `os.environ.get(..., "")` where an empty value has a
    defined meaning, which is documented per variable below.

    `extra` is for a genuinely runtime-specific variable, and is merged last.
    """
    env_vars: dict[str, str] = {
        # A third-party seller reached with auth_type "static_bearer" names its token variable in
        # `auth_token_env` (see seller_agents.py / agents_registry.py). That variable is NOT
        # forwarded automatically: add it here so the container receives it. Nothing is listed by
        # default, because requiring a token for a seller this project does not deploy would fail
        # the deploy for anyone who has no such seller configured.
        "SELLER_AGENTS_JSON": os.environ["SELLER_AGENTS_JSON"],
        "DEFAULT_SELLER_AGENT_ID": os.environ.get("DEFAULT_SELLER_AGENT_ID", ""),
        # Registry of A2A agents the chat UI can chat to (agents_registry.py). Needed in the container,
        # not just locally: /config reads it to build the agent selector, the a2a_chat action resolves
        # proxied agents through it, and this_buyer_agent_url() reads it so the buyer can name itself
        # as check_governance's `caller`.
        #
        # `.get`, not `[...]`, because on a FIRST deploy it cannot exist yet. Its `buyer-a2a` entry
        # holds this buyer's own A2A invoke URL, which is not known until that runtime is deployed --
        # and deploying it calls this function. A required key made that cycle fatal
        # (`KeyError: 'AGENTS_JSON'` at step 10) on any machine without a `.env` left over from an
        # earlier deploy.
        #
        # Empty is therefore a legitimate first-pass state, not a silent defect: deploy_all.py builds
        # the registry at step 12.5 and relaunches both runtimes at 12.6 so they carry the real value.
        # `report()` prints it as EMPTY meanwhile, so a runtime left in that state is visible.
        "AGENTS_JSON": os.environ.get("AGENTS_JSON", ""),
        "DEFAULT_AGENT_ID": os.environ.get("DEFAULT_AGENT_ID", ""),
        "BEDROCK_MODEL_ID": os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-5"),
        "AWS_REGION": region(),
        "SESSION_STORAGE_BUCKET": os.environ["SESSION_STORAGE_BUCKET"],
        "SESSION_STORAGE_PREFIX": os.environ.get("SESSION_STORAGE_PREFIX", "adcp-buyer-agent"),
        # DynamoDB table backing session_store.py's reasoning-step recording (see
        # deploy_sessions_table.py) - required for the UI's Sessions list and viewer to have
        # anything to read.
        "SESSIONS_TABLE_NAME": os.environ["SESSIONS_TABLE_NAME"],
        "COGNITO_USER_POOL_ID": os.environ["COGNITO_USER_POOL_ID"],
        "COGNITO_CLIENT_ID": os.environ["COGNITO_CLIENT_ID"],
        "COGNITO_DISCOVERY_URL": os.environ["COGNITO_DISCOVERY_URL"],
        "COGNITO_REGION": os.environ["COGNITO_REGION"],
        # seller_agents.py's "cognito_bearer" auth_type (used for seller agents deployed to this same
        # account's AgentCore Runtime) mints tokens via auth.get_test_user_access_token(), which needs
        # the test user's own credentials - these must also be present in the deployed container's
        # environment, not just locally.
        "COGNITO_TEST_USERNAME": os.environ["COGNITO_TEST_USERNAME"],
        "COGNITO_TEST_USER_PASSWORD": os.environ["COGNITO_TEST_USER_PASSWORD"],
        # Confidential client_credentials app clients allowed to call this runtime, beyond the
        # browser's public client. auth.py reads this at request time to build its allowlist, so
        # omitting it here would make the container reject machine callers that the platform's own
        # authorizer had already accepted. Defaulted rather than required: an empty value restores the
        # previous single-client behaviour, so an environment that has not run deploy_cognito_m2m.py
        # still deploys.
        "COGNITO_ADDITIONAL_CLIENT_IDS": os.environ.get("COGNITO_ADDITIONAL_CLIENT_IDS", ""),
        # Registry of governance agents this buyer can bind to (governance_agents.py), mirroring
        # SELLER_AGENTS_JSON's shape. Replaces the old single-URL GOVERNANCE_AGENT_MCP_URL
        # (BR-U2-3/BR-U2-4): a single env var could not express an external static_bearer agent
        # (Boltive) alongside an in-account cognito_bearer one. Defaulted rather than required so the
        # buyer still deploys before any governance agent exists; the tools then report the missing
        # configuration instead of the container failing to start, and "not governed" is a legitimate
        # state per BR-U2-11.
        #
        # This is the variable whose absence from the A2A runtime is why this module exists -- its
        # predecessor GOVERNANCE_AGENT_MCP_URL was the one that was added to the HTTP list and not the
        # A2A one.
        "GOVERNANCE_AGENTS_JSON": os.environ.get("GOVERNANCE_AGENTS_JSON", ""),
        # BR-U2-8: the reallocation_threshold sync_plans requires but no brief ever states, expressed
        # as a percentage of budget.total. A POLICY default, not a measurement.
        "BUYER_REALLOCATION_THRESHOLD_PCT": os.environ.get("BUYER_REALLOCATION_THRESHOLD_PCT", "0.1"),
    }

    if extra:
        env_vars.update(extra)
    return env_vars


def report(env_vars: dict[str, Any], runtime_label: str) -> None:
    """Print which variables are set and which resolved empty, without printing any value.

    Several of these are credentials, so the values are never echoed. What matters at deploy time is
    whether an optional variable resolved to a value or to `""` -- an empty
    `GOVERNANCE_AGENTS_JSON` is precisely the state that produces a deployed agent reporting
    "GOVERNANCE_AGENTS_JSON is not set" to a user, and it should be visible in the deploy log
    rather than only in a chat transcript days later.
    """
    empty = sorted(key for key, value in env_vars.items() if not str(value).strip())
    print(f"  {runtime_label}: {len(env_vars)} env vars, {len(empty)} empty")
    for key in empty:
        print(f"    EMPTY: {key}")
