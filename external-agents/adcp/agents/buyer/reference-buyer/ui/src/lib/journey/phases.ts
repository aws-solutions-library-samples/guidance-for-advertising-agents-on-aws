/**
 * The journey's phase registry.
 *
 * This is the single ordered list of phases, and the only place phase identity is defined.
 * Everything downstream reads phases through here, which is what made the open questions about the
 * journey's shape one-file changes rather than refactors. All four are now answered:
 *
 *   O1 = A  Both setup phases go at the front; the authored six renumber behind them.
 *   O2 = A  `report_plan_outcome` earns its own phase, placed after Book. It is the step that
 *           commits spend, so the loop is only visible with it in.
 *   O3 = A  Govern selects the most recent buyer-side intent check (`latest_intent` below), so a
 *           discovery-only session shows a real discovery check instead of an empty panel.
 *   O4 = C  The `audience` chip is sourced from `get_signals`, and its value is expressed as
 *           COVERAGE. See the chip's binding on `govern` for why the wording is load-bearing.
 *
 * Two kinds of field live here and they must not be confused:
 *
 *   PRESENTATION — `n`, `name`, `party`, `op`, `col`, `glow`, `title`, `sub`, `type`. Copied
 *   verbatim from `.kiro/specs/journey.html`'s own `PHASES` array, including its punctuation.
 *   The visual design is fixed and reproduced exactly, so these strings are not edited, not
 *   reworded, and not brought into line with the house style guide. The two phases this project
 *   adds (`bind`, `plan`) are new copy and do follow that guide.
 *
 *   BINDING — `binding`. Says where a phase's data comes from. The prototype carried literal
 *   demo values inline (product counts, confidence percentages, a media_buy_id, a reach figure);
 *   none of those are here. They were placeholders standing in for recorded data, and carrying
 *   them across would put unmeasured numbers on a screen that reports measured ones.
 */

/** Stable phase identifiers. Never displayed. */
import { matchesToolName } from './toolNames';

export type PhaseKey =
  | 'accounts'
  | 'bind'
  | 'plan'
  | 'discover'
  | 'govern'
  | 'score'
  | 'creative'
  | 'book'
  | 'report'
  | 'activate';

/**
 * The prototype's five panel body types. Five, not one per phase: `govern` and `score` both use
 * `radial`, so one renderer serves both and the two governance panels cannot drift apart.
 */
export type PanelType =
  | 'fmtgrid'
  | 'radial'
  | 'scan'
  | 'card'
  | 'meters'
  /** Bind: a directed flow between the governance agent and the seller account. */
  | 'bindflow'
  /** Plan: a budget figure, a flight window and two chip sets. */
  | 'planviz'
  /** Book: a strip of the media buy's packages, each a budget bar with an expandable detail drawer. */
  | 'mediabuy'
  /** Accounts: a strip of per-seller account cards, one highlighted at a time (see MediaBuyStripBody). */
  | 'accounts';

/**
 * Which governance check feeds a panel.
 *
 * `latest_intent` is the answer to O3: the Govern panel shows the most recent buyer-side intent
 * check, so a discovery-only session shows a real discovery check and a booking session shows the
 * purchase check. `purchase_intent_only` is kept as a named alternative because the authored
 * subtitle says "before the buy is sent", and someone reading that later should find the option it
 * describes rather than assume it was never considered.
 *
 * O3 = A carries one obligation not yet met: the panel must NAME the tool that was checked, in the
 * role line, so a discovery check is not mistaken for a purchase check. That needs a decoded check
 * to name, so it lands with the buyer-governance unit and not before.
 *
 * ## There is no `execution` strategy, and there cannot be one on this tool
 *
 * An earlier version had `'execution'`, and Score used it to pick the LAST
 * `adcp_check_governance` exchange while Govern took the first. With one check in the session those
 * are the same exchange, so **both panels rendered the identical result set under two different
 * party attributions** — the buyer's own intent check appearing a second time captioned "Seller's
 * governance agent scores execution". A reader had no way to tell the two panels were one fact.
 *
 * The reason it cannot be repaired by picking a different exchange: AdCP infers the check type from
 * the fields present, and `tool` + `payload` (intent) is the only shape the buyer ever sends.
 * `governance_context` + `planned_delivery` (execution) is sent by the SELLER, from inside its own
 * `create_media_buy`, so it never appears in the buyer's recorded steps at all. No selector over
 * this tool's exchanges can find one. Score is fed by the audit-log read-back instead; see its
 * binding below.
 */
