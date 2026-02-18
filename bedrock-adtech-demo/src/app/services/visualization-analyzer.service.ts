import { Injectable } from '@angular/core';
import { BedrockService } from './bedrock.service';
import { AgentDynamoDBService } from './agent-dynamodb.service';
import { VisualizationAnalysisResult } from '../models/application-models';

@Injectable({
  providedIn: 'root'
})
export class VisualizationAnalyzerService {

  constructor(
    private bedrockService: BedrockService,
    private agentDynamoDBService: AgentDynamoDBService
  ) {}

  /**
   * Check if an agent has visualization templates configured.
   */
  async hasVisualizationTemplates(agentName: string): Promise<boolean> {
    try {
      const mappings = await this.agentDynamoDBService.getVisualizationMappings(agentName);
      return !!(mappings && mappings.templates && mappings.templates.length > 0);
    } catch (error) {
      console.warn(`⚠️ VisualizationAnalyzer: Could not check templates for ${agentName}:`, error);
      return false;
    }
  }

  /**
   * Main entry point — analyzes agent response text against visualization templates
   * using Claude Haiku 4.5 and returns structured visualization data + summary.
   * Returns null if agent has no templates, if Claude fails, or if response JSON is invalid.
   */
  async analyzeResponse(
    agentName: string,
    responseText: string
  ): Promise<VisualizationAnalysisResult | null> {
    try {
      // 1. Get visualization mappings for this agent
      const mappings = await this.agentDynamoDBService.getVisualizationMappings(agentName);
      if (!mappings || !mappings.templates || mappings.templates.length === 0) {
        console.log(`VisualizationAnalyzer: No templates for ${agentName}, skipping analysis`);
        return null;
      }

      // 2. Retrieve full template schemas for each template
      const templateSchemas = await this.retrieveTemplateSchemas(agentName, mappings.templates);
      if (templateSchemas.length === 0) {
        console.warn(`VisualizationAnalyzer: Could not retrieve any template schemas for ${agentName}`);
        return null;
      }

      // 3. Construct the prompt for Claude Opus 4.5
      const prompt = this.buildAnalysisPrompt(responseText, templateSchemas);

      // 4. Call Claude Haiku 4.5
      console.log(`VisualizationAnalyzer: Invoking Claude Haiku 4.5 for ${agentName} with ${templateSchemas.length} templates`);
      const claudeResponse = await this.bedrockService.invokeClaudeHaiku(prompt);

      // 5. Parse the response into VisualizationAnalysisResult
      const result = this.parseClaudeResponse(claudeResponse, responseText);
      if (!result) {
        console.warn(`VisualizationAnalyzer: Failed to parse Claude response for ${agentName}`);
        return null;
      }

      console.log(`✅ VisualizationAnalyzer: Generated ${result.visualizations.length} visualizations for ${agentName}`);
      return result;

    } catch (error) {
      console.error(`❌ VisualizationAnalyzer: Analysis failed for ${agentName}:`, error);
      return null;
    }
  }

  /**
   * Retrieve full template schemas from DynamoDB for each template in the mapping.
   */
  private async retrieveTemplateSchemas(
    agentName: string,
    templates: { templateId: string; usage: string }[]
  ): Promise<{ templateId: string; usage: string; schema: any }[]> {
    const schemas: { templateId: string; usage: string; schema: any }[] = [];

    for (const template of templates) {
      try {
        const templateData = await this.agentDynamoDBService.getVisualizationTemplate(
          agentName,
          template.templateId
        );

        if (templateData) {
          // Extract the dataMapping as the schema, or use the full template
          const schema = templateData.dataMapping || templateData;
          schemas.push({
            templateId: template.templateId,
            usage: template.usage,
            schema
          });
        }
      } catch (error) {
        console.warn(`⚠️ VisualizationAnalyzer: Failed to retrieve template ${template.templateId}:`, error);
      }
    }

    return schemas;
  }

