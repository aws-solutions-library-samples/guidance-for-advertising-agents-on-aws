/**
 * Wire shapes this UI receives.
 *
 * Written to describe what the services actually send, including the parts that are genuinely
 * optional. Fields the recorder or a seller may omit are optional here rather than asserted
 * present, because the whole display discipline in this project rests on telling "absent" apart
 * from "zero": a `reachable_audience` of 0 is a measurement, and a missing one is not.
 *
 * `unknown` rather than `any` for open payloads, so reading a field forces a narrowing step
 * instead of silently trusting a shape a seller never promised.
 */

/** `/config` (app.py) or `./config.json` (deploy_ui.py). Same shape from both. */
export interface AppConfig {
  model_id: string;
  cognito_region: string;
  cognito_client_id: string;
  agent_runtime_arn: string;
  aws_region: string;
  /**
   * Cognito Identity Pool for reading recorded sessions straight from DynamoDB, skipping the
   * ~843 ms AgentCore hop the `get_session_steps` action pays.
   *
   * Optional, and empty when the pool is not deployed — `sessionReads.ts` falls back to the
   * `/invocations` actions in that case rather than failing, so a stack without the pool still
   * shows a dashboard.
   */
  identity_pool_id?: string;
  /** The table those direct reads target. Empty when unconfigured; same fallback applies. */
  sessions_table_name?: string;
  seller_agents: SellerAgent[];
  default_seller_agent_id: string;
  agents: ChatAgent[];
  default_agent_id: string;
  /** Carries a registry error the UI surfaces in the selector. Empty when there is none. */
  agents_error: string;
  /**
   * The configured governance agents (environment entries plus any runtime registrations), public
   * metadata only — never a token value. Present at runtime with a `source` field per entry; typed
   * as optional because a config written before this field existed will not carry it.
   */
  governance_agents?: SellerAgent[];
  /**
   * When the site was published, from the deploy. Empty string means served locally by app.py,
   * which has no publish time. Absent entirely means a config written before the field existed,
   * which is a different answer again and reported as "unknown" rather than as local.
   */
  ui_published_at?: string;
  /**
   * Seller ids in the order the journey view prefers to display them, most preferred first.
   *
   * One journey is shown at a time and the others are named beside it, so this decides which. A
   * seller absent from the list ranks last rather than being hidden: the list is a display
   * preference, not an allowlist, and dropping an unlisted seller would conceal the very thing a
   * fan-out exists to reveal. Absent or empty means no configured preference, in which case sellers
   * keep the order the fan-out returned them in.
   */
  seller_priority?: string[];
}

export interface SellerAgent {
  id: string;
  name: string;
  url: string;
  transport: string;
}

export interface ChatAgent {
  id: string;
  name: string;
  url?: string;
  transport?: string;
  /** True when the agent's credential is a server-side secret, so turns must be relayed. */
  proxied?: boolean;
}

/** Cognito's token set, as returned by InitiateAuth / RespondToAuthChallenge. */
export interface AuthResult {
  AccessToken: string;
  IdToken?: string;
  RefreshToken?: string;
  ExpiresIn?: number;
}

export interface NewPasswordChallenge {
  name: 'NEW_PASSWORD_REQUIRED';
  session: string;
  /**
   * The canonical username to echo back. Cognito's `USER_ID_FOR_SRP` can differ from what was
   * typed (an alias, for instance), and answering the challenge with the typed value fails.
   */
  username: string;
  requiredAttributes: string[];
}

export type SignInOutcome =
  | { authResult: AuthResult; challenge?: undefined }
  | { challenge: NewPasswordChallenge; authResult?: undefined };

/** A recorded session's META record, as `list_sessions` and `get_session_steps` return it. */
export interface SessionMeta {
  session_id: string;
  status: 'active' | 'completed' | 'error' | string;
  invoker?: string;
  mode?: string;
  /** Which agent RAN the turn, as opposed to who asked. Absent on records from a writer that
   *  did not report its identity, which the UI reports as "unknown agent". */
  agent_id?: string;
  agent_name?: string;
  seller_agent_id?: string;
  request_preview?: string;
  started_at?: string;
  updated_at?: string;
  completed_at?: string;
  error_message?: string | null;
  /** AdCP plan(s) this conversation worked on, stamped as turns were recorded. Absent pre-plan. */
  plan_ids?: string[];
  /** The plan a plan-scoped step last named, when it named exactly one. */
  latest_plan_id?: string | null;
}

/** One AdCP plan, as `list_plans` returns it. A plan can span more than one conversation. */
export interface PlanSummary {
  plan_id: string;
  /** ISO timestamp of the most recent work on the plan, across its sessions. */
  last_activity?: string;
  /** How many sessions this plan has been worked on in. */
  session_count?: number;
}

