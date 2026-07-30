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


// ============================================================================
// AdCP Protocol Visualizations — extended sample data
// ============================================================================

SAMPLE_DATA_BY_TEMPLATE['adcp_create_media_buy-visualization'] = {
  visualizationType: 'adcp_create_media_buy-visualization',
  templateId: 'adcp_create_media_buy-visualization',
  media_buy_id: 'mb_2024_espn_001',
  status: 'confirmed',
  budget: 50000,
  currency: 'USD',
  product_name: 'Premium Sports CTV - Live Events',
  start_date: '2024-07-01',
  end_date: '2024-07-31',
  estimated_impressions: 2500000
};

SAMPLE_DATA_BY_TEMPLATE['adcp_get_media_buys-visualization'] = {
  visualizationType: 'adcp_get_media_buys-visualization',
  templateId: 'adcp_get_media_buys-visualization',
  media_buys: [
    {
      media_buy_id: 'mb_001',
      name: 'ESPN CTV Campaign',
      status: 'active',
      budget: 50000,
      spent: 32000,
      currency: 'USD',
      start_date: '2024-07-01',
      end_date: '2024-07-31',
      impressions_delivered: 1600000,
      impressions_target: 2500000
    },
    {
      media_buy_id: 'mb_002',
      name: 'Fox Sports Premium',
      status: 'paused',
      budget: 35000,
      spent: 12000,
      currency: 'USD',
      start_date: '2024-07-05',
      end_date: '2024-08-15',
      impressions_delivered: 600000,
      impressions_target: 1800000
    },
    {
      media_buy_id: 'mb_003',
      name: 'Hulu Video Spot',
      status: 'completed',
      budget: 20000,
      spent: 20000,
      currency: 'USD',
      start_date: '2024-06-01',
      end_date: '2024-06-30',
      impressions_delivered: 1000000,
      impressions_target: 1000000
    }
  ]
};

SAMPLE_DATA_BY_TEMPLATE['adcp_get_media_buy_delivery-visualization'] = {
  visualizationType: 'adcp_get_media_buy_delivery-visualization',
  templateId: 'adcp_get_media_buy_delivery-visualization',
  impressions_delivered: 1600000,
  reach: 540000,
  completion_rate: 0.87,
  spend: 32000,
  daily_delivery: [45000, 52000, 48000, 61000, 55000, 58000, 63000, 68000, 72000]
};

SAMPLE_DATA_BY_TEMPLATE['adcp_update_media_buy-visualization'] = {
  visualizationType: 'adcp_update_media_buy-visualization',
  templateId: 'adcp_update_media_buy-visualization',
  media_buy_id: 'mb_2024_espn_001',
  changes: [
    { field: 'budget', old_value: '$50,000', new_value: '$75,000' },
    { field: 'end_date', old_value: '2024-07-31', new_value: '2024-08-15' },
    { field: 'target_impressions', old_value: '2.5M', new_value: '3.8M' }
  ]
};

SAMPLE_DATA_BY_TEMPLATE['adcp_get_content_standards-visualization'] = {
  visualizationType: 'adcp_get_content_standards-visualization',
  templateId: 'adcp_get_content_standards-visualization',
  standards: [
    {
      name: 'Brand Safety',
      rules: [
        { name: 'No competitor adjacency', description: 'Ads must not appear next to competitor content', severity: 'error' },
        { name: 'Family-safe content', description: 'All placements must be in family-safe environments', severity: 'warning' }
      ]
    },
    {
      name: 'Format Requirements',
      rules: [
        { name: 'Video length 15-30s', description: 'All video creatives must be 15 or 30 seconds', severity: 'error' },
        { name: 'Captions required', description: 'All video must include closed captions', severity: 'info' }
      ]
    }
  ]
};

SAMPLE_DATA_BY_TEMPLATE['adcp_get_property_list-visualization'] = {
  visualizationType: 'adcp_get_property_list-visualization',
  templateId: 'adcp_get_property_list-visualization',
  properties: [
    { domain: 'espn.com', type: 'ctv' },
    { domain: 'foxsports.com', type: 'ctv' },
    { domain: 'hulu.com', type: 'video' },
    { domain: 'nytimes.com', type: 'display' },
    { domain: 'spotify.com', type: 'audio' },
    { domain: 'pandora.com', type: 'audio' },
    { domain: 'tiktok.com', type: 'mobile' }
  ]
};

SAMPLE_DATA_BY_TEMPLATE['adcp_calibrate_content-visualization'] = {
  visualizationType: 'adcp_calibrate_content-visualization',
  templateId: 'adcp_calibrate_content-visualization',
  overall_score: 8.4,
  segments: [
    { name: 'Brand Alignment', score: 9.2 },
    { name: 'Audience Fit', score: 8.1 },
    { name: 'Context Safety', score: 7.8 },
    { name: 'Format Match', score: 8.5 }
  ]
};

SAMPLE_DATA_BY_TEMPLATE['adcp_check_governance-visualization'] = {
  visualizationType: 'adcp_check_governance-visualization',
  templateId: 'adcp_check_governance-visualization',
  verdict: 'pass',
  message: 'All governance checks passed for media buy mb_001',
  checks: [
    { name: 'Budget Compliance', status: 'pass', detail: 'Within approved limits' },
    { name: 'Brand Safety', status: 'pass', detail: 'Tier 1 verified' },
    { name: 'Audience Targeting', status: 'pass', detail: 'Compliant with privacy rules' }
  ]
};
