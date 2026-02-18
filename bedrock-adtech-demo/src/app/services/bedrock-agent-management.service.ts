import { Injectable } from '@angular/core';
import { Observable, from, throwError } from 'rxjs';
import { map, catchError } from 'rxjs/operators';
import { AwsConfigService } from './aws-config.service';
import { AgentConfigService } from './agent-config.service';
import { TextUtils } from '../utils/text-utils';
import { PrepareAgentCommand, GetAgentCommand, GetAgentResponse, Agent, UpdateAgentCommand, UpdateAgentAliasCommand, AssociateAgentCollaboratorCommand, AssociateAgentKnowledgeBaseCommand, BedrockAgentClient, ListAgentKnowledgeBasesCommand, ListAgentCollaboratorsCommand, DisassociateAgentKnowledgeBaseCommand, ListKnowledgeBasesCommand, GetAgentAliasCommand, ListAgentAliasesCommand, ListAgentAliasesResponse } from '@aws-sdk/client-bedrock-agent';
import { EnrichedAgent } from '../models/application-models';

export interface AgentNode {
  id: string;
  name: string;
  displayName: string;
  role: 'supervisor' | 'collaborator';
  agentType?: string; // New field to distinguish agent types
  status: string;
  aliasId?: string;
  instructions?: string;
  knowledgeBases: KnowledgeBaseAssociation[];
  collaborators?: string[]; // For supervisor agents
  supervisor?: string; // For collaborator agents
  runtimeId?: string; // For AgentCore agents
  containerUri?: string; // For AgentCore agents
  runtimeArn?: string; // For AgentCore agents
  runtimeName?: string; // For AgentCore agents
  x?: number;
  y?: number;
}

export interface KnowledgeBaseAssociation {
  knowledgeBaseId: string;
  name: string;
  description: string;
  state: 'ENABLED' | 'DISABLED';
}

export interface AgentUpdateRequest {
  agentId: string;
  instructions?: string;
  knowledgeBaseAssociations?: {
    add?: string[];
    remove?: string[];
  };
}

@Injectable({
  providedIn: 'root'
})
export class BedrockAgentManagementService {
  private bedrockClient: any;
  private isInitialized = false;
  enrichedAgents: EnrichedAgent[] | undefined;

  constructor(
    private awsConfig: AwsConfigService,
    private agentConfig: AgentConfigService
  ) {
    this.initializeClient();
  }

  private async initializeClient(): Promise<void> {
    if (this.isInitialized) return;

    try {
      const config = await this.awsConfig.getAwsConfig();
      if (!config) {
        throw new Error('AWS configuration not available');
      }

      // Import AWS SDK v3 modules dynamically

      // Use the credentials directly from the config (already from Cognito)
      this.bedrockClient = new BedrockAgentClient({
        region: config.region,
        credentials: config.credentials
      });

      this.isInitialized = true;
    } catch (error) {
      console.error('Failed to initialize Bedrock client:', error);
      throw error;
    }
  }

  /**
   * Get all agents with their relationships and knowledge base associations
   */
  getAgentNodes(): Observable<AgentNode[]> {
    return from(this.loadAgentNodes()).pipe(
      catchError(error => {
        console.error('Error loading agent nodes:', error);
        return throwError(() => error);
      })
    );
  }

