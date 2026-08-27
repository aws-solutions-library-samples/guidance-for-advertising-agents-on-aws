"""
A2A Authentication Module.

Handles OAuth credential retrieval from AWS Systems Manager Parameter Store
and Cognito token acquisition with in-memory caching for Agent-to-Agent
(A2A) protocol authentication.
"""

import json
import time
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# Pre-expiry buffer in seconds — refresh tokens 60s before they expire
_TOKEN_EXPIRY_BUFFER_SECONDS = 60

# `grant_type` marking a stored document as OAuth 2.0 client-credentials
# ("machine-to-machine") rather than the Cognito username/password document.
# Both shapes live at the same parameter path per auth surface, so this value is
# what tells them apart. Written by the UI — see
# bedrock-adtech-demo/src/app/services/oauth-client-credentials.ts.
OAUTH_M2M_GRANT_TYPE = "client_credentials"

# Applied when a token response omits `expires_in`.
_DEFAULT_TOKEN_LIFETIME_SECONDS = 3600

# Timeout for the token endpoint request.
_TOKEN_REQUEST_TIMEOUT_SECONDS = 30


@dataclass
class CachedToken:
    """A cached bearer token with expiry tracking."""
    token: str
    expires_at: float  # Unix timestamp
    ssm_path: str

    @property
    def is_expired(self) -> bool:
        """Return True if the token is expired or within the pre-expiry buffer."""
        return time.time() >= (self.expires_at - _TOKEN_EXPIRY_BUFFER_SECONDS)


