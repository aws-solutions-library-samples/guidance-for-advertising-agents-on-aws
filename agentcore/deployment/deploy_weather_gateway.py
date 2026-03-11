#!/usr/bin/env python3
"""
Weather MCP Gateway Deployer for AgentCore

Deploys the mcp-weather-free MCP server (https://github.com/microagents/mcp-weather-free)
as an AgentCore MCP Gateway with Cognito OAuth authentication.

Uses the same Cognito app client and OAuth discovery authentication pattern as
the workflow-execution-stack.ts reference implementation.

Usage:
    python deploy_weather_gateway.py --stack-prefix <prefix> --unique-id <id> --region <region>

Example:
    python deploy_weather_gateway.py --stack-prefix sim --unique-id abc123 --region us-east-1
"""

import argparse
import boto3
import json
import logging
import os
import re
import subprocess
import time
import zipfile
import io

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("WeatherGatewayDeployer")


class WeatherGatewayDeployer:
    """Deploys a Weather MCP Gateway with Cognito OAuth authentication."""

    @staticmethod
    def _validate_aws_identifier(value: str, name: str) -> str:
        if not re.match(r'^[a-zA-Z0-9_\-\.]+$', value):
            raise ValueError(f"Invalid {name}: {value}")
        return value

    @staticmethod
    def _validate_aws_profile(profile: str) -> str:
        if not re.match(r'^[a-zA-Z0-9_\-\.]+$', profile):
            raise ValueError(f"Invalid profile: {profile}")
        return profile

    def __init__(self, stack_prefix: str, unique_id: str, region: str = "us-east-1", profile: str = None):
        self.stack_prefix = stack_prefix
        self.unique_id = unique_id
        self.region = region
        self.profile = profile

        try:
            if profile:
                logger.info(f"Using AWS profile: {profile}")
                session = boto3.Session(profile_name=profile, region_name=region)
            else:
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
            self.cognito_client = session.client("cognito-idp")
            self.ssm_client = session.client("ssm")
            self.account_id = self.sts_client.get_caller_identity()["Account"]
            logger.info(f"Authenticated to AWS account: {self.account_id}")
        except Exception as e:
            logger.error(f"Failed to initialize AWS clients: {e}")
            raise

        self._session = session
        self.gateway_name = f"{stack_prefix}-weather-gateway-{unique_id}"
        self.lambda_name = f"{stack_prefix}-weather-handler-{unique_id}"
        self.role_name = f"{stack_prefix}-weather-lambda-role-{unique_id}"
        self.gateway_role_name = f"{stack_prefix}-weather-gw-role-{unique_id}"
        self.user_pool_name = f"{stack_prefix}-weather-gw-agents-{unique_id}"
        self.client_name = f"{stack_prefix}-weather-gw-client-{unique_id}"


    # =========================================================================
    # Cognito OAuth Setup (mirrors workflow-execution-stack.ts pattern)
    # =========================================================================

    def get_or_create_cognito_resources(self) -> dict:
        """
        Create a dedicated Cognito User Pool and App Client for the weather gateway,
        matching the pattern from workflow-execution-stack.ts:
        - Separate user pool isolated from the web app
        - App client with userPassword and userSrp auth flows
        - Returns pool_id, client_id, and discovery_url
        """
        pool_id = None
        client_id = None

        # Check if user pool already exists
        try:
            paginator = self.cognito_client.get_paginator('list_user_pools')
            for page in paginator.paginate(MaxResults=60):
                for pool in page.get('UserPools', []):
                    if pool['Name'] == self.user_pool_name:
                        pool_id = pool['Id']
                        logger.info(f"Found existing user pool: {pool_id}")
                        break
                if pool_id:
                    break
        except Exception as e:
            logger.warning(f"Could not list user pools: {e}")

        # Create user pool if not found
        if not pool_id:
            logger.info(f"Creating Cognito User Pool: {self.user_pool_name}")
            response = self.cognito_client.create_user_pool(
                PoolName=self.user_pool_name,
                Policies={
                    'PasswordPolicy': {
                        'MinimumLength': 8,
                        'RequireLowercase': True,
                        'RequireUppercase': True,
                        'RequireNumbers': True,
                        'RequireSymbols': False,
                    }
                },
                AdminCreateUserConfig={
                    'AllowAdminCreateUserOnly': True,
                },
                AccountRecoverySetting={
                    'RecoveryMechanisms': [
                        {'Priority': 1, 'Name': 'admin_only'},
                    ]
                },
            )
            pool_id = response['UserPool']['Id']
            logger.info(f"Created user pool: {pool_id}")

        # Check if app client already exists
        try:
            resp = self.cognito_client.list_user_pool_clients(
                UserPoolId=pool_id, MaxResults=60
            )
            for client in resp.get('UserPoolClients', []):
                if client['ClientName'] == self.client_name:
                    client_id = client['ClientId']
                    logger.info(f"Found existing app client: {client_id}")
                    break
        except Exception as e:
            logger.warning(f"Could not list user pool clients: {e}")

        # Create app client if not found
        if not client_id:
            logger.info(f"Creating App Client: {self.client_name}")
            response = self.cognito_client.create_user_pool_client(
                UserPoolId=pool_id,
                ClientName=self.client_name,
                ExplicitAuthFlows=[
                    'ALLOW_USER_PASSWORD_AUTH',
                    'ALLOW_USER_SRP_AUTH',
                    'ALLOW_REFRESH_TOKEN_AUTH',
                ],
                PreventUserExistenceErrors='ENABLED',
                GenerateSecret=False,
            )
            client_id = response['UserPoolClient']['ClientId']
            logger.info(f"Created app client: {client_id}")

        discovery_url = f"https://cognito-idp.{self.region}.amazonaws.com/{pool_id}/.well-known/openid-configuration"

        return {
            "pool_id": pool_id,
            "client_id": client_id,
            "discovery_url": discovery_url,
        }

    # =========================================================================
    # Lambda Function
    # =========================================================================

    def create_lambda_execution_role(self) -> str:
        """Create IAM role for the weather Lambda function."""
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
                Description="Execution role for Weather MCP Lambda"
            )
            role_arn = response["Role"]["Arn"]
            logger.info(f"Created IAM role: {role_arn}")

            self.iam_client.attach_role_policy(
                RoleName=self.role_name,
                PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
            )

            logger.info("Waiting for IAM role propagation (10 seconds)...")
            time.sleep(10)  # nosemgrep: arbitrary-sleep
            return role_arn

        except self.iam_client.exceptions.EntityAlreadyExistsException:
            response = self.iam_client.get_role(RoleName=self.role_name)
            logger.info(f"Using existing IAM role: {response['Role']['Arn']}")
            return response["Role"]["Arn"]

    def create_weather_lambda_code(self) -> bytes:
        """Create a Lambda zip that implements the weather tools using Open-Meteo API."""
        lambda_code = '''\
import json
import urllib.request
import urllib.parse
import urllib.error

WEATHER_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}


def geocode_city(city, country_code=None):
    """Geocode a city name to coordinates using Open-Meteo geocoding API."""
    params = {"name": city, "count": 1, "language": "en", "format": "json"}
    if country_code:
        params["country"] = country_code
    url = "https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
    results = data.get("results", [])
    if not results:
        return None
    r = results[0]
    return {
        "name": r.get("name"),
        "latitude": r.get("latitude"),
        "longitude": r.get("longitude"),
        "country": r.get("country"),
        "timezone": r.get("timezone"),
    }


def fetch_weather(latitude, longitude, temperature_unit="celsius", wind_speed_unit="kmh", precipitation_unit="mm"):
    """Fetch weather data from Open-Meteo API."""
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,wind_direction_10m,precipitation",
        "hourly": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m,precipitation_probability",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max",
        "temperature_unit": temperature_unit,
        "wind_speed_unit": wind_speed_unit,
        "precipitation_unit": precipitation_unit,
        "timezone": "auto",
        "forecast_days": 7,
        "forecast_hours": 24,
    }
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def format_weather_response(data, location_name=None):
    """Format Open-Meteo response into a clean structure."""
    current = data.get("current", {})
    hourly = data.get("hourly", {})
    daily = data.get("daily", {})

    weather_code = current.get("weather_code", 0)
    result = {
        "location": {
            "name": location_name or "Unknown",
            "latitude": data.get("latitude"),
            "longitude": data.get("longitude"),
            "timezone": data.get("timezone"),
            "elevation": data.get("elevation"),
        },
        "current": {
            "temperature": current.get("temperature_2m"),
            "apparent_temperature": current.get("apparent_temperature"),
            "humidity": current.get("relative_humidity_2m"),
            "wind_speed": current.get("wind_speed_10m"),
            "wind_direction": current.get("wind_direction_10m"),
            "precipitation": current.get("precipitation"),
            "weather_description": WEATHER_CODES.get(weather_code, "Unknown"),
            "weather_code": weather_code,
        },
        "hourly_forecast": [],
        "daily_forecast": [],
    }

    # Build hourly forecast (next 24 hours)
    times = hourly.get("time", [])[:24]
    for i, t in enumerate(times):
        result["hourly_forecast"].append({
            "time": t,
            "temperature": hourly.get("temperature_2m", [None])[i] if i < len(hourly.get("temperature_2m", [])) else None,
            "humidity": hourly.get("relative_humidity_2m", [None])[i] if i < len(hourly.get("relative_humidity_2m", [])) else None,
            "weather_description": WEATHER_CODES.get(hourly.get("weather_code", [0])[i], "Unknown") if i < len(hourly.get("weather_code", [])) else "Unknown",
            "wind_speed": hourly.get("wind_speed_10m", [None])[i] if i < len(hourly.get("wind_speed_10m", [])) else None,
            "precipitation_probability": hourly.get("precipitation_probability", [None])[i] if i < len(hourly.get("precipitation_probability", [])) else None,
        })

    # Build daily forecast (7 days)
    days = daily.get("time", [])[:7]
    for i, d in enumerate(days):
        result["daily_forecast"].append({
            "date": d,
            "temperature_max": daily.get("temperature_2m_max", [None])[i] if i < len(daily.get("temperature_2m_max", [])) else None,
            "temperature_min": daily.get("temperature_2m_min", [None])[i] if i < len(daily.get("temperature_2m_min", [])) else None,
            "weather_description": WEATHER_CODES.get(daily.get("weather_code", [0])[i], "Unknown") if i < len(daily.get("weather_code", [])) else "Unknown",
            "precipitation_sum": daily.get("precipitation_sum", [None])[i] if i < len(daily.get("precipitation_sum", [])) else None,
            "wind_speed_max": daily.get("wind_speed_10m_max", [None])[i] if i < len(daily.get("wind_speed_10m_max", [])) else None,
        })

    return result


def handle_get_weather(params):
    """Handle get_weather tool call."""
    latitude = params.get("latitude")
    longitude = params.get("longitude")
    if latitude is None or longitude is None:
        return {"error": "latitude and longitude are required"}

    location_name = params.get("location_name", f"{latitude}, {longitude}")
    temperature_unit = params.get("temperature_unit", "celsius")
    wind_speed_unit = params.get("wind_speed_unit", "kmh")
    precipitation_unit = params.get("precipitation_unit", "mm")

    data = fetch_weather(latitude, longitude, temperature_unit, wind_speed_unit, precipitation_unit)
    return format_weather_response(data, location_name)


def handle_get_weather_by_city(params):
    """Handle get_weather_by_city tool call."""
    city = params.get("city")
    if not city:
        return {"error": "city is required"}

    country_code = params.get("country_code")
    geo = geocode_city(city, country_code)
    if not geo:
        return {"error": f"Could not find city: {city}"}

    temperature_unit = params.get("temperature_unit", "celsius")
    wind_speed_unit = params.get("wind_speed_unit", "kmh")
    precipitation_unit = params.get("precipitation_unit", "mm")

    data = fetch_weather(geo["latitude"], geo["longitude"], temperature_unit, wind_speed_unit, precipitation_unit)
    return format_weather_response(data, geo["name"])


TOOL_HANDLERS = {
    "get_weather": handle_get_weather,
    "get_weather_by_city": handle_get_weather_by_city,
}


def handler(event, context):
    """Lambda handler for weather MCP gateway target."""
    try:
        # Parse tool name and parameters from the gateway invocation
        body = event
        if isinstance(event.get("body"), str):
            body = json.loads(event["body"])

        tool_name = body.get("name") or body.get("tool_name") or body.get("toolName")
        params = body.get("input") or body.get("parameters") or body.get("arguments") or {}

        if not tool_name:
            # Try MCP-style request format
            method = body.get("method")
            if method == "tools/call":
                tool_params = body.get("params", {})
                tool_name = tool_params.get("name")
                params = tool_params.get("arguments", {})

        if not tool_name or tool_name not in TOOL_HANDLERS:
            return {
                "statusCode": 400,
                "body": json.dumps({
                    "error": f"Unknown tool: {tool_name}. Available: {list(TOOL_HANDLERS.keys())}"
                })
            }

        result = TOOL_HANDLERS[tool_name](params)

        return {
            "statusCode": 200,
            "body": json.dumps(result, default=str)
        }

    except urllib.error.URLError as e:
        return {"statusCode": 502, "body": json.dumps({"error": f"Weather API error: {str(e)}"})}
    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
'''

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("lambda_function.py", lambda_code)
        return buf.getvalue()

    def deploy_weather_lambda(self) -> str:
        """Deploy the weather Lambda function."""
        role_arn = self.create_lambda_execution_role()
        code_zip = self.create_weather_lambda_code()

        try:
            response = self.lambda_client.create_function(
                FunctionName=self.lambda_name,
                Runtime="python3.12",
                Role=role_arn,
                Handler="lambda_function.handler",
                Code={"ZipFile": code_zip},
                Description="Weather MCP Gateway Lambda - Open-Meteo API (mcp-weather-free)",
                Timeout=30,
                MemorySize=256,
                Environment={
                    "Variables": {
                        "STACK_PREFIX": self.stack_prefix,
                        "UNIQUE_ID": self.unique_id,
                    }
                }
            )
            logger.info(f"Created Lambda: {response['FunctionArn']}")

            logger.info("Waiting for Lambda to be active...")
            waiter = self.lambda_client.get_waiter('function_active')
            waiter.wait(FunctionName=self.lambda_name)
            return response["FunctionArn"]

        except self.lambda_client.exceptions.ResourceConflictException:
            self.lambda_client.update_function_code(
                FunctionName=self.lambda_name,
                ZipFile=code_zip
            )
            response = self.lambda_client.get_function(FunctionName=self.lambda_name)
            logger.info(f"Updated existing Lambda: {response['Configuration']['FunctionArn']}")
            return response["Configuration"]["FunctionArn"]


    # =========================================================================
    # Gateway IAM Role
    # =========================================================================

    def create_gateway_role(self, lambda_arn: str) -> str:
        """Create IAM role for the AgentCore Gateway to invoke Lambda."""
        service_principals = [
            "bedrock-agentcore.amazonaws.com",
            "gateway.bedrock-agentcore.amazonaws.com",
        ]

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

        for sp in service_principals:
            trust_policy = {
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": sp},
                    "Action": "sts:AssumeRole"
                }]
            }
            try:
                response = self.iam_client.create_role(
                    RoleName=self.gateway_role_name,
                    AssumeRolePolicyDocument=json.dumps(trust_policy),
                    Description="Role for Weather MCP Gateway to invoke Lambda"
                )
                role_arn = response["Role"]["Arn"]
                logger.info(f"Created gateway role with principal {sp}: {role_arn}")
                break
            except self.iam_client.exceptions.MalformedPolicyDocumentException as e:
                last_error = e
                continue
            except self.iam_client.exceptions.EntityAlreadyExistsException:
                response = self.iam_client.get_role(RoleName=self.gateway_role_name)
                role_arn = response["Role"]["Arn"]
                logger.info(f"Using existing gateway role: {role_arn}")
                break
            except Exception as e:
                last_error = e
                continue

        if role_arn is None:
            raise Exception(f"Could not create gateway role: {last_error}")

        self.iam_client.put_role_policy(
            RoleName=self.gateway_role_name,
            PolicyName="LambdaInvokePolicy",
            PolicyDocument=json.dumps(lambda_invoke_policy)
        )

        logger.info("Waiting for gateway role propagation (10 seconds)...")
        time.sleep(10)  # nosemgrep: arbitrary-sleep
        return role_arn

    # =========================================================================
    # Gateway Creation with Cognito OAuth
    # =========================================================================

    def get_existing_gateway(self) -> dict:
        """Check if the weather gateway already exists (SDK with pagination, then CLI fallback)."""
        # Try SDK first
        try:
            from botocore.config import Config as BotoConfig
            gateway_client = self._session.client(
                'bedrock-agentcore-control',
                region_name=self.region,
                config=BotoConfig(parameter_validation=False)
            )
            next_token = None
            while True:
                kwargs = {}
                if next_token:
                    kwargs['nextToken'] = next_token
                response = gateway_client.list_gateways(**kwargs)
                for gw in response.get('items', []):
                    if gw.get('name') == self.gateway_name:
                        gateway_id = gw.get("gatewayId")
                        logger.info(f"Found existing gateway via SDK: {self.gateway_name} (ID: {gateway_id})")
                        # list_gateways may not return gatewayUrl/gatewayArn — fetch full details
                        if gateway_id:
                            try:
                                details = gateway_client.get_gateway(gatewayIdentifier=gateway_id)
                                return {
                                    "status": "exists",
                                    "gateway_id": details.get("gatewayId", gateway_id),
                                    "gateway_arn": details.get("gatewayArn"),
                                    "gateway_url": details.get("gatewayUrl"),
                                    "role_arn": details.get("roleArn"),
                                }
                            except Exception as get_err:
                                logger.warning(f"get_gateway failed, using list data: {get_err}")
                        return {
                            "status": "exists",
                            "gateway_id": gateway_id,
                            "gateway_arn": gw.get("gatewayArn"),
                            "gateway_url": gw.get("gatewayUrl"),
                            "role_arn": gw.get("roleArn"),
                        }
                next_token = response.get('nextToken')
                if not next_token:
                    break
        except Exception as e:
            logger.warning(f"Could not check existing gateways via SDK: {e}")

        # Fallback: try AWS CLI (more reliable for some API versions)
        try:
            validated_region = self._validate_aws_identifier(self.region, "region")
            env = os.environ.copy()
            if self.profile:
                env["AWS_PROFILE"] = self._validate_aws_profile(self.profile)

            cmd = ["aws", "bedrock-agentcore-control", "list-gateways", "--region", validated_region]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)  # nosemgrep: dangerous-subprocess-use-audit

            if result.returncode == 0:
                gateways_data = json.loads(result.stdout)
                for gw in gateways_data.get("items", []):
                    if gw.get("name") == self.gateway_name:
                        gateway_id = gw.get("gatewayId")
                        logger.info(f"Found existing gateway via CLI: {self.gateway_name} (ID: {gateway_id})")

                        # Get full details
                        validated_gw_id = self._validate_aws_identifier(gateway_id, "gateway_id")
                        get_cmd = [
                            "aws", "bedrock-agentcore-control", "get-gateway",
                            "--gateway-identifier", validated_gw_id,
                            "--region", validated_region,
                        ]
                        get_result = subprocess.run(get_cmd, capture_output=True, text=True, timeout=60, env=env)  # nosemgrep: dangerous-subprocess-use-audit
                        if get_result.returncode == 0:
                            gw_details = json.loads(get_result.stdout)
                            return {
                                "status": "exists",
                                "gateway_id": gw_details.get("gatewayId"),
                                "gateway_arn": gw_details.get("gatewayArn"),
                                "gateway_url": gw_details.get("gatewayUrl"),
                                "role_arn": gw_details.get("roleArn"),
                            }
                        # Fallback with partial info from list
                        return {
                            "status": "exists",
                            "gateway_id": gateway_id,
                            "gateway_arn": gw.get("gatewayArn"),
                            "gateway_url": gw.get("gatewayUrl"),
                            "role_arn": gw.get("roleArn"),
                        }
        except Exception as e:
            logger.warning(f"Could not check existing gateways via CLI: {e}")

        return {"status": "not_found"}

    def create_gateway(self, gateway_role_arn: str, cognito_config: dict) -> dict:
        """
        Create MCP Gateway with Cognito OAuth authentication.

        Uses customJWTAuthorizer with the Cognito discovery URL and allowed clients,
        matching the pattern from workflow-execution-stack.ts:
            GatewayAuthorizer.usingCognito({ userPool, allowedClients })
        """
        logger.info(f"Creating Weather MCP Gateway: {self.gateway_name}")

        existing = self.get_existing_gateway()
        if existing.get("status") == "exists":
            logger.info(f"Gateway already exists: {self.gateway_name}")
            return {"status": "success", "already_existed": True,
                    **{k: v for k, v in existing.items() if k != "status"}}

        # Build the Cognito OAuth authorizer config
        # This mirrors the CDK construct: agentcore.GatewayAuthorizer.usingCognito()
        authorizer_config = {
            "customJWTAuthorizer": {
                "discoveryUrl": cognito_config["discovery_url"],
                "allowedClients": [cognito_config["client_id"]],
            }
        }

        try:
            from botocore.config import Config as BotoConfig
            gateway_client = self._session.client(
                'bedrock-agentcore-control',
                region_name=self.region,
                config=BotoConfig(parameter_validation=False)
            )

            create_params = {
                'name': self.gateway_name,
                'protocolType': 'MCP',
                'authorizerType': 'CUSTOM_JWT',
                'authorizerConfiguration': authorizer_config,
                'description': 'Weather MCP Gateway - Open-Meteo free weather API (mcp-weather-free)',
            }

            if gateway_role_arn:
                create_params['roleArn'] = gateway_role_arn

            logger.info("Creating gateway with Cognito OAuth authentication")
            logger.info(f"  Discovery URL: {cognito_config['discovery_url']}")
            logger.info(f"  Allowed Client: {cognito_config['client_id']}")

            response = gateway_client.create_gateway(**create_params)

            gateway_info = {
                "gateway_id": response.get("gatewayId"),
                "gateway_arn": response.get("gatewayArn"),
                "gateway_url": response.get("gatewayUrl"),
                "role_arn": response.get("roleArn"),
            }

            logger.info(f"Gateway created: {gateway_info['gateway_id']}")
            logger.info(f"Gateway URL: {gateway_info['gateway_url']}")

            logger.info("Waiting for gateway to be active (10 seconds)...")
            time.sleep(10)  # nosemgrep: arbitrary-sleep

            return {"status": "success", **gateway_info}

        except Exception as e:
            error_msg = str(e)

            # Handle ConflictException first — gateway already exists, not a real error
            if "ConflictException" in error_msg or "already exists" in error_msg.lower():
                logger.info(f"Gateway already exists (ConflictException), retrieving existing: {self.gateway_name}")
                existing = self.get_existing_gateway()
                if existing.get("status") == "exists":
                    return {"status": "success", "already_existed": True,
                            **{k: v for k, v in existing.items() if k != "status"}}
                # If we can't find it via list, return success with minimal info
                logger.warning("Gateway exists per API but could not retrieve details via list_gateways")
                return {"status": "success", "already_existed": True}

            logger.error(f"Gateway creation failed: {error_msg}")

            # Fallback: try CLI if SDK validation or API rejects the params
            if "ValidationException" in error_msg or "authorizerConfiguration" in error_msg or "Parameter validation failed" in error_msg:
                logger.info("SDK/API validation issue, falling back to CLI...")
                return self._create_gateway_via_cli(cognito_config)

            return {"status": "error", "message": error_msg}

    def _create_gateway_via_cli(self, cognito_config: dict) -> dict:
        """Fallback: Create gateway via agentcore CLI with Cognito authorizer config."""
        logger.info(f"Creating Weather MCP Gateway via CLI: {self.gateway_name}")

        validated_name = self._validate_aws_identifier(self.gateway_name, "gateway_name")
        validated_region = self._validate_aws_identifier(self.region, "region")

        authorizer_json = json.dumps({
            "customJWTAuthorizer": {
                "discoveryUrl": cognito_config["discovery_url"],
                "allowedClients": [cognito_config["client_id"]],
            }
        })

        cmd = [
            "agentcore", "gateway", "create-mcp-gateway",
            "--name", validated_name,
            "--region", validated_region,
            "--authorizer-config", authorizer_json,
        ]

        env = os.environ.copy()
        if self.profile:
            env["AWS_PROFILE"] = self._validate_aws_profile(self.profile)

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)  # nosemgrep: dangerous-subprocess-use-audit

            if result.returncode != 0:
                if "ConflictException" in result.stderr or "already exists" in result.stderr.lower():
                    existing = self.get_existing_gateway()
                    if existing.get("status") == "exists":
                        return {"status": "success", "already_existed": True,
                                **{k: v for k, v in existing.items() if k != "status"}}
                    return {"status": "success", "already_existed": True}

                logger.error(f"CLI gateway creation failed: {result.stderr}")
                return {"status": "error", "message": result.stderr}

            logger.info("Gateway created via CLI")

            gateway_info = {}
            arn_match = re.search(r"'gatewayArn':\s*'([^']+)'", result.stdout)
            url_match = re.search(r"'gatewayUrl':\s*'([^']+)'", result.stdout)
            id_match = re.search(r"'gatewayId':\s*'([^']+)'", result.stdout)

            if arn_match:
                gateway_info["gateway_arn"] = arn_match.group(1)
            if url_match:
                gateway_info["gateway_url"] = url_match.group(1)
            if id_match:
                gateway_info["gateway_id"] = id_match.group(1)

            if not gateway_info.get("gateway_arn"):
                fetched = self.get_existing_gateway()
                if fetched.get("status") == "exists":
                    gateway_info.update({k: v for k, v in fetched.items() if k != "status"})

            return {"status": "success", "output": result.stdout, **gateway_info}

        except subprocess.TimeoutExpired:
            return {"status": "timeout", "message": "Gateway creation timed out"}
        except FileNotFoundError:
            return {"status": "cli_not_found", "message": "AgentCore CLI not found"}

    # =========================================================================
    # Gateway Target (Lambda with tool schema)
    # =========================================================================

    def get_weather_tool_schema(self) -> list:
        """Return the weather tool schema matching mcp-weather-free tools."""
        return [
            {
                "name": "get_weather",
                "description": "Get current weather, hourly forecast (24h), and daily forecast (7 days) for specific coordinates using the free Open-Meteo API.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "latitude": {"type": "number", "description": "Latitude of the location"},
                        "longitude": {"type": "number", "description": "Longitude of the location"},
                        "location_name": {"type": "string", "description": "Display name for the location (optional)"},
                        "temperature_unit": {"type": "string", "description": "Temperature unit: 'celsius' (default) or 'fahrenheit'"},
                        "wind_speed_unit": {"type": "string", "description": "Wind speed unit: 'kmh' (default), 'ms', 'mph', or 'kn'"},
                        "precipitation_unit": {"type": "string", "description": "Precipitation unit: 'mm' (default) or 'inch'"},
                    },
                    "required": ["latitude", "longitude"],
                },
            },
            {
                "name": "get_weather_by_city",
                "description": "Get current weather, hourly forecast (24h), and daily forecast (7 days) by city name with automatic geocoding. Uses the free Open-Meteo API.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "Name of the city"},
                        "country_code": {"type": "string", "description": "2-letter country code (e.g., 'US', 'GB') - optional"},
                        "temperature_unit": {"type": "string", "description": "Temperature unit: 'celsius' (default) or 'fahrenheit'"},
                        "wind_speed_unit": {"type": "string", "description": "Wind speed unit: 'kmh' (default), 'ms', 'mph', or 'kn'"},
                        "precipitation_unit": {"type": "string", "description": "Precipitation unit: 'mm' (default) or 'inch'"},
                    },
                    "required": ["city"],
                },
            },
        ]

    def get_gateway_targets(self, gateway_id: str) -> list:
        """List existing targets for the gateway."""
        try:
            gateway_client = self._session.client('bedrock-agentcore-control', region_name=self.region)
            response = gateway_client.list_gateway_targets(gatewayIdentifier=gateway_id)
            return response.get('items', [])
        except Exception as e:
            logger.warning(f"Could not list gateway targets: {e}")
            return []

    def add_lambda_target(self, gateway_arn: str, gateway_url: str, role_arn: str,
                          lambda_arn: str, gateway_id: str = None) -> dict:
        """Add the weather Lambda as a gateway target with tool schema."""
        target_name = f"{self.gateway_name}-lambda-target"
        logger.info(f"Adding Lambda target: {target_name}")

        # Check if target already exists
        if gateway_id:
            for target in self.get_gateway_targets(gateway_id):
                if target.get("name") == target_name:
                    logger.info(f"Target already exists: {target_name}")
                    return {"status": "success", "already_existed": True, "target": target}

        tool_schema = self.get_weather_tool_schema()
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

        validated_gw_id = self._validate_aws_identifier(gateway_id or self.gateway_name, "gateway_id")
        validated_target = self._validate_aws_identifier(target_name, "target_name")
        validated_region = self._validate_aws_identifier(self.region, "region")

        cmd = [
            "aws", "bedrock-agentcore-control", "create-gateway-target",
            "--gateway-identifier", validated_gw_id,
            "--name", validated_target,
            "--description", "Weather Lambda target - Open-Meteo free weather API tools",
            "--target-configuration", json.dumps(target_config),
            "--credential-provider-configurations", json.dumps(credential_config),
            "--region", validated_region,
        ]

        env = os.environ.copy()
        if self.profile:
            env["AWS_PROFILE"] = self._validate_aws_profile(self.profile)

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)  # nosemgrep: dangerous-subprocess-use-audit

            if result.returncode != 0:
                if "ConflictException" in result.stderr or "already exists" in result.stderr.lower():
                    logger.info(f"Target already exists: {target_name}")
                    return {"status": "success", "already_existed": True}
                if "AccessDeniedException" in result.stderr:
                    logger.warning("Permission denied for CreateGatewayTarget")
                    return {"status": "permission_denied", "message": result.stderr}
                logger.error(f"Target creation failed: {result.stderr}")
                return {"status": "error", "message": result.stderr}

            logger.info("Lambda target added successfully")
            try:
                response_data = json.loads(result.stdout)
                return {"status": "success", "target_id": response_data.get("targetId")}
            except json.JSONDecodeError:
                return {"status": "success", "output": result.stdout}

        except subprocess.TimeoutExpired:
            return {"status": "timeout"}
        except FileNotFoundError:
            return {"status": "cli_not_found", "message": "AWS CLI not found"}

    # =========================================================================
    # SSM Parameter Storage
    # =========================================================================

    def store_gateway_url_in_ssm(self, gateway_url: str) -> dict:
        """Store the gateway URL in SSM Parameter Store."""
        param_name = f"/{self.stack_prefix}/weather_gateway/{self.unique_id}"
        try:
            self.ssm_client.put_parameter(
                Name=param_name,
                Value=gateway_url,
                Type='String',
                Overwrite=True,
                Description='Weather MCP Gateway URL'
            )
            logger.info(f"Stored gateway URL in SSM: {param_name}")
            return {"status": "success", "parameter_name": param_name}
        except Exception as e:
            logger.warning(f"Could not store in SSM: {e}")
            return {"status": "error", "message": str(e)}

    def store_cognito_config_in_ssm(self, cognito_config: dict) -> dict:
        """Store the Cognito auth config in SSM for agents to use."""
        param_name = f"/{self.stack_prefix}/weather_gateway_auth/{self.unique_id}"
        try:
            self.ssm_client.put_parameter(
                Name=param_name,
                Value=json.dumps(cognito_config),
                Type='SecureString',
                Overwrite=True,
                Description='Weather MCP Gateway Cognito OAuth configuration'
            )
            logger.info(f"Stored Cognito config in SSM: {param_name}")
            return {"status": "success", "parameter_name": param_name}
        except Exception as e:
            logger.warning(f"Could not store Cognito config in SSM: {e}")
            return {"status": "error", "message": str(e)}


    # =========================================================================
    # Full Deployment Orchestration
    # =========================================================================

    def deploy(self) -> dict:
        """Full deployment: Cognito + Lambda + Gateway + Target."""
        results = {
            "stack_prefix": self.stack_prefix,
            "unique_id": self.unique_id,
            "region": self.region,
            "gateway_name": self.gateway_name,
            "lambda_name": self.lambda_name,
        }

        # Step 1: Create Cognito User Pool and App Client
        logger.info("=" * 60)
        logger.info("Step 1: Setting up Cognito OAuth authentication")
        logger.info("=" * 60)
        try:
            cognito_config = self.get_or_create_cognito_resources()
            results["cognito_config"] = cognito_config
            logger.info(f"  Pool ID: {cognito_config['pool_id']}")
            logger.info(f"  Client ID: {cognito_config['client_id']}")
            logger.info(f"  Discovery URL: {cognito_config['discovery_url']}")
        except Exception as e:
            logger.error(f"Cognito setup failed: {e}")
            results["status"] = "cognito_failed"
            results["error"] = str(e)
            return results

        # Step 2: Deploy Lambda
        logger.info("=" * 60)
        logger.info("Step 2: Deploying Weather Lambda function")
        logger.info("=" * 60)
        try:
            lambda_arn = self.deploy_weather_lambda()
            results["lambda_arn"] = lambda_arn
        except Exception as e:
            logger.error(f"Lambda deployment failed: {e}")
            results["status"] = "lambda_failed"
            results["error"] = str(e)
            return results

        # Step 3: Create Gateway Role
        logger.info("=" * 60)
        logger.info("Step 3: Creating Gateway IAM Role")
        logger.info("=" * 60)
        try:
            gateway_role_arn = self.create_gateway_role(lambda_arn)
            results["gateway_role_arn"] = gateway_role_arn
        except Exception as e:
            logger.error(f"Gateway role creation failed: {e}")
            results["status"] = "gateway_role_failed"
            results["error"] = str(e)
            return results

        # Step 4: Create Gateway with Cognito OAuth
        logger.info("=" * 60)
        logger.info("Step 4: Creating MCP Gateway with Cognito OAuth authentication")
        logger.info("=" * 60)
        gateway_result = self.create_gateway(
            gateway_role_arn=gateway_role_arn,
            cognito_config=cognito_config,
        )
        results["gateway_result"] = gateway_result

        if gateway_result.get("status") == "cli_not_found":
            logger.warning("AgentCore CLI not found. Lambda deployed but gateway requires manual setup.")
            results["status"] = "partial"
            return results

        if gateway_result.get("status") != "success":
            results["status"] = "gateway_failed"
            return results

        # Step 5: Add Lambda target
        if gateway_result.get("gateway_arn") and gateway_result.get("gateway_url"):
            logger.info("=" * 60)
            logger.info("Step 5: Adding Lambda target to gateway")
            logger.info("=" * 60)

            role_arn = gateway_result.get("role_arn") or gateway_role_arn

            target_result = self.add_lambda_target(
                gateway_result["gateway_arn"],
                gateway_result["gateway_url"],
                role_arn,
                lambda_arn,
                gateway_id=gateway_result.get("gateway_id"),
            )
            results["target_result"] = target_result

            if target_result.get("status") not in ["success"]:
                if target_result.get("status") == "permission_denied":
                    logger.warning("Target requires manual setup or IAM fix")
                    results["status"] = "partial"
                    results["gateway_url"] = gateway_result.get("gateway_url")
                    return results
                results["status"] = "target_failed"
                return results
        else:
            logger.warning("Gateway ARN/URL not available - cannot add target")
            results["status"] = "partial"
            return results

        # Step 6: Store in SSM
        if gateway_result.get("gateway_url"):
            logger.info("=" * 60)
            logger.info("Step 6: Storing configuration in SSM Parameter Store")
            logger.info("=" * 60)
            ssm_result = self.store_gateway_url_in_ssm(gateway_result["gateway_url"])
            results["ssm_result"] = ssm_result

            auth_ssm_result = self.store_cognito_config_in_ssm(cognito_config)
            results["auth_ssm_result"] = auth_ssm_result

        results["status"] = "success"
        results["gateway_url"] = gateway_result.get("gateway_url")

        # Print summary
        logger.info("")
        logger.info("=" * 60)
        logger.info("WEATHER GATEWAY DEPLOYMENT COMPLETE")
        logger.info("=" * 60)
        logger.info(f"Lambda ARN: {results.get('lambda_arn')}")
        logger.info(f"Gateway URL: {gateway_result.get('gateway_url')}")
        logger.info(f"Gateway ID: {gateway_result.get('gateway_id')}")
        logger.info("")
        logger.info("AUTHENTICATION: Cognito OAuth (customJWTAuthorizer)")
        logger.info(f"  User Pool ID: {cognito_config['pool_id']}")
        logger.info(f"  Client ID: {cognito_config['client_id']}")
        logger.info(f"  Discovery URL: {cognito_config['discovery_url']}")
        logger.info("")
        logger.info("AVAILABLE TOOLS:")
        logger.info("  - get_weather(latitude, longitude, ...)")
        logger.info("  - get_weather_by_city(city, country_code, ...)")

        return results


def main():
    parser = argparse.ArgumentParser(description="Deploy Weather MCP Gateway with Cognito OAuth")
    parser.add_argument("--stack-prefix", required=True, help="Stack prefix for resource naming")
    parser.add_argument("--unique-id", required=True, help="Unique identifier for this deployment")
    parser.add_argument("--region", default="us-east-1", help="AWS region (default: us-east-1)")
    parser.add_argument("--profile", help="AWS profile name")

    args = parser.parse_args()

    logger.info("Starting Weather Gateway deployment...")
    logger.info(f"  Stack Prefix: {args.stack_prefix}")
    logger.info(f"  Unique ID: {args.unique_id}")
    logger.info(f"  Region: {args.region}")
    logger.info(f"  Profile: {args.profile or 'default'}")

    try:
        deployer = WeatherGatewayDeployer(
            stack_prefix=args.stack_prefix,
            unique_id=args.unique_id,
            region=args.region,
            profile=args.profile,
        )
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}, indent=2))
        return 1

    try:
        result = deployer.deploy()
        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("status") in ["success", "partial"] else 1
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}, indent=2))
        return 1


if __name__ == "__main__":
    exit(main())
