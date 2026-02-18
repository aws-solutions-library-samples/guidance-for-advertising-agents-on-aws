/**
 * Sample data for visualization template previews.
 * Extracted to reduce the main component file size.
 */
export const SAMPLE_DATA_BY_TEMPLATE: Record<string, any> = {
  'adcp_get_products-visualization': {
    visualizationType: 'adcp_get_products',
    templateId: 'adcp_get_products-visualization',
    title: 'Sample Product Inventory',
    products: [
      { name: 'Premium Video - Sports', reach: 2500000, price: 45.00, format: 'Video', audience: 'Sports Enthusiasts' },
      { name: 'Display Banner - News', reach: 5000000, price: 12.50, format: 'Display', audience: 'News Readers' },
      { name: 'Native Content - Lifestyle', reach: 1800000, price: 28.00, format: 'Native', audience: 'Lifestyle Seekers' },
      { name: 'Audio Spot - Podcast', reach: 800000, price: 18.00, format: 'Audio', audience: 'Podcast Listeners' }
    ]
  },
  'allocations-visualization': {
    visualizationType: 'allocations',
    templateId: 'allocations-visualization',
    title: 'Budget Allocation Preview',
    allocations: [
      { channel: 'Digital Video', percentage: 35, budget: 350000, color: '#6842ff' },
      { channel: 'Display', percentage: 25, budget: 250000, color: '#c300e0' },
      { channel: 'Social Media', percentage: 20, budget: 200000, color: '#ff6200' },
      { channel: 'Search', percentage: 15, budget: 150000, color: '#007e94' },
      { channel: 'Audio', percentage: 5, budget: 50000, color: '#22c55e' }
    ]
  },
  'bar-chart-visualization': {
    visualizationType: 'bar-chart',
    templateId: 'bar-chart-visualization',
    title: 'Performance Comparison',
    data: [
      { label: 'Campaign A', value: 85, color: '#6842ff' },
      { label: 'Campaign B', value: 72, color: '#c300e0' },
      { label: 'Campaign C', value: 91, color: '#ff6200' },
      { label: 'Campaign D', value: 68, color: '#007e94' }
    ],
    xAxisLabel: 'Campaigns',
    yAxisLabel: 'Performance Score'
  },
  'channels-visualization': {
    visualizationType: 'channels',
    templateId: 'channels-visualization',
    title: 'Channel Performance',
    channels: [
      { name: 'CTV', impressions: 1200000, clicks: 24000, ctr: 2.0, spend: 45000 },
      { name: 'Mobile', impressions: 3500000, clicks: 52500, ctr: 1.5, spend: 28000 },
      { name: 'Desktop', impressions: 2100000, clicks: 37800, ctr: 1.8, spend: 32000 },
      { name: 'Tablet', impressions: 800000, clicks: 12000, ctr: 1.5, spend: 15000 }
    ]
  },
  'creative-visualization': {
    visualizationType: 'creative',
    templateId: 'creative-visualization',
    title: 'Creative Assets',
    creatives: [
      { name: 'Hero Banner 1', format: '300x250', status: 'Active', impressions: 450000, ctr: 2.1 },
      { name: 'Video Pre-roll', format: '1920x1080', status: 'Active', impressions: 280000, ctr: 3.5 },
      { name: 'Native Card', format: '1200x628', status: 'Pending', impressions: 0, ctr: 0 }
    ]
  },
  'decision-tree-visualization': {
    visualizationType: 'decision-tree',
    templateId: 'decision-tree-visualization',
    title: 'Decision Flow',
    nodes: [
      { id: 'root', label: 'Start', type: 'start' },
      { id: 'check1', label: 'Budget > $50K?', type: 'decision', parent: 'root' },
      { id: 'yes1', label: 'Premium Inventory', type: 'action', parent: 'check1' },
      { id: 'no1', label: 'Standard Inventory', type: 'action', parent: 'check1' }
    ]
  },
  'donut-chart-visualization': {
    visualizationType: 'donut-chart',
    templateId: 'donut-chart-visualization',
    title: 'Audience Distribution',
    segments: [
      { label: 'Ages 18-24', value: 22, color: '#6842ff' },
      { label: 'Ages 25-34', value: 35, color: '#c300e0' },
      { label: 'Ages 35-44', value: 25, color: '#ff6200' },
      { label: 'Ages 45+', value: 18, color: '#007e94' }
    ]
  },
  'double-histogram-visualization': {
    visualizationType: 'double-histogram',
    templateId: 'double-histogram-visualization',
    title: 'Before vs After Comparison',
    series1: { label: 'Before', data: [12, 25, 38, 45, 32, 18], color: '#6842ff' },
    series2: { label: 'After', data: [18, 32, 48, 52, 41, 28], color: '#ff6200' },
    labels: ['Week 1', 'Week 2', 'Week 3', 'Week 4', 'Week 5', 'Week 6']
  },
  'histogram-visualization': {
    visualizationType: 'histogram',
    templateId: 'histogram-visualization',
    title: 'Frequency Distribution',
    data: [5, 12, 28, 45, 62, 48, 35, 22, 15, 8],
    labels: ['0-10', '10-20', '20-30', '30-40', '40-50', '50-60', '60-70', '70-80', '80-90', '90-100'],
    xAxisLabel: 'Score Range',
    yAxisLabel: 'Frequency'
  },
  'metrics-visualization': {
    visualizationType: 'metrics',
    templateId: 'metrics-visualization',
    title: 'Campaign Metrics',
    metrics: [
      { label: 'Impressions', value: '12.5M', change: '+15%', trend: 'up' },
      { label: 'Clicks', value: '245K', change: '+8%', trend: 'up' },
      { label: 'CTR', value: '1.96%', change: '+0.12%', trend: 'up' },
      { label: 'Spend', value: '$125K', change: '-5%', trend: 'down' },
      { label: 'CPC', value: '$0.51', change: '-12%', trend: 'down' },
      { label: 'Conversions', value: '8.2K', change: '+22%', trend: 'up' }
    ]
  },
  'segments-visualization': {
    visualizationType: 'segments',
    templateId: 'segments-visualization',
    title: 'Audience Segments',
    segments: [
      { name: 'High-Value Shoppers', size: 2500000, match_rate: 85, affinity: 'High' },
      { name: 'Sports Enthusiasts', size: 4200000, match_rate: 72, affinity: 'Medium' },
      { name: 'Tech Early Adopters', size: 1800000, match_rate: 91, affinity: 'High' },
      { name: 'Travel Intenders', size: 3100000, match_rate: 68, affinity: 'Medium' }
    ]
  },
  'timeline-visualization': {
    visualizationType: 'timeline',
    templateId: 'timeline-visualization',
    title: 'Campaign Timeline',
    events: [
      { date: '2026-01-15', label: 'Campaign Launch', type: 'milestone' },
      { date: '2026-02-01', label: 'Mid-Flight Optimization', type: 'action' },
      { date: '2026-02-15', label: 'Creative Refresh', type: 'action' },
      { date: '2026-03-01', label: 'Campaign End', type: 'milestone' }
    ]
  }
};
