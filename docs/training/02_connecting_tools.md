# Training Module 2: Connecting Agents to Tools

## Introduction

Agents become powerful when they can take action. In this module, you'll learn how to connect your agents to tools, both the built-in platform tools and external tools via MCP servers. This is what transforms an agent from a conversational assistant into an autonomous operator.

## Built-In Agent Tools

Open your agent in the Agent Editor Panel and scroll to the Agent Tools section. Here you'll see a list of available tools that can be toggled on or off for your agent.

The most commonly used tools include: "invoke specialist," which lets your agent call other agents in the system for collaboration. "Invoke specialist with RAG," which does the same but passes knowledge base context along with the request. "Retrieve knowledge base results tool," which queries Amazon Bedrock Knowledge Bases directly. "Lookup events," which lets the agent review recent messages from other agents in the conversation. And "http request," which gives the agent the ability to make HTTP calls to external APIs.

For agents in the advertising domain, there are also protocol-specific tools. "Get products" discovers available ad inventory. "Create media buy" places orders. "Get media buy delivery" checks campaign performance. "Get signals" and "activate signal" work with audience and contextual targeting data.

Simply check the tools your agent needs. Each tool you enable becomes available to the agent during conversations.

## Adding MCP Server Connections

MCP, or Model Context Protocol, is how agents connect to external tool servers. This is where things get really flexible. You can connect your agent to any MCP-compatible server, whether it's running locally, on AWS, or anywhere else.

In the Agent Editor Panel, find the MCP Servers section and click "Add MCP Server." You have two options: start from a preset or configure from scratch.

## Using MCP Presets

The platform includes several presets to get you started quickly. "AWS Documentation" connects to the AWS docs MCP server using uvx. "Bedrock KB Retrieval" connects to a knowledge base retrieval server. "Custom HTTP Server" and "Custom SSE Server" give you templates for connecting to your own servers. "AWS IAM Gateway" is preconfigured for AgentCore Gateway endpoints with IAM authentication. And "OAuth Bearer Token" sets up a server with token-based authentication.

Select a preset, and the configuration form pre-populates with the right transport type, command, and arguments.

## Manual MCP Configuration

For custom servers, you'll configure these fields. First, choose the transport type. "Stdio" is for command-line tools that communicate over standard input and output. "HTTP" is for servers accessible via HTTP endpoints. "SSE" is for servers using Server-Sent Events.

For stdio transport, specify the command, like "uvx," "python," or "npx," and the arguments to pass. For HTTP or SSE transport, provide the server URL.

You can also set environment variables, add HTTP headers for authentication, define a tool name prefix to prevent naming conflicts, and whitelist or blacklist specific tools from the server.

## Authentication Options

MCP servers often require authentication. The platform supports three methods.

For AWS IAM authentication, used with AgentCore Gateway endpoints, specify the AWS region and service name. The platform handles SigV4 signing automatically.

For OAuth Bearer Token authentication, you can securely store a token. The token is saved to AWS Systems Manager Parameter Store, never stored in the agent configuration itself. This keeps credentials secure and auditable.

For OAuth credentials, you can store a username and password pair that the system uses to acquire tokens automatically.

## Testing MCP Connections

After configuring an MCP server, you can test the connection by clicking "List Tools." This sends a tools-list request to the server and displays all available tools with their names, descriptions, and input schemas. This is a quick way to verify your connection is working and see exactly what capabilities the server exposes.

## Enabling and Disabling

Each MCP server has an enabled toggle. This lets you temporarily disable a server without removing its configuration. Useful for debugging or when a server is undergoing maintenance.

## Summary

You've learned how to equip your agent with built-in tools and connect it to external MCP servers. Your agent can now take real actions: querying databases, calling APIs, discovering inventory, and interacting with external services. In the next module, we'll cover how to set up multiagent collaboration so your agents can work together.
