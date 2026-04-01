"""A2A client tool provider construction for Strands agents.

Builds A2AClientToolProvider instances from an agent's
``external_agent_configs`` list, handling OAuth, IAM, and no-auth paths.

Error handling wraps every provider creation in try/except so that a
single misconfigured external agent never prevents the remaining agents
from being registered.  A 120-second timeout is applied to all outbound
HTTP requests.
"""

import logging
import os
from typing import Any, Dict, List

from strands_tools.a2a_client import A2AClientToolProvider
from shared.a2a_auth import A2ATokenManager

logger = logging.getLogger(__name__)

# Timeout in seconds for all outbound A2A HTTP requests (Requirement 9.1)
A2A_REQUEST_TIMEOUT_SECONDS = 120


def _sanitize_error_message(error: Exception) -> str:
    """Return a safe error description that never leaks credentials.

    Strips common credential-bearing fields from the string representation
    of the exception so that passwords, tokens, and secrets are not
    propagated to the calling agent or logs.
    """
    msg = str(error)
    # Remove anything that looks like a bearer token or password value
    for sensitive_keyword in ("password", "secret", "token", "credential", "Bearer"):
        if sensitive_keyword.lower() in msg.lower():
            msg = f"{type(error).__name__}: [details redacted for security]"
            break
    return msg


def build_a2a_client_tools(agent_name: str, agent_config: dict) -> List:
    """Build A2AClientToolProvider instances from external_agent_configs.

    For every entry where ``isA2A`` is True and ``enabled`` is True, creates
    an A2AClientToolProvider with the entry's ARN as the endpoint.

    Authentication is configured per-entry:
    - oauth: retrieves a bearer token via A2ATokenManager.
    - iam: creates the provider without extra auth (SigV4 handled by SDK).
    - none: no authentication headers.

    Error handling (Requirements 9.1–9.5):
    - Each provider creation is wrapped in try/except so one failure does
      not prevent the remaining providers from being built.
    - A 120-second timeout is set on all outbound HTTP requests.
    - Timeout, connection, and auth errors produce descriptive messages
      without exposing credentials.
    - Failed invocations are **not** retried automatically.

    Args:
        agent_name: Name of the owning agent (for logging).
        agent_config: Agent configuration dict with ``external_agent_configs``.

    Returns:
        List of A2AClientToolProvider instances.
    """
    external_configs = agent_config.get("external_agent_configs", [])
    if not external_configs:
        return []

    providers: List = []
    token_manager = None

    for entry in external_configs:
        if not entry.get("isA2A", False) or not entry.get("enabled", False):
            continue

        entry_name = entry.get("name", "unknown")
        arn = entry.get("arn", "")
        if not arn:
            logger.warning(
                "⚠️ A2A_TOOLS: Skipping entry '%s' for %s — missing ARN",
                entry_name,
                agent_name,
            )
            continue

        auth_type = entry.get("authType", "none")

        try:
            # Base httpx client args with 120-second timeout (Req 9.1)
            httpx_args: Dict[str, Any] = {
                "timeout": A2A_REQUEST_TIMEOUT_SECONDS,
            }

            if auth_type == "oauth":
                oauth_creds = entry.get("oauthCredentials", {})
                if oauth_creds.get("hasCredentials") and oauth_creds.get("ssmPath"):
                    if token_manager is None:
                        token_manager = A2ATokenManager()

                    pool_id = entry.get(
                        "cognitoPoolId", os.environ.get("A2A_POOL_ID", "")
                    )
                    client_id = entry.get(
                        "cognitoClientId", os.environ.get("A2A_CLIENT_ID", "")
                    )
                    token, err = token_manager.get_bearer_token(
                        oauth_creds["ssmPath"], pool_id, client_id
                    )
                    if err:
                        # Auth error — log without credential details (Req 9.3, 9.4)
                        logger.error(
                            "❌ A2A_TOOLS: OAuth token acquisition failed for '%s' "
                            "(agent=%s, endpoint=%s): %s",
                            entry_name,
                            agent_name,
                            arn,
                            err,
                        )
                        continue

                    httpx_args["headers"] = {
                        "Authorization": f"Bearer {token}"
                    }
                else:
                    logger.warning(
                        "⚠️ A2A_TOOLS: OAuth configured but no credentials for '%s'",
                        entry_name,
                    )
                    continue

            # Create the provider with timeout-enabled httpx args
            provider = A2AClientToolProvider(
                agent_url=arn,
                httpx_client_args=httpx_args,
            )

            providers.append(provider)
            logger.info(
                "✅ A2A_TOOLS: Created provider for '%s' "
                "(auth=%s) targeting %s",
                entry_name,
                auth_type,
                arn,
            )

        except Exception as e:
            # Catch-all: log at ERROR with agent name and endpoint,
            # but sanitize the message to avoid leaking credentials (Req 9.2, 9.4)
            safe_msg = _sanitize_error_message(e)
            logger.error(
                "❌ A2A_TOOLS: Failed to create provider for '%s' "
                "(agent=%s, endpoint=%s): %s",
                entry_name,
                agent_name,
                arn,
                safe_msg,
            )
            # Continue processing remaining entries — do not fail the whole list

    if providers:
        logger.info(
            "🔗 A2A_TOOLS: Built %d A2A client tool provider(s) for %s",
            len(providers),
            agent_name,
        )

    return providers
