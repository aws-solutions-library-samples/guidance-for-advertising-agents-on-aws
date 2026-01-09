#!/usr/bin/env python3
"""
Deploy AdCP MCP Gateway for Agentic Advertising Ecosystem

This script deploys an MCP Gateway via AgentCore CLI with a Lambda target that
implements the Ad Context Protocol (AdCP) for the agentic advertising workflow.

The deployment creates:
1. IAM Role for Lambda execution
2. Lambda function with AdCP protocol handlers
3. MCP Gateway via AgentCore CLI
4. Lambda target attached to the gateway

Usage:
    python deploy_adcp_gateway.py --stack-prefix <prefix> --unique-id <id> --region <region>
    
Example:
    python deploy_adcp_gateway.py --stack-prefix myapp --unique-id abc123 --region us-east-1

After deployment, set the environment variable to enable MCP:
    export ADCP_USE_MCP=true
    export ADCP_GATEWAY_URL=<gateway-url-from-output>
"""

import argparse
import boto3
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import time
import zipfile
from io import BytesIO

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class AdCPGatewayDeployer:
    """Deploy AdCP MCP Gateway with Lambda targets"""
    
    @staticmethod
    def _validate_aws_identifier(value: str, name: str) -> str:
        """Validate AWS identifiers to prevent command injection"""
        if not value:
            raise ValueError(f"{name} cannot be empty")
        # AWS identifiers typically contain alphanumeric, hyphens, underscores
        if not re.match(r'^[a-zA-Z0-9._-]+$', value):
            raise ValueError(f"Invalid {name}: {value}")
        return value
    
    @staticmethod
    def _validate_aws_profile(profile: str) -> str:
        """Validate AWS profile name to prevent command injection"""
        if not profile:
            raise ValueError("Profile name cannot be empty")
        if not re.match(r'^[a-zA-Z0-9._-]+$', profile):
            raise ValueError(f"Invalid AWS profile name: {profile}")
        return profile
    
    def __init__(self, stack_prefix: str, unique_id: str, region: str = "us-east-1", profile: str = None):
        self.stack_prefix = stack_prefix
        self.unique_id = unique_id
        self.region = region
        self.profile = profile
        
        try:
            # Create boto3 session - use profile if provided, otherwise use default credential chain
            if profile:
                logger.info(f"Using AWS profile: {profile}")
                session = boto3.Session(profile_name=profile, region_name=region)
            else:
                # Check if AWS_PROFILE environment variable is set
                env_profile = os.environ.get('AWS_PROFILE')
                if env_profile:
                    logger.info(f"Using AWS profile from environment: {env_profile}")
                    session = boto3.Session(profile_name=env_profile, region_name=region)
                else:
                    logger.info("No profile specified, using default credential chain")
                    session = boto3.Session(region_name=region)
            
            self.lambda_client = session.client("lambda")
            self.iam_client = session.client("iam")
            self.sts_client = session.client("sts")
            self.account_id = self.sts_client.get_caller_identity()["Account"]
            logger.info(f"Successfully authenticated to AWS account: {self.account_id}")
        except Exception as e:
            logger.error(f"Failed to initialize AWS clients: {e}")
            logger.error("Please ensure AWS credentials are configured.")
            if profile:
                logger.error(f"Tried to use profile: {profile}")
            else:
                logger.error("No profile was specified. Pass --profile <profile_name> or set AWS_PROFILE environment variable.")
            raise
        
        self.gateway_name = f"{stack_prefix}-adcp-gateway-{unique_id}"
        self.lambda_name = f"{stack_prefix}-adcp-handler-{unique_id}"
        self.role_name = f"{stack_prefix}-adcp-lambda-role-{unique_id}"
        self.gateway_role_name = f"{stack_prefix}-adcp-gateway-role-{unique_id}"
        self.invoke_role_name = f"{stack_prefix}-adcp-invoke-role-{unique_id}"
        self._session = session
        
    def create_lambda_execution_role(self) -> str:
        """Create IAM role for AdCP Lambda functions"""
        trust_policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole"
            }]
        }
        
        try:
            response = self.iam_client.create_role(
                RoleName=self.role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description="Execution role for AdCP Lambda functions"
            )
            role_arn = response["Role"]["Arn"]
            logger.info(f"Created IAM role: {role_arn}")
            
            # Attach basic Lambda execution policy
            self.iam_client.attach_role_policy(
                RoleName=self.role_name,
                PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
            )
            
            # Wait for role propagation
            logger.info("Waiting for IAM role propagation (10 seconds)...")
            time.sleep(10)  # nosemgrep: arbitrary-sleep - Intentional delay for IAM role propagation
            
            return role_arn
            
        except self.iam_client.exceptions.EntityAlreadyExistsException:
            response = self.iam_client.get_role(RoleName=self.role_name)
            logger.info(f"Using existing IAM role: {response['Role']['Arn']}")
            return response["Role"]["Arn"]
    
    def create_gateway_role(self, lambda_arn: str) -> str:
        """
        Create IAM role for AgentCore Gateway.
        
        This role allows the gateway to:
        1. Be assumed by the bedrock-agentcore service
        2. Invoke the Lambda function (outbound auth)
        """
        # Try multiple service principals - the correct one depends on the region/account
        service_principals = [
            "bedrock-agentcore.amazonaws.com",
            "gateway.bedrock-agentcore.amazonaws.com", 
        ]
        
        # Policy to allow gateway to invoke Lambda
        lambda_invoke_policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Action": "lambda:InvokeFunction",
                "Resource": lambda_arn
            }]
        }
        
        role_arn = None
        last_error = None
        
        for service_principal in service_principals:
            trust_policy = {
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": service_principal},
                    "Action": "sts:AssumeRole"
                }]
            }
            
            try:
                response = self.iam_client.create_role(
                    RoleName=self.gateway_role_name,
                    AssumeRolePolicyDocument=json.dumps(trust_policy),
                    Description="Role for AgentCore Gateway to invoke AdCP Lambda"
                )
                role_arn = response["Role"]["Arn"]
                logger.info(f"Created gateway IAM role with principal {service_principal}: {role_arn}")
                break
            except self.iam_client.exceptions.MalformedPolicyDocumentException as e:
                last_error = e
                logger.debug(f"Service principal {service_principal} not valid, trying next...")
                continue
            except self.iam_client.exceptions.EntityAlreadyExistsException:
                # Role exists, get it and update policy
                response = self.iam_client.get_role(RoleName=self.gateway_role_name)
                role_arn = response["Role"]["Arn"]
                logger.info(f"Using existing gateway IAM role: {role_arn}")
                break
            except Exception as e:
                last_error = e
                continue
        
        if role_arn is None:
            raise Exception(f"Could not create gateway role with any service principal: {last_error}")
        
        # Attach inline policy for Lambda invocation
        self.iam_client.put_role_policy(
            RoleName=self.gateway_role_name,
            PolicyName="LambdaInvokePolicy",
            PolicyDocument=json.dumps(lambda_invoke_policy)
        )
        
        # Wait for role propagation
        logger.info("Waiting for gateway role propagation (10 seconds)...")
        time.sleep(10)  # nosemgrep: arbitrary-sleep - Intentional delay for IAM role propagation
        
        return role_arn
    
    def create_gateway_invoke_role(self, gateway_id: str, caller_arn: str = None) -> str:
        """
        Create IAM role for invoking the AgentCore Gateway.
        
        This role is needed by agents/clients to call the gateway with SigV4 auth.
        It grants the bedrock-agentcore:InvokeGateway permission.
        
        Args:
            gateway_id: The gateway ID to grant invoke permission for
            caller_arn: ARN of the principal that will assume this role (optional)
        """
        # If no caller ARN provided, use the current identity
        if not caller_arn:
            caller_identity = self.sts_client.get_caller_identity()
            caller_arn = caller_identity["Arn"]
        
        # Trust policy allows both the service and the caller to assume the role
        trust_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                },
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": caller_arn},
                    "Action": "sts:AssumeRole"
                }
            ]
        }
        
        # Policy to allow invoking the gateway
        gateway_arn = f"arn:aws:bedrock-agentcore:{self.region}:{self.account_id}:gateway/{gateway_id}"
        invoke_policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Action": "bedrock-agentcore:InvokeGateway",
                "Resource": gateway_arn
            }]
        }
        
        try:
            response = self.iam_client.create_role(
                RoleName=self.invoke_role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description="Role for invoking AdCP MCP Gateway"
            )
            role_arn = response["Role"]["Arn"]
            logger.info(f"Created gateway invoke role: {role_arn}")
            
            # Attach inline policy for gateway invocation
            self.iam_client.put_role_policy(
                RoleName=self.invoke_role_name,
                PolicyName="GatewayInvokePolicy",
                PolicyDocument=json.dumps(invoke_policy)
            )
            
            # Wait for role propagation
            logger.info("Waiting for invoke role propagation (10 seconds)...")
            time.sleep(10)  # nosemgrep: arbitrary-sleep - Intentional delay for IAM role propagation
            
            return role_arn
            
        except self.iam_client.exceptions.EntityAlreadyExistsException:
            # Update the policy in case gateway ID changed
            try:
                self.iam_client.put_role_policy(
                    RoleName=self.invoke_role_name,
                    PolicyName="GatewayInvokePolicy",
                    PolicyDocument=json.dumps(invoke_policy)
                )
            except Exception as e:
                logger.warning(f"Could not update invoke role policy: {e}")
            
            response = self.iam_client.get_role(RoleName=self.invoke_role_name)
            logger.info(f"Using existing gateway invoke role: {response['Role']['Arn']}")
            return response["Role"]["Arn"]
    
    def store_gateway_url_in_ssm(self, gateway_url: str) -> dict:
        """
        Store the gateway URL in SSM Parameter Store for agents to retrieve at runtime.
        
        This allows agents to discover the gateway URL without needing it passed as
        an environment variable during deployment.
        
        Args:
            gateway_url: The MCP gateway URL to store
            
        Returns:
            dict with status and parameter_name
        """
        parameter_name = f"/{self.stack_prefix}/adcp_gateway/{self.unique_id}"
        
        try:
            ssm_client = self._session.client('ssm', region_name=self.region)
            
            ssm_client.put_parameter(
                Name=parameter_name,
                Value=gateway_url,
                Type='String',
                Overwrite=True,
                Description=f'AdCP MCP Gateway URL for {self.stack_prefix}-{self.unique_id}'
            )
            
            logger.info(f"✅ Gateway URL stored in SSM: {parameter_name}")
            return {
                "status": "success",
                "parameter_name": parameter_name,
                "gateway_url": gateway_url
            }
            
        except Exception as e:
            logger.warning(f"⚠️  Failed to store gateway URL in SSM: {e}")
            logger.warning(f"   Agents will need ADCP_GATEWAY_URL environment variable set manually")
            return {
                "status": "failed",
                "error": str(e),
                "parameter_name": parameter_name
            }
    
    def create_adcp_lambda_code(self) -> bytes:
        """
        Create Lambda deployment package with AdCP handlers.
        
        This method packages:
        1. The Lambda handler code from lambda/adcp_mcp_handler.py
        2. CSV data files from synthetic_data/mcp_mocks/ for the Lambda to read
        
        The Lambda reads data from bundled CSV files instead of hardcoded mock data,
        ensuring consistency with the synthetic data used throughout the ecosystem.
        """
        # Find the project root directory
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(script_dir))
        
        # Paths to source files
        lambda_handler_path = os.path.join(project_root, "lambda", "adcp_mcp_handler.py")
        mcp_mocks_dir = os.path.join(project_root, "synthetic_data", "mcp_mocks")
        
        # CSV files to include in the Lambda package
        csv_files = [
            ("products.csv", "products.csv"),  # Primary name
            ("products (1).csv", "products.csv"),  # Fallback name -> normalized
            ("signals.csv", "signals.csv"),
            ("campaigns.csv", "campaigns.csv"),
            ("verification_services.csv", "verification_services.csv"),
            ("measurement_providers.csv", "measurement_providers.csv"),
            ("identity_providers.csv", "identity_providers.csv"),
        ]
        
        # Verify Lambda handler exists - fail loudly if not
        if not os.path.exists(lambda_handler_path):
            error_msg = f"FATAL: Lambda handler not found at {lambda_handler_path}"
            logger.error(error_msg)
            logger.error("The file lambda/adcp_mcp_handler.py must exist.")
            logger.error("This file should have been created during project setup.")
            raise FileNotFoundError(error_msg)
        
        # Create zip file in memory
        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Add the Lambda handler
            logger.info(f"Adding Lambda handler from: {lambda_handler_path}")
            with open(lambda_handler_path, 'r') as f:
                handler_code = f.read()
            zf.writestr('lambda_function.py', handler_code)
            
            # Add CSV data files
            added_files = set()
            for csv_entry in csv_files:
                if isinstance(csv_entry, tuple):
                    source_name, target_name = csv_entry
                else:
                    source_name = target_name = csv_entry
                
                # Skip if we already added this target file
                if target_name in added_files:
                    continue
                
                csv_path = os.path.join(mcp_mocks_dir, source_name)
                if os.path.exists(csv_path):
                    logger.info(f"Adding data file: {source_name} -> data/{target_name}")
                    with open(csv_path, 'r', encoding='utf-8') as f:
                        zf.writestr(f'data/{target_name}', f.read())
                    added_files.add(target_name)
                else:
                    logger.debug(f"CSV file not found: {csv_path} (may use alternate)")
        
        logger.info("Lambda deployment package created successfully")
        return zip_buffer.getvalue()
    
    def deploy_adcp_lambda(self) -> str:
        """Deploy Lambda function for AdCP protocol"""
        role_arn = self.create_lambda_execution_role()
        code_zip = self.create_adcp_lambda_code()
        
        try:
            response = self.lambda_client.create_function(
                FunctionName=self.lambda_name,
                Runtime="python3.11",
                Role=role_arn,
                Handler="lambda_function.handler",
                Code={"ZipFile": code_zip},
                Description="AdCP MCP Protocol Handler for Agentic Advertising Ecosystem",
                Timeout=30,
                MemorySize=256,
                Environment={
                    "Variables": {
                        "STACK_PREFIX": self.stack_prefix,
                        "UNIQUE_ID": self.unique_id
                    }
                }
            )
            logger.info(f"Created Lambda function: {response['FunctionArn']}")
            
            # Wait for Lambda to be active
            logger.info("Waiting for Lambda function to be active...")
            waiter = self.lambda_client.get_waiter('function_active')
            waiter.wait(FunctionName=self.lambda_name)
            
            return response["FunctionArn"]
            
        except self.lambda_client.exceptions.ResourceConflictException:
            # Update existing function
            self.lambda_client.update_function_code(
                FunctionName=self.lambda_name,
                ZipFile=code_zip
            )
            response = self.lambda_client.get_function(FunctionName=self.lambda_name)
            logger.info(f"Updated existing Lambda function: {response['Configuration']['FunctionArn']}")
            return response["Configuration"]["FunctionArn"]
    
    def get_existing_gateway(self) -> dict:
        """Check if gateway already exists and return its info using AWS CLI"""
        logger.info(f"Checking for existing gateway: {self.gateway_name}")
        
        # Validate region to prevent command injection
        validated_region = self._validate_aws_identifier(self.region, "region")
        
        # Set up environment with AWS_PROFILE if specified
        env = os.environ.copy()
        if self.profile:
            validated_profile = self._validate_aws_profile(self.profile)
            env["AWS_PROFILE"] = validated_profile
        
        try:
            # Use AWS CLI to list gateways (more reliable than agentcore CLI)
            cmd = [
                "aws", "bedrock-agentcore-control", "list-gateways",
                "--region", validated_region
            ]
            
            # nosemgrep: dangerous-subprocess-use-audit
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
            
            if result.returncode == 0:
                gateways_data = json.loads(result.stdout)
                # Find gateway by name
                for gw in gateways_data.get("items", []):
                    if gw.get("name") == self.gateway_name:
                        gateway_id = gw.get("gatewayId")
                        # Validate gateway_id from API response before using in subprocess
                        validated_gateway_id = self._validate_aws_identifier(gateway_id, "gateway_id")
                        logger.info(f"Found existing gateway: {self.gateway_name} (ID: {validated_gateway_id})")
                        
                        # Get full gateway details
                        get_cmd = [
                            "aws", "bedrock-agentcore-control", "get-gateway",
                            "--gateway-identifier", validated_gateway_id,
                            "--region", validated_region
                        ]
                        
                        get_result = subprocess.run(get_cmd, capture_output=True, text=True, timeout=60, env=env)  # nosemgrep: dangerous-subprocess-use-audit, dangerous-subprocess-use-tainted-env-args
                        
                        if get_result.returncode == 0:
                            gw_details = json.loads(get_result.stdout)
                            return {
                                "status": "exists",
                                "gateway_id": gw_details.get("gatewayId"),
                                "gateway_arn": gw_details.get("gatewayArn"),
                                "gateway_url": gw_details.get("gatewayUrl"),
                                "role_arn": gw_details.get("roleArn"),
                                "output": get_result.stdout
                            }
                        
                        # Fallback: construct ARN and URL from gateway ID
                        return {
                            "status": "exists",
                            "gateway_id": gateway_id,
                            "gateway_arn": f"arn:aws:bedrock-agentcore:{self.region}:{self.account_id}:gateway/{gateway_id}",
                            "gateway_url": f"https://{gateway_id}.gateway.bedrock-agentcore.{self.region}.amazonaws.com/mcp"
                        }
            
            return {"status": "not_found"}
            
        except subprocess.TimeoutExpired:
            return {"status": "check_timeout"}
        except FileNotFoundError:
            return {"status": "cli_not_found", "message": "AWS CLI not found"}
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse gateway list response: {e}")
            return {"status": "parse_error", "message": str(e)}
        except Exception as e:
            logger.warning(f"Error checking for existing gateway: {e}")
            return {"status": "check_error", "message": str(e)}
    
    def create_gateway(self, enable_semantic_search: bool = False, gateway_role_arn: str = None) -> dict:
        """
        Create MCP Gateway using boto3 SDK with AWS IAM authentication.
        
        This method uses the bedrock-agentcore-control API directly to ensure
        proper configuration of authorizerType='AWS_IAM' for SigV4 authentication.
        
        Args:
            enable_semantic_search: Enable semantic search on the gateway
            gateway_role_arn: IAM role ARN for the gateway (for outbound auth to Lambda)
        """
        logger.info(f"Creating MCP Gateway: {self.gateway_name}")
        
        # First check if gateway already exists
        existing = self.get_existing_gateway()
        if existing.get("status") == "exists":
            logger.info(f"Gateway already exists: {self.gateway_name}")
            logger.info("Using existing gateway instead of creating new one")
            return {
                "status": "success",
                "already_existed": True,
                **{k: v for k, v in existing.items() if k != "status"}
            }
        
        try:
            # Create bedrock-agentcore-control client
            gateway_client = self._session.client('bedrock-agentcore-control', region_name=self.region)
            
            # Build create_gateway parameters
            create_params = {
                'name': self.gateway_name,
                'protocolType': 'MCP',
                'authorizerType': 'AWS_IAM',  # This is critical for SigV4 authentication!
                'description': 'AdCP MCP Gateway for Agentic Advertising Ecosystem'
            }
            
            # Add role ARN if provided (required for Lambda target invocation)
            if gateway_role_arn:
                create_params['roleArn'] = gateway_role_arn
            
            logger.info(f"Creating gateway with authorizerType=AWS_IAM")
            response = gateway_client.create_gateway(**create_params)
            
            gateway_info = {
                "gateway_id": response.get("gatewayId"),
                "gateway_arn": response.get("gatewayArn"),
                "gateway_url": response.get("gatewayUrl"),
                "role_arn": response.get("roleArn"),
            }
            
            logger.info(f"Gateway created successfully: {gateway_info['gateway_id']}")
            logger.info(f"Gateway URL: {gateway_info['gateway_url']}")
            logger.info(f"Gateway ARN: {gateway_info['gateway_arn']}")
            
            # Wait for gateway to be ready
            logger.info("Waiting for gateway to be active (10 seconds)...")
            time.sleep(10)  # nosemgrep: arbitrary-sleep - Intentional delay for gateway propagation
            
            return {"status": "success", **gateway_info}
            
        except gateway_client.exceptions.ConflictException:
            logger.info(f"Gateway already exists (ConflictException): {self.gateway_name}")
            existing = self.get_existing_gateway()
            if existing.get("status") == "exists":
                return {
                    "status": "success",
                    "already_existed": True,
                    **{k: v for k, v in existing.items() if k != "status"}
                }
            return {"status": "success", "already_existed": True, "message": "Gateway already exists"}
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Gateway creation failed: {error_msg}")
            
            # Fall back to CLI if boto3 fails (e.g., API not available)
            if "UnknownServiceError" in error_msg or "Could not connect" in error_msg:
                logger.info("Falling back to agentcore CLI for gateway creation...")
                return self._create_gateway_via_cli(enable_semantic_search)
            
            return {"status": "error", "message": error_msg}
    
    def _create_gateway_via_cli(self, enable_semantic_search: bool = False) -> dict:
        """Fallback: Create MCP Gateway using AgentCore CLI"""
        logger.info(f"Creating MCP Gateway via CLI: {self.gateway_name}")
        
        # Validate inputs to prevent command injection
        validated_gateway_name = self._validate_aws_identifier(self.gateway_name, "gateway_name")
        validated_region = self._validate_aws_identifier(self.region, "region")
        
        cmd = [
            "agentcore", "gateway", "create-mcp-gateway",
            "--name", validated_gateway_name,
            "--region", validated_region
        ]
        
        env = os.environ.copy()
        if self.profile:
            validated_profile = self._validate_aws_profile(self.profile)
            env["AWS_PROFILE"] = validated_profile
        
        try:
            # nosemgrep: dangerous-subprocess-use-audit
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
            
            if result.returncode != 0:
                if "ConflictException" in result.stderr or "already exists" in result.stderr.lower():
                    existing = self.get_existing_gateway()
                    if existing.get("status") == "exists":
                        return {"status": "success", "already_existed": True, **{k: v for k, v in existing.items() if k != "status"}}
                    return {"status": "success", "already_existed": True}
                
                logger.error(f"Gateway creation failed: {result.stderr}")
                return {"status": "error", "message": result.stderr}
            
            logger.info("Gateway created successfully via CLI")
            
            # Parse output
            gateway_info = {}
            import re
            arn_match = re.search(r"'gatewayArn':\s*'([^']+)'", result.stdout)
            url_match = re.search(r"'gatewayUrl':\s*'([^']+)'", result.stdout)
            id_match = re.search(r"'gatewayId':\s*'([^']+)'", result.stdout)
            role_match = re.search(r"'roleArn':\s*'([^']+)'", result.stdout)
            
            if arn_match:
                gateway_info["gateway_arn"] = arn_match.group(1)
            if url_match:
                gateway_info["gateway_url"] = url_match.group(1)
            if id_match:
                gateway_info["gateway_id"] = id_match.group(1)
            if role_match:
                gateway_info["role_arn"] = role_match.group(1)
            
            if not gateway_info.get("gateway_arn"):
                fetched = self.get_existing_gateway()
                if fetched.get("status") == "exists":
                    gateway_info.update({k: v for k, v in fetched.items() if k != "status" and k != "output"})
            
            return {"status": "success", "output": result.stdout, **gateway_info}
            
        except subprocess.TimeoutExpired:
            return {"status": "timeout", "message": "Gateway creation timed out"}
        except FileNotFoundError:
            return {"status": "cli_not_found", "message": "AgentCore CLI not found"}
    
    def get_gateway_targets(self, gateway_id: str) -> list:
        """Get existing targets for a gateway"""
        # Validate inputs to prevent command injection
        validated_gateway_id = self._validate_aws_identifier(gateway_id, "gateway_id")
        validated_region = self._validate_aws_identifier(self.region, "region")
        
        env = os.environ.copy()
        if self.profile:
            validated_profile = self._validate_aws_profile(self.profile)
            env["AWS_PROFILE"] = validated_profile
        
        try:
            cmd = [
                "aws", "bedrock-agentcore-control", "list-gateway-targets",
                "--gateway-identifier", validated_gateway_id,
                "--region", validated_region
            ]
            # nosemgrep: dangerous-subprocess-use-audit
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
            
            if result.returncode == 0:
                data = json.loads(result.stdout)
                return data.get("items", [])
        except Exception as e:
            logger.warning(f"Failed to list gateway targets: {e}")
        
        return []
    
    def get_adcp_tool_schema(self) -> list:
        """Return the AdCP tool schema for Lambda target"""
        return [
            {"name": "get_products", "description": "Get available advertising products/inventory matching criteria",
             "inputSchema": {"type": "object", "properties": {
                 "channels": {"type": "array", "items": {"type": "string"}, "description": "Filter by channels (ctv, online_video, display, etc.)"},
                 "brand_safety_tier": {"type": "string", "description": "Brand safety tier filter (tier_1, tier_2, tier_3)"},
                 "min_budget": {"type": "number", "description": "Minimum budget filter"},
                 "brief": {"type": "string", "description": "Campaign brief description"}}}},
            {"name": "get_signals", "description": "Get available audience signals and targeting data",
             "inputSchema": {"type": "object", "properties": {
                 "signal_types": {"type": "array", "items": {"type": "string"}, "description": "Filter by signal types (audience, contextual)"},
                 "decisioning_platform": {"type": "string", "description": "Target platform (ttd, dv360, etc.)"}}}},
            {"name": "activate_signal", "description": "Activate an audience signal on a decisioning platform",
             "inputSchema": {"type": "object", "properties": {
                 "signal_agent_segment_id": {"type": "string", "description": "Signal ID to activate"},
                 "decisioning_platform": {"type": "string", "description": "Target platform"}}, "required": ["signal_agent_segment_id"]}},
            {"name": "create_media_buy", "description": "Create a media buy with specified packages",
             "inputSchema": {"type": "object", "properties": {
                 "buyer_ref": {"type": "string", "description": "Buyer reference identifier"},
                 "packages": {"type": "array", "items": {"type": "object"}, "description": "List of packages with product_id and budget"}}, "required": ["buyer_ref", "packages"]}},
            {"name": "get_media_buy_delivery", "description": "Get delivery status and metrics for a media buy",
             "inputSchema": {"type": "object", "properties": {
                 "media_buy_id": {"type": "string", "description": "Media buy identifier"}}, "required": ["media_buy_id"]}},
            {"name": "verify_brand_safety", "description": "Verify brand safety for a list of properties/URLs",
             "inputSchema": {"type": "object", "properties": {
                 "properties": {"type": "array", "items": {"type": "object"}, "description": "List of properties to verify"}}, "required": ["properties"]}},
            {"name": "resolve_audience_reach", "description": "Resolve audience reach across channels",
             "inputSchema": {"type": "object", "properties": {
                 "channels": {"type": "array", "items": {"type": "string"}, "description": "Channels to calculate reach for"}}}},
            {"name": "configure_brand_lift_study", "description": "Configure a brand lift or measurement study",
             "inputSchema": {"type": "object", "properties": {
                 "study_name": {"type": "string", "description": "Name of the study"},
                 "study_type": {"type": "string", "description": "Type of study (brand_lift, foot_traffic, sales_lift, attribution)"},
                 "provider": {"type": "string", "description": "Measurement provider (lucid, etc.)"},
                 "metrics": {"type": "array", "items": {"type": "string"}, "description": "Metrics to measure"}}, "required": ["study_name", "study_type"]}}
        ]
    
    def add_lambda_target(self, gateway_arn: str, gateway_url: str, role_arn: str, lambda_arn: str, gateway_id: str = None) -> dict:
        """Add Lambda target to MCP Gateway using AWS CLI (agentcore CLI has a bug with Lambda ARN)"""
        target_name = f"{self.gateway_name}-lambda-target"
        logger.info(f"Adding Lambda target to gateway: {target_name}")
        logger.info(f"Lambda ARN: {lambda_arn}")
        
        # Check if target already exists
        if gateway_id:
            existing_targets = self.get_gateway_targets(gateway_id)
            for target in existing_targets:
                if target.get("name") == target_name:
                    logger.info(f"Target already exists: {target_name}")
                    return {"status": "success", "already_existed": True, "target": target}
        
        # Use AWS CLI directly (agentcore CLI doesn't allow specifying Lambda ARN)
        tool_schema = self.get_adcp_tool_schema()
        target_config = {
            "mcp": {
                "lambda": {
                    "lambdaArn": lambda_arn,
                    "toolSchema": {
                        "inlinePayload": tool_schema
                    }
                }
            }
        }
        
        credential_config = [{"credentialProviderType": "GATEWAY_IAM_ROLE"}]
        
        # Validate inputs to prevent command injection
        validated_gateway_id = self._validate_aws_identifier(gateway_id or self.gateway_name, "gateway_id")
        validated_target_name = self._validate_aws_identifier(target_name, "target_name")
        validated_region = self._validate_aws_identifier(self.region, "region")
        
        cmd = [
            "aws", "bedrock-agentcore-control", "create-gateway-target",
            "--gateway-identifier", validated_gateway_id,
            "--name", validated_target_name,
            "--description", "AdCP Lambda target for advertising protocol tools",
            "--target-configuration", json.dumps(target_config),
            "--credential-provider-configurations", json.dumps(credential_config),
            "--region", validated_region
        ]
        
        # Set up environment with AWS_PROFILE if specified
        env = os.environ.copy()
        if self.profile:
            validated_profile = self._validate_aws_profile(self.profile)
            env["AWS_PROFILE"] = validated_profile
        
        try:
            # nosemgrep: dangerous-subprocess-use-audit
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
            
            if result.returncode != 0:
                # Check if target already exists
                if "ConflictException" in result.stderr or "already exists" in result.stderr.lower():
                    logger.info(f"Target already exists: {target_name}")
                    return {"status": "success", "already_existed": True}
                # Check for permission issues - warn but don't fail deployment
                if "AccessDeniedException" in result.stderr:
                    logger.warning(f"Permission denied for CreateGatewayTarget. Your IAM role may need bedrock-agentcore:CreateGatewayTarget permission.")
                    logger.warning("The gateway was created but the Lambda target could not be added.")
                    logger.warning("You can add the target manually or update your IAM permissions and re-run.")
                    return {"status": "permission_denied", "message": result.stderr}
                logger.error(f"Target creation failed: {result.stderr}")
                return {"status": "error", "message": result.stderr}
            
            logger.info("Lambda target added successfully")
            
            # Parse response to get target details
            try:
                response_data = json.loads(result.stdout)
                return {"status": "success", "output": result.stdout, "target_id": response_data.get("targetId")}
            except json.JSONDecodeError:
                return {"status": "success", "output": result.stdout}
            
        except subprocess.TimeoutExpired:
            return {"status": "timeout"}
        except FileNotFoundError:
            return {"status": "cli_not_found", "message": "AWS CLI not found"}
    
    def deploy(self, enable_semantic_search: bool = False) -> dict:
        """Full deployment: Lambda + Gateway + Target"""
        results = {
            "stack_prefix": self.stack_prefix,
            "unique_id": self.unique_id,
            "region": self.region,
            "gateway_name": self.gateway_name,
            "lambda_name": self.lambda_name
        }
        
        # Step 1: Deploy Lambda
        logger.info("=" * 60)
        logger.info("Step 1: Deploying AdCP Lambda function")
        logger.info("=" * 60)
        try:
            lambda_arn = self.deploy_adcp_lambda()
            results["lambda_arn"] = lambda_arn
            results["lambda_status"] = "success"
        except Exception as e:
            logger.error(f"Lambda deployment failed: {e}")
            results["lambda_status"] = "failed"
            results["lambda_error"] = str(e)
            return results
        
        # Step 2: Create Gateway Role (REQUIRED for outbound auth to Lambda)
        logger.info("=" * 60)
        logger.info("Step 2: Creating Gateway IAM Role (REQUIRED)")
        logger.info("=" * 60)
        try:
            gateway_role_arn = self.create_gateway_role(lambda_arn)
            results["gateway_role_arn"] = gateway_role_arn
        except Exception as e:
            logger.error(f"FATAL: Could not create gateway role: {e}")
            logger.error("The gateway role is REQUIRED for the MCP Gateway to invoke Lambda.")
            logger.error("Check IAM permissions and service principal availability.")
            results["gateway_role_status"] = "failed"
            results["gateway_role_error"] = str(e)
            results["status"] = "gateway_role_failed"
            return results
        
        # Step 3: Create Gateway
        logger.info("=" * 60)
        logger.info("Step 3: Creating MCP Gateway with AWS_IAM authentication")
        logger.info("=" * 60)
        gateway_result = self.create_gateway(
            enable_semantic_search=enable_semantic_search,
            gateway_role_arn=gateway_role_arn
        )
        results["gateway_result"] = gateway_result
        
        if gateway_result.get("status") == "cli_not_found":
            logger.warning("AgentCore CLI not found. Lambda deployed but gateway requires manual setup.")
            logger.info("")
            logger.info("To complete setup manually:")
            logger.info(f"  1. Install CLI: pip install bedrock-agentcore-starter-toolkit")
            logger.info(f"  2. Create gateway: agentcore gateway create-mcp-gateway --name {self.gateway_name} --region {self.region}")
            logger.info(f"  3. Add Lambda target with the gateway ARN and URL from step 2")
            results["status"] = "partial"
            return results
        
        if gateway_result.get("status") != "success":
            results["status"] = "gateway_failed"
            return results
        
        # Step 4: Add Lambda target (if we have gateway info)
        if gateway_result.get("gateway_arn") and gateway_result.get("gateway_url"):
            logger.info("=" * 60)
            logger.info("Step 4: Adding Lambda target to gateway")
            logger.info("=" * 60)
            
            # Use gateway's role ARN if available, otherwise use Lambda role
            role_arn = gateway_result.get("role_arn")
            if not role_arn:
                role_response = self.iam_client.get_role(RoleName=self.role_name)
                role_arn = role_response["Role"]["Arn"]
            
            target_result = self.add_lambda_target(
                gateway_result["gateway_arn"],
                gateway_result["gateway_url"],
                role_arn,
                lambda_arn,
                gateway_id=gateway_result.get("gateway_id")
            )
            results["target_result"] = target_result
            
            if target_result.get("status") == "permission_denied":
                # Permission issue - gateway created but target not added
                # Continue with partial success so deployment doesn't fail
                logger.warning("Deployment partially complete - target requires manual setup or IAM fix")
                results["status"] = "partial"
                results["gateway_url"] = gateway_result.get("gateway_url")
                return results
            elif target_result.get("status") not in ["success"]:
                logger.error("Failed to add Lambda target to gateway")
                results["status"] = "target_failed"
                return results
        else:
            logger.warning("Gateway ARN or URL not available - cannot add Lambda target")
            logger.warning("You may need to manually add the Lambda target to the gateway")
            results["status"] = "partial"
            return results
        
        # Step 5: Create Gateway Invoke Role (for agents to call the gateway)
        if gateway_result.get("gateway_id"):
            logger.info("=" * 60)
            logger.info("Step 5: Creating Gateway Invoke Role")
            logger.info("=" * 60)
            try:
                invoke_role_arn = self.create_gateway_invoke_role(gateway_result["gateway_id"])
                results["invoke_role_arn"] = invoke_role_arn
            except Exception as e:
                logger.warning(f"Could not create invoke role: {e}")
                logger.warning("Agents will need to use their own credentials with InvokeGateway permission")
                invoke_role_arn = None
        else:
            invoke_role_arn = None
        
        # Step 6: Store Gateway URL in SSM Parameter Store
        if gateway_result.get("gateway_url"):
            logger.info("=" * 60)
            logger.info("Step 6: Storing Gateway URL in SSM Parameter Store")
            logger.info("=" * 60)
            ssm_result = self.store_gateway_url_in_ssm(gateway_result["gateway_url"])
            results["ssm_result"] = ssm_result
            if ssm_result.get("status") == "success":
                results["ssm_parameter_name"] = ssm_result.get("parameter_name")
        
        results["status"] = "success"
        
        # Print summary
        logger.info("")
        logger.info("=" * 60)
        logger.info("DEPLOYMENT COMPLETE")
        logger.info("=" * 60)
        logger.info(f"Lambda ARN: {results.get('lambda_arn')}")
        if gateway_result.get("gateway_url"):
            logger.info(f"Gateway URL: {gateway_result['gateway_url']}")
            logger.info(f"Gateway ID: {gateway_result.get('gateway_id')}")
            if results.get("ssm_parameter_name"):
                logger.info(f"SSM Parameter: {results['ssm_parameter_name']}")
            if results.get("invoke_role_arn"):
                logger.info(f"Invoke Role ARN: {results['invoke_role_arn']}")
            logger.info("")
            logger.info("=" * 60)
            logger.info("AUTHENTICATION SETUP")
            logger.info("=" * 60)
            logger.info("This gateway uses AWS IAM (SigV4) authentication.")
            logger.info("")
            logger.info("To enable MCP integration in your agents:")
            logger.info(f"  export ADCP_USE_MCP=true")
            logger.info(f"  export ADCP_GATEWAY_URL={gateway_result['gateway_url']}")
            logger.info("")
            logger.info("Your AWS credentials must have permission to invoke the gateway.")
            if results.get("invoke_role_arn"):
                logger.info(f"You can assume the invoke role: {results['invoke_role_arn']}")
            logger.info("")
            logger.info("The MCP client will automatically sign requests with SigV4.")
        
        return results


