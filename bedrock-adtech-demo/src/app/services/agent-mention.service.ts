import { Injectable } from '@angular/core';
import { AgentConfigService } from './agent-config.service';
import { AgentMention, AgentSuggestion, EnrichedAgent, ParsedMessage } from '../models/application-models';


@Injectable({
  providedIn: 'root'
})
export class AgentMentionService {

  constructor(private agentConfig: AgentConfigService) {}

  /**
   * Parse agent mentions from user input text
   * Supports formats: @[Bid Simulator Agent], @[Contextual Analysis Agent], etc.
   */
  parseAgentMentions(text: string): ParsedMessage {
    const mentions: AgentMention[] = [];
    let cleanedText = text;
    let mentionedAgent: EnrichedAgent | null = null;

    // Regex to match @[Agent Name] patterns
    const mentionRegex = /@\[([^\]]+)\]/g;
    let match: RegExpExecArray | null;

    while ((match = mentionRegex.exec(text)) !== null) {
      const fullMatch = match[0]; // Full @[Agent Name]
      const agentIdentifier = match[1].trim(); // Just the agent name
      const startIndex = match.index;
      const endIndex = match.index + fullMatch.length;
      const agent = this.agentConfig.getAgent(agentIdentifier);
      if (agentIdentifier&&agentIdentifier!="AdFabricAgent") {
        mentions.push({
          agentKey: agentIdentifier,
          displayName: agentIdentifier,
          agent:(agent as EnrichedAgent),
          startIndex,
          endIndex,
          originalText: fullMatch
        });

        // Use the first valid mention as the target agent
        if (!mentionedAgent&&agent) {
          mentionedAgent = agent;
        }

        // Remove the mention from the cleaned text
        cleanedText = cleanedText.replace(fullMatch, '').trim();
      } else {
        console.warn('❌ No agent found for identifier:', agentIdentifier);
      }
    }

    return {
      cleanedText,
      mentionedAgent,
      mentions
    };
  }

  /**
   * Get agent suggestions based on partial input
   * Used for typeahead functionality
   */
  getAgentSuggestions(searchText: string = '', maxResults: number = 4): AgentSuggestion[] {
    // Use the centralized search functionality
    const agents = this.agentConfig.searchAgents(searchText, maxResults);
    
    return agents.map(agent => ({
      key: agent.key||agent.name,
      displayName: agent.displayName,
      description: agent.description,
      agentType: agent.agentType,
      agent:agent,
      color: agent.color,
      icon: agent.icon
    }));
  }

  /**
   * Format agent mention for display
   */
  formatAgentMention(agent: EnrichedAgent, ): string {
    return agent ? `@[${agent.name}]` : ``;
  }

  /**
   * Check if text contains any agent mentions
   */
  hasAgentMentions(text: string): boolean {
    const mentionRegex = /@\[([^\]]+)\]/;
    return mentionRegex.test(text);
  }

  /**
   * Extract the target agent from mentions (returns the first valid mention)
   */
  getTargetAgentFromText(text: string): EnrichedAgent | null {
    const parsed = this.parseAgentMentions(text);
    return parsed.mentionedAgent;
  }

  /**
   * Get all available agents for selection
   */
  getAvailableAgents(): AgentSuggestion[] {
    const agents = this.agentConfig.getActiveAgents();
    return agents.map(agent => ({
      key: agent.key,
      agent:agent,
      displayName: agent.displayName,
      description: agent.description,
      agentType: agent.agentType,
      color: agent.color,
      icon: agent.icon
    }));
  }
} 