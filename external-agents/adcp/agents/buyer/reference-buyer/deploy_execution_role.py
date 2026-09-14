"""Create the IAM role AgentCore Runtime assumes to run the buyer agent.

    .venv/bin/python deploy_execution_role.py            # dry run, prints both policies
    .venv/bin/python deploy_execution_role.py --apply

`deploy_configure.py` does `EXECUTION_ROLE_ARN = os.environ["EXECUTION_ROLE_ARN"]` at import, so
neither buyer runtime can be configured without this role. Nothing in the repo created it -- the
README listed it as a manual prerequisite, which an open-source consumer has no way to satisfy and
which made a deploy into an empty account impossible. Both runtimes (HTTP and A2A) share it, as they
always have.

## Where the policy comes from

The base statements are the AgentCore Runtime execution role from the AWS documentation
(https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html): ECR image
pull, the four CloudWatch Logs statements, X-Ray, `cloudwatch:PutMetricData` scoped to the
`bedrock-agentcore` namespace, workload access tokens, and Bedrock model invocation. On top of that
this agent needs its session bucket and its reasoning-step table.

One deliberate narrowing from the documented policy: `bedrock-agentcore:GetWorkloadAccessTokenForUserId`
is omitted. AWS's own guidance is to grant only `GetWorkloadAccessTokenForJWT` where the application
has JWTs, and this one does -- every request arrives with a Cognito token.

The Cognito user-administration statements are not here; `deploy_user_admin_policy.py` attaches those
separately as their own inline policy, so the UI's Users panel remains an opt-in capability.
"""

from __future__ import annotations

from aws_region import region

import argparse
import functools
import json
import os
import sys

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv()

#: Instance prefix (see deploy_all.resolve_prefix): the auto-generated unique id every resource name
#: derives from. Defaults to 'adcp' (the original instance) for a standalone run; deploy_all.py
#: exports INSTANCE_PREFIX so orchestrated runs inherit the right one. Note the two joiners: a hyphen
#: for the IAM role / bucket names, an underscore for the runtime names (AgentCore runtime names
#: forbid hyphens). The prefix itself is bare alphanumeric, so both are safe.
INSTANCE_PREFIX = os.environ.get("INSTANCE_PREFIX", "adcp").strip() or "adcp"

ROLE_NAME = f"{INSTANCE_PREFIX}-buyer-agent-agentcore-execution"
INLINE_POLICY_NAME = f"{INSTANCE_PREFIX}-buyer-agent-runtime"
DESCRIPTION = "Execution role for the AdCP buyer agent's AgentCore runtimes (HTTP and A2A)."

REGION = region()

#: Both runtimes' names, used to scope the workload-identity grant. Must match `AGENT_NAME` in
#: deploy_configure.py and deploy_buyer_agent_a2a.py (all three derive from INSTANCE_PREFIX).
AGENT_NAMES = (f"{INSTANCE_PREFIX}_buyer_agent", f"{INSTANCE_PREFIX}_buyer_agent_a2a")

SESSION_BUCKET_STEM = f"{INSTANCE_PREFIX}-buyer-agent-sessions"
SESSIONS_TABLE_NAME = os.environ.get(
    "SESSIONS_TABLE_NAME", f"{INSTANCE_PREFIX}-buyer-agent-sessions"
)


@functools.lru_cache(maxsize=1)
def account_id() -> str:
    return os.environ.get("AWS_ACCOUNT_ID", "").strip() or boto3.client(
        "sts"
    ).get_caller_identity()["Account"]


def session_bucket() -> str:
    """The session bucket this role is granted access to.

    A pinned `SESSION_STORAGE_BUCKET` (what deploy_all.py sets, to `<prefix>-buyer-agent-sessions`)
    wins; otherwise the default is that same prefix-derived name. The name no longer carries an
    account, so there is nothing account-specific to validate here -- the prefix is the instance
    discriminator, and deploy_session_bucket.py's bucket_name() derives the create side identically.
    """
    pinned = os.environ.get("SESSION_STORAGE_BUCKET", "").strip()
    if pinned:
        return pinned
    return SESSION_BUCKET_STEM


def trust_policy() -> dict:
    """Who may assume this role.

    The SourceAccount/SourceArn conditions are confused-deputy protection: without them the service
    principal could be induced to assume this role on behalf of a different customer's resource.
    """
    account = account_id()
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AssumeRolePolicy",
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:bedrock-agentcore:{REGION}:{account}:*"
                    },
                },
            }
        ],
    }


