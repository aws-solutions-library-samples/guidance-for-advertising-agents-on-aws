import { Component, Input, OnInit, OnDestroy, ElementRef, ViewChild, AfterViewInit } from '@angular/core';
import * as d3 from 'd3';

/**
 * Donut Chart Visualization Component
 * 
 * Displays proportional data with a central summary using D3.js donut chart.
 * Features interactive segments, customizable colors, and detailed tooltips.
 * 
 * @example
 * ```html
 * <app-donut-chart-visualization [data]="donutChartData"></app-donut-chart-visualization>
 * ```
 * 
 * @example JSON Structure:
 * ```json
 * {
 *   "visualizationType": "donut-chart",
 *   "title": "Budget Allocation Breakdown",
 *   "subtitle": "Q4 2024 Campaign Distribution",
 *   "centerText": {
 *     "primary": "$2.5M",
 *     "secondary": "Total Budget"
 *   },
 *   "data": [
 *     {
 *       "category": "Digital Video",
 *       "value": 1125000,
 *       "percentage": 45,
 *       "color": "#3b82f6",
 *       "label": "Digital Video Advertising",
 *       "metadata": {
 *         "description": "Premium video inventory across top publishers",
 *         "performance": "high",
 *         "trend": "up"
 *       }
 *     },
 *     {
 *       "category": "Social Media",
 *       "value": 750000,
 *       "percentage": 30,
 *       "color": "#10b981",
 *       "label": "Social Media Campaigns",
 *       "metadata": {
 *         "description": "Multi-platform social advertising",
 *         "performance": "medium",
 *         "trend": "stable"
 *       }
 *     },
 *     {
 *       "category": "Display",
 *       "value": 375000,
 *       "percentage": 15,
 *       "color": "#f59e0b",
 *       "label": "Display Advertising",
 *       "metadata": {
 *         "description": "Banner and rich media placements",
 *         "performance": "medium",
 *         "trend": "down"
 *       }
 *     },
 *     {
 *       "category": "Search",
 *       "value": 250000,
 *       "percentage": 10,
 *       "color": "#ef4444",
 *       "label": "Search Marketing",
 *       "metadata": {
 *         "description": "Paid search and shopping campaigns",
 *         "performance": "high",
 *         "trend": "up"
 *       }
 *     }
 *   ],
 *   "colorScheme": "custom",
 *   "showPercentages": true,
 *   "showLegend": true,
 *   "innerRadius": 0.6,
 *   "insights": [
 *     "Digital video dominates allocation with strong performance",
 *     "Social media maintains steady investment",
 *     "Search shows high efficiency despite smaller allocation"
 *   ],
 *   "summary": {
 *     "total": 2500000,
 *     "segments": 4,
 *     "topPerformer": "Digital Video"
 *   }
 * }
 * ```
 * 
 * @features
 * - Interactive donut chart with hover effects
 * - Customizable inner radius and colors
 * - Central text display for totals
 * - Legend with performance indicators
 * - Percentage labels on segments
 * - Detailed tooltips with metadata
 * - Agent color palette support
 * 
 * @useCases
 * - Budget allocation breakdown
 * - Audience segment distribution
 * - Channel mix visualization
 * - Creative format distribution
 * - Campaign performance shares
 */

interface DonutChartDataPoint {
  category: string;
  value: number;
  percentage: number;
  color?: string;
  label: string;
  metadata?: {
    description: string;
    performance: 'high' | 'medium' | 'low';
    trend: 'up' | 'down' | 'stable';
  };
}

interface DonutChartVisualization {
  visualizationType: 'donut-chart';
  title: string;
  subtitle?: string;
  centerText: {
    primary: string;
    secondary: string;
  };
  data: DonutChartDataPoint[];
  colorScheme: 'agent-palette' | 'custom';
  showPercentages: boolean;
  showLegend: boolean;
  innerRadius: number;
  insights?: string[];
  summary?: {
    total: number;
    segments: number;
    topPerformer: string;
  };
}

@Component({
  selector: 'app-donut-chart-visualization',
  template: `
    <div class="donut-chart-container">
      <div class="donut-chart-header">
        <h3 class="donut-chart-title">{{ data.title }}</h3>
        <p class="donut-chart-subtitle" *ngIf="data.subtitle">{{ data.subtitle }}</p>
      </div>
      <div class="donut-chart-content">
        <div class="donut-chart-chart" #chartContainer></div>
        <div class="donut-legend" *ngIf="data.showLegend">
          <div class="legend-item" *ngFor="let item of data.data">
            <div class="legend-color" [style.background-color]="getItemColor(item, data.data.indexOf(item))"></div>
            <div class="legend-text">
              <span class="legend-label">{{ item.label }}</span>
              <span class="legend-value">{{ item.percentage }}%</span>
            </div>
          </div>
        </div>
      </div>
      <div class="donut-chart-insights" *ngIf="data.insights && data.insights.length > 0">
        <h4>Key Insights</h4>
        <ul>
          <li *ngFor="let insight of data.insights">{{ insight }}</li>
        </ul>
      </div>
      <div class="donut-chart-summary" *ngIf="data.summary">
        <div class="summary-stats">
          <div class="stat-item">
            <span class="stat-label">Total:</span>
            <span class="stat-value">{{ data.summary.total | number:'1.0-0' }}</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">Segments:</span>
            <span class="stat-value">{{ data.summary.segments }}</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">Top Performer:</span>
            <span class="stat-value">{{ data.summary.topPerformer }}</span>
          </div>
        </div>
      </div>
    </div>
  `,
  styleUrls: ['./donut-chart-visualization.component.scss']
})
export class DonutChartVisualizationComponent implements OnInit, AfterViewInit, OnDestroy {
  @Input() data!: DonutChartVisualization;
  @ViewChild('chartContainer', { static: false }) chartContainer!: ElementRef;

