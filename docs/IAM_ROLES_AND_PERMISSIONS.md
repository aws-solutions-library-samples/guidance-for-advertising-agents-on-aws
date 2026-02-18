# IAM Roles and Permissions Guide

This document describes all IAM roles and permissions required for the Agentic Advertising Ecosystem architecture.

## Architecture Overview

The ecosystem consists of several components that require specific IAM roles:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           User Authentication                                │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────────────────┐  │
│  │   Cognito    │───▶│   Identity   │───▶│    AuthenticatedRole         │  │
│  │  User Pool   │    │     Pool     │    │  (Frontend User Access)      │  │
│  └──────────────┘    └──────────────┘    └──────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         AgentCore Runtime Layer                              │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                    AgentCore Execution Role                           │  │
│  │  • Bedrock Model Invocation                                           │  │
│  │  • AgentCore Memory Access                                            │  │
│  │  • S3 Data Access                                                     │  │
│  │  • ECR Image Pull                                                     │  │
│  │  • CloudWatch Logging                                                 │  │
│  │  • X-Ray Tracing                                                      │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          MCP Gateway Layer                                   │
│  ┌─────────────────────┐    ┌─────────────────────┐                        │
│  │   Gateway Role      │    │   Lambda Exec Role  │                        │
│  │ (Invoke Lambda)     │───▶│  (AdCP Handler)     │                        │
│  └─────────────────────┘    └─────────────────────┘                        │
│  ┌─────────────────────┐                                                    │
│  │  Gateway Invoke     │                                                    │
│  │  Role (Clients)     │                                                    │
│  └─────────────────────┘                                                    │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Knowledge Base Layer                                  │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                   BedrockExecutionRole                                │  │
│  │  • S3 Data Access (Knowledge Base Sources)                            │  │
│  │  • OpenSearch Serverless Access                                       │  │
│  │  • Bedrock Model Access                                               │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 1. Authenticated User Role (Cognito)

**Role Name:** `AuthenticatedRole-{stack-prefix}-{unique-id}`

**Purpose:** Grants permissions to authenticated frontend users accessing the application through Cognito.

**Trust Policy:**
```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Federated": "cognito-identity.amazonaws.com"
    },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "cognito-identity.amazonaws.com:aud": "{identity-pool-id}"
      },
      "ForAnyValue:StringLike": {
        "cognito-identity.amazonaws.com:amr": "authenticated"
      }
    }
  }]
}
```

**Permissions:**

| Service | Actions | Resources | Purpose |
|---------|---------|-----------|---------|
| Bedrock | `InvokeAgent`, `InvokeModel`, `GetAgent`, `ListAgents`, `GetKnowledgeBase`, `ListKnowledgeBases`, `Retrieve`, `RetrieveAndGenerate` | `arn:aws:bedrock:{region}:{account}:*`, `arn:aws:bedrock:*::foundation-model/*` | Invoke AI models and knowledge bases |
| Bedrock AgentCore | `InvokeAgentRuntime`, `InvokeAgentRuntimeForUser`, `GetMemory`, `ListMemories`, `RetrieveMemoryRecords`, `GetAgentRuntime`, `ListAgentRuntimes` | `arn:aws:bedrock-agentcore:*:{account}:*` | Invoke agent runtimes and access memory |
| S3 | `GetObject`, `PutObject`, `DeleteObject`, `ListBucket` | Data bucket, Generated content bucket | Upload/download files |
| SSM | `GetParameter` | `/{stack-prefix}/*` | Read configuration parameters |
| DynamoDB | `GetItem`, `PutItem`, `UpdateItem`, `DeleteItem`, `Query`, `Scan` | `{stack-prefix}-*` tables | Session and state management |
| Lambda | `InvokeFunction` | `{stack-prefix}-*` functions | Invoke helper functions |
| CloudWatch Logs | `CreateLogGroup`, `CreateLogStream`, `PutLogEvents` | `{stack-prefix}-*` log groups | Client-side logging |

---

## 2. AgentCore Execution Role

**Role Name:** `{stack-prefix}-{agent-name}-role-{unique-id}`

**Purpose:** Grants permissions to AgentCore agent runtimes to execute agent logic, invoke models, access memory, and interact with AWS services.

**Trust Policy:**
```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Service": "bedrock-agentcore.amazonaws.com"
    },
    "Action": "sts:AssumeRole"
  }]
}
```

**Permissions:**

