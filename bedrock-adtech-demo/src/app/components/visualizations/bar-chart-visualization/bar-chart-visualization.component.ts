import { Component, Input, OnInit, OnDestroy, ElementRef, ViewChild, AfterViewInit } from '@angular/core';
import * as d3 from 'd3';

/**
 * Bar Chart Visualization Component
 * 
 * Displays categorical data using horizontal or vertical bars with D3.js.
 * Supports custom colors, sorting, value display, and interactive tooltips.
 * 
 * @example
 * ```html
 * <app-bar-chart-visualization [data]="barChartData"></app-bar-chart-visualization>
 * ```
 * 
 * @example JSON Structure:
 * ```json
 * {
 *   "visualizationType": "bar-chart",
 *   "title": "Channel Performance Comparison",
 *   "subtitle": "Q4 2024 Campaign Results",
 *   "orientation": "horizontal",
 *   "xAxisLabel": "Performance Score",
 *   "yAxisLabel": "Channels",
 *   "data": [
 *     {
 *       "category": "Digital Video",
 *       "value": 85,
 *       "color": "#3b82f6",
 *       "label": "Digital Video Advertising",
 *       "metadata": {
 *         "description": "Premium video inventory performance",
 *         "trend": "up",
 *         "change": "+12% vs last quarter"
 *       }
 *     },
 *     {
 *       "category": "Social Media",
 *       "value": 72,
 *       "label": "Social Media Campaigns",
 *       "metadata": {
 *         "description": "Multi-platform social advertising",
 *         "trend": "stable",
 *         "change": "+2% vs last quarter"
 *       }
 *     },
 *     {
 *       "category": "Display",
 *       "value": 68,
 *       "label": "Display Advertising",
 *       "metadata": {
 *         "description": "Banner and rich media ads",
 *         "trend": "down",
 *         "change": "-5% vs last quarter"
 *       }
 *     }
 *   ],
 *   "colorScheme": "agent-palette",
 *   "showValues": true,
 *   "sortOrder": "descending",
 *   "insights": [
 *     "Digital video shows strongest performance",
 *     "Social media maintains steady growth",
 *     "Display advertising needs optimization"
 *   ],
 *   "summary": {
 *     "total": 225,
 *     "highest": "Digital Video",
 *     "lowest": "Display"
 *   }
 * }
 * ```
 * 
 * @features
 * - Horizontal and vertical bar orientations
 * - Custom color schemes or agent palette
 * - Interactive tooltips with metadata
 * - Sorting options (ascending, descending, none)
 * - Value labels on bars
 * - Responsive SVG rendering
 * 
 * @useCases
 * - Channel performance comparison
 * - Budget allocation by category
 * - Campaign metrics by segment
 * - Publisher performance rankings
 * - Creative format effectiveness
 */

interface BarChartDataPoint {
  category: string;
  value: number;
  color?: string;
  label: string;
  metadata?: {
    description: string;
    trend: 'up' | 'down' | 'stable';
    change: string;
  };
}

interface BarChartVisualization {
  visualizationType: 'bar-chart';
  title: string;
  subtitle?: string;
  orientation: 'horizontal' | 'vertical';
  xAxisLabel: string;
  yAxisLabel: string;
  data: BarChartDataPoint[];
  colorScheme: 'agent-palette' | 'custom';
  showValues: boolean;
  sortOrder: 'ascending' | 'descending' | 'none';
  insights?: string[];
  summary?: {
    total: number;
    highest: string;
    lowest: string;
  };
}

