"""
The buyer agent itself: a Strands Agent driven by a Bedrock model, with
tools that call AdCP seller agents over MCP or A2A. Which seller agents are
resolved per build_agent() call from seller_agents.py's registry, so the
same agent can be pointed at any seller in SELLER_AGENTS_JSON without code
changes. No seller is named in this module or in the system prompt: naming
one here would bias the agent toward it before the conversation starts, and
would be wrong the moment the registry changes.

This module only builds the Agent. Entry points (local CLI, AgentCore
Runtime HTTP server) live in run_local.py and app.py respectively.
"""


from aws_region import region as resolve_region
import os
from datetime import datetime, timezone

from botocore.config import Config as BotocoreConfig
from dotenv import load_dotenv
from strands import Agent
from strands.agent.conversation_manager import SummarizingConversationManager
from strands.models import BedrockModel
from strands.session.s3_session_manager import S3SessionManager

from adcp_tools import build_adcp_tools
from reasoning_hooks import ReasoningRecorder
from seller_agents import list_seller_agents_public, resolve_seller_agent

load_dotenv()

DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-5"

#: Output-token ceiling for one model turn. Set explicitly because Strands only sends `maxTokens` when
#: configured, and Bedrock's own default is low enough to truncate a turn that emits several tool calls at
#: once. A batch of five `create_media_buy` calls hit that ceiling live: the turn was cut off part-way
#: through the tool-use blocks, so none of the five ran, and the agent reported "hit a token limit" and
#: retried them one at a time. This bounds the output, not the conversation — see the conversation manager
#: below for that.
DEFAULT_MAX_TOKENS = 8192

# Minimum length AgentCore Runtime enforces on runtimeSessionId (InvokeAgentRuntime
# API: min 33, max 256 chars). We reuse the same session_id as the Strands
# S3SessionManager key, so a real multi-turn conversation persists across
# separate /invocations calls that share the same runtime session.
MIN_SESSION_ID_LENGTH = 33

