import { Injectable } from '@angular/core';
import { SignatureV4 } from '@smithy/signature-v4';
import { HttpRequest } from '@smithy/protocol-http';
import { Sha256 } from '@aws-crypto/sha256-js';
import { AwsConfigService } from './aws-config.service';
import { AgentDynamoDBService } from './agent-dynamodb.service';
import { EnrichedAgent } from '../models/application-models';

/**
 * The step-event envelope POSTed to a configured `notify_on_invocation`
 * endpoint. `stepType` is reserved for future work beyond `incoming_request`
 * — this service never constructs any other value. See spec:
 * a2a-invocation-notify-hook.
 */
export interface NotificationPayload {
  sessionId: string;
  stepIndex: number;
  stepType: 'thought' | 'tool_call' | 'tool_result' | 'response' | 'incoming_request';
  timestamp: string;
  content: Record<string, any>;
}

/**
 * NotifyDispatchService fires a fire-and-forget, non-blocking HTTP POST to an
 * agent's configured `notify_on_invocation.endpoint` every time that agent is
 * invoked with a real user prompt. It never awaits a response, never throws,
 * and never surfaces a delivery outcome to any caller — the receiving system
 * is assumed to be arbitrary and untrusted, and the agent's own behavior is
 * completely unaffected regardless of what happens to the notification.
 *
 * This is entirely independent of the existing A2A external-agent /
 * `invoke_<agent>` tool-calling feature. See spec: a2a-invocation-notify-hook.
 */
@Injectable({ providedIn: 'root' })
export class NotifyDispatchService {
  /** Next stepIndex to use per sessionId; increments across the whole conversation. */
  private stepCounters = new Map<string, number>();

  constructor(
    private awsConfig: AwsConfigService,
    private agentDynamoDBService: AgentDynamoDBService,
  ) {}

  /**
   * Fire-and-forget entry point. Returns immediately (synchronously, from the
   * caller's perspective) and never throws. If the agent has no
   * `notify_on_invocation` configured, this is a complete no-op — no network
   * call of any kind is made.
   */
  dispatchIfConfigured(
    agent: EnrichedAgent,
    invocation: { sessionId: string; invocationPayload: Record<string, any> },
  ): void {
    const notifyConfig = agent.notify_on_invocation;
    if (!notifyConfig?.endpoint) {
      return;
    }

    // Intentionally not awaited/returned — this runs independently of the
    // agent invocation flow. The .catch() guarantees no unhandled rejection
    // and no error ever propagates back to the caller.
    this.sendNotification(agent, notifyConfig, invocation).catch(err => {
      console.warn(`⚠️ NotifyDispatch: notification failed for ${agent.name}:`, err);
    });
  }

  /** Returns the next stepIndex for a session, then increments the counter. */
  private nextStepIndex(sessionId: string): number {
    const next = this.stepCounters.get(sessionId) ?? 0;
    this.stepCounters.set(sessionId, next + 1);
    return next;
  }

  private async sendNotification(
    agent: EnrichedAgent,
    notifyConfig: NonNullable<EnrichedAgent['notify_on_invocation']>,
    invocation: { sessionId: string; invocationPayload: Record<string, any> },
  ): Promise<void> {
    const payload: NotificationPayload = {
      sessionId: invocation.sessionId,
      stepIndex: this.nextStepIndex(invocation.sessionId),
      stepType: 'incoming_request',
      timestamp: new Date().toISOString(),
      content: invocation.invocationPayload,
    };

    const body = JSON.stringify(payload);
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    let requestInit: RequestInit = { method: 'POST', headers, body };

    if (notifyConfig.auth_type === 'bearer') {
      const token = await this.agentDynamoDBService.getNotifyBearerToken(agent.name);
      if (!token) {
        console.warn(`⚠️ NotifyDispatch: bearer auth configured but no token stored for ${agent.name} — skipping notification`);
        return;
      }
      headers['Authorization'] = `Bearer ${token}`;
      requestInit = { method: 'POST', headers, body };
    } else if (notifyConfig.auth_type === 'iam') {
      requestInit = await this.signRequestWithSigV4(notifyConfig.endpoint, body, headers);
    }

    // Not awaited by dispatchIfConfigured's caller — the promise chain runs
    // independently of the agent invocation flow.
    await fetch(notifyConfig.endpoint, requestInit);
  }

  /**
   * Signs the outbound notification POST with the current session's
   * temporary AWS credentials (the same cached Cognito Identity Pool session
   * already used elsewhere in this app). No new credential-acquisition path
   * or IAM role is introduced — this reuses the existing Authenticated User
   * Role's permissions as-is.
   */
  private async signRequestWithSigV4(
    endpoint: string,
    body: string,
    headers: Record<string, string>,
  ): Promise<RequestInit> {
    const session = await this.awsConfig.getCachedAuthSession();
    const url = new URL(endpoint);

    const signer = new SignatureV4({
      credentials: session.credentials,
      region: this.awsConfig.getRegion(),
      service: 'execute-api',
      sha256: Sha256,
    });

    const request = new HttpRequest({
      method: 'POST',
      protocol: url.protocol,
      hostname: url.hostname,
      path: url.pathname + url.search,
      headers: { ...headers, host: url.hostname },
      body,
    });

    const signed = await signer.sign(request);

    return {
      method: 'POST',
      headers: signed.headers as Record<string, string>,
      body,
    };
  }
}
