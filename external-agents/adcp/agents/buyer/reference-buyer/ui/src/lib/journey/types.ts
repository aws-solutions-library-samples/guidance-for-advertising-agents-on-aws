/**
 * The journey's own types.
 *
 * The important one is `PhaseState`. It is a four-variant union rather than "data or null" because
 * three kinds of absence have to stay distinguishable:
 *
 *   not_reached      this session has not performed the call yet
 *   not_implemented  this build cannot perform the call at all
 *   unreachable      the call was made and the agent could not be reached
 *
 * Collapsing those into `null` would make a governance outage and an unbuilt feature look identical
 * on screen. For the person who stops at a booth for thirty seconds and cannot ask anyone what a
 * dark panel means, that difference is the whole of what the screen communicates.
 *
 * The union also makes the compiler enforce exhaustive rendering, so adding a fifth state later is
 * a build error at every render site rather than a silent fallthrough to whichever branch was last.
 */

import type { ProductFact, ReachInfo, SimilarityBar } from '../products';
import type { PhaseKey } from './phases';

/** What the recorder kept versus what the agent actually sent. */
export interface TruncationNote {
  /** Entries kept, when a list was shortened. */
  readonly kept?: number;
  /** Entries the agent sent. */
  readonly total?: number;
  /** Characters kept, when a string was shortened. */
  readonly keptChars?: number;
  /** Characters the agent sent. */
  readonly totalChars?: number;
  /** The recorder's own reason, when it gave one. */
  readonly reason?: string;
  /** True when the step could not be stored at all. */
  readonly notRecorded?: boolean;
}

export type PhaseState<T> =
  | { readonly state: 'ready'; readonly data: T; readonly truncation?: TruncationNote }
  | { readonly state: 'not_reached' }
  | { readonly state: 'not_implemented'; readonly detail: string }
  | { readonly state: 'unreachable'; readonly detail: string };

/** Any phase's state, for code that handles them uniformly. */
export type AnyPhaseState = PhaseState<unknown>;

/**
 * Exhaustiveness guard.
 *
 * Reached only if a `PhaseState` variant is added without updating a consumer, in which case the
 * compiler has already failed the build. Throwing rather than returning a default, since any
 * default would be a state no caller asked for.
 */
export function assertNever(value: never): never {
  throw new Error(`Unhandled variant: ${JSON.stringify(value)}`);
}

/** True when a state carries data. Narrows for callers. */
export function isReady<T>(
  state: PhaseState<T>,
): state is { readonly state: 'ready'; readonly data: T; readonly truncation?: TruncationNote } {
  return state.state === 'ready';
}

/**
 * A matched `tool_call` / `tool_result` pair from a recorded session.
 *
 * `result` being undefined means the call is IN FLIGHT, not that it failed. A five-seller fan-out
 * routinely takes several seconds, so treating a missing result as an error would flash a failure on
 * the way to every success.
 */
export interface ToolExchange {
  readonly callId: string;
  readonly toolName: string;
  readonly input: unknown;
  readonly callIndex: number;
  readonly result?: {
    readonly output: unknown;
    readonly status: 'success' | 'error';
    readonly durationMs?: number;
    readonly index: number;
  };
  readonly truncation?: TruncationNote;
}

/** One seller's entry in a fan-out, reduced to what the journey needs. */
export interface SellerJourney {
  readonly sellerId: string;
  readonly sellerName: string;
  /** Position from the configured priority list. Unlisted sellers sort last. */
  readonly rank: number;
  readonly outcome: 'responded' | 'errored';
  /** Factual precis, or empty when the shape was not recognised. Never a guess. */
  readonly summary: string;
}

/**
 * One product in a format group, as the Discover panel labels it.
 *
 * Every field here is something a seller actually sent, and an absent field is absent from the
 * object rather than present-and-empty. A row therefore shows a price only when the seller priced
 * the product; it never shows `0`, a dash, or "unpriced", any of which would read as a measured
 * figure. Same rule the product cards already follow — see lib/products.ts.
 */
