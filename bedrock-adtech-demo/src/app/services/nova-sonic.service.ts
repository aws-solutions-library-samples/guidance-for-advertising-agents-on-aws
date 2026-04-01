import { Injectable } from '@angular/core';
import { Observable, Subject } from 'rxjs';
import { AwsConfigService } from './aws-config.service';
import { AgentConfiguration } from './agent-dynamodb.service';
import {
  BedrockRuntimeClient,
  InvokeModelWithBidirectionalStreamCommand
} from '@aws-sdk/client-bedrock-runtime';

// --- Interfaces ---

export interface NovaSonicEvent {
  type: 'partial-transcript' | 'final-transcript' | 'tool-use' | 'text-response' | 'audio-response' | 'turn-complete' | 'error' | 'complete';
  text?: string;
  toolUse?: {
    toolName: string;
    toolUseId: string;
    parameters: {
      agentName: string;
      query: string;
    };
  };
  audioData?: Uint8Array;
  timestamp: Date;
}

export interface AgentToolDefinition {
  name: string;
  description: string;
  parameters: {
    agentName: { type: string; enum: string[]; descriptions: Record<string, string> };
    query: { type: string; description: string };
  };
}

// Nova Sonic event protocol types
interface NovaSonicSessionEvent {
  event: {
    [key: string]: any;
  };
}

const NOVA_SONIC_MODEL_ID = 'amazon.nova-sonic-v1:0';
const AUDIO_SAMPLE_RATE = 16000; // Input: 16kHz PCM mono
const AUDIO_OUTPUT_SAMPLE_RATE = 24000; // Output: 24kHz PCM mono
const SESSION_TIMEOUT_MS = 30000;
const AUDIO_BUFFER_SIZE = 4096;

@Injectable({ providedIn: 'root' })
export class NovaSonicService {

  private bedrockRuntimeClient: BedrockRuntimeClient | null = null;
  private clientInitialized = false;

  // Audio capture
  private audioContext: AudioContext | null = null;
  private audioStream: MediaStream | null = null;
  private scriptProcessor: ScriptProcessorNode | null = null;

  // Session state
  private sessionActive = false;
  private sessionSubject: Subject<NovaSonicEvent> | null = null;
  private timeoutTimer: any = null;

  // Bidirectional stream control
  private inputEventQueue: Array<{ chunk: { bytes: Uint8Array } }> = [];
  private inputResolve: ((value: IteratorResult<any>) => void) | null = null;
  private inputDone = false;

  // Prompt/content tracking for the event protocol
  private promptId = '';
  private systemContentId = '';
  private audioContentId = '';
  private pendingToolContentName = '';  // Track the content name for tool-use result responses
  private pendingToolUse: { toolUseId: string; agentName: string; query: string } | null = null;  // Stash tool-use until contentEnd
  private toolResultSent = false;  // True after we send a tool result back on the stream
  private awaitingPostToolResponse = false;  // True while waiting for the model's post-tool spoken response to finish

  constructor(private awsConfig: AwsConfigService) {
    this.initializeClient();
  }

  // --- Public API ---

  /**
   * Start a Nova Sonic voice session. Returns an Observable that emits NovaSonicEvents.
   * @param agentTools Tool definitions for agent routing (optional for basic transcription)
   * @param systemPrompt Custom system prompt (optional)
   */
  startSession(
    agentTools?: AgentToolDefinition[],
    systemPrompt?: string
  ): Observable<NovaSonicEvent> {
    return new Observable<NovaSonicEvent>(observer => {
      this.sessionSubject = new Subject<NovaSonicEvent>();
      this.sessionSubject.subscribe(observer);

      this.startSessionInternal(agentTools, systemPrompt).catch(err => {
        this.emitEvent({
          type: 'error',
          text: err?.message || 'Failed to start voice session',
          timestamp: new Date()
        });
        this.cleanup();
      });

      return () => {
        this.stopSession();
      };
    });
  }