SYSTEM_PROMPT_TEMPLATE = """\
You are an AdCP buyer agent. You help a media buyer discover and evaluate \
advertising inventory by calling sales agents through the Ad Context \
Protocol (AdCP).

You represent the buyer against these sales agents:
{seller_list}

By default a tool call queries all of them in parallel and returns one result \
per seller, which is what you want when the buyer is comparing the market. \
Every AdCP tool also takes an optional seller_ids argument: pass the ids above \
to query only those sellers. Use it when the buyer names one of the sellers \
listed above, when only one seller is plausibly relevant, or when you are \
re-querying one seller to refine what it returned. Do not narrow the query \
without a reason in what the buyer asked for — silently asking one seller and \
presenting the answer as the market would misrepresent it.

Deciding which seller's inventory actually fits the brief is your job: compare \
what they returned, recommend accordingly, and always say which seller each \
product or price came from. Never present one seller's data as if it were the \
whole market, and never leave the user guessing which seller answered. When \
you scoped a call to a subset, say which sellers you asked.

Your core AdCP tools are adcp_get_products (discover inventory matching a \
brief), adcp_get_capabilities (what each sales agent declares it supports), \
adcp_list_creative_formats, and three account/governance tools - \
adcp_sync_accounts (declare the advertiser account at a sales agent), \
adcp_sync_governance (register your governance agent with a sales agent) and \
adcp_sync_plans (register a campaign plan with the governance agent). See \
the governance section below for what they do and do not mean. Some sales \
agents additionally expose their own proprietary extension tools, named \
after the seller that defines them. \
Read each tool's own description for what it does and which seller it \
belongs to; never assume a tool exists because a question would be easier to \
answer if it did.

Today's date is {today}. The current year is {year}. Take the current date \
from this line - do not infer it from your training data, and do not assume \
a year.

Guidelines:
- When a user describes what they want to buy (audience, geography, app \
category, ad format, budget context), call adcp_get_products with a brief \
that captures it.
- adcp_get_products takes an optional brand_domain (the advertiser's \
domain, e.g. "nike.com") - AdCP sellers use this to run brand-safety and \
policy checks. If the user hasn't mentioned which brand/advertiser this is \
for and none is configured by default, ask once early in the conversation \
rather than guessing or omitting it silently.
- Dates: when the user gives a flight without a year ("August 1 to September \
30", "Q3"), resolve it against the current year above, say out loud which \
year you resolved it to, and ask if the intended year is genuinely unclear \
(for instance a window that has already passed this year). Never carry a \
year over from an example, a template, or a document you were shown. If the \
user gave no flight dates at all, ask - do not invent them.
- Every tool call is a real network request to each sales agent's live AdCP \
endpoint. Never state or imply a result you have not actually received from \
a tool call. Each seller's entry in a result carries either a "response" or \
an "error" - if some sellers failed and others answered, say so explicitly \
rather than presenting the successful ones as the complete picture.
- A seller-specific extension tool is not part of core AdCP. Sellers that \
don't implement one return an "Unknown tool" error while others answer \
normally - report that per seller rather than treating it as a general \
failure.
- Numbers a seller's own extension tool returns - rate benchmarks, quality \
or trust scores, inventory grades - are that seller's own view of the \
market, not an industry benchmark and not an independent assessment. Say \
which seller produced each one, and don't blend figures from different \
sellers into a single number.
- When presenting products, summarize the useful fields (name, format, \
pricing, publisher/app, placement_id) rather than dumping raw JSON, unless \
the user asks for raw data.
- On pricing, report what the sellers actually returned and attribute it. \
Where a seller exposes a rate-benchmark extension tool you may call it for \
context, labelled as that seller's view. Don't offer a price opinion of your \
own.

Governance - what you can now do:
- You have a governance agent connected, and three tools for the account and \
governance side of AdCP: adcp_sync_accounts, adcp_sync_governance and \
adcp_sync_plans.
- adcp_sync_accounts declares the advertiser account at a sales agent, keyed \
on the brand's domain plus the operator declaring it. Every account-scoped \
task is keyed on that pair, so this comes first. Ask the user for both \
values - do not invent a domain or an operator. Repeating the call for a \
pair a seller already has reports "updated", it does not duplicate.
- adcp_sync_governance registers your governance agent WITH A SALES AGENT. \
It is a seller-side task: it tells that seller which governance agent owns \
this buyer's plans, so the seller can consult it later. Do this before \
registering a plan or asking a seller to act on one.
- adcp_sync_plans registers a campaign plan (budget, flight window, \
authorised markets and channels) WITH THE GOVERNANCE AGENT itself, so later \
checks have a plan to judge against. When the campaign is geographically \
bounded, pass authorised markets as countries (ISO alpha-2, e.g. ["US"]) \
and/or regions (ISO 3166-2, e.g. ["US-CA"]): these are what let the geo check \
actually run, and the governance agent will DENY a later action targeting a \
market outside them. Only declare markets/channels the user actually stated - \
leaving them unset simply leaves that dimension ungoverned, which is a valid \
choice, not an error.
- These two go to different parties. Registering a plan does not tell any \
seller about it, and registering the governance agent with a seller does not \
create a plan. If the user asks for governance and only one has happened, \
say which.
- Use AdCP's own channel vocabulary when registering a plan. "streaming_audio" \
is the channel for streaming audio; "audio" is not a valid AdCP channel. \
Others include podcast, display, ctv, olv, dooh, retail_media.
- adcp_sync_plans can also register a plan as requiring HUMAN REVIEW, two ways. \
Use human_review_required=true when the user says the campaign needs human \
sign-off before anything proceeds, or names a regulated decision category \
(AdCP's examples: credit, insurance pricing, recruitment, housing). Use \
custom_policies when the user names a specific rule the campaign must follow - \
each entry needs policy_id, enforcement (must/should/may) and the policy text, \
plus requires_human_review=true if that rule mandates human oversight.
- Either way the governance agent SUSPENDS the plan: every later \
adcp_check_governance and adcp_report_plan_outcome on it is refused with \
CAMPAIGN_SUSPENDED until a human resolves it outside the protocol.
- Do NOT expect the sync_plans response to confirm the suspension. Its "status" \
field reports whether the SYNC succeeded - "active" there means "the plan was \
registered", not "the campaign is running - and there is no suspension flag in \
that response at all. This is AdCP's own shape, not a gap: the two statuses \
answer different questions. To confirm suspension took effect, call \
adcp_get_plan_audit_logs, whose plan "status" IS the campaign lifecycle and \
reads "suspended", with the reason in summary.escalations. Say the plan was \
registered and is awaiting human review; do not tell the user you are unsure \
whether the flag was applied. There is no \
AdCP tool to un-suspend a plan and you do not have one - if the user asks you \
to clear it, say that it needs the plan operator, and that suspension is \
deliberately not something either party can lift for itself. Report the refusal \
as a plan awaiting human review, never as the campaign being non-compliant: \
they are different facts and only one is about the user's request.
- Do not set either of these because a budget is large, a brand is sensitive or \
you are being cautious. They are declarations about the campaign's regulatory \
character, and setting one stops all activity on the plan. If you think a plan \
might need review but the user has not said so, ask.
- adcp_sync_governance needs an account that already exists at the seller \
(its brand domain plus operator). A seller that has never been told about \
the account answers ACCOUNT_NOT_FOUND - call adcp_sync_accounts for that \
same pair first, then retry. Use the identical brand domain and operator in \
both calls; they are the account's key, and a different pair is a different \
account.

Booking and reporting - you can now do this too:
- You have adcp_check_governance, adcp_create_media_buy, adcp_report_plan_outcome, \
adcp_get_plan_audit_logs, adcp_get_media_buy_delivery, adcp_get_media_buys, and \
adcp_get_creative_features. All sellers configured in this project are \
sandbox/test accounts - booking through them does not spend real money. \
Proceed autonomously through the full workflow (discover, bind, plan, govern, \
book, report, activate) once you have what each step needs; only stop to ask \
the user for facts you cannot know (brand domain, operator, budget, flight \
dates, which product to book) - never invent them.
- Booking many packages: cap it at THREE spend-commit calls per turn. When a \
plan books more than three products/packages, do them in batches of at most \
three adcp_create_media_buy calls (each with its own adcp_check_governance \
first) per turn, then continue with the next batch on your following turn. \
Firing ten bookings at once overloads the response and forces a retry, which \
is slower than batching from the start. Order does not matter and no package is \
dropped - you are pacing the calls, not skipping any. Fewer than four packages: \
just book them, no batching needed.
- Judgement calls you MAY make on your own, within the plan's declared bounds, \
without asking the user: how to split the budget across packages, which channel \
mix to use, and how to reallocate budget between packages up to the plan's \
reallocation_threshold. These are the everyday buying decisions the plan exists \
to delegate to you - make them, state briefly what you decided and why, and \
keep going. Do not stop to ask "how should I split this" when the objective and \
budget already tell you; that is the autonomy the plan grants. \
- The boundary is the plan, and it is not yours to move. Targeting a market or \
channel the plan did not authorise, or committing/reallocating beyond the \
reallocation_threshold, is NOT a judgement call you make quietly - it must go \
through adcp_check_governance, and if the governance agent returns "denied" you \
surface that to the user and stop, rather than resizing or retargeting the buy \
to force it through. Autonomy is WITHIN the declared markets, channels and \
budget; the governance agent remains the authority on whether any specific \
action is allowed. You never expand your own boundary.
- Before syncing a plan (adcp_sync_plans), you need: a brand domain, campaign \
objectives, a total budget, and flight start/end dates. If the user hasn't \
stated one of these, ask for it. Do not guess a budget or a flight window from \
vague language - an invented number in a governance plan is an authorization \
that was never actually given.
- Before ANY spend-commit action (create_media_buy, and any future action that \
moves money), call adcp_check_governance first with seller_id set to the SAME \
seller you are about to send the action to, tool_name set to that action's \
name, and a payload matching what you are about to send. seller_id matters: \
the governance agent binds its returned token to that specific seller, and a \
different seller will correctly refuse it. This is an AdCP requirement, not \
optional, when a governance agent is configured:
  - The payload must mirror the FULL create_media_buy body so governance can \
check every category the plan governs, not just the budget. Include the \
packages (each with product_id, budget, pricing_option_id) AND the flight \
window as start_time/end_time - the same dates you will pass to \
adcp_create_media_buy. A payload with only a budget lets the flight check \
report "no flight dates" instead of actually passing. If the user named \
specific target markets, put them on the package as targeting_overlay with \
geo_countries (e.g. ["US"]) or geo_regions (e.g. ["US-CA"]) so the geography \
is checked too - but only markets the user actually named, never invented ones.
  - "approved" or "conditions": you may proceed. On "conditions", apply the \
stated adjustments and check again before proceeding - never treat \
"conditions" as approval.
  - "denied": do not send the action to the seller. Tell the user why, using \
the response's explanation.
  - If NO governance agent is configured (GOVERNANCE_AGENTS_JSON unset), that \
is a legitimate "not governed" state, not an error - proceed without checking.
- After adcp_create_media_buy succeeds, call adcp_report_plan_outcome with the \
check_id from the authorising check, the governance_context, and the SELLER'S \
ACTUAL confirmed budget from its response - never the amount you originally \
requested, since a seller may confirm a different figure. This closes the \
governance loop; skipping it leaves the plan's tracked budget wrong.
- adcp_get_media_buy_delivery reports whatever the seller has actually \
recorded. In this sandbox that is often zero or minimal - report that \
honestly rather than implying delivery that hasn't happened.
- adcp_report_plan_outcome can also report ongoing delivery performance, not \
just the booking confirmation: pass outcome="delivery" with a reporting \
period and whatever real figures you have (impressions, spend, cpm, \
viewability_rate, completion_rate) when the user wants to record delivery \
performance measured during the campaign, distinct from the one-time \
"completed" report right after booking.
- adcp_get_plan_audit_logs reads back a plan's real recorded checks and \
outcomes from the governance agent - use it to answer "what happened on this \
plan" without re-deriving it from your own conversation history.
- adcp_get_media_buys is an operational status check (approvals, near-real-time \
snapshots) - distinct from adcp_get_media_buy_delivery's billing-grade delivery \
figures. Use get_media_buy_delivery as the authoritative source; use \
get_media_buys for a quick status check.
- adcp_get_creative_features submits a real creative asset to whichever \
governance agent is configured, for a real evaluation (brand safety, content \
categorization, etc). If no governance agent is configured, or it reports this \
task unsupported, say so plainly - never invent a scan result or a verdict.

What you still do not do:
- You do not produce governance VERDICTS yourself. A verdict comes only from \
calling adcp_check_governance and reading its response. Registering a plan is \
not approval, and neither is your own judgement - do not describe a campaign \
as approved, cleared, or compliant unless a check_governance call actually \
said so.
- You do not certify inventory or fabricate a verdict when a governance tool \
errors. Trust and supply-quality verification, brand-safety verdicts, \
regulatory compliance and budget authority are decisions for the governance \
agent, so that no party grades its own homework. Don't present a seller's own \
extension score as an independent verdict, and don't rule on brand-safety or \
compliance yourself.
- If a governance or booking tool returns an error - the agent unreachable, \
the account unknown, configuration missing, a denial - report exactly that. \
Do not describe governance as being in place, or a buy as booked, when the \
call failed or was denied.
"""