export type StepType =
  | 'incoming_request'
  | 'thought'
  | 'tool_call'
  | 'tool_result'
  | 'response'
  | string;

export interface SessionStep {
  step_index: number;
  step_type: StepType;
  content?: StepContent;
  timestamp?: string;
}

/** A step's payload. Every field is optional because which ones appear depends on step_type. */
export interface StepContent {
  text?: string;
  from?: string;
  to?: string;
  toolName?: string;
  name?: string;
  callId?: string;
  input?: unknown;
  output?: unknown;
  status?: string;
  durationMs?: number;
  /** Set by the recorder when it stored only part of an oversized payload. */
  _recording?: { truncated?: boolean; original_bytes?: number };
  /** Set in place of a step the recorder could not store at all. */
  _not_recorded?: { original_bytes?: number; reason?: string };
}

/** One sales agent's entry in a fan-out tool result. */
export interface SellerEntry {
  seller_id?: string;
  seller_name?: string;
  /** Present when the call itself threw, in which case there is no response. */
  error?: string;
  response?: SellerResponse;
}

/**
 * A seller's payload. Open by design: this UI renders products specially and everything else as
 * JSON, so the fields named here are the ones it actually reads.
 */
export interface SellerResponse {
  products?: Product[];
  formats?: unknown[];
  adcp?: unknown;
  mcp_error?: unknown;
  a2a_error?: unknown;
  adcp_error?: unknown;
  /** What the recorder kept versus what the seller sent, when it trimmed the list. */
  products_recording_truncated?: { total?: number };
  [key: string]: unknown;
}