export interface FormatGroupProduct {
  /** React key. Seller-prefixed, so two sellers returning the same product_id stay distinct. */
  readonly key: string;
  /** The product's own name, falling back to its id. */
  readonly title: string;
  /**
   * The seller that returned this product.
   *
   * Not optional, and carried per product rather than only per group: a group can hold products
   * from several sellers, and a row without attribution invites the reader to assume it came from
   * whichever seller they last read about.
   */
  readonly seller: string;
  /** The format id exactly as the seller sent it. Absent when it sent none. */
  readonly formatId?: string;
  /** The seller's rate, already formatted. Absent when the product carried no usable pricing. */
  readonly price?: string;
  /**
   * What the seller said about this product, beyond its name and price.
   *
   * Placement, edition, dimensions, duration, market, genre — whichever of them it stated. This is
   * what separates two rows of the same format, and without it ten products that differ only by
   * placement and edition render as ten identical rows. Absent, not empty, when the seller stated
   * none. See `lib/products.ts::productFacts` for which AdCP fields each fact comes from.
   */
  readonly facts?: readonly ProductFact[];
  /** The seller's reach figure and what it counts. Absent when it sent none. */
  readonly reach?: ReachInfo;
  /**
   * The score the seller ranked this product by, as returned.
   *
   * `fused_score`, or the content axis for a single-axis answer. This is what the constellation
   * positions by, and a product without it cannot be placed at all. Never `relevance_score`, which is
   * 1.0 on everything the filter returns.
   */
  readonly relevance?: number;
  /** The quoted rate as a number, for scales. `price` is the same figure already formatted. */
  readonly cpm?: number;
  readonly currency?: string;
  /**
   * The dual-vector relevance scores, per axis.
   *
   * Absent when the seller returned none. Never merged into one number: which axis is strong is the
   * part worth seeing.
   */
  readonly similarity?: readonly SimilarityBar[];
}

/** A format group in the Discover panel. */
export interface FormatGroup {
  readonly formatKey: string;
  readonly label: string;
  readonly cssVar: string;
  /** The products themselves, in the order the sellers returned them. */
  readonly products: readonly FormatGroupProduct[];
  /** Always `products.length`. Derived in the deriver, never supplied, so the two cannot diverge. */
  readonly count: number;
  /** Which sellers contributed, in fan-out order. */
  readonly attribution: readonly string[];
  readonly truncation?: TruncationNote;
}

/** The Discover panel's data. */
export interface DiscoverData {
  readonly groups: readonly FormatGroup[];
  /** Products whose format matched none of the authored groups. Reported, never dropped. */
  readonly unmatched: number;
  readonly sellersQueried: number;
  readonly sellersResponded: number;
  readonly sellersErrored: number;
}

/** Where a summary chip's value came from. Inspectable, so a wrong-source bug is findable. */
export type ChipSource =
  /** The brief the AGENT sent to sellers, read from a recorded `adcp_get_products` call. */
  | 'agent_brief'
  | 'request_text'
  | 'request_preview'
  | 'plan'
  | 'tool_result';

export interface SummaryChip {
  readonly key: string;
  readonly label: string;
  readonly value: string;
  readonly kind: 'brief' | 'entity';
  readonly source: ChipSource;
  readonly live?: boolean;
}

/** One arc of a radial panel. */
export interface Arc {
  readonly categoryId: string;
  /**
   * The finding's confidence, or null when it reported none.
   *
   * Null draws NO bar. It does not draw a bar of length zero: a zero-length arc reads as a measured
   * zero-confidence finding, which is a different claim from "this finding carried no confidence".
   */
  readonly confidence: number | null;
  readonly severity: string;
  readonly explanation: string;
  readonly radius: number;
  readonly colour: string;
}

/**
 * One seller's governance binding, for the Bind flow.
 *
 * Every field is optional except the seller, because a seller may return a partial account. An absent
 * field renders nothing rather than a placeholder.
 */
export interface BindLink {
  /** Seller id, or null when the fan-out named none. Used to label rows when several sellers bound. */
  readonly sellerId: string | null;
  /** The account's natural key as the seller holds it, e.g. "acme.example / acme-media". */
  readonly account: string | null;
  /** The seller's own word for the binding, e.g. "synced". Never defaulted. */
  readonly status: string | null;
  /** The governance agent, shortened to its identifying part for display. */
  readonly agent: string | null;
}

export interface BindData {
  readonly links: readonly BindLink[];
}

/**
 * A `check_governance` response, for the Govern and Score panels.
 *
 * Both panels use this shape and RadialBody -- see that component's header for why. `arcs` may be
 * empty: a healthy `approved` verdict often carries no findings at all (BR-U2-19), which is a normal
 * render, not an error.
 */
