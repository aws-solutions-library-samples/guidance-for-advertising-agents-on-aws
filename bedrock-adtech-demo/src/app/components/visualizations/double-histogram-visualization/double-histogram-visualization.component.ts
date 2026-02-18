import { Component, Input, OnInit, OnDestroy, ElementRef, ViewChild, AfterViewInit } from '@angular/core';
import * as d3 from 'd3';

/**
 * Double Histogram Visualization Component
 * 
 * Displays two data distributions side by side for comparison using D3.js.
 * Ideal for before/after analysis, A/B testing results, and comparative studies.
 * 
 * @example
 * ```html
 * <app-double-histogram-visualization [data]="doubleHistogramData"></app-double-histogram-visualization>
 * ```
 * 
 * @example JSON Structure:
 * ```json
 * {
 *   "visualizationType": "double-histogram",
 *   "title": "Campaign Performance Comparison",
 *   "subtitle": "Before vs After Optimization",
 *   "xAxisLabel": "Click-Through Rate (%)",
 *   "yAxisLabel": "Frequency",
 *   "dataset1": {
 *     "label": "Before Optimization",
 *     "color": "#ef4444",
 *     "data": [
 *       { "value": 0.5, "frequency": 12 },
 *       { "value": 1.0, "frequency": 25 },
 *       { "value": 1.5, "frequency": 35 },
 *       { "value": 2.0, "frequency": 28 },
 *       { "value": 2.5, "frequency": 18 },
 *       { "value": 3.0, "frequency": 8 },
 *       { "value": 3.5, "frequency": 3 }
 *     ]
 *   },
 *   "dataset2": {
 *     "label": "After Optimization",
 *     "color": "#10b981",
 *     "data": [
 *       { "value": 1.0, "frequency": 5 },
 *       { "value": 1.5, "frequency": 15 },
 *       { "value": 2.0, "frequency": 22 },
 *       { "value": 2.5, "frequency": 32 },
 *       { "value": 3.0, "frequency": 28 },
 *       { "value": 3.5, "frequency": 20 },
 *       { "value": 4.0, "frequency": 12 },
 *       { "value": 4.5, "frequency": 6 }
 *     ]
 *   },
 *   "binCount": 20,
 *   "insights": [
 *     "Optimization shifted distribution toward higher CTR",
 *     "Peak performance improved from 1.5% to 2.5% CTR",
 *     "Reduced variance in low-performing segments"
 *   ],
 *   "summary": {
 *     "dataset1Stats": {
 *       "mean": 1.8,
 *       "median": 1.7
 *     },
 *     "dataset2Stats": {
 *       "mean": 2.6,
 *       "median": 2.5
 *     },
 *     "comparison": "44% improvement in average CTR after optimization"
 *   }
 * }
 * ```
 * 
 * @features
 * - Side-by-side histogram comparison
 * - Customizable bin counts and colors
 * - Statistical summary display (mean, median)
 * - Interactive tooltips with dataset information
 * - Overlay capability for direct comparison
 * - Responsive SVG rendering
 * 
 * @useCases
 * - Before/after campaign comparisons
 * - A/B test result distributions
 * - Channel performance comparisons
 * - Audience segment comparisons
 * - Time period comparisons
 */

interface HistogramDataPoint {
  value: number;
  frequency: number;
}

interface Dataset {
  label: string;
  color: string;
  data: HistogramDataPoint[];
}

interface DoubleHistogramVisualization {
  visualizationType: 'double-histogram';
  title: string;
  subtitle?: string;
  xAxisLabel: string;
  yAxisLabel: string;
  dataset1: Dataset;
  dataset2: Dataset;
  binCount: number;
  insights?: string[];
  summary?: {
    dataset1Stats: {
      mean: number;
      median: number;
    };
    dataset2Stats: {
      mean: number;
      median: number;
    };
    comparison: string;
  };
}

@Component({
  selector: 'app-double-histogram-visualization',
  template: `
    <div class="double-histogram-container">
      <div class="double-histogram-header">
        <h3 class="double-histogram-title">{{ data.title }}</h3>
        <p class="double-histogram-subtitle" *ngIf="data.subtitle">{{ data.subtitle }}</p>
      </div>
      <div class="legend">
        <div class="legend-item">
          <div class="legend-color" [style.background-color]="data.dataset1.color"></div>
          <span>{{ data.dataset1.label }}</span>
        </div>
        <div class="legend-item">
          <div class="legend-color" [style.background-color]="data.dataset2.color"></div>
          <span>{{ data.dataset2.label }}</span>
        </div>
      </div>
      <div class="double-histogram-chart" #chartContainer></div>
      <div class="double-histogram-insights" *ngIf="data.insights && data.insights.length > 0">
        <h4>Key Insights</h4>
        <ul>
          <li *ngFor="let insight of data.insights">{{ insight }}</li>
        </ul>
      </div>
      <div class="double-histogram-summary" *ngIf="data.summary">
        <div class="comparison-stats">
          <div class="dataset-stats">
            <h5>{{ data.dataset1.label }}</h5>
            <div class="stat-row">
              <span class="stat-label">Mean:</span>
              <span class="stat-value">{{ data.summary.dataset1Stats.mean | number:'1.2-2' }}</span>
            </div>
            <div class="stat-row">
              <span class="stat-label">Median:</span>
              <span class="stat-value">{{ data.summary.dataset1Stats.median | number:'1.2-2' }}</span>
            </div>
          </div>
          <div class="dataset-stats">
            <h5>{{ data.dataset2.label }}</h5>
            <div class="stat-row">
              <span class="stat-label">Mean:</span>
              <span class="stat-value">{{ data.summary.dataset2Stats.mean | number:'1.2-2' }}</span>
            </div>
            <div class="stat-row">
              <span class="stat-label">Median:</span>
              <span class="stat-value">{{ data.summary.dataset2Stats.median | number:'1.2-2' }}</span>
            </div>
          </div>
        </div>
        <div class="comparison-insight" *ngIf="data.summary.comparison">
          <strong>Comparison:</strong> {{ data.summary.comparison }}
        </div>
      </div>
    </div>
  `,
  styleUrls: ['./double-histogram-visualization.component.scss']
})
export class DoubleHistogramVisualizationComponent implements OnInit, AfterViewInit, OnDestroy {
  @Input() data!: DoubleHistogramVisualization;
  @ViewChild('chartContainer', { static: false }) chartContainer!: ElementRef;

