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
  type: 'partial-transcript' | 'final-transcript' | 'tool-use' | 'text-response' | 'audio-response' | 'error' | 'complete';
  text?: string;
  toolUse?: {
    toolName: string;
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
   */
  buildAgentToolDefinition(agents: AgentConfiguration[]): AgentToolDefinition {
    const validAgents = agents.filter(a => a.agent_name && a.agent_description);

    const agentNames = validAgents.map(a => a.agent_name);
    const descriptions: Record<string, string> = {};
    for (const agent of validAgents) {
      descriptions[agent.agent_name] = agent.agent_description;
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

      this.bedrockRuntimeClient = new BedrockRuntimeClient({
        region: awsConfig.region,
        credentials: awsConfig.credentials
      });
      this.clientInitialized = true;
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
          }
        }
      }
    };

    // Add tool configuration if agent tools are provided
    if (agentTools && agentTools.length > 0) {
      event.event.promptStart.toolUseConfiguration = {
        toolChoice: 'auto',
        tools: this.buildToolConfig(agentTools)
      };
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

        // Tool use event
        if (evt.toolUse) {
          this.handleToolUseEvent(evt.toolUse);
        }

        // Content start/end events (for tracking partial transcripts)
        if (evt.contentStart) {
          // Could track content blocks here if needed
        }

        if (evt.completionEnd || evt.promptEnd) {
          // Prompt cycle complete
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
      let parameters: any = {};

      if (typeof toolUse.content === 'string') {
        try {
          parameters = JSON.parse(toolUse.content);
        } catch {
          parameters = { raw: toolUse.content };
        }
      } else if (toolUse.input) {
        parameters = toolUse.input;
      } else if (toolUse.parameters) {
        parameters = toolUse.parameters;
      }

      const agentName = parameters.agentName || parameters.agent_name || '';
      const query = parameters.query || parameters.user_query || '';

      this.emitEvent({
        type: 'tool-use',
        text: query,
        toolUse: {
          toolName,
          parameters: { agentName, query }
        },
        timestamp: new Date()
      });
    } catch (error) {
      console.error('NovaSonicService: Error parsing tool use event:', error);
    }
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
    const defaultPrompt = `You are a helpful voice assistant for an advertising technology platform. 
Listen to the user's spoken request and determine the best course of action.`;

    const routingInstructions = agentTools && agentTools.length > 0
      ? `\n\nYou have access to a tool called "route_to_agent" that lets you route the user's request to a specialized agent. 
When the user describes a task, analyze their request and use the route_to_agent tool to select the most appropriate agent. 
Pass the user's full spoken request as the "query" parameter.
If you cannot determine which agent to use, respond with a helpful text message asking for clarification.`
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

    return [{
      toolSpec: {
        name: 'route_to_agent',
        description: `Route the user's request to the most appropriate specialized agent. Available agents:\n${agentListDescription}`,
        inputSchema: {
          json: {
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
          }
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
