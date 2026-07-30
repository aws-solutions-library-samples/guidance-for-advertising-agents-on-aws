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
   * using a two-phase approach with Claude Haiku 4.5:
   *
   * Phase 1 (Selection): Send only template IDs + usage descriptions to Haiku,
   *   ask which templates are relevant for the response. This avoids flooding
   *   the token limit with full schemas.
   *
   * Phase 2 (Generation): For each selected template, send the full schema
   *   individually and generate visualization data one at a time.
   *
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

      // 3. PHASE 1: Ask Haiku which templates are relevant (lightweight — descriptions only)
      console.log(`VisualizationAnalyzer: Phase 1 — selecting relevant templates from ${templateSchemas.length} candidates for ${agentName}`);
      const selectionResult = await this.selectRelevantTemplates(responseText, templateSchemas);

      if (!selectionResult || selectionResult.selectedTemplateIds.length === 0) {
        console.log(`VisualizationAnalyzer: No templates selected for ${agentName}, returning summary only`);
        return {
          summary: selectionResult?.summary || '',
          visualizations: [],
          originalText: responseText,
          questions: selectionResult?.questions || []
        };
      }

      // Filter to only selected templates
      const selectedSchemas = templateSchemas.filter(
        t => selectionResult.selectedTemplateIds.includes(t.templateId)
      );
      console.log(`VisualizationAnalyzer: Phase 1 selected ${selectedSchemas.length} templates: ${selectionResult.selectedTemplateIds.join(', ')}`);

      // 4. PHASE 2: Generate visualization data for each selected template individually
      console.log(`VisualizationAnalyzer: Phase 2 — generating data for ${selectedSchemas.length} templates`);
      const visualizations: { visualizationType: string; templateId: string; data: any }[] = [];

      for (const schema of selectedSchemas) {
        try {
          const vizData = await this.generateVisualizationForTemplate(responseText, schema);
          if (vizData) {
            visualizations.push(vizData);
          }
        } catch (err) {
          console.warn(`⚠️ VisualizationAnalyzer: Failed to generate data for ${schema.templateId}:`, err);
        }
      }

      console.log(`✅ VisualizationAnalyzer: Generated ${visualizations.length} visualizations for ${agentName}`);
      return {
        summary: selectionResult.summary,
        visualizations,
        originalText: responseText,
        questions: selectionResult.questions || []
      };

    } catch (error) {
      console.error(`❌ VisualizationAnalyzer: Analysis failed for ${agentName}:`, error);
      return null;
    }
  }

  /**
   * Phase 1: Ask Haiku which templates are relevant for the given response text.
   * Only sends template IDs and usage descriptions — NOT full schemas.
   * Also extracts a summary and questions from the response.
   */
  private async selectRelevantTemplates(
    responseText: string,
    templateSchemas: { templateId: string; usage: string; schema: any }[]
  ): Promise<{ selectedTemplateIds: string[]; summary: string; questions: string[] } | null> {
    const templateList = templateSchemas.map((t, i) =>
      `${i + 1}. templateId: "${t.templateId}" — ${t.usage}`
    ).join('\n');

    const prompt = `You are a visualization selection engine. Given an agent response and a list of available visualization templates (with their usage descriptions), determine which templates are appropriate to visualize the data in the response. Your ONLY output is a single JSON object.

## Agent Response Text
${responseText}

## Available Templates
${templateList}

## Output Format
Return ONLY a JSON object — no other text:
{
  "selectedTemplateIds": ["templateId1", "templateId2"],
  "summary": "1-2 sentence summary of the agent response.",
  "questions": ["Any user-directed questions extracted from the response"]
}

## Rules
- Select ONLY templates whose usage description matches data actually present in the response.
- Select at most 3 templates — prefer the most informative ones.
- If no template fits the response data, return an empty selectedTemplateIds array.
- Summary: 1-2 sentences max.
- Questions: extract questions directed at the user that require a response. Exclude rhetorical questions. Return empty array if none.
- The first character of your response MUST be { and the last must be }.`;

    try {
      const claudeResponse = await this.bedrockService.invokeClaudeHaiku(prompt, 1000);
      const jsonStr = this.extractJsonFromResponse(claudeResponse);
      if (!jsonStr) return null;

      const parsed = JSON.parse(jsonStr);
      const selectedTemplateIds: string[] = Array.isArray(parsed.selectedTemplateIds)
        ? parsed.selectedTemplateIds.filter((id: any) => typeof id === 'string')
        : [];
      const summary: string = typeof parsed.summary === 'string' ? parsed.summary : '';
      const questions: string[] = Array.isArray(parsed.questions)
        ? parsed.questions.filter((q: any) => typeof q === 'string' && q.trim().length > 0)
        : [];

      return { selectedTemplateIds, summary, questions };
    } catch (error) {
      console.error('VisualizationAnalyzer: Phase 1 selection failed:', error);
      return null;
    }
  }

  /**
   * Phase 2: Generate visualization data for a single template.
   * Sends the full schema for ONE template at a time to avoid token flooding.
   */
  private async generateVisualizationForTemplate(
    responseText: string,
    template: { templateId: string; usage: string; schema: any }
  ): Promise<{ visualizationType: string; templateId: string; data: any } | null> {
    const prompt = `You are a data extraction engine for a dashboard visualization card. Your ONLY output is a single JSON object. Extract ONLY key numbers and short labels from the agent response. This data will render in a small UI card — brevity is critical.

## Agent Response Text
${responseText}

## Template to Populate
Template ID: ${template.templateId}
Usage: ${template.usage}
Schema:
\`\`\`json
${JSON.stringify(template.schema, null, 2)}
\`\`\`

## Output Format
Return ONLY a JSON object — no other text:
{
  "visualizationType": "from template schema",
  "templateId": "${template.templateId}",
  "data": { ... populated template data ... }
}

## ABSOLUTE RULES
- Your ENTIRE response must be a single valid JSON object.
- Do NOT wrap it in markdown code blocks.
- The first character MUST be { and the last must be }.
- Each insight/recommendation/risk: MAX 8 WORDS.
- Labels and names: 2-4 words max.
- No narrative text. Numbers and short phrases only.
- Max 5 allocation items.
- Max 4 metric cards.
- Max 2 insights, 2 recommendations, 2 risks per item.
- Max 3 subMetrics per metric card.
- "insights", "risks", "recommendations", "items", "metrics", "subMetrics", "allocations", "channels", "segments" → ALWAYS arrays.
- percentage, budget, value, score → numbers, not strings.
- Do NOT put "visualizationType" or "templateId" inside "data".
- Extract real values from the response text — no placeholders.`;

    try {
      const claudeResponse = await this.bedrockService.invokeClaudeHaiku(prompt, 4000);
      const jsonStr = this.extractJsonFromResponse(claudeResponse);
      if (!jsonStr) return null;

      const parsed = JSON.parse(jsonStr);

      if (!parsed.data || !parsed.templateId) return null;

      return {
        visualizationType: parsed.visualizationType || parsed.data?.visualizationType || template.templateId,
        templateId: parsed.templateId,
        data: this.normalizeVisualizationData(parsed.data)
      };
    } catch (error) {
      console.error(`VisualizationAnalyzer: Phase 2 generation failed for ${template.templateId}:`, error);
      return null;
    }
  }

  /**
   * Retrieve full template schemas from DynamoDB for each template in the mapping.
   *
   * Lookup falls back through two tiers per template:
   *   1. Agent-specific template at `VIZ_TEMPLATE#{agentName}` / `{templateId}`.
   *   2. Generic template at `VIZ_TEMPLATE#_GENERIC` / `{templateId}` —
   *      already handled inside `getVisualizationTemplate`.
   *
   * If both miss, the template is skipped with a warning. This prevents a single
   * missing mapping (common after a template-id rename or partial upload) from
   * nuking the entire visualization pipeline for that agent.
   */
  private async retrieveTemplateSchemas(
    agentName: string,
    templates: { templateId: string; usage: string }[]
  ): Promise<{ templateId: string; usage: string; schema: any }[]> {
    const schemas: { templateId: string; usage: string; schema: any }[] = [];

    for (const template of templates) {
      try {
        // getVisualizationTemplate already falls back to VIZ_TEMPLATE#_GENERIC
        // when the agent-specific record is missing.
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
        } else {
          console.warn(
            `⚠️ VisualizationAnalyzer: No template ${template.templateId} found for ${agentName} ` +
            `(checked agent-specific and generic). Skipping this template.`
          );
        }
      } catch (error) {
        console.warn(`⚠️ VisualizationAnalyzer: Failed to retrieve template ${template.templateId}:`, error);
      }
    }

    return schemas;
  }

  /**
   * @deprecated Use the two-phase approach (selectRelevantTemplates + generateVisualizationForTemplate) instead.
   * This single-prompt method can exceed Haiku's token limit when many templates are configured.
   * Kept for reference only.
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

    return `You are a data extraction engine for dashboard visualization cards. Your ONLY output is a single JSON object. Do NOT include any explanation, commentary, or text outside the JSON. Extract ONLY key numbers and short labels from the agent response. This data will render in small UI cards — brevity is critical.

## Agent Response Text
${responseText}

## Available Visualization Templates
${templateDescriptions}

## Output Format
Return ONLY a JSON object — absolutely no other text before or after it:
{
  "summary": "1-2 sentence summary.",
  "visualizations": [
    {
      "visualizationType": "from template schema",
      "templateId": "the templateId",
      "data": { ... populated template data ... }
    }
  ],
  "questions": ["What budget range would you prefer?", "Should we prioritize reach or frequency?"]
}

## ABSOLUTE RULES — VIOLATING ANY OF THESE IS A FAILURE

### Output format (most important)
- Your ENTIRE response must be a single valid JSON object.
- Do NOT wrap it in markdown code blocks.
- Do NOT include any text before or after the JSON.
- The first character of your response MUST be { and the last must be }.

### Brevity
- Summary: 1-2 sentences max.
- Each insight/recommendation/risk: MAX 8 WORDS.
- Labels and names: 2-4 words max.
- No narrative text anywhere. Numbers and short phrases only.

### Limits
- Max 5 allocation items per visualization.
- Max 4 metric cards per visualization.
- Max 2 insights, 2 recommendations, 2 risks per item.
- Max 3 subMetrics per metric card.

### Types
- "insights", "risks", "recommendations", "items", "metrics", "subMetrics", "allocations", "channels", "segments" → ALWAYS arrays, NEVER strings.
- percentage, budget, value, score → numbers, not strings.
- Do NOT put "visualizationType" or "templateId" inside "data".

### Content
- Extract real values from the response text — no placeholders.
- Only include templates that have enough data to populate.
- Empty visualizations array if nothing fits.`;
  }

  /**
   * Parse Claude Haiku 4.5 response into a VisualizationAnalysisResult.
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

        // Extract and validate questions array
        const questions: string[] = Array.isArray(parsed.questions)
          ? parsed.questions.filter((q: any) => typeof q === 'string' && q.trim().length > 0)
          : [];

        return {
          summary,
          visualizations: validVisualizations,
          originalText,
          questions
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
    if (!response || response.trim().length === 0) return null;

    // Try extracting from ```json ... ``` code block first
    const codeBlockMatch = response.match(/```(?:json)?\s*([\s\S]*?)\s*```/);
    if (codeBlockMatch) {
      const candidate = codeBlockMatch[1].trim();
      // Only return if it actually looks like JSON
      if (candidate.startsWith('{') || candidate.startsWith('[')) {
        return candidate;
      }
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
     * Fields that Angular binds to [style.color] or [style.background].
     * These MUST be strings (or undefined) — never numbers, booleans, or objects —
     * otherwise Angular's DomSanitizer calls .toLowerCase() and crashes.
     */
    private static readonly STYLE_STRING_FIELDS = new Set([
      'color', 'statusColor', 'trendColor', 'background', 'backgroundColor',
      'trend', 'confidenceLevel', 'confidence', 'riskLevel', 'impact',
      'unit', 'label', 'primaryLabel', 'secondaryLabel', 'category',
      'name', 'segment', 'product_line', 'title', 'subtitle'
    ]);

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

        // Coerce style-bound fields to string (or undefined) so Angular's
        // DomSanitizer doesn't crash calling .toLowerCase() on a non-string
        if (VisualizationAnalyzerService.STYLE_STRING_FIELDS.has(key)) {
          const v = normalized[key];
          if (v !== null && v !== undefined && typeof v !== 'string') {
            normalized[key] = String(v);
          }
        }
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