  private svg: any;
  private margin = { top: 20, right: 30, bottom: 60, left: 60 };
  private width = 600 - this.margin.left - this.margin.right;
  private height = 400 - this.margin.top - this.margin.bottom;

  ngOnInit(): void {
    // Component initialization
  }

  ngAfterViewInit(): void {
    this.createDoubleHistogram();
  }

  ngOnDestroy(): void {
    if (this.svg) {
      this.svg.remove();
    }
  }

  private createDoubleHistogram(): void {
    // Clear any existing chart
    d3.select(this.chartContainer.nativeElement).selectAll('*').remove();

    // Create SVG
    this.svg = d3.select(this.chartContainer.nativeElement)
      .append('svg')
      .attr('width', this.width + this.margin.left + this.margin.right)
      .attr('height', this.height + this.margin.top + this.margin.bottom);

    const g = this.svg.append('g')
      .attr('transform', `translate(${this.margin.left},${this.margin.top})`);

    // Combine data to get overall domain
    const allData = [...this.data.dataset1.data, ...this.data.dataset2.data];
    const xExtent = d3.extent(allData, d => d.value) as [number, number];
    const maxFrequency = d3.max(allData, d => d.frequency) as number;

    // Create scales
    const xScale = d3.scaleLinear()
      .domain(xExtent)
      .range([0, this.width]);

    const yScale = d3.scaleLinear()
      .domain([0, maxFrequency])
      .range([this.height, 0]);

    // Create bins from our frequency data - we already have the histogram structure
    const binWidth = (xExtent[1] - xExtent[0]) / (this.data.binCount || 20);
    
    const bins1 = this.data.dataset1.data.map(d => ({
      x0: d.value - binWidth / 2,
      x1: d.value + binWidth / 2,
      length: d.frequency,
      data: d
    }));
    
    const bins2 = this.data.dataset2.data.map(d => ({
      x0: d.value - binWidth / 2,
      x1: d.value + binWidth / 2,
      length: d.frequency,
      data: d
    }));

    // Create bars for dataset 1
    g.selectAll('.bar1')
      .data(bins1)
      .enter().append('rect')
      .attr('class', 'bar1')
      .attr('x', d => xScale(d.x0!))
      .attr('width', d => Math.max(0, (xScale(d.x1!) - xScale(d.x0!)) / 2 - 1))
      .attr('y', d => yScale(d.length))
      .attr('height', d => this.height - yScale(d.length))
      .attr('fill', this.data.dataset1.color)
      .attr('stroke', '#ffffff')
      .attr('stroke-width', 1)
      .style('opacity', 0.7);

    // Create bars for dataset 2 (offset to the right)
    g.selectAll('.bar2')
      .data(bins2)
      .enter().append('rect')
      .attr('class', 'bar2')
      .attr('x', d => xScale(d.x0!) + (xScale(d.x1!) - xScale(d.x0!)) / 2)
      .attr('width', d => Math.max(0, (xScale(d.x1!) - xScale(d.x0!)) / 2 - 1))
      .attr('y', d => yScale(d.length))
      .attr('height', d => this.height - yScale(d.length))
      .attr('fill', this.data.dataset2.color)
      .attr('stroke', '#ffffff')
      .attr('stroke-width', 1)
      .style('opacity', 0.7);

    // Add hover effects for dataset 1
    g.selectAll('.bar1')
      .on('mouseover', (event, d) => {
        d3.select(event.currentTarget).style('opacity', 1);
        this.showTooltip(event, d, this.data.dataset1.label);
      })
      .on('mouseout', (event) => {
        d3.select(event.currentTarget).style('opacity', 0.7);
        this.hideTooltip();
      });

    // Add hover effects for dataset 2
    g.selectAll('.bar2')
      .on('mouseover', (event, d) => {
        d3.select(event.currentTarget).style('opacity', 1);
        this.showTooltip(event, d, this.data.dataset2.label);
      })
      .on('mouseout', (event) => {
        d3.select(event.currentTarget).style('opacity', 0.7);
        this.hideTooltip();
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
  }

  private showTooltip(event: any, d: any, datasetLabel: string): void {
    const tooltip = d3.select('body').append('div')
      .attr('class', 'double-histogram-tooltip')
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
    
    tooltip.html(`${datasetLabel}<br/>Range: ${d.x0?.toFixed(2)} - ${d.x1?.toFixed(2)}<br/>Count: ${d.length}`)
      .style('left', (event.pageX + 10) + 'px')
      .style('top', (event.pageY - 28) + 'px');
  }

  private hideTooltip(): void {
    d3.selectAll('.double-histogram-tooltip').remove();
  }
}