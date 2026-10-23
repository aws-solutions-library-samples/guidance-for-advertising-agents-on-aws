import {
  AgentCoreApplication,
  AgentCoreMcp,
  AgentCorePaymentManager,
  AgentCorePaymentConnector,
  type AgentCoreProjectSpec,
  type AgentCoreMcpSpec,
  type CustomJWTAuthorizerConfig,
  type HarnessDeploymentConfig,
} from '@aws/agentcore-cdk';
import { CfnOutput, Duration, Stack, type StackProps } from 'aws-cdk-lib';
import { Platform } from 'aws-cdk-lib/aws-ecr-assets';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as path from 'path';
import { Construct } from 'constructs';

/**
 * The governance agent's `.well-known/` origin bucket, `<prefix>-reference-governance`, which MUST
 * match `governance_origin.bucket_name()`. The instance prefix now provides S3 global uniqueness, so
 * account/region are no longer in the name. It is read from INSTANCE_PREFIX in the environment --
 * `agentcore deploy` inherits it from `deploy_all.py`; it defaults to 'adcp' for a standalone deploy,
 * matching the Python side.
 */
const INSTANCE_PREFIX = (process.env.INSTANCE_PREFIX || 'adcp').trim() || 'adcp';
const GOVERNANCE_BUCKET_NAME = `${INSTANCE_PREFIX}-reference-governance`;

/**
 * Harness deployment config: role-scoped fields (for IAM role + container build)
 * plus the full validated spec + its config directory so the L3 construct can
 * synthesize the AWS::BedrockAgentCore::Harness resource.
 */
export type HarnessConfig = HarnessDeploymentConfig;

export interface PaymentConnectorSpec {
  name: string;
  provider: 'CoinbaseCDP' | 'StripePrivy';
  credentialProviderArn: string;
}

export interface PaymentSpec {
  name: string;
  description?: string;
  authorizerType: 'AWS_IAM' | 'CUSTOM_JWT';
  authorizerConfiguration?: { customJWTAuthorizer: CustomJWTAuthorizerConfig };
  autoPayment?: boolean;
  paymentToolAllowlist?: string[];
  networkPreferences?: string[];
  connectors: PaymentConnectorSpec[];
}

export interface AgentCoreStackProps extends StackProps {
  /**
   * The AgentCore project specification containing agents, memories, and credentials.
   */
  spec: AgentCoreProjectSpec;
  /**
   * The MCP specification containing gateways and servers.
   */
  mcpSpec?: AgentCoreMcpSpec;
  /**
   * Credential provider ARNs from deployed state, keyed by credential name.
   */
  credentials?: Record<string, { credentialProviderArn: string; clientSecretArn?: string }>;
  /**
   * Harness role configurations.
   */
  harnesses?: HarnessConfig[];
  /**
   * Parsed connectorParameters for non-S3 KB data sources, keyed by
   * connectorConfigFile path. Forwarded to AgentCoreApplication.
   */
  connectorParametersByFile?: Record<string, Record<string, unknown>>;
  /**
   * Payment specifications with resolved credential provider ARNs.
   */
  paymentSpec?: PaymentSpec[];
}

function toCdkId(name: string): string {
  return name.replace(/_/g, '');
}

/**
 * Decide whether a deployed runtime should receive payment env vars + IAM grants.
 * Payments today only ships a runtime shim for Python HTTP runtimes; injecting
 * AGENTCORE_PAYMENT_* env vars into TypeScript / MCP / A2A / AGUI runtimes
 * would surface env vars they cannot consume and would dilute least-privilege
 * IAM grants for runtimes that never call ProcessPayment.
 */
function isPaymentEligibleAgent(agent: { entrypoint?: string; protocol?: string }): boolean {
  if (agent.protocol && agent.protocol !== 'HTTP') {
    return false;
  }
  const entrypoint = typeof agent.entrypoint === 'string' ? agent.entrypoint : '';
  const entrypointFile = entrypoint.split(':')[0] ?? '';
  return entrypointFile.endsWith('.py');
}

