# Governance agent container image.
#
# This Dockerfile IS source and must stay tracked, which is the opposite of the buyer's situation. The two
# deployment paths in this repo behave differently and conflating them has cost a deployment before:
#
#   * the `agentcore` CLI Container path (this agent and all the sellers) REQUIRES a Dockerfile at the
#     runtime's `codeLocation` and does not generate one -- `agentcore deploy` refuses to run without it
#     ("Container agents require a Dockerfile");
#   * the starter-toolkit path (`Runtime().configure()`, used by the buyer) GENERATES and overwrites the
#     Dockerfile, which is why `deploy_configure.py` deletes any stale one before configuring.
#
# So: never hand-author one for the buyer, always keep one here.
#
# Modelled on the reference seller's, deliberately, so the two stay comparable. Differences from it are
# only what this agent actually needs.
FROM public.ecr.aws/docker/library/python:3.12-slim-trixie

RUN pip install --no-cache-dir uv

# U4/Part 1: `pytesseract` (pyproject.toml) is a Python binding, not the OCR engine itself -- the
# real engine is this system binary. Without it, handle_get_creative_features's text-extraction
# feature would have nothing to call and could not be a real computation.
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

ARG UV_DEFAULT_INDEX
ARG UV_INDEX

WORKDIR /app

ENV UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_PROGRESS=1 \
    PYTHONUNBUFFERED=1 \
    DOCKER_CONTAINER=1 \
    UV_DEFAULT_INDEX=${UV_DEFAULT_INDEX} \
    UV_INDEX=${UV_INDEX} \
    PATH="/app/.venv/bin:$PATH"

RUN useradd -m -u 1000 bedrock_agentcore

# Dependencies before source, so a source-only change reuses the dependency layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY --chown=bedrock_agentcore:bedrock_agentcore . .
RUN uv sync --frozen --no-dev

USER bedrock_agentcore

# AgentCore Runtime service contract ports
# https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-service-contract.html
# 8080: HTTP Mode
# 8000: MCP Mode  <- this agent, matching `protocol: MCP` and main.py's serve(port=8000)
# 9000: A2A Mode
EXPOSE 8080 8000 9000

# No sibling package is placed on PYTHONPATH here, unlike the Triton seller: everything this runtime
# imports lives inside `codeLocation`, which is the only directory the build is contractually going to
# copy.

# `opentelemetry-instrument` wraps the process to auto-instrument it, paired with
# `instrumentation.enableOtel: true` in agentcore.json and the pinned `aws-opentelemetry-distro`
# dependency in pyproject.toml. All three are required and none is sufficient alone:
#
#   * enableOtel alone gives the container the OTel environment and an endpoint to export to, with
#     nothing generating spans;
#   * the distro alone installs the instrumentation libraries without activating them;
#   * this CMD alone has nothing to load, and `opentelemetry-instrument` exits non-zero when the distro
#     is absent -- which is a startup failure, not a degraded start.
#
# The wrapper is a no-op for the agent's own logic: `main.py` is unchanged and the module is still
# started as `-m main`.
CMD ["opentelemetry-instrument", "python", "-m", "main"]
