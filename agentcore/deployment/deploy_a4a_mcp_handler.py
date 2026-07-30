#!/usr/bin/env python3
"""
Deploy A4A MCP Handler as Target 2 on the existing AdCP Gateway.

This script:
1. Auto-discovers the existing gateway from .ads-gw-*.json or SSM
2. Creates an IAM execution role (least-privilege)
3. Packages and deploys the Lambda function
4. Registers it as a new target on the gateway with tool schemas
5. Outputs the gateway URL + mcp-proxy-for-aws command

Prerequisites:
- Existing A4A stack deployed (deploy-ecosystem.sh Phase 6 completed)
- AWS CLI configured with appropriate permissions
- Python 3.10+

Usage:
    python deploy_a4a_mcp_handler.py --stack-prefix a4a --unique-id omixaj --region us-west-2 --profile rtbag
"""

import argparse
import glob
import io
import json
import logging
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("deploy-a4a-mcp-handler")


# ============================================================================
# Tool Schemas for Gateway Registration
# ============================================================================

A4A_MCP_TOOL_SCHEMA = [
    {
        "name": "list_agents",
        "description": "List available advertising agents. Call this first to discover what agents are available.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Filter by type — 'orchestrator' (default), 'specialist', or 'all'.",
                }
            },
        },
    },
    {
        "name": "get_agent_schema",
        "description": "Get the structured input schema for a specific agent. Use this if you want to know what parameters an agent accepts beyond a free-text prompt.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "Agent name from list_agents (e.g. 'MediaPlanningAgent', 'AgencyAgent').",
                }
            },
            "required": ["agent_name"],
        },
    },
    {
        "name": "invoke_agent",
        "description": "Invoke an advertising agent with a natural language prompt. Returns immediately with session_id if agent takes longer than 60s — use get_agent_conversation to poll for results.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "Agent name from list_agents (e.g. 'MediaPlanningAgent', 'AgencyAgent').",
                },
                "prompt": {
                    "type": "string",
                    "description": "Natural language task (e.g. 'Plan a $3M sports campaign for cord-cutters 18-34').",
                },
                "session_id": {
                    "type": "string",
                    "description": "Optional session ID for multi-turn conversations. Omit for new sessions.",
                },
                "context": {
                    "type": "string",
                    "description": "Optional additional context (e.g. RFP text, account data).",
                },
                "async": {
                    "type": "boolean",
                    "description": "If false, wait for full response (no timeout). Default: true.",
                },
            },
            "required": ["agent_name", "prompt"],
        },
    },
    {
        "name": "get_agent_conversation",
        "description": "Poll for results of a previously started agent conversation. Long-polls for up to 55s. Use the session_id returned by invoke_agent when status was 'processing'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID returned by invoke_agent.",
                },
                "agent_name": {
                    "type": "string",
                    "description": "Agent name that was invoked (from the invoke_agent call). Helps locate results faster.",
                },
                "no_wait": {
                    "type": "boolean",
                    "description": "If true, return current status immediately without long-polling. Default: false.",
                },
            },
            "required": ["session_id"],
        },
    },
]


# ============================================================================
# Deployer Class
# ============================================================================