export interface GovernanceCheckData {
  readonly arcs: readonly Arc[];
  readonly verdict: {
    readonly count: number;
    readonly countSuffix: string;
    readonly title: string;
    readonly sub: string;
    readonly pill: string;
    readonly modeNote: string | null;
  };
  /**
   * Human escalation, not a verdict (BR-U2-23). When set, the caller renders a distinct pending state
   * instead of the arcs/verdict above.
   *
   * **Three independent signals set this**, because a governance agent can report the same situation in
   * three different places and a reader that watched only one would show an approved-looking panel for a
   * frozen plan:
   *
   *   1. an envelope `status` of `submitted` / `working` -- the review is in progress;
   *   2. a `CAMPAIGN_SUSPENDED` error, on either error layer -- the plan is frozen pending review;
   *   3. an unresolved escalation in a `get_plan_audit_logs` read -- the durable record of (2).
   *
   * Signal 3 is the one that survives a page reload: (1) and (2) are properties of a single response,
   * while the audit log is queryable at any time.
   */
  readonly awaitingHumanReview: boolean;
  /**
   * What is known about the review, when anything is. Null when the agent reported none -- the panel
   * then says only that review is pending, rather than inventing a cause for it.
   */
  readonly humanReview: HumanReviewNote | null;
  /**
   * The `conditions[]` the agent supplied, classified. Null when the response carried no array at all --
   * which a `conditions` verdict legitimately can, and which must render as the verdict alone rather
   * than as "no conditions". See `conditions.ts`.
   *
   * Always null on an audit-log entry: AdCP's audit `Entry` has no `conditions` field, so an entry can
   * report the verdict but not what would have satisfied it.
   */
  readonly conditions: ConditionSet | null;
}

/**
 * One parsed `check_governance` condition. See `conditions.ts` for the classification rules.
 */
export interface ConditionItem {
  /** The dot-path the agent named, as given. Null when it reported only a reason. */
  readonly field: string | null;
  /** Why the condition exists. Null when the agent named only a field. */
  readonly reason: string | null;
  /** The value that would pass, when the agent named one. `0` and `false` are real values. */
  readonly requiredValue: unknown;
  /** Whether `required_value` was present. Distinct from `requiredValue !== null`, since `null` is
   * itself indistinguishable from absent on the wire. */
  readonly hasRequiredValue: boolean;
  /** `action` = about the buyer's proposed action; `delivery` = about the seller's planned delivery. */
  readonly scope: 'action' | 'delivery';
  /** True only when the buyer both owns the field and was given a value to set. */
  readonly buyerActionable: boolean;
  /** Which journey panel the condition belongs to. */
  readonly panel: 'govern' | 'score';
}

/** A parsed `conditions[]` array. Absent-vs-empty is the caller's to distinguish; see `parseConditions`. */
export interface ConditionSet {
  readonly items: readonly ConditionItem[];
  readonly buyerActionableCount: number;
}

/**
 * The detail behind `awaitingHumanReview`.
 *
 * Every field is independently nullable because the three signals carry different amounts: an envelope
 * `status` alone carries nothing, a `CAMPAIGN_SUSPENDED` error carries a message and the SDK's own
 * `suggestion`, and an audit-log escalation carries the agent's `reason` plus -- once a human has
 * answered -- the `resolution` and its timestamp.
 */
export interface HumanReviewNote {
  /** The agent's stated cause. From an escalation's `reason` or an error's `message`. */
  readonly reason: string | null;
  /**
   * The SDK's own recovery hint, from `errors[].suggestion`. Passed through rather than authored here:
   * the governance agent reads it from AdCP's error manifest, so this is the protocol's advice, not the
   * UI's guess at it.
   */
  readonly suggestion: string | null;
  /** A human's recorded decision, once one exists. Null while the escalation is outstanding. */
  readonly resolution: string | null;
  /** ISO 8601, as reported. Not reformatted -- a rendered date is the agent's value or nothing. */
  readonly resolvedAt: string | null;
}

// report_plan_outcome decodes to `readonly CardRow[]` directly (Report's panel type is `card`,
// matching Book's existing dispatch convention) -- no separate wrapper type needed.

