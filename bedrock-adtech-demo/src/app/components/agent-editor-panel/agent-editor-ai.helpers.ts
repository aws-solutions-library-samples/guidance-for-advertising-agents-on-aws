/**
 * AI generation helper functions extracted from AgentEditorPanelComponent.
 */
import { AgentConfiguration } from '../agent-management-modal/agent-management-modal.component';
import { BedrockService } from '../../services/bedrock.service';
import { VisualizationTemplate } from '../../services/agent-dynamodb.service';

/** Represents an attached document for AI generation context */
export interface AttachedDocument {
  name: string;
  content: string;
}

/** Result of full agent generation */
export interface GeneratedAgentResult {
  agent: AgentConfiguration;
  instructions: string;
  visualizationTemplates: VisualizationTemplate[];
}

/**
 * Reference example of a well-structured agent instruction set.
 * This is injected into generation prompts so the LLM can follow the universal
 * patterns used across the platform (workflow, collaboration, response templates,
 * agent context retrieval, etc.).
 *
 * Condensed from MediaPlanningAgent.txt and InventoryOptimizationAgent.txt.
 */
const EXAMPLE_AGENT_INSTRUCTIONS = `
========== BEGIN EXAMPLE AGENT (MediaPlanningAgent) ==========
You are the Media Planning Agent, a strategic orchestrator of comprehensive media planning from the publisher's perspective.

IMPORTANT: Keep your responses to a maximum of 2000 characters, preferably less than that, and be as concise as possible.
Ensure you consider the user's intent and pull in only the needed specialists to complete your analysis.

## Core Responsibilities
1. **Strategic Media Planning**: Develop comprehensive media plans
2. **Inventory Optimization**: Analyze and optimize inventory allocation
3. **Revenue Maximization**: Create strategies that balance advertiser value with publisher revenue
4. **Collaborative Intelligence**: Coordinate with specialist teams

========================================
Your required workflow
========================================
1. If nothing relevant was found, ensure you are transparent about the fact that your analysis is based on your experience and not results from the KnowledgeBase
2. If you need more information from the user, ask for it, but don't require it.
3. If executing the user's request or answering the user's question requires skills that are delegated to specialists, do so using your invoke_specialist_with_RAG tool.
4. Use the data you have collected from the knowledge base as well as any contextual information from the user to support your analysis
5. Return a unified response.

## Input Requirements
You expect the following information for each analysis:
- **Campaign Details**: Advertiser objectives, budget parameters, target audience, campaign type
- **Publisher Context**: Available inventory, content types, audience characteristics, revenue goals

## Strategic Planning Framework
1. **Thinking Process**: Think through analysis step-by-step, reason about alignment, consider multiple scenarios, evaluate confidence levels, plan collaboration with specialist teams.
2. **Collaborative Intelligence**: Coordinate with specialist teams:
   !!!CRITICAL!!! Invoke specialists in parallel wherever their work does not depend on the analysis of another agent. ALWAYS wait for them to respond before your own final output is generated!!
   - **AudienceIntelligenceAgent**: Consumer behavior and targeting expertise
   - **TimingStrategyAgent**: Campaign timing and pacing optimization
   - **FormatStrategyOptimizerAgent**: Ad format optimization and revenue maximization
3. **Strategic Synthesis**: Integrate insights from all teams to create unified strategies.

CRITICAL: When coordinating with specialists, always address them directly using @ syntax like "@AudienceIntelligenceAgent, please analyze the audience alignment for this campaign".

**COLLABORATION STYLE REQUIREMENTS:**
- Engage specialists naturally without announcing "Now I will coordinate with..."
- Don't unnecessarily add responses like "Perfect! Now I have insights from all teams..."
- Present final analysis as unified strategic thinking, not as a compilation of separate inputs
- Focus on strategic substance, not process mechanics

**FORBIDDEN PROGRESS REPORTING EXAMPLES:**
❌ "Perfect! Now I have comprehensive insights from all three specialist teams..."
❌ "Let me analyze the key findings and create my strategic recommendations..."
❌ "I need to use my [tool_name] tool to..."

For all text outside of data, use Markdown format with proper headings and structure. NEVER use emojis in responses.

Your specialist agents (accessed with your invoke_specialist_with_RAG tool):
!!!CRITICAL!!! Invoke these specialists in parallel wherever their work does not depend on the analysis of another agent.

**AudienceIntelligenceAgent:**
- Request audience-content alignment analysis
- Obtain targeting recommendations

**TimingStrategyAgent:**
- Request optimal campaign scheduling analysis
- Obtain content calendar integration strategies

========================================
Agent Context Retrieval
========================================
When a user mentions another agent by name (e.g., "@AgentName said..." or "What did AgentName say about..."), you can retrieve the last things said by that agent using your 'lookup_events' tool.
Since a user may mistype the name, please select the most likely name they meant to type from this list of real agents:
{{AGENT_NAME_LIST}}

Usage:
- agent_name: The exact name of the agent whose messages you want to retrieve
- max_results: Number of recent events to retrieve (default: 5)

This allows you to reference and build upon the analysis and recommendations made by other agents in the conversation.
========== END EXAMPLE AGENT ==========
`.trim();

