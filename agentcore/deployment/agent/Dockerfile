FROM public.ecr.aws/docker/library/python:3.13-slim
WORKDIR /app
# Copy requirements and install Python dependencies
COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt

# Set AWS region environment variables
ENV AWS_REGION=us-east-1
ENV AWS_DEFAULT_REGION=us-east-1

# Accept memory configuration as build arguments
ARG MEMORY_ID
ARG ACTOR_ID
ARG STACK_PREFIX
ARG UNIQUE_ID
ARG VISUALIZATIONS_TABLE_NAME
ARG APPSYNC_ENDPOINT
ARG APPSYNC_REALTIME_DOMAIN
ARG APPSYNC_CHANNEL_NAMESPACE

# Set memory environment variables
ENV MEMORY_ID=${MEMORY_ID}
ENV ACTOR_ID=${ACTOR_ID}
ENV STACK_PREFIX=${STACK_PREFIX}
ENV UNIQUE_ID=${UNIQUE_ID}
ENV APPSYNC_ENDPOINT=${APPSYNC_ENDPOINT}
ENV APPSYNC_REALTIME_DOMAIN=${APPSYNC_REALTIME_DOMAIN}
ENV APPSYNC_CHANNEL_NAMESPACE=${APPSYNC_CHANNEL_NAMESPACE}
ENV VISUALIZATIONS_TABLE_NAME=${VISUALIZATIONS_TABLE_NAME}
# Knowledge Base and Runtime Environment Variables
ARG KNOWLEDGEBASES=""
ARG RUNTIMES=""
ENV KNOWLEDGEBASES=${KNOWLEDGEBASES}
ENV RUNTIMES=${RUNTIMES}

# AdCP MCP Gateway Configuration
# When ADCP_GATEWAY_URL is set, agents will use the MCP Gateway for AdCP tools
# Otherwise, they fall back to local mock data
ARG ADCP_GATEWAY_URL=""
ARG ADCP_USE_MCP="true"
ENV ADCP_GATEWAY_URL=${ADCP_GATEWAY_URL}
ENV ADCP_USE_MCP=${ADCP_USE_MCP}
# Signal that this is running in Docker for host binding logic
ENV DOCKER_CONTAINER=1

# Add shared directory to Python path
ENV PYTHONPATH="/app/agentcore/shared"
COPY . .
# Create non-root user
RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore

EXPOSE 8080
EXPOSE 8000
EXPOSE 9000

# Initialize AgentCore Memory using simplified approach
RUN echo "Initializing AgentCore Memory..." && \
    python /app/agentcore/shared/memory_init.py || echo "Memory initialization failed, continuing..."

# Initialize External Tools
RUN echo "Initializing External Tools..." && \
    python /app/agentcore/shared/external_tools_init.py || echo "External tools initialization failed, continuing..."

CMD ["opentelemetry-instrument","python", "-m", "handler"]