  /**
   * Build an AgentToolDefinition from a list of agent cards retrieved from AgentDynamoDBService.
   * Constructs a route_to_agent tool with an enum of all agent names and their descriptions.
   * Filters out orchestrator/router agents (e.g. AdFabricAgent) so Nova Sonic itself acts as the router.
   */
  buildAgentToolDefinition(agents: AgentConfiguration[]): AgentToolDefinition {
    // Names of orchestrator/router agents that should NOT appear in the tool enum.
    // Nova Sonic replaces these — it IS the router.
    const ORCHESTRATOR_AGENTS = new Set([
      'adfabricagent',
    ]);

    // Agent cards from DynamoDB may use agent_name/agent_description or name/description
    const validAgents = agents.filter(a => {
      const name = a.agent_name || (a as any).name || '';
      const desc = a.agent_description || (a as any).description || '';
      if (!name || !desc) return false;
      // Exclude orchestrator agents
      if (ORCHESTRATOR_AGENTS.has(name.toLowerCase())) return false;
      return true;
    });

    if (validAgents.length === 0) {
      console.warn('NovaSonicService: No valid agents with name+description found for tool definition. Agent count:', agents.length);
      // Log what fields are available on the first agent for debugging
      if (agents.length > 0) {
        console.warn('NovaSonicService: First agent fields:', Object.keys(agents[0]));
        console.warn('NovaSonicService: First agent sample:', JSON.stringify(agents[0]).substring(0, 300));
      }
    } else {
      console.log(`🎯 NovaSonicService: Built tool definition with ${validAgents.length} agents:`,
        validAgents.map(a => a.agent_name || (a as any).name).join(', '));
    }

    const agentNames = validAgents.map(a => a.agent_name || (a as any).name);
    const descriptions: Record<string, string> = {};
    for (const agent of validAgents) {
      const name = agent.agent_name || (agent as any).name;
      const desc = agent.agent_description || (agent as any).description || 'Specialized agent';
      descriptions[name] = desc;
    }

    return {
      name: 'route_to_agent',
      description: 'Route the user\'s request to the most appropriate specialized agent based on their spoken query.',
      parameters: {
        agentName: {
          type: 'string',
          enum: agentNames,
          descriptions
        },
        query: {
          type: 'string',
          description: 'The user\'s full spoken request to send to the selected agent'
        }
      }
    };
  }

  /**
   * Start a Nova Sonic voice session using agent cards directly.
   * Builds the AgentToolDefinition from the agent cards and includes it in the session configuration.
   * @param agents Agent cards from AgentDynamoDBService
   * @param systemPrompt Custom system prompt (optional)
   */
  startSessionWithAgents(
    agents: AgentConfiguration[],
    systemPrompt?: string
  ): Observable<NovaSonicEvent> {
    const toolDefinition = this.buildAgentToolDefinition(agents);
    return this.startSession([toolDefinition], systemPrompt);
  }

  stopSession(): void {
    if (!this.sessionActive) return;
    this.sessionActive = false;

    // Send promptEnd and sessionEnd events to close the stream gracefully
    this.sendPromptEnd();
    this.sendSessionEnd();

    // Signal the input stream is done
    this.inputDone = true;
    if (this.inputResolve) {
      this.inputResolve({ value: undefined, done: true });
      this.inputResolve = null;
    }

    this.cleanup();
  }

  isSessionActive(): boolean {
    return this.sessionActive;
  }

  isSupported(): boolean {
    return !!(navigator.mediaDevices && typeof navigator.mediaDevices.getUserMedia === 'function' && window.AudioContext);
  }

