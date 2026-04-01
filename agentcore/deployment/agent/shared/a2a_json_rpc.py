"""
A2A JSON-RPC 2.0 envelope utilities.

Pure functions for building and parsing JSON-RPC 2.0 messages
used in Agent-to-Agent (A2A) protocol communication.
"""

import uuid
import logging

logger = logging.getLogger(__name__)


def build_json_rpc_request(prompt: str, session_id: str) -> dict:
    """Build a JSON-RPC 2.0 message/send request envelope.

    Args:
        prompt: The text prompt to send to the remote agent.
        session_id: The current conversation session identifier.

    Returns:
        A dict representing a valid JSON-RPC 2.0 request with method "message/send".
    """
    return {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [
                    {"kind": "text", "text": prompt}
                ]
            },
            "sessionId": session_id
        }
    }


def parse_json_rpc_response(response: dict) -> str:
    """Extract text content from a JSON-RPC 2.0 response.

    Handles success responses by extracting text from result.artifacts[*].parts[*].text,
    error responses by returning a descriptive error string, and malformed responses
    by returning an error message.

    Args:
        response: A dict representing a JSON-RPC 2.0 response.

    Returns:
        The extracted text content, or a descriptive error string.
    """
    if not isinstance(response, dict):
        return "Error: Malformed response from remote agent — expected a JSON object"

    # Handle error responses
    if "error" in response:
        error = response["error"]
        if isinstance(error, dict):
            code = error.get("code", "unknown")
            message = error.get("message", "Unknown error")
            return f"Error: JSON-RPC error {code} — {message}"
        return "Error: JSON-RPC error — malformed error object"

    # Handle success responses
    result = response.get("result")
    if result is None:
        return "Error: Malformed response from remote agent — missing 'result' field"

    artifacts = result.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) == 0:
        return "Error: Malformed response from remote agent — missing or empty 'artifacts'"

    texts = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        parts = artifact.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if isinstance(part, dict) and part.get("kind") == "text" and "text" in part:
                texts.append(part["text"])

    if not texts:
        return "Error: Malformed response from remote agent — no text content found in artifacts"

    return "\n".join(texts)
