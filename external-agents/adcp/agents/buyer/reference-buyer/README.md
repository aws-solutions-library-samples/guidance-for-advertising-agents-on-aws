# AdCP Buyer Agent on AWS Bedrock AgentCore

An AI buyer agent, built on [AWS Bedrock AgentCore](https://aws.amazon.com/bedrock/agentcore/)
and [Strands Agents](https://strandsagents.com/), that calls an AdCP seller
agent over MCP or A2A. Which seller it talks to, and which of those two
transports is used to reach it, are both configurable — see "Seller agent
registry" below — rather than hardcoded to one endpoint/transport. This
builds on the same AdCP integration used in `../../../src/client.ts` (the
plain TypeScript buyer agent), but as a reasoning agent with a chat UI
instead of a fixed script.

Unlike the TypeScript client, which runs two hardcoded tool calls in a fixed
order, this agent has an LLM (Claude, via Amazon Bedrock) deciding which AdCP
tools to call and when, based on what the user asks for.

This Buyer Agent is deployed twice, as two separate AgentCore Runtimes
sharing the same tools/seller registry/reasoning-recording, but speaking
two different protocols to two different audiences:

- **HTTP** (`app.py`) — this project's own chat UI talks to this one.
- **A2A** (`a2a_runtime/a2a_entrypoint.py`) — lets *other* agents invoke
  this Buyer Agent directly over the Agent-to-Agent protocol, the same way
  this project's own tools call out to AdCP seller agents over A2A. See "Buyer
  Agent as an A2A server" below.

The chat UI also has a **Direct client** mode (no LLM — one raw AdCP tool
call straight to the selected seller) alongside the default **Buyer Agent**
mode, so the original "just hit the endpoint" behavior this project started
with stays available rather than being replaced by the agent. See "UI
modes" below.

## Architecture

The chat UI (`static/index.html`) talks to AWS directly from the browser —
it does not route through this Python process for either login or chat.
`python3 app.py` exists only to serve the static HTML/JS/CSS locally and to
provide a `/config` endpoint with the non-secret IDs the browser needs
(Cognito pool/client, the deployed runtime's ARN/region). `app.py`'s own
`/invocations` route is the AgentCore Runtime entrypoint contract — it's
what runs when the runtime is deployed/invoked — but the browser calls the
*deployed* runtime's AWS-hosted invoke endpoint, not this local copy of it.

```
Browser (static/index.html)
  │
  ├─ GET /config ─────────────────────► app.py (local, static-file host only)
  │  <- { cognito_region, cognito_client_id, agent_runtime_arn, aws_region }
  │
  ├─ POST https://cognito-idp.{region}.amazonaws.com/  (InitiateAuth)
  │  <- Cognito access token
  │
  ▼  POST https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{arn}/invocations
  │     Authorization: Bearer <token>
  │     X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: <uuid, >=33 chars>
  │  <- SSE stream of {text | tool_call | tool_result | done | error}
  ▼
AWS Bedrock AgentCore Runtime (deployed instance, see "Deployed instance")
  │
  ▼
app.py (BedrockAgentCoreApp / Starlette) — same code, running in the container
  │
  ▼
agent.py (Strands Agent, Bedrock Claude model)
  │  resolves the selected seller_agent_id via seller_agents.py,
  │  decides which tool(s) to call based on the prompt
  ▼
adcp_tools.py (5 tools, bound to the selected seller's url/transport/headers)
  │  transport "mcp": streamablehttp_client + ClientSession, per call
  │  transport "a2a": JSONRPC message/send with a {skill, parameters} DataPart
  ▼
Selected AdCP seller agent (see "Seller agent registry" — any AdCP
seller can be added; ../../seller/reference-seller is one built-in example)
```

Tool results also stream back to the browser as `tool_result` SSE events,
which the UI renders both in the side "Live tool activity" panel and as an
inline chat bubble in the main conversation (see "Inline seller responses"
below) — the same event, two presentations, not two separate calls.

Both Cognito's `InitiateAuth` and AgentCore Runtime's invoke endpoint return
`Access-Control-Allow-Origin: *` on preflight and on the actual request, so
no backend proxy is needed for the browser to reach either service directly.

Every tool call is a network request to the selected seller's endpoint, over
whichever transport (MCP or A2A) that seller's registry entry specifies.
See the [no-fabricated-data steering rule](../../../.kiro/steering) this project
follows for the conventions around fixture data (used by the
`../../seller/reference-seller` sandbox agent's inventory, which it marks
`sandbox: true` on every response).

## Seller agent registry

The UI's seller dropdown and the agent's tools both resolve which seller to
call from a single registry, `SELLER_AGENTS_JSON` in `.env` (see
`seller_agents.py` for the full schema):

```json
[
  {
    "id": "reference",
    "name": "AdCP Reference Test Seller (sandbox)",
    "url": "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/.../invocations?qualifier=DEFAULT",
    "transport": "mcp",
    "auth_type": "cognito_bearer"
  },
  {
    "id": "external-seller",
    "name": "Third-party seller agent (MCP)",
    "url": "https://adcp.example.com/adcp/mcp",
    "transport": "mcp",
    "auth_type": "static_bearer",
    "auth_token_env": "EXTERNAL_SELLER_AUTH_TOKEN"
  }
]
```

`transport` picks which of AdCP's two bindings is used to reach that entry:
`"mcp"` (the default if the field is omitted, for backward compatibility
with entries written before this field existed) opens a streamable-HTTP MCP
session and calls the tool by name; `"a2a"` sends a JSONRPC `message/send`
request with a structured `{"kind":"data","data":{"skill":...,"parameters":...}}`
part, where the AdCP tool name doubles as the skill name, per AdCP's A2A
guide. The same seller may appear twice under different `id`s, once per
transport, when it is reachable both ways, which lets the UI's dropdown switch
between hitting one seller over MCP vs. A2A. Note the cost of doing so: a
request that names no `seller_agent_id` fans out to **every** entry in the
registry (`agent.py::build_agent`), so a seller listed twice answers the same
brief twice.

A seller reachable over both bindings is one such case: some expose a separate
A2A endpoint alongside their MCP one, with its own bespoke skill names, that
also handles a structured `{skill, parameters}` call using AdCP tool names. See
`../../../.kiro/steering/protocol-frameworks.md` for how this repo distinguishes
AdCP, AAMP, and A2A Direct as three separate frameworks rather than three
quality levels of the same one. Listing such a seller once per transport means
it answers the same brief twice, so pick one binding unless you are
deliberately exercising both. Either way it is a config change, not a code
change.

Two `auth_type` values:

- `"static_bearer"` — a long-lived bearer token read from the env var named
  by `auth_token_env`. Note that variable is not forwarded to the deployed
  container automatically — add it to `runtime_env.build_env_vars`. Used for third-party
  seller agents you don't control.
- `"cognito_bearer"` — mints a fresh Cognito access token per call using
  this project's own test user (`auth.get_test_user_access_token()`). Used
  for seller agents deployed to this same AWS account's AgentCore Runtime
  and gated by the same Cognito pool this buyer agent uses — no second
  identity provider needed. See `../../seller/reference-seller`, which uses this.

`DEFAULT_SELLER_AGENT_ID` picks which entry is used when a request doesn't
specify one (currently `"reference"`); the browser lets you switch sellers
via a dropdown in the header, which persists per-tab in `sessionStorage`
and is sent as `seller_agent_id` in the `/invocations` payload. Switching
sellers mid-conversation starts a fresh runtime session, since the old
session's history would reference the previous seller's inventory/IDs.

`adcp_tools.py::build_adcp_tools()` builds a fresh set of tool closures
bound to whichever seller was resolved for that conversation, so the same
tool implementations work against any seller in the registry without
per-seller code branches. Only core AdCP tasks are registered. If you add a
seller's vendor-namespaced extension tool, a seller that doesn't implement it
returns an `"Unknown tool"` MCP error, which the agent reports plainly per its
system prompt rather than hiding the tool per-seller.

### AdCP Reference Test Seller

`../../seller/reference-seller` is a separate AgentCore project (built with the
current [AgentCore CLI](https://github.com/aws/agentcore-cli), not this
project's older `bedrock-agentcore-starter-toolkit`-based deploy scripts)
implementing a minimal, spec-conformant AdCP sales agent with fixed sandbox
inventory. See its own README for what it is, how it was built, and how it
was deployed. It exists so this buyer agent (and anyone testing the seller
dropdown) has a seller to talk to without needing any third-party credentials.

### Inline seller responses

Every tool call's result appears in the chat panel itself, as its own
message bubble labeled `{seller name} — {tool name}` (e.g. `AdCP Reference
Test Seller (sandbox) — adcp_get_products`), positioned in
the conversation right where that tool call happened — before the buyer
agent's own summary of it, matching the order the events actually occurred
in. This is in addition to, not instead of, the existing "Live tool
activity" side panel: the side panel is the fuller/technical view (status,
raw JSON, scrollable), the inline bubble is the same result surfaced as
part of the conversation so you don't have to look sideways to see what
the seller actually returned.

Both come from the same `tool_result` SSE event `app.py`/`agent.py` stream
to the browser — `static/index.html`'s `buildSellerMessageEl()` /
`finishToolCard()` render it two ways, not two separate calls. If a tool
call fails (e.g. `"Unknown tool"` from a seller that doesn't implement it,
or an A2A endpoint's non-AdCP response), that's what shows up in both
places — the bubble is not filtered to only show "successful-looking"
results.

## Choosing which agent you chat to

The header's **agent dropdown** lists the A2A agents this UI can chat to, from
the `AGENTS_JSON` registry (`agents_registry.py`, generated by
`deploy_agents_registry.py` and exposed through `/config`). Adding an agent —
e.g. a new A2A seller — is a config entry, not a UI change.

| | |
|---|---|
| `kind: buyer` | the agent talks to a seller on your behalf, so the **seller dropdown stays active** for it |
| `kind: seller` | you chat to it directly; the seller dropdown is hidden as meaningless |
| `origin: internal` | we deploy it, so its reasoning steps are in our sessions table and its **tool activity is shown** |
| `origin: external` | someone else's deployment; the side panel says its tool calls aren't visible rather than looking empty |

**Two request paths**, decided by the registry's derived `requires_proxy`:

- **direct** — the browser POSTs `message/stream` to the agent's own endpoint
  with the signed-in user's Cognito token. AWS's invoke endpoint sends
  permissive CORS headers, which is how this UI already reaches the runtime.
- **proxied** — the agent's credential is a secret that must not reach a
  browser (a third-party seller's static bearer token), so the turn goes to
  `action: "a2a_chat"` on our own runtime, which relays it
  (`app.py::_handle_a2a_chat`) and also sidesteps the external endpoint's CORS
  policy. That action refuses to relay an agent the browser could call itself,
  so this runtime's identity is never put on a call you could make as yourself.

Both paths normalise to the same `{text|error|done}` events, so one renderer
serves both.

### Where tool activity comes from on each transport

Verified live: A2A's `message/stream` **does** stream incrementally (one `task`
event then `artifact-update` text deltas), but it carries **no tool-call or
tool-result detail at all** — none in the stream, none in `message/send`'s
final result either.

So a chat over A2A gets its tool cards from the agent's **recorded reasoning
steps**, polled once a second during the turn and reconciled once more after
the stream closes, de-duplicated by `step_index`. They render through the exact
same `addToolCard` / `finishToolCard` / seller-bubble path the HTTP mode used,
so the presentation is identical; tool cards just trail the text by up to a
second. Rejected alternative: interleaving tool events into the A2A stream as
custom DataParts — it needs a `StrandsA2AExecutor` override, only ever works
for agents we control, and creates a second source of truth for the same facts.

### Seller agents record their own sessions

Both seller agents record one session turn per skill dispatch, so a
conversation driven *at* a seller shows its own progression in the Sessions
view. It hooks in through the AdCP SDK's `SkillMiddleware` seam
(`serve(middleware=[...])`), which wraps every dispatch on **both** the MCP and
A2A transports — so one wiring covers today's MCP runtimes and a future A2A
one, with no per-handler edits. Deliberately not the SDK's own
`make_audit_middleware`: its `AuditEvent` carries the operation name and timing
but not the params or result, and the UI's tool cards show the real input and
output.

Grouping those calls into one conversation needed a fix that isn't obvious:
**AgentCore forwards a header into a container only if that agent allowlists
it**, and both sellers allowlisted only `Authorization`. So no platform session
header could reach seller code — and `Mcp-Session-Id` is platform-managed in
stateless MCP anyway, identifying a microVM session rather than a conversation.
The buyer therefore sends its own **`X-Adcp-Buyer-Session-Id`** on every seller
call (both transports), which both sellers allowlist. Each
`incoming_request` step stores `sessionIdSource`, so a record always states
which identifier produced its grouping — including the honest
`generated (buyer sent none)` case. Confirmed live: an audio-seller session recorded
`sessionIdSource = buyer-session-header` with its `tool_call`/`tool_result`
steps grouped under one buyer-derived session id.

Recording is best-effort throughout (`seller_session_recorder._safe`): a
missing table, missing permission or DynamoDB stall can never change what a
seller returns.

**Setup:** the recording modules are single-sourced in this package and copied
into each seller by `deploy_all.py --only vendor-session-module` before that
seller deploys (gitignored build artifacts, regenerated every run so a stale
copy can't ship). Each seller's DynamoDB grant is a
`sessions-table-policy.json` declared in its own `agentcore.json`
`additionalPolicies` — `PutItem`/`UpdateItem`/`GetItem` (the last is what the
atomic step counter needs), scoped to the table ARN and its indexes, no
wildcards.

## Direct client mode (removed from the UI, still on the API)

There used to be a **Buyer Agent** / **Direct client** toggle in the header.
It's gone: one control now chooses what you're talking to (see "Choosing which
agent you chat to" above), and every chat target is an A2A agent.

The capability itself is intact over the API. Posting
`{"mode": "direct", "tool_name": ..., "tool_input": ...}` to `/invocations`
still calls that one AdCP tool straight against the selected seller — the exact
same `adcp_tools.call_seller_tool()` every Buyer Agent tool uses, no model in
the loop — and streams back the real result (`app.py::_invoke_direct`). It
records session steps like any other turn.

**What was lost:** the fastest way to tell a seller-side bug from a
buyer-agent bug was a single click. It's now a scripted call.

## Session recording and the Sessions dropdown

Every conversation — a browser chat turn, a Direct-client call, or a real
external agent invoking the Buyer Agent over A2A (see below) — is recorded
as a "session" in DynamoDB (`session_store.py`), regardless of which
runtime or mode handled it. Each session has a `META` item (status,
invoker, mode, seller, request preview, timestamps) and one `STEP` item per
reasoning event (`incoming_request`, `thought`, `tool_call`, `tool_result`,
`response`), ordered by an atomic per-session counter so concurrent tool
calls in one turn never race on ordering.

For the Buyer Agent mode, steps are recorded by `reasoning_hooks.py`'s
`ReasoningRecorder` — a Strands `HookProvider` wired into every agent
`build_agent()` constructs (`agent.py`), so it fires on real
`BeforeToolCallEvent`/`AfterToolCallEvent`/`AfterModelCallEvent`/
`AfterInvocationEvent` hooks regardless of which entrypoint (HTTP or A2A)
invoked the agent. For Direct-client mode, `app.py::_invoke_direct` records
the same four step types explicitly around its one tool call. Recording is
best-effort (`reasoning_hooks.py::_safe`): a DynamoDB hiccup or a missing
`SESSIONS_TABLE_NAME` never breaks the actual agent invocation, it just
means that turn's steps don't show up in the viewer.

The header's **sessions dropdown** lists the Buyer Agent's other
active/recent sessions — any invoker, any mode, any runtime — via
`action: "list_sessions"` on the same authenticated `/invocations`
entrypoint (no separate Cognito Identity Pool or direct-DynamoDB access
from the browser; see `app.py`'s `_handle_list_sessions`). Selecting one
turns the **main chat column** into a **read-only replay of that
conversation**. The list is scoped to the selected agent by default, with an
explicit **All agents** option next to it, and every entry is labelled with the
agent that ran it (`agent_id`/`agent_name` on the `META` record). A record
written before agents reported their identity reads as "unknown agent" — never
backfilled to the buyer agent. Replay itself renders as a conversation rather
than a list of cards in the side panel: the caller's
message renders as a user bubble, recorded `thought` steps as labeled
reasoning bubbles, the seller's tool responses inline as seller bubbles,
and the agent's reply markdown-rendered — the same bubbles a live
conversation uses — while its `tool_call`/`tool_result` steps fill the side
panel exactly as live ones do. The composer is replaced by a read-only bar
with a "Back to my chat" button, since there is nothing to send into
someone else's conversation. Your own live transcript is detached, not
discarded, so returning restores it as it was. It renders that session's
full timeline (`action: "get_session_steps"`) and polls every 2 seconds for
new steps while `status` is `active`; a failed turn's `error_message` (which
lives on the `META` record, not in the steps) is surfaced in the transcript
so the conversation doesn't just stop unexplained. There's no
separate real-time transport (no IoT/WebSocket) — your own in-flight turn
already streams live over the same SSE connection the composer/direct-form
use; viewing *someone else's* session (or your own past ones) has no open
connection to piggyback on, so it polls instead. Sending a new message or
Direct-client call always switches you back to watching your own live
turn.

Session records are TTLed out of DynamoDB after 24 hours
(`session_store.py::TTL_SECONDS`) — this is a live/recent view, not an
audit log; a page reload can lose sessions older than that.

## Buyer Agent as an A2A server

`a2a_runtime/a2a_entrypoint.py` deploys the exact same
`agent.py::build_agent()` — same tools, same seller registry, same
reasoning-step recording — as a second AgentCore Runtime speaking the
[A2A protocol](https://a2a-protocol.org/) instead of this project's own
HTTP contract. This is what makes the Buyer Agent genuinely invokable by
*other* agents (e.g. a media-planning agent calling in to ask "what's the
current spend on campaign X"), not just reachable from this repo's own
chat UI.

`a2a_runtime/` is its own directory, separate from this one, containing
only `a2a_entrypoint.py` plus symlinks back to the shared modules it needs
(`agent.py`, `adcp_tools.py`, `seller_agents.py`, `session_store.py`,
`reasoning_hooks.py`, `auth.py`) and its own real copy of
`requirements.txt` (a symlink doesn't work there — see
`deploy_buyer_agent_a2a.py`'s docstring). `deploy_buyer_agent_a2a.py`
`os.chdir()`s into it before calling `configure()`/`launch()`, so this
runtime always gets its own `Dockerfile` on disk, permanently isolated
from the HTTP runtime's — see the bug writeup below for why that isolation
matters.

AgentCore Runtime deploys exactly one protocol per runtime — HTTP on port
8080 mounted at `/invocations` vs. A2A on port 9000 mounted at `/` — so
this genuinely needs its own runtime, not just a new route on the existing
one. `BuyerAgentA2AExecutor` wraps Strands' `StrandsA2AExecutor` (which
adapts the agent to A2A's JSON-RPC `message/send` contract) and, per R5 of
the feature's spec, reads the invoking agent's declared name and the
prompt text directly off the incoming A2A request
(`params.metadata.invokerAgent` and the message's text parts) rather than
doing a separate lookup, recording the session's start before delegating
to the underlying executor.

Conversations over A2A are keyed on **AgentCore Runtime's own
`runtimeSessionId`**, exactly as the HTTP entrypoint keys on
`context.session_id` — see "Multi-turn sessions" below. `bedrock_agentcore`
reads that header into the A2A `ServerCallContext`, and
`BuyerAgentA2AExecutor` resolves it there
(`a2a_entrypoint.py::_resolve_session_id`) and repoints A2A's own
`context_id` at it (`_align_a2a_context_id`), so one AgentCore session is
one A2A context, one Strands agent, one S3 session and one reasoning
session. A2A's `context_id` cannot serve as that key on its own: a2a-sdk
generates a fresh UUID4 `context_id` for any request whose caller didn't
supply one — i.e. every plain `message/send` — which previously made each
turn of the same runtime session a brand-new agent with a brand-new,
empty conversation.

Test it directly (agent card discovery + a real invocation):

```python
import json, urllib.parse, uuid, requests
from dotenv import load_dotenv
load_dotenv()
from auth import get_test_user_access_token

ARN = "arn:aws:bedrock-agentcore:us-east-1:<aws-account-id>:runtime/adcp_buyer_agent_a2a-TU2G7j8xtB"
token = get_test_user_access_token()
escaped = urllib.parse.quote(ARN, safe="")
headers = {"Authorization": f"Bearer {token}", "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": str(uuid.uuid4()) + "-pad-pad-pad-pad-pad"}

# Agent card (discovery)
card = requests.get(f"https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/{escaped}/invocations/.well-known/agent-card.json", headers=headers)
print(card.json())

# A real invocation, with an invoking agent's name in metadata (per R5)
body = {
    "jsonrpc": "2.0", "id": "req-1", "method": "message/send",
    "params": {
        "message": {"role": "user", "parts": [{"kind": "text", "text": "What are your AdCP capabilities?"}], "messageId": str(uuid.uuid4()), "kind": "message"},
        "metadata": {"invokerAgent": "Media Planner Agent"},
    },
}
resp = requests.post(f"https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/{escaped}/invocations", headers={**headers, "Content-Type": "application/json"}, json=body)
print(resp.json())
```

**A real deployment bug worth knowing about (and why `a2a_runtime/` is its
own directory):** `bedrock-agentcore-starter-toolkit`'s
`Runtime.configure()` silently *reuses* an existing `Dockerfile` on disk
instead of regenerating one for whichever entrypoint you just configured,
if one is already present from a previous `configure()` call — and it
always writes/reads that `Dockerfile` at the process's current working
directory, not near the entrypoint file. Originally both runtimes'
`configure()` calls ran from this same directory and shared one
`Dockerfile` path; that produced an A2A runtime that deployed successfully
(`status: READY`) but was silently still running the HTTP entrypoint's
`CMD`, listening on the wrong port — every request to it hung indefinitely
with zero CloudWatch log entries, since nothing was ever listening on the
port AgentCore Runtime proxied to. The fix that actually prevents this
permanently is giving the A2A runtime its own directory (`a2a_runtime/`,
see above) so the two `configure()` calls can never again share a
`Dockerfile` path, regardless of deploy order.
`deploy_configure.py::delete_stale_dockerfile()` (deletes `Dockerfile`
before every `configure()` call) is kept as cheap defense-in-depth for
this directory too, but the directory split is the real guarantee.

## Deployed instance

This agent is deployed and running on AWS Bedrock AgentCore Runtime, twice —
once per protocol:

| | HTTP (chat UI) | A2A (external agents) |
|---|---|---|
| Agent runtime ARN | `arn:aws:bedrock-agentcore:us-east-1:<aws-account-id>:runtime/adcp_buyer_agent-<runtime-id>` | `arn:aws:bedrock-agentcore:us-east-1:<aws-account-id>:runtime/adcp_buyer_agent_a2a-TU2G7j8xtB` |
| Entrypoint | `app.py` | `a2a_runtime/a2a_entrypoint.py` |
| Env var | `AGENT_RUNTIME_ARN` | `BUYER_AGENT_A2A_RUNTIME_ARN` |
| Region | `us-east-1` | `us-east-1` |
| Status | `READY` | `READY` |
| Execution role | `arn:aws:iam::<aws-account-id>:role/adcp-buyer-agent-agentcore-execution` (shared by both) | (shared) |
| Session storage | S3 bucket `adcp-buyer-agent-sessions-<aws-account-id>-us-east-1`, prefix `adcp-buyer-agent` (shared) | (shared) |
| Reasoning-step storage | DynamoDB table `adcp-buyer-agent-sessions` (shared) | (shared) |
| Inbound auth | Amazon Cognito JWT bearer tokens (see "Authentication" below, shared by both) | (shared) |

The runtime requires a valid Cognito access token on every call — there is
no unauthenticated path, either through the UI or through direct API calls.
`boto3.client("bedrock-agentcore").invoke_agent_runtime()` does not support
bearer tokens (it's SigV4-only), so authenticated calls use the raw HTTPS
invoke endpoint instead:

```python
import json
import urllib.parse
import uuid
import requests
import boto3  # only used here to fetch a Cognito access token via cognito-idp

REGION = "us-east-1"
AGENT_RUNTIME_ARN = "arn:aws:bedrock-agentcore:us-east-1:<aws-account-id>:runtime/adcp_buyer_agent-<runtime-id>"

# 1. Get a Cognito access token (replace with your pool's client ID / a
#    user's credentials — see "Authentication" for where these live).
cognito = boto3.client("cognito-idp", region_name=REGION)
auth = cognito.initiate_auth(
    ClientId="<COGNITO_CLIENT_ID>",
    AuthFlow="USER_PASSWORD_AUTH",
    AuthParameters={"USERNAME": "<username>", "PASSWORD": "<password>"},
)
token = auth["AuthenticationResult"]["AccessToken"]

# 2. Invoke the runtime directly over HTTPS with that token as a bearer token.
escaped_arn = urllib.parse.quote(AGENT_RUNTIME_ARN, safe="")
url = f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{escaped_arn}/invocations?qualifier=DEFAULT"
session_id = f"adcp-buyer-agent-{uuid.uuid4()}"  # must be >= 33 chars, see "Multi-turn sessions"

response = requests.post(
    url,
    headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
    },
    data=json.dumps({"prompt": "Find rewarded video inventory for casual gaming apps in the US"}),
)
print(response.status_code)
print(response.text)  # SSE-formatted stream of {text | tool_call | tool_result | done} events
```

A ready-to-run version of this (including the negative cases below) is
`deploy_invoke_test.py`.

Without a valid token, AWS rejects the call before it ever reaches this
agent's code:

```
No Authorization header  -> 401 {"error":{"message":"Missing Authentication Token"}}
Malformed/garbage token  -> 403 {"message":"OAuth authorization failed: Failed to parse token"}
Expired/wrong-issuer token -> rejected by the platform the same way
```

The chat UI in `static/index.html` (including its login screen) calls this
deployed runtime directly from the browser — see "Architecture" above.
`python3 app.py` is only needed locally to serve the static files and to
expose `/config`; it is not in the request path for chat or login.

## Authentication

Both the deployed runtime and the local dev server require an Amazon
Cognito access token. The same login screen and server-side check run in
both places; the deployed runtime also enforces it independently at the
AWS platform level, before the request reaches this code.

**Where the Cognito resources live** (all created by `deploy_cognito_setup.py`,
not by hand):

| | |
|---|---|
| User pool | `adcp-buyer-agent-users` (`<cognito-user-pool-id>`) |
| App client | `adcp-buyer-agent-client` (`<cognito-client-id>`), no client secret, `USER_PASSWORD_AUTH` flow |
| Test user | username `testuser`, password generated randomly and stored only in `.env` (`COGNITO_TEST_USER_PASSWORD`) — never printed in full, never committed |

Run `deploy_cognito_setup.py` to create these (idempotent — safe to run
again; it reuses existing resources by name and only creates what's
missing):

```bash
source .venv/bin/activate
python3 deploy_cognito_setup.py
```

This writes `COGNITO_USER_POOL_ID`, `COGNITO_CLIENT_ID`, `COGNITO_DISCOVERY_URL`,
`COGNITO_REGION`, `COGNITO_TEST_USERNAME`, and `COGNITO_TEST_USER_PASSWORD`
into `.env`. `deploy_configure.py` / `deploy_launch.py` read these to wire
the runtime's authorizer and to pass the non-secret ones (`COGNITO_REGION`,
`COGNITO_CLIENT_ID`, `COGNITO_USER_POOL_ID`, `COGNITO_DISCOVERY_URL`) into
the deployed container's environment.

**How auth is enforced, in two places:**

1. **AgentCore Runtime itself** — configured with a `customJWTAuthorizer`
   (`discoveryUrl` = the Cognito pool's OIDC discovery URL, `allowedClients`
   = the app client ID). AWS validates the token's signature, issuer, and
   client ID before the request ever reaches this agent's container. See
   `deploy_configure.py::build_authorizer_configuration`.
2. **The app itself** (`auth.py`) — independently re-verifies the same
   token: fetches Cognito's JWKS, checks the RS256 signature, issuer,
   `client_id` claim, and expiry, and rejects anything that fails with a
   `401 Unauthorized` SSE error event. This runs identically locally (where
   there is no platform-level gate) and on the deployed runtime (as
   defense in depth). See `agent.py`'s and `app.py`'s use of
   `auth.verify_bearer_token`.

**One gotcha that cost a redeploy to find:** AgentCore Runtime validates
the `Authorization` header against the authorizer but does **not** forward
it into the running container unless it's explicitly allowlisted via
`request_header_configuration={"requestHeaderAllowlist": ["Authorization"]}`.
Without that, the platform accepts the call but the app sees no header at
all and rejects it itself. See `deploy_configure.py::build_request_header_configuration`.

**Login flow (browser):**

```
User enters username/password in static/index.html's login screen
  │
  ▼
Browser calls Cognito's InitiateAuth API directly (no server in between;
Cognito's CORS response allows this: Access-Control-Allow-Origin: *)
  │
  ▼
Cognito returns an access token (or rejects with NotAuthorizedException
for a wrong password)
  │
  ▼
Browser stores the token in sessionStorage, sends it as
"Authorization: Bearer <token>" on every /invocations call
  │
  ▼
Server (auth.py) independently re-verifies the token before doing anything
```

If the server ever rejects a token the UI thought was valid (expired,
revoked), the 401 error event clears the stored session and forces a
re-login rather than leaving the UI stuck.

### Adding users (and first-login password change)

`testuser` is a service account for the verification scripts — it gets a
*permanent* password because automated tests can't answer an interactive
challenge. Real people get their own user:

```bash
source .venv/bin/activate
python3 create_cognito_user.py alice --email alice@example.com
```

That calls `AdminCreateUser` with a **temporary** password, so the account
lands in Cognito's `FORCE_CHANGE_PASSWORD` status and the script prints the
temporary password once (there's no other way to hand it over; it stops
working after first use and expires on its own). User creation stays a CLI
script on purpose — it needs the Cognito *admin* APIs, and exposing those
to the browser would mean shipping admin credentials to every visitor.

First sign-in then goes through the challenge flow:

```
User enters username + temporary password
  │
  ▼
InitiateAuth returns ChallengeName=NEW_PASSWORD_REQUIRED plus a
short-lived Session — and NO tokens
  │
  ▼
UI swaps the login form for its "choose a new password" step, with a live
checklist mirroring the pool's real policy (8+ chars, upper, lower, digit)
  │
  ▼
RespondToAuthChallenge(NEW_PASSWORD_REQUIRED) with {USERNAME, NEW_PASSWORD,
Session}. Cognito makes the password permanent (status -> CONFIRMED) and
returns the same token set a normal sign-in would
  │
  ▼
From here on, identical to the normal login flow above
```

There is no way to skip this and get in: Cognito issues no tokens at all
until the challenge is answered, so the gate is Cognito's, not the UI's.
The client-side password checklist is UX only — Cognito re-validates and
its rejection (e.g. `InvalidPasswordException`) is surfaced verbatim. If
the challenge `Session` expires before it's answered, the UI says so and
sends the user back to sign in with their temporary password again.
`create_cognito_user.py --reset` issues a fresh temporary password for an
existing user, putting them back through this same flow.

#### Adding users from the UI (`admin` group)

Members of the Cognito `admin` group get a **Users** button in the header,
opening a panel that lists the pool's users and creates new ones — same
outcome as the CLI, without leaving the browser.

The browser still holds no AWS credentials. The panel calls the same
authenticated `/invocations` entrypoint everything else uses, with
`action: "create_user"` / `"list_users"`, and `app.py` performs the Cognito
admin API call using the runtime's execution role (`user_admin.py`).

**Where the access control actually is:** `user_admin.require_admin()`, on
the server, against the `cognito:groups` claim of the token `auth.py` has
already verified against Cognito's JWKS. Group membership is asserted by
Cognito, not by the caller. The UI also reads that claim to decide whether
to show the button, but that is cosmetic — a non-admin who calls the action
directly gets `403 Forbidden`, which is covered by tests.

Three things are deliberately **not** in the UI, so the runtime's IAM policy
stays create-and-read only and a compromised runtime can neither escalate
privilege nor take an account over:

| Action | Where it lives | Why not in the UI |
|---|---|---|
| Granting admin | `create_cognito_user.py --admin` | `AdminAddUserToGroup` would let an admin mint admins from a browser session |
| Resetting a password | `create_cognito_user.py --reset` | `AdminSetUserPassword` can set *permanent* passwords, i.e. account takeover |
| Deleting/disabling a user | AWS console or CLI | destructive, and not needed for "add more users" |

Setup, once:

```bash
source .venv/bin/activate
python3 deploy_cognito_setup.py                    # creates the (empty) 'admin' group
python3 deploy_user_admin_policy.py                # dry run: prints the exact IAM policy
python3 deploy_user_admin_policy.py --apply        # grants the execution role Cognito create+read
python3 create_cognito_user.py testuser --admin    # grant yourself admin
```

`deploy_cognito_setup.py` creates the group **empty** on purpose — nobody,
including the `testuser` service account, is granted admin implicitly. The
IAM policy is a single inline statement scoped to this one user pool ARN
(`AdminCreateUser`, `AdminGetUser`, `ListUsers`, `ListUsersInGroup`), never a
wildcard resource, and the script does nothing without `--apply`.

Cognito groups travel in the access token, so a user granted admin must
sign out and back in before the button appears. Without the IAM policy
applied, the panel reports the real `AccessDenied` and names the script to
run — it does not claim a user was created.

Verified end-to-end against the live pool: a throwaway user created this
way returns `NEW_PASSWORD_REQUIRED` with no tokens, the challenge response
returns a real token set that passes `auth.verify_bearer_token` with the
correct `username`/`client_id` claims, the account moves to `CONFIRMED`,
the temporary password stops working (`NotAuthorizedException`), and the new
password signs in directly with no challenge.

### Multi-turn sessions

`runtimeSessionId` is AgentCore Runtime's own session identifier (minimum 33
characters, enforced by the `InvokeAgentRuntime` API). This project reuses
that exact ID as the key for a Strands `S3SessionManager`
(`agent.py::build_agent`), so as long as you pass the **same**
`runtimeSessionId` across multiple `invoke_agent_runtime` calls, the agent
sees the full prior conversation, persisted in S3 rather than in-memory
state that would be lost between invocations. A different session ID
starts a fresh conversation.

This applies to **both** entrypoints and both are keyed on the same
platform-supplied ID: `app.py` reads it as `context.session_id`, and
`a2a_runtime/a2a_entrypoint.py` reads it off the A2A `ServerCallContext`
(see "Buyer Agent as an A2A server" above). An A2A caller therefore gets
multi-turn continuity the same way an HTTP caller does — by sending the
same `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` header on each turn.

## Tools available to the agent

| Tool | AdCP tool called | Purpose |
|---|---|---|
| `adcp_get_products` | `get_products` | Discover inventory from a natural-language brief |
| `adcp_get_capabilities` | `get_adcp_capabilities` | Check what the connected seller supports |
| `adcp_list_creative_formats` | `list_creative_formats` | List supported creative formats |


All five are bound to whichever seller the request selected — see "Seller
agent registry" above. There is intentionally no `create_media_buy` tool
wired up. This agent reads and evaluates inventory; it does not spend money
or create real deals.

## AdCP v3 conformance

Checked against AdCP's buyer-side wire invariants
([`skills/call-adcp-agent/SKILL.md`](https://github.com/adcontextprotocol/adcp/blob/main/skills/call-adcp-agent/SKILL.md)
and the [v3 readiness checklist](https://github.com/adcontextprotocol/adcp/blob/main/dist/docs/3.0.12/reference/migration/v3-readiness.mdx)),
since this agent only calls the three core, read-only AdCP tasks
(`get_products`, `get_adcp_capabilities`, `list_creative_formats`):

- **`adcp_major_version` on every core-task request** (`adcp_tools.py::_with_version()`)
  — buyers SHOULD emit this on 3.x requests so sellers validate/serve
  against the right major; it's a compliance-grader requirement at 3.2 and
  a MUST at 4.0. Not sent on a seller's vendor-namespaced extension tools,
  which aren't core AdCP tasks and may reject an unrecognized field.
- **`brand: {domain}` on `get_products`** (`adcp_get_products`'s
  `brand_domain` parameter, falling back to `BUYER_BRAND_DOMAIN`) —
  every `get_products` request is required to include the advertiser's
  brand so sellers can run brand-safety/policy checks. The system prompt
  tells the agent to ask the user for this rather than omit it silently
  when neither is available.
- **`buying_mode` values corrected to the real v3 enum** (`browse | brief
  | refine`) — an earlier version of this tool's docstring documented a
  made-up `"wholesale"` value that doesn't exist on the wire.
- **A2A `DataPart` uses `input`** (`adcp_tools.py::_call_seller_tool_a2a`),
  the current field name for explicit skill invocation, sending the
  legacy `parameters` key alongside it for sellers that still only read
  that name during AdCP's compatibility period.

Verified live against the real reference seller (both fixes: `brand` and
`adcp_major_version` accepted without error, real product data returned;
confirmed separately that the LLM correctly extracts a brand domain from
natural language and passes it through).

Gaps intentionally not closed, since they don't apply to a read-only
discovery agent with no mutating tasks:

- **`idempotency_key`** is only required on mutating AdCP tasks
  (`create_media_buy`, `sync_creatives`, etc.). This agent has none. It
  would become a real requirement the moment such a tool is added.
- **Structured error recovery** (`adcp_error.issues[].pointer` /
  `keyword` / `variants`) — the spec's recommended pattern is
  deterministic, targeted retries built from these fields. Errors here
  are passed through to the LLM as-is instead, which handles ordinary
  seller errors fine in practice but doesn't implement the SDK's
  self-correcting retry pattern.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in SELLER_AGENTS_JSON and SESSION_STORAGE_BUCKET
```

Requires AWS credentials with Bedrock `InvokeModel`/`InvokeModelWithResponseStream`
access in your environment (`aws configure` / SSO / whatever you normally use).
The default model is Claude Sonnet 5 via a cross-region inference profile
(`us.anthropic.claude-sonnet-5`) in `us-east-1`. Override
`BEDROCK_MODEL_ID` / `AWS_REGION` in `.env` if you want a different model or
region.

`SESSION_STORAGE_BUCKET` must be an S3 bucket you (or the execution role)
can read/write/list. If unset, the agent runs statelessly (no memory of
prior turns) instead of raising an error — see `agent.py::build_agent`.

## Run locally

```bash
source .venv/bin/activate
python3 app.py
```

Then open **http://localhost:8080/** in a browser. The chat panel is on the
left; the right panel shows live tool calls and their results as they
happen. Login and every chat message go straight from the browser to AWS
(Cognito, then the deployed AgentCore Runtime) — this local server only
serves the static files and the `/config` values the browser needs to
build those requests (see "Architecture"). `AGENT_RUNTIME_ARN` must be set
in `.env` for this to work; if it's missing, the UI's connection badge
reports "No AGENT_RUNTIME_ARN configured" rather than a false "connected"
state.

Test the deployed runtime directly without the UI:

```bash
python3 deploy_invoke_test.py
```

Test this local process's own `/invocations` contract directly (this is
what runs inside the deployed container; the browser UI does not call
this path, see "Architecture"). It enforces the same Cognito auth as the
deployed runtime, so a request with no token gets a `401` error event
rather than a response:

```bash
# No token - rejected, since the auth gate is enforced locally too:
curl -N -X POST http://localhost:8080/invocations \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Find rewarded video inventory for casual gaming apps in the US"}'

# With a Cognito token (fetch one with python3 -c "from auth import
# get_test_user_access_token; print(get_test_user_access_token())" or via
# deploy_invoke_test.py's helper):
curl -N -X POST http://localhost:8080/invocations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -H "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: local-test-session-0000000000000" \
  -d '{"prompt": "Find rewarded video inventory for casual gaming apps in the US"}'
```

Health check:

```bash
curl http://localhost:8080/ping
```

## How it was deployed

**Note:** `deploy_all.py`, at the repo root, now runs this project's three
scripts below (`deploy_cognito_setup.py` → `deploy_launch.py` →
`deploy_buyer_agent_a2a.py`) automatically, in that order, as part of
orchestrating the full demo's deploy (Cognito + both sellers + this
project). See the repo root `README.md`'s "Deploying everything" section.
Running the three scripts individually, as documented below, is still
fully supported and is the right approach when iterating on just this
project without redeploying everything else.

Deployed using `bedrock-agentcore-starter-toolkit`'s `Runtime` class, via
three scripts kept in this directory (run in this order for a from-scratch
deploy):

1. **`deploy_cognito_setup.py`** — creates/reuses the Cognito user pool, app
   client, and test user (see "Authentication" above), writing the results
   to `.env`.
2. **`deploy_configure.py`** — calls `Runtime.configure()`: sets the
   entrypoint (`app.py`), execution role, region, `deployment_type="container"`,
   the Cognito `authorizer_configuration`, and the `Authorization` header
   allowlist. This generates `Dockerfile`, `.dockerignore`, and
   `.bedrock_agentcore.yaml`. All three are gitignored since they're
   reproducible from this script and `.bedrock_agentcore.yaml` carries
   account-specific IDs after a launch.
3. **`deploy_launch.py`** — calls `Runtime.configure()` then `Runtime.launch()`
   in the same process (the toolkit's `_config_path` is in-memory state, not
   reloaded from the YAML on a fresh `Runtime()`), passing the real env vars
   (`SESSION_STORAGE_BUCKET`, the non-secret
   `COGNITO_*` values, etc.) explicitly at launch time since `.env` is
   excluded from the build context.

No local Docker/Finch/Podman was available, so this used the toolkit's
default cloud path: it zips the source, uploads to S3, and builds the ARM64
container via AWS CodeBuild (no local container engine required), then
creates/updates the AgentCore Runtime resource pointing at the resulting
ECR image.

**What was created in this AWS account (us-east-1, account `<aws-account-id>`)
to support the deployment:**

- Cognito user pool `adcp-buyer-agent-users`, app client
  `adcp-buyer-agent-client`, and test user `testuser` (see "Authentication").
- IAM role `adcp-buyer-agent-agentcore-execution` — the agent's execution
  role. Trust policy scoped to the `bedrock-agentcore.amazonaws.com` service
  principal for this account/region only. Permissions: `bedrock:InvokeModel`
  + `InvokeModelWithResponseStream`, ECR pull, CloudWatch Logs, X-Ray, and
  S3 access scoped only to the session bucket below. Plus, if
  `deploy_user_admin_policy.py --apply` has been run, an inline policy
  granting Cognito `AdminCreateUser`/`AdminGetUser`/`ListUsers`/
  `ListUsersInGroup` scoped to the one user pool — create and read only, no
  group, password, or delete permissions (see "Adding users from the UI").
- Cognito group `admin` in that pool — membership gates the UI's Users
  panel. Created empty by `deploy_cognito_setup.py`; granted explicitly with
  `create_cognito_user.py <user> --admin`.
- IAM role `AmazonBedrockAgentCoreSDKCodeBuild-us-east-1-edc5e6434b` —
  auto-created by the toolkit for the CodeBuild project.
- ECR repository `bedrock-agentcore-adcp_buyer_agent` — auto-created,
  holds the built container image.
- S3 bucket `adcp-buyer-agent-sessions-<aws-account-id>-us-east-1` — private,
  `AES256`-encrypted, holds session/conversation history (see "Multi-turn
  sessions" above).
- AgentCore Runtime `adcp_buyer_agent` (ARN above), configured with a
  Cognito JWT authorizer.
- DynamoDB table `adcp-buyer-agent-sessions` (`deploy_sessions_table.py`) —
  on-demand billing, a sparse `gsi1` GSI for the Sessions dropdown's
  "recent sessions" query, TTL enabled on the `ttl` attribute (24h). The
  execution role above was granted an additional inline statement
  (`dynamodb:PutItem`/`UpdateItem`/`GetItem`/`Query`, scoped to this table
  and its GSI) rather than a new role, since both Buyer Agent runtimes
  already shared that role.
- A second AgentCore Runtime, `adcp_buyer_agent_a2a` (see "Buyer Agent as
  an A2A server" above), reusing the same execution role and Cognito
  authorizer — `deploy_buyer_agent_a2a.py`.

To redeploy the HTTP runtime after code changes:

```bash
source .venv/bin/activate
python3 deploy_launch.py
```

To redeploy the A2A runtime after code changes:

```bash
source .venv/bin/activate
python3 deploy_buyer_agent_a2a.py
```

To exercise the live HTTP deployment end to end (auth rejection cases,
multi-turn memory, and an AdCP tool call, all against the deployed ARN):

```bash
python3 deploy_invoke_test.py
```

### UI and the deployed runtime

Only `/invocations` and `/ping` are reachable through the AgentCore Runtime
invoke API — there is no raw HTTP endpoint serving `/`, `/config`, or
`/static/*` for the deployed instance the way `python3 app.py` serves them
locally. That's fine here because the browser doesn't need those routes
from the deployed side at all: it gets the deployed runtime's ARN/region
from the *local* server's `/config` (or, for a hosted deployment, from
wherever the static files themselves are served — see below), then talks
to Cognito and the AgentCore Runtime invoke endpoint directly. Chat and
login never go through `app.py`'s own `/invocations`; that route only
matters as the entrypoint contract the deployed container itself
implements.

`AGENT_RUNTIME_ARN` in `.env` is read by `app.py`'s `/config` for local
dev convenience. It is **not** passed into the deployed container's
environment by `deploy_launch.py` — the deployed runtime doesn't need to
know its own ARN to serve `/invocations` correctly, so this is purely a
client-side routing value, not server-side configuration.

### Hosting the UI without a developer's laptop

`python3 deploy_ui.py` puts `static/index.html` behind a real S3 +
CloudFront deployment, so anyone with a Cognito login can use the UI
without running `python3 app.py` locally:

```bash
python3 deploy_ui.py
```

It creates (or reuses, on re-run) a private S3 bucket, a CloudFront Origin
Access Control (OAC), and a CloudFront distribution using OAC to reach the
bucket — the bucket has Block Public Access fully on and its policy grants
`cloudfront.amazonaws.com` read access scoped to that specific
distribution's ARN, not a public/broad grant. On every run it also
generates `config.json` from the same `.env` values `app.py`'s local
`/config` route reads (model id, Cognito pool/client, the deployed
runtime's ARN/region, the seller-agent registry — no auth tokens) and
uploads it next to `index.html`, then invalidates the CloudFront cache so
the change is visible immediately instead of waiting out a TTL.

No backend proxy is involved: the browser still calls Cognito and the
AgentCore Runtime invoke endpoint directly from the client, exactly as it
does when served locally — see "Architecture" above. `static/index.html`'s
`fetchConfig()` tries `/config` first (the local dev server route) and
falls back to `./config.json` on a 404, so the same file works unmodified
against either hosting path.

Re-run `deploy_ui.py` any time `static/index.html` changes, or the seller
registry / deployed runtime ARN change in `.env`, to republish.

## Files

```
agent.py                 - builds the Strands Agent (Bedrock model + AdCP tools + S3 session manager
                            + ReasoningRecorder hook for session recording)
adcp_tools.py             - build_adcp_tools(): the AdCP tools, bound per-conversation
                            to the resolved seller agent, dispatched over MCP or A2A per its
                            "transport" field; call_seller_tool()/DIRECT_CALL_TOOL_NAMES are the
                            same underlying dispatch, exposed for Direct-client mode (app.py)
seller_agents.py          - seller registry: loads/validates SELLER_AGENTS_JSON, resolves a seller
                            id into {url, headers}, exposes the public (no-secrets) list for /config
session_store.py          - DynamoDB-backed session/reasoning-step record (see "Session recording"
                            above): start_turn/record_step/complete_turn/error_turn plus the
                            Sessions dropdown's list_recent_sessions/get_session_meta/get_session_steps
reasoning_hooks.py        - ReasoningRecorder: a Strands HookProvider that records tool calls/
                            results/interleaved reasoning/final response to session_store.py
app.py                    - BedrockAgentCoreApp HTTP server: streaming /invocations (mode=agent/
                            direct, action=list_sessions/get_session_steps), auth-gated, local UI routes
a2a_runtime/              - own directory for the A2A deployment (see "Buyer Agent as an A2A
                            server" above): a2a_entrypoint.py (BuyerAgentA2AExecutor + serve_a2a)
                            plus symlinks back to the shared agent.py/adcp_tools.py/etc. modules
                            and a real copy of requirements.txt
auth.py                   - Cognito JWT verification (JWKS fetch, signature/issuer/client_id/expiry checks)
static/index.html         - the chat UI: Cognito login (incl. first-login password change),
                            Buyer Agent/Direct client mode toggle, seller-agent dropdown,
                            sessions dropdown + read-only session replay in the chat column
deploy_cognito_setup.py   - idempotent: creates/reuses the Cognito user pool, app client, test user
agents_registry.py        - AGENTS_JSON registry of chat-selectable A2A agents (kind/origin, plus the
                            derived records_sessions and requires_proxy the UI depends on)
session_records.py        - the session-record WRITE contract, shared by every agent in the project
seller_session_recorder.py - SkillMiddleware that records a seller's own tool calls as session steps
                            (single source; vendored into each seller by deploy_all.py)
deploy_agents_registry.py - generates AGENTS_JSON from the deployed runtime ARNs
create_cognito_user.py    - adds a user with a temporary password (AdminCreateUser), so they set
                            their own at first sign-in; --reset re-issues one for an existing user,
                            --admin puts them in the 'admin' group (CLI-only, see "Adding users")
user_admin.py             - server-side Cognito user admin for the UI's Users panel: require_admin()
                            on the verified token's cognito:groups claim, plus create/list users
deploy_user_admin_policy.py - grants the execution role create+read Cognito permissions scoped to
                            the one user pool; dry-run by default, needs --apply
deploy_configure.py       - Runtime.configure() for the HTTP runtime, incl. Cognito authorizer;
                            also hosts delete_stale_dockerfile() (see "Buyer Agent as an A2A server")
deploy_launch.py          - Runtime.configure() + Runtime.launch() for the HTTP runtime (app.py)
deploy_buyer_agent_a2a.py - Runtime.configure() + Runtime.launch() for the A2A runtime (a2a_entrypoint.py)
deploy_sessions_table.py  - idempotent: creates the DynamoDB sessions table + gsi1 GSI + TTL
deploy_invoke_test.py     - invokes the deployed HTTP runtime over HTTPS with a Cognito token; checks
                            auth rejection (no token / bad token) and multi-turn memory
deploy_ui.py              - idempotent: publishes static/index.html + a generated config.json to
                            S3 + CloudFront (OAC-locked bucket), so the UI is reachable without a
                            local server
requirements.txt
.env.example
Dockerfile, .dockerignore - generated by deploy_configure.py (regenerated per-entrypoint on every
                            configure() call - see delete_stale_dockerfile())
```

See `../../seller/reference-seller/` for the sandbox seller agent's own files (a
separate AgentCore project, not part of this one).

## Known limitations

- No request signing: some third-party seller endpoints support optional
  RFC 9421 request signing; this agent uses plain bearer auth only.
- Each session's history in S3 grows unbounded; no TTL/cleanup policy was
  set on the session bucket. (The separate DynamoDB reasoning-step record
  does have a TTL — see "Session recording" above — but that's a distinct
  store from the S3-backed conversation history.)
- Neither the A2A `context_id` nor `runtimeSessionId` is an authentication
  boundary (per Strands' `StrandsA2AExecutor` docs, and AgentCore's own
  session model) — a caller who knows another caller's session ID could in
  principle attach to that conversation. The Cognito JWT authorizer on the
  A2A runtime gates *who can call it at all*, but doesn't scope individual
  sessions to individual callers.
- Whichever directory hosts more than one AgentCore Runtime entrypoint
  (as this one does: `app.py` and `a2a_entrypoint.py`) needs
  `delete_stale_dockerfile()` called before every `configure()`, or a
  redeploy can silently ship the wrong entrypoint in a "successfully
  deployed" runtime — see "Buyer Agent as an A2A server" above.
- The `admin` group is a single flat role — there is no finer-grained
  permission model, and any admin can create users for the whole pool.
  Admin actions are not separately audited beyond CloudTrail's record of the
  execution role's Cognito calls; the reasoning-session store does not log
  them.
- The login screen handles `USER_PASSWORD_AUTH` plus the
  `NEW_PASSWORD_REQUIRED` challenge (see "Adding users" above). It does
  **not** handle MFA, SRP, device confirmation, or self-service
  forgot-password — those challenges are reported by name rather than
  silently failing, but there's no UI for them. A password reset for an
  existing user goes through `create_cognito_user.py --reset`, which is an
  admin action, not self-service.
- The test user's password lives only in `.env` (gitignored) once
  `deploy_cognito_setup.py` has run. There is no secrets-manager-backed
  distribution of it. Real people should get their own user via
  `create_cognito_user.py` instead of sharing that account; the temporary
  password it prints is handed over out-of-band (terminal output) with no
  managed distribution channel.
