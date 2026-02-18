import { Injectable } from '@angular/core';
import { Observable, from, BehaviorSubject, Subject } from 'rxjs';
import { AwsConfigService } from './aws-config.service';
import { AgentConfigService } from './agent-config.service';
import { SessionManagerService } from './session-manager.service';
import { TextUtils } from '../utils/text-utils';
import type { NodeJsClient, SdkStream, StreamingBlobPayloadOutputTypes } from "@smithy/types";
import { BedrockAgentCoreControlClient, ListMemoriesCommand } from '@aws-sdk/client-bedrock-agentcore-control';
// AWS SDK v3 imports
import { BedrockAgentRuntimeClient, FailureTrace, InvokeAgentCommand, ResponseStream, Trace } from '@aws-sdk/client-bedrock-agent-runtime';
import { InvokeAgentRuntimeCommand, BedrockAgentCoreClient, InvokeAgentRuntimeCommandInput, InvokeAgentRuntimeRequest, InvokeAgentRuntimeResponse, InvokeAgentRuntimeCommandOutput, CreateEventCommand, ListMemoryRecordsCommand } from '@aws-sdk/client-bedrock-agentcore';
import { BedrockRuntimeClient, InvokeModelCommand, ConverseCommand } from '@aws-sdk/client-bedrock-runtime';
import { S3Client, PutObjectCommand, GetObjectCommand } from '@aws-sdk/client-s3';
import { getSignedUrl } from '@aws-sdk/s3-request-presigner';
import { getCurrentUser } from 'aws-amplify/auth';
import { v4 } from 'uuid';
// Note: fetchAuthSession is accessed via awsConfig.getCachedAuthSession() to prevent rate limiting
import { ScenarioExample, AgentParticipant, AttachedFile, KnowledgeBaseSource, Message, EnrichedAgent, StreamEvent } from 'src/app/models/application-models';


// Types for the trace events based on the actual format
/*interface TraceEvent {
  modelInvocationInput?: ModelInvocationInput;
  modelInvocationOutput?: ModelInvocationOutput;
  invocationInput?: InvocationInput;
  observation?: Observation;
  rationale?: Rationale;
  orchestrationTrace?: any;
  failureTrace: FailureTrace
}*/

interface ModelInvocationInput {
  foundationModel: string;
  inferenceConfiguration: any;
  text: string;
  traceId?: string;
  type?: string;
}

interface ModelInvocationOutput {
  metadata: {
    clientRequestId: string;
    endTime: string;
    startTime: string;
    totalTimeMs: number;
    usage: {
      inputTokens: number;
      outputTokens: number;
    };
  };
  rawResponse: {
    content: string;
  };
  traceId?: string;
}

interface InvocationInput {
  agentCollaboratorInvocationInput?: {
    agentCollaboratorAliasArn: string;
    agentCollaboratorName: string;
    input: {
      text: string;
      type: string;
    };
  };
  knowledgeBaseLookupInput?: {
    text: string;
    knowledgeBaseId: string;
  };
  invocationType: string;
  traceId: string;
}

interface Observation {
  finalResponse?: {
    metadata: {
      endTime: string;
      operationTotalTimeMs: number;
      startTime: string;
    };
    text: string;
  };
  agentCollaboratorInvocationOutput?: {
    agentCollaboratorAliasArn: string;
    agentCollaboratorName: string;
    metadata: {
      clientRequestId: string;
      endTime: string;
      startTime: string;
      totalTimeMs: number;
    };
    output: {
      text: string;
      type: string;
    };
  };
  actionGroupInvocationOutput?: {
    metadata: {
      clientRequestId: string;
      endTime: string;
      startTime: string;
      totalTimeMs: number;
    };
    text: string;
  };
  traceId: string;
  type: string;
  knowledgeBaseLookupOutput?: {
    retrievedReferences: any[];
  };
  knowledgeBaseLookupInput?: {
    query: string;
  };
}

interface Rationale {
  text: string;
  traceId: string;
}

interface RecentEvent {
  type: string;
  content: string;
  timestamp: Date;
  sessionId: string;
}

@Injectable({
  providedIn: 'root'
})
export class BedrockService {
  private bedrockClient: BedrockAgentRuntimeClient | null = null;
  private agentCoreClient: BedrockAgentCoreClient | null = null;
  private bedrockRuntimeClient: BedrockRuntimeClient | null = null;
  // AgentCore agents use HTTP APIs, no AWS SDK client needed
  // private bedrockAgentCoreClient: BedrockAgentCoreClient | null = null;
  private currentSessionId: string | null = null;
  private sessionSources: Map<string, any[]> = new Map<string, any[]>() //map<sessionid, list of sources>
  private clientInitialized = false;
  // Add deduplication tracking
  private recentEvents = new Map<string, RecentEvent[]>(); // Track events per session
  private directMentionMode = new Map<string, boolean>(); // Track direct mention mode per session
  private activeAgentContext = new Map<string, string>(); // Track active agent per session for context-aware routing
  private readonly MAX_RECENT_EVENTS = 20; // Keep last 20 events for comparison
  private readonly DUPLICATE_TIME_WINDOW_MS = 5000; // 5 seconds window for duplicate detection
  private readonly SIMILARITY_THRESHOLD = 0.85; // 85% similarity threshold
  // Accumulate complete responses before emitting
  private responseAccumulators = new Map<string, string>(); // Track accumulated responses per session
  private agentCoreAccumulators = new Map<string, string>(); // Track AgentCore accumulated text per session/agent
  private chatMessages: any[] = []; // Track messages for AgentCore sources
  private cleanupInterval: any = null; // Track the cleanup interval
  memoryRecordId: any;

  // Removed visualization detection during streaming to avoid conflicts

  constructor(
    private awsConfig: AwsConfigService,
    private agentConfig: AgentConfigService,
    private sessionManager: SessionManagerService
  ) {
    this.initializeClient();
    this.initializeMemoryRecordId();
    // Set up periodic cleanup of old session data (every 10 minutes)
    this.cleanupInterval = setInterval(() => {
      this.cleanupOldSessions();
    }, 10 * 60 * 1000);

  }

  /**
   * Initialize memory record ID from AgentCore ListMemoriesCommand.
   * This dynamically discovers the correct memory ID based on stack prefix/suffix.
   */
  private async initializeMemoryRecordId(): Promise<void> {
    if (this.memoryRecordId) {
      console.log(`📝 Memory record ID already set: ${this.memoryRecordId}`);
      return;
    }

    try {
      const controlClient = new BedrockAgentCoreControlClient({ region: this.awsConfig.getRegion() });
      const memoryIdPart = this.awsConfig.getStackPrefix() + 'memory' + this.awsConfig.getStackSuffix();
      
      console.log(`🔍 Searching for memory with pattern: ${memoryIdPart}`);
      
      const memoryRecords = await controlClient.send(new ListMemoriesCommand({ maxResults: 100 }));
      
      memoryRecords.memories?.forEach(memory => {
        if (memory.id && memory.id.indexOf(memoryIdPart) > -1) {
          this.memoryRecordId = memory.id;
          console.log(`✅ Found memory record ID: ${this.memoryRecordId}`);
        }
      });

      if (!this.memoryRecordId) {
        console.warn(`⚠️ No memory found matching pattern: ${memoryIdPart}. Available memories:`, 
          memoryRecords.memories?.map(m => m.id));
        // Fall back to config value if available
        const configMemoryId = this.awsConfig.getMemoryRecordId();
        if (configMemoryId) {
          this.memoryRecordId = configMemoryId;
          console.log(`📝 Using config memory record ID: ${this.memoryRecordId}`);
        }
      }
    } catch (error) {
      console.error('❌ Failed to load memory record ID from ListMemoriesCommand:', error);
      // Fall back to config value
      const configMemoryId = this.awsConfig.getMemoryRecordId();
      if (configMemoryId) {
        this.memoryRecordId = configMemoryId;
        console.log(`📝 Fallback to config memory record ID: ${this.memoryRecordId}`);
      }
    }
  }

  traceEvents: Array<any> = [];
  lastSessionId: string | undefined = undefined;

  // Helper method to convert base64 to Uint8Array for browser compatibility
  private base64ToUint8Array(base64: string): Uint8Array {
    const binaryString = atob(base64);
    const bytes = new Uint8Array(binaryString.length);
    for (let i = 0; i < binaryString.length; i++) {
      bytes[i] = binaryString.charCodeAt(i);
    }
    return bytes;
  }