  private svg: any;
  private width = 400;
  private height = 400;
  private radius = Math.min(this.width, this.height) / 2;

  // Agent color palette
  private colors = [
    '#6842ff', // Primary purple
    '#ff6200', // Orange
    '#c300e0', // Magenta
    '#007e94', // Teal
    '#10b981', // Green
    '#f59e0b', // Yellow
    '#ef4444', // Red
    '#8b5cf6'  // Light purple
  ];

  ngOnInit(): void {
    // Component initialization
  }

  ngAfterViewInit(): void {
    this.createDonutChart();
  }

  ngOnDestroy(): void {
    if (this.svg) {
      this.svg.remove();
    }
  }

  getItemColor(item: DonutChartDataPoint, index: number): string {
    return item.color || (this.data.colorScheme === 'agent-palette' ? this.colors[index % this.colors.length] : '#6842ff');
  }

  private createDonutChart(): void {
    // Clear any existing chart
    d3.select(this.chartContainer.nativeElement).selectAll('*').remove();

    // Create SVG
    this.svg = d3.select(this.chartContainer.nativeElement)
      .append('svg')
      .attr('width', this.width)
      .attr('height', this.height);

    const g = this.svg.append('g')
      .attr('transform', `translate(${this.width / 2},${this.height / 2})`);

    // Create pie generator
    const pie = d3.pie<DonutChartDataPoint>()
      .value(d => d.value)
      .sort(null);

    // Create arc generator
    const outerRadius = this.radius - 10;
    const innerRadius = outerRadius * (this.data.innerRadius || 0.5);
    
    const arc = d3.arc<d3.PieArcDatum<DonutChartDataPoint>>()
      .innerRadius(innerRadius)
      .outerRadius(outerRadius);

    const hoverArc = d3.arc<d3.PieArcDatum<DonutChartDataPoint>>()
      .innerRadius(innerRadius)
      .outerRadius(outerRadius + 10);

    // Create arcs
    const arcs = g.selectAll('.arc')
      .data(pie(this.data.data))
      .enter().append('g')
      .attr('class', 'arc');

    // Add paths
    const paths = arcs.append('path')
      .attr('d', arc)
      .attr('fill', (d, i) => this.getItemColor(d.data, i))
      .attr('stroke', '#ffffff')
      .attr('stroke-width', 2)
      .style('opacity', 0.8)
      .style('cursor', 'pointer');

    // Add percentage labels if enabled
    if (this.data.showPercentages) {
      arcs.append('text')
        .attr('transform', d => `translate(${arc.centroid(d)})`)
        .attr('dy', '.35em')
        .style('text-anchor', 'middle')
        .style('font-size', '12px')
        .style('font-weight', '600')
        .style('fill', '#ffffff')
        .text(d => `${d.data.percentage}%`)
        .style('pointer-events', 'none');
    }

    // Add hover effects
    paths.on('mouseover', (event, d) => {
        d3.select(event.currentTarget)
          .transition()
          .duration(200)
          .attr('d', (datum: any) => hoverArc(datum))
          .style('opacity', 1);
        
        this.showTooltip(event, d.data);
      })
      .on('mouseout', (event, d) => {
        d3.select(event.currentTarget)
          .transition()
          .duration(200)
          .attr('d', (datum: any) => arc(datum))
          .style('opacity', 0.8);
        
        this.hideTooltip();
      });

    // Add center text
    const centerGroup = g.append('g')
      .attr('class', 'center-text');

    centerGroup.append('text')
      .attr('text-anchor', 'middle')
      .attr('dy', '-0.5em')
      .style('font-size', '18px')
      .style('font-weight', '600')
      .style('fill', '#1f2937')
      .text(this.data.centerText.primary);

    centerGroup.append('text')
      .attr('text-anchor', 'middle')
      .attr('dy', '1em')
      .style('font-size', '14px')
      .style('fill', '#6b7280')
      .text(this.data.centerText.secondary);
  }

  private showTooltip(event: any, d: DonutChartDataPoint): void {
    const tooltip = d3.select('body').append('div')
      .attr('class', 'donut-chart-tooltip')
      .style('opacity', 0)
      .style('position', 'absolute')
      .style('background', 'rgba(0, 0, 0, 0.8)')
      .style('color', 'white')
      .style('padding', '12px')
      .style('border-radius', '6px')
      .style('font-size', '12px')
      .style('pointer-events', 'none')
      .style('max-width', '200px');

    let tooltipContent = `<strong>${d.label}</strong><br/>Value: ${d.value.toLocaleString()}<br/>Percentage: ${d.percentage}%`;
    
    if (d.metadata) {
      tooltipContent += `<br/><br/>${d.metadata.description}`;
      tooltipContent += `<br/>Performance: ${d.metadata.performance}`;
      if (d.metadata.trend) {
        const trendIcon = d.metadata.trend === 'up' ? '↗' : d.metadata.trend === 'down' ? '↘' : '→';
        tooltipContent += `<br/>Trend: ${trendIcon} ${d.metadata.trend}`;
      }
    }

    tooltip.transition()
      .duration(200)
      .style('opacity', 1);
    
    tooltip.html(tooltipContent)
      .style('left', (event.pageX + 10) + 'px')
      .style('top', (event.pageY - 28) + 'px');
  }

  private hideTooltip(): void {
    d3.selectAll('.donut-chart-tooltip').remove();
  }
}