  private async loadAgentNodes(): Promise<AgentNode[]> {
    try {
      await this.initializeClient();

      // Get enriched agents from the agent config service
      this.enrichedAgents = await this.agentConfig.getEnrichedAgents()
      if (!this.enrichedAgents || !Array.isArray(this.enrichedAgents)) {
        console.warn('No enriched agents found, creating mock data for development');
        return [];
      }

      const agentNodes: AgentNode[] = [];

      for (const agent of this.enrichedAgents) {
        try {

          let node: AgentNode = {
            id: agent.id,
            name: agent.name,
            displayName: agent.displayName, // Use the proper display name from enriched agent
            role: 'supervisor',
            agentType: 'bedrock', // Default to bedrock for existing agents
            status: agent.status,
            aliasId: agent.aliasId,
            collaborators: [],
            knowledgeBases: [],
            instructions: ''
          };

          if (agent.deploymentType == 'bedrock') {
            const agentDetails = await this.getAgentDetails(agent.id);
            const knowledgeBases = await this.getAgentKnowledgeBases(agent.id);
            const collaborators = await this.getAgentCollaborators(agent.id);
            // Store both collaborator names and IDs for better matching
            node.collaborators = collaborators.map(c => c.collaboratorName);
            // Also store the full collaborator data for better matching
            (node as any).collaboratorData = collaborators;
            node.instructions = agentDetails?.instruction || '';
            node.role = collaborators.length > 0 ? 'supervisor' : 'collaborator';
            node.knowledgeBases = knowledgeBases
          }
          agentNodes.push(node);
        } catch (agentError) {
          console.warn(`Failed to load details for agent ${agent.name}:`, agentError);
          // Add basic node without details
          const basicNode: AgentNode = {
            id: agent.id,
            name: agent.name,
            displayName: TextUtils.pascalOrCamelToDisplayName(TextUtils.removeStackPrefixSuffix(agent.displayName,this.awsConfig.getStackPrefix(),this.awsConfig.getStackSuffix())),
            role: 'collaborator',
            agentType: agent.deploymentType, // Default to bedrock for existing agents
            status: agent.status,
            aliasId: agent?.aliasId || '',
            instructions: '',
            knowledgeBases: [],
            collaborators: []
          };
          agentNodes.push(basicNode);
        }
      }

      // Set supervisor relationships for collaborators
      this.setCollaboratorSupervisors(agentNodes);

      return agentNodes.length > 0 ? agentNodes : [];
    } catch (error) {
      console.error('Error in loadAgentNodes, falling back to mock data:', error);
      return [];
    }
  }

  private async loadAgentCoreNodes(): Promise<AgentNode[]> {
    try {
      // Try to load AgentCore agents from deployment info file
      const agentCoreNodes: AgentNode[] = [];

      // Get AgentCore agents from the main aws-config.json
      try {
        const awsConfig = this.awsConfig.getConfig();
        if (awsConfig?.bedrock?.allAgents) {
          const agentCoreAgents = awsConfig.bedrock.allAgents.filter(agent =>
            agent.deploymentType === 'agentcore'
          );

          for (const agent of agentCoreAgents) {
            const node: AgentNode = {
              id: agent.id || agent.runtimeArn || `agentcore-${agent.name}`,
              aliasId:agent.aliasId,
              name: agent.name,
              displayName: agent.displayName || agent.displayName || this.formatDisplayName(agent.name),
              role: 'collaborator', // AgentCore agents are typically collaborators
              agentType: 'agentcore',
              status: agent.status === 'active' ? 'ACTIVE' : 'INACTIVE',
              instructions: `AgentCore agent for ${agent.name}`,
              knowledgeBases: [],
              runtimeId: agent.runtimeId || `runtime-${agent.name}`,
              runtimeArn: agent.runtimeArn
            };
            agentCoreNodes.push(node);
          }
        }
      } catch (fetchError) {
        console.warn('Could not load AgentCore agents from aws-config.json:', fetchError);

        // Fallback: try to detect AgentCore agents from config
        // This is a simplified approach - in production you'd call the AgentCore API
        const knownAgentCoreAgents = ['campaign-optimizer', 'analytics-processor', 'coordinator'];

        for (const agentName of knownAgentCoreAgents) {
          const node: AgentNode = {
            id: `agentcore-${agentName}`,
            name: agentName,
            displayName: this.formatDisplayName(agentName),
            role: 'collaborator',
            agentType: 'agentcore',
            status: 'UNKNOWN', // We don't know the actual status without API call
            aliasId: `agentcore-${agentName}-alias`,
            instructions: `AgentCore agent for ${agentName}`,
            knowledgeBases: [],
            runtimeId: `runtime-${agentName}`,
            containerUri: `${agentName}:latest`
          };
          agentCoreNodes.push(node);
        }
      }

      return agentCoreNodes;
    } catch (error) {
      console.error('Error loading AgentCore agents:', error);
      return [];
    }
  }

  private formatDisplayName(agentName: string): string {
    return agentName
      .split('-')
      .map(word => word.charAt(0).toUpperCase() + word.slice(1))
      .join(' ');
  }

