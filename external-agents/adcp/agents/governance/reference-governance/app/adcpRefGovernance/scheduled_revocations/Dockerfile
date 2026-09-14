# Scheduled Lambda: republishes the governance agent's revocation list on a 12h EventBridge
# schedule (see agentcore/cdk/lib/cdk-stack.ts for the Rule + DockerImageFunction, and
# ../deploy_revocations.py's module docstring for why this exists).
#
# This is a SEPARATE image from the main agent's Dockerfile, not a reuse of it: the main image
# runs an MCP server on AgentCore Runtime's service contract (main.py, ports 8000/8080/9000); this
# one runs a single Lambda handler and needs none of that surface. Building it as an AWS Lambda
# base image (rather than reusing main.py's plain-Python base) is what makes it a normal Lambda
# container -- Lambda's own image contract, not AgentCore's.
#
# BUILD CONTEXT IS THE PARENT DIRECTORY (app/adcpRefGovernance/), not this Dockerfile's own
# directory -- see cdk-stack.ts's `DockerImageCode.fromImageAsset(parentDir, { file:
# 'scheduled_revocations/Dockerfile' })`. That is what makes `pyproject.toml`, `uv.lock`, `jws.py`
# and `deploy_revocations.py` (all one level up from this file) reachable by COPY below.
FROM public.ecr.aws/lambda/python:3.12

# Reuses the PARENT package's pyproject.toml/uv.lock (not a second, possibly-drifting lockfile) so
# adcp/boto3/httpx stay pinned identically to the main governance runtime -- see handler.py's
# module docstring for why several of that lockfile's dependencies (mcp, pillow, pytesseract) go
# unused here without needing their own pin set.
RUN pip install --no-cache-dir uv

# Real bug found live: using `/tmp/build` as the build-time WORKDIR left the image's runtime
# WORKDIR pointing there too (Docker's WORKDIR persists across RUN/COPY unless reset). Lambda
# mounts a FRESH, empty tmpfs onto /tmp at container start, which hides whatever was baked into
# /tmp at build time -- so the container booted with its cwd pointing at a path that no longer
# existed, and every invocation failed immediately with `Runtime.InvalidWorkingDir: working dir
# does not exists`, before handler.py ever ran. Building under `${LAMBDA_TASK_ROOT}` instead (the
# same directory site-packages and the handler already live in, which is NOT under /tmp) avoids
# the trap entirely -- there is no separate build workdir to forget to reset.
WORKDIR ${LAMBDA_TASK_ROOT}
COPY pyproject.toml uv.lock ./
# --no-install-project: this directory has no installable project of its own, just the lockfile.
# Exported to the Lambda task root directly rather than a venv, matching the base image's own
# convention (site-packages on PYTHONPATH = LAMBDA_TASK_ROOT).
RUN uv export --frozen --no-dev --no-emit-project --format requirements-txt > /tmp/requirements.txt \
    && uv pip install --system --target "${LAMBDA_TASK_ROOT}" -r /tmp/requirements.txt \
    && rm -f pyproject.toml uv.lock /tmp/requirements.txt

# Only the two parent-package files this handler actually imports -- not main.py, state.py,
# policy.py, or their Pillow/pytesseract/DynamoDB-touching imports. See handler.py's module
# docstring. Paths are relative to the build context (the parent directory), per the note above.
COPY jws.py deploy_revocations.py governance_origin.py aws_region.py ./
COPY scheduled_revocations/handler.py ./

CMD ["handler.handler"]
