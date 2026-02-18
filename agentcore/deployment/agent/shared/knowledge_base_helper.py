"""
Shared Knowledge Base Helper for AgentCore Agents

This module provides utilities for knowledge base integration across all AgentCore agents.
It handles knowledge base ID resolution, retrieval, and source citation formatting.
"""

import os
import json
import logging
import boto3
from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass
from strands import Agent, tool
from strands_tools import http_request, use_llm, memory


@dataclass
class KnowledgeBaseSource:
    """Represents a knowledge base source citation"""

    content: str
    source: str
    score: float
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class KnowledgeBaseResult:
    """Represents the result of a knowledge base query"""

    agent: str
    sources: List[KnowledgeBaseSource]
    formatted_content: str
    query: str
    total_results: int


class KnowledgeBaseHelper:
    """Helper class for knowledge base operations in AgentCore agents"""

    def __init__(
        self, logger: Optional[logging.Logger] = None, region: Optional[str] = None
    ):
        self.logger = logger or logging.getLogger(__name__)
        self._kb_ids_cache = None
        self.region = region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        self._bedrock_agent_client = None

    def get_knowledge_base_by_name_pattern(self, name_pattern: str) -> Optional[str]:
        """
        Get knowledge base ID by name pattern (case-insensitive partial match)

        Args:
            name_pattern: Pattern to match against knowledge base names

        Returns:
            str: Knowledge base ID if found, None otherwise
        """
        # Discover knowledge bases from AWS
        kb_mapping = self._discover_knowledge_bases_from_aws()
        if not kb_mapping:
            return None

        name_pattern_lower = name_pattern.lower()

        # Try exact match first
        for kb_name, kb_id in kb_mapping.items():
            if kb_name.lower() == name_pattern_lower:
                return kb_id

        # Try partial match
        for kb_name, kb_id in kb_mapping.items():
            if name_pattern_lower in kb_name.lower():
                self.logger.info(
                    f"Found knowledge base by pattern '{name_pattern}': {kb_name} -> {kb_id}"
                )
                return kb_id

        return None

    def setup_knowledge_base_environment(self, primary_kb, agent_name: str) -> bool:
        """
        Set up knowledge base environment variables for STRANDS integration

        Args:
            kb_id: the id of the knowledgebase

        Returns:
            bool: True if knowledge base was successfully configured
        """
        self.logger.info(
            f"Setting up knowledge base with name: {primary_kb} for {agent_name}"
        )
        stack_prefix = os.environ.get("STACK_PREFIX", "default")
        unique_id = os.environ.get("UNIQUE_ID", "default")

        print(
            f"[KB_HELPER] Environment variables: STACK_PREFIX={stack_prefix}, UNIQUE_ID={unique_id}"
        )
        print(f"[KB_HELPER] Primary KB name from config: {primary_kb}")
        self.logger.info(
            f"Environment variables: STACK_PREFIX={stack_prefix}, UNIQUE_ID={unique_id}"
        )
        self.logger.info(f"Primary KB name from config: {primary_kb}")

        # Construct the full knowledge base name with stack prefix and unique ID
        full_kb_name = f"{stack_prefix}-{primary_kb}-{unique_id}"
        print(f"[KB_HELPER] Constructed full KB name: {full_kb_name}")
        self.logger.info(f"Constructed full KB name: {full_kb_name}")

        # First try direct AWS discovery by the full name pattern
        print(f"[KB_HELPER] Trying AWS discovery for full KB name: {full_kb_name}")
        self.logger.info(f"Trying AWS discovery for full KB name: {full_kb_name}")
        kb_id = self.get_knowledge_base_by_name_pattern(full_kb_name)
        if kb_id:
            os.environ["STRANDS_KNOWLEDGE_BASE_ID"] = kb_id
            print(
                f"[KB_HELPER] ✅ Set STRANDS_KNOWLEDGE_BASE_ID to {kb_id} for knowledge base: {full_kb_name}"
            )
            self.logger.info(
                f"✅ Set STRANDS_KNOWLEDGE_BASE_ID to {kb_id} for knowledge base: {full_kb_name}"
            )
            return True

        # Also try the base name in case it's not prefixed
        print(f"[KB_HELPER] Trying AWS discovery for base KB name: {primary_kb}")
        self.logger.info(f"Trying AWS discovery for base KB name: {primary_kb}")
        kb_id = self.get_knowledge_base_by_name_pattern(primary_kb)
        if kb_id:
            os.environ["STRANDS_KNOWLEDGE_BASE_ID"] = kb_id
            print(
                f"[KB_HELPER] ✅ Set STRANDS_KNOWLEDGE_BASE_ID to {kb_id} for knowledge base: {primary_kb}"
            )
            self.logger.info(
                f"✅ Set STRANDS_KNOWLEDGE_BASE_ID to {kb_id} for knowledge base: {primary_kb}"
            )
            return True

        # If environment variables are defaults, try to discover them from the runtime context
        if stack_prefix == "default" or unique_id == "default":
            self.logger.info(
                "Environment variables not set, trying to discover all knowledge bases..."
            )
            all_kbs = self.list_available_knowledge_bases()

            # Try to find any KB that contains the primary KB name
            for kb_name, kb_id in all_kbs.items():
                if primary_kb.lower() in kb_name.lower():
                    os.environ["STRANDS_KNOWLEDGE_BASE_ID"] = kb_id
                    self.logger.info(
                        f"✅ Found matching KB by pattern: {kb_name} -> {kb_id}"
                    )
                    return True

        # Fallback to file-based lookup with stack prefix formatting
        self.logger.info("Falling back to file-based knowledge base lookup...")

        kb_ids = self._load_knowledge_base_ids(stack_prefix, unique_id)
        if not kb_ids:
            self.logger.warning("No knowledge bases found via AWS API or file lookup")
            return False

        # Try with stack prefix formatting first (this is the expected format)
        if full_kb_name in kb_ids:
            kb_id = kb_ids[full_kb_name]
            os.environ["STRANDS_KNOWLEDGE_BASE_ID"] = kb_id
            self.logger.info(
                f"Set STRANDS_KNOWLEDGE_BASE_ID to {kb_id} for knowledge base: {full_kb_name}"
            )
            return True
        elif primary_kb in kb_ids:
            kb_id = kb_ids[primary_kb]
            os.environ["STRANDS_KNOWLEDGE_BASE_ID"] = kb_id
            self.logger.info(
                f"Set STRANDS_KNOWLEDGE_BASE_ID to {kb_id} for knowledge base: {primary_kb}"
            )
            return True
        else:
            self.logger.warning(
                f"Knowledge base '{primary_kb}' (or '{full_kb_name}') not found in Reference Datas"
            )
            self.logger.info(f"Reference Datas: {list(kb_ids.keys())}")
            return False

    def _get_bedrock_agent_client(self):
        """Get or create Bedrock Agent client"""
        if not self._bedrock_agent_client:
            try:
                self._bedrock_agent_client = boto3.client(
                    "bedrock-agent", region_name=self.region
                )
            except Exception as e:
                self.logger.error(f"Failed to create Bedrock Agent client: {e}")
                return None
        return self._bedrock_agent_client

    def _discover_knowledge_bases_from_environment(self) -> Optional[Dict[str, str]]:
        """Discover knowledge bases from environment variables (faster than API calls)"""
        try:
            import os

            # Get knowledge bases from environment variable
            kb_env = os.environ.get("KNOWLEDGEBASES", "")
            if not kb_env:
                self.logger.debug("No KNOWLEDGEBASES environment variable found")
                return None

            # Parse comma-separated "name:ID" pairs
            kb_mapping = {}
            kb_pairs = [pair.strip() for pair in kb_env.split(",") if pair.strip()]

            for pair in kb_pairs:
                if ":" in pair:
                    kb_name, kb_id = pair.split(":", 1)
                    kb_mapping[kb_name.strip()] = kb_id.strip()
                    self.logger.debug(
                        f"Found knowledge base from environment: {kb_name} -> {kb_id}"
                    )

            if kb_mapping:
                self.logger.info(
                    f"Discovered {len(kb_mapping)} knowledge bases from environment"
                )
                self._kb_ids_cache = kb_mapping
                return kb_mapping
            else:
                self.logger.debug("No valid knowledge base pairs found in environment")
                return None

        except Exception as e:
            self.logger.error(f"Error parsing knowledge bases from environment: {e}")
            return None

    def _discover_knowledge_bases_from_aws(self) -> Optional[Dict[str, str]]:
        """Discover knowledge bases from AWS Bedrock Agent API"""
        try:
            client = self._get_bedrock_agent_client()
            if not client:
                self.logger.error("Failed to create Bedrock Agent client")
                return None

            self.logger.info(
                "Discovering knowledge bases from AWS Bedrock Agent API..."
            )

            # List all knowledge bases
            response = client.list_knowledge_bases(maxResults=100)
            knowledge_bases = response.get("knowledgeBaseSummaries", [])

            kb_mapping = {}
            for kb in knowledge_bases:
                kb_id = kb.get("knowledgeBaseId")
                kb_name = kb.get("name")
                if kb_id and kb_name:
                    kb_mapping[kb_name] = kb_id
                    # print(f"[KB_HELPER] Found knowledge base: {kb_name} -> {kb_id}")
                    # self.logger.info(f"Found knowledge base: {kb_name} -> {kb_id}")

            # Cache the results
            self._kb_ids_cache = kb_mapping
            # print(f"[KB_HELPER] Discovered {len(kb_mapping)} knowledge bases from AWS")
            # self.logger.info(f"Discovered {len(kb_mapping)} knowledge bases from AWS")

            if not kb_mapping:
                print("[KB_HELPER] ⚠️ No knowledge bases found in AWS account")
                self.logger.warning("No knowledge bases found in AWS account")

            return kb_mapping

        except Exception as e:
            print(f"[KB_HELPER] ❌ Error discovering knowledge bases from AWS: {e}")
            self.logger.error(f"Error discovering knowledge bases from AWS: {e}")
            import traceback

            print(f"[KB_HELPER] Full traceback: {traceback.format_exc()}")
            self.logger.error(f"Full traceback: {traceback.format_exc()}")
            return None

    def _load_knowledge_base_ids(
        self, stack_prefix: str, unique_id: str
    ) -> Optional[Dict[str, str]]:
        """Load knowledge base IDs - first try environment, then AWS discovery, then fallback to file"""
        if self._kb_ids_cache:
            return self._kb_ids_cache

        # First try to discover from environment variables (fastest)
        kb_mapping = self._discover_knowledge_bases_from_environment()
        if kb_mapping:
            return kb_mapping

        # Then try to discover from AWS
        kb_mapping = self._discover_knowledge_bases_from_aws()
        if kb_mapping:
            return kb_mapping

        # Fallback to file-based lookup
        self.logger.info("Falling back to file-based knowledge base lookup...")
        kb_ids_file = f".kb-ids-{stack_prefix}-{unique_id}.json"

        try:
            # Try to read from project root first
            if os.path.exists(kb_ids_file):
                with open(kb_ids_file, "r") as f:
                    self._kb_ids_cache = json.load(f)
                    self.logger.info(f"Loaded knowledge bases from file: {kb_ids_file}")
                    return self._kb_ids_cache

            # Try alternative paths
            project_root = os.environ.get("PROJECT_ROOT", ".")
            alt_path = os.path.join(project_root, kb_ids_file)
            if os.path.exists(alt_path):
                with open(alt_path, "r") as f:
                    self._kb_ids_cache = json.load(f)
                    self.logger.info(f"Loaded knowledge bases from file: {alt_path}")
                    return self._kb_ids_cache

            self.logger.warning(f"Knowledge base IDs file not found: {kb_ids_file}")
            return None

        except Exception as e:
            self.logger.error(f"Error loading knowledge base IDs from file: {e}")
            return None

    def _retrieve_knowledge_base_results(
        self,
        query: str,
        agent: str,
        min_score: float = 0.4,
        max_results: int = 9,
        include_metadata: bool = True,
    ) -> Optional[dict]:
        """
        Retrieve and generate response from knowledge base using retrieve_and_generate API
        
        This method uses Bedrock's retrieve_and_generate which handles formatting
        and generation automatically, eliminating the need for complex CSV/JSON
        reconstruction in the UI.

        Args:
            query: Search query for knowledge base retrieval
            min_score: Minimum relevance threshold (default: 0.4)
            max_results: Maximum number of results to return (default: 9)
            include_metadata: Whether to include metadata in results

        Returns:
            KnowledgeBaseResult: Structured result with sources and formatted content
        """
        try:
            # Get the knowledge base ID from environment
            kb_id = os.environ.get("STRANDS_KNOWLEDGE_BASE_ID")
            if not kb_id:
                self.logger.error("STRANDS_KNOWLEDGE_BASE_ID not set in environment")
                return None

            # Get the foundation model ARN for generation
            # Default to Claude 3 Haiku for cost-effective generation
            model_arn = os.environ.get(
                "KB_MODEL_ARN",
                "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-3-haiku-20240307-v1:0"
            )

            # Create bedrock agent runtime client
            bedrock_agent_runtime = boto3.client(
                "bedrock-agent-runtime", region_name=self.region
            )

            # Use retrieve_and_generate API for automatic formatting and generation
            response = bedrock_agent_runtime.retrieve_and_generate(
                input={'text': query},
                retrieveAndGenerateConfiguration={
                    'type': 'KNOWLEDGE_BASE',
                    'knowledgeBaseConfiguration': {
                        'knowledgeBaseId': kb_id,
                        'modelArn': model_arn,
                        'retrievalConfiguration': {
                            'vectorSearchConfiguration': {
                                'numberOfResults': max_results
                            }
                        }
                    }
                }
            )
            
            '''
            {
    'sessionId': 'string',
    'output': {
        'text': 'string'
    },
    'citations': [
        {
            'generatedResponsePart': {
                'textResponsePart': {
                    'text': 'string',
                    'span': {
                        'start': 123,
                        'end': 123
                    }
                }
            },
            'retrievedReferences': [
                {
                    'content': {
                        'type': 'TEXT'|'IMAGE'|'ROW',
                        'text': 'string',
                        'byteContent': 'string',
                        'row': [
                            {
                                'columnName': 'string',
                                'columnValue': 'string',
                                'type': 'BLOB'|'BOOLEAN'|'DOUBLE'|'NULL'|'LONG'|'STRING'
                            },
                        ]
                    },
                    'location': {
                        'type': 'S3'|'WEB'|'CONFLUENCE'|'SALESFORCE'|'SHAREPOINT'|'CUSTOM'|'KENDRA'|'SQL',
                        's3Location': {
                            'uri': 'string'
                        },
                        'webLocation': {
                            'url': 'string'
                        },
                        'confluenceLocation': {
                            'url': 'string'
                        },
                        'salesforceLocation': {
                            'url': 'string'
                        },
                        'sharePointLocation': {
                            'url': 'string'
                        },
                        'customDocumentLocation': {
                            'id': 'string'
                        },
                        'kendraDocumentLocation': {
                            'uri': 'string'
                        },
                        'sqlLocation': {
                            'query': 'string'
                        }
                    },
                    'metadata': {
                        'string': {...}|[...]|123|123.4|'string'|True|None
                    }
                },
            ]
        },
    ],
    'guardrailAction': 'INTERVENED'|'NONE'
}
            '''
            self.logger.info(f"Retrieved and generated response from KB {kb_id}")

            # Extract the generated text
            generated_text = response.get('output', {}).get('text', '')
            print(f'{"="*80}\n[KB_HELPER] Generated text: {generated_text}\n{"="*80}')
            citations = response.get('citations', [])

            return response

        except Exception as e:
            self.logger.error(f"Error in retrieve_and_generate: {e}")
            import traceback
            self.logger.error(f"Full traceback: {traceback.format_exc()}")
            None

    def _parse_retrieve_and_generate_citations(
        self,
        citations: List[Dict[str, Any]],
        min_score: float = 0.4,
        include_metadata: bool = True,
    ) -> List[KnowledgeBaseSource]:
        """
        Parse citations from retrieve_and_generate response into structured sources
        
        Args:
            citations: List of citations from retrieve_and_generate response
            min_score: Minimum relevance threshold (not used for citations, kept for compatibility)
            include_metadata: Whether to include metadata in results
            
        Returns:
            List[KnowledgeBaseSource]: Parsed sources from citations
        """
        sources = []
        
        try:
            for citation in citations:
                # Extract retrieved references from each citation
                retrieved_refs = citation.get('retrievedReferences', [])
                
                for ref in retrieved_refs:
                    # Extract content - handle different content types
                    content_obj = ref.get('content', {})
                    content = ""
                    
                    # Handle text content
                    if 'text' in content_obj:
                        content = content_obj['text']
                    # Handle row-based content (for structured data like CSV)
                    elif 'row' in content_obj:
                        rows = content_obj['row']
                        # Format rows as readable text
                        row_texts = []
                        for row in rows:
                            col_name = row.get('columnName', '')
                            col_value = row.get('columnValue', '')
                            if col_name and col_value:
                                row_texts.append(f"{col_name}: {col_value}")
                        content = "\n".join(row_texts)
                    
                    # Extract location information
                    location = ref.get('location', {})
                    source_info = "Knowledge Base"
                    
                    if 's3Location' in location:
                        source_info = location['s3Location'].get('uri', source_info)
                    elif 'webLocation' in location:
                        source_info = location['webLocation'].get('url', source_info)
                    elif 'sqlLocation' in location:
                        source_info = f"SQL: {location['sqlLocation'].get('query', source_info)}"
                    
                    # Extract metadata
                    metadata = ref.get('metadata', {})
                    
                    # Add content type to metadata
                    if include_metadata:
                        if metadata is None:
                            metadata = {}
                        metadata['content_type'] = content_obj.get('type', 'text')
                    
                    # Create source (score is not available in citations, use 1.0)
                    sources.append(
                        KnowledgeBaseSource(
                            content=content,
                            source=source_info,
                            score=1.0,  # Citations don't have scores
                            metadata=metadata if include_metadata else None,
                        )
                    )
            
            self.logger.info(
                f"Parsed {len(sources)} sources from retrieve_and_generate citations"
            )
            
        except Exception as e:
            self.logger.error(f"Error parsing retrieve_and_generate citations: {e}")
            import traceback
            self.logger.error(f"Full traceback: {traceback.format_exc()}")
        
        return sources

    def _parse_aws_retrieve_response(
        self,
        retrieve_response: Dict[str, Any],
        min_score: float = 0.4,
        include_metadata: bool = True,
    ) -> List[KnowledgeBaseSource]:
        """
        Parse the AWS retrieve response into structured sources
        
        NOTE: This method is kept for backward compatibility but is no longer
        the primary retrieval method. Use retrieve_and_generate instead.
        """
        sources = []

        try:
            # Extract retrieval results from the AWS response
            retrieval_results = retrieve_response.get("retrievalResults", [])

            for result in retrieval_results:
                # Get the score and filter by minimum threshold
                score = result.get("score", 0.0)
                if score < min_score:
                    continue

                # Extract content and location information
                content = result.get("content", {}).get("text", "")
                print(f"Content: {content}")
                location = result.get("location", {})
                metadata = result.get("metadata", {})
                # Build source information
                source_info = "Knowledge Base"
                if location:
                    # Try to get more specific source information
                    if "s3Location" in location:
                        s3_location = location["s3Location"]
                        source_info = s3_location.get("uri", source_info)
                    elif "webLocation" in location:
                        web_location = location["webLocation"]
                        source_info = web_location.get("url", source_info)

                sources.append(
                    KnowledgeBaseSource(
                        content=content,
                        source=source_info,
                        score=score,
                        metadata=metadata if include_metadata else None,
                    )
                )

            self.logger.info(
                f"Parsed {len(sources)} sources from AWS retrieve response"
            )

        except Exception as e:
            self.logger.error(f"Error parsing AWS retrieve response: {e}")

        return sources

    def _parse_knowledge_base_result(
        self, result: Any, include_metadata: bool = True
    ) -> List[KnowledgeBaseSource]:
        """Legacy method - parse the raw knowledge base result into structured sources"""
        sources = []
        print(result)
        try:
            # Handle different result formats
            if hasattr(result, "content"):
                content = str(result.content)
                print(content)
            elif result:
                content = str(result)
                print(content)
            else:
                return sources

            # For now, treat the entire result as a single source
            # In a real implementation, you might parse multiple sources from the result
            sources.append(
                KnowledgeBaseSource(
                    content=result,
                    source="Knowledge Base",
                    score=1.0,
                    metadata=(
                        {"retrieval_method": "memory_tool"}
                        if include_metadata
                        else None
                    ),
                )
            )

        except Exception as e:
            self.logger.error(f"Error parsing knowledge base result: {e}")

        return sources

    def _enhance_csv_sources(
        self, sources: List[KnowledgeBaseSource]
    ) -> List[KnowledgeBaseSource]:
        """
        Enhance CSV sources with header metadata including column names and inferred data types

        Args:
            sources: List of knowledge base sources

        Returns:
            List[KnowledgeBaseSource]: Enhanced sources with CSV header metadata
        """
        enhanced_sources = []

        for source in sources:
            enhanced_source = source

            # Check if this source is a CSV file
            # print(f"[KB_HELPER] Checking if source is CSV: {source.source}")
            is_csv = self._is_csv_source(source.source)
            # print(f"[KB_HELPER] Is CSV result: {is_csv}")

            if is_csv:
                # print(f"[KB_HELPER] Processing CSV source: {source.source}")
                try:
                    # Extract CSV headers and data types
                    csv_headers, new_content, csv_rows = (
                        self._extract_csv_headers_and_types(source)
                    )
                    # print(csv_headers)
                    if csv_headers:
                        # Add CSV headers to metadata
                        if source.metadata is None:
                            source.metadata = {}

                        source.metadata["csv_headers"] = csv_headers
                        source.metadata["file_type"] = "csv"
                        source.metadata["rows"] = csv_rows

                        source.content = new_content
                        # print(f"[KB_HELPER] ✅ Enhanced CSV source {source.source} with {len(csv_headers)} headers: {[h['name'] for h in csv_headers]}")
                        # self.logger.info(
                        #    f"Enhanced CSV source {source.source} with {len(csv_headers)} headers"
                        # )
                    else:
                        print(
                            f"[KB_HELPER] ❌ No CSV headers extracted for {source.source}"
                        )
                except Exception as e:
                    print(
                        f"[KB_HELPER] ❌ Failed to extract CSV headers from {source.source}: {e}"
                    )
                    self.logger.warning(
                        f"Failed to extract CSV headers from {source.source}: {e}"
                    )

            enhanced_sources.append(enhanced_source)

        return enhanced_sources

    def _is_csv_source(self, source_uri: str) -> bool:
        """
        Check if a source URI points to a CSV file

        Args:
            source_uri: The source URI or path

        Returns:
            bool: True if the source appears to be a CSV file
        """
        if not source_uri:
            return False

        # Check file extension
        return source_uri.lower().endswith(".csv")

    def _extract_csv_headers_and_types(self, source: KnowledgeBaseSource) -> tuple:
        import csv
        import io

        # Get the original CSV file from S3
        csv_content = self._fetch_original_csv_file(source)
        if not csv_content:
            self.logger.warning(
                f"Could not fetch original CSV file for {source.source}"
            )
            return None, None, None

        # Split by newline and get the first row (headers)
        lines = csv_content.strip().split("\n")
        if not lines:
            return None, None, None

        # Extract headers from first line
        headers = lines[0].split(",")
        headers_with_types = []

        # Reform the content so that it splits properly
        flattened_for_excerpt_search = csv_content.replace("\n", " ")
        index_of_content = flattened_for_excerpt_search.find(source.content)
        new_content = source.content  # Default to original content
        rows = []

        if index_of_content != -1:
            # Python uses slicing, not substring method
            new_content = csv_content[
                index_of_content : index_of_content + len(source.content)
            ]
            rows = new_content.split("\n")
        else:
            # Fallback to processing available lines
            rows = []  # Get up to 10 data rows

        # Create headers with types
        for header in headers:
            headers_with_types.append(
                {
                    "name": header.strip(),
                    "type": "string",  # Default type, could be enhanced with type inference
                }
            )

        # print(f"[KB_HELPER] Extracted headers: {headers}")
        # self.logger.info(f"Extracted {len(headers_with_types)} CSV headers from {source.source}")
        return headers_with_types, new_content, rows

    def _fetch_original_csv_file(self, source: KnowledgeBaseSource) -> Optional[str]:
        """
        Fetch the original CSV file content from S3 based on the source URI

        Args:
            source: Knowledge base source with S3 URI

        Returns:
            Optional[str]: Complete CSV file content, or None if fetch fails
        """
        try:
            # Extract S3 location from source metadata or URI
            s3_uri = None

            # First try to get S3 location from metadata
            if source.metadata and "location" in source.metadata:
                location = source.metadata["location"]
                if isinstance(location, dict) and "s3Location" in location:
                    s3_uri = location["s3Location"].get("uri")

            # Fallback to source URI if it looks like an S3 URI
            if not s3_uri and source.source.startswith("s3://"):
                s3_uri = source.source

            if not s3_uri:
                self.logger.warning(f"No S3 URI found for source {source.source}")
                return None

            # Parse S3 URI (format: s3://bucket/key)
            if not s3_uri.startswith("s3://"):
                self.logger.warning(f"Invalid S3 URI format: {s3_uri}")
                return None

            # Remove s3:// prefix and split bucket/key
            s3_path = s3_uri[5:]  # Remove 's3://'
            parts = s3_path.split("/", 1)
            if len(parts) != 2:
                self.logger.warning(f"Invalid S3 path format: {s3_path}")
                return None

            bucket_name, object_key = parts

            # Create S3 client and fetch the file
            s3_client = boto3.client("s3", region_name=self.region)

            # self.logger.info(
            #    f"Fetching CSV file from S3: bucket={bucket_name}, key={object_key}"
            # )

            response = s3_client.get_object(Bucket=bucket_name, Key=object_key)
            csv_content = response["Body"].read().decode("utf-8")

            # self.logger.info(
            #    f"Successfully fetched CSV file ({len(csv_content)} characters)"
            # )
            return csv_content

        except Exception as e:
            self.logger.error(f"Error fetching original CSV file from S3: {e}")
            return None

    def _format_knowledge_base_content(self, sources: List[KnowledgeBaseSource]) -> str:
        """Format knowledge base sources into a readable string"""
        if not sources:
            return "No relevant information found."

        formatted_parts = []
        for i, source in enumerate(sources, 1):
            formatted_parts.append(f"Source {i}: {source.content}")

        return "\n\n".join(formatted_parts)

    def format_sources_for_ui(self, sources: List[KnowledgeBaseSource]) -> str:
        """
        Format sources for UI display in <sources> tags

        Args:
            sources: List of knowledge base sources

        Returns:
            str: Formatted sources string for UI detection
        """
        if not sources:
            return ""

        sources_content = []
        for i, source in enumerate(sources, 1):
            source_entry = {
                "id": i,
                "content": source.content,
                "source": source.source,
                "score": source.score,
            }
            if source.metadata:
                source_entry["metadata"] = source.metadata

            sources_content.append(source_entry)

        # Format as JSON for UI parsing
        sources_json = json.dumps(sources_content, indent=2)
        return f"<sources>\n{sources_json}\n</sources>"

    def format_raw_kb_result_for_ui(self, raw_kb_result: str, query: str) -> str:
        """
        Format a raw knowledge base result string for UI display with sources

        Args:
            raw_kb_result: Raw string result from knowledge base retrieval
            query: The query that was used for retrieval

        Returns:
            str: Original result with sources formatted for UI detection
        """
        if not raw_kb_result or raw_kb_result.strip() == "":
            return raw_kb_result

        # Create a source from the raw result
        source = KnowledgeBaseSource(
            content=raw_kb_result,
            source="Knowledge Base",
            score=1.0,
            metadata={"query": query, "retrieval_method": "raw_result"},
        )

        # Format sources for UI
        sources_ui = self.format_sources_for_ui([source])

        # Return original result with sources appended
        return f"{raw_kb_result}\n\n{sources_ui}"

    def list_available_knowledge_bases(self) -> Dict[str, str]:
        """
        List all Reference Datas in the AWS account

        Returns:
            Dict[str, str]: Mapping of knowledge base names to IDs
        """
        # First try environment variables
        kb_mapping = self._discover_knowledge_bases_from_environment()
        if not kb_mapping:
            # Fallback to AWS API
            kb_mapping = self._discover_knowledge_bases_from_aws()

        if kb_mapping:
            self.logger.info("Reference Datas:")
            for name, kb_id in kb_mapping.items():
                self.logger.info(f"  - {name}: {kb_id}")
        else:
            self.logger.warning(
                "No knowledge bases found in environment or AWS account"
            )
        return kb_mapping or {}

    def enhance_response_with_sources(
        self, response: str, kb_result: KnowledgeBaseResult
    ) -> str:
        """
        Enhance an agent response with knowledge base sources

        Args:
            response: Original agent response
            kb_result: Knowledge base retrieval result

        Returns:
            str: Enhanced response with sources appended
        """
        if not kb_result.sources:
            return response

        sources_section = self.format_sources_for_ui(kb_result.sources)
        return f"{response}\n\n{sources_section}"

    def create_knowledge_base_tool(self, agent_config: Dict[str, Any]):
        """
        Create a knowledge base retrieval tool function for an agent

        Args:
            agent_config: Agent configuration

        Returns:
            Callable: Tool function for knowledge base retrieval
        """
        # Set up knowledge base environment
        kb_configured = self.setup_knowledge_base_environment(agent_config)
        print(f"[KB_HELPER] Knowledge base configured: {kb_configured}")
        self.logger.info(f"Knowledge base configured: {kb_configured}")

        def knowledge_base_retrieval_tool(
            query: str, min_score: float = 0.4, max_results: int = 9
        ) -> str:
            """
            Retrieve information from the agent's knowledge base

            Args:
                query: Search query for knowledge base retrieval
                min_score: Minimum relevance threshold (default: 0.4)
                max_results: Maximum number of results to return (default: 9)

            Returns:
                str: Retrieved knowledge base information with sources
            """
            if not kb_configured:
                print(
                    f"[KB_HELPER] ❌ Knowledge base not configured. Config was: {agent_config}"
                )
                self.logger.error(
                    f"Knowledge base not configured. Config was: {agent_config}"
                )
                return "Knowledge base not configured for this agent."

            kb_result = self.retrieve_knowledge_base_info(query, min_score, max_results)

            # Return formatted content with sources for UI detection
            return kb_result

        return knowledge_base_retrieval_tool

    def create_direct_kb_tool(self, agent_config: Dict[str, Any]):
        """
        Create a direct knowledge base retrieval tool that returns raw AWS response
        (following the pattern from the example notebook)

        Args:
            agent_config: Agent configuration

        Returns:
            Callable: Tool function that returns raw AWS retrieve response
        """
        # Set up knowledge base environment
        kb_configured = self.setup_knowledge_base_environment(agent_config)
        print(f"[KB_HELPER] Direct KB tool configured: {kb_configured}")
        self.logger.info(f"Direct KB tool configured: {kb_configured}")

        @tool
        def knowledge_base_assistant(query: str) -> str:
            """
            Handle document-based, narrative, and conceptual queries using the knowledge base.

            Args:
                query: A question about business strategies, policies, company information,
                       or requiring document comprehension and qualitative analysis

            Returns:
                Raw retrieve response from the knowledge base
            """
            if not kb_configured:
                return f"Error: Knowledge base not configured for this agent."

            try:
                # Get the knowledge base ID from environment
                kb_id = os.environ.get("STRANDS_KNOWLEDGE_BASE_ID")
                if not kb_id:
                    return "Error: STRANDS_KNOWLEDGE_BASE_ID not set in environment"

                # Create bedrock agent runtime client
                bedrock_agent_runtime = boto3.client(
                    "bedrock-agent-runtime", region_name=self.region
                )

                # Use the direct AWS API call like in the example
                retrieve_response = bedrock_agent_runtime.retrieve(
                    knowledgeBaseId=kb_id,
                    retrievalQuery={"text": query},
                    retrievalConfiguration={
                        "vectorSearchConfiguration": {
                            "numberOfResults": 10,
                        }
                    },
                )

                return retrieve_response

            except Exception as e:
                return f"Error in knowledge base assistant: {str(e)}"

        return knowledge_base_assistant

    def retrieve_knowledge_base_info(
        self, query: str, agent: str, min_score: float = 0.4, max_results: int = 9
    ) -> Optional[dict]:
        """
        Public method to retrieve knowledge base information

        Args:
            query: Search query for knowledge base retrieval
            min_score: Minimum relevance threshold (default: 0.4)
            max_results: Maximum number of results to return (default: 9)

        Returns:
            KnowledgeBaseResult: Structured result with sources and formatted content
        """
        if agent is None:
            return None

        return self._retrieve_knowledge_base_results(
            query, agent, min_score, max_results, True
        )


