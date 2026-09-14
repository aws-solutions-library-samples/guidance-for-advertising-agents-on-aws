"""
Configures and launches the Buyer Agent's A2A AgentCore Runtime deployment
— a second, separate runtime from the existing HTTP one (deploy_launch.py),
since AgentCore Runtime deploys exactly one protocol per runtime (HTTP on
port 8080 mounted at /invocations vs A2A on port 9000 mounted at /). This
is what makes the Buyer Agent genuinely invokable by other agents over
A2A, not just from this project's own chat UI — see
a2a_runtime/a2a_entrypoint.py and .kiro/pick_up_external_invocation.md.

This script `os.chdir()`s into a2a_runtime/ before calling configure()/
launch() (see below) — `Runtime.configure()` always writes its generated
Dockerfile to the process's current directory, so giving this runtime its
own directory (a2a_runtime/, containing a2a_entrypoint.py plus symlinks
back to the shared agent.py/adcp_tools.py/etc. modules) means it always
gets its own Dockerfile on disk, never the HTTP runtime's. Before this,
both runtimes' configure() calls wrote to this same reference-buyer/
directory and shared one Dockerfile path, which caused a real, silent
deployment bug (see README's "Buyer Agent as an A2A server" section — a
"successfully deployed" A2A runtime that was actually still running the
HTTP entrypoint's CMD, with nothing listening on the A2A port). Two
separate directories, two permanently separate Dockerfiles — that class
of bug can't recur here regardless of deploy order.

requirements.txt is a real copy inside a2a_runtime/, not a symlink like
the other shared modules — the toolkit's dependency-file resolution calls
.resolve() on an explicit requirements_file= path and rejects one that
dereferences outside the current project root (a2a_runtime/), which a
symlink back to ../requirements.txt does. Keep both files in sync
manually if dependencies change (or replace this with an equivalent copy
step, if that becomes a maintenance burden).

Reuses the same execution role, Cognito JWT authorizer, and Authorization
header allowlist as the HTTP runtime (deploy_configure.py) — the role's
trust policy is scoped to this account's bedrock-agentcore service
principal generally, not to one specific runtime ARN, so it works
unmodified for a second runtime. Run after deploy_cognito_setup.py and
deploy_sessions_table.py (both already run for the HTTP runtime) have
written their values to .env:

    source .venv/bin/activate
    python3 deploy_buyer_agent_a2a.py

Entrypoint is a2a_runtime/a2a_entrypoint.py, not app.py — the A2A runtime
is a distinct container/entrypoint from the HTTP one, though both import
the same agent.py::build_agent() (via symlink) so they share tools, seller
registry, and reasoning-step recording.
"""

import os
from pathlib import Path

from bedrock_agentcore_starter_toolkit import Runtime
from dotenv import load_dotenv

from runtime_env import build_env_vars, report
from deploy_configure import (
    EXECUTION_ROLE_ARN,
    REGION,
    build_authorizer_configuration,
    build_request_header_configuration,
)

load_dotenv()

#: Instance prefix (see deploy_all.resolve_prefix). Underscore joiner (runtime names forbid hyphens);
#: must match AGENT_NAMES in deploy_execution_role.py. Defaults to 'adcp' for a standalone run.
INSTANCE_PREFIX = os.environ.get("INSTANCE_PREFIX", "adcp").strip() or "adcp"
AGENT_NAME = f"{INSTANCE_PREFIX}_buyer_agent_a2a"
A2A_RUNTIME_DIR = Path(__file__).parent / "a2a_runtime"

# Runtime.configure()/.launch() resolve paths (Dockerfile output, .env
# loading via load_dotenv() above, requirements_file=) relative to the
# process's actual current working directory, not this script's location
# — os.chdir() is the toolkit-supported way to point a configure()/
# launch() pair at a different directory (there's no source_path= kwarg
# on the notebook-style Runtime API used elsewhere in this project).
os.chdir(A2A_RUNTIME_DIR)

# Same defense-in-depth deploy_launch.py gets from
# deploy_configure.delete_stale_dockerfile(), applied to *this* directory.
# That helper can't be reused here: its _DOCKERFILE_PATH is hardcoded to
# reference-buyer/Dockerfile, so calling it would delete the HTTP runtime's
# Dockerfile and leave this one in place — the opposite of what's wanted.
# The Dockerfile is a generated build artifact (gitignored, see .gitignore),
# never hand-maintained, so deleting it forces configure() to regenerate it
# from the current entrypoint and requirements.txt rather than silently
# reusing a stale one ("Using existing Dockerfile: ...").
_a2a_dockerfile = A2A_RUNTIME_DIR / "Dockerfile"
if _a2a_dockerfile.exists():
    print(f"Deleting stale generated Dockerfile so configure() regenerates it: {_a2a_dockerfile}")
    _a2a_dockerfile.unlink()

runtime = Runtime()

configure_result = runtime.configure(
    entrypoint="a2a_entrypoint.py",
    execution_role=EXECUTION_ROLE_ARN,
    agent_name=AGENT_NAME,
    requirements_file="requirements.txt",
    region=REGION,
    deployment_type="container",
    protocol="A2A",
    auto_create_ecr=True,
    non_interactive=True,
    authorizer_configuration=build_authorizer_configuration(),
    request_header_configuration=build_request_header_configuration(),
)
print("=== configure ===")
print(configure_result)

env_vars = build_env_vars()
report(env_vars, "A2A runtime")

print("\n=== launch ===")
# auto_update_on_conflict=True: this a2a_runtime/.bedrock_agentcore.yaml is
# a fresh config (this directory didn't exist before), so it has no record
# of the already-deployed runtime's agent_id - without this flag, AWS
# rejects the create call as a name conflict with the runtime that already
# exists from the very first deployment (before this directory split).
launch_result = runtime.launch(env_vars=env_vars, auto_update_on_conflict=True)
print(launch_result)
print(f"\nAgent runtime ARN: {launch_result.agent_arn}")
print("Add this to .env as BUYER_AGENT_A2A_RUNTIME_ARN.")
