import { Component, Input, OnInit, OnDestroy, ChangeDetectionStrategy, ChangeDetectorRef, OnChanges, SimpleChanges, ViewChild, ElementRef } from '@angular/core';

/**
 * Decision Tree Visualization Component
 * 
 * Displays strategic decision scenarios, outcome analysis, and risk assessment in an interactive
 * animated tree format with flowing particles and clickable nodes.
 * 
 * @example
 * ```html
 * <app-decision-tree-visualization [decisionTreeData]="decisionData"></app-decision-tree-visualization>
 * ```
 * 
 * @example JSON Structure:
 * ```json
 * {
 *   "visualizationType": "decision-tree",
 *   "title": "Campaign Budget Allocation Strategy",
 *   "subtitle": "Strategic Decision Analysis",
 *   "description": "Evaluate different budget allocation approaches for maximum ROI",
 *   "startNode": "Budget Decision",
 *   "nodes": [
 *     {
 *       "id": "start",
 *       "text": "Budget Decision",
 *       "type": "start",
 *       "x": 50,
 *       "y": 200,
 *       "description": "Initial budget allocation decision point",
 *       "probability": 1.0,
 *       "impact": "High",
 *       "riskLevel": 5
 *     },
 *     {
 *       "id": "premium-focus",
 *       "text": "Premium Inventory Focus",
 *       "type": "good",
 *       "x": 200,
 *       "y": 100,
 *       "description": "Allocate 70% budget to premium publishers",
 *       "probability": 0.8,
 *       "impact": "High",
 *       "riskLevel": 3,
 *       "cost": "$1.75M",
 *       "timeframe": "3 months",
 *       "expectedValue": "25% ROI increase",
 *       "potentialLoss": "Limited reach",
 *       "metrics": {
 *         "roi": "125%",
 *         "timeToValue": "6 weeks"
 *       },
 *       "mitigation": "Supplement with targeted programmatic"
 *     },
 *     {
 *       "id": "balanced-approach",
 *       "text": "Balanced Portfolio",
 *       "type": "neutral",
 *       "x": 200,
 *       "y": 200,
 *       "description": "50/50 split between premium and programmatic",
 *       "probability": 0.9,
 *       "impact": "Medium",
 *       "riskLevel": 4,
 *       "cost": "$2.5M",
 *       "timeframe": "4 months",
 *       "expectedValue": "15% ROI increase",
 *       "metrics": {
 *         "roi": "115%",
 *         "timeToValue": "8 weeks"
 *       }
 *     },
 *     {
 *       "id": "programmatic-heavy",
 *       "text": "Programmatic Focus",
 *       "type": "bad",
 *       "x": 200,
 *       "y": 300,
 *       "description": "70% programmatic, 30% premium",
 *       "probability": 0.6,
 *       "impact": "Low",
 *       "riskLevel": 7,
 *       "cost": "$2.2M",
 *       "expectedValue": "5% ROI increase",
 *       "potentialLoss": "Brand safety risks",
 *       "mitigation": "Enhanced brand safety filters"
 *     }
 *   ],
 *   "connections": [
 *     {
 *       "from": "start",
 *       "to": "premium-focus",
 *       "label": "High Quality",
 *       "condition": "Focus on brand safety and premium reach",
 *       "probability": 0.8
 *     },
 *     {
 *       "from": "start",
 *       "to": "balanced-approach",
 *       "label": "Balanced Risk",
 *       "condition": "Optimize for reach and efficiency",
 *       "probability": 0.9
 *     },
 *     {
 *       "from": "start",
 *       "to": "programmatic-heavy",
 *       "label": "Cost Efficiency",
 *       "condition": "Maximize reach within budget constraints",
 *       "probability": 0.6
 *     }
 *   ],
 *   "parameters": {
 *     "riskFactor": 4,
 *     "budgetLevel": 8,
 *     "timeframe": "Q1 2025",
 *     "confidenceLevel": "High"
 *   },
 *   "summary": {
 *     "recommendedPath": "Premium Inventory Focus",
 *     "expectedValue": "25% ROI increase",
 *     "riskScore": "Low-Medium",
 *     "decisionConfidence": "High"
 *   }
 * }
 * ```
 * 
 * @features
 * - Interactive animated decision tree with flowing particles
 * - Clickable nodes with detailed information panels
 * - Color-coded node types (start, good, bad, neutral)
 * - Risk level visualization with color gradients
 * - Probability and impact indicators
 * - Connection labels with conditions
 * - Responsive SVG-based layout
 * 
 * @useCases
 * - Strategic scenario planning
 * - Risk/reward analysis
 * - Campaign strategy decisions
 * - Budget allocation choices
 * - Optimization path selection
 */

interface DecisionNode {
  id: string;
  text: string;
  type: 'start' | 'good' | 'bad' | 'neutral';
  x: number;
  y: number;
  description?: string;
  probability?: number;
  impact?: string;
  riskLevel?: number;
}

interface DecisionConnection {
  from: string;
  to: string;
  condition?: string;
  probability?: number;
}

interface DecisionTreeData {
  title: string;
  description?: string;
  startNode: string;
  nodes: DecisionNode[];
  connections: DecisionConnection[];
  parameters?: {
    riskFactor?: number;
    budgetLevel?: number;
    timeframe?: string;
  };
}

