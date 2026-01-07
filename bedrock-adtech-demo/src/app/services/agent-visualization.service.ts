import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

interface VisualizationData {
  type: string;
  templateId?: string;
  data: any;
}

interface AgentVisualization {
  agentName: string;
  displayName: string;
  timestamp: Date;
  visualData:any
}

@Injectable({
  providedIn: 'root'
})
export class AgentVisualizationService {
  private agentVisualizations = new Map<string, AgentVisualization>();
  private visualizationsSubject = new BehaviorSubject<Map<string, AgentVisualization>>(new Map());
  public visualizations$ = this.visualizationsSubject.asObservable();

  constructor() { }

  // Add or update visualization data for an agent
  updateAgentVisualization(
    agentName: string,
    displayName: string,
    visualData: {
      metricData?: any;
      channelAllocations?: any;
      channelCards?: any;
      segmentCards?: any;
      creativeData?: any;
      histogramData?: any;
      doubleHistogramData?: any;
      barChartData?: any;
      donutChartData?: any;
      timelineData?: any;
      decisionTreeData?: any;
      // ADCP-specific visualizations
      adcpInventoryData?: any;      // get_products results
      adcpMediaBuyData?: any;       // create_media_buy results
      adcpDeliveryData?: any;       // get_media_buy_delivery results
      adcpSignalsData?: any;        // get_signals results
    }
  ): void {
    // Only update if there's actual visualization data
    const hasData = visualData.metricData ||
      visualData.channelAllocations ||
      visualData.channelCards ||
      visualData.segmentCards ||
      visualData.creativeData ||
      visualData.histogramData ||
      visualData.doubleHistogramData ||
      visualData.barChartData ||
      visualData.donutChartData ||
      visualData.timelineData ||
      visualData.decisionTreeData ||
      visualData.adcpInventoryData ||
      visualData.adcpMediaBuyData ||
      visualData.adcpDeliveryData ||
      visualData.adcpSignalsData;

    if (!hasData) {
      return;
    }

    const existingVisualization = this.agentVisualizations.get(displayName);

    const visualization: AgentVisualization = {
      agentName,
      displayName,
      timestamp: new Date(),
      visualData: {
        ...existingVisualization?.visualData,
        ...visualData
      }
    };

    this.agentVisualizations.set(displayName, visualization);
    this.visualizationsSubject.next(new Map(this.agentVisualizations));

  }

  // Get visualization data for a specific agent
  getAgentVisualization(displayName: string): AgentVisualization | undefined {
    return this.agentVisualizations.get(displayName);
  }

  // Get all agents that have visualizations
  getAgentsWithVisualizations(): string[] {
    return Array.from(this.agentVisualizations.keys());
  }

  // Check if an agent has any visualizations
  hasVisualizationData(displayName: string): boolean {
    const visualization = this.agentVisualizations.get(displayName);
    if (!visualization) {
      return false;
    }

    const { visualData } = visualization;
    return !!(
      visualData.metricData ||
      visualData.channelAllocations ||
      visualData.allocations ||
      visualData.channels ||
      visualData.channelCards ||
      visualData.segmentCards ||
      visualData.creativeData ||
      visualData.histogramData ||
      visualData.doubleHistogramData ||
      visualData.barChartData ||
      visualData.donutChartData ||
      visualData.timelineData ||
      visualData.decisionTreeData
    );
  }

  // Clear visualization data for a specific agent
  clearAgentVisualization(displayName: string): void {
    this.agentVisualizations.delete(displayName);
    this.visualizationsSubject.next(new Map(this.agentVisualizations));
  }

  // Clear all visualization data
  clearAllVisualizations(): void {
    this.agentVisualizations.clear();
    this.visualizationsSubject.next(new Map());
  }

  // Get count of visualization types for an agent
  getVisualizationCount(displayName: string): number {
    const visualization = this.agentVisualizations.get(displayName);
    if (!visualization) return 0;

    const { visualData } = visualization;
    let count = 0;
    if (visualData.metricData) count++;
    if (visualData.channelAllocations) count++;
    if (visualData.channelCards) count++;
    if (visualData.segmentCards) count++;
    if (visualData.creativeData) count++;
    if (visualData.histogramData) count++;
    if (visualData.doubleHistogramData) count++;
    if (visualData.barChartData) count++;
    if (visualData.donutChartData) count++;
    if (visualData.timelineData) count++;
    if (visualData.decisionTreeData) count++;

    return count;
  }

