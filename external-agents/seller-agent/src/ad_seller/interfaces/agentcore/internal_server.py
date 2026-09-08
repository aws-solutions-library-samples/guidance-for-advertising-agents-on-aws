"""Crewai-free launcher for the seller's internal FastAPI + MCP server.

Extracted from ``http_main._start_fastapi_background`` so the Strands A2A
runtime can start the same internal server (REST on :8001 with the MCP server
mounted at /mcp and /mcp-sse) without importing ``crewai``. The retained CrewAI
``http_main`` delegates here to avoid divergence.
"""

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_INTERNAL_PORT = int(os.environ.get("INTERNAL_API_PORT", "8001"))
_started = False


def internal_port() -> int:
    """Port the internal FastAPI + MCP server listens on."""
    return _INTERNAL_PORT


def start_internal_server() -> None:
    """Start the internal FastAPI+MCP server in a daemon thread (idempotent).

    Blocks until ``/health`` returns 200 (30 attempts x 0.5s = 15s) then creates
    the internal API key. Raises RuntimeError if the server does not come up.
    """
    global _started
    if _started:
        return

    import threading
    import time

    import uvicorn

    from ad_seller.interfaces.api.main import app as fastapi_app

    os.environ["SELLER_AGENT_URL"] = f"http://localhost:{_INTERNAL_PORT}"

    config = uvicorn.Config(
        fastapi_app,
        host="0.0.0.0",
        port=_INTERNAL_PORT,
        log_level="info",
    )
    server = uvicorn.Server(config)

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.serve())

    threading.Thread(target=_run, daemon=True, name="fastapi-mcp-bg").start()

    for _ in range(30):
        try:
            import httpx

            resp = httpx.get(f"http://localhost:{_INTERNAL_PORT}/health", timeout=1.0)
            if resp.status_code == 200:
                logger.info("Internal FastAPI+MCP server ready on port %d", _INTERNAL_PORT)
                _started = True
                _create_internal_api_key()
                return
        except Exception:  # noqa: BLE001 - server still starting
            time.sleep(0.5)

    logger.error("Internal FastAPI+MCP failed to start on port %d within 15s", _INTERNAL_PORT)
    raise RuntimeError(f"Internal server failed to start on port {_INTERNAL_PORT}")


def _create_internal_api_key() -> None:
    """Mint an internal API key for tool calls that require auth (e.g. deals)."""
    import httpx

    try:
        resp = httpx.post(
            f"http://localhost:{_INTERNAL_PORT}/auth/api-keys",
            json={
                "buyer_tier": "preferred_agency",
                "seat_id": "INTERNAL-AGENTCORE",
                "seat_name": "AgentCore Internal",
                "agency_id": "AGY-INTERNAL",
                "agency_name": "AgentCore Runtime",
            },
            timeout=10,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            key = data.get("api_key", data.get("key", ""))
            if key:
                os.environ["INTERNAL_API_KEY"] = key
                logger.info("Internal API key created for tool auth")
            else:
                logger.warning("API key response missing key field")
        else:
            logger.warning("Failed to create internal API key: %d", resp.status_code)
    except Exception as e:  # noqa: BLE001 - non-fatal
        logger.warning("Could not create internal API key (non-fatal): %s", e)