@Component({
  selector: 'app-decision-tree-visualization',
  templateUrl: './decision-tree-visualization.component.html',
  styleUrls: ['./decision-tree-visualization.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class DecisionTreeVisualizationComponent implements OnInit, OnDestroy, OnChanges {
  @Input() decisionTreeData: DecisionTreeData | null = null;
  @ViewChild('treeContainer', { static: false }) treeContainer?: ElementRef<HTMLDivElement>;

  selectedNode: string | null = null;
  animationInterval: any;
  private activeParticles = new Set<HTMLElement>();
  connections: Array<{
    from: string;
    to: string;
    fromPos: { x: number; y: number };
    toPos: { x: number; y: number };
    length: number;
    angle: number;
    probability?: number;
  }> = [];

  constructor(private cdr: ChangeDetectorRef) { }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['decisionTreeData']) {
      this.cleanup();
      if (this.decisionTreeData) {
        this.calculateConnections();
        // Delay animation start to ensure DOM is ready
        setTimeout(() => this.startAnimation(), 100);
      }
      this.cdr.markForCheck();
    }
  }

  ngOnInit() {
    if (this.decisionTreeData) {
      this.calculateConnections();
      this.startAnimation();
    }
  }

  ngOnDestroy() {
    this.cleanup();
  }

  private cleanup() {
    // Clear interval
    if (this.animationInterval) {
      clearInterval(this.animationInterval);
      this.animationInterval = null;
    }

    // Remove all active particles
    this.activeParticles.forEach(particle => {
      if (particle.parentNode) {
        particle.parentNode.removeChild(particle);
      }
    });
    this.activeParticles.clear();
  }

  // TrackBy functions
  trackByNodeId(index: number, node: DecisionNode): string {
    return node.id;
  }

  trackByConnectionIndex(index: number): number {
    return index;
  }

  calculateConnections() {
    if (!this.decisionTreeData) return;

    this.connections = this.decisionTreeData.connections.map(conn => {
      const fromNode = this.decisionTreeData!.nodes.find(n => n.id === conn.from);
      const toNode = this.decisionTreeData!.nodes.find(n => n.id === conn.to);

      if (!fromNode || !toNode) {
        return {
          from: conn.from,
          to: conn.to,
          fromPos: { x: 0, y: 0 },
          toPos: { x: 0, y: 0 },
          length: 0,
          angle: 0,
          probability: conn.probability
        };
      }

      const fromPos = { x: fromNode.x + 40, y: fromNode.y + 40 }; // Center of node
      const toPos = { x: toNode.x + 40, y: toNode.y + 40 };

      const length = Math.sqrt(Math.pow(toPos.x - fromPos.x, 2) + Math.pow(toPos.y - fromPos.y, 2));
      const angle = Math.atan2(toPos.y - fromPos.y, toPos.x - fromPos.x) * 180 / Math.PI;

      return {
        from: conn.from,
        to: conn.to,
        fromPos,
        toPos,
        length,
        angle,
        probability: conn.probability
      };
    });
  }

  startAnimation() {
    // Don't start if already running
    if (this.animationInterval) return;

    let particleId = 0;
    this.animationInterval = setInterval(() => {
      // Limit number of active particles to prevent memory issues
      if (this.activeParticles.size < 5) {
        this.createFlowParticle(particleId++);
      }
    }, 2000);
  }

  createFlowParticle(id: number) {
    if (this.connections.length === 0 || !this.treeContainer) return;

    const randomConnection = this.connections[Math.floor(Math.random() * this.connections.length)];
    
    // Create particle element
    const particle = document.createElement('div');
    particle.className = 'flow-particle';
    particle.id = `particle-${id}`;
    particle.style.left = `${randomConnection.fromPos.x}px`;
    particle.style.top = `${randomConnection.fromPos.y}px`;

    const container = this.treeContainer.nativeElement;
    if (container) {
      container.appendChild(particle);
      this.activeParticles.add(particle);

      // Animate to destination
      const animationTimeout = setTimeout(() => {
        particle.style.left = `${randomConnection.toPos.x}px`;
        particle.style.top = `${randomConnection.toPos.y}px`;
        particle.style.opacity = '1';
      }, 50);

      // Fade out particle
      const fadeTimeout = setTimeout(() => {
        particle.style.opacity = '0';
        
        // Remove particle after fade
        const removeTimeout = setTimeout(() => {
          if (particle.parentNode) {
            particle.parentNode.removeChild(particle);
          }
          this.activeParticles.delete(particle);
        }, 300);
      }, 1500);
    }
  }

  selectNode(nodeId: string) {
    this.selectedNode = nodeId;
    this.cdr.markForCheck();
  }

  getSelectedNodeInfo() {
    if (!this.selectedNode || !this.decisionTreeData) return null;

    const node = this.decisionTreeData.nodes.find(n => n.id === this.selectedNode);
    if (!node) return null;

    const incomingConnections = this.decisionTreeData.connections.filter(c => c.to === this.selectedNode);
    const outgoingConnections = this.decisionTreeData.connections.filter(c => c.from === this.selectedNode);

    return {
      node,
      incomingConnections,
      outgoingConnections
    };
  }

  getNodeTypeClass(type: string): string {
    switch (type) {
      case 'good': return 'node-good';
      case 'bad': return 'node-bad';
      case 'neutral': return 'node-neutral';
      case 'start': return 'node-start';
      default: return 'node-neutral';
    }
  }

  getConnectionClass(connection: any): string {
    const toNode = this.decisionTreeData?.nodes.find(n => n.id === connection.to);
    if (!toNode) return 'connection-neutral';

    switch (toNode.type) {
      case 'good': return 'connection-good';
      case 'bad': return 'connection-bad';
      default: return 'connection-neutral';
    }
  }

  getRiskColor(riskLevel?: number): string {
    if (!riskLevel) return '#6b7280';
    
    if (riskLevel <= 3) return '#059669';
    if (riskLevel <= 7) return '#f59e0b';
    return '#ef4444';
  }

  formatProbability(probability?: number): string {
    if (!probability) return '';
    return `${Math.round(probability * 100)}%`;
  }
} 