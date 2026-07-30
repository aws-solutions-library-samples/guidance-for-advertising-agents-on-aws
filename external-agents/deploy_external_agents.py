#!/usr/bin/env python3
"""Idempotent deployer for external A2A AgentCore agents.

Reads a declarative config object (``<agent>/agentcore.json``), builds an
ARM64 container, and creates **or updates** an Amazon Bedrock AgentCore
Runtime that serves the A2A protocol. Resource details are persisted to
``.external-agents.json`` (at the repo root) so re-runs are idempotent:
an existing runtime is updated (new version) instead of recreated.

After a successful deploy it rewires the deployed runtime ARN into the
matching ``external_agent_configs`` entry in
``agentcore/deployment/agent/global_configuration.json`` — replacing the
previously hardcoded ARN.

Usage:
    python external-agents/deploy_external_agents.py \
        --agent AdCreationAgent \
        --s3-bucket my-stack-generated-content-abc123 \
        --region us-east-1 [--profile PROFILE]

This uses the bedrock-agentcore-control "config object" APIs
(create_agent_runtime / update_agent_runtime) — the current programmatic
way to deploy AgentCore runtimes.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import secrets
import string
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("deploy_external_agents")

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
STATE_FILE = REPO_ROOT / ".external-agents.json"

# AgentCore requires linux/arm64 images.
DOCKER_PLATFORM = "linux/arm64"
_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,47}$")


def _validate_identifier(value: str, name: str) -> str:
    if not value or not re.match(r"^[a-zA-Z0-9._:/-]+$", value):
        raise ValueError(f"Invalid {name}: {value!r}")
    return value


_DISCOVERY_SUFFIX = "/.well-known/openid-configuration"


def _pool_id_from_discovery_url(url: str) -> str:
    """Extract the Cognito user pool id from an OIDC discovery URL.

    e.g. https://cognito-idp.us-east-1.amazonaws.com/us-east-1_ABC/.well-known/openid-configuration
    -> us-east-1_ABC. Returns "" if the URL doesn't match the expected shape.
    """
    if not url.endswith(_DISCOVERY_SUFFIX):
        return ""
    return url[: -len(_DISCOVERY_SUFFIX)].rsplit("/", 1)[-1]


def _run(cmd: list, **kwargs) -> subprocess.CompletedProcess:
    """Run a subprocess with a hardcoded/validated argv (never shell=True)."""
    logger.info("$ %s", " ".join(cmd))
    return subprocess.run(cmd, check=True, **kwargs)  # noqa: S603


class ExternalAgentDeployer:
    def __init__(self, region: str, profile: Optional[str] = None):
        self.region = _validate_identifier(region, "region")
        session = boto3.Session(profile_name=profile) if profile else boto3.Session()
        self.session = session
        self.agentcore = session.client(
            "bedrock-agentcore-control", region_name=region
        )
        self.ecr = session.client("ecr", region_name=region)
        self.iam = session.client("iam", region_name=region)
        self.sts = session.client("sts", region_name=region)
        self.account_id = self.sts.get_caller_identity()["Account"]

    # -- state (.external-agents.json) ---------------------------------

    @staticmethod
    def _load_state() -> Dict[str, Any]:
        if STATE_FILE.exists():
            try:
                return json.loads(STATE_FILE.read_text())
            except json.JSONDecodeError:
                logger.warning("State file corrupt; starting fresh: %s", STATE_FILE)
        return {"agents": {}}

    @staticmethod
    def _save_state(state: Dict[str, Any]) -> None:
        STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")
        logger.info("Wrote state: %s", STATE_FILE)

    def _state_key(self, agent_name: str) -> str:
        return f"{agent_name}:{self.region}"

    # -- IAM execution role (least privilege, scoped to creative bucket) --

    def ensure_execution_role(
        self,
        agent_name: str,
        s3_bucket: Optional[str] = None,
        dynamodb_table_arns: Optional[List[str]] = None,
    ) -> str:
        role_name = f"AgentCoreExternal-{agent_name}-{self.region}"[:64]
        trust = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "ECRAuth",
                    "Effect": "Allow",
                    "Action": ["ecr:GetAuthorizationToken"],
                    "Resource": "*",
                },
                {
                    "Sid": "ECRPull",
                    "Effect": "Allow",
                    "Action": [
                        "ecr:BatchCheckLayerAvailability",
                        "ecr:GetDownloadUrlForLayer",
                        "ecr:BatchGetImage",
                    ],
                    "Resource": [
                        f"arn:aws:ecr:{self.region}:{self.account_id}:repository/*"
                    ],
                },
                {
                    "Sid": "Logs",
                    "Effect": "Allow",
                    "Action": [
                        "logs:CreateLogGroup",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents",
                    ],
                    "Resource": [f"arn:aws:logs:{self.region}:{self.account_id}:*"],
                },
                {
                    "Sid": "XRay",
                    "Effect": "Allow",
                    "Action": [
                        "xray:PutTraceSegments",
                        "xray:PutTelemetryRecords",
                        "xray:GetSamplingRules",
                        "xray:GetSamplingTargets",
                    ],
                    "Resource": "*",
                },
                {
                    "Sid": "CloudWatchMetrics",
                    "Effect": "Allow",
                    "Action": ["cloudwatch:PutMetricData"],
                    "Resource": "*",
                },
                {
                    "Sid": "WorkloadIdentityToken",
                    "Effect": "Allow",
                    "Action": [
                        "bedrock-agentcore:GetWorkloadAccessToken",
                        "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                        "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                    ],
                    "Resource": [
                        f"arn:aws:bedrock-agentcore:{self.region}:{self.account_id}:workload-identity-directory/default",
                        f"arn:aws:bedrock-agentcore:{self.region}:{self.account_id}:workload-identity-directory/default/workload-identity/*",
                    ],
                },
                {
                    "Sid": "BedrockModelInvoke",
                    "Effect": "Allow",
                    "Action": [
                        "bedrock:InvokeModel",
                        "bedrock:InvokeModelWithResponseStream",
                    ],
                    "Resource": [
                        "arn:aws:bedrock:*::foundation-model/*",
                        f"arn:aws:bedrock:*:{self.account_id}:inference-profile/*",
                    ],
                },
            ],
        }

        # Only grant S3 access when the agent declares an S3 bucket need.
        # Scoped to the single bucket — never a wildcard.
        if s3_bucket:
            policy["Statement"].append(
                {
                    "Sid": "CreativeBucketAccess",
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:PutObject"],
                    "Resource": [f"arn:aws:s3:::{s3_bucket}/*"],
                }
            )

        # DynamoDB access, scoped to the agent's own tables (+ their indexes) —
        # never a wildcard. Only added when the agent declares a dynamodbStore.
        if dynamodb_table_arns:
            resources: List[str] = []
            for arn in dynamodb_table_arns:
                resources.append(arn)
                resources.append(f"{arn}/index/*")
            policy["Statement"].append(
                {
                    "Sid": "AdCPStoreAccess",
                    "Effect": "Allow",
                    "Action": [
                        "dynamodb:GetItem",
                        "dynamodb:PutItem",
                        "dynamodb:UpdateItem",
                        "dynamodb:DeleteItem",
                        "dynamodb:Query",
                        "dynamodb:Scan",
                        "dynamodb:BatchGetItem",
                        "dynamodb:BatchWriteItem",
                    ],
                    "Resource": resources,
                }
            )

        try:
            resp = self.iam.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust),
                Description=f"Execution role for external A2A agent {agent_name}",
            )
            role_arn = resp["Role"]["Arn"]
            logger.info("Created role %s", role_arn)
        except self.iam.exceptions.EntityAlreadyExistsException:
            role_arn = self.iam.get_role(RoleName=role_name)["Role"]["Arn"]
            logger.info("Using existing role %s", role_arn)

        # Inline policy is idempotent (put overwrites).
        self.iam.put_role_policy(
            RoleName=role_name,
            PolicyName=f"{role_name}-inline",
            PolicyDocument=json.dumps(policy),
        )
        return role_arn

    # -- ECR build + push (ARM64) --------------------------------------

    def ensure_ecr_repo(self, repo_name: str) -> str:
        try:
            resp = self.ecr.create_repository(repositoryName=repo_name)
            uri = resp["repository"]["repositoryUri"]
            logger.info("Created ECR repo %s", uri)
        except self.ecr.exceptions.RepositoryAlreadyExistsException:
            uri = self.ecr.describe_repositories(repositoryNames=[repo_name])[
                "repositories"
            ][0]["repositoryUri"]
            logger.info("Using existing ECR repo %s", uri)
        return uri

    # -- DynamoDB tables (for agents that declare a dynamodbStore) ------

    def ensure_dynamodb_tables(
        self, table_prefix: str, suffixes: List[str], unique_id: str = ""
    ) -> List[str]:
        """Create the agent's DynamoDB tables (pk string key, on-demand) if absent.

        Idempotent — existing tables are reused. Table name is
        ``<table_prefix>-<Suffix>[-<unique_id>]``, matching
        adcp.store.DynamoDBStore so the runtime resolves the same names.
        Returns the list of table ARNs.
        """
        import time as _time

        ddb = self.session.client("dynamodb", region_name=self.region)

        def _name(suffix: str) -> str:
            base = f"{table_prefix}-{suffix}"
            return f"{base}-{unique_id}" if unique_id else base

        arns: List[str] = []
        for suffix in suffixes:
            name = _name(suffix)
            try:
                resp = ddb.create_table(
                    TableName=name,
                    AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
                    KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
                    BillingMode="PAY_PER_REQUEST",
                )
                arns.append(resp["TableDescription"]["TableArn"])
                logger.info("Created DynamoDB table %s", name)
            except ddb.exceptions.ResourceInUseException:
                desc = ddb.describe_table(TableName=name)["Table"]
                arns.append(desc["TableArn"])
                logger.info("Using existing DynamoDB table %s", name)

        # Best-effort wait for ACTIVE so first invokes don't race table creation.
        for suffix in suffixes:
            name = _name(suffix)
            for _ in range(30):
                status = ddb.describe_table(TableName=name)["Table"]["TableStatus"]
                if status == "ACTIVE":
                    break
                _time.sleep(1)
        return arns

    def build_and_push(self, code_dir: Path, repo_uri: str, tag: str) -> str:
        registry = repo_uri.split("/")[0]
        # Authenticate Docker to ECR.
        token = self.ecr.get_authorization_token()
        import base64

        user_pass = base64.b64decode(
            token["authorizationData"][0]["authorizationToken"]
        ).decode()
        _, password = user_pass.split(":", 1)
        _run(
            ["docker", "login", "--username", "AWS", "--password-stdin", registry],
            input=password.encode(),
        )

        image = f"{repo_uri}:{tag}"
        # buildx for guaranteed linux/arm64 + push in one step.
        _run(
            [
                "docker",
                "buildx",
                "build",
                "--platform",
                DOCKER_PLATFORM,
                "-t",
                image,
                "--push",
                str(code_dir),
            ]
        )
        logger.info("Pushed image %s", image)
        return image

    # -- inbound OAuth login (Cognito user + SSM SecureString) ----------

    @staticmethod
    def _generate_password(length: int = 20) -> str:
        """Generate a strong random password satisfying Cognito's default policy.

        Guarantees at least one uppercase, lowercase, digit, and symbol so it
        passes a pool policy that requires all character classes, and uses
        ``secrets`` (CSPRNG) rather than ``random``.
        """
        length = max(length, 12)
        alphabet = string.ascii_letters + string.digits
        specials = "!@#$%^&*()-_=+"
        chars = [
            secrets.choice(string.ascii_uppercase),
            secrets.choice(string.ascii_lowercase),
            secrets.choice(string.digits),
            secrets.choice(specials),
        ]
        chars += [secrets.choice(alphabet + specials) for _ in range(length - 4)]
        secrets.SystemRandom().shuffle(chars)
        return "".join(chars)

    def verify_cognito_pool_exists(self, pool_id: str) -> None:
        """Fail fast if the OAuth pool doesn't exist in this region.

        AgentCore validates the JWT authorizer by fetching the pool's OIDC
        discovery document; a stale/deleted pool id yields an opaque
        'Failed to fetch discovery document ... 400' ValidationException on
        create/update. Checking here turns that into a clear, actionable error
        (and protects the credential-provisioning step from the same stale id).
        """
        if not pool_id:
            return
        cognito = self.session.client("cognito-idp", region_name=self.region)
        try:
            cognito.describe_user_pool(UserPoolId=pool_id)
        except cognito.exceptions.ResourceNotFoundException:
            raise RuntimeError(
                f"Cognito user pool '{pool_id}' does not exist in {self.region}. "
                "The OAuth discovery URL / --cognito-pool-id points at a deleted "
                "or wrong pool — AgentCore would fail with 'Failed to fetch "
                "discovery document'. Refresh $A2A_DISCOVERY_URL / $A2A_POOL_ID / "
                "$A2A_CLIENT_ID (or the --cognito-* flags) to the current pool and "
                "re-run."
            )
        except ClientError as e:
            # Don't hard-fail on unexpected errors (e.g. missing describe perms);
            # warn and let the deploy proceed.
            logger.warning(
                "Could not verify Cognito pool %s (%s); proceeding.",
                pool_id,
                e.response.get("Error", {}).get("Code", str(e)),
            )

    @staticmethod
    def _ensure_client_password_auth(cognito, pool_id: str, client_id: str) -> None:
        """Ensure the app client permits USER_PASSWORD_AUTH (idempotent).

        The caller authenticates with USER_PASSWORD_AUTH, which only works if
        the app client's ``ExplicitAuthFlows`` include ALLOW_USER_PASSWORD_AUTH.
        This reads the current client config, and only if the flow is missing
        re-writes the client — preserving every other writable field so the
        shared client is not clobbered. Best-effort: a failure logs a warning
        and lets the deploy continue (the operator can enable it manually).
        """
        try:
            existing = cognito.describe_user_pool_client(
                UserPoolId=pool_id, ClientId=client_id
            )["UserPoolClient"]
        except ClientError as e:
            logger.warning(
                "Could not read app client %s to verify USER_PASSWORD_AUTH (%s); "
                "if A2A login fails, enable ALLOW_USER_PASSWORD_AUTH on it manually.",
                client_id,
                e.response.get("Error", {}).get("Code", str(e)),
            )
            return

        flows = list(existing.get("ExplicitAuthFlows", []))
        if "ALLOW_USER_PASSWORD_AUTH" in flows:
            return  # Already permitted — nothing to change.

        flows.append("ALLOW_USER_PASSWORD_AUTH")
        if "ALLOW_REFRESH_TOKEN_AUTH" not in flows:
            # Cognito requires refresh-token auth alongside explicit flows.
            flows.append("ALLOW_REFRESH_TOKEN_AUTH")

        # UpdateUserPoolClient replaces the client, so re-send every writable
        # field the describe returned to avoid resetting anything.
        writable_keys = (
            "ClientName", "RefreshTokenValidity", "AccessTokenValidity",
            "IdTokenValidity", "TokenValidityUnits", "ReadAttributes",
            "WriteAttributes", "SupportedIdentityProviders", "CallbackURLs",
            "LogoutURLs", "DefaultRedirectURI", "AllowedOAuthFlows",
            "AllowedOAuthScopes", "AllowedOAuthFlowsUserPoolClient",
            "AnalyticsConfiguration", "PreventUserExistenceErrors",
            "EnableTokenRevocation", "EnablePropagateAdditionalUserContextData",
            "AuthSessionValidity",
        )
        kwargs = {
            k: existing[k] for k in writable_keys if k in existing
        }
        kwargs["ExplicitAuthFlows"] = flows
        try:
            cognito.update_user_pool_client(
                UserPoolId=pool_id, ClientId=client_id, **kwargs
            )
            logger.info(
                "Enabled ALLOW_USER_PASSWORD_AUTH on app client %s", client_id
            )
        except ClientError as e:
            logger.warning(
                "Could not enable ALLOW_USER_PASSWORD_AUTH on app client %s (%s); "
                "A2A login will fail until it is enabled manually.",
                client_id,
                e.response.get("Error", {}).get("Code", str(e)),
            )

    def ensure_inbound_cognito_credentials(
        self,
        pool_id: str,
        client_id: str,
        ssm_path: str,
        username: str,
        password: Optional[str] = None,
    ) -> str:
        """Provision the inbound A2A login for this runtime and store it in SSM.

        Creates (idempotently) a Cognito user in ``pool_id`` and gives it a
        **permanent** password so the USER_PASSWORD_AUTH flow used by the
        caller's A2ATokenManager works non-interactively. ``admin_create_user``
        lets Cognito generate (and suppress) the initial temporary password;
        because Cognito never returns that value, the permanent password is
        generated here with a CSPRNG and set explicitly via
        ``admin_set_user_password``. The credential JSON is written to a
        SecureString at ``ssm_path`` using the exact schema the caller reads:
        ``{"client_id": ..., "username": ..., "password": ...}``.

        The password is never logged or returned. Returns ``ssm_path``.
        """
        cognito = self.session.client("cognito-idp", region_name=self.region)
        ssm = self.session.client("ssm", region_name=self.region)

        # The caller uses USER_PASSWORD_AUTH — make sure the app client allows it.
        if client_id:
            self._ensure_client_password_auth(cognito, pool_id, client_id)

        secret = password or self._generate_password()

        # Create the user (idempotent). SUPPRESS avoids sending an invite email;
        # the Cognito-generated temporary password is discarded. email +
        # email_verified are required: without a verified email the
        # USER_PASSWORD_AUTH flow is rejected on pools that use email as the
        # sign-in / verification attribute.
        try:
            cognito.admin_create_user(
                UserPoolId=pool_id,
                Username=username,
                UserAttributes=[
                    {"Name": "email", "Value": username},
                    {"Name": "email_verified", "Value": "true"},
                ],
                MessageAction="SUPPRESS",
            )
            logger.info("Created Cognito user %s in pool %s", username, pool_id)
        except cognito.exceptions.UsernameExistsException:
            logger.info("Cognito user %s already exists; rotating password", username)
            # Ensure email_verified is set on a pre-existing user too.
            try:
                cognito.admin_update_user_attributes(
                    UserPoolId=pool_id,
                    Username=username,
                    UserAttributes=[
                        {"Name": "email", "Value": username},
                        {"Name": "email_verified", "Value": "true"},
                    ],
                )
            except ClientError as e:
                logger.warning(
                    "Could not set email_verified on existing user %s (%s)",
                    username,
                    e.response.get("Error", {}).get("Code", str(e)),
                )

        # Set a PERMANENT password. A temporary password would leave the user in
        # FORCE_CHANGE_PASSWORD and USER_PASSWORD_AUTH would return a challenge
        # instead of tokens.
        cognito.admin_set_user_password(
            UserPoolId=pool_id,
            Username=username,
            Password=secret,
            Permanent=True,
        )

        # Store the credential JSON as a SecureString the caller decrypts.
        creds = {"client_id": client_id, "username": username, "password": secret}
        ssm.put_parameter(
            Name=ssm_path,
            Value=json.dumps(creds),
            Type="SecureString",
            Overwrite=True,
        )
        logger.info(
            "Stored inbound A2A credentials in SSM %s (user %s)", ssm_path, username
        )
        return ssm_path

    # -- AgentCore runtime (config-object create/update) ----------------

    def _find_runtime_id(self, runtime_name: str) -> Optional[str]:
        token = None
        while True:
            kwargs = {"maxResults": 100}
            if token:
                kwargs["nextToken"] = token
            resp = self.agentcore.list_agent_runtimes(**kwargs)
            for rt in resp.get("agentRuntimes", []):
                if rt.get("agentRuntimeName") == runtime_name:
                    return rt.get("agentRuntimeId")
            token = resp.get("nextToken")
            if not token:
                return None

    def deploy_runtime(
        self,
        runtime_name: str,
        image: str,
        role_arn: str,
        env: Dict[str, str],
        existing_id: Optional[str],
        authorizer: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        """Create or update the A2A runtime from a config object.

        When ``authorizer`` is provided, the runtime is configured with an
        inbound Cognito/JWT authorizer (OAuth bearer) instead of the default
        IAM SigV4 inbound auth — so callers in other accounts can invoke it
        with a bearer token without any cross-account IAM trust.
        """
        config = {
            "agentRuntimeArtifact": {
                "containerConfiguration": {"containerUri": image}
            },
            "roleArn": role_arn,
            "networkConfiguration": {"networkMode": "PUBLIC"},
            "protocolConfiguration": {"serverProtocol": "A2A"},
            "environmentVariables": env,
        }
        if authorizer:
            config["authorizerConfiguration"] = authorizer

        if existing_id:
            logger.info("Updating existing runtime %s (new version)", existing_id)
            resp = self.agentcore.update_agent_runtime(
                agentRuntimeId=existing_id, **config
            )
            runtime_id = existing_id
        else:
            logger.info("Creating runtime %s", runtime_name)
            resp = self.agentcore.create_agent_runtime(
                agentRuntimeName=runtime_name, **config
            )
            runtime_id = resp["agentRuntimeId"]

        arn = resp["agentRuntimeArn"]
        version = resp.get("agentRuntimeVersion", "1")
        self._wait_ready(runtime_id)
        return {"agentRuntimeId": runtime_id, "agentRuntimeArn": arn, "version": version}

    def _wait_ready(self, runtime_id: str, timeout_s: int = 600) -> None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            resp = self.agentcore.get_agent_runtime(agentRuntimeId=runtime_id)
            status = resp.get("status", "UNKNOWN")
            if status == "READY":
                logger.info("Runtime READY")
                return
            if status in ("CREATE_FAILED", "UPDATE_FAILED"):
                reason = resp.get("failureReason", "(no reason provided)")
                raise RuntimeError(f"Runtime {status}: {reason}")
            logger.info("Runtime status %s — waiting...", status)
            time.sleep(10)
        raise TimeoutError("Timed out waiting for runtime to become READY")

    # -- teardown (reverse of deploy) ----------------------------------

    def cleanup_agent(
        self,
        agent_name: str,
        state_entry: Dict[str, Any],
        pool_id: str = "",
        keep_ecr: bool = False,
    ) -> None:
        """Delete the AWS resources this deployer creates for one agent.

        Idempotent and best-effort: each resource delete tolerates "already
        gone". Does NOT delete the shared Cognito user pool (only the inbound
        user we created), the DynamoDB store tables (data — handled separately
        and opt-in), or the wired config entries (handled by the unwire_*
        helpers).
        """
        # 1. AgentCore runtime.
        runtime_id = state_entry.get("agentRuntimeId") or self._find_runtime_id(
            agent_name
        )
        if runtime_id:
            try:
                self.agentcore.delete_agent_runtime(agentRuntimeId=runtime_id)
                logger.info("Deleted AgentCore runtime %s (%s)", agent_name, runtime_id)
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code", str(e))
                if "ResourceNotFound" in code:
                    logger.info("Runtime %s already gone", runtime_id)
                else:
                    logger.warning("Could not delete runtime %s (%s)", runtime_id, code)
        else:
            logger.info("No AgentCore runtime found for %s", agent_name)

        # 2. IAM execution role (inline policy first, then the role).
        role_name = f"AgentCoreExternal-{agent_name}-{self.region}"[:64]
        try:
            self.iam.delete_role_policy(
                RoleName=role_name, PolicyName=f"{role_name}-inline"
            )
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code != "NoSuchEntity":
                logger.warning(
                    "Could not delete inline policy on %s (%s)", role_name, code
                )
        try:
            self.iam.delete_role(RoleName=role_name)
            logger.info("Deleted IAM role %s", role_name)
        except self.iam.exceptions.NoSuchEntityException:
            logger.info("IAM role %s already gone", role_name)
        except ClientError as e:
            logger.warning(
                "Could not delete role %s (%s)",
                role_name,
                e.response.get("Error", {}).get("Code", str(e)),
            )

        # 3. ECR repository (holds the agent's images).
        if not keep_ecr:
            repo_name = f"external-agents/{agent_name.lower()}"
            try:
                self.ecr.delete_repository(repositoryName=repo_name, force=True)
                logger.info("Deleted ECR repo %s", repo_name)
            except self.ecr.exceptions.RepositoryNotFoundException:
                logger.info("ECR repo %s already gone", repo_name)
            except ClientError as e:
                logger.warning(
                    "Could not delete ECR repo %s (%s)",
                    repo_name,
                    e.response.get("Error", {}).get("Code", str(e)),
                )

        # 4. Inbound Cognito user + SSM credential (only the ones we created).
        username = state_entry.get("inboundUsername", "")
        if username and pool_id:
            cognito = self.session.client("cognito-idp", region_name=self.region)
            try:
                cognito.admin_delete_user(UserPoolId=pool_id, Username=username)
                logger.info("Deleted Cognito user %s", username)
            except cognito.exceptions.UserNotFoundException:
                logger.info("Cognito user %s already gone", username)
            except ClientError as e:
                logger.warning(
                    "Could not delete Cognito user %s (%s)",
                    username,
                    e.response.get("Error", {}).get("Code", str(e)),
                )
        ssm_path = state_entry.get("inboundCredentialSsmPath", "")
        if ssm_path:
            ssm = self.session.client("ssm", region_name=self.region)
            try:
                ssm.delete_parameter(Name=ssm_path)
                logger.info("Deleted SSM parameter %s", ssm_path)
            except ssm.exceptions.ParameterNotFound:
                logger.info("SSM parameter %s already gone", ssm_path)
            except ClientError as e:
                logger.warning(
                    "Could not delete SSM parameter %s (%s)",
                    ssm_path,
                    e.response.get("Error", {}).get("Code", str(e)),
                )

    def delete_dynamodb_tables(self, table_names: List[str]) -> None:
        """Delete the agent's DynamoDB store tables (DESTRUCTIVE — data loss)."""
        ddb = self.session.client("dynamodb", region_name=self.region)
        for name in table_names:
            try:
                ddb.delete_table(TableName=name)
                logger.info("Deleting DynamoDB table %s", name)
            except ddb.exceptions.ResourceNotFoundException:
                logger.info("DynamoDB table %s already gone", name)
            except ClientError as e:
                logger.warning(
                    "Could not delete table %s (%s)",
                    name,
                    e.response.get("Error", {}).get("Code", str(e)),
                )