/**
 * `latest_settled` is Book's, and it exists to stop a panel blanking mid-loop.
 *
 * The default picks a tool's most recent exchange, and `derivePhases` only renders a panel whose
 * selected exchange already succeeded. Booking five products records five calls, so between each
 * call and its result the most recent exchange is an unresolved one — the decoder is skipped and the
 * panel reports an absence, even though earlier buys succeeded and are still recorded. Selecting the
 * most recent SETTLED exchange keeps the panel on data that exists; the in-flight calls are still
 * shown, as rows, by the roster decoder.
 */
export type SelectorStrategy = 'latest_intent' | 'purchase_intent_only' | 'latest_settled';

export interface EntityBinding {
  /** Chip key. Re-using a key updates that chip in place, as the prototype does. */
  readonly key: string;
  /** The chip's bold prefix. */
  readonly label: string;
  /** Whether the chip carries the pulsing live dot. */
  readonly live?: boolean;
  /**
   * The tool that measures this chip's value, when it is not the phase's own tool.
   *
   * Chips and phases are not the same thing. A chip's position in the sticky summary follows the
   * phase that reveals it, which is an authored design decision; where its NUMBER comes from is a
   * question of fact. The `audience` chip is the case that forced the distinction: the prototype
   * reveals it beside Govern, but a governance check does not measure audience coverage. Signals do.
   *
   * Defaults to the phase's `binding.toolName`.
   */
  readonly sourceToolName?: string;
}

export interface PhaseBinding {
  /**
   * The RECORDED tool name that supplies this phase, matched against a step's `content.toolName`.
   *
   * **This is the agent's local tool name, not the AdCP task name, and the two are not the same.**
   * The protocol task is `get_products`; the tool the buyer agent registers to perform it is
   * `adcp_get_products`. The task name lives on `op`, which is display copy; this field has to match
   * what the recorder actually wrote or the phase never finds its data.
   *
   * Getting this wrong is silent. The name simply never matches, the phase reports an absence with a
   * plausible reason, and the screen looks like a build that has not got there yet rather than one
   * looking for the wrong string. It happened to every binding in this file at once, because they
   * were written from the protocol's task list instead of from `adcp_tools.py`. `phases.tools.test.ts`
   * now reads the agent's real registrations and fails if a binding names something it does not
   * register and has not been declared as not-yet-built.
   *
   * A phase whose tool never appears in a session is `not_reached`; one whose tool this build cannot
   * call at all is `not_implemented`. Those are different sentences to say to someone at a booth,
   * which is why they are different states.
   */
  readonly toolName: string;
  /**
   * The AdCP task `toolName` actually performs, when it is not the one `op` names.
   *
   * `op` is the task that PRODUCED the fact a panel shows; `toolName` is how the buyer OBTAINED it.
   * For eight phases those are the same task and this field is absent. Score is the exception: the
   * finding it shows was produced by the seller's own execution `check_governance`, but the buyer
   * reads it back through `get_plan_audit_logs`, because a call the seller makes to the governance
   * agent is not in the buyer's session.
   *
   * Only absence messaging uses it, and that is the whole reason it exists: `absence.ts` writes
   * "<op> returned an error", so without this Score would report a failed audit-log read as
   * "check_governance returned an error" — naming a task that was never called on this path.
   */
  readonly sourceOp?: string;
  /** Which of a tool's several exchanges feeds this panel. Only Govern needs one. */
  readonly selector?: SelectorStrategy;
  /**
   * A second tool this phase reports on but is not supplied by.
   *
   * Creative is the case: its cards come from `list_creative_formats` (a seller task) while the
   * malicious-behaviour verdict its subtitle promises comes from `get_creative_features` (a
   * governance-agent task). Two parties, two tools, one authored panel.
   *
   * Held separately so the panel's DATA and its VERDICT cannot be conflated. Absent evaluator means no
   * verdict and no scan animation, which is the whole of S22.
   */
  readonly evaluatorToolName?: string;
  /** Entity chips this phase contributes to the sticky summary when it completes. */
  readonly entities?: readonly EntityBinding[];
}

export interface PhaseDefinition {
  readonly key: PhaseKey;
  /** Authored ordinal label. Display only. Registry position is the ordering authority. */
  readonly n: string;
  readonly name: string;
  readonly party: string;
  readonly op: string;
  readonly col: string;
  /** `r,g,b` triplet the ribbon retints with. Authored. */
  readonly glow: string;
  readonly title: string;
  readonly sub: string;
  readonly type: PanelType;
  readonly binding: PhaseBinding;
}

