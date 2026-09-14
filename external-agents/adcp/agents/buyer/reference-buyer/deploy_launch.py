"""
Configures (if needed) and launches (build + deploy) the AgentCore Runtime
agent, with Cognito JWT bearer auth wired up as the runtime's inbound
authorizer. Run from this directory (run deploy_cognito_setup.py first if
you haven't already — it writes the COGNITO_* values this script reads):

    source .venv/bin/activate
    python3 deploy_cognito_setup.py
    python3 deploy_launch.py

Uses the default cloud CodeBuild path (no local/local_build flags) since
there's no local container engine configured for this toolkit version.
Environment variables are passed explicitly at launch since .env is
excluded from the build context (it contains secrets that should never be
baked into an image).

Runtime.configure()/.launch() must run in the same process because
_config_path is in-memory instance state, not reloaded from the on-disk
.bedrock_agentcore.yaml on a fresh Runtime().
"""

import os

from dotenv import load_dotenv
from bedrock_agentcore_starter_toolkit import Runtime

from runtime_env import build_env_vars, report
from deploy_configure import (
    EXECUTION_ROLE_ARN,
    REGION,
    AGENT_NAME,
    build_authorizer_configuration,
    build_request_header_configuration,
    delete_stale_dockerfile,
)

load_dotenv()

delete_stale_dockerfile()
runtime = Runtime()

configure_result = runtime.configure(
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
print("=== configure ===")
print(configure_result)

env_vars = build_env_vars()
report(env_vars, "HTTP runtime")

print("\n=== launch ===")
# auto_update_on_conflict: without it, launching against an agent name that already exists fails with
#   ConflictException: Agent 'dev_buyer_agent' already exists
# so this script worked only on the very first deploy and broke on every re-run -- including any
# `deploy_all.py --resume-at`, which is the normal way to recover a partial deploy.
# deploy_buyer_agent_a2a.py already passed this flag for the same reason; this one did not.
launch_result = runtime.launch(env_vars=env_vars, auto_update_on_conflict=True)
print(launch_result)