def _build_a2a_external_entry(
    entry_id: str,
    name: str,
    arn: str,
    region: str,
    auth_mode: str,
    cognito_pool_id: str,
    cognito_client_id: str,
    oauth_ssm_path: str,
    description: str,
) -> Dict[str, Any]:
    """Build a complete ExternalAgentConfig dict for an A2A runtime.

    Matches the ``ExternalAgentConfig`` schema the app/handler consumes
    (isA2A/enabled/arn/authType/oauthCredentials/awsAuth). Used to give a
    top-level seller agent a self-referential entry so it is directly
    invocable via the handler's build_a2a_client_tools path.
    """
    entry: Dict[str, Any] = {
        "id": entry_id,
        "name": name,
        "arn": arn,
        "isA2A": True,
        "enabled": True,
        "description": description,
        "awsAuth": {"region": region},
    }
    if auth_mode == "oauth":
        entry["authType"] = "oauth"
        if cognito_pool_id:
            entry["cognitoPoolId"] = cognito_pool_id
        if cognito_client_id:
            entry["cognitoClientId"] = cognito_client_id
        entry["oauthCredentials"] = {
            "hasCredentials": bool(oauth_ssm_path),
            "ssmPath": oauth_ssm_path,
        }
    else:
        entry["authType"] = "iam"
        entry["awsAuth"]["service"] = "bedrock-agentcore"
    return entry


