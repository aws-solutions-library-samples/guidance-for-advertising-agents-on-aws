import { Injectable } from '@angular/core';
import {
  BedrockAgentCoreClient,
  BedrockAgentCoreClientConfig,
  ListEventsCommand,
  ListEventsCommandOutput,
} from '@aws-sdk/client-bedrock-agentcore';
import { AwsConfigService } from './aws-config.service';
import { BedrockService } from './bedrock.service';
import { SessionManagerService } from './session-manager.service';
import { AgentEvents, HistoryEntry, MemoryEvent, eventsToHistory, toActorId } from '../utils/session-history';

export interface SessionHistoryResult {
  entries: HistoryEntry[];
  /** Set when some or all of the history could not be read. */
  error?: string;
}

/** The part of the AgentCore client this service uses. */
export interface ListEventsClient {
  send(command: ListEventsCommand): Promise<Pick<ListEventsCommandOutput, 'events' | 'nextToken'>>;
}

/** Builds the AgentCore client. A separate service so tests can supply a fake. */
@Injectable({
  providedIn: 'root'
})
export class AgentCoreClientFactory {
  create(config: BedrockAgentCoreClientConfig): ListEventsClient {
    return new BedrockAgentCoreClient(config);
  }
}

/** ListEvents returns at most 100 events per page. */
const PAGE_SIZE = 100;
/** 50 pages is 5,000 events for one agent; more means the token is not advancing. */
const MAX_PAGES_PER_AGENT = 50;

/**
 * Reads a session's conversation back from AgentCore Memory with ListEvents.
 * It reads only; it never calls a model.
 */
@Injectable({
  providedIn: 'root'
})
export class SessionHistoryService {
  constructor(
    private sessionManager: SessionManagerService,
    private awsConfig: AwsConfigService,
    private bedrockService: BedrockService,
    private clientFactory: AgentCoreClientFactory
  ) {}

  /** Never throws: failures are returned in `error`. */
  async loadHistory(sessionId: string): Promise<SessionHistoryResult> {
    if (!sessionId || !this.sessionManager.hasSession(sessionId)) {
      console.error('Session history: refused a session id this page did not create');
      return { entries: [], error: 'Unknown session id' };
    }

    const agentNames = this.sessionManager.getSession(sessionId)?.agentNames || [];
    if (agentNames.length === 0) {
      return { entries: [] };
    }

    try {
      const client = await this.createClient();
      const memoryId = this.requireMemoryId();
      return await this.readAgents(client, memoryId, sessionId, agentNames);
    } catch (error) {
      console.error('Session history: could not read AgentCore Memory', error);
      return { entries: [], error: describeError(error) };
    }
  }

  private async readAgents(
    client: ListEventsClient,
    memoryId: string,
    sessionId: string,
    agentNames: string[]
  ): Promise<SessionHistoryResult> {
    const results = await Promise.allSettled(
      agentNames.map(agentName => this.readAgentEvents(client, memoryId, sessionId, agentName))
    );

    const eventsByAgent: AgentEvents[] = [];
    const failures: string[] = [];
    results.forEach((result, index) => {
      if (result.status === 'fulfilled') {
        eventsByAgent.push(result.value);
      } else {
        console.error(`Session history: ListEvents failed for ${agentNames[index]}`, result.reason);
        failures.push(`${agentNames[index]}: ${describeError(result.reason)}`);
      }
    });

    const entries = eventsToHistory(eventsByAgent);
    return failures.length > 0 ? { entries, error: failures.join('; ') } : { entries };
  }

  /** Every page of one agent's events for the session. */
  private async readAgentEvents(
    client: ListEventsClient,
    memoryId: string,
    sessionId: string,
    agentName: string
  ): Promise<AgentEvents> {
    const events: MemoryEvent[] = [];
    let nextToken: string | undefined;
    for (let page = 0; page < MAX_PAGES_PER_AGENT; page++) {
      const response = await client.send(
        new ListEventsCommand({
          memoryId,
          actorId: toActorId(agentName),
          sessionId,
          includePayloads: true,
          maxResults: PAGE_SIZE,
          nextToken,
        })
      );
      events.push(...((response.events || []) as MemoryEvent[]));
      nextToken = response.nextToken || undefined;
      if (!nextToken) {
        return { agentName, events };
      }
    }
    throw new Error(`more than ${MAX_PAGES_PER_AGENT} pages of events`);
  }

  private async createClient(): Promise<ListEventsClient> {
    const config = await this.awsConfig.getAwsConfig();
    if (!config?.credentials) {
      throw new Error('AWS credentials are not available');
    }
    return this.clientFactory.create({ region: config.region, credentials: config.credentials });
  }

  private requireMemoryId(): string {
    const memoryId = this.bedrockService.getMemoryId();
    if (!memoryId) {
      throw new Error('AgentCore Memory id is not configured');
    }
    return memoryId;
  }
}

function describeError(error: unknown): string {
  if (error instanceof Error) {
    return error.name && error.name !== 'Error' ? `${error.name}: ${error.message}` : error.message;
  }
  return String(error);
}