  // Detect and extract D3 visualization data from JSON content
  detectD3Visualizations(content: string): {
    metricData?: any;
    histogramData?: any;
    doubleHistogramData?: any;
    barChartData?: any;
    donutChartData?: any;
    timelineData?: any;
    decisionTreeData?: any;
  } {
    const visualizations: any = {};

    try {
      // First, handle XML-wrapped visualizations (with or without closing tags)
      const xmlVisualizationRegex = /<visualization-data[^>]*type="([^"]+)"[^>]*>\s*(\{[\s\S]*?)(?:<\/visualization-data>|$)/g;
      let xmlMatch;

      while ((xmlMatch = xmlVisualizationRegex.exec(content)) !== null) {
        const visualizationType = xmlMatch[1];
        const jsonContent = xmlMatch[2].trim();

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

          // Ensure visualizationType is set
          if (!parsed.visualizationType) {
            parsed.visualizationType = visualizationType;
          }

          // Map to appropriate visualization type
          this.mapVisualizationType(parsed, visualizations);
        } catch (parseError) {
          console.warn('Failed to parse XML-wrapped visualization JSON:', parseError);
        }
      }

      // Look for JSON blocks that contain D3 visualization types
      const jsonBlocks = this.extractJsonBlocks(content);
      
      for (const jsonBlock of jsonBlocks) {
        try {
          const parsed = JSON.parse(jsonBlock);
          this.mapVisualizationType(parsed, visualizations);
        } catch (parseError) {
          // Skip invalid JSON blocks
          continue;
        }
      }
    } catch (error) {
      console.warn('Error detecting D3 visualizations:', error);
    }

    return visualizations;
  }

  // Helper method to map visualization types to the appropriate property
  private mapVisualizationType(parsed: any, visualizations: any): void {
    // Check for metrics visualization
    if (parsed.visualizationType === 'metrics') {
      visualizations.metricData = parsed;
    }
    
    // Check for histogram visualization
    if (parsed.visualizationType === 'histogram') {
      visualizations.histogramData = parsed;
    }
    
    // Check for double histogram visualization
    if (parsed.visualizationType === 'double-histogram') {
      visualizations.doubleHistogramData = parsed;
    }
    
    // Check for bar chart visualization
    if (parsed.visualizationType === 'bar-chart') {
      visualizations.barChartData = parsed;
    }
    
    // Check for donut chart visualization
    if (parsed.visualizationType === 'donut-chart') {
      visualizations.donutChartData = parsed;
    }
    
    // Check for timeline visualization
    if (parsed.visualizationType === 'timeline') {
      visualizations.timelineData = parsed;
    }
    
    // Check for decision tree visualization
    if (parsed.visualizationType === 'decision-tree') {
      visualizations.decisionTreeData = parsed;
    }
  }

  // Extract JSON blocks from content
  private extractJsonBlocks(content: string): string[] {
    const jsonBlocks: string[] = [];
    
    // Look for JSON code blocks
    const codeBlockRegex = /```json\s*([\s\S]*?)\s*```/g;
    let match;
    
    while ((match = codeBlockRegex.exec(content)) !== null) {
      jsonBlocks.push(match[1].trim());
    }
    
    // Also look for inline JSON objects that might contain visualizationType
    const inlineJsonRegex = /\{[^{}]*"visualizationType"[^{}]*\}/g;
    while ((match = inlineJsonRegex.exec(content)) !== null) {
      jsonBlocks.push(match[0]);
    }
    
    // Look for larger JSON objects that span multiple lines
    const multilineJsonRegex = /\{[\s\S]*?"visualizationType"[\s\S]*?\}/g;
    while ((match = multilineJsonRegex.exec(content)) !== null) {
      jsonBlocks.push(match[0]);
    }

    // Look for templateId patterns with -visualization suffix
    const templateIdRegex = /\{[\s\S]*?"templateId"[\s\S]*?"-visualization"[\s\S]*?\}/g;
    while ((match = templateIdRegex.exec(content)) !== null) {
      jsonBlocks.push(match[0]);
    }
    
    return jsonBlocks;
  }
} 