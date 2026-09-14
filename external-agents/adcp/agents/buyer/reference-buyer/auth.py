"""
Cognito JWT bearer token verification for the local dev server.

On the deployed AgentCore Runtime, inbound auth is enforced by the platform
itself (see deploy_configure.py's authorizer_configuration) — AWS rejects
unauthenticated invoke_agent_runtime calls before they ever reach this
code. Locally, `python3 app.py` has no such platform-level gate, so this
module gives the local dev server the same real enforcement: it fetches
Cognito's actual JWKS, verifies the token's signature, issuer, audience/
client_id, and expiry, and rejects anything that doesn't check out.

This is not a mock. A request with no token, an expired token, or a token
signed by a different key is genuinely rejected with 401 in both places.
"""

import os
import time
from typing import Any

import jwt
import requests
from jwt import PyJWK

# Cache of {kid: PyJWK} fetched from Cognito's real JWKS endpoint. Fetched
# via `requests` (not urllib/PyJWKClient's default transport) since some
# local Python installs ship without a working default CA bundle for
# urllib specifically; requests+certifi does not have that problem.
_jwks_cache: dict[str, PyJWK] = {}
_jwks_cache_time: float = 0.0
_JWKS_CACHE_TTL_SECONDS = 3600


class AuthError(Exception):
    def __init__(self, message: str, status_code: int = 401):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _fetch_jwks() -> dict[str, PyJWK]:
    global _jwks_cache, _jwks_cache_time
    now = time.time()
    if _jwks_cache and (now - _jwks_cache_time) < _JWKS_CACHE_TTL_SECONDS:
        return _jwks_cache

    region = os.environ["COGNITO_REGION"]
    pool_id = os.environ["COGNITO_USER_POOL_ID"]
    jwks_url = f"https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/jwks.json"

    resp = requests.get(jwks_url, timeout=10)
    resp.raise_for_status()
    keys = resp.json().get("keys", [])

    _jwks_cache = {key["kid"]: PyJWK.from_dict(key) for key in keys}
    _jwks_cache_time = now
    return _jwks_cache


def verify_bearer_token(authorization_header: str | None) -> dict[str, Any]:
    """Verify a Cognito access token from an Authorization header.

    Returns the decoded token claims on success. Raises AuthError (401) on
    any missing header, malformed token, bad signature, wrong issuer/client,
    or expiry.
    """
    if not authorization_header:
        raise AuthError("Missing Authorization header.")

    parts = authorization_header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AuthError("Authorization header must be 'Bearer <token>'.")
    token = parts[1].strip()
    if not token:
        raise AuthError("Empty bearer token.")

    region = os.environ["COGNITO_REGION"]
    pool_id = os.environ["COGNITO_USER_POOL_ID"]
    # More than one app client is legitimate: the browser UI uses a public client with
    # USER_PASSWORD_AUTH, while agent-to-agent calls and the Agent Registry's URL-sync crawler use a
    # confidential client with client_credentials. Both are issued by this pool and both must be
    # accepted, so this is a set. COGNITO_CLIENT_ID stays the single browser client for the sign-in
    # helper below; the extra machine clients are additive and comma-separated.
    #
    # Deliberately an allowlist rather than "any client from this pool": pool membership alone is not
    # authorisation, and a new app client added for some unrelated purpose should not silently gain
    # access to these agents.
    client_id = os.environ["COGNITO_CLIENT_ID"]
    allowed_client_ids = {client_id} | {
        c.strip()
        for c in os.environ.get("COGNITO_ADDITIONAL_CLIENT_IDS", "").split(",")
        if c.strip()
    }
    issuer = f"https://cognito-idp.{region}.amazonaws.com/{pool_id}"

    try:
        header = jwt.get_unverified_header(token)
    except jwt.exceptions.DecodeError as exc:
        raise AuthError(f"Malformed token: {exc}") from exc

    kid = header.get("kid")
    try:
        jwks = _fetch_jwks()
    except requests.RequestException as exc:
        raise AuthError(f"Could not fetch JWKS: {exc}") from exc

    signing_key = jwks.get(kid)
    if signing_key is None:
        raise AuthError(f"No matching signing key for kid={kid!r}.")

    try:
        # Cognito access tokens carry client_id (not aud) for the app
        # client identity, so we validate that claim manually below
        # rather than passing audience= to jwt.decode.
        claims = jwt.decode(
            token,
            key=signing_key.key,
            algorithms=["RS256"],
            issuer=issuer,
            options={"verify_aud": False},
        )
    except jwt.exceptions.ExpiredSignatureError as exc:
        raise AuthError("Token expired.") from exc
    except jwt.exceptions.InvalidTokenError as exc:
        raise AuthError(f"Invalid token: {exc}") from exc

    if claims.get("token_use") != "access":
        raise AuthError("Token is not an access token.")

    if claims.get("client_id") not in allowed_client_ids:
        raise AuthError("Token was not issued for an allowed app client.")

    if claims.get("exp", 0) < time.time():
        raise AuthError("Token expired.")

    return claims