def _apply_top_level_agent(
    data: Dict[str, Any],
    agent_id: str,
    display_name: str,
    description: str,
    team_name: str,
    external_name: str,
    arn: str,
    region: str,
    auth_mode: str,
    cognito_pool_id: str,
    cognito_client_id: str,
    oauth_ssm_path: str,
    model_id: str = "global.anthropic.claude-sonnet-5",
) -> bool:
    """Upsert a top-level agent_configs entry for an external A2A agent.

    Registers the seller runtime as its own selectable top-level agent, flagged
    ``is_a2a`` with its ``runtime_arn`` and a self-referential
    external_agent_configs entry that carries the runtime + authentication. The
    handler's build_tools_for_agent auto-adds an invoke tool from that entry, so
    the top-level agent forwards requests to the remote seller runtime.

    Preserves any operator-populated descriptive fields; always (re)writes the
    deploy-owned a2a runtime + auth fields. Returns True.
    """
    agents = data.setdefault("agent_configs", {})
    entry: Dict[str, Any] = dict(agents.get(agent_id, {}))

    # Descriptive fields: keep operator edits if present, else seed sensible ones.
    entry.setdefault("agent_id", agent_id)
    entry.setdefault("agent_name", agent_id)
    entry.setdefault("agent_display_name", display_name)
    entry.setdefault("agent_description", description)
    entry.setdefault("team_name", team_name)
    entry.setdefault("tool_agent_names", [])
    entry.setdefault("external_agents", [])
    entry.setdefault("agent_tools", [])
    entry.setdefault("mcp_servers", [])
    entry.setdefault("knowledge_base", "")
    entry.setdefault("color", "#6842ff")
    entry.setdefault(
        "model_inputs",
        {
            # No "temperature": the models in use deprecated it, and sending it
            # caused the request to be rejected (a default placeholder came back
            # instead of a real completion). The model default is used instead.
            agent_id: {
                "model_id": model_id,
                "max_tokens": 12000,
                "top_p": 0.8,
            }
        },
    )
    entry.setdefault(
        "instructions",
        (
            f"You are a thin proxy for {display_name} ({agent_id}), an external "
            "Agent-to-Agent (A2A) seller agent hosted on Amazon Bedrock "
            "AgentCore. When you receive a request, forward it to the seller "
            "agent using your available invoke tool and return the seller's "
            "response. Rely only on the tool's output — never fabricate seller "
            "data."
        ),
    )

    # Deploy-owned fields — always set to the freshly deployed runtime + auth.
    entry["is_a2a"] = True
    entry["runtime_arn"] = arn
    entry["external_agent_configs"] = [
        _build_a2a_external_entry(
            entry_id=f"{agent_id}-runtime",
            name=external_name,
            arn=arn,
            region=region,
            auth_mode=auth_mode,
            cognito_pool_id=cognito_pool_id,
            cognito_client_id=cognito_client_id,
            oauth_ssm_path=oauth_ssm_path,
            description=description,
        )
    ]

    # Inbound auth — how OTHER agents authenticate when calling THIS agent's A2A
    # endpoint. Mirrors the runtime's actual inbound authorizer (declared as
    # `inboundAuth` in agentcore.json and provisioned by the deployer). The UI's
    # "Inbound Authentication" panel reads these fields (a2a_auth_type +
    # a2a_oauth_credentials); without them a top-level external agent defaults
    # to "None" even though its runtime was deployed with OAuth/IAM. These are
    # deploy-owned, so set them directly rather than preserving stale edits.
    entry["a2a_auth_type"] = auth_mode if auth_mode in ("oauth", "iam") else "none"
    if auth_mode == "oauth":
        # hasCredentials honestly reflects whether the deployer actually
        # provisioned + stored the inbound login in SSM. When it didn't
        # (oauth_ssm_path empty), the panel shows "No credentials stored".
        entry["a2a_oauth_credentials"] = {
            "hasCredentials": bool(oauth_ssm_path),
            "ssmPath": oauth_ssm_path,
        }
    else:
        # Non-oauth runtimes carry no inbound OAuth credentials; drop any stale
        # entry from a prior oauth deployment.
        entry.pop("a2a_oauth_credentials", None)

    agents[agent_id] = entry
    logger.info(
        "Registered top-level a2a agent %s (runtime %s, auth %s)",
        agent_id,
        arn,
        auth_mode,
    )
    return True