/**
 * Generate agent instructions using Claude, with optional document attachments
 */
export async function generateInstructionsText(
  agent: AgentConfiguration,
  promptText: string,
  bedrockService: BedrockService,
  documents: AttachedDocument[] = []
): Promise<string> {
  const hasExisting = agent.instructions && agent.instructions.trim().length > 0;

  let prompt = `You are an expert at creating agent system prompts for AI agents in an advertising technology platform.

Below is an EXAMPLE of a well-structured agent instruction set from this platform. Study its patterns carefully — your generated instructions MUST follow the same structural conventions:
- A "Your required workflow" section defining the standard workflow steps (knowledge base lookup, transparency, specialist delegation, unified response)
- A "Core Responsibilities" section
- A collaboration framework that uses invoke_specialist_with_RAG tool and @ syntax for addressing specialists
- Parallel specialist invocation with !!!CRITICAL!!! markers
- Collaboration style requirements (no meta-commentary, no progress reporting)
- Forbidden progress reporting examples
- An "Agent Context Retrieval" section at the end with the {{AGENT_NAME_LIST}} placeholder and lookup_events tool usage
- Markdown formatting, no emojis
- A 2000 character response limit reminder at the top

=== EXAMPLE AGENT INSTRUCTIONS (for structural reference only) ===
${EXAMPLE_AGENT_INSTRUCTIONS}
=== END EXAMPLE ===

Now generate instructions for the following agent:

Agent Details:
- Display Name: ${agent.agent_display_name || 'Not specified'}
- Description: ${agent.agent_description || 'Not specified'}
- Team: ${agent.team_name || 'Not specified'}
- Tool Agents Available: ${agent.tool_agent_names?.join(', ') || 'None'}

`;

  if (documents.length > 0) {
    prompt += `The user has attached ${documents.length} reference document(s) to guide the generation. Use them as context for creating the instructions.\n\n`;
  }

  if (hasExisting) {
    prompt += `Current Instructions:
${agent.instructions}

User's Requested Changes:
${promptText || 'Improve and enhance the existing instructions'}

Please update the instructions based on the user's request while maintaining the core functionality and the structural patterns from the example above. Return ONLY the updated instructions text, no explanations.`;
  } else {
    prompt += `User's Requirements:
${promptText || 'Create comprehensive instructions for this agent based on its description'}

Please generate comprehensive system instructions for this agent. Follow the EXACT structural patterns from the example above, including:
1. An opening role definition with the 2000 character response limit reminder
2. Core Responsibilities section
3. "Your required workflow" section (knowledge base query, transparency, specialist delegation, unified response)
4. Input Requirements section relevant to this agent's domain
5. A strategic/analysis framework with thinking process and collaborative intelligence sections
6. Specialist agent coordination using invoke_specialist_with_RAG tool with @ syntax and !!!CRITICAL!!! parallel invocation markers (use the Tool Agents listed above as the specialists)
7. Collaboration style requirements and forbidden progress reporting examples
8. Response template section with relevant analysis areas
9. Agent Context Retrieval section at the end (copy the exact pattern with {{AGENT_NAME_LIST}} placeholder and lookup_events tool)

Return ONLY the instructions text, no explanations or preamble.`;
  }

  const generatedText = documents.length > 0
    ? await bedrockService.invokeClaudeWithDocuments(prompt, documents)
    : await bedrockService.invokeClaudeOpus(prompt);
  return generatedText.trim();
}