  private extractBaseName(agentName: string): string {
    // Remove common prefixes and suffixes to get the base agent name
    let baseName = agentName;

    // Remove stack prefix pattern (e.g., "sim-" or "demo-")
    baseName = baseName.replace(/^[a-z]+-/, '');

    // Remove stack suffix pattern (e.g., "-1234" or "-abcd")
    baseName = baseName.replace(/-[a-z0-9]+$/, '');

    return baseName;
  }

  private async getAgentDetails(agentId: string): Promise<any> {
    try {
      const command = new GetAgentCommand({ agentId });
      const response = await this.bedrockClient.send(command);
      return response.agent;
    } catch (error) {
      console.error(`Error getting agent details for ${agentId}:`, error);
      return null;
    }
  }

  private async getAgentKnowledgeBases(agentId: string): Promise<KnowledgeBaseAssociation[]> {
    try {
      const command = new ListAgentKnowledgeBasesCommand({
        agentId,
        agentVersion: 'DRAFT'
      });
      const response = await this.bedrockClient.send(command);

      return (response.agentKnowledgeBaseSummaries || []).map((kb: any) => ({
        knowledgeBaseId: kb.knowledgeBaseId!,
        name: kb.description || kb.knowledgeBaseId!,
        description: kb.description || '',
        state: kb.knowledgeBaseState as 'ENABLED' | 'DISABLED'
      }));
    } catch (error) {
      console.error(`Error getting knowledge bases for agent ${agentId}:`, error);
      return [];
    }
  }

  private async getAgentCollaborators(agentId: string): Promise<any[]> {
    try {
      const command = new ListAgentCollaboratorsCommand({
        agentId,
        agentVersion: 'DRAFT'
      });
      const response = await this.bedrockClient.send(command);
      const collaborators = response.agentCollaboratorSummaries || [];


      return collaborators;
    } catch (error) {
      console.error(`Error getting collaborators for agent ${agentId}:`, error);
      return [];
    }
  }

  private setCollaboratorSupervisors(nodes: AgentNode[]): void {
    const supervisors = nodes.filter(n => n.role === 'supervisor');

    for (const supervisor of supervisors) {
      const collaboratorData = (supervisor as any).collaboratorData || [];

      if (collaboratorData.length > 0) {

        for (const collaboratorInfo of collaboratorData) {
          const collaboratorName = collaboratorInfo.collaboratorName;
          const collaboratorId = collaboratorInfo.collaboratorId;

          // Try multiple matching strategies using both name and ID
          const collaborator = nodes.find(n =>
            // Exact matches
            n.id === collaboratorId ||
            n.name === collaboratorName ||
            n.displayName === collaboratorName ||
            // Case-insensitive matches
            n.name.toLowerCase() === collaboratorName.toLowerCase() ||
            n.displayName.toLowerCase() === collaboratorName.toLowerCase() ||
            // Partial matches
            collaboratorName.toLowerCase().includes(n.name.toLowerCase()) ||
            n.name.toLowerCase().includes(collaboratorName.toLowerCase()) ||
            // Try matching the base name (remove stack prefix/suffix)
            this.extractBaseName(n.name).toLowerCase() === this.extractBaseName(collaboratorName).toLowerCase()
          );

          if (collaborator) {
            collaborator.supervisor = supervisor.id;
            collaborator.role = 'collaborator'; // Ensure it's marked as collaborator
          } else {
            console.warn(`❌ Could not find collaborator: ${collaboratorName} (ID: ${collaboratorId})`);
          }
        }
      } else if (supervisor.collaborators && supervisor.collaborators.length > 0) {
        // Fallback to old method if no collaboratorData

        for (const collaboratorName of supervisor.collaborators) {
          const collaborator = nodes.find(n =>
            n.name === collaboratorName ||
            n.displayName === collaboratorName ||
            n.name.toLowerCase() === collaboratorName.toLowerCase() ||
            n.displayName.toLowerCase() === collaboratorName.toLowerCase() ||
            collaboratorName.toLowerCase().includes(n.name.toLowerCase()) ||
            n.name.toLowerCase().includes(collaboratorName.toLowerCase()) ||
            this.extractBaseName(n.name).toLowerCase() === this.extractBaseName(collaboratorName).toLowerCase()
          );

          if (collaborator) {
            collaborator.supervisor = supervisor.id;
            collaborator.role = 'collaborator';
          } else {
            console.warn(`❌ Could not find collaborator (fallback): ${collaboratorName}`);
          }
        }
      }
    }


  }