  /**
   * Build the analysis prompt for Claude Haiku 4.5.
   * Includes the response text, all template schemas, and instructions
   * to produce a ≤5-sentence summary and structured visualization JSON.
   */
  /**
     * Build the analysis prompt for Claude Haiku 4.5.
     * Includes the response text, all template schemas, and instructions
     * to produce a ≤5-sentence summary and structured visualization JSON.
     */
    /**
       * Build the analysis prompt for Claude Haiku 4.5.
       * Emphasizes extreme brevity — visualization cards must be scannable at a glance.
       */
      buildAnalysisPrompt(
        responseText: string,
        templateSchemas: { templateId: string; usage: string; schema: any }[]
      ): string {
        const templateDescriptions = templateSchemas.map((t, i) => {
          return `### Template ${i + 1}: ${t.templateId}
    Usage: ${t.usage}
    Schema:
    \`\`\`json
    ${JSON.stringify(t.schema, null, 2)}
    \`\`\``;
        }).join('\n\n');

        return `You are a data extraction engine for dashboard visualization cards. Extract ONLY key numbers and short labels from the agent response. This data will render in small UI cards — brevity is critical.

    ## Agent Response Text
    ${responseText}

    ## Available Visualization Templates
    ${templateDescriptions}

    ## Output Format
    Return a single JSON object — no other text:
    \`\`\`json
    {
      "summary": "1-2 sentence summary.",
      "visualizations": [
        {
          "visualizationType": "from template schema",
          "templateId": "the templateId",
          "data": { ... populated template data ... }
        }
      ]
    }
    \`\`\`

    ## ABSOLUTE RULES — VIOLATING ANY OF THESE IS A FAILURE

    ### Brevity (most important)
    - Summary: 1-2 sentences max.
    - Each insight/recommendation/risk: MAX 8 WORDS. Example: "Increase mobile bid by 15%". NOT: "Based on the analysis of current performance metrics, we recommend increasing the mobile channel bid adjustment by approximately 15% to capture additional impression share."
    - Labels and names: 2-4 words max. Example: "CTV Premium". NOT: "Connected TV Premium Streaming Inventory Segment".
    - No narrative text anywhere. Numbers and short phrases only.

    ### Limits
    - Max 5 allocation items per visualization.
    - Max 4 metric cards per visualization.
    - Max 2 insights, 2 recommendations, 2 risks per item.
    - Max 3 subMetrics per metric card.

    ### Types
    - "insights", "risks", "recommendations", "items", "metrics", "subMetrics", "allocations", "channels", "segments" → ALWAYS arrays, NEVER strings.
    - Single value → wrap in array: ["value"].
    - percentage, budget, value, score → numbers, not strings.
    - Do NOT put "visualizationType" or "templateId" inside "data".

    ### Content
    - Extract real values from the response text — no placeholders.
    - Only include templates that have enough data to populate.
    - Empty visualizations array if nothing fits.
    - Return ONLY the JSON object.`;
      }

  /**
   * Parse Claude Opus 4.5 response into a VisualizationAnalysisResult.
   * Returns null if the response cannot be parsed into valid JSON.
   */
  /**
     * Parse Claude Haiku 4.5 response into a VisualizationAnalysisResult.
     * Normalizes data types (e.g. string→array) before returning.
     * Returns null if the response cannot be parsed into valid JSON.
     */
    parseClaudeResponse(
      claudeResponse: string,
      originalText: string
    ): VisualizationAnalysisResult | null {
      try {
        // Try to extract JSON from the response — Claude may wrap it in markdown code blocks
        const jsonStr = this.extractJsonFromResponse(claudeResponse);
        if (!jsonStr) {
          console.warn('VisualizationAnalyzer: No JSON found in Claude response');
          return null;
        }

        const parsed = JSON.parse(jsonStr);

        // Validate required fields
        if (typeof parsed.summary !== 'string') {
          console.warn('VisualizationAnalyzer: Missing or invalid summary in Claude response');
          return null;
        }

        if (!Array.isArray(parsed.visualizations)) {
          console.warn('VisualizationAnalyzer: Missing or invalid visualizations array in Claude response');
          return null;
        }

        // Validate each visualization entry
        const validVisualizations = parsed.visualizations
          .filter((v: any) => {
            return v &&
              typeof v.visualizationType === 'string' &&
              typeof v.templateId === 'string' &&
              v.data !== undefined && v.data !== null;
          })
          .map((v: any) => ({
            ...v,
            data: this.normalizeVisualizationData(v.data)
          }));

        // Enforce the 5-sentence summary constraint
        const summary = this.constrainSummary(parsed.summary);

        return {
          summary,
          visualizations: validVisualizations,
          originalText
        };
      } catch (error) {
        console.error('VisualizationAnalyzer: Failed to parse Claude response:', error);
        return null;
      }
    }