class A2ATokenManager:
    """Manages bearer token acquisition and caching for A2A authentication.

    Retrieves OAuth credentials from SSM Parameter Store, authenticates
    with Cognito using USER_PASSWORD_AUTH, and caches the resulting
    bearer token in memory with TTL-based refresh.
    """

    def __init__(self, region: Optional[str] = None):
        import os
        self._region = region or os.environ.get("AWS_REGION", "us-east-1")
        self._token_cache: Dict[str, CachedToken] = {}
        self._ssm_client = None
        self._cognito_client = None

    @property
    def ssm_client(self):
        """Lazy-initialize the SSM client."""
        if self._ssm_client is None:
            self._ssm_client = boto3.client("ssm", region_name=self._region)
        return self._ssm_client

    @property
    def cognito_client(self):
        """Lazy-initialize the Cognito IDP client."""
        if self._cognito_client is None:
            self._cognito_client = boto3.client(
                "cognito-idp", region_name=self._region
            )
        return self._cognito_client

    def get_bearer_token(
        self, ssm_path: str, pool_id: str = "", client_id: str = ""
    ) -> Tuple[Optional[str], Optional[str]]:
        """Get a valid bearer token, refreshing it when needed.

        Checks the in-memory cache first. If the cached token is still valid,
        returns it immediately. Otherwise fetches the credential document from
        SSM and runs whichever exchange that document describes.

        Two document shapes are supported, both written by the UI at the same
        parameter path for a given auth surface:

        - ``{"grant_type": "client_credentials", "client_id": ...,
          "client_secret": ..., "token_url": ..., "scope"?, "audience"?}`` — an
          OAuth 2.0 client-credentials grant against any provider.
        - ``{"client_id": ..., "username": ..., "password": ...}`` — Cognito
          USER_PASSWORD_AUTH. When the JSON embeds a ``client_id`` it takes
          precedence, so callers don't need to thread the client id separately
          (the ``client_id`` argument is only a fallback).

        Args:
            ssm_path: SSM Parameter Store path containing the credentials JSON.
            pool_id: Cognito User Pool ID (unused by USER_PASSWORD_AUTH; kept
                for backwards compatibility).
            client_id: Fallback Cognito App Client ID if not embedded in the
                stored credentials.

        Returns:
            A tuple of (token, error). On success error is None.
            On failure token is None and error contains a descriptive message.
        """
        # Check cache
        cached = self._token_cache.get(ssm_path)
        if cached is not None and not cached.is_expired:
            logger.debug("🔑 A2A_AUTH: Using cached bearer token")
            return cached.token, None  # noqa: S105

        creds, err = self._fetch_credential_document(ssm_path)
        if err is not None:
            return None, err

        if creds.get("grant_type") == OAUTH_M2M_GRANT_TYPE:
            token, expires_in, err = self._acquire_client_credentials_token(creds)
        else:
            username = creds.get("username")
            password = creds.get("password")
            if not username or not password:
                logger.error("❌ A2A_AUTH: Credentials missing required fields")
                return None, (
                    "A2A authentication failed — credential format invalid at the configured path"
                )

            effective_client_id = (
                creds.get("client_id") or creds.get("clientId") or client_id
            )
            if not effective_client_id:
                return None, (
                    "A2A authentication failed — no Cognito client id available "
                    "(set it in the stored credentials or the agent config)"
                )

            token, expires_in, err = self._acquire_token(
                username, password, pool_id, effective_client_id
            )

        if err is not None:
            return None, err

        # Cache the token
        self._token_cache[ssm_path] = CachedToken(
            token=token,
            expires_at=time.time() + expires_in,
            ssm_path=ssm_path,
        )
        logger.info("✅ A2A_AUTH: Acquired and cached new bearer token")
        return token, None

    def _fetch_credential_document(
        self, ssm_path: str
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Retrieve and parse the credential JSON document from SSM SecureString.

        Args:
            ssm_path: The SSM parameter path storing the credentials.

        Returns:
            A tuple of (document, error). On success error is None. Error
            messages never include any credential value.
        """
        try:
            response = self.ssm_client.get_parameter(
                Name=ssm_path, WithDecryption=True
            )
            creds = json.loads(response["Parameter"]["Value"])
            if not isinstance(creds, dict):
                logger.error("❌ A2A_AUTH: Stored credentials are not a JSON object")
                return None, (
                    "A2A authentication failed — credential format invalid at the configured path"
                )
            return creds, None
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            logger.error(
                "❌ A2A_AUTH: SSM retrieval failed — %s",
                error_code,
            )
            return None, (
                f"A2A authentication failed — could not retrieve credentials from parameter store ({error_code})"
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(
                "❌ A2A_AUTH: Failed to parse credentials — %s",
                type(e).__name__,
            )
            return None, (
                "A2A authentication failed — credential format invalid at the configured path"
            )
        except Exception:
            logger.error(
                "❌ A2A_AUTH: Unexpected error retrieving credentials",
            )
            return None, (
                "A2A authentication failed — unexpected error retrieving credentials"
            )

    def _acquire_client_credentials_token(
        self, creds: Dict[str, Any]
    ) -> Tuple[Optional[str], Optional[int], Optional[str]]:
        """Exchange client credentials for an access token at the token endpoint.

        Credentials are presented as HTTP Basic first (the method Cognito
        requires for clients with a secret), then retried once as
        ``client_secret_post`` body parameters if the endpoint rejects that —
        providers accept one or the other and do not advertise which to an
        unauthenticated caller.

        Args:
            creds: The stored client-credentials document.

        Returns:
            A tuple of (token, expires_in_seconds, error). On success error is
            None. Error messages carry the HTTP status only, never the secret.
        """
        import requests

        client_id = creds.get("client_id")
        client_secret = creds.get("client_secret")
        token_url = creds.get("token_url")
        if not client_id or not client_secret or not token_url:
            logger.error(
                "❌ A2A_AUTH: Client-credentials document missing required fields"
            )
            return None, None, (
                "A2A authentication failed — stored client-credentials document is "
                "missing client_id, client_secret, or token_url"
            )

        body: Dict[str, str] = {"grant_type": OAUTH_M2M_GRANT_TYPE, "client_id": client_id}
        if creds.get("scope"):
            body["scope"] = creds["scope"]
        if creds.get("audience"):
            body["audience"] = creds["audience"]

        def _post(use_basic: bool):
            data = dict(body)
            kwargs: Dict[str, Any] = {}
            if use_basic:
                kwargs["auth"] = (client_id, client_secret)
            else:
                data["client_secret"] = client_secret
            return requests.post(
                token_url,
                data=data,
                headers={"Accept": "application/json"},
                timeout=_TOKEN_REQUEST_TIMEOUT_SECONDS,
                **kwargs,
            )

        try:
            response = _post(use_basic=True)
            if response.status_code in (400, 401):
                response = _post(use_basic=False)
        except Exception:
            # Do not echo the exception text: request exceptions can include the
            # request body, which holds the client secret on the fallback path.
            logger.error("❌ A2A_AUTH: Token endpoint request failed")
            return None, None, (
                "A2A authentication failed — could not reach the OAuth token endpoint"
            )

        if response.status_code >= 400:
            logger.error(
                "❌ A2A_AUTH: Token endpoint returned HTTP %s", response.status_code
            )
            return None, None, (
                f"A2A authentication failed — OAuth token request rejected "
                f"(HTTP {response.status_code})"
            )

        try:
            payload = response.json()
        except ValueError:
            logger.error("❌ A2A_AUTH: Token endpoint returned a non-JSON response")
            return None, None, (
                "A2A authentication failed — OAuth token endpoint returned a non-JSON response"
            )

        access_token = payload.get("access_token")
        if not access_token:
            logger.error("❌ A2A_AUTH: Token response missing access_token")
            return None, None, (
                "A2A authentication failed — OAuth token response contained no access_token"
            )

        expires_in = payload.get("expires_in")
        if not isinstance(expires_in, int) or expires_in <= 0:
            expires_in = _DEFAULT_TOKEN_LIFETIME_SECONDS

        return access_token, expires_in, None

    def _acquire_token(
        self,
        username: str,
        password: str,
        pool_id: str,
        client_id: str,
    ) -> Tuple[Optional[str], Optional[int], Optional[str]]:
        """Authenticate with Cognito and return an access token.

        Uses the USER_PASSWORD_AUTH flow to obtain a bearer token.

        Args:
            username: Cognito username.
            password: Cognito password.
            pool_id: Cognito User Pool ID.
            client_id: Cognito App Client ID.

        Returns:
            A tuple of (token, expires_in_seconds, error). On success error is None.
        """
        try:
            response = self.cognito_client.initiate_auth(
                AuthFlow="USER_PASSWORD_AUTH",
                AuthParameters={
                    "USERNAME": username,
                    "PASSWORD": password,
                },
                ClientId=client_id,
            )
            auth_result = response.get("AuthenticationResult", {})
            access_token = auth_result.get("AccessToken")
            expires_in = auth_result.get("ExpiresIn", 3600)

            if not access_token:
                logger.error("❌ A2A_AUTH: Cognito response missing AccessToken")
                return None, None, (
                    "A2A authentication failed — token acquisition returned empty result"
                )

            return access_token, expires_in, None
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            logger.error(
                "❌ A2A_AUTH: Cognito authentication failed — %s", error_code
            )
            return None, None, (
                f"A2A authentication failed — Cognito auth error ({error_code})"
            )
        except Exception:
            logger.error(
                "❌ A2A_AUTH: Unexpected error during Cognito authentication",
            )
            return None, None, (
                "A2A authentication failed — unexpected error during token acquisition"
            )