/**
 * A registered campaign plan, for the Plan panel.
 *
 * `budget`, `flightStart`, `flightEnd` and `channels` come from the recorded REQUEST: the governance
 * agent's response does not echo them. They are real, but they are what was submitted rather than what
 * the agent confirmed, which is why the panel labels them so.
 */
export interface PlanData {
  readonly planId: string | null;
  readonly status: string | null;
  readonly version: string | null;
  /** Formatted amount without the currency, e.g. "100,000". Null when no figure was sent. */
  readonly budgetAmount: string | null;
  readonly currency: string | null;
  /** ISO dates, or null. Both must be present for the window to render. */
  readonly flightStart: string | null;
  readonly flightEnd: string | null;
  readonly channels: readonly string[];
  /** Categories the agent said it will evaluate. NOT a verdict. */
  readonly willEvaluate: readonly string[];
  /**
   * Resolved policies. `[]` is an answer (the agent resolved none) and is reported as such;
   * `null` means the field was absent, which is not an answer.
   */
  readonly resolvedPolicies: readonly string[] | null;
}

/** A row of a card panel. A row whose value is absent is not produced at all. */
export interface CardRow {
  readonly key: string;
  readonly value: string;
  readonly mono?: boolean;
  readonly live?: boolean;
}

/**
 * What became of one `create_media_buy` attempt. Exactly one of these describes a card.
 *
 *   booked      the seller returned a `media_buy_id`
 *   no_id       the call succeeded but the seller returned no id and no error — reported as it stands,
 *               never dressed up as booked (the defect that once showed a refusal as a sale)
 *   refused     the seller declined with a non-`warning` `errors[]` entry
 *   in_flight   the call was made and the seller has not answered yet
 *   call_failed the call itself errored, or returned nothing the panel can read
 */
export type BuyOutcome = 'booked' | 'no_id' | 'refused' | 'in_flight' | 'call_failed';

/**
 * One package of a media buy, as the Media Buy strip renders it.
 *
 * Every field here is one the seller echoed on the `create_media_buy` response — the per-package
 * fields from AdCP's `Package` (`packages[]`), the media-buy-level fields (`media_buy_id`, status,
 * `confirmed_at`, `creative_deadline`, `revision`) from the response envelope. The same rule the rest
 * of this view follows holds: an absent field is absent from the object, never a dash or a zero, so a
 * package that carried no optimization goals renders no goals rather than an empty measurement.
 *
 * The detail-drawer sections are pre-formatted strings rather than raw objects, so the component
 * stays presentational and the extraction (which AdCP sub-field each line reads) is unit-tested here
 * in the lib rather than in a mounted component.
 */
export interface PackageCard {
  /** React key. Unique across the strip even when several buys omit ids. */
  readonly key: string;
  /** The product_id, else the package_id, else a positional label. Never invented. */
  readonly title: string;
  readonly productId: string | null;
  readonly packageId: string | null;
  /** The media buy this package belongs to. Shown per-card so multiple buys stay distinguishable. */
  readonly mediaBuyId: string | null;
  /** The media buy's own status, e.g. `active`. Never the envelope `completed` task status. */
  readonly status: string | null;
  readonly outcome: BuyOutcome;
  /** Present only on a `refused` card. The seller's own code, message and offending field. */
  readonly refusalCode: string | null;
  readonly refusalDetail: string | null;
  readonly refusalField: string | null;
  readonly budget: number | null;
  readonly currency: string | null;
  readonly bidPrice: number | null;
  readonly pricingOptionId: string | null;
  /** `even` / `asap` / `front_loaded` (AdCP's enum), or any other value a seller sent, verbatim. */
  readonly pacing: string | null;
  readonly impressions: number | null;
  readonly formats: readonly string[];
  readonly paused: boolean;
  /** ISO date portion of the flight window, or null. */
  readonly startTime: string | null;
  readonly endTime: string | null;
  readonly confirmedAt: string | null;
  readonly creativeDeadline: string | null;
  readonly revision: string | null;
  readonly agencyEstimate: string | null;
  /** Detail-drawer lines. An empty array is a section with nothing to say, and renders nothing. */
  readonly optimizationGoals: readonly string[];
  readonly targeting: readonly string[];
  readonly creativeAssignments: readonly string[];
  readonly measurement: readonly string[];
  readonly performanceStandards: readonly string[];
  readonly catalogs: readonly string[];
  /** The package's opaque `context` object, compacted to a mono string. Null when it carried none. */
  readonly context: string | null;
}