class A4AMCPHandlerDeployer:
    """Deploy A4A MCP Handler as Target 2 on existing AdCP Gateway."""

    def __init__(self, stack_prefix: str, unique_id: str, region: str = "us-west-2", profile: str = None):
        self.stack_prefix = stack_prefix
        self.unique_id = unique_id
        self.region = region
        self.profile = profile

        # Create boto3 session
        if profile:
            logger.info(f"Using AWS profile: {profile}")
            session = boto3.Session(profile_name=profile, region_name=region)
        else:
            env_profile = os.environ.get("AWS_PROFILE")
            if env_profile:
                logger.info(f"Using AWS profile from environment: {env_profile}")
                session = boto3.Session(profile_name=env_profile, region_name=region)
            else:
                session = boto3.Session(region_name=region)

        self.lambda_client = session.client("lambda")
        self.iam_client = session.client("iam")
        self.sts_client = session.client("sts")
        self.ssm_client = session.client("ssm")
        self.account_id = self.sts_client.get_caller_identity()["Account"]
        self._session = session

        logger.info(f"Authenticated to AWS account: {self.account_id}")

        # Naming
        self.lambda_name = f"{stack_prefix}-a4a-mcp-handler-{unique_id}"
        self.role_name = f"{stack_prefix}-a4a-mcp-role-{unique_id}"
        self.target_name = "adcp"
        self.gateway_name = f"{stack_prefix}-ads-gw-{unique_id}"

    # ─── Gateway Discovery ─────────────────────────────────────────────

    def discover_gateway(self) -> dict:
        """Auto-discover existing gateway from .ads-gw-*.json or SSM."""
        # Try 1: .ads-gw-*.json config file in repo root
        repo_root = Path(__file__).parent.parent.parent
        pattern = str(repo_root / f".ads-gw-{self.stack_prefix}-{self.unique_id}.json")
        matches = glob.glob(pattern)
        if matches:
            with open(matches[0]) as f:
                config = json.load(f)
            gw = config.get("gateway_result", {})
            if gw.get("gateway_id") and gw.get("gateway_url"):
                logger.info(f"Discovered gateway from config file: {matches[0]}")
                return {
                    "gateway_id": gw["gateway_id"],
                    "gateway_url": gw["gateway_url"],
                    "gateway_arn": gw.get("gateway_arn", ""),
                    "role_arn": gw.get("role_arn", ""),
                }

        # Try 2: SSM Parameter Store
        try:
            param_name = f"/{self.stack_prefix}/adcp_gateway/{self.unique_id}"
            response = self.ssm_client.get_parameter(Name=param_name)
            gateway_url = response["Parameter"]["Value"]
            # Extract gateway_id from URL
            match = re.search(r"https://([^.]+)\.gateway", gateway_url)
            gateway_id = match.group(1) if match else ""
            logger.info(f"Discovered gateway from SSM: {param_name}")
            return {
                "gateway_id": gateway_id,
                "gateway_url": gateway_url,
                "gateway_arn": f"arn:aws:bedrock-agentcore:{self.region}:{self.account_id}:gateway/{gateway_id}",
                "role_arn": "",
            }
        except ClientError:
            pass

        # Try 3: List gateways via CLI
        try:
            env = os.environ.copy()
            if self.profile:
                env["AWS_PROFILE"] = self.profile
            cmd = ["aws", "bedrock-agentcore-control", "list-gateways", "--region", self.region]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
            if result.returncode == 0:
                data = json.loads(result.stdout)
                for gw in data.get("items", []):
                    if gw.get("name") == self.gateway_name:
                        gateway_id = gw["gatewayId"]
                        logger.info(f"Discovered gateway via CLI: {gateway_id}")
                        return {
                            "gateway_id": gateway_id,
                            "gateway_url": f"https://{gateway_id}.gateway.bedrock-agentcore.{self.region}.amazonaws.com/mcp",
                            "gateway_arn": f"arn:aws:bedrock-agentcore:{self.region}:{self.account_id}:gateway/{gateway_id}",
                            "role_arn": "",
                        }
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
            pass

        return {}

    # ─── Runtime ARN Discovery ─────────────────────────────────────────

    def discover_runtime_arn(self) -> str:
        """Discover the guidance runtime ARN from .agentcore-agents-*.json."""
        repo_root = Path(__file__).parent.parent.parent
        pattern = str(repo_root / f".agentcore-agents-{self.stack_prefix}-{self.unique_id}.json")
        matches = glob.glob(pattern)
        if matches:
            with open(matches[0]) as f:
                config = json.load(f)
            for agent in config.get("deployed_agents", []):
                # The guidance agent (AdFabricAgent) is the primary runtime
                if "adfabricagent" in agent.get("name", "").lower() or "adfabric" in agent.get("runtime_name", "").lower():
                    arn = agent.get("runtime_arn", "")
                    if arn:
                        logger.info(f"Discovered runtime ARN: {arn}")
                        return arn
            # Fallback: first agent
            if config.get("deployed_agents"):
                return config["deployed_agents"][0].get("runtime_arn", "")
        return ""

    # ─── DynamoDB Table Discovery ──────────────────────────────────────

    def discover_config_table(self) -> str:
        """Discover the AgentConfigTable name."""
        # Convention: {stack_prefix}-AgentConfig-{unique_id}
        table_name = f"{self.stack_prefix}-AgentConfig-{self.unique_id}"
        # Verify it exists
        try:
            dynamodb = self._session.client("dynamodb")
            dynamodb.describe_table(TableName=table_name)
            logger.info(f"Discovered config table: {table_name}")
            return table_name
        except ClientError:
            # Try without hyphen
            alt_name = f"{self.stack_prefix}AgentConfig{self.unique_id}"
            try:
                dynamodb.describe_table(TableName=alt_name)
                logger.info(f"Discovered config table: {alt_name}")
                return alt_name
            except ClientError:
                logger.warning(f"Could not verify table. Using convention: {table_name}")
                return table_name

    # ─── IAM Role ──────────────────────────────────────────────────────

    def create_execution_role(self, runtime_arn: str, table_name: str) -> str:
        """Create least-privilege IAM role for the Lambda."""
        trust_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }

        # Create role
        try:
            response = self.iam_client.create_role(
                RoleName=self.role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description="Execution role for A4A MCP Handler Lambda",
            )
            role_arn = response["Role"]["Arn"]
            logger.info(f"Created IAM role: {role_arn}")
        except self.iam_client.exceptions.EntityAlreadyExistsException:
            response = self.iam_client.get_role(RoleName=self.role_name)
            role_arn = response["Role"]["Arn"]
            logger.info(f"IAM role already exists: {role_arn}")

        # Inline policy — least privilege
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "InvokeAgentRuntime",
                    "Effect": "Allow",
                    "Action": ["bedrock-agentcore:InvokeAgentRuntime"],
                    "Resource": [
                        runtime_arn,
                        f"{runtime_arn}/runtime-endpoint/*",
                    ] if runtime_arn else [
                        f"arn:aws:bedrock-agentcore:{self.region}:{self.account_id}:runtime/*",
                    ],
                },
                {
                    "Sid": "AgentCoreMemoryRead",
                    "Effect": "Allow",
                    "Action": [
                        "bedrock-agentcore:GetMemory",
                        "bedrock-agentcore:GetMemoryEvent",
                        "bedrock-agentcore:ListMemoryEvents",
                        "bedrock-agentcore:ListEvents",
                        "bedrock-agentcore:SearchMemory",
                    ],
                    "Resource": [
                        f"arn:aws:bedrock-agentcore:{self.region}:{self.account_id}:memory/*",
                    ],
                },
                {
                    "Sid": "DynamoDBReadOnly",
                    "Effect": "Allow",
                    "Action": ["dynamodb:Scan", "dynamodb:GetItem", "dynamodb:Query"],
                    "Resource": [
                        f"arn:aws:dynamodb:{self.region}:{self.account_id}:table/{table_name}",
                        f"arn:aws:dynamodb:{self.region}:{self.account_id}:table/{table_name}/index/*",
                    ],
                },
                {
                    "Sid": "CloudWatchLogs",
                    "Effect": "Allow",
                    "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                    "Resource": [f"arn:aws:logs:{self.region}:{self.account_id}:log-group:/aws/lambda/{self.lambda_name}:*"],
                },
                {
                    "Sid": "CloudWatchMetrics",
                    "Effect": "Allow",
                    "Action": ["cloudwatch:PutMetricData"],
                    "Resource": ["*"],
                    "Condition": {
                        "StringEquals": {"cloudwatch:namespace": "A4A/MCPHandler"}
                    },
                },
            ],
        }

        self.iam_client.put_role_policy(
            RoleName=self.role_name,
            PolicyName="a4a-mcp-handler-policy",
            PolicyDocument=json.dumps(policy),
        )
        logger.info("Applied inline policy to role")

        return role_arn

    # ─── Lambda Deployment ─────────────────────────────────────────────

    def package_lambda(self) -> bytes:
        """Package the Lambda handler as a zip file."""
        lambda_dir = Path(__file__).parent.parent.parent / "lambda"
        handler_file = lambda_dir / "a4a_mcp_handler.py"

        if not handler_file.exists():
            raise FileNotFoundError(f"Lambda handler not found: {handler_file}")

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # Add handler as lambda_function.py (Lambda expects this name)
            zf.write(handler_file, "lambda_function.py")
        return buf.getvalue()

    def deploy_lambda(self, role_arn: str, runtime_arn: str, table_name: str) -> str:
        """Deploy or update the Lambda function."""
        code_zip = self.package_lambda()

        env_vars = {
            "AGENT_CONFIG_TABLE": table_name,
            "GUIDANCE_RUNTIME_ARN": runtime_arn,
            "STACK_PREFIX": self.stack_prefix,
            "UNIQUE_ID": self.unique_id,
            "A4A_REGION": self.region,
            "AGENTCORE_MEMORY_ID": os.environ.get("AGENTCORE_MEMORY_ID", "a4amemoryomixaj-5hIXD69srX"),
        }

        try:
            response = self.lambda_client.create_function(
                FunctionName=self.lambda_name,
                Runtime="python3.11",
                Role=role_arn,
                Handler="lambda_function.handler",
                Code={"ZipFile": code_zip},
                Description="A4A MCP Handler — agent invocation tools for MCP Gateway",
                Timeout=300,  # 5 min for orchestrator agents
                MemorySize=256,
                Environment={"Variables": env_vars},
            )
            logger.info(f"Created Lambda: {response['FunctionArn']}")

            # Wait for active
            waiter = self.lambda_client.get_waiter("function_active")
            waiter.wait(FunctionName=self.lambda_name)

            return response["FunctionArn"]

        except self.lambda_client.exceptions.ResourceConflictException:
            # Update existing
            self.lambda_client.update_function_code(
                FunctionName=self.lambda_name, ZipFile=code_zip
            )
            # Wait for code update to complete before updating config
            logger.info("Waiting for code update to complete...")
            waiter = self.lambda_client.get_waiter("function_updated")
            waiter.wait(FunctionName=self.lambda_name)
            # Now update env vars and timeout
            self.lambda_client.update_function_configuration(
                FunctionName=self.lambda_name,
                Timeout=300,
                Environment={"Variables": env_vars},
            )
            waiter.wait(FunctionName=self.lambda_name)
            response = self.lambda_client.get_function(FunctionName=self.lambda_name)
            arn = response["Configuration"]["FunctionArn"]
            logger.info(f"Updated existing Lambda: {arn}")
            return arn

    # ─── Gateway Target Registration ──────────────────────────────────

    def _get_aws_cli(self) -> str:
        """Find a working AWS CLI binary (prefer v2 over broken v1)."""
        # Try common v2 locations first
        for path in ["/opt/homebrew/bin/aws", "/usr/local/bin/aws"]:
            if os.path.exists(path):
                try:
                    result = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10)
                    if result.returncode == 0 and "aws-cli/2" in result.stdout:
                        return path
                except (subprocess.TimeoutExpired, OSError):
                    continue
        # Fallback to PATH
        return "aws"

    def register_target(self, gateway_id: str, lambda_arn: str) -> dict:
        """Register Lambda as Target 2 on the gateway with tool schemas."""
        logger.info(f"Registering target: {self.target_name}")

        target_config = {
            "mcp": {
                "lambda": {
                    "lambdaArn": lambda_arn,
                    "toolSchema": {"inlinePayload": A4A_MCP_TOOL_SCHEMA},
                }
            }
        }

        credential_config = [{"credentialProviderType": "GATEWAY_IAM_ROLE"}]

        env = os.environ.copy()
        if self.profile:
            env["AWS_PROFILE"] = self.profile

        aws_cli = self._get_aws_cli()
        cmd = [
            aws_cli, "bedrock-agentcore-control", "create-gateway-target",
            "--gateway-identifier", gateway_id,
            "--name", self.target_name,
            "--description", "A4A MCP Handler — agent discovery and invocation tools",
            "--target-configuration", json.dumps(target_config),
            "--credential-provider-configurations", json.dumps(credential_config),
            "--region", self.region,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)

            if result.returncode != 0:
                if "ConflictException" in result.stderr or "already exists" in result.stderr.lower():
                    logger.info(f"Target already exists: {self.target_name} — updating tool schema...")
                    return self._update_target(gateway_id, lambda_arn, env)
                if "AccessDeniedException" in result.stderr:
                    logger.warning(f"Permission denied for CreateGatewayTarget.")
                    return {"status": "permission_denied", "message": result.stderr}
                logger.error(f"Target creation failed: {result.stderr}")
                return {"status": "error", "message": result.stderr}

            logger.info("Target registered successfully")
            try:
                response_data = json.loads(result.stdout)
                return {"status": "success", "target_id": response_data.get("targetId")}
            except json.JSONDecodeError:
                return {"status": "success", "output": result.stdout}

        except subprocess.TimeoutExpired:
            return {"status": "timeout"}
        except FileNotFoundError:
            return {"status": "cli_not_found", "message": "AWS CLI not found"}

    def _update_target(self, gateway_id: str, lambda_arn: str, env: dict) -> dict:
        """Update an existing gateway target with the current tool schema."""
        # First, discover the actual target ID by listing targets
        target_id = self._find_target_id(gateway_id, env)
        if not target_id:
            logger.error(f"Could not find target ID for {self.target_name}")
            return {"status": "error", "message": f"Target '{self.target_name}' exists but could not resolve its ID"}

        target_config = {
            "mcp": {
                "lambda": {
                    "lambdaArn": lambda_arn,
                    "toolSchema": {"inlinePayload": A4A_MCP_TOOL_SCHEMA},
                }
            }
        }

        aws_cli = self._get_aws_cli()
        cmd = [
            aws_cli, "bedrock-agentcore-control", "update-gateway-target",
            "--gateway-identifier", gateway_id,
            "--target-id", target_id,
            "--name", self.target_name,
            "--description", "A4A MCP Handler — agent discovery and invocation tools",
            "--target-configuration", json.dumps(target_config),
            "--credential-provider-configurations", json.dumps([{"credentialProviderType": "GATEWAY_IAM_ROLE"}]),
            "--region", self.region,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
            if result.returncode != 0:
                logger.error(f"Target update failed: {result.stderr}")
                return {"status": "error", "message": result.stderr}
            logger.info("Target updated successfully with current tool schema")
            return {"status": "success", "updated": True}
        except subprocess.TimeoutExpired:
            return {"status": "timeout"}
        except FileNotFoundError:
            return {"status": "cli_not_found", "message": "AWS CLI not found"}

    def _find_target_id(self, gateway_id: str, env: dict) -> str:
        """List gateway targets and find the target ID matching self.target_name."""
        aws_cli = self._get_aws_cli()
        cmd = [
            aws_cli, "bedrock-agentcore-control", "list-gateway-targets",
            "--gateway-identifier", gateway_id,
            "--region", self.region,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
            if result.returncode == 0:
                data = json.loads(result.stdout)
                for target in data.get("items", []):
                    if target.get("name") == self.target_name:
                        target_id = target.get("targetId", "")
                        logger.info(f"Found target ID: {target_id} for {self.target_name}")
                        return target_id
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"Failed to list targets: {e}")
        return ""

    # ─── Update Gateway Role ───────────────────────────────────────────

    def update_gateway_role(self, gateway_config: dict, lambda_arn: str):
        """Update the gateway role to allow invoking the new Lambda."""
        role_arn = gateway_config.get("role_arn", "")
        if not role_arn:
            # Try to find the gateway role by convention
            gateway_role_name = f"{self.stack_prefix}-ads-gw-role-{self.unique_id}"
            try:
                response = self.iam_client.get_role(RoleName=gateway_role_name)
                role_arn = response["Role"]["Arn"]
            except ClientError:
                logger.warning("Could not find gateway role to update. Target may not be invocable.")
                return

        # Extract role name from ARN
        role_name = role_arn.split("/")[-1]

        # Get existing policy and add the new Lambda ARN
        try:
            policy_response = self.iam_client.get_role_policy(
                RoleName=role_name, PolicyName="gateway-lambda-invoke"
            )
            policy = json.loads(policy_response["PolicyDocument"]) if isinstance(
                policy_response["PolicyDocument"], str
            ) else policy_response["PolicyDocument"]
        except ClientError:
            # Create new policy
            policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": "lambda:InvokeFunction",
                        "Resource": [],
                    }
                ],
            }

        # Add new Lambda ARN to the resource list
        for stmt in policy.get("Statement", []):
            if "lambda:InvokeFunction" in stmt.get("Action", []) or stmt.get("Action") == "lambda:InvokeFunction":
                resources = stmt.get("Resource", [])
                if isinstance(resources, str):
                    resources = [resources]
                if lambda_arn not in resources:
                    resources.append(lambda_arn)
                stmt["Resource"] = resources
                break
        else:
            policy["Statement"].append({
                "Effect": "Allow",
                "Action": "lambda:InvokeFunction",
                "Resource": [lambda_arn],
            })

        self.iam_client.put_role_policy(
            RoleName=role_name,
            PolicyName="gateway-lambda-invoke",
            PolicyDocument=json.dumps(policy),
        )
        logger.info(f"Updated gateway role {role_name} with new Lambda ARN")

    # ─── Full Deploy ───────────────────────────────────────────────────

    def deploy(self) -> dict:
        """Full deployment: discover → role → lambda → target."""
        results = {"stack_prefix": self.stack_prefix, "unique_id": self.unique_id, "region": self.region}

        # Step 1: Discover gateway
        logger.info("=" * 60)
        logger.info("Step 1: Discovering existing gateway")
        logger.info("=" * 60)
        gateway_config = self.discover_gateway()
        if not gateway_config:
            logger.error("Gateway not found. Run deploy-ecosystem.sh Phase 6 first.")
            logger.error(f"Expected gateway name: {self.gateway_name}")
            results["status"] = "gateway_not_found"
            return results
        results["gateway"] = gateway_config

        # Step 2: Discover runtime ARN and config table
        logger.info("=" * 60)
        logger.info("Step 2: Discovering runtime ARN and config table")
        logger.info("=" * 60)
        runtime_arn = self.discover_runtime_arn()
        table_name = self.discover_config_table()
        if not runtime_arn:
            logger.error("Runtime ARN not found. Check .agentcore-agents-*.json exists.")
            results["status"] = "runtime_not_found"
            return results
        results["runtime_arn"] = runtime_arn
        results["config_table"] = table_name

        # Step 3: Create IAM role
        logger.info("=" * 60)
        logger.info("Step 3: Creating IAM execution role")
        logger.info("=" * 60)
        role_arn = self.create_execution_role(runtime_arn, table_name)
        results["role_arn"] = role_arn

        # Step 4: Deploy Lambda
        logger.info("=" * 60)
        logger.info("Step 4: Deploying Lambda function")
        logger.info("=" * 60)
        lambda_arn = self.deploy_lambda(role_arn, runtime_arn, table_name)
        results["lambda_arn"] = lambda_arn

        # Step 5: Update gateway role
        logger.info("=" * 60)
        logger.info("Step 5: Updating gateway role")
        logger.info("=" * 60)
        self.update_gateway_role(gateway_config, lambda_arn)

        # Step 6: Register target
        logger.info("=" * 60)
        logger.info("Step 6: Registering gateway target")
        logger.info("=" * 60)
        target_result = self.register_target(gateway_config["gateway_id"], lambda_arn)
        results["target_result"] = target_result

        if target_result.get("status") != "success":
            logger.error(f"Target registration failed: {target_result}")
            results["status"] = "target_failed"
            return results

        results["status"] = "success"

        # Print summary
        gateway_url = gateway_config["gateway_url"]
        logger.info("")
        logger.info("=" * 60)
        logger.info("DEPLOYMENT COMPLETE")
        logger.info("=" * 60)
        logger.info(f"Lambda ARN: {lambda_arn}")
        logger.info(f"Gateway URL: {gateway_url}")
        logger.info(f"Target: {self.target_name}")
        logger.info("")
        logger.info("=" * 60)
        logger.info("CONNECT FROM KIRO / QUICK DESKTOP")
        logger.info("=" * 60)
        logger.info("")
        logger.info("Add to ~/.kiro/settings/mcp.json:")
        logger.info("")
        mcp_config = {
            "a4a-agents": {
                "command": "uvx",
                "timeout": 300000,
                "args": [
                    "mcp-proxy-for-aws@latest",
                    gateway_url,
                    "--metadata", f"AWS_REGION={self.region}",
                ],
                "env": {"AWS_PROFILE": self.profile or "<YOUR_PROFILE>"},
            }
        }
        logger.info(json.dumps({"mcpServers": mcp_config}, indent=2))
        logger.info("")

        return results