  /**
   * Update agent instructions
   */
  updateAgentInstructions(agentId: string, instructions: string): Observable<boolean> {
    return from(this.updateInstructionsInternal(agentId, instructions)).pipe(
      map(() => true),
      catchError(error => {
        console.error('Error updating agent instructions:', error);
        return throwError(() => error);
      })
    );
  }

  private async updateInstructionsInternal(agentId: string, instructions: string): Promise<void> {
    await this.initializeClient();

    try {
      // Get the current agent configuration from DRAFT version for updating
      // Note: We use DRAFT version here because we're updating the agent
      const agentDetails = await this.getAgentDetails(agentId);
      if (!agentDetails) {
        throw new Error(`Agent ${agentId} not found`);
      }

      // Update the agent with new instructions
      // Remove promptOverrideConfiguration to avoid conflicts with DEFAULT promptCreationMode
      const command = new UpdateAgentCommand({
        agentId: agentId,
        agentName: agentDetails.agentName,
        instruction: instructions, // Note: it's 'instruction' not 'instructions'
        foundationModel: agentDetails.foundationModel,
        description: agentDetails.description,
        idleSessionTTLInSeconds: agentDetails.idleSessionTTLInSeconds,
        agentResourceRoleArn: agentDetails.agentResourceRoleArn,
        customerEncryptionKeyArn: agentDetails.customerEncryptionKeyArn,
        agentCollaboration: agentDetails.agentCollaboration
        // Removed promptOverrideConfiguration to avoid conflicts with DEFAULT promptCreationMode
      });

      await this.bedrockClient.send(command);

      // Prepare the agent to apply changes
      await this.prepareAndUpdateAgentAlias(agentId);
    } catch (error) {
      console.error('Error in updateInstructionsInternal:', error);
      throw error;
    }
  }

  private async prepareAndUpdateAgentAlias(agentId: string): Promise<void> {
    try {
      // Step 1: Prepare the agent
      const prepareCommand = new PrepareAgentCommand({ agentId });
      await this.bedrockClient.send(prepareCommand);

      // Step 2: Wait for agent to be ready
      await this.waitForAgentReady(agentId);

      // Step 3: Update the agent alias
      const aliasesCommand = new ListAgentAliasesCommand({ agentId: agentId });
      const aliases: ListAgentAliasesResponse = await this.bedrockClient.send(aliasesCommand);

      if (aliases && aliases.agentAliasSummaries) {
        const alias = aliases.agentAliasSummaries.find(a => a.agentAliasName === 'latest');
        if (alias && alias.agentAliasId) {
          const updateAgentAlias = new UpdateAgentAliasCommand({
            agentId: agentId,
            agentAliasId: alias.agentAliasId,
            agentAliasName: alias.agentAliasName
          });
          await this.bedrockClient.send(updateAgentAlias);
        } else {
          console.warn(`No 'latest' alias found for agent ${agentId}`);
        }
      }
    } catch (error) {
      console.error(`Error in prepareAndUpdateAgentAlias for agent ${agentId}:`, error);
      throw error;
    }
  }

  /**
   * Wait for agent to be in a ready state (not PREPARING)
   */
  private async waitForAgentReady(agentId: string, maxWaitTimeMs: number = 300000, pollIntervalMs: number = 5000): Promise<void> {
    const startTime = Date.now();

    while (Date.now() - startTime < maxWaitTimeMs) {
      try {
        const agentDetails = await this.getAgentDetails(agentId);

        if (!agentDetails) {
          throw new Error(`Agent ${agentId} not found`);
        }

        const status = agentDetails.agentStatus;

        // Check if agent is ready (not in PREPARING state)
        if (status === 'PREPARED' || status === 'FAILED' || status === 'NOT_PREPARED') {
          if (status === 'FAILED') {
            throw new Error(`Agent ${agentId} preparation failed`);
          }
          return;
        }

        // If still preparing, wait before checking again
        if (status === 'PREPARING') {
          await this.sleep(pollIntervalMs);
          continue;
        }

        // For any other status, assume it's ready
        return;

      } catch (error) {
        console.error(`Error checking agent ${agentId} status:`, error);
        // If we can't check the status, wait a bit and try again
        await this.sleep(pollIntervalMs);
      }
    }

    // If we've exceeded the max wait time, throw an error
    throw new Error(`Timeout waiting for agent ${agentId} to be ready after ${maxWaitTimeMs}ms`);
  }

