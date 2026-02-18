export interface VisualizationData {
    type: string;
    templateId?: string;
    data: any;
  }
  
  export interface AgentVisualization {
    agentName: string;
    displayName: string;
    timestamp: Date;
    visualizations: VisualizationData[];
  }