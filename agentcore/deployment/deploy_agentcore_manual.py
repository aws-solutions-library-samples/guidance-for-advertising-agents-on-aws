#!/usr/bin/env python3
"""
Manual AgentCore Deployment Script using boto3
Uses Method B: Manual Deployment with direct boto3 calls instead of starter toolkit
"""

import boto3
import json
import os
import sys
import argparse
import logging
import time
import subprocess
from datetime import datetime
from typing import Dict, Any, List

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ManualAgentCoreDeployer:
    def __init__(
        self,
        region: str = "us-east-1",
        agentcore_region: str = "us-east-1",
        profile: str = None,
    ):
        self.region = region
        self.agentcore_region = agentcore_region or region
        self._profile = profile  # Store profile for CLI usage
        self.session = (
            boto3.Session(profile_name=profile) if profile else boto3.Session()
        )

        # Try to create AgentCore client, upgrade boto3 if needed
        try:
            self.agentcore_client = self.session.client(
                "bedrock-agentcore-control", region_name=agentcore_region
            )
        except KeyError as e:
            # Handle opsworkscm bug in botocore
            if "opsworkscm" in str(e).lower():
                logger.warning(
                    "Detected opsworkscm KeyError bug in botocore - upgrading boto3/botocore..."
                )
                if self._upgrade_boto3():
                    # Reload boto3 after upgrade
                    import importlib

                    importlib.reload(boto3)

                    # Recreate session with upgraded boto3
                    self.session = (
                        boto3.Session(profile_name=profile)
                        if profile
                        else boto3.Session()
                    )

                    # Try again to create the client
                    try:
                        self.agentcore_client = self.session.client(
                            "bedrock-agentcore-control", region_name=agentcore_region
                        )
                        logger.info(
                            "✅ Successfully created AgentCore client after fixing opsworkscm bug"
                        )
                    except Exception as retry_error:
                        logger.error(
                            f"Failed to create AgentCore client even after upgrade: {retry_error}"
                        )
                        raise
                else:
                    logger.error("Failed to upgrade boto3 automatically")
                    raise
            else:
                raise
        except Exception as e:
            if "Unknown service" in str(e):
                logger.warning(
                    "bedrock-agentcore-control service not available in current boto3 version"
                )
                logger.info("Attempting to upgrade boto3 and botocore automatically...")

                if self._upgrade_boto3():
                    # Reload boto3 after upgrade
                    import importlib

                    importlib.reload(boto3)

                    # Recreate session with upgraded boto3
                    self.session = (
                        boto3.Session(profile_name=profile)
                        if profile
                        else boto3.Session()
                    )

                    # Try again to create the client
                    try:
                        self.agentcore_client = self.session.client(
                            "bedrock-agentcore-control", region_name=agentcore_region
                        )
                        logger.info(
                            "✅ Successfully created AgentCore client after upgrade"
                        )
                    except Exception as retry_error:
                        logger.error(
                            f"Failed to create AgentCore client even after upgrade: {retry_error}"
                        )
                        raise
                else:
                    logger.error("Failed to upgrade boto3 automatically")
                    raise
            else:
                raise

        self.ecr_client = self.session.client("ecr", region_name=region)
        self.iam_client = self.session.client("iam", region_name=region)
        self.sts_client = self.session.client("sts", region_name=region)

    @staticmethod
    def _validate_aws_profile(profile: str) -> str:
        """Validate AWS profile name to prevent command injection"""
        if not profile:
            raise ValueError("Profile name cannot be empty")

        # Only allow alphanumeric, hyphens, underscores, and dots
        import re

        if not re.match(r"^[a-zA-Z0-9._-]+$", profile):
            raise ValueError(f"Invalid AWS profile name: {profile}")

        return profile

    @staticmethod
    def _validate_aws_identifier(value: str, name: str) -> str:
        """Validate AWS identifiers to prevent command injection"""
        if not value:
            raise ValueError(f"{name} cannot be empty")
        import re
        # AWS identifiers: alphanumeric, hyphens, underscores, colons (for ARNs), slashes, dots
        if not re.match(r'^[a-zA-Z0-9._:/-]+$', value):
            raise ValueError(f"Invalid {name}: {value}")
        return value

    def _upgrade_boto3(self) -> bool:
        """Automatically upgrade boto3, botocore, and awscli to latest versions"""
        try:
            import subprocess
            import sys

            logger.info("Upgrading boto3, botocore, and awscli...")

            # Security: Validate sys.executable before using in subprocess
            if not sys.executable or not os.path.isfile(sys.executable):
                raise ValueError("Invalid Python executable path")

            # Security: Use only hardcoded, validated package names
            upgrade_cmd = [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--upgrade",
                "--quiet",
                "boto3",
                "botocore",
                "awscli",
            ]

            # nosemgrep: dangerous-subprocess-use-audit - Hardcoded pip upgrade
            result = subprocess.run(
                upgrade_cmd,
                capture_output=True,
                text=True,
                timeout=120,  # 2 minute timeout
            )

            if result.returncode == 0:
                logger.info("✅ Successfully upgraded boto3, botocore, and awscli")
                return True
            else:
                logger.error(f"Failed to upgrade boto3: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            logger.error("Upgrade timed out after 2 minutes")
            return False
        except Exception as e:
            logger.error(f"Error during boto3 upgrade: {e}")
            return False

    @staticmethod
    def _check_and_upgrade_aws_cli():
        """Check if AWS CLI supports bedrock-agentcore-control and upgrade if needed"""
        try:
            import subprocess

            logger.info("Checking AWS CLI support for bedrock-agentcore-control...")

            # Test if bedrock-agentcore-control is available
            test_cmd = ["aws", "bedrock-agentcore-control", "help"]
            # nosemgrep: dangerous-subprocess-use-audit - Hardcoded brew upgrade
            result = subprocess.run(
                test_cmd, capture_output=True, text=True, timeout=10
            )

            if result.returncode == 0:
                logger.info("✅ AWS CLI supports bedrock-agentcore-control")
                return True

            # Check if error indicates invalid service
            if "Invalid choice" in result.stderr or "usage: aws" in result.stderr:
                logger.warning(
                    "AWS CLI does not support bedrock-agentcore-control service"
                )
                logger.info("Attempting to upgrade AWS CLI...")

                # Try to upgrade AWS CLI
                # On macOS with Homebrew
                if (
                    subprocess.run(
                        ["which", "brew"], capture_output=True
                    ).returncode  # nosemgrep: dangerous-subprocess-use-audit
                    == 0
                ):
                    logger.info("Detected Homebrew, upgrading AWS CLI via brew...")
                    upgrade_result = (
                        subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
                            ["brew", "upgrade", "awscli"],
                            capture_output=True,
                            text=True,
                            timeout=300,  # 5 minutes
                        )
                    )
                    if upgrade_result.returncode == 0:
                        logger.info("✅ Successfully upgraded AWS CLI via Homebrew")
                        # Verify the upgrade worked
                        # nosemgrep: dangerous-subprocess-use-audit - Hardcoded pip3 upgrade
                        verify_result = subprocess.run(
                            test_cmd, capture_output=True, text=True, timeout=10
                        )
                        if verify_result.returncode == 0:
                            logger.info(
                                "✅ Verified: AWS CLI now supports bedrock-agentcore-control"
                            )
                            return True
                        else:
                            logger.warning(
                                "AWS CLI upgraded but still doesn't support bedrock-agentcore-control"
                            )
                    else:
                        logger.warning(
                            f"Homebrew upgrade had issues: {upgrade_result.stderr}"
                        )

                # Try pip upgrade as fallback
                logger.info("Attempting to upgrade AWS CLI via pip...")
                pip_upgrade = (
                    subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
                        ["pip3", "install", "--upgrade", "awscli"],
                        capture_output=True,
                        text=True,
                        timeout=300,
                    )
                )

                if pip_upgrade.returncode == 0:
                    logger.info("✅ Successfully upgraded AWS CLI via pip")
                    # Verify the upgrade worked
                    # nosemgrep: dangerous-subprocess-use-audit - Validated AWS CLI test
                    verify_result = subprocess.run(
                        test_cmd, capture_output=True, text=True, timeout=10
                    )
                    if verify_result.returncode == 0:
                        logger.info(
                            "✅ Verified: AWS CLI now supports bedrock-agentcore-control"
                        )
                        return True
                    else:
                        logger.warning(
                            "AWS CLI upgraded but still doesn't support bedrock-agentcore-control"
                        )
                        return False
                else:
                    logger.error(
                        f"Failed to upgrade AWS CLI via pip: {pip_upgrade.stderr}"
                    )
                    logger.error("Please upgrade AWS CLI manually:")
                    logger.error("  macOS: brew upgrade awscli")
                    logger.error("  Linux: pip3 install --upgrade awscli")
                    logger.error("  Or download from: https://aws.amazon.com/cli/")
                    return False

            return True

        except subprocess.TimeoutExpired:
            logger.error("AWS CLI check timed out")
            return False
        except Exception as e:
            logger.warning(f"Could not check/upgrade AWS CLI: {e}")
            return True  # Continue anyway

    def get_account_id(self) -> str:
        """Get AWS account ID"""
        return self.sts_client.get_caller_identity()["Account"]

    def gather_knowledge_base_ids(self, stack_prefix: str, unique_id: str) -> str:
        """
        Gather knowledge base IDs that match the stack prefix and unique ID pattern

        Args:
            stack_prefix: Stack prefix (e.g., 'sim', 'demo3')
            unique_id: Unique identifier for the stack

        Returns:
            str: Comma-separated list of "name:ID" pairs
        """
        try:
            bedrock_agent_client = self.session.client(
                "bedrock-agent", region_name=self.region
            )

            logger.info(
                f"Gathering knowledge bases for stack: {stack_prefix}-*-{unique_id}"
            )

            # List all knowledge bases
            response = bedrock_agent_client.list_knowledge_bases(maxResults=100)
            knowledge_bases = response.get("knowledgeBaseSummaries", [])

            matching_kbs = []
            expected_pattern = f"{stack_prefix}-"
            expected_suffix = f"-{unique_id}"

            for kb in knowledge_bases:
                kb_id = kb.get("knowledgeBaseId")
                kb_name = kb.get("name", "")

                if kb_id and kb_name:
                    # Check if KB name matches our stack pattern
                    if kb_name.startswith(expected_pattern) and kb_name.endswith(
                        expected_suffix
                    ):
                        # Extract the middle part as the KB type name
                        kb_type = kb_name[len(expected_pattern) : -len(expected_suffix)]
                        matching_kbs.append(f"{kb_type}:{kb_id}")
                        logger.info(
                            f"Found matching KB: {kb_name} -> {kb_type}:{kb_id}"
                        )

            kb_env_value = ",".join(matching_kbs)
            logger.info(f"Knowledge bases environment value: {kb_env_value}")
            return kb_env_value

        except Exception as e:
            logger.error(f"Error gathering knowledge base IDs: {e}")
            return ""

    def gather_runtime_arns(self, stack_prefix: str, unique_id: str) -> str:
        """
        Gather runtime ARNs that match the stack prefix and unique ID pattern.
        
        Runtime names follow the pattern: {stack_prefix}_{AgentName}_{unique_id}[-suffix]
        For example: xia_AdFabricAgent_4208ab or xia_AdFabricAgent_4208ab-ifbXlwC83R

        Args:
            stack_prefix: Stack prefix (e.g., 'sim', 'demo3', 'xia')
            unique_id: Unique identifier for the stack (e.g., '4208ab')

        Returns:
            str: Comma-separated list of runtime ARNs
        """
        try:
            logger.info(
                f"Gathering runtime ARNs for stack: {stack_prefix}_*_{unique_id}"
            )

            # List all agent runtimes
            response = self.agentcore_client.list_agent_runtimes(maxResults=100)
            runtimes = response.get("agentRuntimes", [])

            matching_runtime_arns = []
            # Runtime names use underscores: stack_prefix_AgentName_uniqueid
            expected_prefix = f"{stack_prefix.lower()}_"
            expected_unique_id = unique_id.lower()

            for runtime in runtimes:
                runtime_name = runtime.get("agentRuntimeName", "")
                runtime_name_lower = runtime_name.lower()
                runtime_arn = runtime.get("agentRuntimeArn", "")

                if runtime_arn:
                    # STRICT matching: runtime name must START with stack_prefix_ 
                    # AND contain the unique_id (which appears after the agent name)
                    # This prevents matching "bba_AdFabricAgent_4208ab" when looking for "xia_*_4208ab"
                    if runtime_name_lower.startswith(expected_prefix) and expected_unique_id in runtime_name_lower:
                        matching_runtime_arns.append(runtime_arn)
                        logger.info(
                            f"Found matching runtime: {runtime_name} -> {runtime_arn}"
                        )
                    else:
                        logger.debug(
                            f"Skipping runtime '{runtime_name}' - does not match pattern '{expected_prefix}*_{expected_unique_id}'"
                        )

            runtime_env_value = ",".join(matching_runtime_arns)
            logger.info(f"Runtime ARNs environment value: {runtime_env_value}")
            return runtime_env_value

        except Exception as e:
            logger.error(f"Error gathering runtime ARNs: {e}")
            return ""

    def gather_knowledge_base_ids(self, stack_prefix: str, unique_id: str) -> str:
        """
        Gather knowledge base IDs that match the stack prefix and unique ID pattern

        Args:
            stack_prefix: Stack prefix (e.g., 'sim', 'demo3')
            unique_id: Unique identifier for the stack

        Returns:
            str: Comma-separated list of "name:ID" pairs
        """
        try:
            bedrock_agent_client = self.session.client(
                "bedrock-agent", region_name=self.region
            )

            logger.info(
                f"Gathering knowledge bases for stack: {stack_prefix}-*-{unique_id}"
            )

            # List all knowledge bases
            response = bedrock_agent_client.list_knowledge_bases(maxResults=100)
            knowledge_bases = response.get("knowledgeBaseSummaries", [])

            matching_kbs = []
            expected_pattern = f"{stack_prefix}-"
            expected_suffix = f"-{unique_id}"

            for kb in knowledge_bases:
                kb_id = kb.get("knowledgeBaseId")
                kb_name = kb.get("name", "")

                if kb_id and kb_name:
                    # Check if KB name matches our stack pattern
                    if kb_name.startswith(expected_pattern) and kb_name.endswith(
                        expected_suffix
                    ):
                        # Extract the middle part as the KB type name
                        kb_type = kb_name[len(expected_pattern) : -len(expected_suffix)]
                        matching_kbs.append(f"{kb_type}:{kb_id}")
                        logger.info(
                            f"Found matching KB: {kb_name} -> {kb_type}:{kb_id}"
                        )

            kb_env_value = ",".join(matching_kbs)
            logger.info(f"Knowledge bases environment value: {kb_env_value}")
            return kb_env_value

        except Exception as e:
            logger.error(f"Error gathering knowledge base IDs: {e}")
            return ""

    def create_ecr_repository(self, repo_name: str) -> str:
        """Create ECR repository for AgentCore agent"""
        try:
            response = self.ecr_client.create_repository(repositoryName=repo_name)
            repo_uri = response["repository"]["repositoryUri"]
            logger.info(f"Created ECR repository: {repo_uri}")
            return repo_uri
        except self.ecr_client.exceptions.RepositoryAlreadyExistsException:
            response = self.ecr_client.describe_repositories(
                repositoryNames=[repo_name]
            )
            repo_uri = response["repositories"][0]["repositoryUri"]
            logger.info(f"Using existing ECR repository: {repo_uri}")
            return repo_uri

    def create_agent_runtime_role(self, stack_prefix: str, role_name: str) -> str:
        """Create IAM role for AgentCore agent runtime"""
        account_id = self.get_account_id()

        trust_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }

        # Comprehensive permissions policy for AgentCore runtime
        permissions_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "PassRoleAccess",
                    "Effect": "Allow",
                    "Action": ["iam:PassRole"],
                    "Resource": f"arn:aws:iam::{account_id}:role/*",
                },
                {
                    "Sid": "SpecificNamespaceAccess",
                    "Effect": "Allow",
                    "Action": [
                        "bedrock-agentcore:RetrieveMemoryRecords",
                        "bedrock-agentcore:ListMemoryRecords",
                        "bedrock-agentcore:CreateEvent",
                        "s3:GetObject",
                        "s3:ListBucket",
                        "s3:PutObject",
                    ],
                    "Resource": [
                        f"arn:aws:bedrock-agentcore:{self.agentcore_region}:{account_id}:memory/*",
                        f"arn:aws:s3:::{stack_prefix}-data-*",
                        f"arn:aws:s3:::{stack_prefix}-data-*/*",
                        f"arn:aws:s3:::{stack_prefix}-generated-content-*",
                        f"arn:aws:s3:::{stack_prefix}-generated-content-*/*",
                    ],
                },
                {
                    "Sid": "AllowAgentCoreMemoryKMS",
                    "Effect": "Allow",
                    "Action": [
                        "kms:DescribeKey",
                        "kms:CreateGrant",
                        "kms:Decrypt",
                        "kms:GenerateDataKey",
                    ],
                    "Resource": "arn:aws:kms:*:*:key/*",
                    "Condition": {
                        "StringEquals": {
                            "kms:ViaService": f"bedrock-agentcore.{self.agentcore_region}.amazonaws.com"
                        }
                    },
                },
                {
                    "Sid": "ECRAuthToken",
                    "Effect": "Allow",
                    "Action": ["ecr:GetAuthorizationToken","ecr:BatchCheckLayerAvailability",
                        "ecr:GetDownloadUrlForLayer",
                        "ecr:BatchGetImage","ecr:DescribeRepositories",
                        "ecr:DescribeImages",
                        "ecr:ListImages"],
                    "Resource": "*",
                },
                {
                    "Sid": "ECRRepositoryAccess",
                    "Effect": "Allow",
                    "Action": [
                        "ecr:BatchCheckLayerAvailability",
                        "ecr:GetDownloadUrlForLayer",
                        "ecr:BatchGetImage",
                        "ecr:DescribeRepositories",
                        "ecr:DescribeImages",
                        "ecr:ListImages",
                    ],
                    "Resource": [f"arn:aws:ecr:*:{account_id}:repository/*"],
                },
                {
                    "Sid": "GenerateImagesLambda",
                    "Effect": "Allow",
                    "Action": ["lambda:InvokeFunction"],
                    "Resource": [
                        f"arn:aws:lambda:{self.region}:{account_id}:function:{stack_prefix}-CreativeImageGenerator-*"
                    ],
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "logs:CreateLogGroup",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents",
                        "logs:DescribeLogGroups",
                        "logs:DescribeLogStreams",
                    ],
                    "Resource": [f"arn:aws:logs:{self.region}:{account_id}:*"],
                },
                {
                    "Sid": "XRayTracing",
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
                    "Effect": "Allow",
                    "Resource": [f"arn:aws:cloudwatch:*:{account_id}:*:*"],
                    "Action": ["cloudwatch:PutMetricData"],
                },
                {
                    "Sid": "GetAgentAccessToken",
                    "Effect": "Allow",
                    "Action": [
                        "bedrock-agentcore:GetWorkloadAccessToken",
                        "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                        "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                    ],
                    "Resource": [
                        f"arn:aws:bedrock-agentcore:{self.region}:{account_id}:workload-identity-directory/default",
                        f"arn:aws:bedrock-agentcore:{self.region}:{account_id}:workload-identity-directory/default/workload-identity/*",
                    ],
                },
                {
                    "Sid": "BedrockModelInvocation",
                    "Effect": "Allow",
                    "Action": [
                        "bedrock:InvokeAgent",
                        "bedrock:InvokeModel",
                        "bedrock:InvokeModelWithResponseStream",
                        "bedrock:ApplyGuardrail",
                        "bedrock:Retrieve",
                        "bedrock:RetrieveAndGenerate",
                        "bedrock:ListFoundationModels",
                        "bedrock:ListKnowledgeBases",
                        "bedrock:ListDataSources",
                    ],
                    "Resource": ["arn:aws:bedrock:*::*/*", "arn:aws:bedrock:*:*:*",f"arn:aws:bedrock:*:{account_id}:inference-profile/*"],
                },
                {
                    "Sid": "AgentCoreAccess",
                    "Effect": "Allow",
                    "Action": [
                        "bedrock-agentcore:*Gateway*",
                        "bedrock-agentcore:*WorkloadIdentity",
                        "bedrock-agentcore:*CredentialProvider",
                        "bedrock-agentcore:*Token*",
                        "bedrock-agentcore:*Access*",
                    ],
                    "Resource": f"arn:aws:bedrock-agentcore:*:{account_id}:*gateway*",
                },
                {
                    "Sid": "AgentCoreGatewayInvoke",
                    "Effect": "Allow",
                    "Action": [
                        "bedrock-agentcore:InvokeGateway",
                        "bedrock-agentcore:GetGateway",
                        "bedrock-agentcore:ListGateways",
                        "bedrock-agentcore:ListGatewayTargets",
                        "bedrock-agentcore:GetGatewayTarget",
                    ],
                    "Resource": f"arn:aws:bedrock-agentcore:*:{account_id}:*",
                },
                {
                    "Sid": "AgentCoreMemory",
                    "Effect": "Allow",
                    "Action": [
                        "bedrock-agentcore:List*",
                        "bedrock-agentcore:Create*",
                        "bedrock-agentcore:Delete*",
                        "bedrock-agentcore:Update*",
                        "bedrock-agentcore:Start*",
                        "bedrock-agentcore:Stop*"
                    ],
                    "Resource": f"arn:aws:bedrock-agentcore:*:{account_id}:*memory*",
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "dynamodb:GetItem",
                        "dynamodb:PutItem",
                        "dynamodb:UpdateItem",
                        "dynamodb:DeleteItem",
                        "dynamodb:Query",
                        "dynamodb:Scan",
                        "dynamodb:DescribeTable",
                    ],
                    "Resource": f"arn:aws:dynamodb:{self.region}:{account_id}:table/*",
                },
                {
                    "Sid": "SSMParameterAccess",
                    "Effect": "Allow",
                    "Action": [
                        "ssm:GetParameter",
                        "ssm:GetParameters",
                        "ssm:GetParametersByPath",
                        "ssm:PutParameter",
                        "ssm:DeleteParameter",
                    ],
                    "Resource": [
                        f"arn:aws:ssm:{self.region}:{account_id}:parameter/{stack_prefix}/*",
                    ],
                },
            ],
        }

        try:
            response = self.iam_client.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description="IAM role for AgentCore agent runtime",
            )
            role_arn = response["Role"]["Arn"]
            logger.info(f"Created IAM role: {role_arn}")

            # Create and attach permissions policy
            policy_name = f"{role_name}-Policy"
            try:
                self.iam_client.create_policy(
                    PolicyName=policy_name,
                    PolicyDocument=json.dumps(permissions_policy),
                    Description="Permissions for AgentCore runtime",
                )
                logger.info(f"Created policy: {policy_name}")
            except self.iam_client.exceptions.EntityAlreadyExistsException:
                logger.info(f"Policy already exists: {policy_name}")

            policy_arn = f"arn:aws:iam::{account_id}:policy/{policy_name}"

            # Attach the policy
            self.iam_client.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
            logger.info(f"Attached policy to role: {role_name}")

            # Attach AWS managed X-Ray policy for OpenTelemetry tracing
            try:
                self.iam_client.attach_role_policy(
                    RoleName=role_name,
                    PolicyArn="arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess",
                )
                logger.info(
                    f"Attached AWSXRayDaemonWriteAccess policy to role: {role_name}"
                )
            except Exception as e:
                logger.warning(
                    f"Could not attach X-Ray policy (may already be attached): {e}"
                )

            # Wait for IAM policy propagation
            logger.info("Waiting 30 seconds for IAM policy propagation...")
            # nosemgrep: arbitrary-sleep - Required for IAM policy propagation
            time.sleep(30)

            return role_arn
        except self.iam_client.exceptions.EntityAlreadyExistsException:
            response = self.iam_client.get_role(RoleName=role_name)
            role_arn = response["Role"]["Arn"]
            logger.info(f"Using existing IAM role: {role_arn}")

            # Ensure policy exists and is attached to existing role
            try:
                account_id = self.get_account_id()
                policy_name = f"{role_name}-Policy"
                policy_arn = f"arn:aws:iam::{account_id}:policy/{policy_name}"

                # Create policy if it doesn't exist, or update if it does
                try:
                    self.iam_client.create_policy(
                        PolicyName=policy_name,
                        PolicyDocument=json.dumps(permissions_policy),
                        Description="Permissions for AgentCore runtime",
                    )
                    logger.info(f"Created policy for existing role: {policy_name}")
                except self.iam_client.exceptions.EntityAlreadyExistsException:
                    logger.info(f"Policy already exists: {policy_name}, updating with latest permissions...")
                    # Update the existing policy with a new version
                    try:
                        # List policy versions and delete old ones if at limit (max 5 versions)
                        versions_response = self.iam_client.list_policy_versions(PolicyArn=policy_arn)
                        versions = versions_response.get("Versions", [])
                        non_default_versions = [v for v in versions if not v.get("IsDefaultVersion", False)]
                        
                        # Delete oldest non-default versions if we're at the limit
                        if len(versions) >= 5:
                            # Sort by create date and delete oldest non-default
                            non_default_versions.sort(key=lambda x: x.get("CreateDate", ""))
                            for old_version in non_default_versions[:len(versions) - 4]:
                                self.iam_client.delete_policy_version(
                                    PolicyArn=policy_arn,
                                    VersionId=old_version["VersionId"]
                                )
                                logger.info(f"Deleted old policy version: {old_version['VersionId']}")
                        
                        # Create new policy version and set as default
                        self.iam_client.create_policy_version(
                            PolicyArn=policy_arn,
                            PolicyDocument=json.dumps(permissions_policy),
                            SetAsDefault=True
                        )
                        logger.info(f"Updated policy with new version: {policy_name}")
                    except Exception as update_error:
                        logger.warning(f"Could not update policy version: {update_error}")

                # Try to attach the policy
                try:
                    self.iam_client.attach_role_policy(
                        RoleName=role_name, PolicyArn=policy_arn
                    )
                    logger.info(f"Attached policy to existing role: {role_name}")
                except Exception as e:
                    if "is already attached" in str(e):
                        logger.info(f"Policy already attached to role: {role_name}")
                    else:
                        logger.warning(f"Could not attach policy: {e}")

                # Attach AWS managed X-Ray policy for OpenTelemetry tracing
                try:
                    self.iam_client.attach_role_policy(
                        RoleName=role_name,
                        PolicyArn="arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess",
                    )
                    logger.info(
                        f"Attached AWSXRayDaemonWriteAccess policy to existing role: {role_name}"
                    )
                except Exception as e:
                    if (
                        "is already attached" in str(e).lower()
                        or "already attached" in str(e).lower()
                    ):
                        logger.info(
                            f"X-Ray policy already attached to role: {role_name}"
                        )
                    else:
                        logger.warning(f"Could not attach X-Ray policy: {e}")

                # Wait for IAM policy propagation
                logger.info("Waiting 30 seconds for IAM policy propagation...")
                # nosemgrep: arbitrary-sleep - Required for IAM policy propagation
                time.sleep(30)

            except Exception as e:
                logger.warning(f"Could not ensure policy on existing role: {e}")

            return role_arn

    def list_agent_runtimes(self) -> List[Dict[str, Any]]:
        """List existing AgentCore agent runtimes"""
        try:
            import boto3

            agentcore_client = boto3.client(
                "bedrock-agentcore-control", region_name="us-east-1"
            )
            response = agentcore_client.list_agent_runtimes(maxResults=100)
            runtimes = response.get("agentRuntimes", [])
            print(f"Runtimes are {runtimes}")
            return runtimes
        except Exception as e:
            logger.error(f"Failed to list agent runtimes: {str(e)}")
            return []

    def find_runtime_by_name(
        self,
        runtime_name: str,
        stack_prefix: str = None,
        agent_name: str = None,
        unique_id: str = None,
    ) -> str:
        """
        Find runtime ID by runtime name with STRICT prefix matching.
        
        Runtime names follow the pattern: {stack_prefix}_{AgentName}_{unique_id}[-suffix]
        For example: xia_AdFabricAgent_4208ab or xia_AdFabricAgent_4208ab-ifbXlwC83R
        
        CRITICAL: This method must NOT match runtimes from different stacks.
        For example, when looking for xia_AdFabricAgent_4208ab, it must NOT match
        bba_AdFabricAgent_4208ab even though both contain "4208ab".
        """
        try:
            runtimes = self.list_agent_runtimes()

            logger.info(f"Searching for runtime with name: '{runtime_name}'")
            logger.info(f"Stack prefix: '{stack_prefix}', unique_id: '{unique_id}'")
            logger.info(
                f"Available runtimes: {[r['agentRuntimeName'] for r in runtimes]}"
            )

            if not runtimes:
                logger.info("No runtimes returned from API")
                return None

            # STRICT MATCHING: Runtime name must START with stack_prefix_
            # This prevents matching "bba_AdFabricAgent_4208ab" when looking for "xia_*_4208ab"
            expected_prefix = f"{stack_prefix}_" if stack_prefix else None
            expected_prefix_lower = expected_prefix.lower() if expected_prefix else None

            # Create search variations for the runtime name
            search_variations = [
                runtime_name,
                runtime_name.replace("_", "-"),
                runtime_name.replace("-", "_"),
            ]
            search_variations = list(dict.fromkeys(search_variations))
            logger.info(f"Searching with variations: {search_variations}")

            # Try exact matches first (case sensitive)
            for variation in search_variations:
                for runtime in runtimes:
                    actual_name = runtime["agentRuntimeName"]
                    if actual_name == variation:
                        # Verify stack prefix if provided
                        if expected_prefix and not actual_name.startswith(expected_prefix):
                            logger.warning(
                                f"Skipping exact match '{actual_name}' - does not start with expected prefix '{expected_prefix}'"
                            )
                            continue
                        logger.info(
                            f"Found exact match for '{variation}': {runtime['agentRuntimeId']}"
                        )
                        return runtime["agentRuntimeId"]

            # Try case-insensitive exact matches
            for variation in search_variations:
                for runtime in runtimes:
                    actual_name = runtime["agentRuntimeName"]
                    if actual_name.lower() == variation.lower():
                        # Verify stack prefix if provided
                        if expected_prefix_lower and not actual_name.lower().startswith(expected_prefix_lower):
                            logger.warning(
                                f"Skipping case-insensitive match '{actual_name}' - does not start with expected prefix '{expected_prefix}'"
                            )
                            continue
                        logger.info(
                            f"Found case-insensitive match for '{variation}': {runtime['agentRuntimeId']}"
                        )
                        return runtime["agentRuntimeId"]

            # Try matching with suffix (runtime IDs often have random suffixes like -ifbXlwC83R)
            for variation in search_variations:
                for runtime in runtimes:
                    actual_name = runtime["agentRuntimeName"]
                    # Check if actual name starts with our variation (handles suffixes)
                    if actual_name.lower().startswith(variation.lower()):
                        # Verify stack prefix if provided
                        if expected_prefix_lower and not actual_name.lower().startswith(expected_prefix_lower):
                            logger.warning(
                                f"Skipping prefix match '{actual_name}' - does not start with expected prefix '{expected_prefix}'"
                            )
                            continue
                        logger.info(
                            f"Found prefix match for '{variation}' in '{actual_name}': {runtime['agentRuntimeId']}"
                        )
                        return runtime["agentRuntimeId"]

            # Construct expected runtime name pattern and search
            # Expected pattern: {stack_prefix}_{agent_name}_{unique_id}
            if stack_prefix and agent_name and unique_id:
                agent_name_normalized = agent_name.replace("-", "_")
                expected_runtime_name = f"{stack_prefix}_{agent_name_normalized}_{unique_id}"
                expected_runtime_name_lower = expected_runtime_name.lower()

                logger.info(f"Trying expected runtime name pattern: '{expected_runtime_name}'")

                for runtime in runtimes:
                    actual_name = runtime["agentRuntimeName"]
                    actual_name_lower = actual_name.lower()
                    
                    # Check if actual name starts with expected pattern (handles suffixes)
                    if actual_name_lower.startswith(expected_runtime_name_lower):
                        logger.info(
                            f"Found pattern match: '{actual_name}' starts with '{expected_runtime_name}' -> {runtime['agentRuntimeId']}"
                        )
                        return runtime["agentRuntimeId"]
                    
                    # Also try with hyphen separator
                    expected_with_hyphen = f"{stack_prefix}-{agent_name_normalized}-{unique_id}".lower()
                    if actual_name_lower.startswith(expected_with_hyphen):
                        logger.info(
                            f"Found hyphen pattern match: '{actual_name}' -> {runtime['agentRuntimeId']}"
                        )
                        return runtime["agentRuntimeId"]

            logger.info(f"No runtime found matching any variation of '{runtime_name}'")
            logger.info(f"Searched variations: {search_variations}")
            logger.info("Available runtime names for comparison:")
            for runtime in runtimes:
                logger.info(
                    f"  - {runtime['agentRuntimeName']} (ID: {runtime['agentRuntimeId']})"
                )
            return None

        except Exception as e:
            logger.error(f"Error searching for runtime '{runtime_name}': {e}")
            return None

    def get_agent_runtime(self, runtime_id: str) -> Dict[str, Any]:
        """Get details of an existing AgentCore agent runtime"""
        try:
            response = self.agentcore_client.get_agent_runtime(
                agentRuntimeId=runtime_id
            )
            return response
        except Exception as e:
            logger.error(f"Failed to get agent runtime {runtime_id}: {str(e)}")
            raise

    def create_agent_runtime(
        self,
        agent_config: Dict[str, Any],
        stack_prefix: str = None,
        agent_name: str = None,
        unique_id: str = None,
    ) -> str:
        """Create new AgentCore agent runtime using Method B: Manual Deployment"""
        agent_name = agent_config["name"]
        container_uri = agent_config["container_uri"]
        role_arn = agent_config["role_arn"]

        # Convert agent name to valid runtime name (replace hyphens with underscores)
        runtime_name = agent_name.replace("-", "_")

        # Ensure container URI has a tag
        if ":" not in container_uri:
            container_uri = f"{container_uri}:latest"

        # Validate runtime name format
        import re

        if not re.match(r"^[a-zA-Z][a-zA-Z0-9_]{0,47}$", runtime_name):
            raise ValueError(
                f"Invalid runtime name: {runtime_name}. Must match pattern [a-zA-Z][a-zA-Z0-9_]{{0,47}}"
            )

        logger.info(f"Creating AgentCore runtime using Manual Method:")
        logger.info(f"  Runtime name: {runtime_name}")
        logger.info(f"  Container URI: {container_uri}")
        logger.info(f"  Role ARN: {role_arn}")
        logger.info(f"  Region: {self.agentcore_region}")

        # Validate ECR access
        if not self.validate_ecr_access(container_uri, role_arn):
            logger.error("ECR validation failed, but continuing with deployment...")

        # Test role assumption
        if not self.test_role_assumption(role_arn):
            logger.error(
                "Role assumption test failed, but continuing with deployment..."
            )

        # Retry mechanism for IAM propagation delays
        max_retries = 3
        retry_delay = 45  # seconds - increased delay

        for attempt in range(max_retries):
            try:
                logger.info(
                    f"Attempt {attempt + 1}/{max_retries} to create AgentCore runtime..."
                )

                # Gather environment variables for the container
                kb_env_value = self.gather_knowledge_base_ids(stack_prefix, unique_id)

                # Get RUNTIMES from runtime registry
                from runtime_registry import RuntimeRegistry

                project_root = os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                )
                registry = RuntimeRegistry(stack_prefix, unique_id, project_root)
                runtime_env_value = registry.build_runtimes_env_value()
                logger.info(
                    f"RUNTIMES environment variable: {runtime_env_value[:100]}... (truncated)"
                )

                # Prepare container configuration (without environment variables)
                container_config = {
                    "containerConfiguration": {
                        "containerUri": container_uri,
                    }
                }

                # Prepare environment variables as separate parameter
                env_vars = {
                    "STACK_PREFIX": stack_prefix,
                    "UNIQUE_ID": unique_id,
                    "KNOWLEDGEBASES": kb_env_value,
                    "RUNTIMES": runtime_env_value,
                }

                # Add A2A configuration if available (from deploy-ecosystem.sh)
                if os.environ.get("A2A_POOL_ID"):
                    logger.info(
                        "Adding A2A configuration to runtime environment variables"
                    )
                    env_vars["A2A_POOL_ID"] = os.environ.get("A2A_POOL_ID", "")
                    env_vars["A2A_CLIENT_ID"] = os.environ.get("A2A_CLIENT_ID", "")
                    env_vars["A2A_DISCOVERY_URL"] = os.environ.get(
                        "A2A_DISCOVERY_URL", ""
                    )
                    env_vars["A2A_PROTOCOL"] = os.environ.get("A2A_PROTOCOL", "A2A")
                    logger.info(f"  A2A_POOL_ID: {env_vars['A2A_POOL_ID']}")
                    logger.info(f"  A2A_CLIENT_ID: {env_vars['A2A_CLIENT_ID']}")
                    logger.info(f"  A2A_DISCOVERY_URL: {env_vars['A2A_DISCOVERY_URL']}")
                else:
                    logger.info(
                        "No A2A configuration found - deploying as standard AgentCore agent"
                    )

                # Add AdCP MCP Gateway URL if available (from deploy-ecosystem.sh)
                adcp_gateway_url = os.environ.get("ADCP_GATEWAY_URL", "")
                if adcp_gateway_url:
                    env_vars["ADCP_GATEWAY_URL"] = adcp_gateway_url
                    env_vars["ADCP_USE_MCP"] = "true"
                    logger.info(f"Adding AdCP MCP Gateway configuration:")
                    logger.info(f"  ADCP_GATEWAY_URL: {adcp_gateway_url}")
                    logger.info(f"  ADCP_USE_MCP: true")
                else:
                    logger.info(
                        "No ADCP_GATEWAY_URL found - agents will use fallback local tools"
                    )

                # Method B: Manual Deployment with AWS CLI (with 10-minute timeouts)
                # Validate inputs to prevent command injection
                validated_runtime_name = self._validate_aws_identifier(runtime_name, "runtime_name")
                validated_role_arn = self._validate_aws_identifier(role_arn, "role_arn")
                validated_region = self._validate_aws_identifier(self.agentcore_region, "region")
                
                cli_command = [
                    "aws",
                    "bedrock-agentcore-control",
                    "create-agent-runtime",
                    "--agent-runtime-name",
                    validated_runtime_name,
                    "--agent-runtime-artifact",
                    json.dumps(container_config),
                    "--environment-variables",
                    json.dumps(env_vars),
                    "--network-configuration",
                    json.dumps({"networkMode": "PUBLIC"}),
                    "--role-arn",
                    validated_role_arn,
                    "--region",
                    validated_region,
                    "--cli-read-timeout",
                    "600",  # 10 minutes
                    "--cli-connect-timeout",
                    "600",  # 10 minutes
                    "--output",
                    "json",
                ]

                # Add profile if specified during initialization
                if hasattr(self, "_profile") and self._profile:
                    # Security: Validate profile name before using in subprocess
                    validated_profile = self._validate_aws_profile(self._profile)
                    cli_command.extend(["--profile", validated_profile])

                logger.info(
                    f"Executing CLI command with 10-minute timeouts: aws bedrock-agentcore-control create-agent-runtime..."
                )

                try:
                    result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit, dangerous-subprocess-use-tainted-env-args
                        cli_command, capture_output=True, text=True, timeout=700
                    )

                    if result.returncode != 0:
                        raise Exception(
                            f"CLI command failed (exit code {result.returncode}): {result.stderr}"
                        )

                    response = json.loads(result.stdout)

                except subprocess.TimeoutExpired:
                    raise Exception("CLI command timed out after 11+ minutes")
                except json.JSONDecodeError as e:
                    raise Exception(
                        f"Failed to parse CLI response as JSON: {e}. Output: {result.stdout}"
                    )
                except Exception as e:
                    raise Exception(f"CLI execution failed: {e}")

                runtime_id = response["agentRuntimeId"]
                runtime_arn = response["agentRuntimeArn"]

                logger.info(f"✅ Created AgentCore runtime: {runtime_id}")
                logger.info(f"   Runtime ARN: {runtime_arn}")

                return runtime_id

            except Exception as e:
                error_msg = str(e)
                logger.error(f"Attempt {attempt + 1} failed: {error_msg}")

                # Handle specific error cases
                if "ConflictException" in error_msg or "already exists" in error_msg:
                    logger.error(f"Runtime with name '{runtime_name}' already exists!")

                    # This should not happen if deploy_agent_runtime is working correctly
                    # But we'll handle it as a fallback
                    logger.error("This indicates the runtime detection logic failed")
                    logger.error("Attempting emergency conflict resolution...")

                    # Try to find the conflicting runtime with a fresh API call
                    existing_runtime = self.find_runtime_by_name(
                        runtime_name, stack_prefix, agent_name, unique_id
                    )
                    if existing_runtime:
                        logger.info(
                            f"Found existing runtime via emergency search: {existing_runtime}"
                        )
                        logger.info("Attempting to update the existing runtime...")
                        try:
                            # Get the role ARN from the existing runtime
                            runtime_details = self.get_agent_runtime(existing_runtime)
                            existing_role_arn = runtime_details["roleArn"]

                            # Check if the existing role is the default SDK role
                            # IMPORTANT: Always use the correct role naming pattern: AgentCoreRole-{prefix}-{agent}-{id}
                            if "AmazonBedrockAgentCoreSDKRuntime" in existing_role_arn:
                                logger.warning(f"Runtime uses default SDK role, upgrading to custom role...")
                                full_agent_name = f"{stack_prefix}-{agent_name}-{unique_id}"
                                custom_role_name = f"AgentCoreRole-{full_agent_name}"[:64]
                                try:
                                    existing_role_arn = self.create_agent_runtime_role(stack_prefix, custom_role_name)
                                    logger.info(f"Switched to custom role: {existing_role_arn}")
                                except Exception as role_err:
                                    logger.warning(f"Could not create custom role: {role_err}")

                            # Update the existing runtime
                            updated_runtime_id = self.update_agent_runtime(
                                existing_runtime,
                                container_uri,
                                existing_role_arn,
                                stack_prefix,
                                unique_id,
                            )
                            logger.info(
                                f"✅ Successfully updated existing runtime via emergency resolution: {updated_runtime_id}"
                            )
                            return updated_runtime_id
                        except Exception as update_error:
                            logger.error(
                                f"Failed to update existing runtime: {update_error}"
                            )
                            raise Exception(
                                f"Runtime '{runtime_name}' already exists and could not be updated automatically: {update_error}"
                            )
                    else:
                        logger.error(
                            "Could not find the conflicting runtime even with emergency search"
                        )
                        all_runtimes = self.list_agent_runtimes()
                        logger.error(
                            f"All available runtimes: {[(r['agentRuntimeName'], r['agentRuntimeId']) for r in all_runtimes]}"
                        )
                        raise Exception(
                            f"Runtime '{runtime_name}' already exists but could not be found for update"
                        )

                    # This should not be reached, but just in case
                    raise Exception(
                        f"Runtime '{runtime_name}' already exists. Manual intervention required."
                    )

                elif (
                    "Access denied" in error_msg or "ValidationException" in error_msg
                ) and attempt < max_retries - 1:
                    if "ECR URI" in error_msg:
                        logger.info(
                            f"ECR validation failed, likely due to IAM propagation delay. Waiting {retry_delay} seconds before retry..."
                        )
                    else:
                        logger.info(
                            f"IAM permissions may still be propagating. Waiting {retry_delay} seconds before retry..."
                        )
                    # nosemgrep: arbitrary-sleep - Intentional retry backoff
                    time.sleep(retry_delay)
                    continue
                else:
                    logger.error(
                        f"Failed to create agent runtime for {agent_name} after {max_retries} attempts"
                    )
                    raise

    def update_agent_runtime(
        self,
        runtime_id: str,
        container_uri: str,
        role_arn: str,
        stack_prefix: str = None,
        unique_id: str = None,
        max_retries: int = 5,
        retry_delay: int = 15,
    ) -> str:
        """Update existing AgentCore agent runtime with new container image"""
        logger.info(f"Updating AgentCore runtime: {runtime_id}")
        logger.info(f"  New container URI: {container_uri}")
        logger.info(f"  Role ARN: {role_arn}")

        for attempt in range(max_retries):
            try:
                # Gather environment variables for the container
                kb_env_value = ""
                runtime_env_value = ""
                if stack_prefix and unique_id:
                    kb_env_value = self.gather_knowledge_base_ids(
                        stack_prefix, unique_id
                    )

                    # Get RUNTIMES from runtime registry
                    from runtime_registry import RuntimeRegistry

                    project_root = os.path.dirname(
                        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    )
                    registry = RuntimeRegistry(stack_prefix, unique_id, project_root)
                    runtime_env_value = registry.build_runtimes_env_value()
                    logger.info(
                        f"RUNTIMES environment variable: {runtime_env_value[:100]}... (truncated)"
                    )

                # Prepare container configuration (without environment variables)
                container_config = {
                    "containerConfiguration": {
                        "containerUri": container_uri,
                    }
                }

                # Prepare environment variables as separate parameter
                env_vars = {
                    "STACK_PREFIX": stack_prefix or "",
                    "UNIQUE_ID": unique_id or "",
                    "KNOWLEDGEBASES": kb_env_value,
                    "RUNTIMES": runtime_env_value,
                }

                # Add A2A configuration if available (from deploy-ecosystem.sh)
                if os.environ.get("A2A_POOL_ID"):
                    logger.info(
                        "Adding A2A configuration to runtime environment variables"
                    )
                    env_vars["A2A_POOL_ID"] = os.environ.get("A2A_POOL_ID", "")
                    env_vars["A2A_CLIENT_ID"] = os.environ.get("A2A_CLIENT_ID", "")
                    env_vars["A2A_DISCOVERY_URL"] = os.environ.get(
                        "A2A_DISCOVERY_URL", ""
                    )
                    env_vars["A2A_PROTOCOL"] = os.environ.get("A2A_PROTOCOL", "A2A")
                    logger.info(f"  A2A_POOL_ID: {env_vars['A2A_POOL_ID']}")
                    logger.info(f"  A2A_CLIENT_ID: {env_vars['A2A_CLIENT_ID']}")
                    logger.info(f"  A2A_DISCOVERY_URL: {env_vars['A2A_DISCOVERY_URL']}")

                # Add AdCP MCP Gateway URL if available (from deploy-ecosystem.sh)
                adcp_gateway_url = os.environ.get("ADCP_GATEWAY_URL", "")
                if adcp_gateway_url:
                    env_vars["ADCP_GATEWAY_URL"] = adcp_gateway_url
                    env_vars["ADCP_USE_MCP"] = "true"
                    logger.info(f"Adding AdCP MCP Gateway configuration:")
                    logger.info(f"  ADCP_GATEWAY_URL: {adcp_gateway_url}")
                    logger.info(f"  ADCP_USE_MCP: true")
                else:
                    logger.info(
                        "No ADCP_GATEWAY_URL found - agents will use fallback local tools"
                    )

                # Use AWS CLI with 10-minute timeouts for update operations
                # Validate inputs to prevent command injection
                validated_runtime_id = self._validate_aws_identifier(runtime_id, "runtime_id")
                validated_role_arn = self._validate_aws_identifier(role_arn, "role_arn")
                validated_region = self._validate_aws_identifier(self.agentcore_region, "region")
                
                cli_command = [
                    "aws",
                    "bedrock-agentcore-control",
                    "update-agent-runtime",
                    "--agent-runtime-id",
                    validated_runtime_id,
                    "--agent-runtime-artifact",
                    json.dumps(container_config),
                    "--environment-variables",
                    json.dumps(env_vars),
                    "--network-configuration",
                    json.dumps({"networkMode": "PUBLIC"}),
                    "--role-arn",
                    validated_role_arn,
                    "--description",
                    "Updated agent runtime with new container image and environment variables",
                    "--region",
                    validated_region,
                    "--cli-read-timeout",
                    "600",  # 10 minutes
                    "--cli-connect-timeout",
                    "600",  # 10 minutes
                    "--output",
                    "json",
                ]

                # Add profile if specified
                if hasattr(self, "_profile") and self._profile:
                    # Security: Validate profile name before using in subprocess
                    validated_profile = self._validate_aws_profile(self._profile)
                    cli_command.extend(["--profile", validated_profile])

                logger.info(f"Executing CLI update command with 10-minute timeouts...")
                logger.info(f"Command: {' '.join(cli_command[:10])}... (truncated)")
                logger.debug(f"Full command: {cli_command}")
                logger.debug(
                    f"Container config: {json.dumps(container_config, indent=2)}"
                )
                logger.debug(f"Environment vars: {json.dumps(env_vars, indent=2)}")

                result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit, dangerous-subprocess-use-tainted-env-args
                    cli_command,
                    capture_output=True,
                    text=True,
                    timeout=700,  # 11+ minutes to allow for CLI timeouts
                )

                if result.returncode != 0:
                    error_details = (
                        f"CLI update command failed (exit code {result.returncode})"
                    )
                    if result.stderr:
                        error_details += f"\nStderr: {result.stderr}"
                    if result.stdout:
                        error_details += f"\nStdout: {result.stdout}"
                    logger.error(error_details)
                    raise Exception(error_details)

                response = json.loads(result.stdout)

                logger.info(f"✅ Updated AgentCore runtime: {runtime_id}")
                return runtime_id

            except subprocess.TimeoutExpired:
                raise Exception("CLI update command timed out after 11+ minutes")
            except json.JSONDecodeError as e:
                error_msg = f"Failed to parse CLI update response as JSON: {e}"
                if result.stdout:
                    error_msg += f"\nOutput: {result.stdout}"
                if result.stderr:
                    error_msg += f"\nStderr: {result.stderr}"
                logger.error(error_msg)
                raise Exception(error_msg)
            except Exception as e:
                error_msg = str(e)
                logger.error(f"Update attempt {attempt + 1} failed: {error_msg}")

                # Check for IAM propagation issues (ECR validation failures)
                if (
                    "Access denied" in error_msg or "ValidationException" in error_msg
                ) and attempt < max_retries - 1:
                    if "ECR URI" in error_msg:
                        logger.info(
                            f"ECR validation failed, likely due to IAM propagation delay. "
                            f"Waiting {retry_delay} seconds before retry ({attempt + 2}/{max_retries})..."
                        )
                    else:
                        logger.info(
                            f"IAM permissions may still be propagating. "
                            f"Waiting {retry_delay} seconds before retry ({attempt + 2}/{max_retries})..."
                        )
                    # nosemgrep: arbitrary-sleep - Intentional retry backoff for IAM propagation
                    time.sleep(retry_delay)
                    continue
                else:
                    logger.error(
                        f"Failed to update agent runtime {runtime_id} after {attempt + 1} attempts: {error_msg}"
                    )
                    raise

    def ensure_fresh_config_for_build(
        self,
        agent_folder: str,
        agent_name: str,
    ):
        """Ensure the config file has a fresh timestamp to bust Docker cache"""
        config_path = os.path.join(agent_folder, "config.json")

        if not os.path.exists(config_path):
            logger.warning(f"Config file not found: {config_path}")
            return

        try:
            logger.info(f"Ensuring fresh config for Docker build: {config_path}")

            # Load current config
            with open(config_path, "r") as f:
                config = json.load(f)

            # Add a build timestamp to ensure Docker picks up changes
            # This forces Docker to rebuild layers that depend on config.json
            config["build_timestamp"] = datetime.utcnow().strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            )

            # Write updated config back to file
            with open(config_path, "w") as f:
                json.dump(config, f, indent=4)

            logger.info(f"✅ Added build timestamp to config file for cache busting")

        except Exception as e:
            logger.error(f"Failed to update config build timestamp: {e}")

    def deploy_agent_runtime(
        self,
        agent_config: Dict[str, Any],
        stack_prefix: str = None,
        agent_name: str = None,
        unique_id: str = None,
    ) -> str:
        """Deploy AgentCore agent runtime (create or update)"""
        agent_name = agent_config["name"]
        runtime_name = agent_name.replace("-", "_")

        logger.info(f"Deploying AgentCore runtime: {agent_name} -> {runtime_name}")

        # Check if runtime already exists
        existing_runtime_id = self.find_runtime_by_name(
            runtime_name, stack_prefix, agent_name, unique_id
        )

        if existing_runtime_id:
            logger.info(f"Found existing runtime: {existing_runtime_id}")
            # Update existing runtime
            try:
                runtime_details = self.get_agent_runtime(existing_runtime_id)
                role_arn = runtime_details["roleArn"]
                container_uri = agent_config["container_uri"]

                # Check if the existing role is the default SDK role (lacks DynamoDB/SSM permissions)
                # If so, create/ensure a custom role with full permissions and switch to it
                # IMPORTANT: Always use the correct role naming pattern: AgentCoreRole-{prefix}-{agent}-{id}
                if "AmazonBedrockAgentCoreSDKRuntime" in role_arn:
                    logger.warning(
                        f"Runtime is using default SDK role which lacks DynamoDB/SSM permissions: {role_arn}"
                    )
                    logger.info("Creating/ensuring custom execution role with full permissions...")
                    full_agent_name = f"{stack_prefix}-{agent_name}-{unique_id}"
                    custom_role_name = f"AgentCoreRole-{full_agent_name}"[:64]
                    try:
                        custom_role_arn = self.create_agent_runtime_role(stack_prefix, custom_role_name)
                        logger.info(f"Switching runtime to custom role: {custom_role_arn}")
                        role_arn = custom_role_arn
                    except Exception as role_error:
                        logger.warning(f"Could not create custom role, continuing with existing: {role_error}")

                updated_runtime_id = self.update_agent_runtime(
                    existing_runtime_id,
                    container_uri,
                    role_arn,
                    stack_prefix,
                    unique_id,
                )
                logger.info(
                    f"✅ Successfully updated existing runtime: {updated_runtime_id}"
                )
                return updated_runtime_id
            except Exception as e:
                logger.error(
                    f"Failed to update existing runtime {existing_runtime_id}: {e}"
                )
                raise Exception(
                    f"Runtime '{runtime_name}' exists but could not be updated: {e}"
                )
        else:
            logger.info(f"No existing runtime found, creating new one")
            # Create new runtime - this will handle conflicts automatically
            try:
                runtime_id = self.create_agent_runtime(
                    agent_config, stack_prefix, agent_name, unique_id
                )

                return runtime_id
            except Exception as e:
                # If create fails due to conflict, try to find and update the runtime
                if "already exists" in str(e) or "ConflictException" in str(e):
                    logger.warning(
                        f"Create failed due to conflict, attempting to find and update runtime"
                    )

                    # Multiple attempts to find the runtime due to API consistency issues
                    existing_runtime_id = None
                    for search_attempt in range(3):
                        logger.info(
                            f"Search attempt {search_attempt + 1}/3 for runtime: {runtime_name}"
                        )

                        # Wait a bit for API consistency
                        if search_attempt > 0:
                            import time

                            # nosemgrep: arbitrary-sleep - Intentional retry backoff
                            time.sleep(5)

                        existing_runtime_id = self.find_runtime_by_name(
                            runtime_name, stack_prefix, agent_name, unique_id
                        )
                        if existing_runtime_id:
                            logger.info(
                                f"Found runtime after conflict (attempt {search_attempt + 1}): {existing_runtime_id}"
                            )
                            break

                        # Try alternative search methods
                        logger.info(
                            f"Direct search failed, trying comprehensive runtime listing..."
                        )
                        runtimes = self.list_agent_runtimes()
                        logger.info(
                            f"All available runtimes: {[(r['agentRuntimeName'], r['agentRuntimeId']) for r in runtimes]}"
                        )

                        # Try STRICT prefix matching - runtime must START with stack_prefix_
                        expected_prefix = f"{stack_prefix}_" if stack_prefix else None
                        for runtime in runtimes:
                            actual_name = runtime["agentRuntimeName"]
                            actual_name_lower = actual_name.lower()
                            
                            # CRITICAL: Only match if runtime starts with correct stack prefix
                            if expected_prefix and not actual_name_lower.startswith(expected_prefix.lower()):
                                continue
                            
                            # Also verify unique_id is present
                            if unique_id and unique_id.lower() not in actual_name_lower:
                                continue
                            
                            # Check if runtime name matches our target pattern
                            if (
                                runtime_name.lower() in actual_name_lower
                                or actual_name_lower.startswith(runtime_name.lower())
                            ):
                                logger.info(
                                    f"Found strict match: '{actual_name}' matches '{runtime_name}' with prefix '{expected_prefix}'"
                                )
                                print(f"Runtime object: {json.dumps(runtime)}")
                                existing_runtime_id = runtime["agentRuntimeId"]
                                break

                        if existing_runtime_id:
                            break

                    if existing_runtime_id:
                        try:
                            runtime_details = self.get_agent_runtime(
                                existing_runtime_id
                            )
                            role_arn = runtime_details["roleArn"]
                            container_uri = agent_config["container_uri"]

                            # Check if the existing role is the default SDK role
                            # IMPORTANT: Always use the correct role naming pattern: AgentCoreRole-{prefix}-{agent}-{id}
                            if "AmazonBedrockAgentCoreSDKRuntime" in role_arn:
                                logger.warning(f"Runtime uses default SDK role, upgrading to custom role...")
                                full_agent_name_for_role = f"{stack_prefix}-{agent_name}-{unique_id}"
                                custom_role_name = f"AgentCoreRole-{full_agent_name_for_role}"[:64]
                                try:
                                    role_arn = self.create_agent_runtime_role(stack_prefix, custom_role_name)
                                    logger.info(f"Switched to custom role: {role_arn}")
                                except Exception as role_err:
                                    logger.warning(f"Could not create custom role: {role_err}")

                            updated_runtime_id = self.update_agent_runtime(
                                existing_runtime_id,
                                container_uri,
                                role_arn,
                                stack_prefix,
                                unique_id,
                            )
                            logger.info(
                                f"✅ Successfully updated runtime after conflict resolution: {updated_runtime_id}"
                            )
                            return updated_runtime_id
                        except Exception as update_error:
                            logger.error(
                                f"Failed to update existing runtime: {update_error}"
                            )
                            # Fall through to the error case below

                    # If we still can't find it, provide more detailed error information
                    logger.error(
                        f"Runtime conflict detected but could not find runtime to update"
                    )
                    logger.error(f"Searched for: {runtime_name}")
                    runtimes = self.list_agent_runtimes()
                    logger.error(
                        f"Available runtimes: {[(r['agentRuntimeName'], r['agentRuntimeId']) for r in runtimes]}"
                    )
                    raise Exception(
                        f"Runtime '{runtime_name}' exists but could not be found for update. This may be due to API consistency issues."
                    )
                else:
                    raise

    def save_agentcore_runtime_info(
        self,
        agent_name: str,
        agent_type: str,
        display_name: str,
        runtime_id: str,
        container_uri: str,
        stack_prefix: str,
        unique_id: str,
    ):
        """Save AgentCore runtime information to local file with memory and external tool configuration"""
        try:

            # Get account ID for ARN construction
            account_id = self.get_account_id()

            # Construct runtime ARN
            runtime_arn = f"arn:aws:bedrock-agentcore:{self.agentcore_region}:{account_id}:runtime/{runtime_id}"

            # Path to the agentcore agents file
            project_root = os.environ.get("PROJECT_ROOT")
            if not project_root:
                current_dir = os.path.dirname(os.path.abspath(__file__))
                project_root = os.path.dirname(os.path.dirname(current_dir))

            agentcore_file = os.path.join(
                project_root, f".agentcore-agents-{stack_prefix}-{unique_id}.json"
            )

            logger.info(f"Saving AgentCore runtime info to: {agentcore_file}")
            logger.info(f"Agent: {agent_name}, Runtime ID: {runtime_id}")

            # Load existing config or create new one
            if os.path.exists(agentcore_file):
                with open(agentcore_file, "r") as f:
                    config = json.load(f)
            else:
                config = {
                    "deployed_agents": [],
                    "deployment_time": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "stack_prefix": stack_prefix,
                    "unique_id": unique_id,
                }

            # Ensure deployed_agents is a list
            if "deployed_agents" not in config:
                config["deployed_agents"] = []

            # Generate memory configuration
            try:
                memory_config = self._generate_memory_config(
                    agent_name, stack_prefix, unique_id
                )
                logger.debug(f"Generated memory config: {memory_config}")
            except Exception as e:
                logger.error(f"Failed to generate memory config: {e}")
                memory_config = {"memory_id": f"{stack_prefix}memory{unique_id}"}

            # Load external agent tools from agent config if available
            try:
                external_tools = []
                # external_tools = self._load_external_agent_tools(agent_config_path)
                logger.debug(f"Loaded external tools: {external_tools}")
            except Exception as e:
                logger.error(f"Failed to load external tools: {e}")
                external_tools = []

            # Validate external tool dependencies
            try:
                validated_tools = self._validate_external_tool_dependencies(
                    external_tools, stack_prefix, unique_id
                )
                logger.debug(f"Validated tools: {validated_tools}")
            except Exception as e:
                logger.error(f"Failed to validate external tools: {e}")
                validated_tools = []

            # Create agent runtime info with enhanced configuration
            try:
                agent_runtime_info = {
                    "name": agent_name,
                    "runtime_id": runtime_id,
                    "runtime_arn": runtime_arn,
                    "container_uri": container_uri,
                    "deployment_time": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "memory_config": memory_config,
                    "external_tools": validated_tools,
                    "runtime_name": agent_name.replace(
                        "-", "_"
                    ),  # For external tool resolution
                }
                logger.debug(f"Created agent runtime info: {agent_runtime_info}")
            except Exception as e:
                logger.error(f"Failed to create agent runtime info: {e}")
                # Create minimal runtime info as fallback
                agent_runtime_info = {
                    "name": agent_name,
                    "runtime_id": runtime_id,
                    "runtime_arn": runtime_arn,
                    "container_uri": container_uri,
                    "deployment_time": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "memory_config": {"memory_id": f"{stack_prefix}memory{unique_id}"},
                    "external_tools": [],
                    "runtime_name": agent_name.replace("-", "_"),
                }

            # Check if agent already exists in the list
            existing_agent_index = None
            try:
                for i, agent in enumerate(config["deployed_agents"]):
                    if isinstance(agent, dict) and agent.get("name") == agent_name:
                        existing_agent_index = i
                        break
                    elif isinstance(agent, str) and agent == agent_name:
                        existing_agent_index = i
                        break
                logger.debug(f"Existing agent index: {existing_agent_index}")
            except Exception as e:
                logger.error(f"Error checking for existing agent: {e}")
                existing_agent_index = None

            if existing_agent_index is not None:
                config["deployed_agents"][existing_agent_index] = agent_runtime_info
                logger.info(
                    f"Updated existing AgentCore agent runtime info: {agent_name}"
                )
            else:
                config["deployed_agents"].append(agent_runtime_info)
                logger.info(f"Added new AgentCore agent runtime info: {agent_name}")

            # Update deployment time and remove skipped flag if it exists
            config["deployment_time"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            if "skipped" in config:
                del config["skipped"]
            if "reason" in config:
                del config["reason"]

            # Write updated config
            with open(agentcore_file, "w") as f:
                json.dump(config, f, indent=2)

            logger.info(
                f"✅ Successfully saved AgentCore runtime information to {agentcore_file}"
            )
            logger.info(f"Memory configuration: {memory_config['memory_id']}")
            logger.info(f"External tools: {len(validated_tools)} tools configured")

        except Exception as e:
            logger.error(f"Failed to save AgentCore runtime information: {e}")
            import traceback

            logger.error(f"Full traceback: {traceback.format_exc()}")

    def _generate_memory_config(
        self, agent_name: str, stack_prefix: str, unique_id: str
    ) -> Dict[str, Any]:
        """Generate memory configuration for the agent"""
        # Convert agent name to consistent format (hyphens to underscores)
        agent_name_normalized = agent_name.replace("-", "_")

        # Generate consistent memory patterns
        memory_id = f"{stack_prefix}memory{unique_id}"
        namespace = f"/stack/{stack_prefix}_{unique_id}/context/"
        actor_id = f"{stack_prefix}_{agent_name_normalized}_actor_{unique_id}"

        return {
            "memory_id": memory_id,
            "namespace": namespace,
            "actor_id": actor_id,
            "actor_id_pattern": f"{stack_prefix}_{{agent_name_with_underscores}}_actor_{unique_id}",
            "session_id_pattern": f"{stack_prefix}_{unique_id}_{{ui_session_id}}",
            "max_conversation_turns": 100,
            "memory_expiry_days": 7,
            "session_based_threads": True,
            "cross_agent_access": True,
            "aws_region": self.agentcore_region,
            "stack_prefix": stack_prefix,
            "unique_id": unique_id,
            "agent_name": agent_name,
            "agent_name_normalized": agent_name_normalized,
        }

    def _load_external_agent_tools(
        self, agent_config_path: str
    ) -> List[Dict[str, Any]]:
        """Load external agent tools from agent configuration"""
        if not agent_config_path or not os.path.exists(agent_config_path):
            logger.debug("No agent config path provided or file doesn't exist")
            return []

        try:
            with open(agent_config_path, "r") as f:
                agent_config = json.load(f)

            external_tools = agent_config.get("external_agent_tools", [])

            # Ensure external_tools is a list
            if not isinstance(external_tools, list):
                logger.warning(
                    f"external_agent_tools should be a list, got {type(external_tools)}"
                )
                return []

            logger.info(
                f"Loaded {len(external_tools)} external agent tools from config"
            )
            logger.debug(f"External tools structure: {external_tools}")
            return external_tools

        except Exception as e:
            logger.warning(f"Failed to load external agent tools from config: {e}")
            return []

    def _validate_external_tool_dependencies(
        self, external_tools: List[Dict[str, Any]], stack_prefix: str, unique_id: str
    ) -> List[Dict[str, Any]]:
        """Validate external agent tool dependencies and resolve runtime ARNs"""
        validated_tools = []

        for tool_config in external_tools:
            # Handle case where tool_config might be a string instead of dict
            if isinstance(tool_config, str):
                # Convert string to dict format
                tool_config = {"agent_name": tool_config}
                logger.debug(f"Converted string tool config to dict: {tool_config}")
            elif not isinstance(tool_config, dict):
                logger.warning(
                    f"Invalid tool config type: {type(tool_config)}, skipping"
                )
                continue

            agent_name = tool_config.get("agent_name")
            if not agent_name:
                logger.warning("External tool config missing agent_name, skipping")
                continue

            # Generate expected runtime name pattern
            runtime_name = f"{stack_prefix}_{agent_name.replace('-', '_')}_{unique_id}"

            # Try to find the runtime
            runtime_id = self.find_runtime_by_name(
                runtime_name, stack_prefix, agent_name, unique_id
            )

            if runtime_id:
                # Runtime found, add ARN to tool config
                account_id = self.get_account_id()
                runtime_arn = f"arn:aws:bedrock-agentcore:{self.agentcore_region}:{account_id}:runtime/{runtime_id}"

                validated_tool = tool_config.copy()
                validated_tool.update(
                    {
                        "runtime_id": runtime_id,
                        "runtime_arn": runtime_arn,
                        "runtime_name": runtime_name,
                        "validation_status": "resolved",
                        "validated_at": datetime.utcnow().strftime(
                            "%Y-%m-%dT%H:%M:%SZ"
                        ),
                    }
                )
                validated_tools.append(validated_tool)
                logger.info(f"✅ Validated external tool: {agent_name} -> {runtime_id}")
            else:
                # Runtime not found, mark as unresolved but keep config for future resolution
                validated_tool = tool_config.copy()
                validated_tool.update(
                    {
                        "runtime_name": runtime_name,
                        "validation_status": "unresolved",
                        "validation_error": f"Runtime not found for agent: {agent_name}",
                        "validated_at": datetime.utcnow().strftime(
                            "%Y-%m-%dT%H:%M:%SZ"
                        ),
                    }
                )
                validated_tools.append(validated_tool)
                logger.warning(
                    f"⚠️  External tool runtime not found: {agent_name} (expected: {runtime_name})"
                )

        return validated_tools

    def test_role_assumption(self, role_arn: str) -> bool:
        """Test if the role can be assumed by the AgentCore service"""
        try:
            logger.info(f"Testing role assumption for: {role_arn}")

            # We can't directly assume the role as AgentCore service, but we can check if it exists
            role_name = role_arn.split("/")[-1]
            response = self.iam_client.get_role(RoleName=role_name)

            # Check if the trust policy allows AgentCore service
            trust_policy = response["Role"]["AssumeRolePolicyDocument"]
            if isinstance(trust_policy, str):
                import json

                trust_policy = json.loads(trust_policy)

            for statement in trust_policy.get("Statement", []):
                principal = statement.get("Principal", {})
                if isinstance(principal, dict):
                    service = principal.get("Service", "")
                    if (
                        isinstance(service, str)
                        and service == "bedrock-agentcore.amazonaws.com"
                    ) or (
                        isinstance(service, list)
                        and "bedrock-agentcore.amazonaws.com" in service
                    ):
                        logger.info("✅ Role trust policy allows AgentCore service")
                        return True

            logger.warning("⚠️  Role trust policy may not allow AgentCore service")
            return False

        except Exception as e:
            logger.warning(f"⚠️  Could not test role assumption: {e}")
            return True  # Assume it's okay if we can't test

    def validate_ecr_access(self, container_uri: str, role_arn: str) -> bool:
        """Validate that the role has access to the ECR repository"""
        try:
            # Extract repository name from URI
            if "/" in container_uri:
                repo_name = container_uri.split("/")[-1].split(":")[0]
            else:
                repo_name = container_uri.split(":")[0]

            logger.info(f"Validating ECR access for repository: {repo_name}")

            # Try to describe the repository
            try:
                response = self.ecr_client.describe_repositories(
                    repositoryNames=[repo_name]
                )
                logger.info(f"✅ ECR repository exists: {repo_name}")
                return True
            except self.ecr_client.exceptions.RepositoryNotFoundException:
                logger.error(f"❌ ECR repository not found: {repo_name}")
                return False
            except Exception as e:
                logger.warning(f"⚠️  Could not validate ECR repository access: {e}")
                return True  # Assume it's accessible if we can't validate

        except Exception as e:
            logger.warning(f"⚠️  ECR validation failed: {e}")
            return True  # Assume it's accessible if validation fails

    def cache_runtime_arn(
        self, agent_name: str, runtime_id: str, stack_prefix: str, unique_id: str
    ):
        """Cache runtime ARN for external tool resolution"""
        try:
            account_id = self.get_account_id()
            runtime_arn = f"arn:aws:bedrock-agentcore:{self.agentcore_region}:{account_id}:runtime/{runtime_id}"

            # Store in a runtime cache file for quick lookups
            project_root = os.environ.get("PROJECT_ROOT")
            if not project_root:
                current_dir = os.path.dirname(os.path.abspath(__file__))
                project_root = os.path.dirname(os.path.dirname(current_dir))

            cache_file = os.path.join(
                project_root, f".runtime-cache-{stack_prefix}-{unique_id}.json"
            )

            # Load existing cache or create new one
            if os.path.exists(cache_file):
                with open(cache_file, "r") as f:
                    cache = json.load(f)
            else:
                cache = {
                    "stack_prefix": stack_prefix,
                    "unique_id": unique_id,
                    "runtimes": {},
                }

            # Add/update runtime entry
            runtime_name = agent_name.replace("-", "_")
            cache["runtimes"][runtime_name] = {
                "agent_name": agent_name,
                "runtime_id": runtime_id,
                "runtime_arn": runtime_arn,
                "cached_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            }

            # Write updated cache
            with open(cache_file, "w") as f:
                json.dump(cache, f, indent=2)

            logger.debug(f"Cached runtime ARN for {agent_name}: {runtime_arn}")

        except Exception as e:
            logger.warning(f"Failed to cache runtime ARN for {agent_name}: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Deploy AgentCore agent using Manual Method"
    )
    parser.add_argument("--profile", required=True, help="AWS profile name")
    parser.add_argument("--region", required=True, help="AWS region (for ECR)")
    parser.add_argument(
        "--agentcore-region", help="AWS region for AgentCore (defaults to --region)"
    )
    parser.add_argument(
        "--stack-prefix", required=True, help="Stack prefix for agent name"
    )
    parser.add_argument("--unique-id", required=True, help="Unique ID for agent name")
    parser.add_argument("--agent-name", required=True, help="Base agent name")
    parser.add_argument(
        "--agent-folder", required=True, help="Path to agent configuration folder"
    )
    parser.add_argument("--container-uri", required=True, help="ECR container URI")

    args, unknown = parser.parse_known_args()

    # Warn about unknown arguments but don't fail
    if unknown:
        logger.warning(f"Unknown arguments ignored: {unknown}")

    # Check and upgrade AWS CLI if needed
    print("🔍 Checking AWS CLI compatibility...")
    if not ManualAgentCoreDeployer._check_and_upgrade_aws_cli():
        print(
            "❌ AWS CLI does not support bedrock-agentcore-control and could not be upgraded"
        )
        print("Please upgrade AWS CLI manually and try again")
        sys.exit(1)

    # Set environment variables for consistency
    os.environ["AWS_PROFILE"] = args.profile
    os.environ["STACK_PREFIX"] = args.stack_prefix
    os.environ["UNIQUE_ID"] = args.unique_id

    # Calculate project root
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(current_dir))
    os.environ["PROJECT_ROOT"] = project_root

    region = args.region
    agentcore_region = args.agentcore_region or args.region

    # Handle EU regions - use eu-central-1 for AgentCore if region starts with eu-
    if region.startswith("eu-") and not args.agentcore_region:
        agentcore_region = "eu-central-1"
        print(
            f"EU region detected ({region}), using AgentCore region: {agentcore_region}"
        )

    # Load agent configuration
    # config_path = os.path.join(args.agent_folder, "config.json")
    # if not os.path.exists(config_path):
    #     print(f"Error: Config file not found at {config_path}")
    #     sys.exit(1)

    # with open(config_path, "r") as f:
    #     agent_config = json.load(f)

    # Construct agent name: {stack-prefix}-{agent-name}-{unique-id}
    full_agent_name = f"{args.stack_prefix}-{args.agent_name}-{args.unique_id}"
    # agent_type = agent_config.get("agent_type", args.agent_name)
    # display_name = agent_config.get("display_name", full_agent_name)

    print(f"🚀 Deploying AgentCore agent using Manual Method:")
    print(f"   Agent Name: {args.agent_name}")
    print(f"   Full Agent Name: {full_agent_name}")
    # print(f"   Agent Type: {agent_type}")
    # print(f"   Display Name: {display_name}")
    print(f"   Container URI: {args.container_uri}")
    print(f"   ECR Region: {region}")
    print(f"   AgentCore Region: {agentcore_region}")
    print(f"   Stack Prefix: {args.stack_prefix}")
    print(f"   Unique ID: {args.unique_id}")

    # Create deployer
    deployer = ManualAgentCoreDeployer(
        region=region, agentcore_region=agentcore_region, profile=args.profile
    )

    try:
        # Ensure fresh config for Docker build (bust cache to pick up manual changes)
        # deployer.ensure_fresh_config_for_build(args.agent_folder, args.agent_name)

        # Create IAM role for the agent
        role_name = f"AgentCoreRole-{full_agent_name}"
        role_arn = deployer.create_agent_runtime_role(args.stack_prefix, role_name)

        # Prepare agent configuration
        agent_deployment_config = {
            "name": full_agent_name,
            "container_uri": args.container_uri,
            "role_arn": role_arn,
            "agent_type": "http",
            "display_name": args.agent_name,
        }

        # Deploy the agent runtime
        runtime_id = deployer.deploy_agent_runtime(
            agent_deployment_config, args.stack_prefix, args.agent_name, args.unique_id
        )

        print(f"✅ AgentCore agent deployed successfully!")
        print(f"   Runtime ID: {runtime_id}")
        print(f"   Agent Name: {full_agent_name}")
        print(f"   Container URI: {args.container_uri}")

        # Save runtime information with memory and external tool configuration
        deployer.save_agentcore_runtime_info(
            full_agent_name,
            "agentcore",
            full_agent_name,
            runtime_id,
            args.container_uri,
            args.stack_prefix,
            args.unique_id,
        )

        # Cache runtime ARN for external tool resolution
        deployer.cache_runtime_arn(
            full_agent_name, runtime_id, args.stack_prefix, args.unique_id
        )

        print(f"✅ Runtime information saved successfully")

    except Exception as e:
        print(f"❌ Error during deployment: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