def permissions_policy() -> dict:
    account = account_id()
    logs_prefix = f"arn:aws:logs:{REGION}:{account}:log-group"
    runtimes_group = f"{logs_prefix}:/aws/bedrock-agentcore/runtimes"
    identity_dir = f"arn:aws:bedrock-agentcore:{REGION}:{account}:workload-identity-directory/default"

    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ECRImageAccess",
                "Effect": "Allow",
                "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
                "Resource": [f"arn:aws:ecr:{REGION}:{account}:repository/*"],
            },
            {
                "Sid": "ECRTokenAccess",
                "Effect": "Allow",
                "Action": ["ecr:GetAuthorizationToken"],
                # GetAuthorizationToken is account-wide by design; it takes no resource.
                "Resource": "*",
            },
            {
                "Sid": "LogGroupLifecycle",
                "Effect": "Allow",
                "Action": ["logs:DescribeLogStreams", "logs:CreateLogGroup"],
                "Resource": [f"{runtimes_group}/*"],
            },
            {
                "Sid": "LogResourcePolicy",
                "Effect": "Allow",
                "Action": ["logs:PutResourcePolicy"],
                "Resource": [f"{runtimes_group}/{name}-*" for name in AGENT_NAMES],
            },
            {
                "Sid": "DescribeLogGroups",
                "Effect": "Allow",
                "Action": ["logs:DescribeLogGroups"],
                "Resource": [f"{logs_prefix}:*"],
            },
            {
                "Sid": "WriteLogs",
                "Effect": "Allow",
                "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                "Resource": [f"{runtimes_group}/*:log-stream:*"],
            },
            {
                "Sid": "Tracing",
                "Effect": "Allow",
                "Action": [
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                    "xray:GetSamplingRules",
                    "xray:GetSamplingTargets",
                ],
                "Resource": ["*"],
            },
            {
                "Sid": "Metrics",
                "Effect": "Allow",
                "Action": "cloudwatch:PutMetricData",
                "Resource": "*",
                "Condition": {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}},
            },
            {
                # ForUserId deliberately omitted -- see module docstring.
                "Sid": "GetAgentAccessToken",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:GetWorkloadAccessToken",
                    "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                ],
                "Resource": [
                    identity_dir,
                    *[f"{identity_dir}/workload-identity/{name}-*" for name in AGENT_NAMES],
                ],
            },
            {
                "Sid": "BedrockModelInvocation",
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                # The account-scoped entry covers inference profiles, which is what
                # BEDROCK_MODEL_ID names (`us.anthropic.claude-sonnet-5`); a cross-region profile
                # also routes to foundation models in other regions, hence the wildcard region on
                # the first entry.
                "Resource": [
                    "arn:aws:bedrock:*::foundation-model/*",
                    f"arn:aws:bedrock:{REGION}:{account}:*",
                ],
            },
            {
                "Sid": "SessionStorage",
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"],
                "Resource": [
                    f"arn:aws:s3:::{session_bucket()}",
                    f"arn:aws:s3:::{session_bucket()}/*",
                ],
            },
            {
                "Sid": "ReasoningStepRecording",
                "Effect": "Allow",
                "Action": [
                    "dynamodb:PutItem",
                    "dynamodb:UpdateItem",
                    "dynamodb:GetItem",
                    "dynamodb:Query",
                ],
                "Resource": [
                    f"arn:aws:dynamodb:{REGION}:{account}:table/{SESSIONS_TABLE_NAME}",
                    f"arn:aws:dynamodb:{REGION}:{account}:table/{SESSIONS_TABLE_NAME}/index/*",
                ],
            },
        ],
    }


def upsert_role(iam) -> str:
    trust = json.dumps(trust_policy())
    try:
        existing = iam.get_role(RoleName=ROLE_NAME)["Role"]
        print(f"  role exists: {existing['Arn']}")
        iam.update_assume_role_policy(RoleName=ROLE_NAME, PolicyDocument=trust)
        print("  trust policy converged")
        return existing["Arn"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "NoSuchEntity":
            raise

    created = iam.create_role(
        RoleName=ROLE_NAME,
        AssumeRolePolicyDocument=trust,
        Description=DESCRIPTION,
        Tags=[{"Key": "Project", "Value": "adcp-buyer-agent"}],
    )["Role"]
    print(f"  created role: {created['Arn']}")
    return created["Arn"]


def put_inline_policy(iam) -> None:
    iam.put_role_policy(
        RoleName=ROLE_NAME,
        PolicyName=INLINE_POLICY_NAME,
        PolicyDocument=json.dumps(permissions_policy()),
    )
    print(f"  inline policy written: {INLINE_POLICY_NAME}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="create/converge it; else report only")
    args = parser.parse_args()

    account = account_id()
    arn = f"arn:aws:iam::{account}:role/{ROLE_NAME}"
    print(f"account : {account}")
    print(f"region  : {REGION}")
    print(f"role    : {ROLE_NAME}")
    print(f"bucket  : {session_bucket()}")
    print(f"table   : {SESSIONS_TABLE_NAME}")
    print()

    if not args.apply:
        print("--- trust policy ---")
        print(json.dumps(trust_policy(), indent=2))
        print("--- permissions policy ---")
        print(json.dumps(permissions_policy(), indent=2))
        print("\nDry run. Nothing created. Re-run with --apply.")
        return 0

    iam = boto3.client("iam")
    arn = upsert_role(iam)
    put_inline_policy(iam)

    print()
    print("=== Done ===")
    print(f"Set EXECUTION_ROLE_ARN={arn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