/**
 * Generate visualization mappings using Claude, with optional document attachments
 */
export async function generateVisualizationMappingsText(
  agent: AgentConfiguration,
  existingTemplates: VisualizationTemplate[] | undefined,
  availableTemplates: string[],
  promptText: string,
  bedrockService: BedrockService,
  documents: AttachedDocument[] = []
): Promise<VisualizationTemplate[]> {
  const hasExisting = existingTemplates && existingTemplates.length > 0;

  let prompt = `You are an expert at configuring visualization mappings for AI agents in an advertising technology platform.

Agent Details:
- Name: ${agent.agent_name || 'Not specified'}
- Display Name: ${agent.agent_display_name || 'Not specified'}
- Description: ${agent.agent_description || 'Not specified'}
- Instructions Summary: ${(agent.instructions || '').substring(0, 500)}...

Available Visualization Templates:
${availableTemplates.map(t => `- ${t}`).join('\n')}

Template Descriptions:
- adcp_get_products-visualization: Displays product inventory with pricing, reach, and audience data
- allocations-visualization: Shows budget allocation across channels/publishers
- bar-chart-visualization: Generic bar chart for comparing values
- channels-visualization: Channel performance and distribution
- creative-visualization: Creative assets and variations display
- decision-tree-visualization: Decision flow and logic trees
- donut-chart-visualization: Proportional data visualization
- double-histogram-visualization: Comparative histogram data
- histogram-visualization: Distribution data visualization
- metrics-visualization: KPIs and performance metrics
- segments-visualization: Audience segment analysis
- timeline-visualization: Temporal data and milestones

`;

  if (documents.length > 0) {
    prompt += `The user has attached ${documents.length} reference document(s). Use them as context.\n\n`;
  }

  if (hasExisting) {
    prompt += `Current Mappings:
${JSON.stringify(existingTemplates, null, 2)}

User's Requested Changes:
${promptText || 'Improve and optimize the visualization mappings'}

Please update the mappings based on the user's request. Return ONLY a JSON array of template mappings in this format:
[{"templateId": "template-name", "usage": "Description of when to use this visualization"}]`;
  } else {
    prompt += `User's Requirements:
${promptText || 'Suggest appropriate visualizations based on the agent description'}

Based on the agent's purpose and capabilities, suggest appropriate visualization templates. Return ONLY a JSON array of template mappings in this format:
[{"templateId": "template-name", "usage": "Description of when to use this visualization"}]

Select 2-5 most relevant templates for this agent.`;
  }

  const generatedText = documents.length > 0
    ? await bedrockService.invokeClaudeWithDocuments(prompt, documents, 2000)
    : await bedrockService.invokeClaudeOpus(prompt, 2000);

  const jsonMatch = generatedText.match(/\[[\s\S]*\]/);
  if (jsonMatch) {
    return JSON.parse(jsonMatch[0]) as VisualizationTemplate[];
  }
  throw new Error('Could not parse visualization mappings from response');
}

/**
 * Generate a complete agent configuration from a single prompt + optional documents.
 * Returns the agent config, instructions, and visualization mappings.
 */