# ============================================================================
# CLI Entry Point
# ============================================================================


# ============================================================================
# OAuth Gateway Deployer (Default Path)
# ============================================================================


class OAuthGatewayDeployer:
    """Deploy OAuth MCP Gateway with Cognito CUSTOM_JWT authentication.
    
    Creates a second gateway (alongside the existing IAM gateway) that
    business users can connect to from Quick Suite web using OAuth credentials.
    Reuses the existing Cognito User Pool and Lambda targets.
    """

    def __init__(self, stack_prefix: str, unique_id: str, region: str = "us-west-2", profile: str = None):
        self.stack_prefix = stack_prefix
        self.unique_id = unique_id
        self.region = region
        self.profile = profile

        # Create boto3 session
        if profile:
            session = boto3.Session(profile_name=profile, region_name=region)
        else:
            env_profile = os.environ.get("AWS_PROFILE")
            if env_profile:
                session = boto3.Session(profile_name=env_profile, region_name=region)
            else:
                session = boto3.Session(region_name=region)

        self.iam_client = session.client("iam")
        self.cognito_client = session.client("cognito-idp")
        self.cfn_client = session.client("cloudformation")
        self.sts_client = session.client("sts")
        self.account_id = self.sts_client.get_caller_identity()["Account"]
        self._session = session

        logger.info(f"OAuthGatewayDeployer: Authenticated to account {self.account_id}")

        # Naming
        self.gateway_name = f"{stack_prefix}-oauth-gw-{unique_id}"
        self.role_name = f"{stack_prefix}-oauth-gw-role-{unique_id}"
        self.app_client_name = f"{stack_prefix}-oauth-gw-client-{unique_id}"
        self.resource_server_id = f"{stack_prefix}-oauth-gw-{unique_id}"
        self.scope_name = "invoke"

    # ─── Discovery ─────────────────────────────────────────────────────

    def discover_user_pool_id(self) -> str:
        """Discover Cognito User Pool ID from CloudFormation stack outputs."""
        stack_name = f"{self.stack_prefix}-infrastructure-core"
        try:
            response = self.cfn_client.describe_stacks(StackName=stack_name)
            for output in response["Stacks"][0].get("Outputs", []):
                if output["OutputKey"] == "UserPoolId":
                    logger.info(f"Discovered User Pool ID: {output['OutputValue']}")
                    return output["OutputValue"]
        except ClientError as e:
            logger.warning(f"Could not query CloudFormation stack {stack_name}: {e}")

        # Fallback: list user pools and match by name
        try:
            response = self.cognito_client.list_user_pools(MaxResults=60)
            target_name = f"{self.stack_prefix}-users-{self.unique_id}"
            for pool in response.get("UserPools", []):
                if pool["Name"] == target_name:
                    logger.info(f"Discovered User Pool by name: {pool['Id']}")
                    return pool["Id"]
        except ClientError as e:
            logger.warning(f"Could not list user pools: {e}")

        return ""

    def discover_lambda_arns(self) -> dict:
        """Read existing Lambda ARNs from config files."""
        repo_root = Path(__file__).parent.parent.parent
        arns = {}

        # AdCP handler ARN from .ads-gw config
        ads_gw_config = repo_root / f".ads-gw-{self.stack_prefix}-{self.unique_id}.json"
        if ads_gw_config.exists():
            with open(ads_gw_config) as f:
                config = json.load(f)
            arns["adcp_handler"] = config.get("lambda_arn", "")
            logger.info(f"Discovered adcp-handler ARN: {arns['adcp_handler']}")

        # A4A MCP handler ARN
        mcp_config = repo_root / f".a4a-mcp-handler-{self.stack_prefix}-{self.unique_id}.json"
        if mcp_config.exists():
            with open(mcp_config) as f:
                config = json.load(f)
            arns["a4a_mcp_handler"] = config.get("lambda_arn", "")
            logger.info(f"Discovered a4a-mcp-handler ARN: {arns['a4a_mcp_handler']}")

        return arns

    def validate_prerequisites(self):
        """Validate all prerequisites before any resource creation."""
        repo_root = Path(__file__).parent.parent.parent

        if not (repo_root / f".ads-gw-{self.stack_prefix}-{self.unique_id}.json").exists():
            logger.error("IAM gateway config not found. Run deploy-ecosystem.sh Phase 6 first.")
            sys.exit(1)

        if not (repo_root / f".a4a-mcp-handler-{self.stack_prefix}-{self.unique_id}.json").exists():
            logger.error("A4A MCP handler config not found. Run deploy_a4a_mcp_handler.py --iam-target first.")
            sys.exit(1)

        user_pool_id = self.discover_user_pool_id()
        if not user_pool_id:
            logger.error("Cognito User Pool not found. Check CloudFormation stack outputs.")
            sys.exit(1)

        return user_pool_id

    # ─── Cognito Setup ─────────────────────────────────────────────────

    def ensure_cognito_domain(self, user_pool_id: str) -> str:
        """Create Cognito domain if not exists. Returns domain prefix."""
        # Check if domain already exists
        try:
            response = self.cognito_client.describe_user_pool(UserPoolId=user_pool_id)
            existing_domain = response["UserPool"].get("Domain", "")
            if existing_domain:
                logger.info(f"Cognito domain already exists: {existing_domain}")
                return existing_domain
        except ClientError:
            pass

        # Create domain
        domain = f"{self.stack_prefix}-{self.unique_id}"
        try:
            self.cognito_client.create_user_pool_domain(
                UserPoolId=user_pool_id,
                Domain=domain,
            )
            logger.info(f"Created Cognito domain: {domain}")
            return domain
        except ClientError as e:
            if "InvalidParameterException" in str(e) or "already exists" in str(e).lower():
                # Try alternate pattern
                domain = f"{self.stack_prefix}-gw-{self.unique_id}"
                try:
                    self.cognito_client.create_user_pool_domain(
                        UserPoolId=user_pool_id,
                        Domain=domain,
                    )
                    logger.info(f"Created Cognito domain (alternate): {domain}")
                    return domain
                except ClientError:
                    pass
            logger.error(f"Could not create Cognito domain: {e}")
            raise

    def create_resource_server(self, user_pool_id: str) -> dict:
        """Create Cognito Resource Server with gateway/invoke scope."""
        try:
            response = self.cognito_client.create_resource_server(
                UserPoolId=user_pool_id,
                Identifier=self.resource_server_id,
                Name="A4A OAuth Gateway",
                Scopes=[
                    {"ScopeName": self.scope_name, "ScopeDescription": "Invoke MCP tools on the A4A OAuth Gateway"}
                ],
            )
            logger.info(f"Created Resource Server: {self.resource_server_id}")
            return response["ResourceServer"]
        except ClientError as e:
            if "already exists" in str(e).lower() or "ResourceAlreadyExistsException" in str(type(e).__name__):
                logger.info(f"Resource Server already exists: {self.resource_server_id}")
                return {"Identifier": self.resource_server_id}
            raise

    def create_app_client(self, user_pool_id: str) -> dict:
        """Create App Client with client_credentials flow. Returns {client_id, client_secret}."""
        # Check if client already exists
        try:
            response = self.cognito_client.list_user_pool_clients(UserPoolId=user_pool_id, MaxResults=60)
            for client in response.get("UserPoolClients", []):
                if client["ClientName"] == self.app_client_name:
                    # Retrieve full details including secret
                    detail = self.cognito_client.describe_user_pool_client(
                        UserPoolId=user_pool_id, ClientId=client["ClientId"]
                    )
                    client_data = detail["UserPoolClient"]
                    logger.info(f"App Client already exists: {client_data['ClientId']}")
                    return {
                        "client_id": client_data["ClientId"],
                        "client_secret": client_data.get("ClientSecret", ""),
                    }
        except ClientError:
            pass

        # Create new client — needs both code + client_credentials flows
        # code flow: for Quick Suite web (user login via hosted UI)
        # client_credentials: for service-to-service (programmatic access)
        scope_custom = f"{self.resource_server_id}/{self.scope_name}"
        response = self.cognito_client.create_user_pool_client(
            UserPoolId=user_pool_id,
            ClientName=self.app_client_name,
            GenerateSecret=True,
            SupportedIdentityProviders=["COGNITO"],
            AllowedOAuthFlows=["client_credentials", "code"],
            AllowedOAuthScopes=[scope_custom, "openid", "email", "profile"],
            AllowedOAuthFlowsUserPoolClient=True,
            CallbackURLs=[
                "https://us-east-1.quicksight.aws.amazon.com/sn/oauthcallback",
                "http://localhost:3000/callback",
            ],
        )
        client_data = response["UserPoolClient"]
        logger.info(f"Created App Client: {client_data['ClientId']}")
        return {
            "client_id": client_data["ClientId"],
            "client_secret": client_data.get("ClientSecret", ""),
        }

    # ─── Gateway Creation ──────────────────────────────────────────────

    def create_oauth_gateway_role(self, lambda_arns: list) -> str:
        """Create IAM role for OAuth gateway to invoke both Lambdas."""
        trust_policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }],
        }

        try:
            response = self.iam_client.create_role(
                RoleName=self.role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description="IAM role for A4A OAuth MCP Gateway",
            )
            role_arn = response["Role"]["Arn"]
            logger.info(f"Created OAuth gateway role: {role_arn}")
            # Wait for IAM propagation only on new role creation
            import time
            logger.info("Waiting 10s for IAM propagation...")
            time.sleep(10)
        except self.iam_client.exceptions.EntityAlreadyExistsException:
            response = self.iam_client.get_role(RoleName=self.role_name)
            role_arn = response["Role"]["Arn"]
            logger.info(f"OAuth gateway role already exists: {role_arn}")

        # Attach Lambda invoke policy
        policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Action": "lambda:InvokeFunction",
                "Resource": lambda_arns,
            }],
        }
        self.iam_client.put_role_policy(
            RoleName=self.role_name,
            PolicyName="oauth-gw-lambda-invoke",
            PolicyDocument=json.dumps(policy),
        )
        return role_arn

    def create_oauth_gateway(self, user_pool_id: str, role_arn: str, app_client_id: str) -> dict:
        """Create AgentCore gateway with CUSTOM_JWT authorizer."""
        # Check if gateway already exists
        env = os.environ.copy()
        if self.profile:
            env["AWS_PROFILE"] = self.profile

        aws_cli = self._get_aws_cli()

        # List gateways to check for existing
        try:
            cmd = [aws_cli, "bedrock-agentcore-control", "list-gateways", "--region", self.region]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
            if result.returncode == 0:
                data = json.loads(result.stdout)
                for gw in data.get("items", []):
                    if gw.get("name") == self.gateway_name:
                        gateway_id = gw["gatewayId"]
                        logger.info(f"OAuth gateway already exists: {gateway_id}")
                        return {
                            "gateway_id": gateway_id,
                            "gateway_url": f"https://{gateway_id}.gateway.bedrock-agentcore.{self.region}.amazonaws.com/mcp",
                            "gateway_arn": f"arn:aws:bedrock-agentcore:{self.region}:{self.account_id}:gateway/{gateway_id}",
                        }
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
            pass

        # Create gateway
        discovery_url = f"https://cognito-idp.{self.region}.amazonaws.com/{user_pool_id}/.well-known/openid-configuration"

        authorizer_config = {
            "customJWTAuthorizer": {
                "discoveryUrl": discovery_url,
                "allowedClients": [app_client_id],
            }
        }

        cmd = [
            aws_cli, "bedrock-agentcore-control", "create-gateway",
            "--name", self.gateway_name,
            "--protocol-type", "MCP",
            "--authorizer-type", "CUSTOM_JWT",
            "--authorizer-configuration", json.dumps(authorizer_config),
            "--role-arn", role_arn,
            "--description", "OAuth MCP Gateway for Quick Suite web business users",
            "--region", self.region,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)
            if result.returncode != 0:
                if "ConflictException" in result.stderr or "already exists" in result.stderr.lower():
                    logger.info("OAuth gateway already exists (conflict)")
                    return {}
                logger.error(f"Gateway creation failed: {result.stderr}")
                return {}

            response_data = json.loads(result.stdout)
            gateway_id = response_data.get("gatewayId", "")
            gateway_url = response_data.get("gatewayUrl", f"https://{gateway_id}.gateway.bedrock-agentcore.{self.region}.amazonaws.com/mcp")
            logger.info(f"Created OAuth gateway: {gateway_id}")
            # Wait for new gateway to become ready
            import time
            logger.info("Waiting 15s for gateway to become ready...")
            time.sleep(15)
            return {
                "gateway_id": gateway_id,
                "gateway_url": gateway_url,
                "gateway_arn": response_data.get("gatewayArn", ""),
            }
        except subprocess.TimeoutExpired:
            logger.error("Gateway creation timed out")
            return {}
        except FileNotFoundError:
            logger.error("AWS CLI not found")
            return {}

    def register_target(self, gateway_id: str, target_name: str, lambda_arn: str, tool_schema: list) -> dict:
        """Register a Lambda target on the OAuth gateway."""
        env = os.environ.copy()
        if self.profile:
            env["AWS_PROFILE"] = self.profile

        aws_cli = self._get_aws_cli()

        target_config = {
            "mcp": {
                "lambda": {
                    "lambdaArn": lambda_arn,
                    "toolSchema": {"inlinePayload": tool_schema},
                }
            }
        }

        credential_config = [{"credentialProviderType": "GATEWAY_IAM_ROLE"}]

        cmd = [
            aws_cli, "bedrock-agentcore-control", "create-gateway-target",
            "--gateway-identifier", gateway_id,
            "--name", target_name,
            "--description", f"Lambda target: {target_name}",
            "--target-configuration", json.dumps(target_config),
            "--credential-provider-configurations", json.dumps(credential_config),
            "--region", self.region,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
            if result.returncode != 0:
                if "ConflictException" in result.stderr or "already exists" in result.stderr.lower():
                    logger.info(f"Target already exists: {target_name}")
                    return {"status": "success", "already_existed": True}
                logger.error(f"Target creation failed: {result.stderr}")
                return {"status": "error", "message": result.stderr}
            logger.info(f"Registered target: {target_name}")
            return {"status": "success"}
        except subprocess.TimeoutExpired:
            return {"status": "timeout"}
        except FileNotFoundError:
            return {"status": "cli_not_found"}

    def _get_aws_cli(self) -> str:
        """Find a working AWS CLI binary."""
        for path in ["/opt/homebrew/bin/aws", "/usr/local/bin/aws"]:
            if os.path.exists(path):
                try:
                    result = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10)
                    if result.returncode == 0 and "aws-cli/2" in result.stdout:
                        return path
                except (subprocess.TimeoutExpired, OSError):
                    continue
        return "aws"

    # ─── Orchestration ─────────────────────────────────────────────────

    def deploy(self) -> dict:
        """Full OAuth gateway deployment."""
        results = {"stack_prefix": self.stack_prefix, "unique_id": self.unique_id, "region": self.region}

        # Step 1: Validate prerequisites
        logger.info("=" * 60)
        logger.info("Step 1: Validating prerequisites")
        logger.info("=" * 60)
        user_pool_id = self.validate_prerequisites()
        lambda_arns = self.discover_lambda_arns()
        if not lambda_arns.get("adcp_handler") or not lambda_arns.get("a4a_mcp_handler"):
            logger.error("Could not discover both Lambda ARNs")
            results["status"] = "lambda_arns_not_found"
            return results

        # Step 2: Cognito setup
        logger.info("=" * 60)
        logger.info("Step 2: Setting up Cognito (domain, resource server, app client)")
        logger.info("=" * 60)
        domain = self.ensure_cognito_domain(user_pool_id)
        self.create_resource_server(user_pool_id)
        app_client = self.create_app_client(user_pool_id)
        token_url = f"https://{domain}.auth.{self.region}.amazoncognito.com/oauth2/token"
        authorization_url = f"https://{domain}.auth.{self.region}.amazoncognito.com/oauth2/authorize"

        results["cognito"] = {
            "user_pool_id": user_pool_id,
            "app_client_id": app_client["client_id"],
            "domain": domain,
            "token_url": token_url,
            "authorization_url": authorization_url,
            "scope": f"{self.resource_server_id}/{self.scope_name}",
        }

        # Step 3: Create OAuth gateway role
        logger.info("=" * 60)
        logger.info("Step 3: Creating OAuth gateway IAM role")
        logger.info("=" * 60)
        all_lambda_arns = [lambda_arns["adcp_handler"], lambda_arns["a4a_mcp_handler"]]
        role_arn = self.create_oauth_gateway_role(all_lambda_arns)
        results["role_arn"] = role_arn

        # Step 4: Create OAuth gateway
        logger.info("=" * 60)
        logger.info("Step 4: Creating OAuth MCP Gateway")
        logger.info("=" * 60)
        gateway = self.create_oauth_gateway(user_pool_id, role_arn, app_client["client_id"])
        if not gateway.get("gateway_id"):
            logger.error("OAuth gateway creation failed")
            results["status"] = "gateway_creation_failed"
            return results
        results["oauth_gateway"] = gateway

        # Step 5: Register targets
        logger.info("=" * 60)
        logger.info("Step 5: Registering Lambda targets on OAuth gateway")
        logger.info("=" * 60)

        # Get tool schemas — import from sibling module
        try:
            import importlib.util
            spec_path = Path(__file__).parent / "deploy_adcp_gateway.py"
            spec_module = importlib.util.spec_from_file_location("deploy_adcp_gateway", spec_path)
            adcp_module = importlib.util.module_from_spec(spec_module)
            spec_module.loader.exec_module(adcp_module)
            adcp_deployer = adcp_module.AdCPGatewayDeployer(self.stack_prefix, self.unique_id, self.region, self.profile)
            adcp_schema = adcp_deployer.get_adcp_tool_schema()
        except Exception as e:
            logger.warning(f"Could not import AdCP tool schema: {e}. Using empty schema for AdCP target.")
            adcp_schema = []

        # Register AdCP target
        adcp_target_name = "adcp"
        self.register_target(gateway["gateway_id"], adcp_target_name, lambda_arns["adcp_handler"], adcp_schema)

        # Register A4A MCP handler target
        a4a_target_name = "agent"
        self.register_target(gateway["gateway_id"], a4a_target_name, lambda_arns["a4a_mcp_handler"], A4A_MCP_TOOL_SCHEMA)

        results["status"] = "success"

        # Output connection config
        logger.info("")
        logger.info("=" * 60)
        logger.info("DEPLOYMENT COMPLETE — OAuth Gateway")
        logger.info("=" * 60)
        logger.info(f"Gateway URL: {gateway['gateway_url']}")
        logger.info(f"Client ID: {app_client['client_id']}")
        logger.info(f"Client Secret: {app_client['client_secret'][:8]}...{app_client['client_secret'][-4:]}")
        logger.info(f"Token URL: {token_url}")
        logger.info(f"Scope: {self.resource_server_id}/{self.scope_name}")
        logger.info("")
        logger.info("=" * 60)
        logger.info("QUICK SUITE WEB — Connection Config")
        logger.info("=" * 60)
        logger.info("In Quick Suite web → Settings → Capabilities → MCP → Add:")
        logger.info(f"  Name: A4A Advertising Agents")
        logger.info(f"  URL: {gateway['gateway_url']}")
        logger.info(f"  Auth: Service authentication (OAuth)")
        logger.info(f"  Client ID: {app_client['client_id']}")
        logger.info(f"  Client Secret: {app_client['client_secret']}")
        logger.info(f"  Token URL: {token_url}")
        logger.info(f"  Authorization URL: {authorization_url}")
        logger.info(f"  Scope: {self.resource_server_id}/{self.scope_name}")
        logger.info("")

        # Save config (redact secret in file)
        results["connection_config"] = {
            "gateway_url": gateway["gateway_url"],
            "client_id": app_client["client_id"],
            "client_secret": "*** SEE CONSOLE OUTPUT ***",
            "token_url": token_url,
            "authorization_url": authorization_url,
            "scope": f"{self.resource_server_id}/{self.scope_name}",
        }

        return results


