"""
AdCP Reference Test Seller Agent.

A minimal, spec-conformant AdCP sales agent, built on the official `adcp`
Python SDK (adcp.server.ADCPHandler + adcp.server.serve), deployed to AWS
Bedrock AgentCore Runtime as an MCP server (via the AgentCore CLI,
protocol="MCP" in ../../agentcore/agentcore.json). Exists so the buyer
agent in ../../../../buyer/reference-buyer has a seller to talk to without
needing any third-party seller's production credentials - the buyer agent's seller registry
(SELLER_AGENTS_JSON) can point at any AdCP seller, this is just one
built-in, always-available option.

This is explicitly a sandbox/reference seller, not a production one:
  - Its inventory (see fixtures.py) is a small, fixed, hand-authored
    catalog, not live market data.
  - Every response is built with adcp.server.responses builders, which set
    sandbox: true by default - callers can see on the wire that this is
    not a real production seller's live data.
  - get_products matches the brief deterministically (keyword overlap, see
    fixtures.py::match_products) - no LLM, no randomness. The same brief
    always returns the same products.

Implements the three read-only discovery tasks the buyer agent actually
calls (get_adcp_capabilities, get_products, list_creative_formats) plus
create_media_buy (task 3), update_media_buy (task 4), get_media_buys
(task 5), get_media_buy_delivery (task 6), provide_performance_feedback
(task 7), and sync_accounts/list_accounts (task 8) of
.kiro/specs/seller-agent-adcp-compliance/tasks.md. Every media buy this
agent creates, updates, reports delivery for, or receives feedback
against - and every account it syncs - is stored in DynamoDB (see
state.py). Idempotency (replay/conflict) comes from the `adcp` SDK's
IdempotencyStore rather than a hand-rolled check, and delivery figures
are derived deterministically from stored state (see delivery.py).
Every other mutating task is unimplemented and returns the SDK's
built-in "not supported" response.

Run locally:
    uv run python main.py
Then point the buyer agent's SELLER_AGENTS_JSON at
http://localhost:8000/mcp (auth_type omitted/no bearer token needed
locally - see ../../README.md's "Authentication" for the deployed case).
"""

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import jwt

from adcp.server import ADCPHandler, RequestMetadata, ToolContext, serve
from adcp.types.generated_poc.core.package import Package

# Session recording for the chat UI's Sessions view. Vendored into this
# package by deploy_all.py's vendor-session-module step (single source:
# agents/buyer/reference-buyer/seller_session_recorder.py) — see that file for
# why it's copied rather than imported across packages.
import seller_session_recorder
from adcp.server.idempotency import IdempotencyStore
from adcp.server.responses import (
    capabilities_response,
    creative_formats_response,
    delivery_response,
    media_buy_error_response,
    media_buy_response,
    media_buys_response,
    products_response,
    sync_accounts_response,
    sync_governance_response,
    update_media_buy_response,
)

from delivery import derive_delivery_figures
from fixtures import CREATIVE_FORMATS, find_product, match_products
import governance_verification
from state import (
    AccountStore,
    DynamoDBIdempotencyBackend,
    IDEMPOTENCY_TTL_SECONDS,
    MediaBuyNotFoundError,
    MediaBuyStore,
    new_media_buy_id,
)
from validation import validate_account_oneof

AGENT_NAME = "adcp-reference-test-seller"


def _coerce_flight_time(value: Any, *, end_of_day: bool) -> datetime | None:
    """Resolve a request flight bound to an aware datetime, or None if it cannot be.

    AdCP's Package.start_time/end_time are AwareDatetime and the spec says sellers SHOULD echo the
    resolved values even when inherited from the buy window. The buyer sends a date (`2026-10-01`)
    or a full timestamp; a bare date is a faithful whole-day resolution (start-of-day / end-of-day
    UTC), not an invented precision. Anything unparseable yields None so the field is simply omitted
    rather than breaking the booking.
    """
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        text = f"{text}T23:59:59+00:00" if end_of_day else f"{text}T00:00:00+00:00"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _selected_pricing_option(product: dict[str, Any], pricing_option_id: Any) -> dict[str, Any] | None:
    """The product's own pricing option the buyer selected, by id. Real catalogue data."""
    for option in product.get("pricing_options") or []:
        if option.get("pricing_option_id") == pricing_option_id:
            return option
    return None


def _impressions_from_cpm(budget: Any, option: dict[str, Any] | None) -> float | None:
    """Impression goal implied by a real budget against the product's real CPM: budget / cpm * 1000.

    Only for cpm pricing with a positive fixed price; otherwise None (omitted). This is computed
    from two figures the buyer/seller already agreed, not a fabricated estimate.
    """
    if budget is None or option is None or option.get("pricing_model") != "cpm":
        return None
    price = option.get("fixed_price")
    if not isinstance(price, (int, float)) or price <= 0:
        return None
    return round(float(budget) / float(price) * 1000.0)


def _build_response_package(
    package_dict: dict[str, Any],
    product: dict[str, Any],
    *,
    start_time: datetime | None,
    end_time: datetime | None,
) -> dict[str, Any]:
    """Enrich a booked package with the product's REAL catalogue attributes, validated by the SDK.

    Constructs the SDK `Package` so every echoed field (format_ids, impressions, pacing, flight
    times) is validated at construction rather than hand-assembled -- then serialises to the dict
    the response builder and the media-buy store both consume. Only fields with a genuine source are
    set: format_ids and the CPM currency come from the product; impressions is derived from the real
    budget and CPM. targeting/creatives/optimization goals are NOT set, because this buyer does not
    send them -- their absence renders nothing rather than an invented row.
    """
    option = _selected_pricing_option(product, package_dict.get("pricing_option_id"))
    package = Package(
        package_id=f"pkg_{uuid.uuid4().hex}",
        product_id=package_dict["product_id"],
        budget=package_dict.get("budget"),
        pricing_option_id=package_dict.get("pricing_option_id"),
        impressions=_impressions_from_cpm(package_dict.get("budget"), option),
        format_ids=product.get("format_ids") or None,
        start_time=start_time,
        end_time=end_time,
    )
    serialised = package.model_dump(mode="json", exclude_none=True)
    currency = (option or {}).get("currency")
    if currency:
        # Not a Package schema field (currency lives on the pricing option / buy), but the read
        # surfaces key money off it; carried as an allowed extra so `$` renders correctly.
        serialised["currency"] = currency
    return serialised


def _caller_identity_from_authorization_header(request: Any) -> str | None:
    """Extract a stable per-caller identity from the incoming request's
    real `Authorization: Bearer <cognito-access-token>` header.

    Bug (found live, task 10 verification): every `@idempotency_store.wrap`
    handler (`create_media_buy`, `update_media_buy`,
    `provide_performance_feedback`, `sync_accounts`) never deduped over the
    real deployed MCP transport - every call created a new resource, even
    with an identical `idempotency_key` + body. Root cause: `main.py` never
    passed a `context_factory` to `serve()`, so every `ToolContext` the
    dispatcher builds is the bare default (`ToolContext()`,
    `caller_identity=None`) - `IdempotencyStore._prepare` (see
    `adcp.server.idempotency.store`) fails closed and skips dedup entirely
    whenever `caller_identity` is `None` (documented, intentional: "no
    caller identity: we can't safely scope the key... fall through to the
    handler").

    Fix: derive `caller_identity` from the authenticated caller. A static
    `caller_identity="default"` would collapse every distinct buyer into
    one shared idempotency namespace, which is the cross-principal
    replay/leak the SDK's docs warn `caller_identity` reuse causes - see
    `ToolContext.caller_identity`'s docstring. This
    agent's deployed runtime (`agents/seller/reference-seller/agentcore/agentcore.json`)
    is `authorizerType: CUSTOM_JWT`: AgentCore Runtime's platform-level
    Cognito JWT authorizer already verifies the token's signature, issuer,
    and audience/client allowlist *before* forwarding the request to this
    container - `requestHeaderAllowlist: ["Authorization"]` is what makes
    the (already-verified) header visible here at all. So this function
    does not re-verify the signature (that would require this container
    to also hold the pool's JWKS and duplicate work the platform already
    did as the security boundary) - it only *decodes* the claims of a
    request that could not have reached this code path without already
    passing that boundary. Same trust model this project's `main.py`/README
    documents for `enable_dns_rebinding_protection=False`: the platform's
    authorizer is the inbound security boundary, not a container-local check.

    Uses the Cognito access token's `sub` claim (a stable, globally-unique
    UUID per Cognito user - never reused across principals, unlike email/
    username) as `caller_identity`. Falls back to `client_id` (the Cognito
    app client id) only when `sub` is absent, and to `None` (no dedup, the
    SDK's documented fall-through) when there is no bearer token at all -
    e.g. local `uv run python main.py` development, where no platform
    authorizer sits in front of this code (see README.md's "Local
    development").
    """
    auth_header = None
    headers = getattr(request, "headers", None)
    if headers is not None:
        auth_header = headers.get("authorization") or headers.get("Authorization")
    if not auth_header:
        return None

    parts = auth_header.split(maxsplit=1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    if not token:
        return None

    try:
        claims = jwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_aud": False,
                "verify_exp": False,
                "verify_iss": False,
            },
        )
    except jwt.exceptions.DecodeError:
        return None

    return claims.get("sub") or claims.get("client_id")