/**
 * Arc radii for a `radial` panel, outermost first, exactly as the prototype's `bars[].r`.
 *
 * Presentation, not data: the prototype's four arcs sat at these radii, and real findings are
 * assigned to them in order. A fifth finding has no authored radius, so the renderer shows the
 * first four and reports the remainder rather than adding a ring.
 */
export const RADIAL_RADII = [76, 60, 44, 28] as const;

/**
 * Arc colours in assignment order, from the prototype's own `bars[].col`.
 *
 * The prototype hard-coded a colour per named category. Real `category_id` values are opaque
 * agent-defined labels (AdCP is explicit that they must not be pattern-matched), so a colour is
 * assigned by position instead. Same palette, no assumption about what the categories will be.
 */
export const RADIAL_COLOURS = ['#8c2afc', '#006c24', '#f06c00', '#e4006c'] as const;

/** Segment count in a `meters` track, from the prototype's `NSEG`. */
export const METER_SEGMENTS = 34;

/** The authored pink-to-orange gradient endpoints for a meter track. */
export const METER_GRADIENT = {
  from: { r: 229, g: 64, b: 111 },
  to: { r: 255, g: 138, b: 61 },
} as const;

/** Format metadata, verbatim from the prototype's `FMT_META`. */
export const FORMAT_META = {
  audio: { label: 'Streaming audio', cssVar: '--audio' },
  podcast: { label: 'Podcast', cssVar: '--podcast' },
  display: { label: 'Companion display', cssVar: '--display' },
  video: { label: 'Video / pre-roll', cssVar: '--video' },
  endcard: { label: 'Interactive endcard', cssVar: '--endcard' },
} as const;

export type FormatKey = keyof typeof FORMAT_META;

export const FORMAT_KEYS = Object.keys(FORMAT_META) as readonly FormatKey[];

/**
 * The phases, in order.
 *
 * `bind`, `plan` and `report` are additions. The prototype's own six began at `get_products`, which
 * skipped the two AdCP setup tasks that must precede any governance check and the one that closes
 * the loop afterwards.
 *
 * ## Why Discover leads, and Bind/Plan follow it
 *
 * An earlier version put Bind and Plan at positions 0 and 1, ahead of everything authored, on the
 * reading that AdCP's minimal sequence starts with sync_governance and sync_plans. **That over-read
 * the dependency.** What AdCP actually requires is that governance is bound and a plan registered
 * before a seller is asked to *act* — before Book — not before it is asked to *read*.
 * `get_products` is a read: it takes a brand and a brief, not an account, and consults no governance
 * agent. Nothing about discovery depends on either setup task.
 *
 * Putting them first also misdescribed what happens. A session begins with a brief, and the buyer
 * discovers inventory before it has anything to govern; Bind and Plan land once there is a
 * commitment to make. Two panels sat at the head of the rail waiting on work that had not been
 * reached yet, which reads as the flow stalling at step one rather than as it not having got there.
 *
 * So Discover leads. The dependencies that ARE real still hold and are asserted in `phases.test.ts`:
 * bind before plan, plan before either governance check, and every check before Book.
 */
