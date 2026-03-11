# Training Module 3: Multiagent Collaboration

## Introduction

A single agent can do a lot, but the real power of this platform comes from agents working together. In this module, you'll learn how to configure multiagent collaboration, allowing your agents to invoke each other, share context, and coordinate across specialized domains.

## How Collaboration Works

When an agent encounters a task outside its expertise, it can invoke another agent as a specialist. The calling agent sends a request with context, the specialist processes it and returns a response, and the calling agent incorporates that response into its own work. This happens within the conversation flow, transparently to the user.

The platform supports two collaboration patterns. Internal collaboration uses the "invoke specialist" tool to call other agents running on the same AgentCore runtime. External collaboration uses A2A, or Agent-to-Agent protocol, to call agents running on separate runtimes or even separate AWS accounts.

## Configuring Internal Collaboration

Open your agent in the Agent Editor Panel and find the Tool Agent Names section. This is where you specify which other agents your agent can call.

Click "Add Tool Agent" and select from the list of available agents in the system. For example, if you're configuring a Media Planning Agent, you might add the Audience Intelligence Agent, the Inventory Optimization Agent, and the Measurement Agent as tool agents.

The order matters. Agents listed here appear in the agent's system prompt as available specialists, and the agent can invoke any of them using the "invoke specialist" tool. Make sure you've enabled that tool in the Agent Tools section.

When your agent invokes a specialist, it can also pass knowledge base context by using the "invoke specialist with RAG" tool instead. This sends relevant retrieved documents along with the request, giving the specialist additional context without requiring a separate knowledge base query.

## Configuring External A2A Agents

For agents running outside your current runtime, use the External Agent Configs section. Click "Add External Agent" to open the A2A agent editor.

Provide a display name and the agent's ARN. This is typically an AgentCore runtime ARN or an A2A protocol endpoint. Toggle the "A2A Protocol" switch if the remote agent supports the Agent-to-Agent protocol standard.

## External Agent Authentication

External agents often require authentication. The platform supports three options.

"None" works for agents within the same AWS account that rely on IAM role-based access. "IAM" uses AWS SigV4 signing, appropriate for cross-account access within AWS. "OAuth" supports both bearer token and credential-based authentication for agents outside AWS.

For OAuth, you can either paste a bearer token directly, which gets stored securely in SSM Parameter Store, or provide OAuth credentials, a username and password pair, that the system uses to acquire tokens automatically.

## The Agent Interaction Matrix

The platform's agent ecosystem follows a defined interaction matrix. Not every agent talks to every other agent. The matrix defines who communicates with whom and which protocol they use.

The Agency Agent is the primary orchestrator. It communicates with the Advertiser Agent via A2A for campaign briefs and approvals. It talks to the Publisher Agent via AdCP for inventory discovery and media buys. It engages the Signal Agent via AdCP Signals for audience and contextual data. And it coordinates with the Identity, Verification, and Measurement agents via MCP for specialized services.

When configuring your agent's collaborators, follow this matrix. An agent should only have access to the specialists it legitimately needs. This keeps the system organized and prevents circular dependencies.

## Collaboration in Practice

Here's what a typical collaboration flow looks like. A user asks the Agency Agent to create a media plan. The Agency Agent recognizes it needs audience data and inventory availability. It invokes the Audience Intelligence Agent, which analyzes demographics and returns segment recommendations. In parallel, it invokes the Inventory Optimization Agent, which checks available inventory and returns allocation strategies. The Agency Agent synthesizes both responses into a unified media plan and presents it to the user.

The key word is "parallel." When specialists don't depend on each other's output, the orchestrating agent can invoke them simultaneously, significantly reducing response time.

## The Lookup Events Tool

Sometimes an agent needs to see what other agents have already said in the conversation. The "lookup events" tool lets an agent query recent messages from specific agents. This is useful when an agent joins a conversation mid-stream and needs to catch up on context without re-invoking specialists.

## Summary

You've learned how to configure both internal and external multiagent collaboration. Your agents can now invoke specialists, share context, authenticate across boundaries, and coordinate complex workflows. In the next module, we'll cover how to configure visualization mappings so your agents' output renders as rich visual components in the interface.