def _build_tool_context(meta: RequestMetadata) -> ToolContext:
    """`adcp.server.ContextFactory` wiring `caller_identity` from the real
    authenticated caller (see `_caller_identity_from_authorization_header`
    above) into every `ToolContext` the dispatcher builds - the missing
    piece that made every `@idempotency_store.wrap` handler's dedup a
    no-op over the real deployed MCP transport."""
    # Also capture whichever session id the buyer propagated on this request,
    # for the session-recording middleware below. Only the context factory is
    # handed the transport's request metadata (and so its headers), which is
    # why this is observed here rather than in the middleware itself. Purely
    # additive - it does not change the ToolContext returned.
    seller_session_recorder.observe_request(meta.request_context)
    return ToolContext(caller_identity=_caller_identity_from_authorization_header(meta.request_context))

# Single module-level idempotency store, shared by every mutating task
# handler (create_media_buy here; update_media_buy in task 4). Backed by
# the same `adcp-reference-seller-state` table state.py's other stores use
# (lazy STATE_TABLE_NAME lookup on each call - see
# DynamoDBIdempotencyBackend._tbl - so no AWS call happens at import time,
# and tests can point STATE_TABLE_NAME at a moto-mocked table without any
# dependency-injection seam here).
idempotency_store = IdempotencyStore(
    backend=DynamoDBIdempotencyBackend(),
    ttl_seconds=IDEMPOTENCY_TTL_SECONDS,
)

# Single module-level media-buy store, same lazy-table-lookup pattern as
# idempotency_store above.
media_buy_store = MediaBuyStore()

# Single module-level account store (task 8), same lazy-table-lookup
# pattern as media_buy_store above.
account_store = AccountStore()


