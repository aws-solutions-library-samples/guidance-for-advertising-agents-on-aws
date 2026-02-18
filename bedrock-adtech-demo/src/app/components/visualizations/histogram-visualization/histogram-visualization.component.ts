import { Component, Input, OnInit, OnDestroy, ElementRef, ViewChild, AfterViewInit } from '@angular/core';
import * as d3 from 'd3';

/**
 * Histogram Visualization Component
 * 
 * Displays data distribution with optional colored tail highlighting using D3.js.
 * Features statistical summaries and customizable bin counts.
 * 
 * @example
 * ```html
 * <app-histogram-visualization [data]="histogramData"></app-histogram-visualization>
 * ```
 * 
 * @example JSON Structure:
 * ```json
 * {
 *   "visualizationType": "histogram",
 *   "title": "Campaign Performance Distribution",
 *   "subtitle": "Click-Through Rate Analysis",
 *   "xAxisLabel": "Click-Through Rate (%)",
 *   "yAxisLabel": "Number of Campaigns",
 *   "data": [
 *     { "value": 0.5, "frequency": 8, "label": "Low Performance" },
 *     { "value": 1.0, "frequency": 15, "label": "Below Average" },
 *     { "value": 1.5, "frequency": 25, "label": "Average" },
 *     { "value": 2.0, "frequency": 32, "label": "Above Average" },
 *     { "value": 2.5, "frequency": 28, "label": "Good" },
 *     { "value": 3.0, "frequency": 18, "label": "Very Good" },
 *     { "value": 3.5, "frequency": 12, "label": "Excellent" },
 *     { "value": 4.0, "frequency": 6, "label": "Outstanding" },
 *     { "value": 4.5, "frequency": 3, "label": "Exceptional" },
 *     { "value": 5.0, "frequency": 1, "label": "Best in Class" }
 *   ],
 *   "coloredTail": {
 *     "enabled": true,
 *     "threshold": 3.5,
 *     "color": "#10b981"
 *   },
 *   "binCount": 20,
 *   "insights": [
 *     "Most campaigns achieve 2-2.5% CTR",
 *     "Top 10% of campaigns exceed 3.5% CTR",
 *     "Distribution shows normal pattern with slight right skew"
 *   ],
 *   "summary": {
 *     "mean": 2.3,
 *     "median": 2.2,
 *     "standardDeviation": 0.8
 *   }
 * }
 * ```
 * 
 * @features
 * - Customizable histogram with D3.js rendering
 * - Optional colored tail highlighting for outliers
 * - Statistical summary display (mean, median, std dev)
 * - Interactive tooltips with frequency data
 * - Configurable bin counts
 * - Agent color palette integration
 * 
 * @useCases
 * - Performance distribution analysis
 * - Audience behavior patterns
 * - Budget allocation distributions
 * - Campaign performance ranges
 * - Metric frequency analysis
 */

interface HistogramData {
  value: number;
  frequency: number;
  label?: string;
}

interface HistogramVisualization {
  visualizationType: 'histogram';
  title: string;
  subtitle?: string;
  xAxisLabel: string;
  yAxisLabel: string;
  data: HistogramData[];
  coloredTail?: {
    enabled: boolean;
    threshold: number;
    color: string;
  };
  binCount: number;
  insights?: string[];
  summary?: {
    mean: number;
    median: number;
    standardDeviation: number;
  };
}

@Component({
  selector: 'app-histogram-visualization',
  template: `
    <div class="histogram-container">
      <div class="histogram-header">
        <h3 class="histogram-title">{{ data.title }}</h3>
        <p class="histogram-subtitle" *ngIf="data.subtitle">{{ data.subtitle }}</p>
      </div>
      <div class="histogram-chart" #chartContainer></div>
      <div class="histogram-insights" *ngIf="data.insights && data.insights.length > 0">
        <h4>Key Insights</h4>
        <ul>
          <li *ngFor="let insight of data.insights">{{ insight }}</li>
        </ul>
      </div>
      <div class="histogram-summary" *ngIf="data.summary">
        <div class="summary-stats">
          <div class="stat-item">
            <span class="stat-label">Mean:</span>
            <span class="stat-value">{{ data.summary.mean | number:'1.2-2' }}</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">Median:</span>
            <span class="stat-value">{{ data.summary.median | number:'1.2-2' }}</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">Std Dev:</span>
            <span class="stat-value">{{ data.summary.standardDeviation | number:'1.2-2' }}</span>
          </div>
        </div>
      </div>
    </div>
  `,
  styleUrls: ['./histogram-visualization.component.scss']
})
export class HistogramVisualizationComponent implements OnInit, AfterViewInit, OnDestroy {
  @Input() data!: HistogramVisualization;
  @ViewChild('chartContainer', { static: false }) chartContainer!: ElementRef;