  // Helper method to unescape JSON content with escaped newlines and quotes
  private unescapeJsonContent(content: string): string {
    return content
      // Unescape newlines
      .replace(/\\n/g, '\n')
      // Unescape quotes
      .replace(/\\"/g, '"')
      // Unescape backslashes (but do this last to avoid double unescaping)
      .replace(/\\\\/g, '\\')
      // Clean up any extra whitespace
      .trim();
  }

  // Helper method to determine content type from source URI
  private getContentTypeFromSource(sourceUri: string): string {
    if (!sourceUri) return 'text/plain';

    const uri = sourceUri.toLowerCase();

    if (uri.endsWith('.json')) {
      return 'application/json';
    } else if (uri.endsWith('.csv')) {
      return 'text/csv';
    } else if (uri.endsWith('.pdf')) {
      return 'application/pdf';
    } else if (uri.endsWith('.txt')) {
      return 'text/plain';
    } else if (uri.endsWith('.xml')) {
      return 'application/xml';
    } else if (uri.endsWith('.html') || uri.endsWith('.htm')) {
      return 'text/html';
    } else {
      return 'text/plain';
    }
  }

  // Removed visualization detection methods to avoid conflicts with existing logic

  // Deduplication helper methods
  private calculateTextSimilarity(text1: string, text2: string): number {
    if (text1 === text2) return 1.0;
    if (!text1 || !text2) return 0.0;

    // Simple word-based similarity calculation
    const words1 = text1.toLowerCase().split(/\s+/).filter(w => w.length > 2);
    const words2 = text2.toLowerCase().split(/\s+/).filter(w => w.length > 2);

    if (words1.length === 0 && words2.length === 0) return 1.0;
    if (words1.length === 0 || words2.length === 0) return 0.0;

    const set1 = new Set(words1);
    const set2 = new Set(words2);
    const intersection = new Set([...set1].filter(x => set2.has(x)));
    const union = new Set([...set1, ...set2]);

    return intersection.size / union.size;
  }

  private isDuplicateEvent(sessionId: string, eventType: string, content: string): boolean {
    const sessionEvents = this.recentEvents.get(sessionId) || [];
    const now = new Date();

    // Check for duplicates within the time window
    for (const recentEvent of sessionEvents) {
      const timeDiff = now.getTime() - recentEvent.timestamp.getTime();

      // Skip events outside time window
      if (timeDiff > this.DUPLICATE_TIME_WINDOW_MS) {
        continue;
      }

      // Check for exact matches first
      if (recentEvent.content === content) {
        return true;
      }

      // Check for similar content for the same event type
      const similarity = this.calculateTextSimilarity(recentEvent.content, content);
      if (similarity >= this.SIMILARITY_THRESHOLD) {
        return true;
      }

    }

    return false;
  }

  private addToRecentEvents(sessionId: string, eventType: string, content: string): void {
    if (!this.recentEvents.has(sessionId)) {
      this.recentEvents.set(sessionId, []);
    }

    const sessionEvents = this.recentEvents.get(sessionId)!;
    const now = new Date();

    // Add new event
    sessionEvents.push({
      type: eventType,
      content,
      timestamp: now,
      sessionId
    });

    // Clean up old events beyond the max limit
    if (sessionEvents.length > this.MAX_RECENT_EVENTS) {
      sessionEvents.splice(0, sessionEvents.length - this.MAX_RECENT_EVENTS);
    }

    // Clean up events outside the time window
    const cutoffTime = now.getTime() - this.DUPLICATE_TIME_WINDOW_MS;
    const validEvents = sessionEvents.filter(event => event.timestamp.getTime() > cutoffTime);
    this.recentEvents.set(sessionId, validEvents);
  }

  private extractEventContent(eventType: string, data: any): string {
    if (eventType === 'chunk') {
      return typeof data === 'string' ? data : JSON.stringify(data);
    } else if (eventType === 'trace') {
      // Extract meaningful content from trace events
      if (typeof data === 'string') {
        return data;
      } else if (data && typeof data === 'object') {
        // Extract rationale text or other meaningful content
        if (data.orchestrationTrace?.rationale?.text) {
          return data.orchestrationTrace.rationale.text;
        } else if (data.rationale?.text) {
          return data.rationale.text;
        } else if (data.rationale) {
          return typeof data.rationale === 'string' ? data.rationale : JSON.stringify(data.rationale);
        }
        // For other trace types, create a normalized string representation
        return JSON.stringify(data).substring(0, 200);
      }
      return String(data);
    } else if (eventType === 'error') {
      return String(data);
    } else if (eventType === 'complete') {
      return String(data);
    }

    return String(data);
  }

  private async initializeClient(): Promise<void> {
    try {
      // Wait for AWS config to be loaded
      this.awsConfig.config$.subscribe(config => {
        if (config && this.awsConfig.isAuthenticated()) {
          this.setupBedrockClient();
        }
      });

      
    } catch (error) {
      //console.error('Error initializing Bedrock client:', error);
    }
  }

  private async setupBedrockClient(): Promise<void> {
    try {
      if (this.clientInitialized && this.bedrockClient && this.agentCoreClient) {
        return;
      }

      // Add retry logic for credentials initialization
      let retryCount = 0;
      const maxRetries = 5;
      const retryDelay = 1000; // 1 second

      while (retryCount < maxRetries) {
        try {
          const awsConfig = await this.awsConfig.getAwsConfig();
          if (!awsConfig || !awsConfig.credentials) {
            if (retryCount < maxRetries - 1) {
              await new Promise(resolve => setTimeout(resolve, retryDelay));
              retryCount++;
              continue;
            } else {
              console.warn('AWS credentials not available after retries, will use simulation mode');
              return;
            }
          }

          // Validate that credentials have required properties
          if (!awsConfig.credentials.accessKeyId && !awsConfig.credentials.sessionToken) {
            if (retryCount < maxRetries - 1) {
              await new Promise(resolve => setTimeout(resolve, retryDelay));
              retryCount++;
              continue;
            } else {
              console.warn('AWS credentials incomplete after retries, will use simulation mode');
              return;
            }
          }

          this.bedrockClient = new BedrockAgentRuntimeClient({
            region: awsConfig.region,
            credentials: awsConfig.credentials
          });

          this.bedrockRuntimeClient = new BedrockRuntimeClient({
            region: awsConfig.region,
            credentials: awsConfig.credentials
          });

          this.agentCoreClient = new BedrockAgentCoreClient({
            region: awsConfig.region,
            credentials: awsConfig.credentials,
            requestHandler: { connectionTimeout: 10000 }
          })

          this.clientInitialized = true;
          return;

        } catch (credentialsError) {
          if (retryCount < maxRetries - 1) {
            await new Promise(resolve => setTimeout(resolve, retryDelay));
            retryCount++;
            continue;
          } else {
            console.warn('Failed to get valid credentials after retries:', credentialsError);
            throw credentialsError;
          }
        }
      }
    } catch (error) {
      console.error('Error setting up Bedrock client:', error);
      this.clientInitialized = false;
    }
  }

  // New streaming method for real-time feedback
  invokeAgentWithStreaming(resolvedAgent: EnrichedAgent, query: string, sessionId?: string, attachedFiles?: AttachedFile[]): Observable<StreamEvent> {

    let agentType = resolvedAgent.name;

    // Store active agent context for this session
    if (sessionId) {
      this.activeAgentContext.set(sessionId, agentType);
      console.log(`📍 Set active agent context for session ${sessionId}: ${agentType}`);
    }

    // Detect direct agent mentions and extract target agent name
    const directMentionPattern = /@(\w+Agent)/g;
    let matches = query.match(directMentionPattern);
    let hasDirectMention = matches && matches.length > 0;
    let directMentionTarget = hasDirectMention && matches ? matches[0].substring(1) : agentType; // Remove @ symbol
    if (!directMentionTarget) {
      hasDirectMention = true;
      directMentionTarget = resolvedAgent.agentType;
    }

    if (hasDirectMention) {
      console.log(`🎯 Direct mention detected - target: ${directMentionTarget}`);
      if (sessionId) {
        this.directMentionMode.set(sessionId, true);
      }
    }

    return new Observable<StreamEvent>(observer => {
      this.invokeAgentStreamInternal(agentType, query, observer, sessionId, attachedFiles, directMentionTarget || agentType).catch(error => {
        //observer.error(error);
        console.error('Error invoking agent with streaming:', error);
      });
    });
  }

  invokeModel(prompt: string, modelId: string = 'us.amazon.nova-pro-v1:0'): Observable<any> {
    return new Observable(observer => {
      this.invokeModelInternal(prompt, modelId, observer).catch(error => {
        observer.error(error);
      });
    });
  }

  // Specific method for scenario generation
  generateScenarios(contextData: any, requirements?: string): Observable<any> {
    const scenarioPrompt = this.buildScenarioPrompt(contextData, requirements);
    return this.invokeModel(scenarioPrompt);
  }

  private buildScenarioPrompt(contextData: any, requirements?: string): string {
    const basePrompt = `You are an expert advertising technology strategist. Generate realistic, actionable scenarios for advertising campaign optimization.

Context Data:
${JSON.stringify(contextData, null, 2)}

${requirements ? `Additional Requirements: ${requirements}` : ''}

Please generate 3-5 diverse scenarios that cover different aspects of advertising optimization such as:
- Budget allocation strategies
- Audience targeting approaches  
- Creative optimization tactics
- Channel mix optimization
- Performance improvement strategies

For each scenario, provide:
1. A clear title
2. A detailed description of the scenario
3. Expected outcomes and metrics
4. Implementation steps
5. Risk factors and mitigation strategies

Format your response as a JSON object with a "scenarios" array containing the scenario objects.

Example format:
{
  "scenarios": [
    {
      "title": "Premium Inventory Focus Strategy",
      "description": "Concentrate budget on high-performing premium publishers...",
      "expectedOutcomes": {
        "cpmIncrease": "15-20%",
        "qualityImprovement": "25%",
        "brandSafetyScore": "95%+"
      },
      "implementationSteps": [
        "Analyze current publisher performance",
        "Identify top-tier publishers",
        "Reallocate budget distribution"
      ],
      "riskFactors": [
        "Higher costs may reduce overall reach",
        "Limited inventory availability"
      ],
      "mitigationStrategies": [
        "Gradual budget shift to test impact",
        "Maintain backup publisher relationships"
      ]
    }
  ]
}`;

    return basePrompt;
  }

  private async invokeModelInternal(prompt: string, modelId: string, observer: any): Promise<void> {
    try {
      // Ensure clients are set up
      if (!this.clientInitialized || !this.bedrockRuntimeClient) {
        await this.setupBedrockClient();
      }

      if (!this.bedrockRuntimeClient) {
        throw new Error('Bedrock Runtime client not initialized');
      }

      // Prepare the request body for LLM
      const requestBody = {
        messages: [
          {
            role: 'user',
            content: [
              {
                type: 'text',
                text: prompt
              }
            ]
          }
        ],
        inferenceConfig: {
          max_new_tokens: 4000,
          max_tokens_to_sample: 4000,
          max_tokens: 4000,
          temperature: 0.7,
          top_p: 0.9
        }
      };

      const command = new InvokeModelCommand({
        modelId: modelId,
        contentType: 'application/json',
        accept: 'application/json',
        body: JSON.stringify(requestBody)
      });

      const response = await this.bedrockRuntimeClient.send(command);

      if (!response.body) {
        throw new Error('No response body received from LLM');
      }

      // Parse the response
      const responseBody = JSON.parse(new TextDecoder().decode(response.body));

      // Extract the generated content
      const generatedContent = responseBody.output?.message?.content?.[0]?.text ||
        responseBody.content?.[0]?.text ||
        'No content generated';

      // Try to parse as JSON if it looks like structured data
      let parsedContent;
      try {
        // Check if the content contains JSON-like structure
        if (generatedContent.includes('{') && generatedContent.includes('}')) {
          // Extract JSON from the response (handle cases where there's text before/after JSON)
          const jsonMatch = generatedContent.match(/\{[\s\S]*\}/);
          if (jsonMatch) {
            parsedContent = JSON.parse(jsonMatch[0]);
          } else {
            parsedContent = { text: generatedContent };
          }
        } else {
          parsedContent = { text: generatedContent };
        }
      } catch (parseError) {
        console.warn('Could not parse LLM response as JSON, returning as text');
        parsedContent = { text: generatedContent };
      }

      observer.next({
        success: true,
        content: parsedContent,
        rawResponse: generatedContent,
        modelId: modelId
      });
      observer.complete();

    } catch (error: any) {
      console.error('❌ LLM invocation error:', error);

      // Provide helpful error messages
      let errorMessage = 'Failed to generate scenarios with LLM';
      if (error.message?.includes('credentials')) {
        errorMessage = 'Authentication issue. Please sign out and sign in again.';
      } else if (error.message?.includes('AccessDenied')) {
        errorMessage = 'Access denied to LLM model. Please check permissions.';
      } else if (error.message?.includes('ValidationException')) {
        errorMessage = 'Invalid request format for LLM.';
      } else if (error.message) {
        errorMessage = error.message;
      }

      observer.error({
        success: false,
        error: errorMessage,
        originalError: error
      });
    }
  }

  private async invokeAgentStreamInternal(agentType: string, query: string, observer: any, providedSessionId?: string, attachedFiles?: AttachedFile[], directMentionTarget?: string | null): Promise<void> {
    try {
      // Ensure client is set up
      if (!this.clientInitialized || !this.agentCoreClient) {
        await this.setupBedrockClient();
      }

      const config = this.awsConfig.getConfig();
      if (!config) {
        throw new Error('AWS configuration not available');
      }

      // Route to the specified agent type using centralized agent config
      const resolvedAgent = this.agentConfig.getAgent(directMentionTarget || agentType);
      if (!resolvedAgent) {

        return;
      }
      // Use provided session ID or get existing/new session
      let sessionId: string;
      if (providedSessionId) {
        sessionId = providedSessionId;
      } else {
        // Use current session ID or get from session manager
        sessionId = this.currentSessionId || this.sessionManager.getCurrentSessionId();
      }

      // Store the current session ID (shared across all agents)
      this.currentSessionId = sessionId;
      console.log(`📝 Using session ID: ${sessionId}`);

      if (this.lastSessionId !== sessionId) {
        console.log('📡 Subscribing to new session:', sessionId);
        this.lastSessionId = sessionId;
      }
      return this.invokeAgentCoreStreamInternal(resolvedAgent, query, observer, sessionId, attachedFiles, resolvedAgent.agentType);
    }

    catch (error) {
      console.error('Error in streaming agent invocation:', error);
      //observer.error(error);
    }
  }

  // Generate session ID based on user and customer information (delegates to session manager)
  generateCustomSessionId(loginId: string, customerName?: string): string {
    const sessionInfo = this.sessionManager.initializeSession(loginId, customerName);
    console.log(`🔑 Generated custom session ID: ${sessionInfo.sessionId} (user: ${loginId}, customer: ${customerName || 'none'})`);
    return sessionInfo.sessionId;
  }

  // Set custom session ID (shared across all agents)
  setCustomSessionId(agentType: string, sessionId: string): void {
    this.currentSessionId = sessionId;
  }

  async invokeAgent(resolvedAgent: EnrichedAgent, query: string, attachedFiles?: AttachedFile[], customSessionId?: string): Promise<any> {
    try {
      // Ensure client is set up
      if (!this.clientInitialized || !this.bedrockClient || !this.agentCoreClient) {
        await this.setupBedrockClient();

      }

      const config = this.awsConfig.getConfig();
      if (!config) {
        throw new Error('AWS configuration not available');
      }

      // Use custom session ID if provided, otherwise get from session manager
      let sessionId: string;
      if (customSessionId) {
        sessionId = customSessionId;
      } else {
        // Get session ID from centralized session manager
        sessionId = this.sessionManager.getCurrentSessionId();
      }
      this.currentSessionId = sessionId;

      // Check if this is an AgentCore agent
      this.invokeAgentWithStreaming(resolvedAgent, query, sessionId, attachedFiles);
    } catch (error) {

      // Only fall back to simulation for specific cases where we want graceful degradation
      const errorMessage = error instanceof Error ? error.message : String(error);
      console.error('Error invoking agent:', errorMessage);

      // For all other errors, throw them so components can handle appropriately
      //throw error;
    }
    return {
      response: `Error invoking agent:`,
      sessionId: this.sessionManager.getCurrentSessionId(),
      citations: [],
      traceEvents: []
    }
  }
  // Session ID generation is now handled by SessionManagerService



  // Clear all sessions when customer changes to force new session IDs
  clearAllSessionsForNewCustomer(): void {
    this.sessionManager.clearSession();
    this.currentSessionId = null;
    this.recentEvents.clear();
    this.responseAccumulators.clear();
    this.agentCoreAccumulators.clear();
  }

  // Get current session ID (for debugging)
  getCurrentSessionId(agentType: string): string | undefined {
    return this.currentSessionId || undefined;
  }

  clearSession(agentType: string): void {
    if (this.currentSessionId) {
      // Clear recent events for this session
      this.recentEvents.delete(this.currentSessionId);
      // Clear response accumulator for this session
      this.responseAccumulators.delete(this.currentSessionId);
      // Clear AgentCore accumulators for this session
      for (const [key] of this.agentCoreAccumulators.entries()) {
        if (key.startsWith(this.currentSessionId + '-')) {
          this.agentCoreAccumulators.delete(key);
        }
      }
      // Clear direct mention mode and active context
      this.directMentionMode.delete(this.currentSessionId);
      this.activeAgentContext.delete(this.currentSessionId);
    }
    this.currentSessionId = null;
  }

  clearAllSessions(): void {
    this.currentSessionId = null;
    this.recentEvents.clear();
    this.responseAccumulators.clear();
    this.agentCoreAccumulators.clear();
    this.sessionSources.clear();
  }

  /**
   * Trigger a cache refresh on the backend to reload agent configurations from DynamoDB.
   * This is useful after updating agent configs in DynamoDB to have the running agent
   * pick up the changes without restarting.
   * 
   * @param resolvedAgent - The agent to use for the refresh request (any agent works)
   * @param forceReinitialize - If true, forces full re-initialization of the agent
   * @returns Observable that emits the cache refresh result
   */
  refreshAgentCache(resolvedAgent: EnrichedAgent, forceReinitialize: boolean = false): Observable<StreamEvent> {
    console.log('🔄 Triggering backend cache refresh...');
    
    return new Observable<StreamEvent>(observer => {
      this.triggerCacheRefreshInternal(resolvedAgent, forceReinitialize, observer).catch(error => {
        console.error('Error triggering cache refresh:', error);
        observer.error(error);
      });
    });
  }

  /**
   * Internal method to trigger cache refresh on the backend
   */
  private async triggerCacheRefreshInternal(resolvedAgent: EnrichedAgent, forceReinitialize: boolean, observer: any): Promise<void> {
    try {
      // Ensure client is set up
      if (!this.clientInitialized || !this.agentCoreClient) {
        await this.setupBedrockClient();
      }

      if (!this.agentCoreClient) {
        throw new Error('Bedrock Agent Runtime client not initialized');
      }

      if (!resolvedAgent.runtimeArn) {
        throw new Error('No runtime ARN available for cache refresh request');
      }

      const currentUser = await getCurrentUser();
      const userId = currentUser.signInDetails?.loginId || 'anonymous';
      const sessionId = this.sessionManager.getCurrentSessionId();

      console.log('🔄 Sending cache refresh request to backend...');

      // Send a cache-refresh-only request (no prompt)
      const commandInput: InvokeAgentRuntimeCommandInput = {
        agentRuntimeArn: resolvedAgent.runtimeArn,
        runtimeSessionId: sessionId,
        runtimeUserId: userId,
        qualifier: 'DEFAULT',
        payload: new TextEncoder().encode(JSON.stringify({
          refresh_cache: true,
          force_reinitialize: forceReinitialize,
          session_id: sessionId,
          user_id: userId,
          memory_id: this.memoryRecordId || this.awsConfig.getMemoryRecordId(),
          agent_name: resolvedAgent.name,
        })),
      };

      const command = new InvokeAgentRuntimeCommand(commandInput);
      const response = await this.agentCoreClient.send(command, { requestTimeout: 60000 });

      if (response.response) {
        // Process the response
        let responseText = '';
        
        if (typeof response.response.transformToString === 'function') {
          responseText = await response.response.transformToString();
        } else if (typeof response.response.transformToByteArray === 'function') {
          const bytes = await response.response.transformToByteArray();
          responseText = new TextDecoder().decode(bytes);
        } else if (response.response instanceof Uint8Array) {
          responseText = new TextDecoder().decode(response.response);
        } else if (typeof response.response === 'string') {
          responseText = response.response;
        }

        // Parse the response to extract cache refresh stats
        try {
          // Look for cache_refresh_complete event in the response
          const lines = responseText.split('\n');
          for (const line of lines) {
            if (line.trim().startsWith('data: ')) {
              const dataContent = line.substring(6);
              try {
                const eventData = JSON.parse(dataContent);
                if (eventData.type === 'cache_refresh_complete') {
                  console.log('✅ Cache refresh completed:', eventData.data);
                  observer.next({
                    type: 'cache_refresh_complete',
                    data: eventData.data,
                    timestamp: new Date(),
                    agentName: resolvedAgent.name,
                    messageType: 'cache-refresh'
                  });
                }
              } catch (parseError) {
                // Skip unparseable lines
              }
            }
          }
        } catch (parseError) {
          console.warn('Could not parse cache refresh response:', parseError);
        }

        observer.next({
          type: 'complete',
          data: 'Cache refresh request completed',
          timestamp: new Date()
        });
        observer.complete();
      } else {
        throw new Error('No response received from cache refresh request');
      }

    } catch (error: any) {
      console.error('❌ Cache refresh error:', error);
      observer.error(error);
    }
  }

  /**
   * Add sources for an agent in the current session
   * CRITICAL: Preserves original source content structure from backend
   */
  addAgentSources(agentName: string, sources: any): void {
    agentName = TextUtils.removeStackPrefixSuffix(agentName);

    // Handle new retrieve_and_generate response format
    if (sources && sources.citations && Array.isArray(sources.citations)) {
      // Extract unique sources from citations
      const processedSources: any[] = [];
      const seenUris = new Set<string>();

      sources.citations.forEach((citation: any) => {
        if (citation.retrievedReferences && Array.isArray(citation.retrievedReferences)) {
          citation.retrievedReferences.forEach((ref: any) => {
            const uri = ref.location?.s3Location?.uri;

            // Avoid duplicates
            if (uri && !seenUris.has(uri)) {
              seenUris.add(uri);

              processedSources.push({
                content: {
                  text: ref.content?.text || 'No content available'
                },
                location: ref.location,
                metadata: ref.metadata,
                agent: agentName,
                // Store the citation text that referenced this source
                citationText: citation.generatedResponsePart?.textResponsePart?.text || ''
              });
            }
          });
        }
      });

      if (processedSources.length > 0) {
        if (!this.sessionSources.has(agentName)) {
          this.sessionSources.set(agentName, []);
        }
        const existingSources = this.sessionSources.get(agentName)!;
        existingSources.push(...processedSources);
        console.log(`📦 Stored ${existingSources.length} sources for ${agentName} (retrieve_and_generate format)`);
      }
      return; // Exit early after processing new format
    }

    // Ensure sources is in the expected format
    let agentSources: any[] = [];
    if (Array.isArray(sources)) {
      // If sources is already an array, use it directly WITHOUT modification
      agentSources = sources;
    } else if (sources && typeof sources === 'object' && sources[agentName]) {
      // If sources is an object with agentName property, use that
      agentSources = sources[agentName];
    } else if (typeof sources === 'string') {
      // If sources is a string, log a warning and create a single-source array
      console.warn('addAgentSources received string instead of array/object:', sources);
      agentSources = [{
        content: { text: sources },
        location: { type: 'TEXT' }
      }];
    } else {
      // Unexpected format, log warning and return
      console.warn('addAgentSources received unexpected sources format:', sources);
      return;
    }

    if (!this.sessionSources.has(agentName)) {
      this.sessionSources.set(agentName, []);
    }

    const existingSources = this.sessionSources.get(agentName) || [];

    // Deduplicate sources based on source URI and content
    agentSources.forEach(newSource => {
      const newSourceUri = newSource.source || '';
      const newSourceContent = typeof newSource.content === 'string'
        ? newSource.content
        : newSource.content?.text || '';

      const isDuplicate = existingSources.some(existing => {
        const existingUri = existing.source || '';
        const existingContent = typeof existing.content === 'string'
          ? existing.content
          : existing.content?.text || '';

        return existingUri === newSourceUri && existingContent === newSourceContent;
      });

      if (!isDuplicate) {
        // CRITICAL: Store source exactly as received from backend
        existingSources.push(newSource);
      }
    });

    console.log(`📦 Stored ${existingSources.length} sources for ${agentName}`);
    this.sessionSources.set(agentName, existingSources);
  }

  replaceAgentCoreSources(sources: any[]) {
    // Ensure sources is an array before processing
    if (!Array.isArray(sources)) {
      console.warn('replaceAgentCoreSources called with non-array:', sources);
      return;
    }

    sources.forEach(source => {
      source.agent = TextUtils.removeStackPrefixSuffix(source.agent);

      this.sessionSources[source.agent] = sources.filter((s: any) => s.agent === source.agent)
    })
  }

  replaceAgentCoreSourcesForAllAgents(sources: any) {
    // Ensure sources is an array before processing
    this.sessionSources = sources;
  }

  /**
   * Get sources for a specific agent
   */
  getAgentSources(agentName: string): KnowledgeBaseSource[] {
    let sources = this.sessionSources.get(agentName) as Array<any>;
    if (!sources) {
      console.log(`No sources found for agent: ${agentName}`);
      return [];
    }

    console.log(`Processing ${sources.length} sources for agent: ${agentName}`);

    sources.forEach((source: any, index: number) => {
      source.agent = agentName;

      // Ensure content is properly structured
      if (typeof source.content === 'string') {
        console.log(`Source ${index}: Converting string content to object`);
        source.content = { text: source.content, type: 'text/plain' };
      }

      // Extract rows for CSV files
      if (source.metadata?.csv_headers && source.content?.text) {
        const headers = source.metadata.csv_headers;
        const contentText = source.content.text;

        console.log(`Source ${index}: Extracting CSV rows. Content length: ${contentText.length}, Headers: ${headers.length}`);

        // Parse CSV content into rows
        const rows = TextUtils.extractRowsFromText(contentText, headers);

        console.log(`Source ${index}: Extracted ${rows.length} rows`);

        // Store rows in metadata for display
        if (!source.metadata.rows || source.metadata.rows.length === 0) {
          source.metadata.rows = rows;
        }
      } else {
        console.log(`Source ${index}: No CSV headers or content. Has headers: ${!!source.metadata?.csv_headers}, Has content: ${!!source.content?.text}`);
      }
    });

    return sources;
  }

  /**
   * Check if an agent has sources in the current session
   */
  hasAgentSources(agentName: string) {
    return (this.sessionSources && this.sessionSources.get(agentName) && (this.sessionSources.get(agentName) as Array<any>).length > 0);
  }

  /**
   * Clear sources for a specific agent
   */
  clearAgentSources(agentName: string): void {
    agentName = TextUtils.removeStackPrefixSuffix(agentName);

    this.sessionSources.delete(agentName);
  }

  // Clean up old session data periodically
  private cleanupOldSessions(): void {
    const now = new Date();
    const cutoffTime = now.getTime() - (this.DUPLICATE_TIME_WINDOW_MS * 10); // Keep data for 10x the duplicate window

    for (const [sessionId, events] of this.recentEvents.entries()) {
      // Remove sessions with no recent activity
      const hasRecentActivity = events.some(event => event.timestamp.getTime() > cutoffTime);
      if (!hasRecentActivity) {
        this.recentEvents.delete(sessionId);
      }
    }

    // Clean up AgentCore accumulators for old sessions
    for (const [key] of this.agentCoreAccumulators.entries()) {
      // Key format: sessionId-agentName-type-toolId
      const sessionId = key.split('-')[0];
      if (!this.recentEvents.has(sessionId)) {
        this.agentCoreAccumulators.delete(key);
      }
    }
  }

  private extractVisualizationFromText(text: string, visualizationType: string): any | null {
    try {
      // Look for JSON blocks that start with { and contain the specified visualizationType
      // This approach finds balanced JSON blocks
      const lines = text.split('\n');
      let jsonStart = -1;
      let braceCount = 0;
      let potentialJson = '';

      for (let i = 0; i < lines.length; i++) {
        const line = lines[i].trim();

        // Look for lines that might start a JSON block
        if (line.startsWith('{') && (line.includes('"visualizationType"') || jsonStart === -1)) {
          if (jsonStart === -1) {
            jsonStart = i;
            potentialJson = '';
            braceCount = 0;
          }
        }

        // If we're inside a potential JSON block, accumulate it
        if (jsonStart !== -1) {
          potentialJson += line + '\n';

          // Count braces to find the end of the JSON block
          for (const char of line) {
            if (char === '{') braceCount++;
            if (char === '}') braceCount--;
          }

          // If braces are balanced and we have some content, try to parse
          if (braceCount === 0 && potentialJson.trim().length > 10) {
            try {
              const cleanJson = potentialJson.trim();
              const parsed = JSON.parse(cleanJson);

              // Check if this is the visualization type we're looking for
              if (parsed.visualizationType === visualizationType) {
                console.log(parsed)
                return parsed;
              } else {
                // Reset and continue looking
                jsonStart = -1;
                potentialJson = '';
                braceCount = 0;
              }
            } catch (parseError) {
              // Reset and continue looking
              jsonStart = -1;
              potentialJson = '';
              braceCount = 0;
            }
          }
        }
      }

      // Fallback: try to find visualization with a more targeted search
      if (text.includes(`"visualizationType": "${visualizationType}"`)) {
        // Find the start of the JSON object containing this
        const visualTypeIndex = text.indexOf(`"visualizationType": "${visualizationType}"`);
        let searchStart = visualTypeIndex;

        // Search backwards to find the opening brace
        while (searchStart > 0 && text[searchStart] !== '{') {
          searchStart--;
        }

        if (text[searchStart] === '{') {
          // Now find the matching closing brace
          let braceCount = 0;
          let jsonContent = '';

          for (let i = searchStart; i < text.length; i++) {
            jsonContent += text[i];
            if (text[i] === '{') braceCount++;
            if (text[i] === '}') {
              braceCount--;
              if (braceCount === 0) {
                try {
                  const parsed = JSON.parse(jsonContent);
                  if (parsed.visualizationType === visualizationType) {
                    //console.log(parsed)
                    return parsed;
                  }
                } catch (parseError) {
                  //console.warn('Fallback parse failed:', parseError);
                }
                break;
              }
            }
          }
        }
      }

    } catch (error) {
      //console.warn(`Error extracting ${visualizationType} visualization from text:`, error);
    }

    return null;
  }

  // Enhanced method specifically for AgentCore visualization extraction
  private extractAgentCoreVisualizations(text: string, toolUseId: string = ''): any[] {
    const visualizations: any[] = [];
    console.log('🔍 Extracting AgentCore visualizations from text length:', text.length);
    console.log('🔍 Text preview:', text.substring(0, 500) + (text.length > 500 ? '...' : ''));

    try {
      // Method 0: Check if the entire text is valid JSON with visualizationType
      try {
        const trimmedText = text.trim();
        if (trimmedText.startsWith('{') && trimmedText.endsWith('}')) {
          const parsed = JSON.parse(trimmedText);
          if (parsed.visualizationType && typeof parsed.visualizationType === 'string') {
            console.log('✅ Method 0: Entire response is a visualization JSON:', parsed.visualizationType);
            visualizations.push(parsed);
            return visualizations; // Return early if we found it
          }
        }
      } catch (parseError) {
        // Not a complete JSON object, continue with other methods
      }

      // Method 1: Look for JSON code blocks (```json ... ```)
      const jsonCodeBlockRegex = /```json\s*([\s\S]*?)\s*```/g;
      let match;
      let codeBlockCount = 0;

      while ((match = jsonCodeBlockRegex.exec(text)) !== null) {
        codeBlockCount++;
        const jsonContent = match[1].trim();
        console.log(`🔍 Method 1: Found JSON code block ${codeBlockCount}:`, jsonContent.substring(0, 200) + '...');

        try {
          const parsed = JSON.parse(jsonContent);
          console.log('🔍 Method 1: Parsed JSON:', parsed);

          if (parsed.visualizationType && typeof parsed.visualizationType === 'string') {
            console.log('✅ Method 1: Found visualization in JSON code block:', parsed.visualizationType);
            visualizations.push(parsed);
          } else {
            console.log('⚠️ Method 1: JSON missing visualizationType:', Object.keys(parsed));
          }
        } catch (parseError) {
          console.warn('❌ Method 1: Failed to parse JSON code block:', parseError);
        }
      }

      console.log(`🔍 Method 1: Processed ${codeBlockCount} JSON code blocks, found ${visualizations.length} visualizations so far`);

      // Method 2: Look for plain JSON blocks without code block markers
      const jsonBlockRegex = /(\{[\s\S]*?"visualizationType"[\s\S]*?\})/g;
      while ((match = jsonBlockRegex.exec(text)) !== null) {
        const jsonContent = match[1].trim();
        try {
          // Find the complete JSON object by balancing braces
          const completeJson = this.extractCompleteJsonObject(jsonContent);
          if (completeJson) {
            const parsed = JSON.parse(completeJson);
            if (parsed.visualizationType && typeof parsed.visualizationType === 'string') {
              // Check for duplicates
              const isDuplicate = visualizations.some(v =>
                v.visualizationType === parsed.visualizationType &&
                JSON.stringify(v) === JSON.stringify(parsed)
              );
              if (!isDuplicate) {
                console.log('✅ Found visualization in JSON block:', parsed.visualizationType);
                visualizations.push(parsed);
              }
            }
          }
        } catch (parseError) {
          console.warn('Failed to parse JSON block:', parseError);
        }
      }

      // Method 3: Look for XML-wrapped visualizations (supports both type and templateId attributes)
      const xmlVisualizationRegex = /<visualization-data[^>]*(?:type|templateId|visualizationType)="([^"]+)"[^>]*>\s*(\{[\s\S]*?)(?:<\/visualization-data>|$)/g;
      while ((match = xmlVisualizationRegex.exec(text)) !== null) {
        const visualizationType = match[1].replace('-visualization', '');
        const jsonContent = match[2].trim();
        try {
          const completeJson = this.extractCompleteJsonObject(jsonContent);
          if (completeJson) {
            const parsed = JSON.parse(completeJson);
            if (!parsed.visualizationType) {
              parsed.visualizationType = visualizationType;
            }
            console.log('✅ Found XML-wrapped visualization:', visualizationType);
            visualizations.push(parsed);
          }
        } catch (parseError) {
          console.warn('Failed to parse XML-wrapped visualization:', parseError);
        }
      }

      // Method 4: Look for templateId patterns (fallback)
      const templateIdRegex = /["']templateId["']:\s*["']([^"']*)["']/g;

      const typeRegex = /["']type["']:\s*["']([^"']*)["']/g;
      match = templateIdRegex.exec(text) || typeRegex.exec(text);

      while (match !== null) {
        const templateId = match[1];
        const visualizationType = templateId.replace('-visualization', '');

        // Try to extract the complete object containing this templateId
        const objectStart = text.lastIndexOf('{', match.index);
        if (objectStart !== -1) {
          const objectText = text.substring(objectStart);
          const completeJson = this.extractCompleteJsonObject(objectText);
          if (completeJson) {
            try {
              const parsed = JSON.parse(completeJson);
              if (!parsed.visualizationType) {
                parsed.visualizationType = visualizationType;
              }
              // add toolUseId
              parsed.toolUseId = toolUseId;
              // Check for duplicates
              const isDuplicate = visualizations.some(v =>
                v.visualizationType === parsed.visualizationType &&
                JSON.stringify(v) === JSON.stringify(parsed)
              );
              if (!isDuplicate) {
                console.log('✅ Found visualization via templateId:', visualizationType);
                visualizations.push(parsed);
              }
            } catch (parseError) {
              console.warn('Failed to parse templateId visualization:', parseError);
            }
          }
        }
      }

    } catch (error) {
      console.error('Error extracting AgentCore visualizations:', error);
    }

    console.log(`🎯 AgentCore visualization extraction complete: found ${visualizations.length} visualizations`);
    return visualizations;
  }

  // Helper method to extract a complete JSON object from text starting with '{'
  private extractCompleteJsonObject(text: string): string | null {
    if (!text.trim().startsWith('{')) {
      return null;
    }

    let braceCount = 0;
    let inString = false;
    let escaped = false;

    for (let i = 0; i < text.length; i++) {
      const char = text[i];

      if (escaped) {
        escaped = false;
        continue;
      }

      if (char === '\\') {
        escaped = true;
        continue;
      }

      if (char === '"') {
        inString = !inString;
        continue;
      }

      if (!inString) {
        if (char === '{') {
          braceCount++;
        } else if (char === '}') {
          braceCount--;
          if (braceCount === 0) {
            return text.substring(0, i + 1);
          }
        }
      }
    }

    return null; // Incomplete JSON object
  }

  // Generic method to extract all visualizations from text
  private extractAllVisualizationsFromText(text: string, isAgentCore = false): any[] {
    const visualizations: any[] = [];
    console.log('🔍 Extracting visualizations from text:', text.substring(0, 200) + '...' + (isAgentCore ? "agent core" : ""));

    try {
      // First, handle XML-wrapped visualizations (with or without closing tags, with or without attributes)
      const xmlVisualizationRegex = /<visualization-data[^>]*>\s*(\{[\s\S]*?)(?:<\/visualization-data>|$)/g;
      let xmlMatch;

      while ((xmlMatch = xmlVisualizationRegex.exec(text)) !== null) {
        const jsonContent = xmlMatch[1].trim();
        // get the type from the attribute on the visualization-data xml tag, which could be called "type", "templateId", "visualizationType", or "template"
        const visualizationTypeAttrRegex = /<visualization-data[^>]*(?:type|templateId|visualizationType|template)=['"]?([^'">\s]+)['"]?[^>]*>/;
        const attrMatch = visualizationTypeAttrRegex.exec(xmlMatch[0]);
        let visualizationType = attrMatch ? attrMatch[1].replace('-visualization', '') : null;

        try {
          // Find the end of the JSON object by counting braces
          let braceCount = 0;
          let jsonEnd = 0;
          let foundStart = false;

          for (let i = 0; i < jsonContent.length; i++) {
            const char = jsonContent[i];
            if (char === '{') {
              if (!foundStart) foundStart = true;
              braceCount++;
            } else if (char === '}') {
              braceCount--;
              if (foundStart && braceCount === 0) {
                jsonEnd = i + 1;
                break;
              }
            }
          }

          const completeJson = jsonEnd > 0 ? jsonContent.substring(0, jsonEnd) : jsonContent;
          const parsed = JSON.parse(completeJson);

          // Get visualizationType from JSON content (templateId or visualizationType field)
          if (!visualizationType)
            visualizationType = parsed.visualizationType || parsed.templateId?.replace('-visualization', '') || 'unknown';
          if (!parsed.visualizationType) {
            parsed.visualizationType = visualizationType;
            parsed.type = visualizationType;
            parsed.templateId = visualizationType;
            parsed.template = visualizationType;
          }

          console.log('✅ Found XML-wrapped visualization:', visualizationType, parsed);
          visualizations.push(parsed);
        } catch (parseError) {
          console.warn('Failed to parse XML-wrapped visualization JSON:', parseError);
        }
      }

      // Look for all JSON blocks that contain visualizationType
      const lines = text.split('\n');
      let jsonStart = -1;
      let braceCount = 0;

      for (let i = 0; i < lines.length; i++) {
        const line = lines[i].trim();

        // Look for lines that might start a JSON block
        if (line.startsWith('{') && (line.includes('"visualizationType"') || line.includes('"templateId"') || jsonStart === -1)) {
          if (jsonStart === -1) {
            jsonStart = i;
            braceCount = 0;
          }
        }

        if (jsonStart !== -1) {
          // Count braces to find the end of the JSON block
          for (const char of line) {
            if (char === '{') braceCount++;
            if (char === '}') braceCount--;
          }

          // If braces are balanced, we found a complete JSON block
          if (braceCount === 0) {
            const jsonLines = lines.slice(jsonStart, i + 1);
            const jsonText = jsonLines.join('\n');

            try {
              // Clean up the JSON text
              const cleanJson = jsonText
                .replace(/```json\s*/g, '')
                .replace(/```\s*/g, '')
                .replace(/^\s*[\r\n]/gm, '')
                .trim();

              const parsed = JSON.parse(cleanJson);

              // Check if this has a visualizationType property
              if (parsed.visualizationType && typeof parsed.visualizationType === 'string') {
                // Check if we already have this visualization from XML parsing
                const isDuplicate = visualizations.some(v =>
                  v.visualizationType === parsed.visualizationType &&
                  JSON.stringify(v) === JSON.stringify(parsed)
                );
                visualizations.push(parsed);

              }
              // Also check for templateId with -visualization suffix
              else if (parsed.templateId && typeof parsed.templateId === 'string' && parsed.templateId.endsWith('-visualization')) {
                // Extract visualization type by removing -visualization suffix
                const visualizationType = parsed.templateId.replace('-visualization', '');
                // Add visualizationType property to the parsed object
                parsed.visualizationType = visualizationType;
                console.log('✅ Found visualization via templateId:', visualizationType, parsed);

                // Check for duplicates
                const isDuplicate = visualizations.some(v =>
                  v.visualizationType === parsed.visualizationType &&
                  JSON.stringify(v) === JSON.stringify(parsed)
                );
                if (!isDuplicate) {
                  visualizations.push(parsed);
                }
                else console.log('duplicate:', parsed)
              }
            } catch (parseError) {
              // Continue looking for other JSON blocks
            }

            // Reset for next JSON block
            jsonStart = -1;
            braceCount = 0;
          }
        }
      }
    } catch (error) {
      //console.warn('Error extracting visualizations from text:', error);
    }

    //console.log('🎯 Total visualizations found:', visualizations.length, visualizations);
    return visualizations;
  }

  // Method to invoke model directly for scenario generation using Converse API
  async invokeLLMForScenarios(prompt: string): Promise<string> {
    try {
      // Ensure client is set up
      if (!this.clientInitialized || !this.bedrockRuntimeClient) {
        await this.setupBedrockClient();
      }

      if (!this.bedrockRuntimeClient) {
        throw new Error('Bedrock Runtime client not initialized');
      }

      // Use the Converse API for direct model interaction
      const command = new ConverseCommand({
        modelId: `${this.awsConfig.getConfig()?.aws.region.split('-')[0]}.anthropic.claude-sonnet-4-20250514-v1:0`,
        messages: [
          {
            role: 'user',
            content: [
              {
                text: prompt
              }
            ]
          }
        ],
        inferenceConfig: {
          maxTokens: 4000,
          temperature: 0.7,
          topP: 0.9
        }
      });

      const response = await this.bedrockRuntimeClient.send(command);

      if (!response.output?.message?.content) {
        throw new Error('No content received from LLM');
      }

      // Extract the text content from the response
      const textContent = response.output.message.content
        .filter(item => item.text)
        .map(item => item.text)
        .join('\n');

      return textContent;

    } catch (error) {
      console.error('Error invoking LLM for scenarios:', error);

      // Check for expired token specifically
      const errorMessage = error instanceof Error ? error.message : (error as any)?.toString() || '';
      const isExpiredToken = errorMessage.includes('ExpiredTokenException') ||
        errorMessage.includes('The security token included in the request is expired');

      if (isExpiredToken) {
        console.error('🔑 Token expired in LLM call, user needs to refresh:', errorMessage);
        // Create a special error that the UI can detect
        const expiredTokenError = new Error('Your session has expired. Please refresh the page to sign in again.');
        (expiredTokenError as any).isExpiredToken = true;
        throw expiredTokenError;
      }

      // Provide a more helpful error message
      const friendlyErrorMessage = error instanceof Error ? error.message : 'Unknown error';
      console.error('❌ LLM scenario generation failed:', friendlyErrorMessage);

      // Re-throw the error so the calling code can handle it appropriately
      throw error;
    }
  }

  // Method to invoke Claude Haiku for agent summaries using Converse API
  async invokeClaudeHaikuForSummary(prompt: string): Promise<string> {
    try {
      // Ensure client is set up
      if (!this.clientInitialized || !this.bedrockRuntimeClient) {
        await this.setupBedrockClient();
      }

      if (!this.bedrockRuntimeClient) {
        throw new Error('Bedrock Runtime client not initialized');
      }

      // Use the Converse API for direct model interaction with Claude Haiku
      const command = new ConverseCommand({
        modelId: 'anthropic.claude-3-haiku-20240307-v1:0',
        messages: [
          {
            role: 'user',
            content: [
              {
                text: prompt
              }
            ]
          }
        ],
        inferenceConfig: {
          maxTokens: 2000,
          temperature: 0.3,
          topP: 0.9
        }
      });

      const response = await this.bedrockRuntimeClient.send(command);

      if (!response.output?.message?.content) {
        throw new Error('No content received from Claude Haiku model');
      }

      // Extract the text content from the response
      const textContent = response.output.message.content
        .filter(item => item.text)
        .map(item => item.text)
        .join('\n');

      return textContent;

    } catch (error) {
      console.error('Error invoking model for summary:', error);

      // Check for expired token specifically
      const errorMessage = error instanceof Error ? error.message : (error as any)?.toString() || '';
      const isExpiredToken = errorMessage.includes('ExpiredTokenException') ||
        errorMessage.includes('The security token included in the request is expired');

      if (isExpiredToken) {
        console.error('🔑 Token expired in Claude Haiku call, user needs to refresh:', errorMessage);
        // Create a special error that the UI can detect
        const expiredTokenError = new Error('Your session has expired. Please refresh the page to sign in again.');
        (expiredTokenError as any).isExpiredToken = true;
        throw expiredTokenError;
      }

      // Provide a more helpful error message
      const friendlyErrorMessage = error instanceof Error ? error.message : 'Unknown error';
      console.error('❌ model summary generation failed:', friendlyErrorMessage);

      // Re-throw the error so the calling code can handle it appropriately
      throw error;
    }
  }

  sanitizeFileNameWithHyphens(fileName) {
    return fileName
      .replace(/[^a-zA-Z0-9\s]/g, '') // Remove non-alphanumeric (keep spaces)
      .replace(/\s+/g, '-')          // Replace spaces with single hyphens
      .replace(/^-+|-+$/g, '');      // Remove leading/trailing hyphens
  }

  /**
   * Invoke Claude Opus 4.5 for AI-assisted content generation
   * Used for generating agent instructions and visualization mappings
   * @param prompt The prompt to send to Claude Opus 4.5
   * @param maxTokens Maximum tokens for the response (default: 8000)
   * @returns The generated text content
   */
  async invokeClaudeOpus(prompt: string, maxTokens: number = 8000): Promise<string> {
    try {
      // Ensure client is set up
      if (!this.clientInitialized || !this.bedrockRuntimeClient) {
        await this.setupBedrockClient();
      }

      if (!this.bedrockRuntimeClient) {
        throw new Error('Bedrock Runtime client not initialized');
      }

      // Use the Converse API for Claude Opus 4.5
      const command = new ConverseCommand({
        modelId: 'us.anthropic.claude-opus-4-20250514-v1:0',
        messages: [
          {
            role: 'user',
            content: [
              {
                text: prompt
              }
            ]
          }
        ],
        inferenceConfig: {
          maxTokens: maxTokens,
          temperature: 0.7,
          topP: 0.9
        }
      });

      const response = await this.bedrockRuntimeClient.send(command);

      if (!response.output?.message?.content) {
        throw new Error('No content received from Claude Opus 4.5');
      }

      // Extract the text content from the response
      const textContent = response.output.message.content
        .filter(item => item.text)
        .map(item => item.text)
        .join('\n');

      return textContent;

    } catch (error) {
      console.error('Error invoking Claude Opus 4.5:', error);

      // Check for expired token specifically
      const errorMessage = error instanceof Error ? error.message : (error as any)?.toString() || '';
      const isExpiredToken = errorMessage.includes('ExpiredTokenException') ||
        errorMessage.includes('The security token included in the request is expired');

      if (isExpiredToken) {
        console.error('🔑 Token expired in Claude Opus call, user needs to refresh:', errorMessage);
        const expiredTokenError = new Error('Your session has expired. Please refresh the page to sign in again.');
        (expiredTokenError as any).isExpiredToken = true;
        throw expiredTokenError;
      }

      // Re-throw the error so the calling code can handle it appropriately
      throw error;
    }
  }
  /**
   * Invoke Claude Haiku 4.5 for visualization generation and other structured tasks.
   * Uses the cross-region inference profile for optimal availability.
   * @param prompt The prompt to send to Claude Haiku 4.5
   * @param maxTokens Maximum tokens for the response (default: 8000)
   * @returns The generated text content
   */
  async invokeClaudeHaiku(prompt: string, maxTokens: number = 8000): Promise<string> {
    try {
      if (!this.clientInitialized || !this.bedrockRuntimeClient) {
        await this.setupBedrockClient();
      }

      if (!this.bedrockRuntimeClient) {
        throw new Error('Bedrock Runtime client not initialized');
      }

      const command = new ConverseCommand({
        modelId: 'global.anthropic.claude-haiku-4-5-20251001-v1:0',
        messages: [
          {
            role: 'user',
            content: [{ text: prompt }]
          }
        ],
        inferenceConfig: {
          maxTokens,
          temperature: 0.5
        }
      });

      const response = await this.bedrockRuntimeClient.send(command);

      if (!response.output?.message?.content) {
        throw new Error('No content received from Claude Haiku 4.5');
      }

      const textContent = response.output.message.content
        .filter(item => item.text)
        .map(item => item.text)
        .join('\n');

      return textContent;

    } catch (error) {
      console.error('Error invoking Claude Haiku 4.5:', error);

      const errorMessage = error instanceof Error ? error.message : (error as any)?.toString() || '';
      const isExpiredToken = errorMessage.includes('ExpiredTokenException') ||
        errorMessage.includes('The security token included in the request is expired');

      if (isExpiredToken) {
        console.error('🔑 Token expired in Claude Haiku 4.5 call, user needs to refresh:', errorMessage);
        const expiredTokenError = new Error('Your session has expired. Please refresh the page to sign in again.');
        (expiredTokenError as any).isExpiredToken = true;
        throw expiredTokenError;
      }

      throw error;
    }
  }



  /**
   * Upload file to S3 and return the S3 URI
   */
  private async uploadFileToS3(file: AttachedFile, uniqueFileName: string): Promise<string> {
    try {
      // Use cached auth session from awsConfig to prevent excessive fetchAuthSession calls
      const session = await this.awsConfig.getCachedAuthSession();
      const bucketName = this.awsConfig.getCreativesBucket();
      const region = this.awsConfig.getRegion();

      if (!bucketName) {
        throw new Error('Creatives bucket not configured');
      }

      if (!session.credentials) {
        throw new Error('No AWS credentials available');
      }

      // Initialize S3 client
      const s3Client = new S3Client({
        region: region,
        credentials: session.credentials
      });

      // Convert base64 to Uint8Array (browser-compatible)
      const binaryString = atob(file.base64Content);
      const bytes = new Uint8Array(binaryString.length);
      for (let i = 0; i < binaryString.length; i++) {
        bytes[i] = binaryString.charCodeAt(i);
      }

      // Upload to S3 under 'uploads' folder
      const s3Key = `uploads/${uniqueFileName}`;
      const putCommand = new PutObjectCommand({
        Bucket: bucketName,
        Key: s3Key,
        Body: bytes,
        ContentType: file.mediaType || file.type,
        Metadata: {
          'original-filename': file.name,
          'upload-timestamp': new Date().toISOString()
        }
      });

      await s3Client.send(putCommand);

      // Return S3 URI
      const s3Uri = `s3://${bucketName}/${s3Key}`;
      console.log(`✅ File uploaded to S3: ${s3Uri}`);
      return s3Uri;

    } catch (error: any) {
      console.error('❌ Error uploading file to S3:', error);
      throw new Error(`Failed to upload file to S3: ${error?.message || 'Unknown error'}`);
    }
  }

  // AgentCore invocation method using proper AWS SDK
  private async invokeAgentCoreStreamInternal(resolvedAgent: any, query: any, observer: any, sessionId: string, attachedFiles?: AttachedFile[], directMentionTarget?: string | null, refreshCache: boolean = false): Promise<void> {
    try {
      // Ensure client is set up
      if (!this.clientInitialized || !this.agentCoreClient) {
        await this.setupBedrockClient();
      }

      if (!this.agentCoreClient) {
        throw new Error('Bedrock Agent Runtime client not initialized');
      }

      // Check if we have the required runtime ARN - retry if not available yet (race condition on first login)
      if (!resolvedAgent.runtimeArn) {
        console.log(`⏳ Runtime ARN not yet available for ${resolvedAgent.name}, waiting for config to load...`);
        
        // Retry up to 5 times with 500ms delay to allow SSM config to load
        const maxRetries = 5;
        const retryDelay = 500;
        
        for (let attempt = 1; attempt <= maxRetries; attempt++) {
          await new Promise(resolve => setTimeout(resolve, retryDelay));
          
          // Re-fetch the agent from config service to get updated runtimeArn
          const refreshedAgent = this.agentConfig.getAgent(resolvedAgent.name || resolvedAgent.agentType);
          if (refreshedAgent?.runtimeArn) {
            console.log(`✅ Runtime ARN loaded on attempt ${attempt}: ${refreshedAgent.runtimeArn}`);
            resolvedAgent = refreshedAgent;
            break;
          }
          
          if (attempt === maxRetries) {
            throw new Error(`No runtime ARN provided for AgentCore agent: ${resolvedAgent.name}. AgentCore agents require a runtimeArn field in the configuration. Please refresh the page to reload agent configuration.`);
          }
          
          console.log(`⏳ Retry ${attempt}/${maxRetries} - still waiting for runtime ARN...`);
        }
      }

      // Emit initial trace event with session info
      observer.next({
        type: 'trace',
        data: `Initializing AgentCore agent: ${resolvedAgent.name || resolvedAgent.id} (Session: ${sessionId.substring(0, 8)}...)`,
        timestamp: new Date(),
        agentName: resolvedAgent.name || resolvedAgent.id,
        messageType: 'reasoning'
      });
      // Get current user for the request
      const currentUser = await getCurrentUser();
      const userId = currentUser.signInDetails?.loginId || 'anonymous';

      console.log(`🤖 AgentCore Request Details:`, {
        agentName: resolvedAgent.name,
        runtimeArn: resolvedAgent.runtimeArn,
        sessionId: sessionId,
        userId: userId,
        queryLength: query.length
      });

      if (attachedFiles && attachedFiles.length > 0 && attachedFiles[0]) {
        let extension = attachedFiles[0].name.substring(attachedFiles[0].name.lastIndexOf('.'))
        // Add timestamp to document name to ensure uniqueness across conversation
        const uniqueDocName = `${this.sanitizeFileNameWithHyphens(attachedFiles[0].name)}-${crypto.randomUUID()}${extension}`;

        try {
          // Upload file to S3
          const s3Uri = await this.uploadFileToS3(attachedFiles[0], uniqueDocName);

          // Use S3 location instead of bytes
          query += `<file>${JSON.stringify({
            name: uniqueDocName,
            "format": this.getFormatCode(attachedFiles[0]),
            "source": { "s3Location": { "uri": s3Uri } }
          })
            }</file>`
          query = [
            { "text": query },
            {
              "document": {
                name: uniqueDocName,
                "format": this.getFormatCode(attachedFiles[0]),
                "source": { "s3Location": { "uri": s3Uri } }
              }
            },
            {
              "cachePoint": { "type": "default" }
            }
          ];
        } catch (s3Error) {
          console.warn('⚠️ S3 upload failed, falling back to bytes:', s3Error);
          // Fallback to bytes if S3 upload fails

          query = [
            { "text": query },
            {
              "document": {
                name: uniqueDocName,
                "format": this.getFormatCode(attachedFiles[0]),
                "source": { "bytes": attachedFiles[0].base64Content }
              }
            },
            {
              "cachePoint": { "type": "default" }
            }
          ];
        }
      }

      // Prepare the command input for AgentCore using InvokeAgentRuntimeCommand
      const commandInput: InvokeAgentRuntimeCommandInput = {
        agentRuntimeArn: resolvedAgent.runtimeArn,
        runtimeSessionId: sessionId,
        runtimeUserId: userId,
        qualifier: 'DEFAULT',

        payload: new TextEncoder().encode(JSON.stringify({
          prompt: query,
          session_id: sessionId,
          user_id: userId,
          // CRITICAL: memory_id and agent_name must be at top level for backend extraction
          // Use dynamically loaded memoryRecordId (from ListMemoriesCommand) with fallback to config
          memory_id: this.memoryRecordId || this.awsConfig.getMemoryRecordId(),
          agent_name: resolvedAgent.name,
          enableStreaming: true,
          direct_mention_target: directMentionTarget, // Explicit flag for direct mentions
          // Cache refresh flag - when true, backend will reload configs from DynamoDB
          refresh_cache: refreshCache,
          // Add session metadata for additional context
          session_metadata: {
            agent_name: resolvedAgent.name,
            agent_type: resolvedAgent.agentType,
            timestamp: new Date().toISOString(),
            session_id: sessionId,
            memory_id: this.memoryRecordId || this.awsConfig.getMemoryRecordId()
          },
          context: attachedFiles ? { attachedFiles } : {},
          media: attachedFiles && attachedFiles.length > 0 ? {
            "type": "file",
            "format": this.getFormatCode(attachedFiles[0]),
            "data": attachedFiles[0].base64Content,
          } : {},
        })),
      };

      const command = new InvokeAgentRuntimeCommand(commandInput);
      const response = await this.agentCoreClient.send(command,{
    requestTimeout: 300000    });
      if (response.response) {
        try {
          console.log('🔄 AgentCore streaming response received');

          // Handle the streaming response properly
          if (typeof response.response.transformToWebStream === 'function') {
            // Use Web Streams API for proper streaming
            const stream = response.response.transformToWebStream();
            const reader = stream.getReader();
            const decoder = new TextDecoder();
            let accumulatedText = '';
            let responseComplete = false;

            try {
              while (!responseComplete) {
                const { done, value } = await reader.read();
                if (done)
                  break;

                // Decode the chunk
                const chunk = decoder.decode(value, { stream: true });
                //console.log('🔍 Raw AgentCore chunk:', chunk);

                // Process each line in the chunk (Server-Sent Events format)
                const lines = chunk.split('\n');
                for (const line of lines) {

                  if (line.trim().startsWith('data: ')) {
                    let dataContent = line.substring(6); // Remove 'data: ' prefix
                    // Skip Python object representations and non-JSON data
                    if (dataContent.includes('<strands.agent.agent.Agent object') ||
                      dataContent.includes('event_loop_cycle_id') ||
                      !dataContent.trim().startsWith('{')) {
                      continue;
                    }

                    try {
                      let isExternalAgent = false;
                      if (dataContent.toLowerCase().indexOf('invoke_external_agent_with_sdk') > -1) {
                        isExternalAgent = true;
                        let escapedLine = dataContent.replace(/\\n/g, "\n")
                          .replace(/\\'/g, "'")
                          .replace(/\\"/g, '"')
                          .replace(/\\&/g, "&")
                          .replace(/\\r/g, "\r")
                          .replace(/\\t/g, "\t")
                          .replace(/\\b/g, "\b")
                          .replace(/\\f/g, "\f");
                        dataContent = escapedLine;
                      }

                      const eventData = JSON.parse(dataContent);

                      // EARLY SUPPRESSION CHECK - before any processing
                      if (eventData.suppress_in_direct_mode === true &&
                        this.directMentionMode.get(sessionId)) {
                        console.log('🚫 EARLY SUPPRESSION: Skipping message in direct mention mode:', {
                          messageRole: eventData.message_role,
                          directMentionMode: this.directMentionMode.get(sessionId),
                          sessionId: sessionId
                        });
                        continue; // Skip this entire event
                      }

                      if (isExternalAgent) console.log('🔍 Parsed External Agent event:', eventData);
                      //console.log('🔍 Parsed AgentCore event:', dataContent.substring(0, 300));

                      // Handle Strands StreamEvent format from AgentCore
                      if (eventData.event) {
                        const strandsEvent = eventData.event;

                        // Handle messageStart event
                        if (strandsEvent.messageStart) {
                          observer.next({
                            type: 'trace',
                            data: `Message start...`,
                            timestamp: new Date(),
                            agentName: resolvedAgent.name || resolvedAgent.id,
                            messageType: 'reasoning'
                          });
                        }
                        // Handle contentBlockStart event
                        else if (strandsEvent.contentBlockStart) {
                          if (strandsEvent.contentBlockStart.start?.toolUse) {
                            const toolName = strandsEvent.contentBlockStart.start.toolUse.name;
                            observer.next({
                              type: 'trace',
                              data: `🔧 Using tool: ${toolName}`,
                              timestamp: new Date(),
                              agentName: resolvedAgent.name || resolvedAgent.id,
                              messageType: 'tool-trace'
                            });
                          }
                        }
                        // Handle contentBlockDelta event - this contains the actual text chunks
                        else if (strandsEvent.contentBlockDelta?.delta?.text) {
                          const textChunk = strandsEvent.contentBlockDelta.delta.text;
                          accumulatedText += textChunk;

                          // Emit the chunk for real-time display (simplified approach)
                          /*observer.next({
                            type: 'chunk',
                            data: textChunk,
                            timestamp: new Date(),
                            agentName: resolvedAgent.name || resolvedAgent.id,
                            messageType: 'streaming-chunk'
                          });

                          console.log('✅ AgentCore text chunk processed:', textChunk);*/
                        }
                        // Handle contentBlockStop event
                        else if (strandsEvent.contentBlockStop) {
                          console.log('🏁 AgentCore content block stopped');
                        }
                        // Handle messageStop event
                        else if (strandsEvent.messageStop || eventData.data.finalResponse) {
                          //console.log('🏁 AgentCore message stopped:', strandsEvent.messageStop);
                          // observer.next({
                          //   type: 'trace',
                          //   data: accumulatedText,
                          //   timestamp: new Date(),
                          //   agentName: resolvedAgent.name || resolvedAgent.id,
                          //   messageType: 'final-response'
                          // });
                          console.log('message stop message - event.event', accumulatedText)
                        }
                      }
                      // Handle direct message format from AgentCore (new format)
                      if (eventData.message) {

                        console.log('handling event data directly???', eventData)

                        const message = eventData.message;
                        // Handle reasoning content
                        if (message.content && Array.isArray(message.content)) {
                          for (const contentItem of message.content) {
                            // Process reasoning content
                            if (contentItem.reasoningContent?.reasoningText?.text) {
                              const fullReasoningText = contentItem.reasoningContent?.reasoningText?.text;

                              observer.next({
                                type: 'chunk',
                                data: fullReasoningText,
                                timestamp: new Date(),
                                agentName: resolvedAgent.name || resolvedAgent.id,
                                messageType: 'reasoning',
                                metadata: { type: 'reasoning' }
                              });
                              console.log('reasoning message', message)
                            }

                            // Process tool results as separate agent responses - this gets picked up properly
                            else if (contentItem.toolResult) {
                              const toolResult = contentItem.toolResult;
                              console.log('🔍 Processing toolResult:', toolResult);

                              if (toolResult.content && Array.isArray(toolResult.content)) {

                                for (const toolContent of toolResult.content) {
                                  if (toolContent.text) {
                                    // Parse the tool result text to extract agent information
                                    const toolText = toolContent.text;
                                    console.log('🔍 Tool result text: line 2026', toolText.substring(0, 200));

                                    // Look for agent-message format: <agent-message agent='agent_name'>content</agent-message>
                                    const agentMessageMatch = toolText.replace(" direct_mention='true'", '').match(/<agent-message agent='([^']+)'>([\s\S]*?)<\/agent-message>/);
                                    if (agentMessageMatch) {
                                      const toolAgentName = agentMessageMatch[1];
                                      const toolAgentContent = agentMessageMatch[2].trim();

                                      // Convert tool agent name to display name
                                      const toolAgentDisplayName = toolAgentName;

                                      console.log('✅ Found agent message in tool result:', {
                                        toolAgentName,
                                        toolAgentDisplayName,
                                        contentPreview: toolAgentContent.substring(0, 100),
                                        toolUseId: toolResult.toolUseId,
                                        toolName: toolResult.name
                                      });

                                      observer.next({
                                        type: 'chunk',
                                        data: toolAgentContent,
                                        timestamp: new Date(),
                                        agentName: toolAgentName,
                                        messageType: 'collaborator-response',
                                        metadata: {
                                          type: 'collaborator-response',
                                          originalAgentName: toolAgentName,
                                          toolUseId: toolResult.toolUseId,
                                          name: toolResult.name
                                        }
                                      });
                                      console.log('✅ AgentCore tool agent message emitted with toolUseId:', toolResult.toolUseId);
                                    } else {
                                      console.log('⚠️ No agent-message wrapper found in tool result');
                                      // Regular tool result without agent wrapper

                                      console.log('✅ AgentCore tool result processed:', toolText.substring(0, 100) + '...');
                                      try {
                                        const agentCoreVisualizations = this.extractAllVisualizationsFromText(toolText);
                                        console.log('🔍 Found AgentCore visualizations:', agentCoreVisualizations.length);

                                        for (const visualization of agentCoreVisualizations) {
                                          if (visualization && visualization.visualizationType) {
                                            const messageType = `${visualization.visualizationType}-visualization`;
                                            const visualContent = visualization;
                                            this.addToRecentEvents(sessionId, messageType, visualContent);
                                            observer.next({
                                              type: 'chunk',
                                              data: JSON.stringify(visualization),
                                              timestamp: new Date(),
                                              agentName: resolvedAgent.name || resolvedAgent.id,
                                              messageType: "visualization-data",
                                              metadata: {
                                                type: 'visualization-data',
                                                toolUseId: toolResult.toolUseId,
                                                name: toolResult.name
                                              }
                                            });

                                          }
                                          else {
                                            observer.next({
                                        type: 'chunk',
                                        data: toolText,
                                        timestamp: new Date(),
                                        agentName: resolvedAgent.name || resolvedAgent.id,
                                        messageType: 'streaming-chunk',
                                        metadata: {
                                          type: 'tool-result',
                                          toolUseId: toolResult.toolUseId
                                        }
                                      });
                                      console.log('✅ AgentCore tool result processed:', toolText.substring(0, 100) + '...');

                                          }
                                        }
                                      }
                                      catch (ex) {
                                        console.log('could not extract visualizations from the final response')
                                      }
                                    }
                                  }
                                }
                              }
                            }
                            else if (contentItem.toolUse && ((contentItem.toolUse.name as string).indexOf("invoke_specialist")>=0 && contentItem.text != "[]")) {

                              const toolUse = contentItem.toolUse;
                              const toolAgentDisplayName = toolUse.input.agent_name;

                              if (toolUse.input) {
                                if (!toolUse.input.agent_prompt.startsWith(`@${toolUse.input.agent_name}`) && !toolUse.input.agent_prompt.startsWith(`@${toolAgentDisplayName}`))
                                  toolUse.input.agent_prompt = `@${toolAgentDisplayName} ` + toolUse.input.agent_prompt;
                                
                                console.log('🔧 [Specialist Tracker] Emitting supervisor-to-collaborator with toolUseId:', toolUse.toolUseId, 'name:', toolUse.name);
                                
                                observer.next({
                                  type: 'chunk',
                                  data: toolUse.input.agent_prompt,
                                  timestamp: new Date(),
                                  agentName: resolvedAgent.name || resolvedAgent.id,
                                  messageType: 'supervisor-to-collaborator',
                                  metadata: {
                                    type: 'chunk',
                                    toolUseId: toolUse.toolUseId,
                                    name: toolUse.name
                                  }
                                });

                                console.log('✅ AgentCore agent tool use detected:', toolUse.input.agent_prompt.substring(0, 100) + '...');
                              }



                            }
                            else if (contentItem.text) {                              const text = contentItem.text;

                              // Skip if this text contains agent-message XML that was already processed from toolResult
                              if (text.includes('<agent-message') && text.includes('</agent-message>')) {
                                console.log('⚠️ Skipping duplicate agent-message in text content');
                                continue;
                              }

                              // For AgentCore agents, each message should be treated as separate
                              // Don't accumulate across different messages - just send the full text

                              observer.next({
                                type: 'chunk',
                                data: text,
                                timestamp: new Date(),
                                agentName: resolvedAgent.name || resolvedAgent.id,
                                messageType: 'chunk',
                                metadata: { type: 'delegation', signature: contentItem }
                              });
                              console.log('final message', message)
                              //console.log('✅ AgentCore reasoning processed (length:', fullReasoningText.length, '):', fullReasoningText.substring(0, 50) + '...');
                            }
                          }
                        }

                        else if (message.content && message.content.text) {
                          const text = message.content.text;

                          // For AgentCore agents, each message should be treated as separate
                          // Don't accumulate across different messages - just send the full text

                          observer.next({
                            type: 'chunk',
                            data: text,
                            timestamp: new Date(),
                            agentName: resolvedAgent.name || resolvedAgent.id,
                            messageType: 'final-response',
                            metadata: { type: 'final-response' }
                          });
                          try {
                            const agentCoreVisualizations = this.extractAgentCoreVisualizations(text);
                            console.log('🔍 Found AgentCore visualizations:', agentCoreVisualizations.length);

                            for (const visualization of agentCoreVisualizations) {
                              if (visualization && visualization.visualizationType) {
                                const messageType = `${visualization.visualizationType}-visualization`;
                                const visualContent = this.extractEventContent(messageType, JSON.stringify(visualization));
                                if (!this.isDuplicateEvent(sessionId, messageType, visualContent)) {
                                  this.addToRecentEvents(sessionId, messageType, visualContent);
                                  console.log('✅ Emitting AgentCore visualization:', messageType, visualization.title || 'Untitled');
                                  observer.next({
                                    type: 'trace',
                                    data: JSON.stringify(visualization),
                                    timestamp: new Date(),
                                    agentName: resolvedAgent.name || resolvedAgent.id,
                                    messageType: messageType as any
                                  });
                                }
                              }
                            }
                          }
                          catch (ex) {
                            console.log('could not extract visualizations from the final response')
                          }
                          console.log('final message', message)
                          //console.log('✅ AgentCore reasoning processed (length:', fullReasoningText.length, '):', fullReasoningText.substring(0, 50) + '...');
                        }
                      }
                      // NEW CLEAN APPROACH: Detect knowledge base sources by looking for start_event_loop and sources_str
                      // Handle sources event from backend (new separate event approach)
                      if (eventData.type === 'sources' && eventData.sources) {
                        console.log('📦 Received sources event from backend:', eventData.sources);

                        // Process sources for each agent
                        if (typeof eventData.sources === 'object' && !Array.isArray(eventData.sources)) {
                          // Sources is an object with agent names as keys
                          for (const [agentName, agentSources] of Object.entries(eventData.sources)) {
                            if (Array.isArray(agentSources) && agentSources.length > 0) {
                              console.log(`📦 Adding ${agentSources.length} sources for agent: ${agentName}`);
                              // CRITICAL: Don't modify source content here - keep it as-is from backend
                              this.addAgentSources(agentName, agentSources);
                            }
                          }
                        } else if (Array.isArray(eventData.sources)) {
                          // Sources is an array - group by agent
                          const sourcesByAgent: { [key: string]: any[] } = {};
                          for (const source of eventData.sources) {
                            const agentName = source.agent || source.tool || 'unknown';
                            if (!sourcesByAgent[agentName]) {
                              sourcesByAgent[agentName] = [];
                            }
                            sourcesByAgent[agentName].push(source);
                          }

                          // Add sources for each agent
                          for (const [agentName, agentSources] of Object.entries(sourcesByAgent)) {
                            console.log(`📦 Adding ${agentSources.length} sources for agent: ${agentName}`);
                            // CRITICAL: Don't modify source content here - keep it as-is from backend
                            this.addAgentSources(agentName, agentSources);
                          }
                        }

                        // Emit a sources event to notify subscribers
                        observer.next({
                          type: 'sources',
                          data: eventData.sources,
                          timestamp: new Date(),
                          agentName: resolvedAgent.name || resolvedAgent.id,
                          messageType: 'sources-update'
                        });
                      }
                      // Handle other event types that might not be wrapped in 'event'
                      if (eventData.init_event_loop || eventData.start || eventData.start_event_loop) {
                        // Initialization events - just log them
                        console.log('AgentCore initialization event:', eventData);

                      }


                    } catch (parseError) {
                      // Skip unparseable data lines
                      //console.warn('⚠️ Could not parse AgentCore event data:', dataContent.substring(0, 100));
                    }
                  }
                }
              }
            } finally {
              reader.releaseLock();
            }



          } else {
            // Fallback to non-streaming processing
            console.log('⚠️ AgentCore response not streamable, using fallback processing');

            let responseText = '';
            let parsedResponse: any = null;

            if (typeof response.response.transformToString === 'function') {
              responseText = await response.response.transformToString();
            } else if (typeof response.response.transformToByteArray === 'function') {
              const bytes = await response.response.transformToByteArray();
              responseText = new TextDecoder().decode(bytes);
            } else if (response.response instanceof Uint8Array) {
              responseText = new TextDecoder().decode(response.response);
            } else if (typeof response.response === 'string') {
              responseText = response.response;
            } else {
              responseText = JSON.stringify(response.response);
            }

            // Try to parse as JSON to check for sources
            try {
              parsedResponse = JSON.parse(responseText);
            } catch (e) {
              // Not JSON, treat as plain text
            }

            // Check if parsed response has sources
            if (parsedResponse && parsedResponse.sources) {
              console.log('📦 Fallback: Found sources in response:', parsedResponse.sources);

              // Process sources
              if (typeof parsedResponse.sources === 'object' && !Array.isArray(parsedResponse.sources)) {
                for (const [agentName, agentSources] of Object.entries(parsedResponse.sources)) {
                  if (Array.isArray(agentSources) && agentSources.length > 0) {
                    console.log(`📦 Fallback: Adding ${agentSources.length} sources for agent: ${agentName}`);
                    this.addAgentSources(agentName, agentSources);
                  }
                }
              }

              // Emit sources event
              observer.next({
                type: 'sources',
                data: parsedResponse.sources,
                timestamp: new Date(),
                agentName: resolvedAgent.name || resolvedAgent.id,
                messageType: 'sources-update'
              });
            }

            // Emit as a single chunk
            if (responseText.trim()) {
              observer.next({
                type: 'chunk',
                data: responseText,
                timestamp: new Date(),
                agentName: resolvedAgent.name || resolvedAgent.id,
                messageType: 'agentcore-response'
              });

              // Extract visualizations using enhanced AgentCore method
              const agentCoreVisualizations = this.extractAgentCoreVisualizations(responseText);
              console.log('🔍 Fallback: Found AgentCore visualizations:', agentCoreVisualizations.length);

              for (const visualization of agentCoreVisualizations) {
                if (visualization && visualization.visualizationType) {
                  const messageType = `${visualization.visualizationType}-visualization`;
                  const visualContent = this.extractEventContent(messageType, JSON.stringify(visualization));
                  if (!this.isDuplicateEvent(sessionId, messageType, visualContent)) {
                    this.addToRecentEvents(sessionId, messageType, visualContent);
                    console.log('✅ Fallback: Emitting AgentCore visualization:', messageType, visualization.title || 'Untitled');
                    observer.next({
                      type: 'trace',
                      data: JSON.stringify(visualization),
                      timestamp: new Date(),
                      agentName: resolvedAgent.name || resolvedAgent.id,
                      messageType: messageType as any
                    });
                  }
                }
              }
            }
          }

        } catch (streamError: any) {
          console.error('❌ Error processing AgentCore stream:', streamError);
          observer.next({
            type: 'error',
            data: `Stream processing error: ${streamError.message}`,
            timestamp: new Date()
          });
        }
      } else {
        console.warn('⚠️ No response stream from AgentCore');
        observer.next({
          type: 'error',
          data: 'No response received from AgentCore agent',
          timestamp: new Date()
        });
      }

      // Session cleanup handled elsewhere

      // Emit completion
      //if (!skip) {
      observer.next({
        type: 'complete',
        data: 'AgentCore streaming completed',
        timestamp: new Date()
      });

      observer.complete();
      //}

    } catch (error: any) {
      console.error('❌ AgentCore invocation error:', error);

      let errorMessage = 'Failed to invoke AgentCore agent';
      const originalErrorMessage = error?.message || error?.toString() || '';

      // Check for expired token specifically
      const isExpiredToken = originalErrorMessage.includes('ExpiredTokenException') ||
        originalErrorMessage.includes('The security token included in the request is expired');

      if (isExpiredToken) {
        console.error('🔑 Token expired in AgentCore call, user needs to refresh:', originalErrorMessage);
        observer.next({
          type: 'error',
          data: 'Your session has expired. Please refresh the page to sign in again.',
          timestamp: new Date(),
          metadata: {
            errorType: 'ExpiredTokenException',
            requiresRefresh: true
          }
        });
        observer.complete();
        return;
      }

      if (error.message?.includes('credentials')) {
        errorMessage = 'Authentication issue with AgentCore. Please check credentials.';
      } else if (error.message?.includes('AccessDenied')) {
        errorMessage = 'Access denied to AgentCore runtime. Please check permissions.';
      } else if (error.message?.includes('ValidationException')) {
        errorMessage = 'Invalid request format for AgentCore.';
      } else if (error.message?.includes('ResourceNotFoundException')) {
        errorMessage = `AgentCore runtime not found. Please verify the runtime ARN: ${resolvedAgent.runtimeArn}`;
      } else if (error.message) {
        errorMessage = error.message;
      }

      observer.next({
        type: 'error',
        data: errorMessage,
        timestamp: new Date()
      });

      // Session cleanup handled elsewhere

      observer.next({
        type: 'complete',
        data: 'AgentCore invocation failed',
        timestamp: new Date()
      });

      observer.complete();
    }
  }
  getFormatCode(file: AttachedFile) {
    //pdf | csv | doc | docx | xls | xlsx | html | txt | md
    return file.name.toLowerCase().substring(file.name.lastIndexOf('.') + 1)
  }
  processAgentCoreEventData(eventData: any) {
    console.log('Event data received', eventData)
  }
  getStackSpecificActorId(name: string): string | undefined {
    if (!name) return undefined;
    let stackPrefix = this.awsConfig.getStackPrefix();
    let uniqueId = this.awsConfig.getStackSuffix();
    name = name.toLowerCase().replaceAll("-", "_").replaceAll(" ", "_");
    if (name.indexOf(stackPrefix) == -1) name = stackPrefix + "_" + name;
    if (name.indexOf(uniqueId) == -1) name = name + "_" + uniqueId;
    return name;
  }

  // Cleanup method to clear timers and prevent memory leaks
  cleanup(): void {
    if (this.cleanupInterval) {
      clearInterval(this.cleanupInterval);
      this.cleanupInterval = null;
    }

    // Clear all session data
    this.recentEvents.clear();
    this.responseAccumulators.clear();
  }

  // Test method for debugging AgentCore visualization extraction
  public testAgentCoreVisualizationExtraction(sampleText: string): any[] {
    console.log('🧪 Testing AgentCore visualization extraction with sample text:', sampleText.substring(0, 300) + '...');
    const result = this.extractAgentCoreVisualizations(sampleText);
    console.log('🧪 Test result:', result);
    return result;
  }

  // Method to update chat messages for AgentCore sources tracking
  updateChatMessages(messages: any[]): void {
    this.chatMessages = [...messages];
  }

  /**
   * Retrieve memory records for a session to generate a summary
   * Uses the AgentCore SDK to fetch conversation history
   */
  async retrieveSessionMemory(sessionId: string, searchQuery: string = 'summarize the conversation'): Promise<{
    summary: string;
    topics: string[];
    messageCount: number;
    records: any[];
  }> {
    try {
      // Ensure client is set up
      if (!this.clientInitialized || !this.agentCoreClient) {
        await this.setupBedrockClient();
      }

      if (!this.agentCoreClient) {
        throw new Error('AgentCore client not initialized');
      }

      const memoryId = this.memoryRecordId || this.awsConfig.getMemoryRecordId();
      if (!memoryId) {
        throw new Error('Memory ID not configured');
      }

      // Use ListMemoryRecordsCommand to retrieve session memory
      const command = new ListMemoryRecordsCommand({
        memoryId: memoryId,
        namespace: sessionId,
        maxResults: 50
      });

      const response = await this.agentCoreClient.send(command);
      const records = response.memoryRecordSummaries || [];

      if (records.length === 0) {
        return {
          summary: 'No conversation history found for this session.',
          topics: [],
          messageCount: 0,
          records: []
        };
      }

      // Extract topics and generate summary from memory records
      const topics = this.extractTopicsFromRecords(records);
      const summary = this.generateSummaryFromRecords(records);

      return {
        summary,
        topics,
        messageCount: records.length,
        records
      };
    } catch (error: any) {
      console.error('Error retrieving session memory:', error);

      // Return a graceful fallback
      return {
        summary: 'Unable to retrieve session summary. The conversation history may not be available.',
        topics: [],
        messageCount: 0,
        records: []
      };
    }
  }

  /**
   * Extract topics from memory records
   */
  private extractTopicsFromRecords(records: any[]): string[] {
    const topicSet = new Set<string>();

    records.forEach(record => {
      // Extract topics from record content or metadata
      if (record.content) {
        // Look for common advertising/campaign related keywords
        const content = typeof record.content === 'string' ? record.content : JSON.stringify(record.content);
        const keywords = [
          'campaign', 'budget', 'targeting', 'audience', 'creative',
          'optimization', 'performance', 'metrics', 'ROI', 'conversion',
          'impression', 'click', 'CTR', 'CPM', 'inventory', 'publisher',
          'advertiser', 'media', 'channel', 'segment', 'bidding'
        ];

        keywords.forEach(keyword => {
          if (content.toLowerCase().includes(keyword.toLowerCase())) {
            topicSet.add(keyword.charAt(0).toUpperCase() + keyword.slice(1));
          }
        });
      }
    });

    return Array.from(topicSet).slice(0, 6); // Limit to 6 topics
  }

  /**
   * Generate a summary from memory records
   */
  private generateSummaryFromRecords(records: any[]): string {
    if (records.length === 0) {
      return 'No conversation history available.';
    }

    // Get the most recent records for summary
    const recentRecords = records.slice(0, 5);

    // Try to extract meaningful content from records
    const contentParts: string[] = [];

    recentRecords.forEach(record => {
      if (record.content) {
        const content = typeof record.content === 'string' ? record.content : '';
        if (content.length > 0 && content.length < 200) {
          contentParts.push(content);
        }
      }
    });

    if (contentParts.length > 0) {
      return `Your previous session included ${records.length} interaction${records.length > 1 ? 's' : ''}. Recent topics: ${contentParts.slice(0, 2).join('; ')}...`;
    }

    return `Your previous session included ${records.length} interaction${records.length > 1 ? 's' : ''} with the advertising agents.`;
  }

  /**
   * Get the memory ID for the current stack
   */
  getMemoryId(): string | null {
    return this.memoryRecordId || this.awsConfig.getMemoryRecordId() || null;
  }

}