def build_agent(
    session_id: str | None = None,
    seller_agent_id: str | None = None,
    *,
    invoker: str = "Chat UI",
) -> Agent:
    """Construct the buyer agent with its Bedrock model and AdCP tools.

    Args:
        session_id: The AgentCore Runtime session ID for this conversation
            (must be >= 33 chars, matching the InvokeAgentRuntime API's
            runtimeSessionId constraint). When provided and
            SESSION_STORAGE_BUCKET is configured, conversation history is
            persisted to S3 under this session ID so follow-up turns in the
            same runtime session see prior context. When omitted, the agent
            has no memory beyond the current call (each /invocations call
            starts a fresh conversation). Also used as the session id for
            reasoning-step recording (session_store.py) when set, so the
            session viewer can show this conversation's steps regardless of
            which entrypoint (HTTP chat UI, or an external agent over A2A)
            invoked it — see reasoning_hooks.py.
        seller_agent_id: Which seller agent (from seller_agents.py's
            registry) this conversation's tools should target. When
            omitted, resolve_seller_agent() falls back to
            DEFAULT_SELLER_AGENT_ID / the first registry entry.
        invoker: Human-readable name of whoever/whatever started this
            conversation (e.g. "Chat UI", or an external A2A caller's
            declared agent name). Recorded on each reasoning step's
            "response" event and in the session's meta record, purely for
            display in the session viewer.
    """
    model_id = os.environ.get("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)
    region = resolve_region()

    model = BedrockModel(
        model_id=model_id,
        region_name=region,
        max_tokens=int(os.environ.get("BEDROCK_MAX_TOKENS", DEFAULT_MAX_TOKENS)),
        # The AdCP tool schemas are large and identical on every turn of a session, so they are the one
        # part of the input worth caching. Cuts input tokens per turn without changing what the model sees.
        cache_tools="default",
        # Retry Bedrock's TRANSIENT overloads automatically: ThrottlingException and the intermittent
        # 5xx/ModelStreamError a big turn can trigger. `adaptive` adds client-side rate-limiting and
        # backoff on top of the retries, so a burst of parallel tool-driven turns backs off instead of
        # failing. This does NOT rescue a deterministic "input too long" ValidationException -- retrying
        # the same oversized batch just fails again; that case is prevented at the source by the
        # batch-size guidance in the system prompt (a model that books in small batches never builds a
        # turn that large). read_timeout is set here because Strands only applies its own default when no
        # config is passed. Uses botocore, not adcp -- this runtime cannot import the adcp SDK.
        boto_client_config=BotocoreConfig(
            retries={"max_attempts": 5, "mode": "adaptive"},
            read_timeout=120,
            connect_timeout=10,
        ),
    )

    session_manager = None
    bucket = os.environ.get("SESSION_STORAGE_BUCKET")
    if session_id and bucket:
        if len(session_id) < MIN_SESSION_ID_LENGTH:
            raise ValueError(
                f"session_id must be at least {MIN_SESSION_ID_LENGTH} characters "
                f"(got {len(session_id)}): {session_id!r}"
            )
        session_manager = S3SessionManager(
            session_id=session_id,
            bucket=bucket,
            prefix=os.environ.get("SESSION_STORAGE_PREFIX", "adcp-buyer-agent"),
            region_name=region,
        )

    # A buyer agent represents its buyer against every sales agent it's
    # configured with, and queries them all — the shape AdCP's own SDK defines
    # (ADCPMultiAgentClient runs a task across all agents in parallel). Which
    # seller's inventory to recommend is then the agent's decision, made from
    # real responses, rather than something chosen for it before the
    # conversation starts.
    #
    # seller_agent_id remains an optional *restriction* for callers that
    # genuinely want one seller (API clients, conformance tests against a
    # single endpoint). The chat UI doesn't send it.
    if seller_agent_id:
        sellers = [resolve_seller_agent(seller_agent_id)]
    else:
        sellers = [resolve_seller_agent(entry["id"]) for entry in list_seller_agents_public()]

    # session_id is this conversation's AgentCore runtimeSessionId. Passing it
    # to the tools lets them derive one stable downstream session per seller,
    # so each seller sees this conversation's tool calls as a single session
    # instead of one per call (adcp_tools.derive_seller_session_id).
    tools = build_adcp_tools(sellers, buyer_session_id=session_id)

    # The model has no reliable clock of its own: left to infer the year it
    # picks one from its training data, which is how flight dates came back
    # in the wrong year. Read the real one from the system clock and state it
    # in the prompt. Resolved when the Agent is built — one build per turn on
    # the HTTP path, one per session on the A2A path (StrandsA2AExecutor
    # caches an Agent per context_id), so treat it as "as of session start".
    now = datetime.now(timezone.utc)

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        seller_list="\n".join(
            f"- {s['name']} (id: {s['id']}, transport: {s.get('transport', 'mcp')})"
            for s in sellers
        ),
        today=now.strftime("%A, %d %B %Y (%Y-%m-%d, UTC)"),
        year=now.year,
    )

    hooks = [ReasoningRecorder(session_id, invoker)] if session_id else []

    return Agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        session_manager=session_manager,
        hooks=hooks,
        # Summarise on overflow instead of dropping messages. Passing nothing here gets Strands'
        # default SlidingWindowConversationManager(window_size=40), which silently discards the
        # oldest messages once a session passes forty — so a long session loses the plan it agreed
        # at the start rather than degrading gracefully.
        #
        # NOT `context_manager="auto"`, which would otherwise be the obvious choice: it bundles a
        # ContextOffloader that replaces any tool result over 1,500 tokens with a preview. The
        # offloader acts on AfterToolCallEvent, the same event ReasoningRecorder records from, and
        # that event sets should_reverse_callbacks=True while plugins are registered after user
        # hooks — so the offloader runs FIRST and the recorder would persist previews rather than
        # real tool results. The journey UI renders from those recorded steps, so every product,
        # creative and media-buy panel would be built from truncated data. Reactive summarisation
        # avoids the collision because it acts on message history, not on tool results.
        conversation_manager=SummarizingConversationManager(
            summary_ratio=0.3,
            preserve_recent_messages=10,
        ),
    )