  /**
   * Sleep utility function
   */
  private sleep(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  /**
   * Associate knowledge base with agent
   */
  associateKnowledgeBase(agentId: string, knowledgeBaseId: string, description?: string): Observable<boolean> {
    return from(this.associateKnowledgeBaseInternal(agentId, knowledgeBaseId, description)).pipe(
      map(() => true),
      catchError(error => {
        console.error('Error associating knowledge base:', error);
        return throwError(() => error);
      })
    );
  }

  private async associateKnowledgeBaseInternal(agentId: string, knowledgeBaseId: string, description?: string): Promise<void> {
    await this.initializeClient();

    try {
      const command = new AssociateAgentKnowledgeBaseCommand({
        agentId,
        agentVersion: 'DRAFT',
        knowledgeBaseId,
        description: description || `Knowledge base ${knowledgeBaseId}`,
        knowledgeBaseState: 'ENABLED'
      });

      await this.bedrockClient.send(command);

      // Prepare the agent to apply changes
      await this.prepareAndUpdateAgentAlias(agentId);

    } catch (error) {
      console.error('Error in associateKnowledgeBaseInternal:', error);
      throw error;
    }
  }

  /**
   * Disassociate knowledge base from agent
   */
  disassociateKnowledgeBase(agentId: string, knowledgeBaseId: string): Observable<boolean> {
    return from(this.disassociateKnowledgeBaseInternal(agentId, knowledgeBaseId)).pipe(
      map(() => true),
      catchError(error => {
        console.error('Error disassociating knowledge base:', error);
        return throwError(() => error);
      })
    );
  }

  private async disassociateKnowledgeBaseInternal(agentId: string, knowledgeBaseId: string): Promise<void> {
    await this.initializeClient();

    try {
      const command = new DisassociateAgentKnowledgeBaseCommand({
        agentId,
        agentVersion: 'DRAFT',
        knowledgeBaseId
      });

      await this.bedrockClient.send(command);

      // Prepare the agent to apply changes
      await this.prepareAndUpdateAgentAlias(agentId);

    } catch (error) {
      console.error('Error in disassociateKnowledgeBaseInternal:', error);
      throw error;
    }
  }

  /**
   * Get available knowledge bases that can be associated with agents
   */
  getAvailableKnowledgeBases(): Observable<any[]> {
    return from(this.loadAvailableKnowledgeBases()).pipe(
      catchError(error => {
        console.error('Error loading available knowledge bases:', error);
        return throwError(() => error);
      })
    );
  }

  private async loadAvailableKnowledgeBases(): Promise<any[]> {
    try {
      await this.initializeClient();

      const command = new ListKnowledgeBasesCommand({});
      const response = await this.bedrockClient.send(command);

      return response.knowledgeBaseSummaries || [];
    } catch (error) {
      console.error('Error in loadAvailableKnowledgeBases, falling back to mock data:', error);
      return [];
      //return this.createMockKnowledgeBases();
    }
  }

  /**
   * Batch update agent configuration
   */
  updateAgent(request: AgentUpdateRequest): Observable<boolean> {
    return from(this.updateAgentInternal(request)).pipe(
      map(() => true),
      catchError(error => {
        console.error('Error updating agent:', error);
        return throwError(() => error);
      })
    );
  }

  private async updateAgentInternal(request: AgentUpdateRequest): Promise<void> {
    const { agentId, instructions, knowledgeBaseAssociations } = request;

    // Update instructions if provided
    if (instructions !== undefined) {
      await this.updateInstructionsInternal(agentId, instructions);
    }

    // Handle knowledge base associations
    if (knowledgeBaseAssociations) {
      // Remove knowledge bases
      if (knowledgeBaseAssociations.remove) {
        for (const kbId of knowledgeBaseAssociations.remove) {
          await this.disassociateKnowledgeBaseInternal(agentId, kbId);
        }
      }

      // Add knowledge bases
      if (knowledgeBaseAssociations.add) {
        for (const kbId of knowledgeBaseAssociations.add) {
          await this.associateKnowledgeBaseInternal(agentId, kbId);
        }
      }
    }
  }
}