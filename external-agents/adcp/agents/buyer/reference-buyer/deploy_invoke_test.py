"""
Invokes the deployed AgentCore Runtime agent using a real Cognito access
token, with a real 33+ character runtimeSessionId, to verify:
  1. Multi-turn conversation persistence against the live deployed endpoint.
  2. That the deployed runtime's Cognito JWT authorizer actually enforces
     auth: an unauthenticated call is rejected, and a call with a valid
     Cognito access token succeeds.

boto3's invoke_agent_runtime does not support bearer-token auth (only
SigV4), so this uses the raw HTTPS invoke endpoint directly, matching the
pattern in the AWS docs for JWT-authorized AgentCore Runtime agents.

Run from this directory:
    source .venv/bin/activate
    python3 deploy_invoke_test.py
"""


from aws_region import region
import json
import os
import urllib.parse
import uuid

import requests
from dotenv import load_dotenv

from auth import get_test_user_access_token

load_dotenv()

AGENT_RUNTIME_ARN = os.environ["AGENT_RUNTIME_ARN"]
REGION = region()
ESCAPED_ARN = urllib.parse.quote(AGENT_RUNTIME_ARN, safe="")
INVOKE_URL = f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{ESCAPED_ARN}/invocations?qualifier=DEFAULT"

# Real UUID4-based session id, well over AgentCore's 33-char minimum for
# runtimeSessionId, so the same session ID ties both invocations to the same
# runtime session and the same persisted conversation in S3.
SESSION_ID = f"adcp-buyer-agent-{uuid.uuid4()}"
assert len(SESSION_ID) >= 33, len(SESSION_ID)


def invoke(prompt: str, token: str | None) -> requests.Response:
    headers = {
        "Content-Type": "application/json",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": SESSION_ID,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return requests.post(
        INVOKE_URL, headers=headers, data=json.dumps({"prompt": prompt}), timeout=60
    )


def extract_text(body: str) -> str:
    text_out = []
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        json_str = line[5:].strip()
        if not json_str:
            continue
        try:
            event = json.loads(json_str)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "text":
            text_out.append(event["data"])
        elif event.get("type") == "error":
            print("ERROR EVENT:", event)
    return "".join(text_out)


print(f"Session ID ({len(SESSION_ID)} chars): {SESSION_ID}")

print("\n=== Negative case: no token ===")
resp = invoke("hi", token=None)
print("status:", resp.status_code)
print("body:", resp.text[:500])
assert resp.status_code == 401, f"expected 401, got {resp.status_code}"
print("Correctly rejected.")

print("\n=== Negative case: garbage token ===")
resp = invoke("hi", token="not-a-real-jwt")
print("status:", resp.status_code)
print("body:", resp.text[:500])
assert resp.status_code in (401, 403), f"expected 401/403, got {resp.status_code}"
print("Correctly rejected.")

token = get_test_user_access_token()
print(f"\nFetched real Cognito access token ({len(token)} chars) for the test user.")

print("\n=== Turn 1 (deployed agent, authenticated) ===")
resp = invoke(
    "My name is Zellest and I'm interested in CTV news inventory. "
    "Just acknowledge my name, don't search yet.",
    token=token,
)
assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text[:300]}"
print(extract_text(resp.text))

print("\n=== Turn 2 (same session, deployed agent, should remember my name) ===")
resp = invoke("What's my name? Answer in one short sentence, no tool calls needed.", token=token)
assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text[:300]}"
print(extract_text(resp.text))