def _apply_external_wire(
    data: Dict[str, Any],
    agent_id: str,
    external_name: str,
    arn: str,
    region: str,
    auth_mode: str,
    cognito_pool_id: str,
    cognito_client_id: str,
    oauth_ssm_path: str = "",
    top_level_agent_id: str = "",
    top_level_display_name: str = "",
    top_level_description: str = "",
    top_level_team: str = "External Agents",
    top_level_model_id: str = "global.anthropic.claude-sonnet-5",
) -> bool:
    """Patch one external_agent_configs entry on a global-config dict in place.

    Shared by the file writer (wire_into_global_config) and the DynamoDB
    writer (wire_into_dynamodb) so both apply identical changes. Returns True
    if a change was made (external entry patched and/or top-level agent added).

    When ``top_level_agent_id`` is provided, the external agent is also
    registered as its own top-level a2a agent in the same pass.
    """
    top_changed = False
    if top_level_agent_id:
        top_changed = _apply_top_level_agent(
            data,
            agent_id=top_level_agent_id,
            display_name=top_level_display_name or top_level_agent_id,
            description=(
                top_level_description
                or f"External A2A seller agent ({top_level_agent_id})."
            ),
            team_name=top_level_team,
            external_name=external_name,
            arn=arn,
            region=region,
            auth_mode=auth_mode,
            cognito_pool_id=cognito_pool_id,
            cognito_client_id=cognito_client_id,
            oauth_ssm_path=oauth_ssm_path,
            model_id=top_level_model_id,
        )

    agent = data.get("agent_configs", {}).get(agent_id)
    if not agent:
        logger.warning("Agent %s not found in config; skipping wire-in", agent_id)
        return top_changed

    changed = False
    for entry in agent.get("external_agent_configs", []):
        if entry.get("name") != external_name:
            continue
        entry["arn"] = arn
        entry.setdefault("awsAuth", {})["region"] = region

        if auth_mode == "oauth":
            entry["authType"] = "oauth"
            # Region is the only awsAuth field relevant to OAuth; drop the
            # IAM SigV4 service marker if a prior IAM deploy set it.
            entry["awsAuth"].pop("service", None)
            if cognito_pool_id:
                entry["cognitoPoolId"] = cognito_pool_id
            if cognito_client_id:
                entry["cognitoClientId"] = cognito_client_id
            if oauth_ssm_path:
                # The deployer provisioned the inbound login and stored it in
                # SSM — mark it available and point the caller at the path.
                entry["oauthCredentials"] = {
                    "hasCredentials": True,
                    "ssmPath": oauth_ssm_path,
                }
            else:
                # Preserve an operator-populated ssmPath; otherwise scaffold it.
                entry.setdefault(
                    "oauthCredentials", {"hasCredentials": False, "ssmPath": ""}
                )
        else:
            entry["authType"] = "iam"
            entry["awsAuth"]["service"] = "bedrock-agentcore"
        changed = True

    if not changed:
        logger.warning(
            "External entry %r not found on agent %s; nothing wired",
            external_name,
            agent_id,
        )
    return changed or top_changed