export const PHASES: readonly PhaseDefinition[] = [
  {
    key: 'discover',
    n: '01',
    name: 'Discover',
    party: 'seller',
    op: 'get_products',
    col: '#8c2afc',
    glow: '140,42,252',
    title: 'Offered products',
    sub: 'Seller surfaces available audio inventory via get_products',
    type: 'fmtgrid',
    binding: {
      toolName: 'adcp_get_products',
      entities: [{ key: 'products', label: 'products' }],
    },
  },
  {
    // NEW, and the first thing that actually happens in a governed session.
    //
    // `sync_accounts` was unpanelled, so the rail opened on Bind and read as though registering the
    // governance agent were the buyer's first act. It is not: every account-scoped task is keyed on the
    // (brand domain, operator) pair, and a seller that has never been told about the account answers
    // `ACCOUNT_NOT_FOUND` to `sync_governance`. It was the first recorded call in every real session.
    //
    // Placed after Discover rather than first: Discover is a read that needs no account (see the O1
    // tests), and the account is only needed once there is a commitment to make.
    key: 'accounts',
    n: '02',
    name: 'Accounts',
    party: 'buyer orchestrator',
    op: 'sync_accounts',
    col: '#00b3a4',
    glow: '0,179,164',
    title: 'Advertiser account declared',
    sub: 'Buyer declares the advertiser account at each seller, keyed on brand domain and operator',
    // A strip of per-seller cards rather than a flat row list: sync_accounts fans out to every
    // seller, each returning the same account with its own id, so a row list was a stack of
    // near-identical blocks. The strip shows the shared account key once and cycles the sellers.
    type: 'accounts',
    binding: { toolName: 'adcp_sync_accounts' },
  },
  {
    // NEW. Copy follows the house style guide, unlike the authored entries below.
    key: 'bind',
    n: '03',
    name: 'Bind',
    party: 'buyer orchestrator',
    op: 'sync_governance',
    col: '#80f0f8',
    glow: '128,240,248',
    title: 'Governance binding',
    sub: 'Buyer registers its governance agent against the seller account, before any check can run',
    type: 'bindflow',
    binding: { toolName: 'adcp_sync_governance' },
  },
  {
    // NEW.
    key: 'plan',
    n: '04',
    name: 'Plan',
    party: 'buyer orchestrator',
    op: 'sync_plans',
    col: '#8c8cfa',
    glow: '140,140,250',
    title: 'Campaign plan registered',
    sub: 'Budget, channels, flight dates and authorised markets pushed to the governance agent',
    type: 'planviz',
    binding: { toolName: 'adcp_sync_plans' },
  },
  {
    key: 'govern',
    n: '05',
    name: 'Govern',
    party: 'buyer orchestrator',
    op: 'check_governance',
    col: '#d200b6',
    glow: '210,0,182',
    title: 'Intent governance',
    sub: 'Buyer-side orchestrator validates intent before the buy is sent \u2014 signals weighed',
    type: 'radial',
    binding: {
      toolName: 'adcp_check_governance',
      selector: 'latest_intent',
      // O4 = C. The prototype's chip read "92% match (Clean Rooms ML)" — a brief-to-audience match
      // score, credited to a component that does not exist here. `get_signals` returns segment
      // COVERAGE and size, which measures a different thing.
      //
      // So the chip is bound, and its wording is part of the binding rather than decoration: the
      // decoder must express this value as coverage and must never render the word "match". A
      // coverage figure under a label saying "match" is worse than no chip, because the number
      // survives scrutiny and the claim does not. The one persona this view is built for cannot ask
      // which of the two it is looking at.
      entities: [{ key: 'audience', label: 'audience', sourceToolName: 'adcp_get_signals' }],
    },
  },
  {
    key: 'creative',
    n: '06',
    name: 'Creative',
    // `list_creative_formats` IS a seller task, so this attribution is correct — which it was not while
    // the phase named `get_creative_features`. That one is a GOVERNANCE-agent task: in the AdCP SDK it is
    // overridden with validation only on `GovernanceHandler`. Labelling a governance task as the seller's
    // put the wrong party's name against it.
    party: 'seller',
    // DELIBERATE DIVERGENCE from the prototype, which authored `get_creative_features` here.
    //
    // The panel shows one card per format each seller accepts, and that data comes from
    // `list_creative_formats` — which is what R21 always said. Keeping the authored `op` while showing
    // format data would put a task name against a panel that task did not supply: the same mislabelling
    // as a coverage figure captioned "match", and the role line is where a reader goes to check.
    //
    // The authored-string test exempts this one field by name, with this reason, so the divergence is
    // recorded rather than silently tolerated.
    op: 'list_creative_formats',
    col: '#f06c00',
    glow: '240,108,0',
    title: 'Creative scan',
    sub: 'sync_creatives, then malicious-behavior check + feature tagging on each asset',
    type: 'scan',
    binding: {
      toolName: 'adcp_list_creative_formats',
      // The evaluator, kept as its own field rather than as the phase's tool.
      //
      // Two different questions: "which formats does this seller accept" (a seller task) and "is
      // this creative malicious, and what features does it have" (a governance-agent task). The
      // authored subtitle describes the second.
      //
      // U4/Part 1: the evaluator IS now real, for the one format this build has fixture creative
      // content for (`display_300x250` -- see `fixtures/creatives/`). Every other format still gets
      // no verdict, honestly, because nothing was submitted for it to evaluate -- not because no
      // evaluator exists. `decodeCreativeCards`/`decodeCreativeEvaluation` in `governanceCards.ts`
      // own the exact per-format attachment logic.
      //
      // The scan must still NOT animate for a card with no verdict (S22) -- `ScanBody` enforces this
      // per-card, not per-phase, which is what lets one format show a real scan while its siblings
      // correctly show none.
      evaluatorToolName: 'adcp_get_creative_features',
    },
  },
  {
    key: 'book',
    n: '07',
    name: 'Book',
    party: 'seller',
    op: 'create_media_buy',
    col: '#006c24',
    glow: '0,108,36',
    // Seller-neutral on purpose. This read 'Booked at Triton', which named one seller in authored copy
    // for a panel whose seller varies per session -- so a buy confirmed by any other seller would have
    // been labelled as Triton's. That mattered more once a second audio seller existed. The seller is
    // already identified by the headline and the seller notice, so the panel title does not need to
    // name one; `headline.test.ts` now enforces that no demo seller name appears in source at all.
    //
    // Outcome-neutral for the same reason it is seller-neutral: 'Media buy booked' / 'accepted' is an
    // authored claim about a session-specific outcome. A seller that refuses returns `errors[]` and no
    // `media_buy_id`, so `decodeBookCard` renders `status: refused by seller` — under a heading that
    // said BOOKED. Found live with Poseidon. The rows say which way it went; the heading names the step.
    title: 'Media buy',
    sub: 'create_media_buy \u2014 the seller books the plan or refuses it',
    // A strip of package cards rather than the two-row `card`: create_media_buy echoes a full
    // `Package` per booked line, so the richer body shows what was actually booked without inventing
    // anything. The entity chip and absence handling are unchanged.
    type: 'mediabuy',
    binding: {
      toolName: 'adcp_create_media_buy',
      // See `SelectorStrategy`: without this, a multi-product booking loop blanks this panel between
      // every product, because the newest exchange is an unresolved call for as long as the seller
      // is answering it.
      selector: 'latest_settled',
      // U4/Part 1, Q3: `reach` was dropped. Neither `create_media_buy`'s response nor
      // `planned_delivery` carries a `reach` field in the AdCP 3.1 spec (confirmed directly against
      // the raw schema, including a direct grep of core/package.json) -- the prototype's authored
      // reach figure had no real source to bind to, and a product's pre-purchase
      // `reachable_audience` is a different, non-interchangeable number this response never echoes.
      entities: [{ key: 'status', label: 'status', live: true }],
    },
  },
  {
    key: 'score',
    n: '08',
    name: 'Score',
    // MOVED, from before Creative to after Book. It sat at 05, two slots ahead of the event it reports.
    //
    // The seller's execution check runs INSIDE `create_media_buy` — so it is a consequence of Book, not a
    // precursor to it. Rendering it earlier told the reader the seller's governance agent had scored the
    // execution before the buy was even sent, which inverts cause and effect.
    //
    // Proven from a real run's audit trail rather than argued: the intent check is stamped 21:46:45 and
    // the execution check 21:47:04, 19 seconds later, with `create_media_buy` in between. The buyer's own
    // `get_plan_audit_logs` — this panel's data source — was the LAST call in every session.
    //
    // Immediately after Book, not later: the check happens between submission and commit, and one panel
    // cannot split that. `phases.test.ts` previously asserted `score < book` under the heading "a buy
    // cannot precede its own governance" — true of Govern, which must produce the intent token first, and
    // false of Score.
    party: 'seller governance agent',
    op: 'check_governance',
    col: '#e4006c',
    glow: '228,0,108',
    title: 'Governance findings by category',
    sub: "Seller's governance agent scores execution \u2014 confidence per AdCP category",
    type: 'radial',
    binding: {
      // The SELLER's execution check, read back from the governance agent's audit log.
      //
      // Not `adcp_check_governance`, which is what this bound to and is why the two governance
      // panels showed the same result set twice. The seller's execution check is an outbound call
      // the seller makes from inside its own `create_media_buy` (see reference-seller's
      // `governance_verification.run_execution_check`), addressed to the governance agent. It is
      // real, and it is recorded — in the governance agent's audit trail, not in the buyer's
      // session. So the buyer reaches it the only way it can: by reading the log.
      //
      // The governance agent already stamps `entries[].check_type` as `intent` or `execution` for
      // exactly this purpose, and says so in its own comment on that line. This binding is what
      // finally consumes it. `decodeExecutionCheckCard` selects on that discriminator rather than
      // on position, so a session with no execution check reports an absence instead of borrowing
      // Govern's intent check.
      toolName: 'adcp_get_plan_audit_logs',
      sourceOp: 'get_plan_audit_logs',
    },
  },
  {
    // NEW, and the answer to O2. Copy follows the house style guide.
    //
    // Placed after Book rather than last, which is a claim about what this task reports and worth
    // being explicit about: `report_plan_outcome` confirms the PLAN's execution — that the buy was
    // placed, and for how much — which happens the moment the buy is confirmed. It is not a report
    // of delivery results, so it does not wait on Activate. That is also why it is the step that
    // moves budget: the governance agent decrements authority from confirmed outcomes, not from
    // approved checks, so a session that checks and books but never reports leaves the plan showing
    // authority it has actually spent.
    key: 'report',
    n: '09',
    name: 'Report',
    party: 'buyer orchestrator',
    op: 'report_plan_outcome',
    // The palette's blue, authored as `--video`. Reusing a format colour as a phase accent is how
    // the prototype already works: Discover takes `--audio`, Score `--podcast`, Creative
    // `--display`, Book `--green`. This is the one cool hue those six leave unused.
    col: '#2f6bff',
    glow: '47,107,255',
    title: 'Plan outcome reported',
    sub: 'Buyer confirms the executed buy back to the governance agent, committing spend against the plan',
    type: 'card',
    binding: {
      toolName: 'adcp_report_plan_outcome',
      // Keyed `committed`, deliberately not `spend`. Activate contributes a `spend` chip from
      // delivery metrics, and chips merge last-wins by key: sharing one would let delivered spend
      // silently replace committed spend under an unchanged label. They are different quantities and
      // a reader has no way to tell which one survived.
      entities: [{ key: 'committed', label: 'committed' }],
    },
  },
  {
    key: 'activate',
    n: '10',
    name: 'Activate',
    party: 'seller',
    op: 'get_media_buy_delivery',
    col: '#002a2a',
    glow: '0,42,42',
    title: 'In-flight delivery',
    sub: 'get_media_buy_delivery \u2014 live pacing, fill and drift against plan',
    type: 'meters',
    binding: {
      toolName: 'adcp_get_media_buy_delivery',
      entities: [
        { key: 'status', label: 'status', live: true },
        { key: 'spend', label: 'spend' },
      ],
    },
  },
];