def _to_dict(value: Any) -> dict[str, Any] | None:
    """Coerce a request field to a plain dict.

    `main.py`'s task handlers all type-hint `params: Any` (matching this
    file's existing style for get_products/get_adcp_capabilities/
    list_creative_formats), so the AdCP SDK's dispatcher never coerces
    `params` into a typed `CreateMediaBuyRequest` for us (see
    `adcp.server.mcp_tools._resolve_params_pydantic_model` - it reads
    Python type hints, and `Any` resolves to no coercion). `params`
    therefore normally arrives as a plain dict already. This helper is a
    defensive fallback for the (currently untriggered, but doc'd per task
    3's instructions) case where a caller hands this a typed Pydantic
    model instead - e.g. `account`/individual `packages[*]` entries if a
    future task starts type-hinting more precisely.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    return dict(value)


def _validation_error(pointer: str, message: str, *, keyword: str = "required") -> dict[str, Any]:
    """Build one `media_buy_error_response`-ready error dict for a
    VALIDATION_ERROR, matching AdCP's documented error shape: `code`,
    `message`, `field` (JSONPath-lite, for pre-3.1 consumers), and
    `issues[]` (RFC 6901 pointer, for 3.1+ consumers) - see
    `adcp.types` `Error`/`Issue` and the buyer skill's error-envelope
    section. `field` is derived from `pointer` per AdCP's own translation
    rule (`/foo/0/bar` -> `foo[0].bar`); every pointer this module ever
    passes is a single top-level segment (`/idempotency_key`, `/account`,
    `/packages`), so the translation is just stripping the leading `/`.
    """
    field = pointer[1:] if pointer.startswith("/") else pointer
    return {
        "code": "VALIDATION_ERROR",
        "message": message,
        "field": field,
        "issues": [{"pointer": pointer, "keyword": keyword, "message": message}],
    }


def _governance_error(code: str, message: str) -> dict[str, Any]:
    """U3/BR-U3-6: build one `media_buy_error_response`-ready error dict for a governance-related
    rejection (`code` is one of `PERMISSION_DENIED`, `GOVERNANCE_DENIED`, `GOVERNANCE_UNAVAILABLE` --
    the AdCP `ErrorCode` enum values this project's spec research confirmed for these three cases).
    `message` is a generic, non-diagnostic string -- NFR-U3-3 requires the specific verification
    failure reason to stay server-side only, never echoed to the caller (spec: "Servers MUST NOT
    echo internal verification details... to the counterparty")."""
    return {"code": code, "message": message}


def _media_buy_view(record: dict[str, Any]) -> dict[str, Any]:
    """Project a stored `MediaBuyStore` record (state.py) into the per-item
    shape `adcp.server.responses.media_buys_response` expects (see its
    docstring: "Each media buy should include: media_buy_id, status,
    currency, packages" - matching `adcp.types.generated_poc.media_buy
    .get_media_buys_response.MediaBuy`, which also requires `total_budget`
    and `revision`).

    `currency` isn't a field `MediaBuyStore.create` persists (task 3's
    stored record has no currency column - see state.py) - it defaults to
    `"USD"` here because every product/pricing_option in this fixture
    catalog (fixtures.py) is USD-denominated. `confirmed_at` defaults to
    the record's `created_at`: this agent confirms every media buy
    synchronously at creation (see `create_media_buy`'s
    `status="completed"`), so "when the seller committed" and "when it was
    created" are the same timestamp for every record this store holds.

    `MediaBuy`'s schema declares `extra="allow"`, so passing through
    `account`/`cancellation_reason` when present is harmless even though
    they're not exactly the schema's `account`/`cancellation` shapes;
    `_serialize`'s `_strip_none_values` pass (adcp.server.responses) drops
    them entirely when absent rather than emitting a null.
    """
    return {
        "media_buy_id": record["media_buy_id"],
        "status": record.get("status", "completed"),
        "currency": "USD",
        "total_budget": record.get("budget"),
        "packages": record.get("packages") or [],
        "revision": int(record.get("revision") or 1),
        "created_at": record.get("created_at"),
        "confirmed_at": record.get("created_at"),
        "account": _media_buy_account_view(record.get("account")),
        "cancellation_reason": record.get("cancellation_reason"),
    }


def _media_buy_account_view(account: dict[str, Any] | None) -> dict[str, Any] | None:
    """Normalize a stored media-buy `account` object into the full
    `adcp.types.generated_poc.core.account.Account` shape the
    get_media_buys response schema requires.

    Bug (found live, task 10 verification): `_media_buy_view` used to pass
    the stored `account` straight through unchanged. That's fine for the
    `{account_id}` oneOf variant's *presence* of `account_id`, but the
    `Account` schema requires `account_id`, `name`, AND `status` on every
    account object it emits (see `core/account.json`'s `required` list) -
    regardless of which oneOf variant the buyer originally submitted on
    `create_media_buy`/`update_media_buy`. A `{brand, operator}`-created
    account has no `account_id` field at all, so passing it through
    verbatim fails live schema validation with `'account_id' is a
    required property`.

    Fix: derive `account_id` for the `{brand, operator}` variant using the
    exact same deterministic `_account_id_slug` derivation `sync_accounts`/
    `list_accounts` (R6, `_account_view` below) already use - so a
    `{brand, operator}` account synced via `sync_accounts` and a media buy
    created against the same `(brand, operator)` pair resolve to the same
    `account_id`, rather than two different ids for one account.
    `name`/`status` are built the same way `_account_view` does: a label
    from the stored brand/operator, and `status: "active"` since this agent
    has no seller-side approval step (see `_account_view`'s docstring).
    The `{account_id}` variant is passed
    through with the same `name`/`status` backfill, since that variant's
    schema requirement is identical.

    Returns `None` (schema-legal - `account` is optional on `MediaBuy`)
    when `account` is absent, not a dict, or has neither oneOf variant's
    identifying fields."""
    if not isinstance(account, dict):
        return None
    account_id = account.get("account_id")
    brand = account.get("brand")
    brand_domain = brand.get("domain") if isinstance(brand, dict) else None
    operator = account.get("operator")
    if not account_id and brand_domain and operator:
        account_id = _account_id_slug(brand_domain, operator)
    if not account_id:
        return None
    view: dict[str, Any] = {
        "account_id": account_id,
        "name": f"{brand_domain} via {operator}" if brand_domain and operator else account_id,
        "status": "active",
    }
    if brand_domain:
        view["brand"] = {"domain": brand_domain}
    if operator:
        view["operator"] = operator
    return view


def _account_id_slug(brand_domain: str, operator: str) -> str:
    """A deterministic `account_id` derived from `(brand_domain, operator)`.

    `AccountStore` (state.py) already uses `(brand, operator)` as this
    table's natural key (`ACCOUNT#<brand>#<operator>`), so this lowercases
    and slugifies that pair into the `account_id: str` shape the AdCP
    `Account`/`AccountWithAuthorization` schemas expect. The same pair
    always produces the same id, so `sync_accounts` and `list_accounts`
    (and repeat `sync_accounts` calls for one pair) agree on one
    identifier."""
    slug = re.sub(r"[^a-z0-9]+", "_", f"{brand_domain}_{operator}".lower()).strip("_")
    return f"acc_{slug}"


def _account_view(record: dict[str, Any]) -> dict[str, Any]:
    """Project a stored `AccountStore` record (state.py) into the shape
    `adcp.types.generated_poc.core.account_with_authorization
    .AccountWithAuthorization` requires (via `Account`): `account_id`,
    `name`, `status` are all required strings/enums.

    `status` is always `"active"` — this agent has no seller-side
    onboarding/approval step for buyer-declared accounts (matching its
    existing `require_operator_auth: false` capability declaration, see
    `get_adcp_capabilities`), so an account is active the moment it is
    stored. `name` is a label built from this record's stored
    `brand`/`operator`; this store has no separate display-name field.
    """
    brand_domain = record.get("brand")
    operator = record.get("operator")
    account_id = _account_id_slug(brand_domain, operator) if brand_domain and operator else None
    return {
        "account_id": account_id,
        "name": f"{brand_domain} via {operator}" if brand_domain and operator else (account_id or "account"),
        "status": "active",
        "brand": {"domain": brand_domain} if brand_domain else None,
        "operator": operator,
    }


def _pricing_option_cpm(product: dict[str, Any] | None, pricing_option_id: str | None) -> float:
    """Look up the `fixed_price` (CPM) of a product's pricing option by
    id. Returns `0.0` when the product is missing, the pricing option
    isn't found, or the matched option
    isn't a `cpm` model - `delivery.derive_delivery_figures` treats a
    non-positive `cpm` as "no valid pricing, zero delivery" rather than
    dividing by zero or guessing a value."""
    if not product or not pricing_option_id:
        return 0.0
    for option in product.get("pricing_options") or []:
        if option.get("pricing_option_id") == pricing_option_id and option.get("pricing_model") == "cpm":
            return float(option.get("fixed_price") or 0.0)
    return 0.0


def _media_buy_delivery_view(record: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """Project a stored `MediaBuyStore` record into the per-item shape
    `adcp.server.responses.delivery_response` expects for
    `media_buy_deliveries[*]` - matching `adcp.types.generated_poc
    .media_buy.get_media_buy_delivery_response.MediaBuyDelivery`
    (required: `media_buy_id`, `status`, `totals`, `by_package`).

    Every figure comes from `delivery.derive_delivery_figures` (see that
    module's docstring), fed with this record's stored `budget`/`created_at`
    and the matched product's pricing option `fixed_price`.

    Computes one set of figures for the whole media buy (fed to
    `totals`) and one set per package (fed to `by_package`, using each
    package's own `budget` against the same product's pricing option -
    packages in this fixture catalog's single-product-per-buy scope all
    share the buy's `product_id`, so they share the same CPM; a
    multi-product buy would need a per-package product lookup, out of
    this fixture-scale agent's scope).
    """
    product = find_product(record.get("product_id"))
    cpm = _pricing_option_cpm(product, record.get("pricing_option_id"))
    created_at = record.get("created_at")

    totals_figures = derive_delivery_figures(
        budget=float(record.get("budget") or 0.0),
        cpm=cpm,
        created_at=created_at,
        now=now,
    )

    by_package: list[dict[str, Any]] = []
    for package in record.get("packages") or []:
        package_figures = derive_delivery_figures(
            budget=float(package.get("budget") or 0.0),
            cpm=cpm,
            created_at=created_at,
            now=now,
        )
        by_package.append(
            {
                "package_id": package.get("package_id"),
                "pricing_model": "cpm",
                "rate": cpm,
                "currency": "USD",
                "impressions": package_figures.impressions,
                "spend": package_figures.spend,
            }
        )

    return {
        "media_buy_id": record["media_buy_id"],
        "status": record.get("status", "completed"),
        "pricing_model": "cpm",
        "totals": {
            "impressions": totals_figures.impressions,
            "spend": totals_figures.spend,
        },
        "by_package": by_package,
    }


class ReferenceSellerAgent(ADCPHandler):
    # Explicitly declares which tools this agent advertises via tools/list,
    # rather than inheriting ADCPHandler's full ~60-tool surface (most of
    # which this reference seller doesn't implement). Only the read-only
    # discovery tasks plus create_media_buy (task 3) are real; everything
    # else would otherwise be advertised as available and then fail with
    # a generic "not supported" response, which is misleading discovery
    # behavior.
    advertised_tools: set[str] = {
        "get_adcp_capabilities",
        "get_products",
        "list_creative_formats",
        "create_media_buy",
        "update_media_buy",
        "get_media_buys",
        "get_media_buy_delivery",
        "provide_performance_feedback",
        "sync_accounts",
        "list_accounts",
        # Implementing the handler is NOT enough to make a tool callable: this set is what
        # `tools/list` advertises and what the dispatcher will accept, so a tool absent here answers
        # "Unknown tool: sync_governance" however complete its implementation is. That is exactly what
        # happened -- the handler and 11 unit tests landed while this line did not, the unit tests
        # passed because they call `agent.sync_governance` directly and never cross the dispatcher, and
        # the defect only surfaced when a real buyer invocation tried to use it.
        # `test_advertised_tools_matches_dispatcher` now pins this set to the dispatcher's own view.
        "sync_governance",
    }

    async def get_adcp_capabilities(self, params: Any, context: Any = None) -> dict[str, Any]:
        resp = capabilities_response(
            ["media_buy"],
            major_versions=[3],
            features={
                "pricing_models": ["cpm"],
                "channels": ["ctv", "gaming", "display", "podcast"],
            },
            # Required by the get_adcp_capabilities response schema whenever
            # media_buy is declared. Task 9: idempotency is now real (every
            # mutating task - create_media_buy, update_media_buy,
            # provide_performance_feedback, sync_accounts - is decorated with
            # @idempotency_store.wrap, see this module's top), so this
            # declares actual support rather than a false negative.
            # `idempotency_store.capability()` (adcp.server.idempotency
            # .IdempotencyStore.capability, see state.py's module docstring)
            # returns exactly `{"supported": True, "replay_ttl_seconds":
            # IDEMPOTENCY_TTL_SECONDS}` - the same real ttl_seconds value
            # idempotency_store was constructed with above, never a
            # hand-typed duplicate that could drift out of sync with it.
            idempotency=idempotency_store.capability(),
        )
        # "account" is required by the schema when media_buy is declared.
        # This reference seller uses buyer-declared accounts (no seller-side
        # onboarding/auth needed to call get_products), so no operator auth
        # and no specific billing party requirement.
        resp["account"] = {
            "require_operator_auth": False,
            "supported_billing": ["operator", "agent"],
        }
        return resp

    async def get_products(self, params: Any, context: Any = None) -> dict[str, Any]:
        brief = params.get("brief") if isinstance(params, dict) else getattr(params, "brief", None)
        return products_response(match_products(brief))

    async def list_creative_formats(self, params: Any, context: Any = None) -> dict[str, Any]:
        return creative_formats_response(CREATIVE_FORMATS)

    @idempotency_store.wrap
    async def create_media_buy(self, params: Any, context: Any = None) -> dict[str, Any]:
        """R1: create a media buy against a product from the fixture
        catalog. See `.kiro/specs/seller-agent-adcp-compliance/{requirements,design}.md`
        (R1) for the full contract this implements.

        `@idempotency_store.wrap` (see state.py's module docstring and
        `adcp.server.idempotency.IdempotencyStore.wrap`) owns replay/
        conflict detection entirely - it intercepts the call before this
        body runs when `idempotency_key` is present AND `context` carries
        a `caller_identity` (see this project's IMPORTANT note on the
        caller_identity gap, and get_adcp_capabilities/task 9 for the
        capability declaration this store backs). When either is
        missing, the wrap falls through to this body unwrapped - so this
        handler still explicitly checks for a missing `idempotency_key`
        below rather than relying on the wrap for that case.
        """
        params_dict = _to_dict(params) or {}

        idempotency_key = params_dict.get("idempotency_key")
        if not idempotency_key:
            return media_buy_error_response(
                [_validation_error("/idempotency_key", "idempotency_key is required")]
            )

        account = _to_dict(params_dict.get("account"))
        account_error = validate_account_oneof(account)
        if account_error is not None:
            return media_buy_error_response([_validation_error("/account", account_error, keyword="oneOf")])

        packages_in = params_dict.get("packages") or []
        if not packages_in:
            return media_buy_error_response(
                [_validation_error("/packages", "at least one package is required")]
            )

        # Validate every package's product_id up front (PRODUCT_NOT_FOUND per
        # R1) before creating anything, so a bad package cannot leave a
        # partially-created media buy behind.
        products_by_package: list[dict[str, Any]] = []
        booked_channels: list[str] = []
        for index, package_in in enumerate(packages_in):
            package_dict = _to_dict(package_in) or {}
            product_id = package_dict.get("product_id")
            product = find_product(product_id) if product_id else None
            if product is None:
                return media_buy_error_response(
                    [
                        {
                            "code": "PRODUCT_NOT_FOUND",
                            "message": f"product_id {product_id!r} does not match any product in the catalog",
                            "field": f"packages[{index}].product_id",
                        }
                    ]
                )
            products_by_package.append(package_dict)
            # The product's own declared channels are real inventory data, so the seller can state them
            # in its planned_delivery (used for the governance channel_compliance check below) without
            # inventing anything. De-duplicated, order-stable across packages.
            for channel in product.get("channels") or []:
                if channel not in booked_channels:
                    booked_channels.append(channel)

        # U3/BR-U3-3 through BR-U3-10: governed accounts require a verified intent token and an
        # independent execution check BEFORE this seller creates anything. BR-U3-10: an account with
        # no governance_agents is completely unaffected -- this whole block is skipped.
        stored_account = None
        if account is not None:
            brand_dict = account.get("brand") or {}
            brand_domain = brand_dict.get("domain") if isinstance(brand_dict, dict) else None
            operator = account.get("operator")
            if brand_domain and operator:
                stored_account = account_store.get(brand_domain, operator)
        governance_agents = (stored_account or {}).get("governance_agents") or []

        if governance_agents:
            plan_id = params_dict.get("plan_id")
            governance_context_in = params_dict.get("governance_context")
            if not plan_id or not governance_context_in:
                # FR-8: log the specific cause server-side (never to the caller). "required" means
                # the account has a governance agent but the request carried no plan_id/context.
                logging.getLogger(__name__).warning(
                    "governance gate: PERMISSION_DENIED reason=missing_token "
                    "has_plan_id=%s has_context=%s",
                    bool(plan_id),
                    bool(governance_context_in),
                )
                return media_buy_error_response(
                    [_governance_error("PERMISSION_DENIED", "governance verification required")]
                )

            governance_agent = governance_agents[0]
            verification = await governance_verification.verify_intent_token(
                governance_context_in,
                plan_id=plan_id,
                this_seller_url=governance_verification.THIS_SELLER_URL,
                governance_agent_url=governance_agent["url"],
            )
            if not verification.valid:
                # FR-8: the specific failure_reason stays server-side (NFR-U3-3 keeps it out of the
                # caller-facing message) but is logged here so a denied buy is diagnosable.
                logging.getLogger(__name__).warning(
                    "governance gate: PERMISSION_DENIED reason=%s plan_id=%s",
                    verification.failure_reason,
                    plan_id,
                )
                return media_buy_error_response(
                    [_governance_error("PERMISSION_DENIED", "governance verification failed")]
                )

            planned_delivery = governance_verification.build_planned_delivery(
                packages_in,
                currency=params_dict.get("currency", "USD"),
                start_time=params_dict.get("start_time", ""),
                end_time=params_dict.get("end_time", ""),
                channels=booked_channels,
            )
            execution_check = await governance_verification.run_execution_check(
                governance_agent_url=governance_agent["url"],
                authentication=governance_agent["authentication"],
                plan_id=plan_id,
                governance_context=governance_context_in,
                planned_delivery=planned_delivery,
            )
            if execution_check.outcome == "unreachable":
                logging.getLogger(__name__).warning(
                    "governance gate: GOVERNANCE_UNAVAILABLE (execution check unreachable) plan_id=%s",
                    plan_id,
                )
                return media_buy_error_response(
                    [_governance_error("GOVERNANCE_UNAVAILABLE", "governance agent unreachable")]
                )
            if execution_check.outcome in ("denied", "conditions"):
                # A second "conditions" is treated as denied for this reference implementation's
                # bounded-retry choice (Code Generation plan Step 10) -- never an infinite loop, and
                # never silently treated as approval per N6.
                logging.getLogger(__name__).warning(
                    "governance gate: GOVERNANCE_DENIED outcome=%s plan_id=%s",
                    execution_check.outcome,
                    plan_id,
                )
                return media_buy_error_response(
                    [_governance_error("GOVERNANCE_DENIED", "governance execution check denied")]
                )
            # outcome == "approved": proceed exactly as an ungoverned account would.

        media_buy_id = new_media_buy_id()
        # Echo the product's real catalogue attributes on each booked package (format, impressions,
        # resolved flight, currency), SDK-validated via `Package`. Everything shown on the buyer's
        # Book card that a seller can honestly supply comes from here; see `_build_response_package`.
        start_dt = _coerce_flight_time(params_dict.get("start_time"), end_of_day=False)
        end_dt = _coerce_flight_time(params_dict.get("end_time"), end_of_day=True)
        packages_out = [
            _build_response_package(package_dict, find_product(package_dict["product_id"]) or {},
                                    start_time=start_dt, end_time=end_dt)
            for package_dict in products_by_package
        ]

        # AdCP's create_media_buy request carries budget per-package
        # (packages[*].budget), not a single top-level budget - see
        # design.md's task-3 IMPORTANT note. The stored record's `budget`
        # is the sum across all packages; MediaBuyStore.create's single
        # (product_id, pricing_option_id) fields describe the first
        # package (this fixture catalog and this task's scope only ever
        # exercise single-package requests - multi-package support is
        # tracked in `packages`, which the store keeps in full).
        total_budget = sum(p.get("budget") for p in packages_out if p.get("budget") is not None)
        media_buy_store.create(
            media_buy_id,
            product_id=packages_out[0]["product_id"],
            pricing_option_id=packages_out[0].get("pricing_option_id"),
            budget=total_budget,
            packages=packages_out,
            account=account,
            status="completed",
        )

        return media_buy_response(media_buy_id, packages_out, status="completed", sandbox=True)

    @idempotency_store.wrap
    async def update_media_buy(self, params: Any, context: Any = None) -> dict[str, Any]:
        """R2: apply budget/pause/cancel changes to an existing media buy.
        See `.kiro/specs/seller-agent-adcp-compliance/{requirements,design}.md`
        (R2) for the full contract this implements.

        Confirmed against `adcp.types.generated_poc.media_buy
        .update_media_buy_request.UpdateMediaBuyRequest` (bundled with the
        installed `adcp==6.6.0` SDK): the real request shape is
        `media_buy_id` + `idempotency_key` + `account` (required) plus a mix
        of top-level whole-buy actions (`paused: bool`, `canceled:
        Literal[True]`, `cancellation_reason`) and a `packages:
        list[PackageUpdate]` array for per-package changes (`package_id` +
        `budget`/`paused`/`canceled`/etc). There is no generic `patch`
        object and no top-level `action` enum - AdCP's update shape is
        "send only the fields you want to change", not a single action
        verb. This handler only implements the fields task 4 asks it to
        exercise (budget change via `packages[*].budget`, whole-buy
        `paused`, whole-buy `canceled`) - other `UpdateMediaBuyRequest`
        fields (targeting, creatives, `new_packages`, webhooks, etc.) are
        accepted on the wire (Pydantic/schema validation upstream of this
        handler doesn't reject them) but silently have no effect here,
        consistent with this reference seller's fixture-scale scope.

        Dispatch note: `adcp.server.mcp_tools.create_tool_caller` resolves
        `params_model` from this method's *type hint* alone
        (`_resolve_params_pydantic_model`, keyed off `typing.get_type_hints`)
        and always invokes `await method(call_params, ctx)` - a single
        positional `(params, context)` call, never split into per-field
        kwargs - regardless of what the type hint says. `patch=`/
        `media_buy_id=` arg-projection is a calling convention
        `IdempotencyStore.wrap`'s docstring documents as *possible* for a
        method literally named `update_media_buy`, but nothing in this
        installed SDK version's dispatcher actually produces it - grepping
        `mcp_tools.py`/`a2a_server.py` for `patch=` or any arg-projection
        call site turns up nothing. So `@idempotency_store.wrap` here uses
        the same positional/dict convention `create_media_buy` uses
        (`_resolve_call_args`'s convention 1), not the arg-projected one -
        no adjustment needed relative to task 3's pattern.
        """
        params_dict = _to_dict(params) or {}

        idempotency_key = params_dict.get("idempotency_key")
        if not idempotency_key:
            return media_buy_error_response(
                [_validation_error("/idempotency_key", "idempotency_key is required")]
            )

        media_buy_id = params_dict.get("media_buy_id")
        if not media_buy_id:
            return media_buy_error_response(
                [_validation_error("/media_buy_id", "media_buy_id is required")]
            )

        account = _to_dict(params_dict.get("account"))
        account_error = validate_account_oneof(account)
        if account_error is not None:
            return media_buy_error_response([_validation_error("/account", account_error, keyword="oneOf")])

        existing = media_buy_store.get(media_buy_id)
        if existing is None:
            return media_buy_error_response(
                [
                    {
                        "code": "MEDIA_BUY_NOT_FOUND",
                        "message": f"media_buy_id {media_buy_id!r} does not exist",
                        "field": "media_buy_id",
                    }
                ]
            )

        update_fields: dict[str, Any] = {}

        # Whole-buy pause/cancel - AdCP's `canceled` is a one-way
        # Literal[True] (irreversible); `paused` is a two-way toggle
        # (True -> paused, False -> resume to active).
        if params_dict.get("canceled") is True:
            update_fields["status"] = "canceled"
            cancellation_reason = params_dict.get("cancellation_reason")
            if cancellation_reason is not None:
                update_fields["cancellation_reason"] = cancellation_reason
        elif "paused" in params_dict and params_dict["paused"] is not None:
            update_fields["status"] = "paused" if params_dict["paused"] else "active"

        # Per-package budget (and other) changes, merged into the stored
        # `packages` list by `package_id` - AdCP's PackageUpdate shape,
        # not a top-level budget field (same "budget lives on packages"
        # rule task 3's create_media_buy already documented).
        package_updates_in = params_dict.get("packages")
        stored_packages: list[dict[str, Any]] = list(existing.get("packages") or [])
        if package_updates_in:
            stored_by_id = {p.get("package_id"): p for p in stored_packages}
            for index, package_update_in in enumerate(package_updates_in):
                package_update_dict = _to_dict(package_update_in) or {}
                package_id = package_update_dict.get("package_id")
                target = stored_by_id.get(package_id)
                if target is None:
                    return media_buy_error_response(
                        [
                            {
                                "code": "PACKAGE_NOT_FOUND",
                                "message": f"package_id {package_id!r} does not exist on media_buy_id {media_buy_id!r}",
                                "field": f"packages[{index}].package_id",
                            }
                        ]
                    )
                if package_update_dict.get("budget") is not None:
                    target["budget"] = package_update_dict["budget"]
            update_fields["packages"] = stored_packages
            total_budget = sum(p["budget"] for p in stored_packages if p.get("budget") is not None)
            update_fields["budget"] = total_budget

        # Optimistic-concurrency revision: this record's next revision is
        # always current + 1, regardless of which fields above actually
        # changed - matches update_media_buy_response's contract that
        # `revision` is "the new optimistic-concurrency token after the
        # update". Stored records created before this task's revision
        # tracking existed (task 3) have no `revision` attribute yet, so this
        # defaults the base to 1, media_buy_response's own default.
        new_revision = int(existing.get("revision") or 1) + 1
        update_fields["revision"] = new_revision

        try:
            updated = media_buy_store.update(media_buy_id, **update_fields)
        except MediaBuyNotFoundError:
            # Raced with a delete between the `get` above and this `update`.
            # Report not-found, the same error a missing id gets.
            return media_buy_error_response(
                [
                    {
                        "code": "MEDIA_BUY_NOT_FOUND",
                        "message": f"media_buy_id {media_buy_id!r} does not exist",
                        "field": "media_buy_id",
                    }
                ]
            )

        return update_media_buy_response(
            media_buy_id,
            affected_packages=updated.get("packages") if package_updates_in else None,
            status=updated.get("status"),
            revision=new_revision,
            sandbox=True,
        )

    @idempotency_store.wrap
    async def provide_performance_feedback(self, params: Any, context: Any = None) -> dict[str, Any]:
        """R5: send optimization signals back to the seller for an existing
        media buy. See `.kiro/specs/seller-agent-adcp-compliance/
        {requirements,design}.md` (R5) for the full contract this
        implements.

        Confirmed against `adcp.types.generated_poc.media_buy
        .provide_performance_feedback_request.ProvidePerformanceFeedbackRequest`
        (bundled with the installed `adcp==6.6.0` SDK): design.md/requirements.md's
        "metric, value, timestamp" pseudocode was a simplification. The real
        request shape has no top-level `account` field at all (unlike
        `create_media_buy`/`update_media_buy` — this task genuinely has no
        `validate_account_oneof` call, confirmed by its absence from the
        generated model rather than assumed), and reports a single
        `performance_index: float` (ge=0.0, "0.0 = no value, 1.0 = expected,
        >1.0 = above expected") against a `measurement_period: DatetimeRange`
        (`start`/`end`), not a free-form `metric`/`value` pair. The closest
        thing to a "metric name" is the legacy `metric_type` enum field
        (deprecated in favor of a richer `metric: Metric | Metric7`
        discriminated union this fixture-scale agent doesn't implement —
        same "accept on the wire, no effect" posture `update_media_buy`'s
        docstring already documents for its own out-of-scope fields),
        defaulting to `"overall_performance"` per the schema's own default
        when omitted. This handler maps that vocabulary onto
        `MediaBuyStore.add_feedback`'s existing `(metric, value)` shape
        (task 2, unchanged by this task): `metric=metric_type` (or the
        schema's own default), `value=performance_index` as reported, and
        lets `timestamp`
        default to submission time (`add_feedback`'s own `_now_iso()`
        fallback), consistent with `PerformanceFeedback.submitted_at`'s
        documented semantics ("timestamp when feedback was submitted").

        Response: there is no dedicated `provide_performance_feedback_response`
        builder in `adcp.server.responses` (checked: absent from that
        module's full export list, unlike `sync_accounts_response`/
        `media_buy_response`/etc.) — confirmed against `adcp.types
        .generated_poc.media_buy.provide_performance_feedback_response
        .ProvidePerformanceFeedbackResponse1` (success) /
        `...Response2` (error) via `.model_validate(...)` that the minimal
        hand-built shapes below are schema-correct: success is
        `{"success": True, "sandbox": True}` (`success` is a
        `Literal[True]`, not a generic status string), error is
        `{"errors": [...]}` — the same shape `media_buy_error_response`
        already produces, reused here rather than duplicating an identical
        one-line builder.

        Confirmed against `adcp._idempotency.IDEMPOTENT_TASKS`: unlike
        `get_media_buys`/`get_media_buy_delivery` (pure reads, no
        idempotency), `provide_performance_feedback` IS listed there — same
        `@idempotency_store.wrap` + manual missing-key check pattern
        `create_media_buy`/`update_media_buy` use (the wrap only dedups
        when `context.caller_identity` is set — same known gap documented
        on those two handlers, not re-litigated here).
        """
        params_dict = _to_dict(params) or {}

        idempotency_key = params_dict.get("idempotency_key")
        if not idempotency_key:
            return media_buy_error_response(
                [_validation_error("/idempotency_key", "idempotency_key is required")]
            )

        media_buy_id = params_dict.get("media_buy_id")
        if not media_buy_id:
            return media_buy_error_response(
                [_validation_error("/media_buy_id", "media_buy_id is required")]
            )

        metric = params_dict.get("metric_type") or "overall_performance"
        value = params_dict.get("performance_index")

        try:
            media_buy_store.add_feedback(media_buy_id, metric=metric, value=value)
        except MediaBuyNotFoundError:
            return media_buy_error_response(
                [
                    {
                        "code": "MEDIA_BUY_NOT_FOUND",
                        "message": f"media_buy_id {media_buy_id!r} does not exist",
                        "field": "media_buy_id",
                    }
                ]
            )

        return {"success": True, "sandbox": True}

    async def get_media_buys(self, params: Any, context: Any = None) -> dict[str, Any]:
        """R3: retrieve the operational state of media buys. See
        `.kiro/specs/seller-agent-adcp-compliance/{requirements,design}.md`
        (R3) for the full contract this implements.

        Confirmed against `adcp.types.generated_poc.media_buy
        .get_media_buys_request.GetMediaBuysRequest` (bundled with the
        installed `adcp==6.6.0` SDK): the optional filter field is
        `media_buy_ids: list[str] | None` (task text's guess was correct),
        not e.g. `ids`. The real request shape also carries an optional
        `account` (an `AccountReference`, used to scope the read - "When
        omitted, returns data across all accessible accounts") and a
        `status_filter` that only applies "when `media_buy_ids` is
        omitted" - this reference seller has no multi-account or
        multi-status semantics to enforce (task 3/4 never partition
        records by account beyond storing whatever `account` object the
        buyer sent), so neither is used to filter the DynamoDB read here;
        this handler always returns from the single shared
        `media_buy_store` regardless of `account`/`status_filter`,
        consistent with this agent's fixture-scale, single-tenant scope.

        No `@idempotency_store.wrap` here - confirmed against
        `adcp._idempotency.IDEMPOTENT_TASKS` (the SDK's own frozenset of
        which tool names get idempotency-key handling) that
        `get_media_buys` is absent from it; idempotency only applies to
        mutating tasks (`create_media_buy`, `update_media_buy`, etc.), and
        this is a pure read.

        No caching layer of any kind sits between this handler and
        `MediaBuyStore.get`/`get_many` - both read DynamoDB fresh on every
        call, so a prior `update_media_buy` call's changes are always
        visible here (R3's "no stale caching" requirement holds by
        construction, not by an explicit invalidation step).
        """
        params_dict = _to_dict(params) or {}
        media_buy_ids = params_dict.get("media_buy_ids")

        records = media_buy_store.get_many(media_buy_ids)
        return media_buys_response([_media_buy_view(record) for record in records], sandbox=True)

    async def get_media_buy_delivery(self, params: Any, context: Any = None) -> dict[str, Any]:
        """R4: deterministic simulated delivery figures for one or more
        media buys. See `.kiro/specs/seller-agent-adcp-compliance/
        {requirements,design}.md` (R4) and `delivery.py`'s module docstring
        (the pure derivation function this delegates to) for the full
        contract this implements.

        Confirmed against `adcp.types.generated_poc.media_buy
        .get_media_buy_delivery_request.GetMediaBuyDeliveryRequest`
        (bundled with the installed `adcp==6.6.0` SDK): the filter field is
        plural `media_buy_ids: list[str] | None` (same convention
        `get_media_buys` already uses, task text's guess was correct) —
        there is no singular `media_buy_id`. The request also carries
        `account` (ignored here, same "single shared store, no
        multi-tenant partitioning" rationale as `get_media_buys`),
        `status_filter` (only applies "when media_buy_ids is omitted" per
        the schema — not enforced by this fixture-scale agent, same as
        `get_media_buys`), `start_date`/`end_date` (see date-range handling
        below), and a long tail of reporting-dimension/breakdown/windowing
        fields (`reporting_dimensions`, `time_granularity`,
        `include_window_breakdown`, `attribution_window`, etc.) this agent
        does not implement — accepted on the wire (upstream Pydantic
        validation doesn't reject unknown-to-this-handler fields) but
        silently have no effect, consistent with this reference seller's
        fixture-scale scope (same posture `update_media_buy`'s docstring
        already documents for its own out-of-scope fields).

        No `@idempotency_store.wrap` here — confirmed against
        `adcp._idempotency.IDEMPOTENT_TASKS` that `get_media_buy_delivery`
        is absent from it (a pure read, same as `get_media_buys`).

        **Date-range handling (R4's `reporting_capabilities.
        date_range_support` requirement):** every product in this fixture
        catalog (`fixtures.py`) declares `date_range_support:
        "lifetime_only"` — this agent has no bounded-window reporting
        pipeline, only a single derived lifetime figure per media buy
        (see `delivery.py`). Per `GetMediaBuyDeliveryRequest`'s own schema
        description, `start_date`/`end_date` are "only accepted when the
        product's reporting_capabilities.date_range_support is
        'date_range'" — so a request that sends them against a
        `lifetime_only` product is asking for something the product never
        declared support for. AdCP's standard error-code vocabulion
        (`adcp._schemas/*/enums/error-code.json`) has no code specific to
        "unsupported reporting window" (checked: no
        `UNSUPPORTED_GRANULARITY`-style code exists for date ranges,
        only for `time_granularity`, which this agent also doesn't
        support and also ignores) — the closest standard fit is
        `UNSUPPORTED_FEATURE` ("a requested feature or field is not
        supported by this seller"). Rather than fail the whole call over
        an ignorable filter field, this handler takes design.md's
        explicitly-permitted no-op path: `start_date`/`end_date` are
        silently ignored and every media buy's full lifetime figures are
        returned regardless of the window requested. Disclosed here because
        this agent cannot compute a windowed figure.
        """
        params_dict = _to_dict(params) or {}
        media_buy_ids = params_dict.get("media_buy_ids")
        now = datetime.now(timezone.utc)

        records = media_buy_store.get_many(media_buy_ids)
        deliveries = [_media_buy_delivery_view(record, now=now) for record in records]
        return delivery_response(deliveries, currency="USD", sandbox=True)

    @idempotency_store.wrap
    async def sync_governance(self, params: Any, context: Any = None) -> dict[str, Any]:
        """Register the governance agent a buyer wants this seller to consult.

        The Bind phase of the buyer's journey. The buyer tells the SELLER which governance agent owns
        its plans, and the seller stores that on the account so a later `check_governance` call has
        somewhere to go. Confirmed against
        `adcp.types.generated_poc.account.sync_governance_request.SyncGovernanceRequest` in the
        installed `adcp==6.6.0`:

            SyncGovernanceRequest  required: idempotency_key, accounts (1..100)
            Account               required: account, governance_agents
            GovernanceAgent       required: url (HTTPS), authentication
            Authentication        required: schemes, credentials

        **One agent per account, and that is a protocol invariant, not a simplification.**
        `Account.governance_agents` carries `minItems: 1, maxItems: 1`, and the SDK's own field
        description calls the array shape wire-compatibility with 3.0 while `maxItems: 1` is
        "load-bearing". So a request carrying two agents is rejected rather than silently taking the
        first, which would leave the buyer believing both were registered.

        **Credentials are write-only.** They are stored (the seller must present them when calling the
        agent) and never echoed. `sync_governance_response` runs entries through the SDK's `_serialize`,
        which strips them, but this handler also simply does not put them in the response -- relying on
        a downstream stripper to hide a credential a caller deliberately included is one refactor away
        from a leak.

        **`categories` is omitted.** The response schema allows a per-agent `categories` list
        describing what the agent evaluates. This seller cannot know that without calling the agent's
        own capabilities surface, and it is not in the request, so the field is absent until the
        seller queries it.
        """
        payload = _to_dict(params) or {}

        idempotency_key = payload.get("idempotency_key")
        if not isinstance(idempotency_key, str) or len(idempotency_key) < 16:
            # Same manual check the other idempotent handlers use: the decorator dedupes on the key but
            # does not validate it, and a short key silently weakens dedup rather than failing.
            return media_buy_error_response(
                [_validation_error("/idempotency_key", "idempotency_key of at least 16 characters is required")]
            )

        entries = payload.get("accounts")
        if not isinstance(entries, list) or not entries:
            return media_buy_error_response(
                [_validation_error("/accounts", "at least one account entry is required")]
            )

        accounts_out: list[dict[str, Any]] = []
        for index, entry in enumerate(entries):
            entry_dict = _to_dict(entry) or {}

            account_ref = _to_dict(entry_dict.get("account"))
            if account_ref is None:
                return media_buy_error_response(
                    [_validation_error(f"/accounts/{index}/account", "account reference is required")]
                )
            ref_brand = _to_dict(account_ref.get("brand"))
            ref_operator = account_ref.get("operator")
            if ref_brand is None or ref_operator is None:
                # Same limitation sync_accounts documents: this seller's accounts are keyed by the
                # (brand, operator) natural pair, so an account_id-only reference has nothing to
                # resolve against without a separate id index.
                return media_buy_error_response(
                    [
                        {
                            "code": "UNSUPPORTED_PROVISIONING",
                            "message": "governance sync by account_id is not supported by this "
                            "buyer-declared-account seller; use the brand/operator natural key",
                            "field": f"accounts[{index}].account",
                        }
                    ]
                )
            brand_domain = ref_brand.get("domain")
            if not brand_domain:
                return media_buy_error_response(
                    [_validation_error(f"/accounts/{index}/account/brand/domain", "brand.domain is required")]
                )

            agents = entry_dict.get("governance_agents")
            if not isinstance(agents, list) or len(agents) != 1:
                return media_buy_error_response(
                    [
                        _validation_error(
                            f"/accounts/{index}/governance_agents",
                            "exactly one governance agent is required (maxItems: 1 is load-bearing: "
                            "one agent owns an account's plans)",
                        )
                    ]
                )

            agent = _to_dict(agents[0]) or {}
            url = agent.get("url")
            if not isinstance(url, str) or not url.startswith("https://"):
                return media_buy_error_response(
                    [
                        _validation_error(
                            f"/accounts/{index}/governance_agents/0/url",
                            "governance agent url is required and must use HTTPS",
                        )
                    ]
                )

            authentication = _to_dict(agent.get("authentication"))
            if authentication is None or not authentication.get("schemes") or not authentication.get(
                "credentials"
            ):
                # Refused rather than stored without credentials: an agent this seller cannot
                # authenticate to is an agent it cannot consult, and a check that silently cannot run
                # is worse than a rejected registration.
                return media_buy_error_response(
                    [
                        _validation_error(
                            f"/accounts/{index}/governance_agents/0/authentication",
                            "authentication with schemes and credentials is required",
                        )
                    ]
                )

            if account_store.get(brand_domain, ref_operator) is None:
                # The account must exist first. sync_accounts is what creates it, and binding a
                # governance agent to an account nobody declared would create a record no later call
                # could find.
                return media_buy_error_response(
                    [
                        {
                            "code": "ACCOUNT_NOT_FOUND",
                            "message": "no such account; call sync_accounts before sync_governance",
                            "field": f"accounts[{index}].account",
                        }
                    ]
                )

            account_store.upsert(
                brand_domain,
                ref_operator,
                governance_agents=[{"url": url, "authentication": authentication}],
            )

            accounts_out.append(
                {
                    "account": {"brand": ref_brand, "operator": ref_operator},
                    "status": "synced",
                    # url only. See the docstring: credentials are write-only, and `categories` is
                    # omitted rather than guessed.
                    "governance_agents": [{"url": url}],
                }
            )

        return sync_governance_response(accounts_out, sandbox=True)

    @idempotency_store.wrap
    async def sync_accounts(self, params: Any, context: Any = None) -> dict[str, Any]:
        """R6: provision/update buyer-declared accounts. See
        `.kiro/specs/seller-agent-adcp-compliance/{requirements,design}.md`
        (R6) for the full contract this implements.

        Confirmed against `adcp.types.generated_poc.account
        .sync_accounts_request.SyncAccountsRequest` (bundled with the
        installed `adcp==6.6.0` SDK): the real request shape is a **bulk**
        sync — `idempotency_key` + `accounts: list[Accounts | Accounts1]`
        (up to 1000 entries per call), not a single `{brand, operator}`
        pair per call as design.md's pseudocode simplified it. Each entry
        is one of two modes: **provisioning mode** (`brand` + `operator` +
        `billing`, no `account` field — creates/refreshes a buyer-declared
        account) or **settings-update mode** (`account: AccountReference`
        present, `brand`/`operator`/`billing` absent — targets an existing
        account, which this fixture-scale agent does not implement any
        settings beyond brand/operator/billing for, so such an entry is
        rejected with `UNSUPPORTED_PROVISIONING`... except this agent's
        buyer-declared-account model has no settings surface at all beyond
        the natural key, so entries in settings-update mode are simply
        re-upserted against whatever `account` reference resolves to a
        known `(brand, operator)` pair — see below).

        This handler processes every entry in `accounts[]` independently
        (one `AccountStore.upsert` call each) and returns one
        `Account`-shaped dict per entry, in request order, matching
        `SyncAccountsResponse1.accounts` — never a single-account response,
        since the real request is always an array.

        `AccountStore.get(brand, operator)` (state.py) is called *before*
        `upsert`, so `action` is derived rather than assumed: `"created"`
        when this `(brand, operator)` pair had no prior record, `"updated"`
        when it did.
        `status` is always `"active"` (see `_account_view`'s docstring —
        this agent has no seller-side approval step).

        `idempotency_key` is required per the schema (`min_length=16`) —
        same manual missing-key check pattern `create_media_buy`/
        `update_media_buy`/`provide_performance_feedback` use, since
        `sync_accounts` IS listed in `adcp._idempotency.IDEMPOTENT_TASKS`
        (confirmed) and gets the same `@idempotency_store.wrap` treatment.

        `delete_missing`/`dry_run`/`push_notification_config`/
        `billing`/`billing_entity`/`payment_terms`/`notification_configs`
        are accepted on the wire (not rejected) but have no effect here —
        same "fixture-scale scope, out-of-scope fields silently ignored"
        posture `update_media_buy`'s docstring already documents.
        """
        params_dict = _to_dict(params) or {}

        idempotency_key = params_dict.get("idempotency_key")
        if not idempotency_key:
            return media_buy_error_response(
                [_validation_error("/idempotency_key", "idempotency_key is required")]
            )

        entries_in = params_dict.get("accounts") or []
        if not entries_in:
            return media_buy_error_response(
                [_validation_error("/accounts", "at least one account entry is required")]
            )

        accounts_out: list[dict[str, Any]] = []
        for index, entry_in in enumerate(entries_in):
            entry_dict = _to_dict(entry_in) or {}

            brand = _to_dict(entry_dict.get("brand"))
            operator = entry_dict.get("operator")
            account_ref = _to_dict(entry_dict.get("account"))

            if brand is None or operator is None:
                if account_ref is not None:
                    # Settings-update mode targeting an existing account by
                    # reference. This agent's buyer-declared-account model
                    # has no settings beyond (brand, operator), so resolve
                    # the natural-key variant and re-sync it rather than
                    # rejecting outright; the account_id variant has
                    # nothing to resolve against without a separate lookup
                    # table, so it is rejected.
                    ref_brand = _to_dict(account_ref.get("brand"))
                    ref_operator = account_ref.get("operator")
                    if ref_brand is None or ref_operator is None:
                        return media_buy_error_response(
                            [
                                {
                                    "code": "UNSUPPORTED_PROVISIONING",
                                    "message": "settings-update by account_id is not supported by this "
                                    "buyer-declared-account seller; use the brand/operator natural key",
                                    "field": f"accounts[{index}].account",
                                }
                            ]
                        )
                    brand, operator = ref_brand, ref_operator
                else:
                    return media_buy_error_response(
                        [
                            _validation_error(
                                f"/accounts/{index}/brand",
                                "each account entry requires brand + operator (provisioning mode) "
                                "or account (settings-update mode)",
                            )
                        ]
                    )

            brand_domain = brand.get("domain") if isinstance(brand, dict) else None
            if not brand_domain or not operator:
                return media_buy_error_response(
                    [_validation_error(f"/accounts/{index}/brand/domain", "brand.domain is required")]
                )

            existing = account_store.get(brand_domain, operator)
            action = "updated" if existing is not None else "created"

            extra: dict[str, Any] = {}
            if entry_dict.get("sandbox") is not None:
                extra["sandbox"] = entry_dict["sandbox"]

            record = account_store.upsert(brand_domain, operator, **extra)

            accounts_out.append(
                {
                    "account_id": _account_id_slug(brand_domain, operator),
                    "brand": {"domain": brand_domain},
                    "operator": operator,
                    "action": action,
                    "status": "active",
                    "sandbox": record.get("sandbox"),
                }
            )

        return sync_accounts_response(accounts_out, sandbox=True)

    async def list_accounts(self, params: Any, context: Any = None) -> dict[str, Any]:
        """R6: list buyer-declared accounts previously provisioned via
        `sync_accounts`. See `.kiro/specs/seller-agent-adcp-compliance/
        {requirements,design}.md` (R6) for the full contract this
        implements.

        Confirmed against `adcp.types.generated_poc.account
        .list_accounts_request.ListAccountsRequest` (bundled with the
        installed `adcp==6.6.0` SDK): no required fields — an optional
        `account: AccountReference` exact filter, an optional `status`
        filter, `pagination`, and an optional `sandbox` filter. This
        fixture-scale agent has a single flat `AccountStore.list()` (no
        pagination, no multi-tenant partitioning — same rationale
        `get_media_buys`/`get_media_buy_delivery` already document for
        their own filters), so `pagination`/`status` filters are accepted
        on the wire but not applied; the optional `account` exact filter
        (natural key) IS honored below, since it maps directly onto this
        store's keys.

        There is no dedicated `list_accounts_response` builder in
        `adcp.server.responses` (confirmed: absent from that module's
        export list, unlike `sync_accounts_response`) — confirmed against
        `adcp.types.generated_poc.account.list_accounts_response
        .ListAccountsResponse` via `.model_validate(...)` that the shape
        below (`{"accounts": [...], "sandbox": True}`) is schema-correct;
        `ListAccountsResponse` is a *different* schema than
        `SyncAccountsResponse1` (its `accounts[*]` items are
        `AccountWithAuthorization`/`Account`, requiring `account_id`,
        `name`, `status` — not `SyncAccountsResponse1`'s `Account`, which
        additionally requires `action`), so this hand-builds the correct
        shape rather than reusing `sync_accounts_response`.

        No `@idempotency_store.wrap` here — confirmed against
        `adcp._idempotency.IDEMPOTENT_TASKS` that `list_accounts` is
        absent from it (a pure read, same as `get_media_buys`).
        """
        params_dict = _to_dict(params) or {}
        account_filter = _to_dict(params_dict.get("account"))

        records = account_store.list()

        if account_filter is not None:
            filter_brand = _to_dict(account_filter.get("brand"))
            filter_brand_domain = filter_brand.get("domain") if isinstance(filter_brand, dict) else None
            filter_operator = account_filter.get("operator")
            if filter_brand_domain and filter_operator:
                records = [
                    r for r in records if r.get("brand") == filter_brand_domain and r.get("operator") == filter_operator
                ]

        return {"accounts": [_account_view(record) for record in records], "sandbox": True}


if __name__ == "__main__":
    # host/port/stateless_http match AgentCore Runtime's MCP container
    # contract exactly (0.0.0.0:8000/mcp, stateless streamable-http) - see
    # https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-mcp-protocol-contract.html
    serve(
        ReferenceSellerAgent(),
        name=AGENT_NAME,
        host="0.0.0.0",
        port=8000,
        stateless_http=True,
        context_factory=_build_tool_context,
        # Outermost (and only) middleware, per the SDK's composition guidance:
        # anything that short-circuits deeper in a chain would otherwise
        # disappear from the trail. Records one session turn per skill
        # dispatch; every write is best-effort and cannot affect the response.
        middleware=[
            seller_session_recorder.make_session_recording_middleware(
                agent_id="reference-seller",
                agent_name="AdCP Reference Test Seller",
            )
        ],
        description=(
            "AdCP reference test seller agent: fixed sandbox inventory "
            "(CTV, mobile rewarded video, display, audio) for conformance "
            "testing and demos. Not a production seller."
        ),
        specialisms=["sales-non-guaranteed"],
        # AgentCore Runtime's own edge proxies requests to this container
        # with a Host header that doesn't match the SDK's default DNS
        # rebinding protection allowlist (localhost/127.0.0.1), which
        # otherwise rejects every request with HTTP 421 "Misdirected
        # Request" before it reaches this agent's code (confirmed live:
        # requests never appeared in this container's own logs). Disabling
        # it here is safe because the only network path to this container
        # is AgentCore Runtime's own trusted proxy layer - the platform's
        # Cognito JWT authorizer (see agentcore/agentcore.json) is the real
        # inbound security boundary, not this host-header check.
        enable_dns_rebinding_protection=False,
    )