def wire_into_global_config(
    global_config_path: Path,
    agent_id: str,
    external_name: str,
    arn: str,
    region: str,
    auth_mode: str = "oauth",
    cognito_pool_id: str = "",
    cognito_client_id: str = "",
    oauth_ssm_path: str = "",
    top_level_agent_id: str = "",
    top_level_display_name: str = "",
    top_level_description: str = "",
    top_level_team: str = "External Agents",
    top_level_model_id: str = "global.anthropic.claude-sonnet-5",
) -> bool:
    """Set the deployed ARN (and auth) on the matching external_agent_configs entry.

    Returns True if a change was written. Reads + writes surgically to
    avoid reformatting the whole config.

    For ``oauth`` (the default for external agents that may live in another
    account), the entry is marked ``authType: "oauth"`` and the Cognito
    pool/client ids are recorded so the caller's A2ATokenManager can mint a
    bearer token. The inbound-credential ``ssmPath`` is left for the operator
    credential store to populate. For ``iam`` it records the SigV4 service.

    NOTE: This only updates the local source file. The running app reads from
    the DynamoDB AgentConfig table — use wire_into_dynamodb() to register the
    change live.
    """
    data = json.loads(global_config_path.read_text())
    changed = _apply_external_wire(
        data,
        agent_id,
        external_name,
        arn,
        region,
        auth_mode,
        cognito_pool_id,
        cognito_client_id,
        oauth_ssm_path,
        top_level_agent_id=top_level_agent_id,
        top_level_display_name=top_level_display_name,
        top_level_description=top_level_description,
        top_level_team=top_level_team,
        top_level_model_id=top_level_model_id,
    )

    if changed:
        # ensure_ascii=False preserves unicode already present in the config
        # (e.g. characters embedded in agent instructions) to keep the diff small.
        global_config_path.write_text(
            json.dumps(data, indent=4, ensure_ascii=False) + "\n"
        )
        logger.info("Wired ARN into %s (%s)", global_config_path.name, external_name)
    return changed


