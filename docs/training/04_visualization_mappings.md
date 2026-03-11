# Training Module 4: Configuring Visualization Mappings

## Introduction

One of the most distinctive features of this platform is how agent responses automatically render as rich visual components. Charts, allocation breakdowns, audience segments, timelines, and product grids all appear alongside the conversational output. In this module, you'll learn how to configure and generate the visualization mappings that make this possible.

## How Visualization Mappings Work

Every agent has a visualization map. This map defines which visualization templates apply to the agent's output and describes when each template should be used. When the agent produces a response, the frontend examines the output for visualization data tags, matches them against the agent's visualization map, and renders the appropriate visual component.

The key insight is that the agent doesn't need to know anything about the UI. It simply includes structured data in its response using a specific tag format, and the visualization layer handles the rest. This decouples agent reasoning from data presentation.

## The Visualization Map Structure

A visualization map is a JSON document with three fields. "agentName" is the agent's identifier. "agentId" is a lowercase version used for lookups. And "templates" is an array of template entries, each with a "templateId" and a "usage" description.

The templateId references one of the platform's built-in visualization templates. The usage description tells the agent when to use that template. This description gets incorporated into the agent's context, so it knows which visualization to emit for different types of analysis.

## Available Templates

The platform includes twelve visualization templates. "Metrics visualization" displays KPIs, performance metrics, and numerical summaries in card layouts. "Allocations visualization" shows budget or resource distribution across categories using bar charts and breakdowns. "Timeline visualization" renders workflow phases, milestones, and schedules on a horizontal timeline. "Segments visualization" displays audience segments with demographic and behavioral breakdowns. "Creative visualization" shows generated creative assets with preview images and metadata. "Bar chart visualization" renders standard bar charts for comparative data. "Donut chart visualization" shows proportional data in donut or pie format. "Histogram visualization" and "double histogram visualization" display distribution data. "Channels visualization" shows channel-level performance comparisons. "Decision tree visualization" renders branching logic and decision flows. And "AdCP get products visualization" displays interactive product grids from inventory discovery calls.

## Viewing Current Mappings

Open your agent in the Agent Editor Panel and scroll to the Visualization Mappings section. If your agent already has mappings configured, you'll see them listed with their template IDs and usage descriptions. You can click any mapping to preview what the template looks like with sample data.

## Editing Mappings Manually

Click "Edit JSON" to open the visualization JSON editor. This shows the raw JSON of your agent's visualization map. You can add, remove, or modify template entries directly. Each entry needs a templateId that matches one of the available templates and a usage description that clearly explains when the agent should use it.

For example, if you're adding a metrics visualization to a new analytics agent, you'd add an entry like: templateId "metrics-visualization" with usage "Performance metrics, KPIs, and numerical analysis results."

The editor validates your JSON in real time and shows errors if the format is incorrect.

## Generating Mappings with AI

If you'd rather not write mappings manually, click "Generate with AI." This opens a prompt field where you describe your agent's purpose and the types of data it produces. The AI analyzes your description, cross-references it with the available templates, and generates a complete visualization map tailored to your agent.

You can also attach reference documents to give the AI more context. For example, you might attach a sample agent response or a description of the data formats your agent works with.

After generation, review the suggested mappings. You can accept them as-is, modify individual entries, or regenerate with a different prompt.

## How Agents Emit Visualization Data

For the visualizations to render, your agent needs to include structured data in its responses using visualization data tags. The format looks like this: the agent wraps JSON data in a tag that specifies the template type. For example, a metrics visualization tag would contain an array of metric objects with labels, values, and trend indicators.

This is why the usage descriptions in your visualization map matter. They guide the agent on when and how to emit visualization data. Good usage descriptions are specific. Instead of "show metrics," write "campaign performance KPIs including impressions, clicks, CTR, and spend with period-over-period comparisons."

## Previewing Visualizations

The editor includes a preview feature. Select any template from your mapping and click "Preview." This renders the template with sample data so you can see exactly how it will look in the chat interface. Each template has built-in sample data that demonstrates its layout and capabilities.

## Best Practices

Keep your visualization maps focused. An agent should only have templates that match its actual output types. A measurement agent doesn't need a creative visualization template, and a creative agent doesn't need a decision tree template.

Write specific usage descriptions. The more precise the description, the better the agent understands when to emit visualization data. Vague descriptions lead to inconsistent visualization behavior.

Test with real conversations. After configuring mappings, have a conversation with your agent and verify that visualizations render correctly. If a visualization doesn't appear when expected, check that the agent's instructions mention the visualization format and that the usage description matches the context.

## Summary

You've learned how visualization mappings connect agent output to rich visual components. You can configure mappings manually, generate them with AI, preview templates, and understand how agents emit visualization data. Combined with the previous modules on agent creation, tool connections, and multiagent collaboration, you now have a complete understanding of how to build and configure agents in the Agentic Advertising platform.