# Global instance for easy import
knowledge_base_helper = KnowledgeBaseHelper()


def retrieve_knowledge_base_results(
    query: str,
    agent: str,
    min_score: float = 0.4,
    max_results: int = 9,
    include_metadata: bool = True,
) -> Optional[dict]:
    """
    Convenience function to retrieve knowledge base results using the direct AWS API approach

    Args:
        query: Search query for knowledge base retrieval
        min_score: Minimum relevance threshold (default: 0.4)
        max_results: Maximum number of results to return (default: 9)
        include_metadata: Whether to include metadata in results

    Returns:
        KnowledgeBaseResult: Structured result with sources and formatted content
    """
    helper = KnowledgeBaseHelper(
        region=os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    )
    return helper._retrieve_knowledge_base_results(
        query, agent, min_score, max_results, include_metadata
    )


def retrieve_raw_kb_response(
    query: str, kb_id: str, max_results: int = 10, region: Optional[str] = None
) -> dict:
    """
    Convenience function to get raw AWS retrieve response (like in the example)

    Args:
        query: Search query for knowledge base retrieval
        kb_id: Knowledge base ID
        max_results: Maximum number of results to return (default: 10)
        region: AWS region (optional, defaults to environment or us-east-1)

    Returns:
        dict: Raw AWS retrieve response
    """
    import boto3

    region = region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    bedrock_agent_runtime = boto3.client("bedrock-agent-runtime", region_name=region)

    retrieve_response = bedrock_agent_runtime.retrieve(
        knowledgeBaseId=kb_id,
        retrievalQuery={"text": query},
        retrievalConfiguration={
            "vectorSearchConfiguration": {
                "numberOfResults": max_results,
            }
        },
    )

    return retrieve_response