| Service | Actions | Resources | Purpose |
|---------|---------|-----------|---------|
| IAM | `PassRole` | `arn:aws:iam::{account}:role/*` | Pass roles to other services |
| Bedrock AgentCore Memory | `RetrieveMemoryRecords`, `ListMemoryRecords`, `CreateEvent`, `List*`, `Create*`, `Delete*`, `Update*`, `Start*`, `Stop*` | `arn:aws:bedrock-agentcore:{region}:{account}:memory/*` | Full memory management |
| Bedrock AgentCore Gateway | `*Gateway*`, `*WorkloadIdentity`, `*CredentialProvider`, `*Token*`, `*Access*` | `arn:aws:bedrock-agentcore:*:{account}:*gateway*` | MCP Gateway access |
| Bedrock AgentCore Gateway Invoke | `InvokeGateway`, `GetGateway`, `ListGateways`, `ListGatewayTargets`, `GetGatewayTarget` | `arn:aws:bedrock-agentcore:*:{account}:*` | Invoke any gateway on the account |
| Bedrock AgentCore Identity | `GetWorkloadAccessToken`, `GetWorkloadAccessTokenForJWT`, `GetWorkloadAccessTokenForUserId` | Workload identity directory | Agent identity tokens |
| S3 | `GetObject`, `ListBucket`, `PutObject` | Data buckets, Generated content buckets | Read/write data |
| KMS | `DescribeKey`, `CreateGrant`, `Decrypt`, `GenerateDataKey` | KMS keys (via AgentCore service) | Encryption for memory |
| ECR | `GetAuthorizationToken`, `BatchCheckLayerAvailability`, `GetDownloadUrlForLayer`, `BatchGetImage`, `DescribeRepositories`, `DescribeImages`, `ListImages` | All ECR repositories | Pull container images |
| Bedrock | `InvokeAgent`, `InvokeModel`, `InvokeModelWithResponseStream`, `ApplyGuardrail`, `Retrieve`, `RetrieveAndGenerate`, `ListFoundationModels`, `ListKnowledgeBases`, `ListDataSources` | `arn:aws:bedrock:*::*/*`, `arn:aws:bedrock:*:*:*` | AI model invocation |
| Lambda | `InvokeFunction` | Image generator functions | Generate creative images |
| CloudWatch Logs | `CreateLogGroup`, `CreateLogStream`, `PutLogEvents`, `DescribeLogGroups`, `DescribeLogStreams` | `arn:aws:logs:{region}:{account}:*` | Agent logging |
| CloudWatch | `PutMetricData` | `arn:aws:cloudwatch:*:{account}:*:*` | Metrics publishing |
| X-Ray | `PutTraceSegments`, `PutTelemetryRecords`, `GetSamplingRules`, `GetSamplingTargets` | `*` | Distributed tracing |
| DynamoDB | `GetItem`, `PutItem`, `UpdateItem`, `DeleteItem`, `Query`, `Scan`, `DescribeTable` | All tables | State persistence |

---

## 3. MCP Gateway Role

**Role Name:** `{stack-prefix}-adcp-gateway-role-{unique-id}`

**Purpose:** Allows the AgentCore MCP Gateway to invoke Lambda functions that implement the Ad Context Protocol (AdCP).

**Trust Policy:**
```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Service": "bedrock-agentcore.amazonaws.com"
    },
    "Action": "sts:AssumeRole"
  }]
}
```

**Permissions:**

| Service | Actions | Resources | Purpose |
|---------|---------|-----------|---------|
| Lambda | `InvokeFunction` | AdCP Lambda function ARN | Invoke AdCP protocol handlers |

---

## 4. MCP Gateway Invoke Role

**Role Name:** `{stack-prefix}-adcp-invoke-role-{unique-id}`

**Purpose:** Allows agents and clients to invoke the MCP Gateway using SigV4 authentication.

**Trust Policy:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "bedrock-agentcore.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    },
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "{caller-arn}"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

**Permissions:**

| Service | Actions | Resources | Purpose |
|---------|---------|-----------|---------|
| Bedrock AgentCore | `InvokeGateway` | `arn:aws:bedrock-agentcore:{region}:{account}:gateway/{gateway-id}` | Invoke MCP Gateway |

---

## 5. AdCP Lambda Execution Role

**Role Name:** `{stack-prefix}-adcp-lambda-role-{unique-id}`

**Purpose:** Execution role for the Lambda function that implements AdCP protocol handlers.

**Trust Policy:**
```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Service": "lambda.amazonaws.com"
    },
    "Action": "sts:AssumeRole"
  }]
}
```

**Permissions:**

| Service | Actions | Resources | Purpose |
|---------|---------|-----------|---------|
| CloudWatch Logs | Basic execution role | Lambda log groups | Lambda logging |

**Managed Policy:** `arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole`

---

## 6. Bedrock Execution Role (Knowledge Bases)

**Role Name:** `BedrockExecutionRole-{stack-prefix}-{unique-id}`

**Purpose:** Allows Bedrock Knowledge Bases to access data sources and OpenSearch Serverless.

**Trust Policy:**
```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Service": "bedrock.amazonaws.com"
    },
    "Action": "sts:AssumeRole",
    "Condition": {
      "StringEquals": {
        "aws:SourceAccount": "{account-id}"
      },
      "ArnLike": {
        "aws:SourceArn": "arn:aws:bedrock:{region}:{account-id}:*"
      }
    }
  }]
}
```

**Permissions:**