/**
 * CDK Stack that deploys AgentCore infrastructure.
 *
 * This is a thin wrapper that instantiates L3 constructs.
 * All resource logic and outputs are contained within the L3 constructs.
 */
export class AgentCoreStack extends Stack {
  /** The AgentCore application containing all agent environments */
  public readonly application: AgentCoreApplication;

  constructor(scope: Construct, id: string, props: AgentCoreStackProps) {
    super(scope, id, props);

    const { spec, mcpSpec, credentials, harnesses, connectorParametersByFile, paymentSpec } = props;

    // Create AgentCoreApplication with all agents and harness roles
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const appProps: Record<string, unknown> = { spec };
    if (harnesses?.length) {
      appProps.harnesses = harnesses;
    }
    if (connectorParametersByFile && Object.keys(connectorParametersByFile).length > 0) {
      appProps.connectorParametersByFile = connectorParametersByFile;
    }
    if (credentials) {
      appProps.credentials = credentials;
    }
    this.application = new AgentCoreApplication(this, 'Application', appProps as any);

    // Create AgentCoreMcp if there are gateways configured
    if (mcpSpec?.agentCoreGateways && mcpSpec.agentCoreGateways.length > 0) {
      new AgentCoreMcp(this, 'Mcp', {
        projectName: spec.name,
        mcpSpec,
        agentCoreApplication: this.application,
        credentials,
        projectTags: spec.tags,
      });
    }

    // Create payment infrastructure via CFN constructs
    if (paymentSpec && paymentSpec.length > 0) {
      for (const payment of paymentSpec) {
        const mgrId = toCdkId(payment.name);
        const manager = new AgentCorePaymentManager(this, `Payment${mgrId}`, {
          projectName: spec.name,
          name: payment.name,
          authorizerType: payment.authorizerType,
          description: payment.description,
          authorizerConfiguration: payment.authorizerConfiguration,
          tags: spec.tags,
        });

        const prefix = `AGENTCORE_PAYMENT_${payment.name.toUpperCase().replace(/-/g, '_')}`;

        // Wire env vars from construct output tokens into eligible agent environments only.
        // See isPaymentEligibleAgent — non-Python or non-HTTP runtimes have no shim that
        // can consume these env vars, and giving them sts:AssumeRole on the
        // ProcessPaymentRole would broaden the privilege surface unnecessarily.
        for (const env of this.application.environments.values()) {
          if (!isPaymentEligibleAgent(env.agent)) {
            continue;
          }
          env.runtime.addEnvironmentVariable(`${prefix}_MANAGER_ARN`, manager.paymentManagerArn);
          env.runtime.addEnvironmentVariable(`${prefix}_PROCESS_PAYMENT_ROLE_ARN`, manager.processPaymentRoleArn);

          // Grant runtime execution role permission to assume the ProcessPaymentRole.
          // The ProcessPaymentRole's trust policy allows AccountRootPrincipal, but the
          // caller still needs sts:AssumeRole on its own role to perform the assumption.
          env.runtime.role.addToPrincipalPolicy(
            new iam.PolicyStatement({
              actions: ['sts:AssumeRole'],
              resources: [manager.processPaymentRoleArn],
            })
          );

          // Grant payment data-plane actions directly to the runtime role.
          //
          // NOTE: This deviates from the canonical role model in the AgentCore Payments
          // beta guide, which assigns Get/List/Create instrument+session actions to a
          // separate ManagementRole and limits the agent's role to ProcessPayment only.
          // The current SDK plugin (AgentCorePaymentsPlugin.generate_payment_header)
          // calls GetPaymentInstrument internally during the 402 auto-pay path, so the
          // runtime role needs read access. CreatePaymentSession is included so
          // `agentcore invoke --auto-session` works without a separate ManagementRole
          // call. Tighten this if the SDK is updated to accept pre-fetched instrument
          // details and split create-session into a backend-only flow.
          env.runtime.role.addToPrincipalPolicy(
            new iam.PolicyStatement({
              actions: [
                'bedrock-agentcore:GetPaymentInstrument',
                'bedrock-agentcore:ListPaymentInstruments',
                'bedrock-agentcore:GetPaymentInstrumentBalance',
                'bedrock-agentcore:GetPaymentSession',
                'bedrock-agentcore:ListPaymentSessions',
                'bedrock-agentcore:CreatePaymentSession',
                'bedrock-agentcore:ProcessPayment',
              ],
              resources: [manager.paymentManagerArn, `${manager.paymentManagerArn}/*`],
            })
          );

          if (payment.autoPayment !== undefined) {
            env.runtime.addEnvironmentVariable(`${prefix}_AUTO_PAYMENT`, String(payment.autoPayment));
          }
          if (payment.paymentToolAllowlist) {
            env.runtime.addEnvironmentVariable(`${prefix}_TOOL_ALLOWLIST`, payment.paymentToolAllowlist.join(','));
          }
          if (payment.networkPreferences) {
            env.runtime.addEnvironmentVariable(`${prefix}_NETWORK_PREFERENCES`, payment.networkPreferences.join(','));
          }
          if (payment.authorizerType === 'CUSTOM_JWT') {
            env.runtime.addEnvironmentVariable(`${prefix}_AUTH_MODE`, 'bearer');
          }
        }

        // Create connectors for this manager
        for (const connector of payment.connectors) {
          const connId = toCdkId(connector.name);
          const conn = new AgentCorePaymentConnector(this, `Payment${mgrId}${connId}`, {
            projectName: spec.name,
            paymentManager: manager,
            connectorName: connector.name,
            connectorType: connector.provider,
            credentialProviderArn: connector.credentialProviderArn,
          });

          // Wire first connector's ID as env var (eligible agents only)
          if (connector === payment.connectors[0]) {
            for (const env of this.application.environments.values()) {
              if (!isPaymentEligibleAgent(env.agent)) continue;
              env.runtime.addEnvironmentVariable(`${prefix}_CONNECTOR_ID`, conn.paymentConnectorId);
            }
          }

          new CfnOutput(this, `Payment${mgrId}${connId}ConnectorId`, {
            value: conn.paymentConnectorId,
          });
        }

        // CFN Outputs for post-deploy state parsing
        new CfnOutput(this, `Payment${mgrId}ManagerArn`, {
          value: manager.paymentManagerArn,
        });
        new CfnOutput(this, `Payment${mgrId}ManagerId`, {
          value: manager.paymentManagerId,
        });
        new CfnOutput(this, `Payment${mgrId}ProcessPaymentRoleArn`, {
          value: manager.processPaymentRoleArn,
        });
        new CfnOutput(this, `Payment${mgrId}ResourceRetrievalRoleArn`, {
          value: manager.resourceRetrievalRoleArn,
        });
      }
    }

    // Scheduled revocation-list republish (U4/Part 1 fix). See
    // app/adcpRefGovernance/deploy_revocations.py's module docstring for the live outage this
    // exists to prevent: the published revocation list declares its own freshness window
    // (`next_update`), and the AdCP SDK's revocation checker fails every seller's
    // `create_media_buy` closed once that window lapses with nothing having republished it.
    //
    // Signing env vars are read from the main runtime's OWN agentcore.json entry rather than
    // duplicated as literals here, so the Lambda can never drift from whatever signing identity
    // the runtime is actually configured with.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    // The runtime name is the bare base `RefGovernance` -- render_agentcore_json.py prefixes only the
    // project/stack name, leaving runtime.name unprefixed, so this is the same across every instance.
    const governanceRuntimeSpec = (spec as any).runtimes?.find(
      (r: { name?: string }) => r.name === 'RefGovernance'
    );
    const governanceRuntimeEnv: Record<string, string> = Object.fromEntries(
      (governanceRuntimeSpec?.envVars ?? []).map((e: { name: string; value: string }) => [e.name, e.value])
    );
    // GOVERNANCE_ORIGIN_URL and GOVERNANCE_ORIGIN_DISTRIBUTION_ID are in this list because they are
    // interpolated into the Lambda's environment and its invalidation grant. Absent, they would
    // synthesise as the string "undefined" -- an ARN that is syntactically valid, deploys cleanly, and
    // matches nothing, so the republisher would fail only on its first scheduled run 12h later.
    const requiredSigningEnvVars = [
      'GOVERNANCE_SIGNING_KMS_KEY_ID',
      'GOVERNANCE_SIGNING_ALG',
      'GOVERNANCE_SIGNING_KID',
      'GOVERNANCE_AGENT_URL',
      'GOVERNANCE_ORIGIN_DISTRIBUTION_ID',
    ];
    for (const key of requiredSigningEnvVars) {
      if (!governanceRuntimeEnv[key]) {
        throw new Error(
          `RefGovernance runtime's agentcore.json envVars is missing ${key}. ` +
            'Run `python3 deploy_all.py --only governance-origin` first: it creates the origin and ' +
            'writes the values every later step reads.'
        );
      }
    }
    //
    // A separate DockerImageFunction, not a reuse of the main agent's image: that image runs an
    // MCP server on AgentCore Runtime's service contract and carries dependencies (Pillow,
    // pytesseract, mcp, adcp.server) this handler does not need. The build context is the
    // PARENT directory (app/adcpRefGovernance/) so the Dockerfile can COPY the sibling jws.py/
    // deploy_revocations.py plus the shared pyproject.toml/uv.lock -- see
    // scheduled_revocations/Dockerfile's own comment on why.
    // __dirname at runtime is dist/lib/ (compiled output), so reaching
    // reference-governance/app/adcpRefGovernance needs dist/lib -> dist -> cdk -> agentcore ->
    // reference-governance (4 levels up), then app/adcpRefGovernance.
    const revocationsCodeDir = path.resolve(
      __dirname,
      '..',
      '..',
      '..',
      '..',
      'app',
      'adcpRefGovernance'
    );
    const revocationsFn = new lambda.DockerImageFunction(this, 'RevocationsRepublisher', {
      code: lambda.DockerImageCode.fromImageAsset(revocationsCodeDir, {
        file: path.join('scheduled_revocations', 'Dockerfile'),
        // Real bug found live: DockerImageAssetOptions.platform defaults to "the current
        // machine architecture", which on an Apple Silicon dev machine is arm64. Left
        // unspecified, the built image silently mismatched the function's own default
        // architecture (X86_64) and every invocation failed with `Runtime.InvalidEntrypoint:
        // ProcessSpawnFailed` -- the runtime bootstrap could not exec, wrong CPU architecture,
        // not a missing-file or code problem. Pinning both this and `architecture` below to
        // ARM_64 keeps them in agreement regardless of which machine runs `cdk deploy`.
        platform: Platform.LINUX_ARM64,
      }),
      architecture: lambda.Architecture.ARM_64,
      timeout: Duration.seconds(60),
      memorySize: 256,
      description: 'Republishes the governance agent revocation list before it goes stale.',
      // The same signing identity the main runtime's governance_context tokens use --
      // jws.issue_revocation_list (deploy_revocations.py -> jws.py) needs exactly these three to
      // resolve a KMS signer. GOVERNANCE_AGENT_URL is read by deploy_revocations.py's own module
      // constant only indirectly (ISSUER is hardcoded there to match), so it is not required by
      // this Lambda -- kept out to avoid implying a dependency that does not exist.
      environment: {
        GOVERNANCE_SIGNING_KMS_KEY_ID: governanceRuntimeEnv.GOVERNANCE_SIGNING_KMS_KEY_ID,
        GOVERNANCE_SIGNING_ALG: governanceRuntimeEnv.GOVERNANCE_SIGNING_ALG,
        GOVERNANCE_SIGNING_KID: governanceRuntimeEnv.GOVERNANCE_SIGNING_KID,
        // Handed over rather than discovered: governance_origin.origin_url() otherwise finds the
        // distribution by listing CloudFront distributions, which is a needless API call on every
        // cold start when the value is already known at synth.
        GOVERNANCE_ORIGIN_URL: governanceRuntimeEnv.GOVERNANCE_AGENT_URL,
      },
    });

    // Same three permissions the manual CLI's own execution context needs -- KMS sign/get-public-
    // key (reuses the governance_context signer, no new key), S3 put on the well-known prefix,
    // and CloudFront invalidate. Authored directly in CDK rather than via `additionalPolicies` in
    // agentcore.json: that mechanism attaches to an AgentCore RUNTIME's execution role, and this
    // is a plain Lambda with its own role, so it is not applicable here.
    // Scoped by the key's ALIAS rather than its id. IAM does not accept an alias ARN as a Resource
    // for key operations, so the grant is `key/*` narrowed by `kms:RequestAlias` -- which is the one
    // key carrying that alias, without this file having to know the key's UUID (and therefore without
    // pinning it to the account it was created in).
    revocationsFn.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'GovernanceSigningKey',
        actions: ['kms:Sign', 'kms:GetPublicKey'],
        resources: [`arn:aws:kms:${this.region}:${this.account}:key/*`],
        // Scoped by the signing key's alias, read from the runtime's OWN env (GOVERNANCE_SIGNING_
        // KMS_KEY_ID = `alias/<prefix>-governance-signing`, validated present above) rather than a
        // hardcoded literal, so the grant follows the instance's actual per-prefix alias.
        conditions: {
          StringEquals: { 'kms:RequestAlias': governanceRuntimeEnv.GOVERNANCE_SIGNING_KMS_KEY_ID },
        },
      })
    );
    revocationsFn.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'GovernanceWellKnownBucket',
        actions: ['s3:PutObject'],
        // Must match governance_origin.bucket_name(): `<prefix>-reference-governance`.
        resources: [
          `arn:aws:s3:::${GOVERNANCE_BUCKET_NAME}/.well-known/*`,
        ],
      })
    );
    revocationsFn.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'GovernanceOriginDistributionLookup',
        actions: ['cloudfront:ListDistributions'],
        resources: ['*'],
      })
    );
    // The distribution id comes from the runtime's own envVars, which deploy_governance_origin.py
    // writes after creating the distribution in phase 1 -- the same mechanism deploy_all.py already
    // uses to hand the Gotham seller its reach-service URL. Kept scoped to the one distribution
    // rather than widened to a wildcard: this role can invalidate the governance origin and nothing
    // else in the account.
    const governanceDistributionId = governanceRuntimeEnv.GOVERNANCE_ORIGIN_DISTRIBUTION_ID;
    revocationsFn.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'GovernanceOriginDistributionInvalidate',
        actions: ['cloudfront:CreateInvalidation'],
        resources: [
          `arn:aws:cloudfront::${this.account}:distribution/${governanceDistributionId}`,
        ],
      })
    );

    // 12h -- half of DEFAULT_NEXT_UPDATE_SECONDS (24h, deploy_revocations.py) -- so one missed
    // invocation still leaves the previous document's declared freshness window intact.
    new events.Rule(this, 'RevocationsRepublishSchedule', {
      schedule: events.Schedule.rate(Duration.hours(12)),
      targets: [new targets.LambdaFunction(revocationsFn)],
      description: 'Republishes the governance revocation list every 12h so it never goes stale.',
    });

    new CfnOutput(this, 'RevocationsRepublisherFunctionName', {
      value: revocationsFn.functionName,
    });

    // Stack-level output
    new CfnOutput(this, 'StackNameOutput', {
      description: 'Name of the CloudFormation Stack',
      value: this.stackName,
    });
  }
}
