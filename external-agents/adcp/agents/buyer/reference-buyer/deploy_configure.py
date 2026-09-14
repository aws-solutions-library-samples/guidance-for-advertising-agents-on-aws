"""
One-off script to configure the AgentCore Runtime deployment for this agent
using the bedrock-agentcore-starter-toolkit. Run once (or again to
reconfigure) from this directory. Run deploy_cognito_setup.py first (it
writes the COGNITO_* values this script reads from .env):

    source .venv/bin/activate
    python3 deploy_cognito_setup.py   # creates/reuses the Cognito user pool
    python3 deploy_configure.py

This writes a .bedrock_agentcore.yaml config and a generated Dockerfile,
but does not build or deploy anything (see deploy_launch.py for that).

Configures the runtime's inbound auth as a Cognito JWT bearer authorizer
(authorizer_configuration), replacing the default IAM/SigV4-only inbound
auth. After this, invoke_agent_runtime calls without a valid Cognito access
token in the Authorization header are rejected by AgentCore Runtime itself,
not just by application code.
"""


from aws_region import region
import os
from pathlib import Path

from dotenv import load_dotenv
from bedrock_agentcore_starter_toolkit import Runtime

load_dotenv()

EXECUTION_ROLE_ARN = os.environ["EXECUTION_ROLE_ARN"]
REGION = region()
#: Instance prefix (see deploy_all.resolve_prefix). Underscore joiner: AgentCore runtime names allow
#: only [a-zA-Z0-9_], no hyphens, and the prefix is bare alphanumeric so `<prefix>_buyer_agent` is
#: valid. Must match AGENT_NAMES in deploy_execution_role.py. Defaults to 'adcp' for a standalone run.
INSTANCE_PREFIX = os.environ.get("INSTANCE_PREFIX", "adcp").strip() or "adcp"
AGENT_NAME = f"{INSTANCE_PREFIX}_buyer_agent"

_DOCKERFILE_PATH = Path(__file__).parent / "Dockerfile"


def delete_stale_dockerfile() -> None:
    """Force Runtime.configure() to regenerate this directory's Dockerfile
    for app.py, rather than silently reusing whatever Dockerfile happens to
    be on disk from a previous configure() call.

    This directory (agentcore/) hosts only the HTTP runtime (app.py) now —
    the A2A runtime (a2a_runtime/a2a_entrypoint.py, see
    deploy_buyer_agent_a2a.py) was moved into its own directory specifically
    so the two deployments can never again collide on one shared Dockerfile
    path. That collision was a real, silently-broken deployment bug: the
    toolkit's "reuse existing Dockerfile" behavior (logged as "Using
    existing Dockerfile: ...") happily reused a Dockerfile whose CMD
    invoked the *other* entrypoint's module, producing a runtime that
    deployed successfully (status READY) but silently served the wrong
    process and never responded on the protocol's actual port — confirmed
    live (agent card requests hung indefinitely, zero CloudWatch log
    entries, because nothing was listening on port 9000). Kept here as
    defense-in-depth for this directory even though it's single-runtime
    now — cheap insurance against the same class of bug if a future
    entrypoint change reintroduces a stale Dockerfile.
    """
    if _DOCKERFILE_PATH.exists():
        _DOCKERFILE_PATH.unlink()


def build_authorizer_configuration() -> dict:
    discovery_url = os.environ["COGNITO_DISCOVERY_URL"]
    client_id = os.environ["COGNITO_CLIENT_ID"]
    # Two tiers of caller, both legitimate: the browser's public client (USER_PASSWORD_AUTH) and
    # confidential client_credentials clients used for agent-to-agent calls. Both must appear here or
    # the platform rejects the machine callers before the container ever sees them - and it must stay in
    # step with auth.py's own allowlist, which reads the same variable.
    additional = [
        c.strip()
        for c in os.environ.get("COGNITO_ADDITIONAL_CLIENT_IDS", "").split(",")
        if c.strip()
    ]
    return {
        "customJWTAuthorizer": {
            "discoveryUrl": discovery_url,
            # De-duplicated, order preserved.
            "allowedClients": list(dict.fromkeys([client_id, *additional])),
        }
    }


def build_request_header_configuration() -> dict:
    # AgentCore Runtime validates the Authorization header against the JWT
    # authorizer above before the request reaches this container, but it
    # does NOT forward the header into the app process unless it's on this
    # allowlist. Without this, app.py's own verify_bearer_token() sees no
    # Authorization header at all on the deployed runtime (confirmed live:
    # omitting this produced "401 Unauthorized: Missing Authorization
    # header." even on requests the platform had already accepted).
    return {"requestHeaderAllowlist": ["Authorization"]}


if __name__ == "__main__":
    delete_stale_dockerfile()
    runtime = Runtime()
    result = runtime.configure(
        entrypoint="app.py",
        execution_role=EXECUTION_ROLE_ARN,
        agent_name=AGENT_NAME,
        requirements_file="requirements.txt",
        region=REGION,
        deployment_type="container",
        auto_create_ecr=True,
        non_interactive=True,
        authorizer_configuration=build_authorizer_configuration(),
        request_header_configuration=build_request_header_configuration(),
    )
    print(result)