export interface Product {
  product_id?: string;
  name?: string;
  description?: string;
  delivery_type?: string;
  format_ids?: Array<FormatRef | string>;
  pricing_options?: PricingOption[];
  publisher_properties?: unknown[];
  reporting_capabilities?: unknown;
  /** AdCP `MediaChannel` values, e.g. `["radio"]`, `["ctv"]`. */
  channels?: string[];
  /** Where in the publisher's surface the product runs. AdCP `core/placement.json`. */
  placements?: Placement[];
  /** OpenRTB 2.6 `video.plcmt`, AdCP-named: `instream`, `interstitial`, ... */
  video_placement_types?: string[];
  /** OpenRTB 2.6 `audio.feed`, AdCP-named. */
  audio_distribution_types?: string[];
  social_placement_surfaces?: string[];
  sponsored_placement_types?: string[];
  /** AdCP's own labelled-fact carrier. Its schema examples are "Duration: 30s", "Aspect ratio: 9:16". */
  product_card_detailed?: ProductCardDetailed;
  ext?: {
    gotham_measurement?: SellerMeasurement;
    triton_measurement?: SellerMeasurement;
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

/**
 * A product's format reference.
 *
 * `width`/`height`/`duration_ms` are AdCP's own parameterisation of a template format
 * (`core/format_id.json`): dimensions for visual formats, duration for time-based ones. They are how
 * a seller says "the 300x250 variant" or "the 30-second variant" without the buyer parsing the id.
 */
export interface FormatRef {
  id: string;
  agent_url?: string;
  width?: number;
  height?: number;
  duration_ms?: number;
}

/**
 * One placement within a product.
 *
 * `name` is required by AdCP for `kind: "seller_inline"` and is the buyer-facing label, which is what
 * makes it the right home for a slot position like "right-rail" — AdCP defines no enum for on-page
 * position, so the name and `tags` are the vocabulary.
 */
export interface Placement {
  kind?: string;
  placement_id?: string;
  publisher_domain?: string;
  name?: string;
  description?: string;
  /** `targetable` if the buyer may select it by id, `included` if it is part of the composition. */
  mode?: string;
  tags?: string[];
  video_placement_types?: string[];
  audio_distribution_types?: string[];
  [key: string]: unknown;
}

export interface Specification {
  label?: string;
  value?: string;
}

export interface ProductCardDetailed {
  title?: string;
  description?: string;
  specifications?: Specification[];
  price_label?: string;
  [key: string]: unknown;
}

export interface PricingOption {
  pricing_option_id?: string;
  pricing_model?: string;
  currency?: string;
  fixed_price?: number;
}

/**
 * The seller's own measurements. All optional: a seller that could not reach its measurement
 * service omits them rather than sending zeros, and this UI must render that difference.
 */
/**
 * A seller's own measurements, from a vendor-namespaced `ext.*_measurement` block.
 *
 * Named for the shape rather than for Gotham, which was the only seller emitting one for a while.
 * `productRelevance` finds these by the `_measurement` suffix; naming this type after one seller is
 * how Triton's score stayed invisible after it started sending one.
 *
 * All optional: a seller that could not reach its measurement service omits the field rather than
 * sending a zero, and this UI must render that difference.
 */
export interface SellerMeasurement {
  relevance_score?: number;
  fused_score?: number;
  content_similarity?: number;
  audience_similarity?: number;
  reachable_audience?: number;
  reach_as_of?: string;
  /**
   * The reach service's own label for the figure. Present on the wire, not rendered: it is a
   * sentence long and the seller's name on the same row already says "(sandbox, MCP)".
   */
  reach_label?: string;
  /** The single-vector form of the ranking score. Triton sends this; Gotham sends the fused pair. */
  cosine?: number;
  /** A seller may report figures this UI does not read. */
  [key: string]: unknown;
}

/** A Cognito user, as `list_users` reports it. */
export interface PoolUser {
  username: string;
  email?: string;
  /** Cognito's own UserStatus, shown as-is. */
  status?: string;
  /**
   * Whether the user is in the admin group.
   *
   * `null` when membership could not be read, which happens when the group is missing or the caller
   * lacks permission to list it. That is a different answer from `false` and is reported as unknown,
   * because showing "not an admin" on the strength of a failed lookup is a false statement about
   * someone's access.
   */
  is_admin?: boolean | null;
}

export interface UsersEvent {
  type: 'users';
  users?: PoolUser[];
  /** The group name the pool uses for admins, so the UI can label it accurately. */
  admin_group?: string;
}

export interface UserCreatedEvent {
  type: 'user_created';
  username: string;
  temporary_password: string;
  /** Cognito's status for the new user. */
  status?: string;
  /** Whether Cognito will require a password change at first sign-in. */
  must_change_password?: boolean;
}

export interface SessionsEvent {
  type: 'sessions';
  sessions?: SessionMeta[];
}

export interface SessionStepsEvent {
  type: 'session_steps';
  meta?: SessionMeta | null;
  steps?: SessionStep[];
}

export interface PlansEvent {
  type: 'plans';
  plans?: PlanSummary[];
}

/** Steps for one plan, merged across every session it was worked on in. */
export interface PlanStepsEvent {
  type: 'plan_steps';
  plan_id?: string;
  meta?: SessionMeta | null;
  steps?: SessionStep[];
  sessions?: string[];
}

export interface ErrorEvent {
  type: 'error';
  message?: string;
}

/**
 * Events the runtime streams back from an `action` invocation.
 *
 * The open member is deliberate: the runtime may send event types this build does not model, and
 * dropping them at the type level would push the problem to a runtime cast. It does mean `find` on
 * `type` cannot narrow on its own, which is what the finders below are for.
 */
export type InvokeEvent =
  | SessionsEvent
  | SessionStepsEvent
  | PlansEvent
  | PlanStepsEvent
  | UsersEvent
  | UserCreatedEvent
  | ErrorEvent
  | { type: string; [key: string]: unknown };

/** The first `users` event, or null. */
export function findUsersEvent(events: InvokeEvent[]): UsersEvent | null {
  const event = events.find((e) => e.type === 'users');
  return event ? (event as UsersEvent) : null;
}

/** The first `user_created` event, or null. */
export function findUserCreatedEvent(events: InvokeEvent[]): UserCreatedEvent | null {
  const event = events.find((e) => e.type === 'user_created');
  return event ? (event as UserCreatedEvent) : null;
}

/** The first `sessions` event, or null. */
export function findSessionsEvent(events: InvokeEvent[]): SessionsEvent | null {
  const event = events.find((e) => e.type === 'sessions');
  return event ? (event as SessionsEvent) : null;
}

/** The first `session_steps` event, or null. */
export function findSessionStepsEvent(events: InvokeEvent[]): SessionStepsEvent | null {
  const event = events.find((e) => e.type === 'session_steps');
  return event ? (event as SessionStepsEvent) : null;
}

/** The first `plans` event, or null. */
export function findPlansEvent(events: InvokeEvent[]): PlansEvent | null {
  const event = events.find((e) => e.type === 'plans');
  return event ? (event as PlansEvent) : null;
}

/** The first `plan_steps` event, or null. */
export function findPlanStepsEvent(events: InvokeEvent[]): PlanStepsEvent | null {
  const event = events.find((e) => e.type === 'plan_steps');
  return event ? (event as PlanStepsEvent) : null;
}

/** The message from the first `error` event, or null when the response carried none. */
export function findErrorMessage(events: InvokeEvent[]): string | null {
  const event = events.find((e) => e.type === 'error');
  if (!event) return null;
  const message = (event as ErrorEvent).message;
  return typeof message === 'string' && message !== '' ? message : null;
}
