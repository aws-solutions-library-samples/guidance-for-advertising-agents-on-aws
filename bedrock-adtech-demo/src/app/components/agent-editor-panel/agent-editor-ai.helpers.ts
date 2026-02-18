/**
 * AI generation helper functions extracted from AgentEditorPanelComponent.
 */
import { AgentConfiguration } from '../agent-management-modal/agent-management-modal.component';
import { BedrockService } from '../../services/bedrock.service';
import { VisualizationTemplate } from '../../services/agent-dynamodb.service';

/**
 * Generate agent instructions using Claude
 */
export async function generateInstructionsText(
  agent: AgentConfiguration,
  promptText: string,
  bedrockService: BedrockService
): Promise<string> {
  const hasExisting = agent.instructions && agent.instructions.trim().length > 0;

  let prompt = `You are an expert at creating agent system prompts for AI agents in an advertising technology platform.

Agent Details:
- Display Name: ${agent.agent_display_name || 'Not specified'}
- Description: ${agent.agent_description || 'Not specified'}
- Team: ${agent.team_name || 'Not specified'}
- Tool Agents Available: ${agent.tool_agent_names?.join(', ') || 'None'}

`;

  if (hasExisting) {
    prompt += `Current Instructions:
${agent.instructions}

User's Requested Changes:
${promptText || 'Improve and enhance the existing instructions'}

Please update the instructions based on the user's request while maintaining the core functionality. Return ONLY the updated instructions text, no explanations.`;
  } else {
    prompt += `User's Requirements:
${promptText || 'Create comprehensive instructions for this agent based on its description'}

Please generate comprehensive system instructions for this agent. The instructions should:
1. Define the agent's role and responsibilities
2. Specify how it should interact with users
3. Outline its capabilities and limitations
4. Include any relevant domain knowledge for advertising technology
5. Define output formats and response styles

Return ONLY the instructions text, no explanations or preamble.`;
  }

  const generatedText = await bedrockService.invokeClaudeOpus(prompt);
  return generatedText.trim();
}

/**
 * Generate visualization mappings using Claude
 */
export async function generateVisualizationMappingsText(
  agent: AgentConfiguration,
  existingTemplates: VisualizationTemplate[] | undefined,
  availableTemplates: string[],
  promptText: string,
  bedrockService: BedrockService
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

  const generatedText = await bedrockService.invokeClaudeOpus(prompt, 2000);

  const jsonMatch = generatedText.match(/\[[\s\S]*\]/);
  if (jsonMatch) {
    return JSON.parse(jsonMatch[0]) as VisualizationTemplate[];
  }
  throw new Error('Could not parse visualization mappings from response');
}