def setup_agent_knowledge_base(
    kb_name: str, agent_name: str, region: Optional[str] = None
) -> bool:
    """
    Convenience function to set up knowledge base for an agent

    Args:
        agent_config: Agent configuration dictionary
        region: AWS region (optional, defaults to environment or us-east-1)

    Returns:
        bool: True if knowledge base was successfully configured
    """
    helper = KnowledgeBaseHelper(
        region=os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    )
    return helper.setup_knowledge_base_environment(kb_name, agent_name)


def get_knowledge_base_tool(agent_config: Dict[str, Any], region: Optional[str] = None):
    """
    Convenience function to get a knowledge base tool for an agent

    Args:
        agent_config: Agent configuration dictionary
        region: AWS region (optional, defaults to environment or us-east-1)

    Returns:
        Callable: Knowledge base retrieval tool function
    """
    helper = KnowledgeBaseHelper(
        region=os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    )
    return helper.create_knowledge_base_tool(agent_config)


def get_direct_kb_tool(agent_config: Dict[str, Any], region: Optional[str] = None):
    """
    Convenience function to get a direct knowledge base tool that returns raw AWS response
    (following the pattern from the example notebook)

    Args:
        agent_config: Agent configuration dictionary
        region: AWS region (optional, defaults to environment or us-east-1)

    Returns:
        Callable: Direct knowledge base retrieval tool function
    """
    helper = KnowledgeBaseHelper(
        region=region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    )
    return helper.create_direct_kb_tool(agent_config)


