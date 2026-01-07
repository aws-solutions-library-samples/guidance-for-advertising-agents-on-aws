export interface Message {
    id: string;
    text: string;
    sender: 'user' | 'agent';
    timestamp: Date;
    type?: 'text' | 'data' | 'chart'| 'passthrough';
    data?: any;
    agentName?: string; // Add agent name for group thread display
    displayName?: string; // Friendly display name (e.g., "Bid Optimization Agent")
    attachedFiles?: AttachedFile[]; // Add support for attached files
    sources?: { [agentName: string]: KnowledgeBaseSource[] } | KnowledgeBaseSource[]; // Sources organized by agent name or flat array
    agent?:EnrichedAgent
}

export interface AwsConfig {
  aws: {
    region: string;
    cognito: {
      userPoolId: string;
      userPoolWebClientId: string;
      identityPoolId: string;
      mandatorySignIn: boolean;
    };
  };
  bedrock: {
    allAgents: Array<{
      name: string;
      agentType: string;
      status: string;
      id: string;
      aliasId: string;
      displayName: string;
      icon: string;
      color: string;
      deploymentType: string;
      serviceName: string;
      runtimeArn: string;
      runtimeId: string;
      
    }>;
    stackPrefix: string;
    stackSuffix: string;
    creativesDynamoDBTable: string;
  };
  ui?: {
    bucketName: string;
    cloudFrontDistributionId: string;
  };
  appSyncEvents?: {
    apiId: string;
    eventsEndpoint: string;
    realtimeEndpoint: string;
  };
  appSyncEndpoint?: string;
  appSyncApiId?: string;
  appSyncRealtimeEndpoint?: string;
  appSyncChannelNamespace?: string;
  memoryRecordId: string;

  appConfig?: AppConfigSettings;
  stackPrefix: string;
  stackSuffix: string; // Optional since it has a default value
  uniqueId: string;
  creativesBucket: string;
  creativesDynamoDBTable: string;
  demoLogGroupName?: string;
}

export interface AgentConfig {
  agentType: string;
  displayName: string;
  color: string;
  icon: string;
  alternativeNames?: string[];
  description?: string;
}

export interface AgentsConfiguration {
  agents: { [key: string]: AgentConfig };
  defaultAgent: AgentConfig;
}

export interface AppConfigSettings {
  applicationId: string;
  environmentId: string;
  region: string;
  profiles: {
    agents: string;
    tabs: string;
    uiSettings: string;
  };
}


export interface StreamEvent {
  type: string;//'chunk' | 'trace' | 'error' | 'complete' | 'creative-visualization';
  data: any;
  timestamp: Date;
  agentName?: string;
  teamName?:string;
  sources?:any;
  messageType?: 'rationale' | 'supervisor-to-collaborator' | 'collaborator-response' | 'final-response' | 'raw-response' | 'knowledge-base-sources' | 'knowledge-base-query' | 'streaming-chunk' | 'tool-trace' | 'visualization-data' | string;
  metadata?: {
    type?: 'reasoning' | 'tool-agent' | 'tool-result' | 'response'|string;
    originalAgentName?: string;
    toolUseId?: string;
    name:string;
    [key: string]: any;
  };
}

export interface CacheEntry {
  data: any;
  timestamp: number;
  ttl: number;
  sessionToken?: string;
}


export interface KnowledgeBaseSource {
    output:any;
    content: {
        text: string;
        type?: string;
    };
    location: {
        s3Location?: {
            uri: string;
        };
        type?: string;
    };
    metadata?: {
        [key: string]: string;
    };
    searchQuery?: string; // The query that retrieved this source
    traceId?: string; // Link to the trace that generated this source
    agent?: string; // The AgentCore tool that retrieved this source
    formatted_summary?: any;
    citationText?: string; // The text from the response that cited this source (retrieve_and_generate format)
}

export interface AttachedFile {
    name: string;
    size: number;
    type: string;
    base64Content: string;
    mediaType: string;
}

export interface AgentParticipant {
    name: string;
    displayName: string;
    lastActivity: Date;
    messageCount: number;
    agent: EnrichedAgent|null;
}

export interface ScenarioExample {
    title: string;
    description: string;
    query: string;
    category: string;
    agentObject:EnrichedAgent|null;
    agent:string;
    agentType: string;
}


export interface AgentMention {
  agentKey: string;
  displayName: string;
  startIndex: number;
  endIndex: number;
  originalText: string;
  agent:EnrichedAgent;
}

export interface ParsedMessage {
  cleanedText: string;
  mentionedAgent: EnrichedAgent | null;
  mentions: AgentMention[];
}

export interface AgentSuggestion {
  key: string;
  displayName: string;
  description: string;
  agentType: string;
  color: string;
  icon: string;
  agent:EnrichedAgent;
}

export interface DeployedAgent {
  name: string;
  agentType: string;
  status: string;
  id: string;
  aliasId: string | undefined;
  displayName: string | undefined;
  description?: string;
  deploymentType?: string;
  runtimeId?: string;
  runtimeArn?: string;
  runtimeName?: string;
  color?: string;
  icon?: string;
  role?: string;
  alternativeNames?: string[]
}

export interface EnrichedAgent {
  // From deployment (aws-config.json)
  name: string;
  agentType: string;
  status: string;
  id: string;
  aliasId: string | undefined; // Optional for AgentCore agents
  deploymentType?: string;

  // AgentCore specific fields
  runtimeId?: string;
  runtimeArn?: string;
  runtimeName?: string;

  // From agents-config.json + computed
  displayName: string;
  color: string;
  icon: string;
  alternativeNames: string[];
  description: string;

  // Computed properties
  key: string; // normalized key for lookups

  // Session management
  sessionId?: string; // Agent-specific session ID for conversations

  // Team organization
  teamName?: string; // Team this agent belongs to
  orchestratorAgent?: string; // For collaborators, which agent orchestrates them
}

export interface TabConfiguration {
  id: string;
  title: string;
  description: string;
  icon: string;
  defaultAgent: string;
  availableAgents: string[];
  scenarios?: any[];
  visualizations?: any[];
  contextData?: any;
  contextButtonLabel?: string; // Label for the context trigger button
  // Additional properties for backward compatibility
  availableCampaigns?: any[];
  agentType?: string; // Keep for backward compatibility
}

export interface TabsConfiguration {
  tabConfigurations: { [key: string]: TabConfiguration };
}