@Component({
  selector: 'app-bar-chart-visualization',
  template: `
    <div class="bar-chart-container">
      <div class="bar-chart-header">
        <h3 class="bar-chart-title">{{ data.title }}</h3>
        <p class="bar-chart-subtitle" *ngIf="data.subtitle">{{ data.subtitle }}</p>
      </div>
      <div class="bar-chart-chart" #chartContainer></div>
      <div class="bar-chart-insights" *ngIf="data.insights && data.insights.length > 0">
        <h4>Key Insights</h4>
        <ul>
          <li *ngFor="let insight of data.insights">{{ insight }}</li>
        </ul>
      </div>
      <div class="bar-chart-summary" *ngIf="data.summary">
        <div class="summary-stats">
          <div class="stat-item">
            <span class="stat-label">Total:</span>
            <span class="stat-value">{{ data.summary.total | number:'1.0-0' }}</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">Highest:</span>
            <span class="stat-value">{{ data.summary.highest }}</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">Lowest:</span>
            <span class="stat-value">{{ data.summary.lowest }}</span>
          </div>
        </div>
      </div>
    </div>
  `,
  styleUrls: ['./bar-chart-visualization.component.scss']
})
export class BarChartVisualizationComponent implements OnInit, AfterViewInit, OnDestroy {
  @Input() data!: BarChartVisualization;
  @ViewChild('chartContainer', { static: false }) chartContainer!: ElementRef;