def enhance_agent_response_with_kb(
    response: str,
    query: str,
    agent_config: Dict[str, Any],
    min_score: float = 0.4,
    max_results: int = 5,
    region: Optional[str] = None,
) -> str:
    """
    Convenience function to enhance an agent response with knowledge base sources

    Args:
        response: Original agent response
        query: Query to search knowledge base with
        agent_config: Agent configuration
        min_score: Minimum relevance threshold
        max_results: Maximum number of results
        region: AWS region (optional, defaults to environment or us-east-1)

    Returns:
        str: Enhanced response with knowledge base sources
    """
    # Set up knowledge base
    helper = KnowledgeBaseHelper(region=region)
    kb_configured = helper.setup_knowledge_base_environment(agent_config)
    if not kb_configured:
        return response

    # Retrieve relevant information
    kb_result = helper.retrieve_knowledge_base_info(query, min_score, max_results)

    # Enhance response with sources
    return helper.enhance_response_with_sources(response, kb_result)


def format_kb_result_with_sources(
    kb_result: str, query: str, region: Optional[str] = None
) -> str:
    """
    Convenience function to format an existing KB result with sources for UI display

    Args:
        kb_result: Raw knowledge base result string
        query: The query that was used for retrieval
        region: AWS region (optional, defaults to environment or us-east-1)

    Returns:
        str: KB result with sources formatted for UI detection
    """
    helper = KnowledgeBaseHelper(region=region)
    return helper.format_raw_kb_result_for_ui(kb_result, query)


def list_available_knowledge_bases(region: Optional[str] = None) -> Dict[str, str]:
    """
    Convenience function to list all Reference Datas

    Args:
        region: AWS region (optional, defaults to environment or us-east-1)

    Returns:
        Dict[str, str]: Mapping of knowledge base names to IDs
    """
    helper = KnowledgeBaseHelper(region=region)
    return helper.list_available_knowledge_bases()