def wire_into_dynamodb(
    table_name: str,
    region: str,
    profile: Optional[str],
    agent_id: str,
    external_name: str,
    arn: str,
    auth_mode: str = "oauth",
    cognito_pool_id: str = "",
    cognito_client_id: str = "",
    oauth_ssm_path: str = "",
    top_level_agent_id: str = "",
    top_level_display_name: str = "",
    top_level_description: str = "",
    top_level_team: str = "External Agents",
    top_level_model_id: str = "global.anthropic.claude-sonnet-5",
) -> bool:
    """Patch the live GLOBAL_CONFIG/v1 item in the DynamoDB AgentConfig table.

    The running "Agents for Advertising" app loads agent configs from this
    item (``pk=GLOBAL_CONFIG``, ``sk=v1``, ``content``=whole-config JSON), NOT
    from global_configuration.json. We do a surgical read-modify-write of the
    single external_agent_configs entry so we never clobber UI-side runtime
    edits to other agents.

    Returns True if the item was updated. Honestly reports (and skips) when
    the table or item is missing rather than implying registration succeeded.
    """
    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    table = session.resource("dynamodb", region_name=region).Table(table_name)

    try:
        resp = table.get_item(Key={"pk": "GLOBAL_CONFIG", "sk": "v1"})
    except ClientError as e:
        logger.warning(
            "Could not read GLOBAL_CONFIG from table %s (%s); skipping DynamoDB "
            "registration. The live app will not see this agent.",
            table_name,
            e.response.get("Error", {}).get("Code", str(e)),
        )
        return False

    item = resp.get("Item")
    if not item:
        logger.warning(
            "GLOBAL_CONFIG/v1 not found in table %s — the app config has not "
            "been uploaded to DynamoDB yet. Skipping DynamoDB registration.",
            table_name,
        )
        return False

    content = item.get("content", "{}")
    try:
        data = json.loads(content) if isinstance(content, str) else content
    except json.JSONDecodeError as e:
        logger.warning(
            "GLOBAL_CONFIG content in %s is not valid JSON (%s); skipping",
            table_name,
            e,
        )
        return False

    changed = _apply_external_wire(
        data,
        agent_id,
        external_name,
        arn,
        region,
        auth_mode,
        cognito_pool_id,
        cognito_client_id,
        oauth_ssm_path,
        top_level_agent_id=top_level_agent_id,
        top_level_display_name=top_level_display_name,
        top_level_description=top_level_description,
        top_level_team=top_level_team,
        top_level_model_id=top_level_model_id,
    )
    if not changed:
        return False

    table.put_item(
        Item={
            "pk": "GLOBAL_CONFIG",
            "sk": "v1",
            "config_type": "global_config",
            "content": json.dumps(data),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    logger.info(
        "✅ Registered %s into live DynamoDB GLOBAL_CONFIG (table %s)",
        external_name,
        table_name,
    )
    return True


def _remove_external_wire(
    data: Dict[str, Any],
    agent_id: str,
    external_name: str,
    top_level_agent_id: str = "",
) -> bool:
    """Remove the wired external entry and any top-level agent (in place).

    Reverses _apply_external_wire: drops the matching external_agent_configs
    entry from ``agent_id`` and removes the top-level ``top_level_agent_id``
    agent if it was registered. Returns True if anything changed.
    """
    changed = False
    agents = data.get("agent_configs", {})
    agent = agents.get(agent_id)
    if agent:
        ext = agent.get("external_agent_configs", [])
        kept = [e for e in ext if e.get("name") != external_name]
        if len(kept) != len(ext):
            agent["external_agent_configs"] = kept
            changed = True
    if top_level_agent_id and agents.pop(top_level_agent_id, None) is not None:
        changed = True
    return changed


def unwire_from_global_config(
    global_config_path: Path,
    agent_id: str,
    external_name: str,
    top_level_agent_id: str = "",
) -> bool:
    """Remove the wired entry + top-level agent from the local source file."""
    data = json.loads(global_config_path.read_text())
    changed = _remove_external_wire(
        data, agent_id, external_name, top_level_agent_id
    )
    if changed:
        global_config_path.write_text(
            json.dumps(data, indent=4, ensure_ascii=False) + "\n"
        )
        logger.info(
            "Unwired %s from %s", external_name, global_config_path.name
        )
    return changed


def unwire_from_dynamodb(
    table_name: str,
    region: str,
    profile: Optional[str],
    agent_id: str,
    external_name: str,
    top_level_agent_id: str = "",
) -> bool:
    """Remove the wired entry + top-level agent from the live GLOBAL_CONFIG item."""
    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    table = session.resource("dynamodb", region_name=region).Table(table_name)
    try:
        resp = table.get_item(Key={"pk": "GLOBAL_CONFIG", "sk": "v1"})
    except ClientError as e:
        logger.warning(
            "Could not read GLOBAL_CONFIG from table %s (%s); skipping DynamoDB "
            "unwire.",
            table_name,
            e.response.get("Error", {}).get("Code", str(e)),
        )
        return False

    item = resp.get("Item")
    if not item:
        logger.info("GLOBAL_CONFIG/v1 not found in %s; nothing to unwire", table_name)
        return False

    content = item.get("content", "{}")
    try:
        data = json.loads(content) if isinstance(content, str) else content
    except json.JSONDecodeError:
        logger.warning("GLOBAL_CONFIG content in %s is not valid JSON; skipping", table_name)
        return False

    if not _remove_external_wire(data, agent_id, external_name, top_level_agent_id):
        return False

    table.put_item(
        Item={
            "pk": "GLOBAL_CONFIG",
            "sk": "v1",
            "config_type": "global_config",
            "content": json.dumps(data),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    logger.info(
        "Unwired %s from live DynamoDB GLOBAL_CONFIG (table %s)",
        external_name,
        table_name,
    )
    return True


def load_config_object(agent_dir: Path) -> Dict[str, Any]:
    cfg_path = agent_dir / "agentcore.json"
    if not cfg_path.exists():
        raise FileNotFoundError(f"Missing config object: {cfg_path}")
    return json.loads(cfg_path.read_text())


def _discover_all_agents() -> List[tuple]:
    """Return [(agent_name, agent_cfg), ...] for every declared external agent."""
    found: List[tuple] = []
    for candidate in sorted(HERE.glob("*/agentcore.json")):
        try:
            cfg = json.loads(candidate.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for a in cfg.get("agents", []):
            if a.get("name"):
                found.append((a["name"], a))
    return found


def _cleanup_one(deployer: "ExternalAgentDeployer", args, agent_name: str, agent_cfg: dict) -> None:
    """Tear down a single external agent: AWS resources, config wiring, state."""
    logger.info("=== Cleaning up %s ===", agent_name)
    state = deployer._load_state()
    key = deployer._state_key(agent_name)
    state_entry = state["agents"].get(key, {})

    wire = agent_cfg.get("wireInto", {})
    wire_agent_id = wire.get("agentId", agent_name)
    wire_external_name = wire.get("externalConfigName", f"{agent_name}_Runtime")
    tl_id = agent_name if wire.get("registerTopLevel", False) else ""

    # AWS resources (runtime, role, ECR, inbound Cognito user + SSM credential).
    deployer.cleanup_agent(
        agent_name=agent_name,
        state_entry=state_entry,
        pool_id=args.cognito_pool_id,
        keep_ecr=args.keep_ecr,
    )

    # DynamoDB store tables — DESTRUCTIVE, opt-in only.
    store_cfg = agent_cfg.get("dynamodbStore")
    if store_cfg and args.stack_prefix and args.unique_id:
        table_prefix = f"{args.stack_prefix}-{store_cfg.get('prefix', 'AdCPSeller')}"
        table_names = [
            f"{table_prefix}-{suffix}-{args.unique_id}"
            for suffix in store_cfg.get("tables", [])
        ]
        if args.delete_tables:
            deployer.delete_dynamodb_tables(table_names)
        elif table_names:
            logger.info(
                "Keeping DynamoDB store tables (%s). Re-run with --delete-tables "
                "to remove them and their data.",
                ", ".join(table_names),
            )

    # Unwire config entries (local source file + live DynamoDB).
    if not args.skip_wire:
        gpath = REPO_ROOT / wire.get(
            "globalConfigPath", "agentcore/deployment/agent/global_configuration.json"
        )
        if gpath.exists():
            unwire_from_global_config(gpath, wire_agent_id, wire_external_name, tl_id)
    if not args.skip_dynamodb:
        table_name = args.dynamodb_table
        if not table_name and args.stack_prefix and args.unique_id:
            table_name = f"{args.stack_prefix}-AgentConfig-{args.unique_id}"
        if table_name:
            unwire_from_dynamodb(
                table_name,
                args.region,
                args.profile,
                wire_agent_id,
                wire_external_name,
                tl_id,
            )

    # Drop the state entry so a later deploy starts fresh.
    if key in state["agents"]:
        del state["agents"][key]
        deployer._save_state(state)


def run_cleanup(args) -> int:
    """Reverse a deploy for one agent (--agent NAME) or all (--agent all)."""
    deployer = ExternalAgentDeployer(region=args.region, profile=args.profile)

    if args.agent.lower() == "all":
        targets = _discover_all_agents()
        if not targets:
            logger.error("No external agents found to clean up.")
            return 1
    else:
        match = next(
            ((n, c) for n, c in _discover_all_agents() if n == args.agent), None
        )
        if match is None:
            logger.error(
                "Agent '%s' not found in any external-agents/<dir>/agentcore.json",
                args.agent,
            )
            return 1
        targets = [match]

    for agent_name, agent_cfg in targets:
        _cleanup_one(deployer, args, agent_name, agent_cfg)

    logger.info(
        "✅ Cleanup complete for %s. Re-run the deploy to start over.",
        ", ".join(n for n, _ in targets),
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", default="AdCreationAgent", help="Agent dir name")
    parser.add_argument(
        "--s3-bucket",
        default=None,
        help=(
            "Optional S3 bucket the runtime may read/write (e.g. "
            "<stack>-generated-content-<id>). Only needed by agents that use "
            "S3, such as AdCreationAgent. Omit for agents like AdCPSellerAgent."
        ),
    )
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument("--profile", default=None)
    parser.add_argument(
        "--auth",
        choices=["oauth", "iam"],
        default=None,
        help=(
            "Override the inbound auth for the runtime. Defaults to the agent's "
            "'inboundAuth' in agentcore.json. 'oauth' configures a Cognito JWT "
            "authorizer so callers in any account invoke it with a bearer token "
            "(no cross-account IAM trust). 'iam' uses SigV4 (same-account only)."
        ),
    )
    parser.add_argument(
        "--cognito-discovery-url",
        default=os.environ.get("A2A_DISCOVERY_URL", ""),
        help=(
            "Cognito OpenID discovery URL "
            "(.../.well-known/openid-configuration). Defaults to "
            "$A2A_DISCOVERY_URL. Required when --auth oauth."
        ),
    )
    parser.add_argument(
        "--cognito-client-id",
        default=os.environ.get("A2A_CLIENT_ID", ""),
        help="Cognito app client id allowed on the JWT. Defaults to $A2A_CLIENT_ID.",
    )
    parser.add_argument(
        "--cognito-pool-id",
        default=os.environ.get("A2A_POOL_ID", ""),
        help="Cognito user pool id (recorded for the caller). Defaults to $A2A_POOL_ID.",
    )
    parser.add_argument(
        "--a2a-username",
        default=os.environ.get("A2A_INBOUND_USERNAME", ""),
        help=(
            "Username for the inbound A2A Cognito login this runtime accepts. "
            "Defaults to $A2A_INBOUND_USERNAME, else 'a2a-<agent>@example.com'."
        ),
    )
    parser.add_argument(
        "--a2a-password",
        default=os.environ.get("A2A_INBOUND_PASSWORD", ""),
        help=(
            "Optional explicit password for the inbound A2A login. When omitted "
            "(recommended), a strong random password is generated and set as the "
            "user's permanent Cognito password — it is never printed, only "
            "stored in the SecureString SSM parameter."
        ),
    )
    parser.add_argument(
        "--skip-inbound-credentials",
        action="store_true",
        help=(
            "Do not provision the inbound Cognito login / SSM credential for an "
            "oauth runtime (leave it for an operator to populate)."
        ),
    )
    parser.add_argument(
        "--skip-top-level",
        action="store_true",
        help=(
            "Do not register this agent as its own top-level a2a agent, even if "
            "its agentcore.json wireInto.registerTopLevel is true."
        ),
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help=(
            "Tear down instead of deploy: delete the agent's AgentCore runtime, "
            "IAM role, ECR repo, inbound Cognito user + SSM credential, and "
            "unwire its config entries (file + live DynamoDB), then drop its "
            "state so a later deploy starts fresh. Use '--agent all' to tear "
            "down every external agent. DynamoDB store tables are kept unless "
            "--delete-tables is given."
        ),
    )
    parser.add_argument(
        "--keep-ecr",
        action="store_true",
        help="During --cleanup, keep the agent's ECR repository (and its images).",
    )
    parser.add_argument(
        "--delete-tables",
        action="store_true",
        help=(
            "During --cleanup, also delete the agent's DynamoDB store tables. "
            "DESTRUCTIVE — this permanently removes their data."
        ),
    )
    parser.add_argument(
        "--skip-wire",
        action="store_true",
        help="Do not modify global_configuration.json (the local source file)",
    )
    parser.add_argument(
        "--dynamodb-table",
        default=os.environ.get("AGENT_CONFIG_TABLE", ""),
        help=(
            "DynamoDB AgentConfig table the live app reads from. Defaults to "
            "$AGENT_CONFIG_TABLE. If omitted, it is derived from "
            "--stack-prefix/--unique-id as '<prefix>-AgentConfig-<id>'. The "
            "deployer surgically patches the GLOBAL_CONFIG item so the running "
            "app actually picks up this agent."
        ),
    )
    parser.add_argument(
        "--stack-prefix",
        default=os.environ.get("STACK_PREFIX", ""),
        help="Deployment stack prefix (e.g. 'vas'). Defaults to $STACK_PREFIX. "
        "Used with --unique-id to derive the DynamoDB table name.",
    )
    parser.add_argument(
        "--unique-id",
        default=os.environ.get("UNIQUE_ID", ""),
        help="Deployment unique id (e.g. 'nvcu25'). Defaults to $UNIQUE_ID. "
        "Used with --stack-prefix to derive the DynamoDB table name.",
    )
    parser.add_argument(
        "--skip-dynamodb",
        action="store_true",
        help="Do not register the agent into the live DynamoDB AgentConfig table",
    )
    args = parser.parse_args()

    # Teardown path — reverse a deploy instead of creating. Handled before the
    # deploy-only agent resolution so '--agent all' works.
    if args.cleanup:
        return run_cleanup(args)

    # Resolve the agent config. Fast path: a directory named exactly like the
    # agent (dir == agent name == agentcore.json entry). Fallback: scan every
    # external-agents/<dir>/agentcore.json for an entry named args.agent — this
    # lets one code directory declare multiple runtimes (e.g. an A2A and an MCP
    # variant of the same codebase, like AdCPSellerAgent + AdCPSellerAgentMcp)
    # without duplicating code. The build/code dir is the directory that
    # contains the matching agentcore.json.
    agent_dir = HERE / args.agent
    agent_cfg = None
    if agent_dir.is_dir() and (agent_dir / "agentcore.json").exists():
        config = load_config_object(agent_dir)
        agent_cfg = next(
            (a for a in config.get("agents", []) if a.get("name") == args.agent), None
        )
    if agent_cfg is None:
        for candidate in sorted(HERE.glob("*/agentcore.json")):
            try:
                cfg = json.loads(candidate.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            match = next(
                (a for a in cfg.get("agents", []) if a.get("name") == args.agent), None
            )
            if match is not None:
                agent_cfg = match
                agent_dir = candidate.parent
                break
    if agent_cfg is None:
        logger.error(
            "Agent '%s' not found: no external-agents/<dir>/agentcore.json declares it",
            args.agent,
        )
        return 1

    if not _NAME_RE.match(args.agent):
        logger.error("Agent name must match %s", _NAME_RE.pattern)
        return 1
    if args.s3_bucket:
        _validate_identifier(args.s3_bucket, "s3-bucket")

    # Resolve inbound auth: CLI override wins, else the agent's declared
    # 'inboundAuth' in agentcore.json (default 'iam' for backward compatibility
    # with same-account SigV4 agents like AdCreationAgent).
    auth_mode = args.auth or agent_cfg.get("inboundAuth", "iam")

    # Inbound auth. OAuth (Cognito JWT) is the right choice for external agents
    # that may live in another account/org where IAM trust is not available.
    # Fail loudly rather than silently deploying a SigV4-only runtime while
    # claiming OAuth.
    authorizer: Optional[Dict[str, Any]] = None
    if auth_mode == "oauth":
        if not args.cognito_discovery_url or not args.cognito_client_id:
            logger.error(
                "inboundAuth 'oauth' requires a Cognito discovery URL and client "
                "id. Set --cognito-discovery-url/--cognito-client-id or the "
                "$A2A_DISCOVERY_URL/$A2A_CLIENT_ID env vars (as deploy-ecosystem.sh "
                "exports them). Use --auth iam only for same-account dev."
            )
            return 1
        if not args.cognito_discovery_url.endswith("/.well-known/openid-configuration"):
            logger.error(
                "--cognito-discovery-url must end with "
                "'/.well-known/openid-configuration': %s",
                args.cognito_discovery_url,
            )
            return 1
        authorizer = {
            "customJWTAuthorizer": {
                "discoveryUrl": args.cognito_discovery_url,
                "allowedClients": [args.cognito_client_id],
            }
        }
        logger.info(
            "Inbound auth: Cognito JWT (OAuth) — discovery=%s",
            args.cognito_discovery_url,
        )
    else:
        logger.warning(
            "Inbound auth: IAM SigV4. Only same-account callers can invoke this "
            "runtime; cross-account callers would need an IAM resource policy. "
            "Declare inboundAuth 'oauth' in agentcore.json for external agents."
        )

    deployer = ExternalAgentDeployer(region=args.region, profile=args.profile)

    # Preflight: for oauth, confirm the pool referenced by the discovery URL
    # actually exists before AgentCore tries (and fails opaquely) to fetch its
    # discovery document. Catches stale $A2A_* env / --cognito-* values pointing
    # at a deleted or recreated pool.
    if auth_mode == "oauth":
        disc_pool_id = _pool_id_from_discovery_url(args.cognito_discovery_url)
        if (
            disc_pool_id
            and args.cognito_pool_id
            and disc_pool_id != args.cognito_pool_id
        ):
            logger.warning(
                "Discovery URL pool (%s) differs from --cognito-pool-id (%s); "
                "AgentCore uses the discovery URL. Make sure they match.",
                disc_pool_id,
                args.cognito_pool_id,
            )
        deployer.verify_cognito_pool_exists(disc_pool_id or args.cognito_pool_id)

    state = deployer._load_state()
    key = deployer._state_key(args.agent)
    existing = state["agents"].get(key, {})

    # Provision the agent's DynamoDB store if it declares one (e.g. the AdCP
    # Seller Agent). Creates the tables, scopes IAM to them, and injects
    # ADCP_TABLE_PREFIX/UNIQUE_ID so the runtime uses durable DynamoDB instead
    # of the in-process dev store.
    dynamodb_table_arns: List[str] = []
    store_env: Dict[str, str] = {}
    store_cfg = agent_cfg.get("dynamodbStore")
    if store_cfg:
        if not args.stack_prefix or not args.unique_id:
            logger.error(
                "%s declares a dynamodbStore, so --stack-prefix and --unique-id "
                "(or $STACK_PREFIX/$UNIQUE_ID) are required to name its tables.",
                args.agent,
            )
            return 1
        table_prefix = f"{args.stack_prefix}-{store_cfg.get('prefix', 'AdCPSeller')}"
        dynamodb_table_arns = deployer.ensure_dynamodb_tables(
            table_prefix, store_cfg.get("tables", []), args.unique_id
        )
        store_env[store_cfg.get("tablePrefixEnv", "ADCP_TABLE_PREFIX")] = table_prefix
        store_env["UNIQUE_ID"] = args.unique_id

    # Runtime + ECR names (AgentCore names: [a-zA-Z][a-zA-Z0-9_]{0,47}).
    runtime_name = args.agent
    repo_name = f"external-agents/{args.agent.lower()}"

    role_arn = deployer.ensure_execution_role(
        args.agent, args.s3_bucket, dynamodb_table_arns=dynamodb_table_arns
    )
    repo_uri = deployer.ensure_ecr_repo(repo_name)
    tag = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    image = deployer.build_and_push(agent_dir, repo_uri, tag)

    env = dict(agent_cfg.get("environment", {}))
    if args.s3_bucket:
        env["CREATIVE_S3_BUCKET"] = args.s3_bucket
    env["AWS_REGION"] = args.region
    # Mirror the ecosystem A2A convention so the runtime is OAuth-aware.
    if auth_mode == "oauth":
        env["A2A_PROTOCOL"] = "A2A"
        env["A2A_DISCOVERY_URL"] = args.cognito_discovery_url
        env["A2A_CLIENT_ID"] = args.cognito_client_id
        if args.cognito_pool_id:
            env["A2A_POOL_ID"] = args.cognito_pool_id

    # DynamoDB store env (ADCP_TABLE_PREFIX/UNIQUE_ID) for agents that declare one.
    env.update(store_env)

    existing_id = existing.get("agentRuntimeId") or deployer._find_runtime_id(
        runtime_name
    )
    result = deployer.deploy_runtime(
        runtime_name=runtime_name,
        image=image,
        role_arn=role_arn,
        env=env,
        existing_id=existing_id,
        authorizer=authorizer,
    )

    state["agents"][key] = {
        "agentName": args.agent,
        "region": args.region,
        "agentRuntimeId": result["agentRuntimeId"],
        "agentRuntimeArn": result["agentRuntimeArn"],
        "version": result["version"],
        "roleArn": role_arn,
        "ecrImage": image,
        "s3Bucket": args.s3_bucket,
        "protocol": "A2A",
        "inboundAuth": auth_mode,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    deployer._save_state(state)

    # Provision the inbound A2A login (Cognito user + permanent password) and
    # store it in SSM, so the calling agent can mint a bearer token with no
    # manual credential step. Only for oauth runtimes, and only when the SSM
    # path can be named by the repo convention
    # (/{stack_prefix}/a2a-inbound-tokens/{unique_id}/{agent}) — which is the
    # same path the caller's A2ATokenManager falls back to.
    provisioned_ssm_path = ""
    if auth_mode == "oauth" and not args.skip_inbound_credentials:
        if not args.cognito_pool_id:
            logger.warning(
                "Skipping inbound credential provisioning — no Cognito pool id "
                "(set --cognito-pool-id or $A2A_POOL_ID). The caller will have "
                "no stored login and A2A calls to this runtime will fail until "
                "credentials are provisioned."
            )
        elif not (args.stack_prefix and args.unique_id):
            logger.warning(
                "Skipping inbound credential provisioning — --stack-prefix and "
                "--unique-id (or $STACK_PREFIX/$UNIQUE_ID) are required to name "
                "the SSM credential path."
            )
        else:
            inbound_username = (
                args.a2a_username or f"a2a-{args.agent.lower()}@example.com"
            )
            ssm_path = (
                f"/{args.stack_prefix}/a2a-inbound-tokens/"
                f"{args.unique_id}/{args.agent}"
            )
            provisioned_ssm_path = deployer.ensure_inbound_cognito_credentials(
                pool_id=args.cognito_pool_id,
                client_id=args.cognito_client_id,
                ssm_path=ssm_path,
                username=inbound_username,
                password=args.a2a_password or None,
            )
            state["agents"][key]["inboundCredentialSsmPath"] = provisioned_ssm_path
            state["agents"][key]["inboundUsername"] = inbound_username
            deployer._save_state(state)

    wire = agent_cfg.get("wireInto", {})
    wire_agent_id = wire.get("agentId", args.agent)
    wire_external_name = wire.get("externalConfigName", f"{args.agent}_Runtime")

    # Optionally register this external agent as its own top-level a2a agent,
    # with its runtime + authentication already configured. Opt-in per agent via
    # agentcore.json `wireInto.registerTopLevel`, overridable with --skip-top-level.
    register_top_level = bool(wire.get("registerTopLevel", False)) and (
        not args.skip_top_level
    )
    tl_id = args.agent if register_top_level else ""
    tl_display = agent_cfg.get("displayName", args.agent)
    tl_description = agent_cfg.get(
        "description", f"External A2A seller agent ({tl_display})."
    )
    tl_team = wire.get("topLevelTeam", "External Agents")

    if not args.skip_wire:
        gpath = REPO_ROOT / wire.get(
            "globalConfigPath", "agentcore/deployment/agent/global_configuration.json"
        )
        if gpath.exists():
            wire_into_global_config(
                global_config_path=gpath,
                agent_id=wire_agent_id,
                external_name=wire_external_name,
                arn=result["agentRuntimeArn"],
                region=args.region,
                auth_mode=auth_mode,
                cognito_pool_id=args.cognito_pool_id,
                cognito_client_id=args.cognito_client_id,
                oauth_ssm_path=provisioned_ssm_path,
                top_level_agent_id=tl_id,
                top_level_display_name=tl_display,
                top_level_description=tl_description,
                top_level_team=tl_team,
            )
        else:
            logger.warning("global config not found at %s; skipping file wire-in", gpath)

    # CRITICAL: the running app reads agent configs from the DynamoDB
    # AgentConfig table (GLOBAL_CONFIG/v1), NOT from global_configuration.json
    # — that file is only the source the ecosystem deploy copies into DynamoDB.
    # Since this deployer runs separately, we must patch the live item too or
    # the agent stays unregistered in "Agents for Advertising".
    if not args.skip_dynamodb:
        table_name = args.dynamodb_table
        if not table_name and args.stack_prefix and args.unique_id:
            table_name = f"{args.stack_prefix}-AgentConfig-{args.unique_id}"
        if table_name:
            wire_into_dynamodb(
                table_name=table_name,
                region=args.region,
                profile=args.profile,
                agent_id=wire_agent_id,
                external_name=wire_external_name,
                arn=result["agentRuntimeArn"],
                auth_mode=auth_mode,
                cognito_pool_id=args.cognito_pool_id,
                cognito_client_id=args.cognito_client_id,
                oauth_ssm_path=provisioned_ssm_path,
                top_level_agent_id=tl_id,
                top_level_display_name=tl_display,
                top_level_description=tl_description,
                top_level_team=tl_team,
            )
        else:
            logger.warning(
                "DynamoDB AgentConfig table unknown — the live app will NOT see "
                "this agent. Re-run with --dynamodb-table <table> (or "
                "--stack-prefix/--unique-id, or set $AGENT_CONFIG_TABLE / "
                "$STACK_PREFIX+$UNIQUE_ID). Pass --skip-dynamodb to silence this."
            )

    logger.info("✅ Deployed %s", result["agentRuntimeArn"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