  private svg: any;
  private margin = { top: 20, right: 30, bottom: 60, left: 60 };
  private width = 600 - this.margin.left - this.margin.right;
  private height = 400 - this.margin.top - this.margin.bottom;

  // Agent color palette
  private colors = {
    primary: '#6842ff',
    secondary: '#ff6200',
    tertiary: '#c300e0',
    quaternary: '#007e94',
    success: '#10b981',
    warning: '#f59e0b',
    error: '#ef4444'
  };

  ngOnInit(): void {
    // Component initialization
  }

  ngAfterViewInit(): void {
    this.createHistogram();
  }

  ngOnDestroy(): void {
    if (this.svg) {
      this.svg.remove();
    }
  }

  private createHistogram(): void {
    // Clear any existing chart
    d3.select(this.chartContainer.nativeElement).selectAll('*').remove();

    // Create SVG
    this.svg = d3.select(this.chartContainer.nativeElement)
      .append('svg')
      .attr('width', this.width + this.margin.left + this.margin.right)
      .attr('height', this.height + this.margin.top + this.margin.bottom);

    const g = this.svg.append('g')
      .attr('transform', `translate(${this.margin.left},${this.margin.top})`);

    // Create scales
    const xScale = d3.scaleLinear()
      .domain(d3.extent(this.data.data, d => d.value) as [number, number])
      .range([0, this.width]);

    const yScale = d3.scaleLinear()
      .domain([0, d3.max(this.data.data, d => d.frequency) as number])
      .range([this.height, 0]);

    // Create histogram bins - we need to create bins based on the data structure we have
    // Since we already have frequency data, we'll create bins manually
    const binWidth = (xScale.domain()[1] - xScale.domain()[0]) / (this.data.binCount || 20);
    const bins = this.data.data.map(d => ({
      x0: d.value - binWidth / 2,
      x1: d.value + binWidth / 2,
      length: d.frequency,
      data: d
    }));

    // Create bars
    const bars = g.selectAll('.bar')
      .data(bins)
      .enter().append('rect')
      .attr('class', 'bar')
      .attr('x', d => xScale(d.x0!))
      .attr('width', d => Math.max(0, xScale(d.x1!) - xScale(d.x0!) - 1))
      .attr('y', d => yScale(d.length))
      .attr('height', d => this.height - yScale(d.length))
      .attr('fill', (d, i) => {
        // Apply colored tail if enabled
        if (this.data.coloredTail?.enabled && d.x0! >= this.data.coloredTail.threshold) {
          return this.data.coloredTail.color;
        }
        return this.colors.primary;
      })
      .attr('stroke', '#ffffff')
      .attr('stroke-width', 1)
      .style('opacity', 0.8);

    // Add hover effects
    bars.on('mouseover', (event: any, d: any) => {
      d3.select(event.currentTarget).style('opacity', 1);

      // Create tooltip
      const tooltip = d3.select('body').append('div')
        .attr('class', 'histogram-tooltip')
        .style('opacity', 0)
        .style('position', 'absolute')
        .style('background', 'rgba(0, 0, 0, 0.8)')
        .style('color', 'white')
        .style('padding', '8px')
        .style('border-radius', '4px')
        .style('font-size', '12px')
        .style('pointer-events', 'none');

      tooltip.transition()
        .duration(200)
        .style('opacity', 1);

      tooltip.html(`Range: ${d.x0?.toFixed(2)} - ${d.x1?.toFixed(2)}<br/>Count: ${d.length}`)
        .style('left', (event.pageX + 10) + 'px')
        .style('top', (event.pageY - 28) + 'px');
    })
      .on('mouseout', (event: any) => {
        d3.select(event.currentTarget).style('opacity', 0.8);
        d3.selectAll('.histogram-tooltip').remove();
      });

    // Add X axis
    g.append('g')
      .attr('transform', `translate(0,${this.height})`)
      .call(d3.axisBottom(xScale))
      .append('text')
      .attr('x', this.width / 2)
      .attr('y', 40)
      .attr('fill', '#374151')
      .style('text-anchor', 'middle')
      .style('font-size', '12px')
      .text(this.data.xAxisLabel);

    // Add Y axis
    g.append('g')
      .call(d3.axisLeft(yScale))
      .append('text')
      .attr('transform', 'rotate(-90)')
      .attr('y', -40)
      .attr('x', -this.height / 2)
      .attr('fill', '#374151')
      .style('text-anchor', 'middle')
      .style('font-size', '12px')
      .text(this.data.yAxisLabel);

    // Add colored tail threshold line if enabled
    if (this.data.coloredTail?.enabled) {
      g.append('line')
        .attr('x1', xScale(this.data.coloredTail.threshold))
        .attr('x2', xScale(this.data.coloredTail.threshold))
        .attr('y1', 0)
        .attr('y2', this.height)
        .attr('stroke', this.data.coloredTail.color)
        .attr('stroke-width', 2)
        .attr('stroke-dasharray', '5,5');
    }
  }
}