  async requestMicrophonePermission(): Promise<boolean> {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.getTracks().forEach(t => t.stop());
      return true;
    } catch {
      return false;
    }
  }

  // --- Internal: Client Setup ---

  private async initializeClient(): Promise<void> {
    try {
      this.awsConfig.config$.subscribe(config => {
        if (config && this.awsConfig.isAuthenticated()) {
          this.setupClient();
        }
      });
    } catch (error) {
      console.error('NovaSonicService: Error initializing client:', error);
    }
  }

  private async setupClient(): Promise<void> {
    if (this.clientInitialized && this.bedrockRuntimeClient) return;

    try {
      const awsConfig = await this.awsConfig.getAwsConfig();
      if (!awsConfig?.credentials) {
        console.warn('NovaSonicService: AWS credentials not available');
        return;
      }

      // Nova Sonic is only available in us-east-1
      this.bedrockRuntimeClient = new BedrockRuntimeClient({
        region: 'us-east-1',
        credentials: awsConfig.credentials
      });
      this.clientInitialized = true;
      console.log('NovaSonicService: Client initialized with region us-east-1 (Nova Sonic only available here)');
    } catch (error) {
      console.error('NovaSonicService: Error setting up client:', error);
    }
  }

  // --- Internal: Session Lifecycle ---

  private async startSessionInternal(
    agentTools?: AgentToolDefinition[],
    systemPrompt?: string
  ): Promise<void> {
    if (!this.clientInitialized || !this.bedrockRuntimeClient) {
      await this.setupClient();
    }
    if (!this.bedrockRuntimeClient) {
      throw new Error('Bedrock Runtime client not initialized. Please sign in.');
    }

    // Request microphone
    this.audioStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        sampleRate: AUDIO_SAMPLE_RATE,
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true
      }
    });

    this.sessionActive = true;
    this.inputEventQueue = [];
    this.inputDone = false;
    this.inputResolve = null;

    // Generate IDs for the session protocol
    this.promptId = this.generateId('prompt');
    this.systemContentId = this.generateId('system-content');
    this.audioContentId = this.generateId('audio-content');

    // Build the system prompt with tool definitions
    const fullSystemPrompt = this.buildSystemPrompt(systemPrompt, agentTools);
    console.log('📝 NovaSonicService: System prompt length:', fullSystemPrompt.length, '| Content:', fullSystemPrompt.substring(0, 200));

    // Queue the initial protocol events: sessionStart → promptStart → system content → audio content start
    this.sendSessionStart();
    this.sendPromptStart(agentTools);
    this.sendSystemContent(fullSystemPrompt);
    this.sendAudioContentStart();

    // Start audio capture — this will queue audioInput events
    this.startAudioCapture();

    // Start the timeout timer
    this.resetTimeout();

    // Create the bidirectional stream
    const inputStream = this.createInputStream();

    const command = new InvokeModelWithBidirectionalStreamCommand({
      modelId: NOVA_SONIC_MODEL_ID,
      body: inputStream
    });

    try {
      const response = await this.bedrockRuntimeClient.send(command);

      // Process output events
      if (response.body) {
        for await (const event of response.body) {
          if (!this.sessionActive && !this.inputDone) break;

          if (event.chunk?.bytes) {
            this.handleOutputEvent(event.chunk.bytes);
          } else if (event.internalServerException) {
            this.emitEvent({ type: 'error', text: 'Server error: ' + event.internalServerException.message, timestamp: new Date() });
          } else if (event.modelStreamErrorException) {
            this.emitEvent({ type: 'error', text: 'Stream error: ' + event.modelStreamErrorException.message, timestamp: new Date() });
          } else if (event.validationException) {
            this.emitEvent({ type: 'error', text: 'Validation error: ' + event.validationException.message, timestamp: new Date() });
          } else if (event.throttlingException) {
            this.emitEvent({ type: 'error', text: 'Throttled. Please try again later.', timestamp: new Date() });
          } else if (event.modelTimeoutException) {
            this.emitEvent({ type: 'error', text: 'Model timed out. Please try again.', timestamp: new Date() });
          }
        }
      }

      // Session completed normally
      if (this.sessionActive) {
        this.emitEvent({ type: 'complete', text: 'Voice session ended', timestamp: new Date() });
        this.cleanup();
      }
    } catch (error: any) {
      const msg = error?.message || 'Voice connection failed';
      if (msg.includes('ExpiredToken') || msg.includes('security token')) {
        this.emitEvent({ type: 'error', text: 'Session expired. Please refresh and sign in again.', timestamp: new Date() });
      } else {
        this.emitEvent({ type: 'error', text: 'Voice connection failed. Please try again.', timestamp: new Date() });
      }
      console.error('NovaSonicService: Stream error:', error);
      this.cleanup();
    }
  }

  // --- Internal: Nova Sonic Event Protocol ---

  private sendSessionStart(): void {
    const event = {
      event: {
        sessionStart: {
          inferenceConfiguration: {
            maxTokens: 1024,
            topP: 0.9,
            temperature: 0.7
          }
        }
      }
    };
    this.enqueueInputEvent(event);
  }

  private sendPromptStart(agentTools?: AgentToolDefinition[]): void {
    const event: any = {
      event: {
        promptStart: {
          promptName: this.promptId,
          textOutputConfiguration: { mediaType: 'text/plain' },
          audioOutputConfiguration: {
            mediaType: 'audio/lpcm',
            sampleRateHertz: AUDIO_OUTPUT_SAMPLE_RATE,
            sampleSizeBits: 16,
            channelCount: 1,
            voiceId: 'tiffany'
          },
          // Required for Nova Sonic to send tool results back as JSON
          toolUseOutputConfiguration: {
            mediaType: 'application/json'
          }
        }
      }
    };

    // Add tool configuration if agent tools are provided
    // Nova Sonic expects `toolConfiguration.tools` (not `toolUseConfiguration`)
    // and inputSchema.json must be a JSON-stringified string, not a raw object.
    if (agentTools && agentTools.length > 0) {
      const tools = this.buildToolConfig(agentTools);
      console.log(`🔧 NovaSonicService: Sending ${tools.length} tool(s) in promptStart:`, JSON.stringify(tools.map(t => t.toolSpec?.name)));
      event.event.promptStart.toolConfiguration = {
        tools
      };
    } else {
      console.warn('🔧 NovaSonicService: No agent tools provided — Nova Sonic will respond with text only (no routing)');
    }

    this.enqueueInputEvent(event);
  }

  private sendSystemContent(systemPrompt: string): void {
    // contentStart for system text
    this.enqueueInputEvent({
      event: {
        contentStart: {
          promptName: this.promptId,
          contentName: this.systemContentId,
          type: 'TEXT',
          role: 'SYSTEM',
          textInputConfiguration: { mediaType: 'text/plain' }
        }
      }
    });

    // textInput with the system prompt
    this.enqueueInputEvent({
      event: {
        textInput: {
          promptName: this.promptId,
          contentName: this.systemContentId,
          content: systemPrompt
        }
      }
    });

    // contentEnd for system text
    this.enqueueInputEvent({
      event: {
        contentEnd: {
          promptName: this.promptId,
          contentName: this.systemContentId
        }
      }
    });
  }

  private sendAudioContentStart(): void {
    this.enqueueInputEvent({
      event: {
        contentStart: {
          promptName: this.promptId,
          contentName: this.audioContentId,
          type: 'AUDIO',
          role: 'USER',
          audioInputConfiguration: {
            mediaType: 'audio/lpcm',
            sampleRateHertz: AUDIO_SAMPLE_RATE,
            sampleSizeBits: 16,
            channelCount: 1,
            audioType: 'SPEECH',
            encoding: 'base64'
          }
        }
      }
    });
  }

  private sendAudioChunk(pcmData: Uint8Array): void {
    if (!this.sessionActive) return;

    // Base64 encode the PCM data
    const base64Audio = this.uint8ArrayToBase64(pcmData);

    this.enqueueInputEvent({
      event: {
        audioInput: {
          promptName: this.promptId,
          contentName: this.audioContentId,
          content: base64Audio
        }
      }
    });

    this.resetTimeout();
  }

  private sendAudioContentEnd(): void {
    this.enqueueInputEvent({
      event: {
        contentEnd: {
          promptName: this.promptId,
          contentName: this.audioContentId
        }
      }
    });
  }

  private sendPromptEnd(): void {
    this.enqueueInputEvent({
      event: {
        promptEnd: {
          promptName: this.promptId
        }
      }
    });
  }

  private sendSessionEnd(): void {
    this.enqueueInputEvent({
      event: {
        sessionEnd: {}
      }
    });
  }

  // --- Internal: Input Stream (async iterable for bidirectional streaming) ---

  private createInputStream(): AsyncIterable<{ chunk: { bytes: Uint8Array } }> {
    const self = this;
    return {
      [Symbol.asyncIterator]() {
        return {
          next(): Promise<IteratorResult<{ chunk: { bytes: Uint8Array } }>> {
            // If there are queued events, return the next one
            if (self.inputEventQueue.length > 0) {
              return Promise.resolve({ value: self.inputEventQueue.shift()!, done: false });
            }

            // If the stream is done, signal completion
            if (self.inputDone) {
              return Promise.resolve({ value: undefined as any, done: true });
            }

            // Wait for the next event to be enqueued
            return new Promise<IteratorResult<{ chunk: { bytes: Uint8Array } }>>(resolve => {
              self.inputResolve = resolve;
            });
          }
        };
      }
    };
  }

  private enqueueInputEvent(event: any): void {
    const bytes = new TextEncoder().encode(JSON.stringify(event));
    const wrapped = { chunk: { bytes } };

    if (this.inputResolve) {
      const resolve = this.inputResolve;
      this.inputResolve = null;
      resolve({ value: wrapped, done: false });
    } else {
      this.inputEventQueue.push(wrapped);
    }
  }

  // --- Internal: Output Event Handling ---

  private handleOutputEvent(bytes: Uint8Array): void {
    try {
      const text = new TextDecoder().decode(bytes);
      const event = JSON.parse(text);

      if (event.event) {
        const evt = event.event;
        // Log all event keys for debugging the protocol flow
        console.log('🔍 NovaSonicService: Output event keys:', Object.keys(evt).join(', '));

        // Text output (transcription or text response)
        if (evt.textOutput) {
          const content = evt.textOutput.content || '';
          const role = evt.textOutput.role;

          if (role === 'USER') {
            // This is the user's transcribed speech
            this.emitEvent({ type: 'final-transcript', text: content, timestamp: new Date() });
          } else if (role === 'ASSISTANT') {
            // This is the model's text response
            this.emitEvent({ type: 'text-response', text: content, timestamp: new Date() });
            // If we already processed a tool-use cycle, the model is now giving its
            // spoken acknowledgement. Mark that we're waiting for this response to finish.
            if (this.toolResultSent) {
              this.awaitingPostToolResponse = true;
            }
          }
        }

        // Audio output (model speaking back)
        if (evt.audioOutput) {
          const audioContent = evt.audioOutput.content;
          if (audioContent) {
            const audioBytes = this.base64ToUint8Array(audioContent);
            this.emitEvent({ type: 'audio-response', audioData: audioBytes, timestamp: new Date() });
          }
        }

        // Tool use event — Nova Sonic sends this when it wants to call a tool
        if (evt.toolUse) {
          console.log('🔧 NovaSonicService: Received toolUse event:', JSON.stringify(evt.toolUse).substring(0, 200));
          this.handleToolUseEvent(evt.toolUse);
        }

        // Content start — track tool content blocks
        if (evt.contentStart) {
          const contentType = evt.contentStart.type;
          if (contentType === 'TOOL') {
            this.pendingToolContentName = evt.contentStart.contentName || evt.contentStart.contentId || '';
            console.log('🔧 NovaSonicService: Tool content block started:', this.pendingToolContentName);
          }
        }

        // Content end — when a TOOL content block ends, send back the tool result
        if (evt.contentEnd) {
          const hasToolStopReason = evt.contentEnd.stopReason === 'TOOL_USE';
          const isToolContentBlock = this.pendingToolContentName &&
            (evt.contentEnd.contentName === this.pendingToolContentName || evt.contentEnd.contentId === this.pendingToolContentName);

          if ((hasToolStopReason || isToolContentBlock) && this.pendingToolUse) {
            console.log(`🔧 NovaSonicService: Tool content block ended (stopReason=${evt.contentEnd.stopReason}), NOW sending tool result`);
            const { toolUseId, agentName, query } = this.pendingToolUse;
            this.sendToolResult(toolUseId, agentName, query);
            this.pendingToolUse = null;
            this.toolResultSent = true;
          }

          // Detect end of the post-tool-result response.
          // After we send a tool result, the model responds with text + audio.
          // When the audio (or text) content block ends and it's NOT a tool block,
          // that means the model's spoken acknowledgement is done — emit turn-complete
          // and end the session so the chat interface can route to the agent.
          if (this.awaitingPostToolResponse && !isToolContentBlock && !hasToolStopReason) {
            const stopReason = evt.contentEnd.stopReason;
            // END_TURN or no stopReason on a non-tool contentEnd after tool result = model is done
            if (stopReason === 'END_TURN' || !this.pendingToolContentName) {
              console.log('🔧 NovaSonicService: Post-tool response complete (contentEnd stopReason=' + stopReason + ') — emitting turn-complete');
              this.awaitingPostToolResponse = false;
              this.toolResultSent = false;
              this.emitEvent({ type: 'turn-complete', text: '', timestamp: new Date() });
            }
          }
        }

        if (evt.completionEnd) {
          console.log('🔧 NovaSonicService: completionEnd received — model turn complete');
          this.emitEvent({ type: 'turn-complete', text: '', timestamp: new Date() });
        }
      }
    } catch (error) {
      // Some events may not be JSON — skip silently
      console.debug('NovaSonicService: Non-JSON output event, skipping');
    }
  }

  private handleToolUseEvent(toolUse: any): void {
    try {
      const toolName = toolUse.toolName || toolUse.name || '';
      const toolUseId = toolUse.toolUseId || '';
      let parameters: any = {};

      // Nova Sonic sends tool parameters as a JSON string in the "content" field
      if (typeof toolUse.content === 'string') {
        try {
          parameters = JSON.parse(toolUse.content);
        } catch {
          parameters = { raw: toolUse.content };
        }
      } else if (typeof toolUse.content === 'object' && toolUse.content !== null) {
        parameters = toolUse.content;
      } else if (toolUse.input) {
        parameters = typeof toolUse.input === 'string' ? JSON.parse(toolUse.input) : toolUse.input;
      } else if (toolUse.parameters) {
        parameters = toolUse.parameters;
      }

      const agentName = parameters.agentName || parameters.agent_name || '';
      const query = parameters.query || parameters.user_query || '';

      console.log(`🎯 NovaSonicService: Tool use detected — tool: ${toolName}, toolUseId: ${toolUseId}, agent: ${agentName}, query: "${query.substring(0, 80)}..."`);

      // Stash the tool use info — we'll send the tool result when contentEnd arrives
      // with stopReason: 'TOOL_USE'. Nova Sonic requires the full content block to close
      // before we send the result back on the input stream.
      this.pendingToolUse = { toolUseId, agentName, query };

      this.emitEvent({
        type: 'tool-use',
        text: query,
        toolUse: {
          toolName,
          toolUseId,
          parameters: { agentName, query }
        },
        timestamp: new Date()
      });
    } catch (error) {
      console.error('NovaSonicService: Error parsing tool use event:', error, toolUse);
    }
  }

  /**
   * Send a tool result back on the bidirectional stream so the model can continue
   * and produce a spoken response after deciding to use a tool.
   */
  private sendToolResult(toolUseId: string, agentName: string, query: string): void {
    const toolResultContentName = this.generateId('tool-result');
    const resultPayload = JSON.stringify({
      status: 'routed',
      agentName,
      message: `Request routed to ${agentName}.`
    });

    console.log(`🔧 NovaSonicService: Sending tool result — contentName=${toolResultContentName}, toolUseId=${toolUseId}`);

    // 1. contentStart for TOOL result (type must be "TOOL", not "TOOL_RESULT")
    this.enqueueInputEvent({
      event: {
        contentStart: {
          promptName: this.promptId,
          contentName: toolResultContentName,
          interactive: false,
          type: 'TOOL',
          role: 'TOOL',
          toolResultInputConfiguration: {
            toolUseId: toolUseId,
            type: 'TEXT',
            textInputConfiguration: { mediaType: 'text/plain' }
          }
        }
      }
    });

    // 2. toolResult event (NOT textInput) with the result content
    this.enqueueInputEvent({
      event: {
        toolResult: {
          promptName: this.promptId,
          contentName: toolResultContentName,
          content: resultPayload
        }
      }
    });

    // 3. contentEnd to close the tool result block
    this.enqueueInputEvent({
      event: {
        contentEnd: {
          promptName: this.promptId,
          contentName: toolResultContentName
        }
      }
    });

    // Reset the pending tool content name
    this.pendingToolContentName = '';
  }

  // --- Internal: Audio Capture ---

  private startAudioCapture(): void {
    if (!this.audioStream) return;

    this.audioContext = new AudioContext({ sampleRate: AUDIO_SAMPLE_RATE });
    const source = this.audioContext.createMediaStreamSource(this.audioStream);
    this.scriptProcessor = this.audioContext.createScriptProcessor(AUDIO_BUFFER_SIZE, 1, 1);

    this.scriptProcessor.onaudioprocess = (event: AudioProcessingEvent) => {
      if (!this.sessionActive) return;

      const inputData = event.inputBuffer.getChannelData(0);

      // Convert float32 to 16-bit PCM
      const pcmData = new Int16Array(inputData.length);
      for (let i = 0; i < inputData.length; i++) {
        const sample = Math.max(-1, Math.min(1, inputData[i]));
        pcmData[i] = sample < 0 ? sample * 0x8000 : sample * 0x7FFF;
      }

      this.sendAudioChunk(new Uint8Array(pcmData.buffer));
    };

    source.connect(this.scriptProcessor);
    this.scriptProcessor.connect(this.audioContext.destination);
  }

  // --- Internal: System Prompt & Tool Config ---

  private buildSystemPrompt(customPrompt?: string, agentTools?: AgentToolDefinition[]): string {
    const defaultPrompt = 'You are a helpful voice assistant for an advertising technology platform. You help users by routing their requests to the best specialist agent.';

    const routingInstructions = agentTools && agentTools.length > 0
      ? '\n\nYou are the voice router for this platform. Your job is to understand the user\'s request and route it to the most appropriate specialist agent using the "route_to_agent" tool. ' +
        'Listen carefully to what the user is asking about, then select the single best agent for their request. ' +
        'Before routing, briefly tell the user which agent you\'re sending them to and why (one sentence). ' +
        'If the user\'s request is vague or could match multiple agents, ask the user a short clarifying question to narrow it down — do NOT guess. ' +
        'Never route to an orchestrator or fabric agent. Only route to specialist agents that directly handle the user\'s topic. ' +
        'Always call the route_to_agent tool once you know the right agent — never try to answer the question yourself.'
      : '';

    return (customPrompt || defaultPrompt) + routingInstructions;
  }

  private buildToolConfig(agentTools: AgentToolDefinition[]): any[] {
    if (!agentTools || agentTools.length === 0) return [];

    // Build the enum of agent names and their descriptions
    const agentNames = agentTools.flatMap(t => t.parameters.agentName.enum);
    const agentDescriptions = agentTools.reduce((acc, t) => {
      return { ...acc, ...t.parameters.agentName.descriptions };
    }, {} as Record<string, string>);

    // Build a description string that includes each agent's purpose
    const agentListDescription = agentNames
      .map(name => `- ${name}: ${agentDescriptions[name] || 'No description available'}`)
      .join('\n');

    // Nova Sonic requires inputSchema.json to be a JSON-stringified string,
    // NOT a raw object. This matches the working sonic-client.html pattern.
    return [{
      toolSpec: {
        name: 'route_to_agent',
        description: `Route the user's request to the most appropriate specialized agent. Available agents:\n${agentListDescription}`,
        inputSchema: {
          json: JSON.stringify({
            type: 'object',
            properties: {
              agentName: {
                type: 'string',
                enum: agentNames,
                description: 'The name of the agent to route the request to'
              },
              query: {
                type: 'string',
                description: 'The user\'s full spoken request to send to the selected agent'
              }
            },
            required: ['agentName', 'query']
          })
        }
      }
    }];
  }

  // --- Internal: Helpers ---

  private emitEvent(event: NovaSonicEvent): void {
    if (this.sessionSubject && !this.sessionSubject.closed) {
      this.sessionSubject.next(event);
    }
  }

  private cleanup(): void {
    this.sessionActive = false;

    // Clear pending tool use state
    this.pendingToolUse = null;
    this.pendingToolContentName = '';
    this.toolResultSent = false;
    this.awaitingPostToolResponse = false;

    // Clear timeout
    if (this.timeoutTimer) {
      clearTimeout(this.timeoutTimer);
      this.timeoutTimer = null;
    }

    // Stop audio stream tracks
    if (this.audioStream) {
      this.audioStream.getTracks().forEach(track => track.stop());
      this.audioStream = null;
    }

    // Disconnect and close audio context
    if (this.scriptProcessor) {
      try { this.scriptProcessor.disconnect(); } catch {}
      this.scriptProcessor = null;
    }

    if (this.audioContext) {
      this.audioContext.close().catch(() => {});
      this.audioContext = null;
    }

    // Complete the subject
    if (this.sessionSubject && !this.sessionSubject.closed) {
      this.sessionSubject.complete();
    }
    this.sessionSubject = null;

    // Clear input stream
    this.inputEventQueue = [];
    this.inputDone = true;
    if (this.inputResolve) {
      this.inputResolve({ value: undefined as any, done: true });
      this.inputResolve = null;
    }
  }

  private resetTimeout(): void {
    if (this.timeoutTimer) {
      clearTimeout(this.timeoutTimer);
    }
    this.timeoutTimer = setTimeout(() => {
      if (this.sessionActive) {
        this.emitEvent({
          type: 'error',
          text: 'Voice session timed out due to inactivity. Please try again.',
          timestamp: new Date()
        });
        this.stopSession();
      }
    }, SESSION_TIMEOUT_MS);
  }

  private generateId(prefix: string): string {
    return `${prefix}-${Date.now()}-${Math.random().toString(36).substring(2, 8)}`;
  }

  private uint8ArrayToBase64(bytes: Uint8Array): string {
    let binary = '';
    for (let i = 0; i < bytes.length; i++) {
      binary += String.fromCharCode(bytes[i]);
    }
    return btoa(binary);
  }

  private base64ToUint8Array(base64: string): Uint8Array {
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    return bytes;
  }
}