  /**
   * Extract a JSON object string from Claude's response.
   * Handles responses wrapped in markdown code blocks or with surrounding text.
   */
  private extractJsonFromResponse(response: string): string | null {
    // Try extracting from ```json ... ``` code block first
    const codeBlockMatch = response.match(/```(?:json)?\s*([\s\S]*?)\s*```/);
    if (codeBlockMatch) {
      return codeBlockMatch[1].trim();
    }

    // Try to find a top-level JSON object directly
    const firstBrace = response.indexOf('{');
    const lastBrace = response.lastIndexOf('}');
    if (firstBrace !== -1 && lastBrace > firstBrace) {
      return response.substring(firstBrace, lastBrace + 1);
    }

    return null;
  }
  /**
   * Known fields that must always be arrays. If Haiku returns a plain string
   * for any of these, wrap it in a single-element array to prevent NG02200 errors.
   */
  private static readonly ARRAY_FIELDS = new Set([
    'insights', 'risks', 'recommendations', 'items', 'metrics',
    'subMetrics', 'allocations', 'channels', 'segments', 'tags',
    'keywords', 'features', 'benefits', 'actions'
  ]);

  /**
   * Recursively walk the visualization data object and fix type mismatches:
   * - String values for known array fields → wrapped in a single-element array
   * - Removes duplicate visualizationType/templateId that Haiku sometimes nests inside data
   */
  /**
     * Known fields whose array items should be truncated to keep cards scannable.
     */
    private static readonly TRUNCATE_FIELDS = new Set([
      'insights', 'risks', 'recommendations'
    ]);

    /** Max characters for insight/risk/recommendation strings */
    private static readonly MAX_ITEM_LENGTH = 80;

    /** Max items for insight/risk/recommendation arrays */
    private static readonly MAX_ARRAY_ITEMS = 3;

    /**
     * Recursively walk the visualization data object and fix type mismatches:
     * - String values for known array fields → wrapped in a single-element array
     * - Truncates verbose insight/risk/recommendation strings
     * - Caps array lengths for known verbose fields
     * - Removes duplicate visualizationType/templateId that Haiku sometimes nests inside data
     */
    private normalizeVisualizationData(data: any): any {
      if (data === null || data === undefined) return data;
      if (typeof data !== 'object') return data;

      if (Array.isArray(data)) {
        return data.map(item => this.normalizeVisualizationData(item));
      }

      const normalized: any = {};
      for (const key of Object.keys(data)) {
        // Strip duplicated top-level keys that belong on the visualization wrapper, not inside data
        if (key === 'visualizationType' || key === 'templateId') {
          continue;
        }

        let value = data[key];

        // If this key should be an array but got a string, wrap it
        if (VisualizationAnalyzerService.ARRAY_FIELDS.has(key) && typeof value === 'string') {
          value = [value];
        }

        // Truncate verbose array items and cap array length for insight-like fields
        if (VisualizationAnalyzerService.TRUNCATE_FIELDS.has(key) && Array.isArray(value)) {
          value = value
            .slice(0, VisualizationAnalyzerService.MAX_ARRAY_ITEMS)
            .map((item: any) => {
              if (typeof item === 'string' && item.length > VisualizationAnalyzerService.MAX_ITEM_LENGTH) {
                return item.substring(0, VisualizationAnalyzerService.MAX_ITEM_LENGTH - 1) + '…';
              }
              return item;
            });
        }

        // Recurse into nested objects/arrays
        normalized[key] = this.normalizeVisualizationData(value);
      }

      return normalized;
    }



  /**
   * Ensure the summary has at most 5 sentences.
   */
  private constrainSummary(summary: string): string {
    // Split on sentence-ending punctuation followed by a space or end of string
    const sentences = summary.match(/[^.!?]*[.!?]+/g);
    if (!sentences || sentences.length <= 5) {
      return summary;
    }
    return sentences.slice(0, 5).join('').trim();
  }
}