const BY_KEY: ReadonlyMap<PhaseKey, PhaseDefinition> = new Map(
  PHASES.map((phase) => [phase.key, phase]),
);

export function phaseByKey(key: PhaseKey): PhaseDefinition | undefined {
  return BY_KEY.get(key);
}

/**
 * Phases fed by a given recorded tool name.
 *
 * Returns a list because `check_governance` legitimately feeds two panels, distinguished by their
 * `selector`. Callers pick using the entry's strategy rather than assuming a single match.
 */
export function phasesForTool(toolName: string): readonly PhaseDefinition[] {
  // Prefix-insensitive, matching `exchangesForTool`. A recorded name arrives either as the buyer's
  // `adcp_`-prefixed Strands registration or as the seller's unprefixed AdCP skill name, and this must
  // place both. Currently unused outside tests, fixed anyway so it cannot reintroduce the exact-match
  // bug the moment something starts calling it.
  return PHASES.filter((phase) => matchesToolName(toolName, phase.binding.toolName));
}

/** Registry position, which is the ordering authority. `-1` for an unknown key. */
export function phaseOrder(key: PhaseKey): number {
  return PHASES.findIndex((phase) => phase.key === key);
}

/**
 * Panel types that need a whole row of the flow to themselves.
 *
 * Keyed on the panel TYPE rather than the phase, so a new phase reusing an existing body inherits the
 * right width without a second decision. `fmtgrid` is the only one: it carries its own internal
 * two-column grid plus a 320px constellation, so at a third of the stage it would be a column of
 * wrapped text beside a squeezed graph. Every other body is a card that reads fine at column width —
 * the radials are 180px plus a 230px legend, and Bind, Plan, Book, Report and Activate are narrower
 * still.
 */
const WIDE_PANEL_TYPES: ReadonlySet<PanelType> = new Set<PanelType>(['fmtgrid']);

/** Whether a panel of this type spans the full width of the flow rather than sharing a row. */
export function isWidePanel(type: PanelType): boolean {
  return WIDE_PANEL_TYPES.has(type);
}

/** Every tool name the journey knows how to place. */
export const BOUND_TOOL_NAMES: readonly string[] = [
  ...new Set(PHASES.map((phase) => phase.binding.toolName)),
];