def get_test_user_access_token() -> str:
    """Fetch a fresh access token for the configured test user via Cognito's
    real InitiateAuth API. Used only by verification/test scripts, not by
    the running app.
    """
    region = os.environ["COGNITO_REGION"]
    client_id = os.environ["COGNITO_CLIENT_ID"]
    username = os.environ["COGNITO_TEST_USERNAME"]
    password = os.environ["COGNITO_TEST_USER_PASSWORD"]

    url = f"https://cognito-idp.{region}.amazonaws.com/"
    headers = {
        "Content-Type": "application/x-amz-json-1.1",
        "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
    }
    body = {
        "AuthFlow": "USER_PASSWORD_AUTH",
        "ClientId": client_id,
        "AuthParameters": {"USERNAME": username, "PASSWORD": password},
    }
    resp = requests.post(url, json=body, headers=headers, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return data["AuthenticationResult"]["AccessToken"]


# --- M2M OAuth2 client_credentials (agent-registration auth_type "m2m_oauth") ---

#: In-process cache of minted client_credentials access tokens, keyed by (token_url, client_id, scope,
#: audience). Value is (access_token, expiry_epoch). The client_secret is deliberately NOT part of the
#: key: a rotated secret for the same client is rare, and the worst case is a single stale 401 that the
#: caller re-mints past. Tokens live only here -- never persisted, unlike the client_secret which the
#: registry stores at rest the same way a static bearer token is stored.
_m2m_token_cache: dict[tuple[str, str, str, str], tuple[str, float]] = {}

#: Safety margin before a token's own `exp`, and a HARD CEILING on cache lifetime regardless of how
#: long-lived the token claims to be. The ceiling is the operator's answer to "make it a max timeout,
#: not just 60s": even a provider that issues 24h tokens gets re-minted at least this often, so a
#: revoked client is not honoured out of this cache for a whole day. Override via env if needed.
_M2M_SKEW_SECONDS = 60
_M2M_MAX_CACHE_SECONDS = int(os.environ.get("M2M_TOKEN_MAX_CACHE_SECONDS", "3600"))


def get_m2m_access_token(
    token_url: str,
    client_id: str,
    client_secret: str,
    scope: str | None = None,
    audience: str | None = None,
) -> str:
    """Mint (or reuse a cached) OAuth2 client_credentials access token for an `m2m_oauth` registration.

    Plain HTTP via `requests` -- no SDK (this runtime cannot carry `adcp`, and does not need it here).
    Credentials go in the HTTP Basic header, the client_credentials convention Cognito and most
    providers accept; `scope`/`audience` ride in the form body only when set.

    Cached in-process until ``min(expires_in - skew, M2M_TOKEN_MAX_CACHE_SECONDS)`` -- the token's own
    expiry is honoured, but also capped by a hard ceiling so a long-lived token is still re-minted
    regularly. On expiry (or a downstream 401) the next call re-mints.
    """
    key = (token_url, client_id, scope or "", audience or "")
    now = time.time()
    cached = _m2m_token_cache.get(key)
    if cached is not None and cached[1] > now:
        return cached[0]

    form: dict[str, str] = {"grant_type": "client_credentials"}
    if scope:
        form["scope"] = scope
    if audience:
        form["audience"] = audience
    resp = requests.post(
        token_url,
        data=form,
        auth=(client_id, client_secret),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    access_token = data.get("access_token")
    if not access_token:
        raise AuthError(
            f"client_credentials token endpoint {token_url} returned no access_token "
            f"(response keys: {sorted(data)})."
        )
    lifetime = float(_M2M_MAX_CACHE_SECONDS)
    expires_in = data.get("expires_in")
    if isinstance(expires_in, (int, float)) and expires_in > 0:
        lifetime = min(float(expires_in) - _M2M_SKEW_SECONDS, float(_M2M_MAX_CACHE_SECONDS))
    if lifetime > 0:
        _m2m_token_cache[key] = (access_token, now + lifetime)
    return access_token
