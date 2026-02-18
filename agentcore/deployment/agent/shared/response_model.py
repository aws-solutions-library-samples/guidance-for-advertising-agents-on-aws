from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from strands import event_loop
import uuid
import json

class StructuredDataContent(BaseModel):
    type_of_content: str = Field(
        description="The type of content, example 'visualization'"
    )
    subtype: str = Field(
        description="The subtype of content, example 'metrics-visualization'"
    )
    data: dict = Field(
        description="The JSON-formatted structured data, example {'metrics': {'metric1': 1, 'metric2': 2}}"
    )


class Source(BaseModel):
    type_of_source: str = Field(
        description="The type of source, example 'Bedrock Knowledge Base' or 'API'"
    )
    source_format: str = Field(
        description="The format of the data source, options are 'JSON', 'CSV', or 'PARQUET'"
    )
    csv_rows: Optional[List[str]] = Field(
        description="If the source_format is CSV, the list of rows, each one represented as a string, with quotation-wrapped values separated by commas",
        default=None,
    )
    csv_headers: Optional[List[str]] = Field(
        description="If the source_format is CSV, the headers of the csv file", default=None
    )
    json_data: Optional[dict] = Field(
        description="If the source_format is JSON, the JSON-formatted data",
        default=None,
    )
    parquet_data: Optional[dict] = Field(
        description="If the source_format is Parquet, the Parquet-formatted data",
        default=None,
    )
    source_uri: str = Field(
        description="The source URI or path, example 's3://bucket-name/key' or 'https://example.com/data.csv'",default=""
    )
    content:str = Field(description="The content of the source, example 'header1,header2,header3\nrow1cell1,row1cell2,row1cell3'",default="")
    score:float=Field(description="The relevance score of the source, example 0.5", default=0.5)
    metadata:dict=Field(description="The metadata of the source, example {'location': {'s3Location': {'uri': 's3://bucket-name/key'}}}", default={})

class SourceSet(BaseModel):
    query: str = Field(
        description="The lookup query or natural language question used for retrieving the data"
    )
    sources: List[Source] = Field(description="The list of sources retrieved")


class ResponseModel(BaseModel):
    """Complete weather forecast information."""

    text_content: Optional[str] = Field(
        description="The content of a response that is not structured data",
        default=None,
    )
    prompt: Optional[str] = Field(
        description="The query or prompt that was sent to the agent or tool",
        default=None,
    )
    is_complete: bool = Field(
        description="Whether the response is complete or partial (in the case of streaming)",
        default=True,
    )
    structured_data_content: Optional[StructuredDataContent] = Field(
        description="The content of a response that is structured data", default=None
    )
    response_sources: Optional[List[SourceSet]] = Field(
        description="The sources retrieved and referenced during analysis", default=None
    )
    from_agent: str = Field(
        description="The name of the agent that generated this response"
    )
    to: str = Field(
        description="The intended recipient of this response. Either an agent's name, the user's name (if available, otherwise 'user'), or 'self' if this is reasoning",
        default="user",
    )
    response_type: str = Field(
        description="The type of information in this response. Either reasoning, delegation_to_agent, question_to_user, question_to_agent, collaborator_to_orchestrator, recommendation, alert, fact, summary, or analysis",
        default="analysis",
    )
    timestamp: str = Field(
        description="The timestamp of the response",
        default_factory=lambda: datetime.now().isoformat(),
    )
    session_id: str = Field(
        description="The session ID of the conversation", default="N/A"
    )
    related_response_id: Optional[str] = Field(
        description="The ID of the response that this response is related to. If this is the result of a delegation from the orchestrator, this would be the matching orchestrator's delegation_to_agent message's ID",
        default="N/A",
    )
    response_id: str = Field(
        description="The ID of the response", default_factory=lambda: str(uuid.uuid4())
    )

    @staticmethod
    def parse_event_loop_structure_to_response_model(
        event: dict,
        from_agent: str = "unknown",
        session_id: str = "N/A",
        prompt: Optional[str] = None,
        tool_context: Optional[dict] = None,
    ) -> "ResponseModel":
        """
        Parse an event loop structure to a ResponseModel instance

        Args:
            event: Event loop stop event dictionary containing 'stop', 'message', etc.
            from_agent: Name of the agent that generated this response
            session_id: Session ID of the conversation
            prompt: The query or prompt that was sent to the agent/tool
            tool_context: Context about which tool was called (e.g., {'tool': 'invoke_specialist_with_RAG', 'agent_name': 'X', 'orchestrator': 'Y'})

        Returns:
            ResponseModel: Response model instance
        """
        # Extract text content from the message
        text_content = None
        message = event.get("message", {})
        content = message.get("content", [])

        # Check for tool use in content blocks
        tool_uses = []
        for block in content:
            if isinstance(block, dict):
                if "text" in block:
                    text_content = block["text"]
                elif "toolUse" in block:
                    tool_uses.append(block["toolUse"])

        # Determine response type and recipient based on tool context
        response_type = "analysis"
        to = "user"
        response_sources = None

        if tool_context:
            tool_name = tool_context.get("tool")

            # Handle invoke_specialist_with_RAG - collaborator to orchestrator
            if tool_name == "invoke_specialist_with_RAG":
                response_type = "collaborator_to_orchestrator"
                to = tool_context.get("orchestrator", "orchestrator")
                from_agent = tool_context.get("agent_name", from_agent)

            # Handle retrieve_knowledge_base_results_tool - create SourceSet
            elif tool_name == "retrieve_knowledge_base_results_tool":
                kb_query = tool_context.get("knowledge_base_query", prompt or "")
                kb_sources = tool_context.get("sources", [])

                if kb_sources:
                    # Convert KB sources to Source objects
                    sources = []
                    for kb_source in kb_sources:
                        source_format = "CSV"
                        source = Source(
                            type_of_source="Bedrock Knowledge Base",
                            source_format=source_format,
                            content= kb_source.get("content", ""),
                            source_uri= json.dumps(kb_source.get("source", {})),
                            score= kb_source.get("score", 0.0),
                            metadata= kb_source.get("metadata", {})
                        )
                        sources.append(source)

                    # Create SourceSet
                    source_set = SourceSet(query=kb_query, sources=sources)
                    response_sources = [source_set]

        # Create the response model
        return ResponseModel(
            text_content=text_content,
            prompt=prompt,
            is_complete=True,
            from_agent=from_agent,
            session_id=session_id,
            to=to,
            response_type=response_type,
            response_sources=response_sources,
        )