# ============================================================================
# External OAuth Gateway Deployer (Federate, Okta, Auth0, etc.)
# ============================================================================


class ExternalOAuthGatewayDeployer:
    """Deploy OAuth MCP Gateway with an external OIDC provider (no Cognito).

    Use this for Amazon Federate, Okta, Auth0, or any provider that exposes
    a .well-known/openid-configuration endpoint. Skips all Cognito setup.
    """

    def __init__(self, stack_prefix: str, unique_id: str, region: str = "us-west-2",
                 profile: str = None, well_known_url: str = "",
                 allowed_clients: list = None, gateway_name: str = None):
        self.stack_prefix = stack_prefix
        self.unique_id = unique_id
        self.region = region
        self.profile = profile
        self.well_known_url = well_known_url
        self.allowed_clients = allowed_clients or []
        self.custom_gateway_name = gateway_name

        # Create boto3 session
        if profile:
            session = boto3.Session(profile_name=profile, region_name=region)
        else:
            env_profile = os.environ.get("AWS_PROFILE")
            if env_profile:
                session = boto3.Session(profile_name=env_profile, region_name=region)
            else:
                session = boto3.Session(region_name=region)

        self.iam_client = session.client("iam")
        self.sts_client = session.client("sts")
        self.account_id = self.sts_client.get_caller_identity()["Account"]
        self._session = session

        logger.info(f"ExternalOAuthGatewayDeployer: Authenticated to account {self.account_id}")

        # Naming — use custom name or derive from well-known URL
        if self.custom_gateway_name:
            self.gateway_name = self.custom_gateway_name
        else:
            import urllib.parse
            parsed = urllib.parse.urlparse(well_known_url)
            domain_parts = parsed.hostname.split(".") if parsed.hostname else ["external"]
            short_name = domain_parts[1] if len(domain_parts) > 2 else domain_parts[0]
            self.gateway_name = f"{stack_prefix}-oauth-gw-{unique_id}-{short_name}"

        self.role_name = f"{stack_prefix}-oauth-gw-role-{unique_id}"  # Reuse same role

    def _get_aws_cli(self) -> str:
        """Find a working AWS CLI binary."""
        for path in ["/opt/homebrew/bin/aws", "/usr/local/bin/aws"]:
            if os.path.exists(path):
                try:
                    result = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10)
                    if result.returncode == 0 and "aws-cli/2" in result.stdout:
                        return path
                except (subprocess.TimeoutExpired, OSError):
                    continue
        return "aws"

    def discover_lambda_arns(self) -> dict:
        """Read existing Lambda ARNs from config files."""
        repo_root = Path(__file__).parent.parent.parent
        arns = {}

        ads_gw_config = repo_root / f".ads-gw-{self.stack_prefix}-{self.unique_id}.json"
        if ads_gw_config.exists():
            with open(ads_gw_config) as f:
                config = json.load(f)
            arns["adcp_handler"] = config.get("lambda_arn", "")

        mcp_config = repo_root / f".a4a-mcp-handler-{self.stack_prefix}-{self.unique_id}.json"
        if mcp_config.exists():
            with open(mcp_config) as f:
                config = json.load(f)
            arns["a4a_mcp_handler"] = config.get("lambda_arn", "")

        return arns

    def ensure_gateway_role(self, lambda_arns: list) -> str:
        """Reuse or create the OAuth gateway role."""
        trust_policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }],
        }

        try:
            response = self.iam_client.create_role(
                RoleName=self.role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description="IAM role for A4A OAuth MCP Gateway",
            )
            role_arn = response["Role"]["Arn"]
            logger.info(f"Created gateway role: {role_arn}")
            import time
            time.sleep(10)
        except self.iam_client.exceptions.EntityAlreadyExistsException:
            response = self.iam_client.get_role(RoleName=self.role_name)
            role_arn = response["Role"]["Arn"]
            logger.info(f"Gateway role already exists: {role_arn}")

        # Update policy to include all Lambda ARNs
        policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Action": "lambda:InvokeFunction",
                "Resource": lambda_arns,
            }],
        }
        self.iam_client.put_role_policy(
            RoleName=self.role_name,
            PolicyName="oauth-gw-lambda-invoke",
            PolicyDocument=json.dumps(policy),
        )
        return role_arn

    def create_gateway(self, role_arn: str) -> dict:
        """Create AgentCore gateway with external OIDC CUSTOM_JWT authorizer."""
        env = os.environ.copy()
        if self.profile:
            env["AWS_PROFILE"] = self.profile

        aws_cli = self._get_aws_cli()

        # Check if gateway already exists
        try:
            cmd = [aws_cli, "bedrock-agentcore-control", "list-gateways", "--region", self.region]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
            if result.returncode == 0:
                data = json.loads(result.stdout)
                for gw in data.get("items", []):
                    if gw.get("name") == self.gateway_name:
                        gateway_id = gw["gatewayId"]
                        logger.info(f"External OAuth gateway already exists: {gateway_id}")
                        return {
                            "gateway_id": gateway_id,
                            "gateway_url": f"https://{gateway_id}.gateway.bedrock-agentcore.{self.region}.amazonaws.com/mcp",
                        }
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
            pass

        # Create gateway — no allowedAudience, no allowedScopes
        authorizer_config = {
            "customJWTAuthorizer": {
                "discoveryUrl": self.well_known_url,
                "allowedClients": self.allowed_clients,
            }
        }

        cmd = [
            aws_cli, "bedrock-agentcore-control", "create-gateway",
            "--name", self.gateway_name,
            "--protocol-type", "MCP",
            "--authorizer-type", "CUSTOM_JWT",
            "--authorizer-configuration", json.dumps(authorizer_config),
            "--role-arn", role_arn,
            "--description", f"External OAuth MCP Gateway ({self.gateway_name})",
            "--region", self.region,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)
            if result.returncode != 0:
                if "ConflictException" in result.stderr or "already exists" in result.stderr.lower():
                    logger.info("External OAuth gateway already exists (conflict)")
                    return {}
                logger.error(f"Gateway creation failed: {result.stderr}")
                return {}

            response_data = json.loads(result.stdout)
            gateway_id = response_data.get("gatewayId", "")
            gateway_url = response_data.get("gatewayUrl", f"https://{gateway_id}.gateway.bedrock-agentcore.{self.region}.amazonaws.com/mcp")
            logger.info(f"Created external OAuth gateway: {gateway_id}")
            import time
            time.sleep(15)
            return {"gateway_id": gateway_id, "gateway_url": gateway_url}
        except subprocess.TimeoutExpired:
            logger.error("Gateway creation timed out")
            return {}
        except FileNotFoundError:
            logger.error("AWS CLI not found")
            return {}

    def register_target(self, gateway_id: str, target_name: str, lambda_arn: str, tool_schema: list) -> dict:
        """Register a Lambda target on the gateway."""
        env = os.environ.copy()
        if self.profile:
            env["AWS_PROFILE"] = self.profile

        aws_cli = self._get_aws_cli()

        target_config = {
            "mcp": {
                "lambda": {
                    "lambdaArn": lambda_arn,
                    "toolSchema": {"inlinePayload": tool_schema},
                }
            }
        }

        cmd = [
            aws_cli, "bedrock-agentcore-control", "create-gateway-target",
            "--gateway-identifier", gateway_id,
            "--name", target_name,
            "--description", f"Lambda target: {target_name}",
            "--target-configuration", json.dumps(target_config),
            "--credential-provider-configurations", json.dumps([{"credentialProviderType": "GATEWAY_IAM_ROLE"}]),
            "--region", self.region,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
            if result.returncode != 0:
                if "ConflictException" in result.stderr or "already exists" in result.stderr.lower():
                    logger.info(f"Target already exists: {target_name}")
                    return {"status": "success", "already_existed": True}
                logger.error(f"Target creation failed: {result.stderr}")
                return {"status": "error", "message": result.stderr}
            logger.info(f"Registered target: {target_name}")
            return {"status": "success"}
        except subprocess.TimeoutExpired:
            return {"status": "timeout"}
        except FileNotFoundError:
            return {"status": "cli_not_found"}

    def deploy(self) -> dict:
        """Full external OAuth gateway deployment."""
        results = {"stack_prefix": self.stack_prefix, "unique_id": self.unique_id, "region": self.region}

        # Step 1: Discover Lambda ARNs
        logger.info("=" * 60)
        logger.info("Step 1: Discovering Lambda ARNs")
        logger.info("=" * 60)
        lambda_arns = self.discover_lambda_arns()
        if not lambda_arns.get("adcp_handler") or not lambda_arns.get("a4a_mcp_handler"):
            logger.error("Could not discover both Lambda ARNs. Deploy IAM target first.")
            results["status"] = "lambda_arns_not_found"
            return results

        # Step 2: Ensure gateway role
        logger.info("=" * 60)
        logger.info("Step 2: Ensuring gateway IAM role")
        logger.info("=" * 60)
        all_arns = [lambda_arns["adcp_handler"], lambda_arns["a4a_mcp_handler"]]
        role_arn = self.ensure_gateway_role(all_arns)
        results["role_arn"] = role_arn

        # Step 3: Create gateway (no Cognito — external OIDC)
        logger.info("=" * 60)
        logger.info(f"Step 3: Creating external OAuth gateway ({self.gateway_name})")
        logger.info(f"  Discovery URL: {self.well_known_url}")
        logger.info(f"  Allowed clients: {self.allowed_clients}")
        logger.info("=" * 60)
        gateway = self.create_gateway(role_arn)
        if not gateway.get("gateway_id"):
            logger.error("External OAuth gateway creation failed")
            results["status"] = "gateway_creation_failed"
            return results
        results["gateway"] = gateway

        # Step 4: Register targets
        logger.info("=" * 60)
        logger.info("Step 4: Registering Lambda targets")
        logger.info("=" * 60)

        try:
            import importlib.util
            spec_path = Path(__file__).parent / "deploy_adcp_gateway.py"
            spec_module = importlib.util.spec_from_file_location("deploy_adcp_gateway", spec_path)
            adcp_module = importlib.util.module_from_spec(spec_module)
            spec_module.loader.exec_module(adcp_module)
            adcp_deployer = adcp_module.AdCPGatewayDeployer(self.stack_prefix, self.unique_id, self.region, self.profile)
            adcp_schema = adcp_deployer.get_adcp_tool_schema()
        except Exception as e:
            logger.warning(f"Could not import AdCP tool schema: {e}. Using empty schema.")
            adcp_schema = []

        adcp_target_name = "adcp"
        self.register_target(gateway["gateway_id"], adcp_target_name, lambda_arns["adcp_handler"], adcp_schema)

        a4a_target_name = "agent"
        self.register_target(gateway["gateway_id"], a4a_target_name, lambda_arns["a4a_mcp_handler"], A4A_MCP_TOOL_SCHEMA)

        results["status"] = "success"

        # Output connection config
        logger.info("")
        logger.info("=" * 60)
        logger.info("DEPLOYMENT COMPLETE — External OAuth Gateway")
        logger.info("=" * 60)
        logger.info(f"Gateway Name: {self.gateway_name}")
        logger.info(f"Gateway URL: {gateway['gateway_url']}")
        logger.info(f"Discovery URL: {self.well_known_url}")
        logger.info(f"Allowed Clients: {', '.join(self.allowed_clients)}")
        logger.info("")
        logger.info("QUICK SUITE WEB — Connection Config:")
        logger.info(f"  URL: {gateway['gateway_url']}")
        logger.info(f"  Auth: Service authentication (OAuth)")
        logger.info(f"  Client ID: {self.allowed_clients[0] if self.allowed_clients else 'N/A'}")
        logger.info(f"  Token URL: (from your IDP)")
        logger.info(f"  Authorization URL: (from your IDP)")
        logger.info(f"  Scope: (none required)")
        logger.info("")

        results["connection_config"] = {
            "gateway_url": gateway["gateway_url"],
            "gateway_name": self.gateway_name,
            "well_known_url": self.well_known_url,
            "allowed_clients": self.allowed_clients,
            "note": "No scope or audience required for external OAuth path",
        }

        return results


# ============================================================================
# Target Schema Updater (update-targets mode)
# ============================================================================


class TargetSchemaUpdater:
    """Update tool schemas on existing gateways without touching auth config.

    Discovers all known gateways (IAM, Cognito OAuth, Federate OAuth) and
    updates the A4A MCP Handler target's tool schema on each.
    """

    def __init__(self, stack_prefix: str, unique_id: str, region: str = "us-west-2",
                 profile: str = None, gateway_id: str = None):
        self.stack_prefix = stack_prefix
        self.unique_id = unique_id
        self.region = region
        self.profile = profile
        self.specific_gateway_id = gateway_id

        if profile:
            session = boto3.Session(profile_name=profile, region_name=region)
        else:
            env_profile = os.environ.get("AWS_PROFILE")
            if env_profile:
                session = boto3.Session(profile_name=env_profile, region_name=region)
            else:
                session = boto3.Session(region_name=region)

        self.sts_client = session.client("sts")
        self.account_id = self.sts_client.get_caller_identity()["Account"]
        self._session = session

        logger.info(f"TargetSchemaUpdater: Authenticated to account {self.account_id}")

    def _get_aws_cli(self) -> str:
        """Find a working AWS CLI binary."""
        for path in ["/opt/homebrew/bin/aws", "/usr/local/bin/aws"]:
            if os.path.exists(path):
                try:
                    result = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10)
                    if result.returncode == 0 and "aws-cli/2" in result.stdout:
                        return path
                except (subprocess.TimeoutExpired, OSError):
                    continue
        return "aws"

    def discover_all_gateways(self) -> list:
        """Discover all known gateways from config files and API."""
        gateways = []
        repo_root = Path(__file__).parent.parent.parent

        # If specific gateway ID provided, just use that
        if self.specific_gateway_id:
            return [{"gateway_id": self.specific_gateway_id, "name": "specified", "source": "cli-arg"}]

        # Read from config files
        # IAM gateway
        iam_config = repo_root / f".ads-gw-{self.stack_prefix}-{self.unique_id}.json"
        if iam_config.exists():
            with open(iam_config) as f:
                config = json.load(f)
            gw_result = config.get("gateway_result", {})
            if gw_result.get("gateway_id"):
                gateways.append({
                    "gateway_id": gw_result["gateway_id"],
                    "name": f"{self.stack_prefix}-ads-gw-{self.unique_id}",
                    "source": "iam-config",
                })

        # Cognito OAuth gateway
        oauth_config = repo_root / f".oauth-gw-{self.stack_prefix}-{self.unique_id}.json"
        if oauth_config.exists():
            with open(oauth_config) as f:
                config = json.load(f)
            oauth_gw = config.get("oauth_gateway", {})
            if oauth_gw.get("gateway_id"):
                gateways.append({
                    "gateway_id": oauth_gw["gateway_id"],
                    "name": f"{self.stack_prefix}-oauth-gw-{self.unique_id}",
                    "source": "oauth-config",
                })

        # External OAuth gateway (Federate)
        ext_config = repo_root / f".oauth-gw-{self.stack_prefix}-{self.unique_id}-external.json"
        if ext_config.exists():
            with open(ext_config) as f:
                config = json.load(f)
            ext_gw = config.get("gateway", {})
            if ext_gw.get("gateway_id"):
                gateways.append({
                    "gateway_id": ext_gw["gateway_id"],
                    "name": config.get("connection_config", {}).get("gateway_name", "external"),
                    "source": "external-oauth-config",
                })

        # If no config files found, try listing from API
        if not gateways:
            gateways = self._list_gateways_from_api()

        return gateways

    def _list_gateways_from_api(self) -> list:
        """List all gateways from the API and filter by stack prefix."""
        env = os.environ.copy()
        if self.profile:
            env["AWS_PROFILE"] = self.profile

        aws_cli = self._get_aws_cli()
        cmd = [aws_cli, "bedrock-agentcore-control", "list-gateways", "--region", self.region]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
            if result.returncode == 0:
                data = json.loads(result.stdout)
                gateways = []
                for gw in data.get("items", []):
                    name = gw.get("name", "")
                    if self.stack_prefix in name and self.unique_id in name:
                        gateways.append({
                            "gateway_id": gw["gatewayId"],
                            "name": name,
                            "source": "api",
                        })
                return gateways
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"Failed to list gateways from API: {e}")

        return []

    def _find_a4a_mcp_target(self, gateway_id: str) -> dict:
        """Find the A4A MCP handler target on a gateway."""
        env = os.environ.copy()
        if self.profile:
            env["AWS_PROFILE"] = self.profile

        aws_cli = self._get_aws_cli()
        cmd = [
            aws_cli, "bedrock-agentcore-control", "list-gateway-targets",
            "--gateway-identifier", gateway_id,
            "--region", self.region,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
            if result.returncode == 0:
                data = json.loads(result.stdout)
                for target in data.get("items", []):
                    name = target.get("name", "")
                    if name in ("agent", "adcp", "data") or "agent-target" in name or "mcp-handler" in name:
                        return {
                            "target_id": target.get("targetId", ""),
                            "name": name,
                        }
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"Failed to list targets for gateway {gateway_id}: {e}")

        return {}

    def _get_lambda_arn(self) -> str:
        """Get the A4A MCP handler Lambda ARN from config."""
        repo_root = Path(__file__).parent.parent.parent
        mcp_config = repo_root / f".a4a-mcp-handler-{self.stack_prefix}-{self.unique_id}.json"
        if mcp_config.exists():
            with open(mcp_config) as f:
                config = json.load(f)
            return config.get("lambda_arn", "")
        return ""

    def update_target_schema(self, gateway_id: str, target_id: str, target_name: str, lambda_arn: str) -> dict:
        """Update a specific target's tool schema."""
        env = os.environ.copy()
        if self.profile:
            env["AWS_PROFILE"] = self.profile

        aws_cli = self._get_aws_cli()

        target_config = {
            "mcp": {
                "lambda": {
                    "lambdaArn": lambda_arn,
                    "toolSchema": {"inlinePayload": A4A_MCP_TOOL_SCHEMA},
                }
            }
        }

        cmd = [
            aws_cli, "bedrock-agentcore-control", "update-gateway-target",
            "--gateway-identifier", gateway_id,
            "--target-id", target_id,
            "--name", target_name,
            "--target-configuration", json.dumps(target_config),
            "--credential-provider-configurations", json.dumps([{"credentialProviderType": "GATEWAY_IAM_ROLE"}]),
            "--region", self.region,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
            if result.returncode != 0:
                logger.error(f"Target schema update failed for {target_name}: {result.stderr}")
                return {"status": "error", "message": result.stderr}
            logger.info(f"Updated tool schema on target: {target_name} (gateway: {gateway_id})")
            return {"status": "success"}
        except subprocess.TimeoutExpired:
            return {"status": "timeout"}
        except FileNotFoundError:
            return {"status": "cli_not_found"}

    def update_all(self) -> dict:
        """Update tool schemas on all discovered gateways."""
        results = {"gateways_updated": [], "gateways_failed": []}

        gateways = self.discover_all_gateways()
        if not gateways:
            logger.error("No gateways found to update")
            results["status"] = "no_gateways_found"
            return results

        lambda_arn = self._get_lambda_arn()
        if not lambda_arn:
            logger.error("Could not discover A4A MCP handler Lambda ARN")
            results["status"] = "lambda_arn_not_found"
            return results

        logger.info(f"Found {len(gateways)} gateway(s) to update")
        logger.info(f"Lambda ARN: {lambda_arn}")
        logger.info(f"Tool schema has {len(A4A_MCP_TOOL_SCHEMA)} tools: {[t['name'] for t in A4A_MCP_TOOL_SCHEMA]}")
        logger.info("")

        for gw in gateways:
            gateway_id = gw["gateway_id"]
            gw_name = gw.get("name", gateway_id)
            logger.info(f"--- Updating gateway: {gw_name} ({gateway_id}) ---")

            target = self._find_a4a_mcp_target(gateway_id)
            if not target or not target.get("target_id"):
                logger.warning(f"  No A4A MCP target found on gateway {gw_name}. Skipping.")
                results["gateways_failed"].append({"gateway": gw_name, "reason": "target_not_found"})
                continue

            update_result = self.update_target_schema(
                gateway_id=gateway_id,
                target_id=target["target_id"],
                target_name=target["name"],
                lambda_arn=lambda_arn,
            )

            if update_result.get("status") == "success":
                results["gateways_updated"].append(gw_name)
            else:
                results["gateways_failed"].append({"gateway": gw_name, "reason": update_result.get("message", "unknown")})

        logger.info("")
        logger.info("=" * 60)
        logger.info("TARGET SCHEMA UPDATE COMPLETE")
        logger.info("=" * 60)
        logger.info(f"Updated: {len(results['gateways_updated'])} gateway(s)")
        if results["gateways_failed"]:
            logger.warning(f"Failed: {len(results['gateways_failed'])} gateway(s)")
            for fail in results["gateways_failed"]:
                raw_gateway = str(fail.get("gateway", "unknown"))
                masked_gateway = f"***{raw_gateway[-4:]}" if len(raw_gateway) > 4 else "***"
                raw_reason = str(fail.get("reason", "unknown"))
                safe_reason = raw_reason if raw_reason in {"target_not_found", "unknown"} else "operation_failed"
                logger.warning(f"  - {masked_gateway}: {safe_reason}")

        results["status"] = "success" if results["gateways_updated"] else "all_failed"
        return results


# ============================================================================
# CLI Entry Point
# ============================================================================


def main():
    parser = argparse.ArgumentParser(description="Deploy A4A MCP Handler — multiple modes for gateway and target management")
    parser.add_argument("--stack-prefix", required=True, help="Stack prefix (e.g., a4a)")
    parser.add_argument("--unique-id", required=True, help="Unique deployment ID (e.g., omixaj)")
    parser.add_argument("--region", default="us-west-2", help="AWS region (default: us-west-2)")
    parser.add_argument("--profile", help="AWS profile name")
    parser.add_argument("--iam-target", action="store_true",
                        help="Register Target 2 on IAM gateway (developer mode)")
    parser.add_argument("--data-target", action="store_true",
                        help="Deploy data management Lambda and register as Target 3 on all gateways")
    parser.add_argument("--mode", choices=["oauth-cognito", "oauth-external", "update-targets"],
                        help="Deployment mode: oauth-cognito (default), oauth-external (Federate/Okta), update-targets (schema only)")
    # oauth-external args
    parser.add_argument("--well-known-url", help="External OAuth discovery URL (for --mode oauth-external)")
    parser.add_argument("--allowed-clients", help="Comma-separated allowed client IDs (for --mode oauth-external)")
    parser.add_argument("--gateway-name", help="Custom gateway name (for --mode oauth-external)")
    # update-targets args
    parser.add_argument("--gateway-id", help="Specific gateway ID to update (for --mode update-targets). If omitted, updates all known gateways.")

    args = parser.parse_args()

    if args.data_target:
        # Deploy data management Lambda and register on all gateways
        deployer = A4AMCPHandlerDeployer(
            stack_prefix=args.stack_prefix,
            unique_id=args.unique_id,
            region=args.region,
            profile=args.profile,
        )

        # Deploy data management Lambda
        logger.info("Deploying data management Lambda...")
        data_lambda_name = f"{args.stack_prefix}-data-handler-{args.unique_id}"
        data_handler_path = Path(__file__).parent.parent.parent / "lambda" / "data_management_handler.py"
        data_schema_path = Path(__file__).parent / "data_tool_schema.json"

        if not data_handler_path.exists():
            logger.error(f"Data handler not found: {data_handler_path}")
            sys.exit(1)

        # Create Lambda zip with just the handler
        from io import BytesIO
        import zipfile
        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            with open(data_handler_path, 'r') as f:
                zf.writestr('lambda_function.py', f.read())
        zip_bytes = zip_buffer.getvalue()

        # Deploy Lambda (create or update)
        lambda_client = deployer.session.client("lambda") if hasattr(deployer, 'session') else boto3.Session(
            profile_name=args.profile, region_name=args.region
        ).client("lambda")

        try:
            lambda_client.get_function(FunctionName=data_lambda_name)
            # Update existing
            lambda_client.update_function_code(
                FunctionName=data_lambda_name,
                ZipFile=zip_bytes,
            )
            # Update env vars
            lambda_client.update_function_configuration(
                FunctionName=data_lambda_name,
                Environment={"Variables": {
                    "DATA_BUCKET": f"{args.stack_prefix}-data-{args.unique_id}",
                    "STACK_PREFIX": args.stack_prefix,
                    "UNIQUE_ID": args.unique_id,
                    "AWS_REGION_OVERRIDE": args.region,
                }},
                Timeout=120,
                MemorySize=256,
            )
            logger.info(f"✅ Updated Lambda: {data_lambda_name}")
        except lambda_client.exceptions.ResourceNotFoundException:
            # Get execution role from existing adcp Lambda
            adcp_lambda_name = f"{args.stack_prefix}-adcp-handler-{args.unique_id}"
            try:
                adcp_config = lambda_client.get_function(FunctionName=adcp_lambda_name)
                role_arn = adcp_config["Configuration"]["Role"]
            except Exception:
                logger.error("Cannot find execution role — deploy adcp-handler first")
                sys.exit(1)

            lambda_client.create_function(
                FunctionName=data_lambda_name,
                Runtime="python3.12",
                Role=role_arn,
                Handler="lambda_function.lambda_handler",
                Code={"ZipFile": zip_bytes},
                Timeout=120,
                MemorySize=256,
                Environment={"Variables": {
                    "DATA_BUCKET": f"{args.stack_prefix}-data-{args.unique_id}",
                    "STACK_PREFIX": args.stack_prefix,
                    "UNIQUE_ID": args.unique_id,
                    "AWS_REGION_OVERRIDE": args.region,
                }},
            )
            logger.info(f"✅ Created Lambda: {data_lambda_name}")

        # Get Lambda ARN
        data_lambda_arn = lambda_client.get_function(
            FunctionName=data_lambda_name
        )["Configuration"]["FunctionArn"]
        logger.info(f"Data Lambda ARN: {data_lambda_arn}")

        # Load tool schema
        with open(data_schema_path) as f:
            tool_schema = json.load(f)["tools"]

        # Register on all known gateways
        config_files = list(Path(__file__).parent.parent.parent.glob(f".*-gw-{args.stack_prefix}-{args.unique_id}.json"))
        if not config_files:
            # Try the standard naming
            for pattern in [f".ads-gw-{args.stack_prefix}-{args.unique_id}.json",
                          f".oauth-gw-{args.stack_prefix}-{args.unique_id}.json"]:
                p = Path(__file__).parent.parent.parent / pattern
                if p.exists():
                    config_files.append(p)

        target_name = "data"
        for config_file in config_files:
            try:
                with open(config_file) as f:
                    gw_config = json.load(f)
                gateway_id = gw_config.get("gateway_id", "")
                if gateway_id:
                    logger.info(f"Registering data target on gateway: {gateway_id}")
                    result = deployer.register_target(gateway_id, target_name, data_lambda_arn, tool_schema)
                    logger.info(f"  Result: {result.get('status')}")
            except Exception as e:
                logger.warning(f"  ⚠️ Failed for {config_file.name}: {e}")

        print(json.dumps({"status": "success", "lambda_arn": data_lambda_arn, "target_name": target_name}))

    elif args.iam_target:
        # Existing code path — register Target 2 on IAM gateway
        deployer = A4AMCPHandlerDeployer(
            stack_prefix=args.stack_prefix,
            unique_id=args.unique_id,
            region=args.region,
            profile=args.profile,
        )
        result = deployer.deploy()

        if result.get("status") != "success":
            logger.error(f"Deployment failed: {result.get('status')}")
            sys.exit(1)

        config_path = Path(__file__).parent.parent.parent / f".a4a-mcp-handler-{args.stack_prefix}-{args.unique_id}.json"
        with open(config_path, "w") as f:
            json.dump(result, f, indent=2, default=str)
        logger.info(f"Config saved: {config_path}")

    elif args.mode == "oauth-external":
        # External OAuth gateway (Federate, Okta, Auth0, etc.)
        if not args.well_known_url:
            logger.error("--well-known-url is required for --mode oauth-external")
            sys.exit(1)
        if not args.allowed_clients:
            logger.error("--allowed-clients is required for --mode oauth-external")
            sys.exit(1)

        deployer = ExternalOAuthGatewayDeployer(
            stack_prefix=args.stack_prefix,
            unique_id=args.unique_id,
            region=args.region,
            profile=args.profile,
            well_known_url=args.well_known_url,
            allowed_clients=[c.strip() for c in args.allowed_clients.split(",")],
            gateway_name=args.gateway_name,
        )
        result = deployer.deploy()

        if result.get("status") != "success":
            logger.error(f"Deployment failed: {result.get('status')}")
            sys.exit(1)

        config_path = Path(__file__).parent.parent.parent / f".oauth-gw-{args.stack_prefix}-{args.unique_id}-external.json"
        with open(config_path, "w") as f:
            json.dump(result, f, indent=2, default=str)
        logger.info(f"Config saved: {config_path}")

    elif args.mode == "update-targets":
        # Update tool schemas on existing gateways without touching auth config
        updater = TargetSchemaUpdater(
            stack_prefix=args.stack_prefix,
            unique_id=args.unique_id,
            region=args.region,
            profile=args.profile,
            gateway_id=args.gateway_id,
        )
        result = updater.update_all()

        if result.get("status") != "success":
            logger.error(f"Update failed: {result.get('status')}")
            sys.exit(1)

    else:
        # Default path — create Cognito OAuth gateway
        deployer = OAuthGatewayDeployer(
            stack_prefix=args.stack_prefix,
            unique_id=args.unique_id,
            region=args.region,
            profile=args.profile,
        )
        result = deployer.deploy()

        if result.get("status") != "success":
            logger.error(f"Deployment failed: {result.get('status')}")
            sys.exit(1)

        config_path = Path(__file__).parent.parent.parent / f".oauth-gw-{args.stack_prefix}-{args.unique_id}.json"
        with open(config_path, "w") as f:
            json.dump(result, f, indent=2, default=str)
        logger.info(f"Config saved: {config_path}")


if __name__ == "__main__":
    main()