  private svg: any;
  private margin = { top: 20, right: 30, bottom: 80, left: 80 };
  private width = 600 - this.margin.left - this.margin.right;
  private height = 400 - this.margin.top - this.margin.bottom;

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
    this.createBarChart();
  }

  ngOnDestroy(): void {
    if (this.svg) {
      this.svg.remove();
    }
  }

  private createBarChart(): void {
    // Clear any existing chart
    d3.select(this.chartContainer.nativeElement).selectAll('*').remove();

    // Sort data if needed
    let sortedData = [...this.data.data];
    if (this.data.sortOrder === 'ascending') {
      sortedData.sort((a, b) => a.value - b.value);
    } else if (this.data.sortOrder === 'descending') {
      sortedData.sort((a, b) => b.value - a.value);
    }

    // Create SVG
    this.svg = d3.select(this.chartContainer.nativeElement)
      .append('svg')
      .attr('width', this.width + this.margin.left + this.margin.right)
      .attr('height', this.height + this.margin.top + this.margin.bottom);

    const g = this.svg.append('g')
      .attr('transform', `translate(${this.margin.left},${this.margin.top})`);

    if (this.data.orientation === 'vertical') {
      this.createVerticalBarChart(g, sortedData);
    } else {
      this.createHorizontalBarChart(g, sortedData);
    }
  }

  private createVerticalBarChart(g: any, data: BarChartDataPoint[]): void {
    // Create scales
    const xScale = d3.scaleBand()
      .domain(data.map(d => d.category))
      .range([0, this.width])
      .padding(0.1);

    const yScale = d3.scaleLinear()
      .domain([0, d3.max(data, d => d.value) as number])
      .range([this.height, 0]);

    // Create bars
    const bars = g.selectAll('.bar')
      .data(data)
      .enter().append('rect')
      .attr('class', 'bar')
      .attr('x', (d: BarChartDataPoint) => xScale(d.category))
      .attr('width', xScale.bandwidth())
      .attr('y', (d: BarChartDataPoint) => yScale(d.value))
      .attr('height', (d: BarChartDataPoint) => this.height - yScale(d.value))
      .attr('fill', (d: BarChartDataPoint, i: number) => 
        d.color || (this.data.colorScheme === 'agent-palette' ? this.colors[i % this.colors.length] : '#6842ff')
      )
      .attr('stroke', '#ffffff')
      .attr('stroke-width', 1)
      .style('opacity', 0.8);

    // Add value labels if enabled
    if (this.data.showValues) {
      g.selectAll('.value-label')
        .data(data)
        .enter().append('text')
        .attr('class', 'value-label')
        .attr('x', (d: BarChartDataPoint) => xScale(d.category)! + xScale.bandwidth() / 2)
        .attr('y', (d: BarChartDataPoint) => yScale(d.value) - 5)
        .attr('text-anchor', 'middle')
        .style('font-size', '12px')
        .style('fill', '#374151')
        .text((d: BarChartDataPoint) => d.value.toLocaleString());
    }

    // Add hover effects
    this.addHoverEffects(bars);

    // Add X axis
    g.append('g')
      .attr('transform', `translate(0,${this.height})`)
      .call(d3.axisBottom(xScale))
      .selectAll('text')
      .style('text-anchor', 'end')
      .attr('dx', '-.8em')
      .attr('dy', '.15em')
      .attr('transform', 'rotate(-45)');

    // Add Y axis
    g.append('g')
      .call(d3.axisLeft(yScale));

    // Add axis labels
    this.addAxisLabels(g);
  }

  private createHorizontalBarChart(g: any, data: BarChartDataPoint[]): void {
    // Create scales
    const xScale = d3.scaleLinear()
      .domain([0, d3.max(data, d => d.value) as number])
      .range([0, this.width]);

    const yScale = d3.scaleBand()
      .domain(data.map(d => d.category))
      .range([0, this.height])
      .padding(0.1);

    // Create bars
    const bars = g.selectAll('.bar')
      .data(data)
      .enter().append('rect')
      .attr('class', 'bar')
      .attr('x', 0)
      .attr('y', (d: BarChartDataPoint) => yScale(d.category))
      .attr('width', (d: BarChartDataPoint) => xScale(d.value))
      .attr('height', yScale.bandwidth())
      .attr('fill', (d: BarChartDataPoint, i: number) => 
        d.color || (this.data.colorScheme === 'agent-palette' ? this.colors[i % this.colors.length] : '#6842ff')
      )
      .attr('stroke', '#ffffff')
      .attr('stroke-width', 1)
      .style('opacity', 0.8);

    // Add value labels if enabled
    if (this.data.showValues) {
      g.selectAll('.value-label')
        .data(data)
        .enter().append('text')
        .attr('class', 'value-label')
        .attr('x', (d: BarChartDataPoint) => xScale(d.value) + 5)
        .attr('y', (d: BarChartDataPoint) => yScale(d.category)! + yScale.bandwidth() / 2)
        .attr('dy', '.35em')
        .style('font-size', '12px')
        .style('fill', '#374151')
        .text((d: BarChartDataPoint) => d.value.toLocaleString());
    }

    // Add hover effects
    this.addHoverEffects(bars);

    // Add X axis
    g.append('g')
      .attr('transform', `translate(0,${this.height})`)
      .call(d3.axisBottom(xScale));

    // Add Y axis
    g.append('g')
      .call(d3.axisLeft(yScale));

    // Add axis labels
    this.addAxisLabels(g);
  }

  private addHoverEffects(bars: any): void {
    bars.on('mouseover', (event: any, d: BarChartDataPoint) => {
        d3.select(event.currentTarget).style('opacity', 1);
        this.showTooltip(event, d);
      })
      .on('mouseout', (event: any) => {
        d3.select(event.currentTarget).style('opacity', 0.8);
        this.hideTooltip();
      });
  }

  private addAxisLabels(g: any): void {
    // X axis label
    g.append('text')
      .attr('x', this.width / 2)
      .attr('y', this.height + (this.data.orientation === 'vertical' ? 60 : 40))
      .attr('fill', '#374151')
      .style('text-anchor', 'middle')
      .style('font-size', '12px')
      .text(this.data.xAxisLabel);

    // Y axis label
    g.append('text')
      .attr('transform', 'rotate(-90)')
      .attr('y', -50)
      .attr('x', -this.height / 2)
      .attr('fill', '#374151')
      .style('text-anchor', 'middle')
      .style('font-size', '12px')
      .text(this.data.yAxisLabel);
  }

  private showTooltip(event: any, d: BarChartDataPoint): void {
    const tooltip = d3.select('body').append('div')
      .attr('class', 'bar-chart-tooltip')
      .style('opacity', 0)
      .style('position', 'absolute')
      .style('background', 'rgba(0, 0, 0, 0.8)')
      .style('color', 'white')
      .style('padding', '12px')
      .style('border-radius', '6px')
      .style('font-size', '12px')
      .style('pointer-events', 'none')
      .style('max-width', '200px');

    let tooltipContent = `<strong>${d.label}</strong><br/>Value: ${d.value.toLocaleString()}`;
    
    if (d.metadata) {
      tooltipContent += `<br/><br/>${d.metadata.description}`;
      if (d.metadata.change) {
        tooltipContent += `<br/>Change: ${d.metadata.change}`;
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
    d3.selectAll('.bar-chart-tooltip').remove();
  }
}