/**
 * The Media Buy panel's data: every package booked in the session, presented as one media buy.
 *
 * A session that books several products makes several `create_media_buy` calls in this build, but the
 * panel does not frame them as separate buys: every package is a card in one list, and the header
 * presents the summable facts across all of them — a budget (single currency only, else null), the
 * package count, the overall flight window. The per-buy identity (media_buy_id, confirmation time,
 * creative deadline) that cannot be summed lives on each card's own detail, so nothing is lost by not
 * distinguishing the buys at the top.
 */
export interface MediaBuyStripData {
  readonly cards: readonly PackageCard[];
  /** Sum of the booked packages' budgets, formatted, when they share a currency. Null otherwise. */
  readonly totalBudget: string | null;
  readonly currency: string | null;
  /** Booked packages, i.e. cards carrying real package data. */
  readonly packageCount: number;
  readonly flightStart: string | null;
  readonly flightEnd: string | null;
  /** Largest booked budget, for scaling the budget bars. `0` when nothing carried a budget. */
  readonly maxBudget: number;
  /** The value of the sticky-summary `status` chip, e.g. `active` or `2 of 3 booked`. */
  readonly statusSummary: string | null;
}

/**
 * What became of one seller's `sync_accounts` reply for the declared account.
 *   active  the seller holds the account (action created/updated/unchanged, and a real status)
 *   failed  the seller reported an `errors[]` entry, or `action: failed`
 */
export type AccountOutcome = 'active' | 'failed';

/**
 * One seller's advertiser-account record, as the Accounts strip renders it.
 *
 * `sync_accounts` fans out to every seller and each returns the SAME account — keyed on the
 * (brand domain, operator) pair the buyer declared — with its own `account_id`. So the strip is one
 * card per seller, and the shared brand/operator sits in the header once rather than on every card,
 * which is what a flat list turned into a stack of near-identical blocks. The honesty rules
 * `decodeAccountStrip` enforces mirror `decodeMediaBuyStrip`'s: a failed reply carries its error and
 * no `account_id`, never a success field standing in for one.
 */
export interface AccountCard {
  /** React key, unique across the strip. */
  readonly key: string;
  readonly seller: string;
  readonly outcome: AccountOutcome;
  /** `created`/`updated`/`unchanged`/`failed` — AdCP's `SyncAccountsAccount.action`, verbatim. */
  readonly action: string | null;
  /** `active`/`pending_approval`/`rejected`/`payment_required`/`suspended`/`closed`. Null on failure. */
  readonly status: string | null;
  /** The seller's own account id. Withheld on a failed reply, which has no id to show. */
  readonly accountId: string | null;
  /** Present only on a failed card: the seller's own error code and message. */
  readonly errorCode: string | null;
  readonly errorDetail: string | null;
}

/**
 * The Accounts panel's data: one card per seller that answered `sync_accounts`.
 *
 * `brand`/`operator` are the account's natural key and identical across sellers, so they are lifted
 * to the header. `sellerCount` is the number of distinct sellers that returned an account, shown as a
 * chip. Null (from the decoder) when nothing usable came back, which the caller renders as a
 * classified absence rather than an empty strip.
 */
export interface AccountStripData {
  readonly cards: readonly AccountCard[];
  readonly brand: string | null;
  readonly operator: string | null;
  readonly sellerCount: number;
}

/** A meter in the Activate panel. */
export interface Meter {
  readonly name: string;
  /** Fill fraction 0..1, or null when nothing real measures this meter. */
  readonly fraction: number | null;
  /** The figure as text, or the reason there is none. */
  readonly text: string;
}

/** Which build capabilities exist, for telling `not_implemented` from `not_reached`. */
export interface BuildCapabilities {
  /** Tool names this build can actually call. */
  readonly callableTools: readonly string[];
}

/** Everything derivation needs beyond the steps themselves. */
export interface DeriveContext {
  readonly capabilities: BuildCapabilities;
  /** Seller ids in display-priority order. Empty means no configured priority. */
  readonly sellerPriority: readonly string[];
}

/** The derived state of every phase, keyed by phase. */
export type PhaseStates = ReadonlyMap<PhaseKey, AnyPhaseState>;
