# Local Agent Testing

Test agents locally without deploying to AgentCore Runtime.

## Quick Start

```bash
# List available agents
python local_agent_tester.py --list-agents

# Test an agent with a simple query
source agentcore/deployment/.venv/bin/activate && python local_agent_tester.py --agent MediaPlanningAgent --query "Help me optimize my media plan"

# Use a predefined scenario
source agentcore/deployment/.venv/bin/activate && python local_agent_tester.py --agent MediaPlanningAgent --scenario 1

# List scenarios for an agent
python local_agent_tester.py --list-scenarios MediaPlanningAgent
```

## Features

- **Zero Deployment**: Test agents without AgentCore Runtime
- **Real Agent Code**: Uses actual agent instructions and configurations
- **Graceful Fallbacks**: Handles missing AWS services automatically
- **Visualization Testing**: Shows generated JSON visualizations
- **Predefined Scenarios**: Built-in test cases for each agent type
- **Output Management**: Saves full responses to `test-output/` directory with summary display

## Prerequisites

1. **Virtual Environment**: Use the AgentCore deployment virtual environment
   ```bash
   source agentcore/deployment/.venv/bin/activate
   ```

2. **AWS Credentials**: Configure valid AWS credentials with Bedrock access
   ```bash
   aws configure
   # Verify: aws sts get-caller-identity
   ```

3. **Bedrock Model Access**: Ensure Claude 4.5 access is enabled in your AWS account

## Agent Types Supported

### Orchestrator Agents
- `MediaPlanningAgent` - Strategic media planning from publisher perspective
- `CampaignOptimizationAgent` - Integrated campaign strategy and optimization
- `YieldOptimizationAgent` - Revenue and yield optimization
- `InventoryOptimizationAgent` - Inventory forecasting and optimization

### Specialist Agents
- `AudienceIntelligenceAgent` - Audience analysis and segmentation
- `CreativeSelectionAgent` - Creative concepts and format selection
- `TimingStrategyAgent` - Campaign timing and scheduling
- `WeatherImpactAgent` - Weather-based campaign insights
- And 20+ more...

## Command Line Options

```bash
python local_agent_tester.py [OPTIONS]

Options:
  --agent AGENT_NAME        Agent to test
  --query "QUERY TEXT"      Custom query to send
  --scenario NUMBER         Use predefined scenario (1, 2, 3...)
  --list-agents            Show all available agents
  --list-scenarios AGENT   Show scenarios for specific agent
  --verbose, -v            Show detailed output and errors
```

## Examples

### Test Media Planning
```bash
source agentcore/deployment/.venv/bin/activate && python local_agent_tester.py --agent MediaPlanningAgent --query "Develop strategic media plan for Q4 holiday season"
```

### Test Campaign Optimization
```bash
source agentcore/deployment/.venv/bin/activate && python local_agent_tester.py --agent CampaignOptimizationAgent --scenario 1
```

### Test Creative Generation
```bash
source agentcore/deployment/.venv/bin/activate && python local_agent_tester.py --agent CreativeSelectionAgent --query "Generate creative concepts for luxury automotive campaign"
```

## Output Management

### Console Output
- Shows first 5 lines of response + summary
- Displays response statistics (length, lines, visualizations)
- Shows visualization count and types

### File Output
- Full responses saved to `test-output/AGENT_TIMESTAMP.json`
- Structured JSON format with metadata
- Includes parsed visualizations and performance data
- Timestamped for easy comparison

### Example Output
```
📊 Response Summary:
   Total Length: 8,102 characters
   Lines: 210
   Visualizations: 2 found
   Full Output: test-output/CreativeSelectionAgent_20260104_233532.json
```

## How It Works

1. **Environment Setup**: Uses existing AgentCore virtual environment and AWS credentials
2. **Agent Creation**: Uses the real `GenericAgent` orchestrator from `handler.py`
3. **Fallback Behavior**: Memory system falls back to local conversation management
4. **Response Processing**: Extracts and displays agent responses and visualizations
5. **Output Management**: Saves complete data while showing clean summaries

## Architecture

```
local_agent_tester.py
    ↓
handler.GenericAgent()
    ↓
Real agent instructions (.txt files)
    ↓
Local fallbacks (no AWS deployment needed)
    ↓
test-output/ (full response preservation)
```

## Expected Warnings (Normal)

These warnings are expected and don't affect functionality:

```
No agent cards found to replace in instructions for MediaPlanningAgent. Replaced with empty string
⊘ Using SummarizingConversationManager (no memory configured) for MediaPlanningAgent
```

- **Agent cards**: Normal when agents don't use dynamic agent discovery
- **SummarizingConversationManager**: Expected fallback for local testing (vs AgentCore Memory)

## Troubleshooting

### Common Issues

**Import Errors**: Make sure you're using the correct virtual environment:
```bash
source agentcore/deployment/.venv/bin/activate
```

**Agent Not Found**: Use `--list-agents` to see available agents

**AWS Errors**: Verify credentials and Bedrock model access:
```bash
aws sts get-caller-identity
aws bedrock-runtime converse --model-id anthropic.claude-3-haiku-20240307-v1:0 --messages '[{"role":"user","content":[{"text":"test"}]}]' --region us-east-1
```

### Verbose Mode
Use `--verbose` to see detailed error information:
```bash
source agentcore/deployment/.venv/bin/activate && python local_agent_tester.py --agent MediaPlanningAgent --query "test" --verbose
```

## Testing Your Changes

1. **Modify Agent Instructions**: Edit files in `agentcore/deployment/agent/agent-instructions-library/`
2. **Test Locally**: Run the local tester to verify changes
3. **Deploy When Ready**: Use the normal deployment process

This allows rapid iteration on agent behavior without deployment cycles while preserving complete test records for analysis.