export async function generateFullAgentConfig(
  promptText: string,
  availableAgents: string[],
  availableTemplates: string[],
  bedrockService: BedrockService,
  documents: AttachedDocument[] = []
): Promise<GeneratedAgentResult> {
  const prompt = `You are an expert at creating AI agent configurations for an advertising technology platform.

Below is an EXAMPLE of a well-structured agent instruction set from this platform. When generating the "instructions" field, you MUST follow the same structural conventions demonstrated here:
- A "Your required workflow" section (knowledge base lookup, transparency, specialist delegation, unified response)
- A "Core Responsibilities" section
- A collaboration framework using invoke_specialist_with_RAG tool and @ syntax for addressing specialists
- Parallel specialist invocation with !!!CRITICAL!!! markers
- Collaboration style requirements (no meta-commentary, no progress reporting)
- Forbidden progress reporting examples
- An "Agent Context Retrieval" section at the end with the {{AGENT_NAME_LIST}} placeholder and lookup_events tool usage
- Markdown formatting, no emojis
- A 2000 character response limit reminder at the top

=== EXAMPLE AGENT INSTRUCTIONS (for structural reference only) ===
${EXAMPLE_AGENT_INSTRUCTIONS}
=== END EXAMPLE ===

Based on the user's description below, generate a COMPLETE agent configuration as a JSON object.

User's Description:
${promptText}

Available Tool Agents (that this agent can invoke as specialists):
${availableAgents.map(a => `- ${a}`).join('\n') || '- None available'}

Available Visualization Templates:
${availableTemplates.map(t => `- ${t}`).join('\n')}

${documents.length > 0 ? `The user has attached ${documents.length} reference document(s). Use them as context for generating the agent configuration, instructions, and visualization mappings.\n` : ''}

Return a JSON object with EXACTLY this structure (no markdown fences, no explanation):
{
  "agent": {
    "agent_id": "PascalCaseAgentName (e.g., CampaignOptimizationAgent)",
    "agent_name": "Same as agent_id",
    "agent_display_name": "Human-readable name (e.g., Campaign Optimization Agent)",
    "team_name": "Appropriate team name",
    "agent_description": "1-2 sentence description of what this agent does",
    "tool_agent_names": ["list of tool agent names from the available list above, if relevant"],
    "external_agents": [],
    "model_inputs": {
      "default": {
        "model_id": "anthropic.claude-3-5-sonnet-20241022-v2:0",
        "max_tokens": 8000,
        "temperature": 0.3
      }
    },
    "agent_tools": ["list of relevant tools like invoke_specialist_with_RAG, retrieve_knowledge_base_results_tool, lookup_events, etc."],
    "color": "#6842ff"
  },
  "instructions": "Full system prompt instructions following the EXACT structural patterns from the example above (workflow, collaboration, specialist coordination with @ syntax, agent context retrieval with {{AGENT_NAME_LIST}}, etc.)",
  "visualizationTemplates": [
    {"templateId": "template-id-from-available-list", "usage": "When to use this visualization"}
  ]
}

IMPORTANT:
- agent_id and agent_name must be PascalCase with no spaces
- Select 2-5 visualization templates that are most relevant
- Instructions MUST follow the structural patterns from the example (workflow section, collaboration framework, forbidden examples, agent context retrieval section, etc.)
- Instructions should be comprehensive (500+ words) defining role, workflow, capabilities
- Only select tool_agent_names from the available list provided
- Return ONLY the JSON object, nothing else`;

  const generatedText = documents.length > 0
    ? await bedrockService.invokeClaudeWithDocuments(prompt, documents, 16000)
    : await bedrockService.invokeClaudeOpus(prompt, 16000);

  // Parse the JSON response
  const jsonMatch = generatedText.match(/\{[\s\S]*\}/);
  if (!jsonMatch) {
    throw new Error('Could not parse agent configuration from response');
  }

  const parsed = JSON.parse(jsonMatch[0]);

  if (!parsed.agent || !parsed.instructions) {
    throw new Error('Generated response missing required fields (agent, instructions)');
  }

  return {
    agent: {
      ...parsed.agent,
      instructions: parsed.instructions,
      tool_agent_names: parsed.agent.tool_agent_names || [],
      external_agents: parsed.agent.external_agents || [],
      agent_tools: parsed.agent.agent_tools || [],
      model_inputs: parsed.agent.model_inputs || {
        default: { model_id: 'anthropic.claude-3-5-sonnet-20241022-v2:0', max_tokens: 8000, temperature: 0.3 }
      },
      color: parsed.agent.color || '#6842ff'
    },
    instructions: parsed.instructions,
    visualizationTemplates: parsed.visualizationTemplates || []
  };
}
