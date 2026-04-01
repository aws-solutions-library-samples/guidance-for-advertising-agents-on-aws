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
from typing import Dict, Optional, Tuple

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# Pre-expiry buffer in seconds — refresh tokens 60s before they expire
_TOKEN_EXPIRY_BUFFER_SECONDS = 60


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
        self, ssm_path: str, pool_id: str, client_id: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """Get a valid bearer token, refreshing from Cognito if needed.

        Checks the in-memory cache first. If the cached token is still valid,
        returns it immediately. Otherwise fetches credentials from SSM and
        acquires a fresh token from Cognito.

        Args:
            ssm_path: SSM Parameter Store path containing the credentials JSON.
            pool_id: Cognito User Pool ID.
            client_id: Cognito App Client ID.

        Returns:
            A tuple of (token, error). On success error is None.
            On failure token is None and error contains a descriptive message.
        """
        # Check cache
        cached = self._token_cache.get(ssm_path)
        if cached is not None and not cached.is_expired:
            logger.debug("🔑 A2A_AUTH: Using cached bearer token for %s", ssm_path)
            return cached.token, None

        # Fetch credentials from SSM
        username, password, err = self._fetch_credentials_from_ssm(ssm_path)
        if err is not None:
            return None, err

        # Acquire token from Cognito
        token, expires_in, err = self._acquire_token(
            username, password, pool_id, client_id
        )
        if err is not None:
            return None, err

        # Cache the token
        self._token_cache[ssm_path] = CachedToken(
            token=token,
            expires_at=time.time() + expires_in,
            ssm_path=ssm_path,
        )
        logger.info("✅ A2A_AUTH: Acquired and cached bearer token for %s", ssm_path)
        return token, None

    def _fetch_credentials_from_ssm(
        self, ssm_path: str
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Retrieve username/password JSON from SSM SecureString.

        Args:
            ssm_path: The SSM parameter path storing the credentials.

        Returns:
            A tuple of (username, password, error). On success error is None.
        """
        try:
            response = self.ssm_client.get_parameter(
                Name=ssm_path, WithDecryption=True
            )
            value = response["Parameter"]["Value"]
            creds = json.loads(value)
            username = creds.get("username")
            password = creds.get("password")
            if not username or not password:
                logger.error(
                    "❌ A2A_AUTH: Credentials at %s missing username or password field",
                    ssm_path,
                )
                return None, None, (
                    "A2A authentication failed — credential format invalid at the configured path"
                )
            return username, password, None
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            logger.error(
                "❌ A2A_AUTH: SSM retrieval failed for %s — %s",
                ssm_path,
                error_code,
            )
            return None, None, (
                f"A2A authentication failed — could not retrieve credentials from parameter store ({error_code})"
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(
                "❌ A2A_AUTH: Failed to parse credentials from %s — %s",
                ssm_path,
                type(e).__name__,
            )
            return None, None, (
                "A2A authentication failed — credential format invalid at the configured path"
            )
        except Exception:
            logger.error(
                "❌ A2A_AUTH: Unexpected error retrieving credentials from %s",
                ssm_path,
            )
            return None, None, (
                "A2A authentication failed — unexpected error retrieving credentials"
            )

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