| Service | Actions | Resources | Purpose |
|---------|---------|-----------|---------|
| S3 | `GetObject`, `PutObject`, `DeleteObject`, `ListBucket`, `GetObjectVersion` | Synthetic data bucket | Access knowledge base source documents |
| DynamoDB | `GetItem`, `PutItem`, `UpdateItem`, `DeleteItem`, `Query`, `Scan`, `BatchGetItem`, `BatchWriteItem` | `{stack-prefix}-*` tables | Knowledge base metadata |
| Lambda | `InvokeFunction`, `GetFunction` | `{stack-prefix}-*` functions | Custom transformations |
| OpenSearch Serverless | `APIAccessAll` | `arn:aws:aoss:{region}:{account}:collection/*` | Vector store access |
| Bedrock | `InvokeModel`, `InvokeModelWithResponseStream`, `Retrieve`, `RetrieveAndGenerate`, `ListFoundationModels`, `GetKnowledgeBase`, `ListKnowledgeBases` | Knowledge bases, Foundation models | Embedding and retrieval |

---

## 7. Unauthenticated Role (Cognito)

**Role Name:** `UnauthenticatedRole-{stack-prefix}-{unique-id}`

**Purpose:** Denies all access for unauthenticated users.

**Trust Policy:**
```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Federated": "cognito-identity.amazonaws.com"
    },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "cognito-identity.amazonaws.com:aud": "{identity-pool-id}"
      },
      "ForAnyValue:StringLike": {
        "cognito-identity.amazonaws.com:amr": "unauthenticated"
      }
    }
  }]
}
```

**Permissions:**
```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Deny",
    "Action": "*",
    "Resource": "*"
  }]
}
```

---

## 8. Create Demo User Lambda Role

**Role Name:** `CreateDemoUserLambdaRole-{stack-prefix}-{unique-id}`

**Purpose:** Allows the CloudFormation custom resource Lambda to create demo users in Cognito.

**Trust Policy:**
```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Service": "lambda.amazonaws.com"
    },
    "Action": "sts:AssumeRole"
  }]
}
```

**Permissions:**

| Service | Actions | Resources | Purpose |
|---------|---------|-----------|---------|
| Cognito IDP | `AdminCreateUser`, `AdminSetUserPassword`, `AdminGetUser`, `AdminDeleteUser` | User Pool | Manage demo users |

**Managed Policy:** `arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole`

---

## Deployment Permissions

The user/role deploying this architecture needs the following permissions:

### CloudFormation Deployment
- `cloudformation:*` on stack resources
- `iam:CreateRole`, `iam:DeleteRole`, `iam:AttachRolePolicy`, `iam:DetachRolePolicy`, `iam:PutRolePolicy`
- `iam:PassRole` for all created roles

### AgentCore Deployment
- `bedrock-agentcore-control:CreateAgentRuntime`
- `bedrock-agentcore-control:UpdateAgentRuntime`
- `bedrock-agentcore-control:DeleteAgentRuntime`
- `bedrock-agentcore-control:GetAgentRuntime`
- `bedrock-agentcore-control:ListAgentRuntimes`
- `bedrock-agentcore-control:CreateGateway`
- `bedrock-agentcore-control:CreateGatewayTarget`
- `bedrock-agentcore-control:ListGateways`
- `bedrock-agentcore-control:GetGateway`

### ECR Deployment
- `ecr:CreateRepository`
- `ecr:GetAuthorizationToken`
- `ecr:BatchCheckLayerAvailability`
- `ecr:PutImage`
- `ecr:InitiateLayerUpload`
- `ecr:UploadLayerPart`
- `ecr:CompleteLayerUpload`

### Lambda Deployment
- `lambda:CreateFunction`
- `lambda:UpdateFunctionCode`
- `lambda:UpdateFunctionConfiguration`
- `lambda:GetFunction`
- `lambda:DeleteFunction`

---

## Security Best Practices

1. **Least Privilege:** Each role is scoped to only the permissions required for its function.

2. **Resource Constraints:** Permissions are constrained to specific resource ARN patterns using the stack prefix and unique ID.

3. **Service Principal Conditions:** Trust policies include conditions to ensure only the intended AWS services can assume roles.

4. **No Wildcard Actions:** Avoid `*` actions except where absolutely necessary (e.g., X-Ray tracing).

5. **Encryption:** KMS permissions are scoped to the AgentCore service via condition keys.

6. **Unauthenticated Denial:** Unauthenticated Cognito users are explicitly denied all access.

---

## Troubleshooting

### Common Permission Errors

| Error | Likely Cause | Solution |
|-------|--------------|----------|
| `AccessDeniedException` on `InvokeAgentRuntime` | Missing `bedrock-agentcore:InvokeAgentRuntime` | Add permission to AuthenticatedRole |
| `AccessDeniedException` on `InvokeModel` | Missing Bedrock model access | Verify model access in Bedrock console |
| `AccessDeniedException` on memory operations | Missing AgentCore memory permissions | Check execution role has memory permissions |
| `AccessDeniedException` on `InvokeGateway` | Missing gateway invoke permission | Verify invoke role has correct gateway ARN |
| ECR pull failures | Missing ECR permissions | Verify execution role has ECR access |

### IAM Propagation Delays

IAM role and policy changes can take 10-30 seconds to propagate. The deployment scripts include appropriate delays, but if you encounter permission errors immediately after deployment, wait and retry.