def main():
    parser = argparse.ArgumentParser(description="Deploy AdCP MCP Gateway for Agentic Advertising")
    parser.add_argument("--stack-prefix", required=True, help="Stack prefix for resource naming")
    parser.add_argument("--unique-id", required=True, help="Unique identifier for this deployment")
    parser.add_argument("--region", default="us-east-1", help="AWS region (default: us-east-1)")
    parser.add_argument("--profile", help="AWS profile name")
    parser.add_argument("--lambda-only", action="store_true", help="Deploy only Lambda (skip gateway)")
    parser.add_argument("--enable-semantic-search", action="store_true", default=True,
                        help="Enable semantic search on the gateway (enabled by default)")
    parser.add_argument("--target-only", action="store_true", 
                        help="Only add Lambda target to existing gateway (skip gateway creation)")
    
    args = parser.parse_args()
    
    logger.info(f"Starting AdCP Gateway deployment...")
    logger.info(f"  Stack Prefix: {args.stack_prefix}")
    logger.info(f"  Unique ID: {args.unique_id}")
    logger.info(f"  Region: {args.region}")
    logger.info(f"  Profile: {args.profile or 'default'}")
    logger.info(f"  Semantic Search: {'enabled' if args.enable_semantic_search else 'disabled'}")
    
    try:
        deployer = AdCPGatewayDeployer(
            stack_prefix=args.stack_prefix,
            unique_id=args.unique_id,
            region=args.region,
            profile=args.profile
        )
    except Exception as e:
        error_result = {
            "status": "error",
            "message": f"Failed to initialize deployer: {str(e)}",
            "hint": "Check AWS credentials and profile configuration"
        }
        print(json.dumps(error_result, indent=2))
        return 1
    
    if args.lambda_only:
        # Just deploy Lambda
        try:
            lambda_arn = deployer.deploy_adcp_lambda()
            print(json.dumps({"status": "success", "lambda_arn": lambda_arn}, indent=2))
            return 0
        except Exception as e:
            print(json.dumps({"status": "error", "message": str(e)}, indent=2))
            return 1
    
    if args.target_only:
        # Just add Lambda target to existing gateway
        try:
            lambda_arn = deployer.deploy_adcp_lambda()
            gateway_info = deployer.get_existing_gateway()
            
            if gateway_info.get("status") != "exists":
                print(json.dumps({"status": "error", "message": "Gateway not found. Create gateway first."}, indent=2))
                return 1
            
            role_arn = gateway_info.get("role_arn")
            if not role_arn:
                role_response = deployer.iam_client.get_role(RoleName=deployer.role_name)
                role_arn = role_response["Role"]["Arn"]
            
            target_result = deployer.add_lambda_target(
                gateway_info["gateway_arn"],
                gateway_info["gateway_url"],
                role_arn,
                lambda_arn,
                gateway_id=gateway_info.get("gateway_id")
            )
            
            result = {
                "status": target_result.get("status"),
                "lambda_arn": lambda_arn,
                "gateway_url": gateway_info.get("gateway_url"),
                "target_result": target_result
            }
            print(json.dumps(result, indent=2, default=str))
            return 0 if target_result.get("status") == "success" else 1
        except Exception as e:
            print(json.dumps({"status": "error", "message": str(e)}, indent=2))
            return 1
    
    # Full deployment
    try:
        result = deployer.deploy(enable_semantic_search=args.enable_semantic_search)
        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("status") in ["success", "partial"] else 1
    except Exception as e:
        error_result = {
            "status": "error",
            "message": f"Deployment failed: {str(e)}"
        }
        print(json.dumps(error_result, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
