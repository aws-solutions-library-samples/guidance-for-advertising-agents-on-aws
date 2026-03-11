# Training Module 1: Creating New Agents

## Introduction

In this module, you'll learn how to create a new agent in the Agentic Advertising platform. Every agent in the system is a specialist with its own instructions, tools, and visual identity. Creating one takes about five minutes once you know the workflow.

## Opening the Agent Management Panel

Start from the main chat interface. In the top navigation area, you'll see an agent management icon. Click it to open the Agent Management Modal. This is your central hub for all agent operations. You'll see a grid of existing agent cards, each showing the agent's name, team, description, and color accent.

## Starting a New Agent

Click the "Add Agent" button. This opens the Agent Editor Panel with an empty form. The editor is organized into sections, and we'll walk through each one.

## Basic Information

Start with the display name. Type something descriptive, like "Brand Safety Analyst" or "Audience Segmentation Agent." As you type, the system automatically generates an agent ID from your display name, converting it to a format like "BrandSafetyAnalystAgent." You can override this if you prefer a different ID, but it must start with a letter and contain only alphanumeric characters and underscores.

Next, select a team name. This groups your agent with related agents in the interface. Common teams include "Planning," "Optimization," "Measurement," and "Creative."

Write a brief description of what your agent does. Keep it under a few sentences. This description appears on the agent card and helps other users understand the agent's purpose.

## Choosing a Color

Pick a color from the preset palette. These colors follow the platform's design system, with purples, oranges, and fuchsias as primary accents. The color you choose appears as the agent's accent throughout the interface, on its card, in chat messages, and in visualization headers.

## Model Configuration

Every agent needs a language model. The default configuration uses Claude 3.5 Sonnet with 8,000 max tokens and a temperature of 0.3. You can adjust these settings based on your agent's needs. Lower temperature values produce more focused, deterministic responses. Higher values allow more creative variation. For analytical agents, keep temperature low. For creative agents, you might push it higher.

## Writing Instructions

The instructions field is where you define your agent's personality, capabilities, and behavior. This is the system prompt that shapes everything the agent does. You can write in markdown for better formatting.

A good instruction set covers four areas. First, identity and role: who the agent is and what it specializes in. Second, core capabilities: what the agent can do and what data it works with. Third, output format: how the agent should structure its responses, including any visualization data tags. Fourth, collaboration guidelines: when and how to engage other agents.

If you'd rather not write instructions from scratch, click the "Generate with AI" button. This opens a prompt field where you describe what you want the agent to do, and the system generates a complete instruction set for you. You can also attach reference documents to give the AI more context about your domain.

## Selecting a Knowledge Base

If your agent needs access to historical data or reference materials, select a knowledge base from the dropdown. This connects the agent to Amazon Bedrock Knowledge Bases for retrieval-augmented generation. The agent can then query the knowledge base with natural language questions and ground its responses in real data.

## Saving Your Agent

Once you've filled in the required fields, click Save. The system validates your configuration, checking for unique IDs, required fields, and valid parameter ranges. If everything passes, your agent is persisted to DynamoDB and immediately available in the system.

Your new agent now appears in the agent management grid and can be mentioned in chat conversations using the at-mention typeahead.

## Summary

That's the core workflow for creating a new agent. You defined its identity, configured its model, wrote its instructions, and saved it to the system. In the next module, we'll cover how to connect your agent to external tools using MCP servers and other integrations.
