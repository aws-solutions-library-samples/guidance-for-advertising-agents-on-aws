/**
 * Turning a recorded session into every phase's state.
 *
 * The shape is deliberately dull: pair the steps into exchanges, then walk the phase registry IN
 * REGISTRY ORDER and ask each phase's decoder for data, falling back to a classified absence. All of
 * it is pure, which is what lets the absence rules be asserted without mounting a component.
 *
 * Registry order, not arrival order. A seller that answers `list_creative_formats` before
 * `get_products` must not make Creative appear before Discover: the rail is an authored narrative
 * and its sequence is a design decision, not a race outcome.
 *
 * A phase has a decoder only once the unit that owns it lands. All nine phases (Bind, Plan,
 * Discover, Govern, Score, Report, Creative, Book, Activate) have one now (U1 + U2 + U4).
 */

import type { SessionMeta, SessionStep } from '../types';
import { classify } from './absence';
import { exchangesForTool, latestExchangeForTool, pairExchanges } from './exchanges';
import {
  decodeAccountStrip,
  decodeActivateMeters,
  decodeBindCard,
  decodeCreativeCards,
  decodeCreativeEvaluation,
  decodeExecutionCheckCard,
  decodeGovernanceCheckCard,
  decodeMediaBuyStrip,
  decodePlanCard,
  decodeReportOutcomeCard,
} from './governanceCards';
import { PHASES, phaseByKey, type PhaseDefinition, type PhaseKey } from './phases';
import { deriveDiscover, discoverNote } from './productGroups';
import { parseFanOut } from '../sellers';
import { othersNotice, selectFromOutput } from './sellerJourneys';
import { summaryChips, type PlanValues } from './summary';
import type {
  AnyPhaseState,
  DeriveContext,
  DiscoverData,
  MediaBuyStripData,
  Meter,
  PhaseState,
  PhaseStates,
  SellerJourney,
  SummaryChip,
  ToolExchange,
} from './types';

/**
 * A decoder turns a successful exchange into a phase's data, or returns null to mean "this result
 * carried nothing this panel can show" — which the caller reports as an absence rather than as an
 * empty success.
 *
 * `allExchanges` is every exchange in the session, for the few decoders that need a SECOND tool's
 * result to build their own phase's data (Creative needs the evaluator's separate
 * `get_creative_features` exchange; Activate needs Plan's submitted budget). Most decoders ignore it.
 */
type Decoder = (
  exchange: ToolExchange,
  context: DeriveContext,
  allExchanges: readonly ToolExchange[],
) => unknown | null;

/** The Discover panel: real products from a real fan-out. */
const decodeDiscover: Decoder = (exchange) => {
  const output = exchange.result?.output;
  const data = deriveDiscover(output);
  if (!data) return null;
  // A fan-out where every seller responded with an empty list is an answer: the sellers were
  // asked and have nothing matching. That is `ready` with no groups, not an absence.
  return data;
};

/**
 * The Accounts panel: the advertiser account each seller now holds.
 *
 * The prerequisite for Bind — `sync_governance` answers `ACCOUNT_NOT_FOUND` without it — and the first
 * recorded call in every real session.
 */
const decodeAccounts: Decoder = (exchange) => decodeAccountStrip(exchange);

/** The Bind panel: which governance agent a seller now holds against which account. */
const decodeBind: Decoder = (exchange) => decodeBindCard(exchange);

/** The Plan panel: the plan the governance agent registered, and the terms it was given. */
const decodePlan: Decoder = (exchange) => decodePlanCard(exchange);

/** The Govern panel: the buyer's own intent `check_governance` response. */
const decodeGovernance: Decoder = (exchange) => decodeGovernanceCheckCard(exchange);

/**
 * The Score panel: the SELLER's execution check, read out of the governance agent's audit log.
 *
 * A different decoder from Govern's, reading a different tool's result. They shared one, over one
 * tool, separated only by which exchange was picked — which made them the same panel twice whenever
 * the session held a single check. See `decodeExecutionCheckCard`.
 */
const decodeExecutionGovernance: Decoder = (exchange) => decodeExecutionCheckCard(exchange);

/** The Report panel: what `report_plan_outcome` actually confirmed. */
const decodeReport: Decoder = (exchange) => decodeReportOutcomeCard(exchange);

/**
 * The format a `get_creative_features` exchange was actually about, read from its own request.
 *
 * The evaluated format used to be the literal `'display_300x250'`, on the reasoning that it is the only
 * format with a published fixture asset. True today, and still the wrong thing to hardcode: the verdict
 * would attach to that card no matter which format was really submitted, so evaluating any other format
 * would either lose the verdict or file it against the wrong asset. The request names the format, so read
 * it.
 *
 * Returns null when the request does not name one. The caller then attaches no verdict at all, which is
 * the honest outcome — a scan result whose subject is unknown cannot be shown against a card.
 */
export function evaluatedFormatId(exchange: ToolExchange): string | null {
  const input = exchange.input;
  if (input === null || typeof input !== 'object') return null;
  const formatId = (input as Record<string, unknown>)['format_id'];
  // The buyer's tool takes the format's own id as a string (`format_agent_url` is a separate argument),
  // but a manifest-shaped `{id, agent_url}` pair is read too, since that is the form AdCP uses for a
  // format reference elsewhere and a caller could reasonably pass it.
  if (typeof formatId === 'string' && formatId.length > 0) return formatId;
  if (formatId !== null && typeof formatId === 'object') {
    const id = (formatId as Record<string, unknown>)['id'];
    if (typeof id === 'string' && id.length > 0) return id;
  }
  return null;
}

/**
 * The Creative panel: one card per (seller, format) from `list_creative_formats`, with a real verdict
 * attached to whichever format a `get_creative_features` exchange in this session actually evaluated.
 *
 * Only that format's card gets a verdict. Its siblings show "not evaluated" rather than borrowing one,
 * because the evaluator measured one specific asset and says nothing about the others.
 *
 * No decoder is registered for `evaluatorToolName` itself — `get_creative_features` supplies a
 * VERDICT for Creative's own cards, it does not feed a separate phase (see phases.ts's own comment
 * on why the evaluator is a distinct binding field, not a phase).
 */
const decodeCreative: Decoder = (exchange, _context, allExchanges) => {
  // Last, not first: a session that re-evaluates after fixing an asset should show the latest verdict.
  const evaluatorExchanges = allExchanges.filter(
    (candidate) =>
      candidate.toolName.toLowerCase().includes('get_creative_features') &&
      candidate.result?.status === 'success',
  );
  const evaluatorExchange = evaluatorExchanges[evaluatorExchanges.length - 1];
  const formatId = evaluatorExchange ? evaluatedFormatId(evaluatorExchange) : null;
  const evaluation =
    evaluatorExchange !== undefined && formatId !== null
      ? decodeCreativeEvaluation(evaluatorExchange, formatId)
      : null;
  return decodeCreativeCards(exchange, evaluation);
};

/**
 * The Book panel: the media buy strip, built from every `create_media_buy` in the session.
 *
 * Fed the whole set rather than the single selected exchange, so a multi-product batch shows one card
 * per package and the strip does not blank between products while a later call is still in flight —
 * the card count does not depend on which call is in flight. `decodeMediaBuyStrip` keeps the honesty
 * rules the previous card/roster pair encoded (refusal before success fields, warning is not a
 * refusal, a missing id is reported as such); see its own header.
 */
const decodeBook: Decoder = (_exchange, _context, allExchanges) => {
  const purchases = exchangesForTool(allExchanges, 'adcp_create_media_buy');
  // Friendly package titles come from the session's own get_products answers (real seller catalogue
  // names). AdCP's Package has no name field, so a booked card would otherwise show the raw
  // product_id; this maps it back to the name the seller already returned during Discover.
  return decodeMediaBuyStrip(purchases, collectProductNames(allExchanges));
};

/**
 * product_id -> friendly name, harvested from every get_products fan-out in the session. First name
 * wins (products are stable within a session). Empty when Discover was never run or names are absent,
 * in which case the strip falls back to the product_id -- never an invented name.
 */
function collectProductNames(allExchanges: readonly ToolExchange[]): Map<string, string> {
  const names = new Map<string, string>();
  for (const exchange of exchangesForTool(allExchanges, 'adcp_get_products')) {
    const entries = parseFanOut(exchange.result?.output);
    if (!entries) continue;
    for (const entry of entries) {
      const products = (entry.response as Record<string, unknown> | undefined)?.['products'];
      if (!Array.isArray(products)) continue;
      for (const product of products) {
        if (product === null || typeof product !== 'object') continue;
        const id = (product as Record<string, unknown>)['product_id'];
        const name = (product as Record<string, unknown>)['name'];
        if (typeof id === 'string' && typeof name === 'string' && name.trim() !== '' && !names.has(id)) {
          names.set(id, name);
        }
      }
    }
  }
  return names;
}

/**
 * The Activate panel: real spend/pacing meters from `get_media_buy_delivery`, plus the plan's own
 * submitted budget (read from the Plan exchange's recorded REQUEST, same provenance
 * `decodePlanCard` already uses) so the spend meter has a real denominator.
 */
const decodeActivate: Decoder = (exchange, _context, allExchanges) => {
  const planExchange = allExchanges.find(
    (candidate) =>
      candidate.toolName.toLowerCase().includes('sync_plans') &&
      candidate.result?.status === 'success',
  );
  const input =
    planExchange && typeof planExchange.input === 'object' && planExchange.input !== null
      ? (planExchange.input as Record<string, unknown>)
      : null;
  const rawBudget = input?.['budget_total'];
  const planBudget = typeof rawBudget === 'number' && Number.isFinite(rawBudget) ? rawBudget : null;
  return decodeActivateMeters(exchange, planBudget);
};

/**
 * Decoders by phase.
 *
 * Bind, Plan, Discover, Govern, Score, Report, Creative, Book and Activate are all real in this
 * build (U1/U2/U4).
 */
const DECODERS: Partial<Record<PhaseKey, Decoder>> = {
  accounts: decodeAccounts,
  bind: decodeBind,
  plan: decodePlan,
  discover: decodeDiscover,
  govern: decodeGovernance,
  score: decodeExecutionGovernance,
  report: decodeReport,
  creative: decodeCreative,
  book: decodeBook,
  activate: decodeActivate,
};

/**
 * Whether a recorded `check_governance` call is an INTENT check.
 *
 * AdCP infers the check type from the fields present: `tool` + `payload` is an orchestrator's intent
 * check, `governance_context` + `planned_delivery` is a seller's execution check. This reads the
 * recorded request rather than the response, because that is where the discriminating fields are.
 *
 * The buyer's tool takes the task as `tool_name` and sends it on the wire as `tool`, so both names
 * are accepted — a session recorded from the wire and one recorded from the tool arguments must
 * classify the same way.
 *
 * Shape, not position. Position is what the two governance panels used to partition on, and it does
 * not distinguish anything: two intent checks in a session are still two intent checks.
 */
function isIntentCheck(exchange: ToolExchange): boolean {
  const input = exchange.input;
  if (typeof input !== 'object' || input === null) return false;
  const record = input as Record<string, unknown>;
  const task = record['tool_name'] ?? record['tool'];
  return typeof task === 'string' && task.trim().length > 0;
}

/** The task an intent check was made about, e.g. `create_media_buy`. */
function checkedTask(exchange: ToolExchange): string | null {
  const input = exchange.input as Record<string, unknown> | null;
  const task = input?.['tool_name'] ?? input?.['tool'];
  return typeof task === 'string' && task.trim().length > 0 ? task.trim() : null;
}

/**
 * The exchange that feeds a phase.
 *
 * Only Govern carries a `selector`, and it uses one because a governed session makes SEVERAL intent
 * checks — one before discovery, one before the buy — and the panel shows one of them. Everything
 * else takes the latest exchange for its tool: a session may legitimately call `get_products` more
 * than once, and the panel shows the most recent answer.
 *
 * Score no longer selects here at all. It is bound to a different tool (`get_plan_audit_logs`) and
 * its own decoder does the partitioning, on AdCP's `check_type` discriminator. While both governance
 * phases shared `check_governance` and were separated by picking first-versus-last exchange, a
 * single-check session gave both the same exchange and rendered one result set twice under two
 * different party attributions.
 */
export function exchangeForPhase(
  phase: PhaseDefinition,
  exchanges: readonly ToolExchange[],
): ToolExchange | undefined {
  const selector = phase.binding.selector;
  if (selector === undefined) {
    return latestExchangeForTool(exchanges, phase.binding.toolName);
  }

  if (selector === 'latest_settled') {
    const matching = exchangesForTool(exchanges, phase.binding.toolName);
    const settled = matching.filter(
      (candidate) => candidate.result !== undefined && candidate.result.status === 'success',
    );
    // Falls back to the latest exchange when nothing has settled yet, so a first call still in
    // flight classifies as in-flight rather than as "never called" — two different absences, and
    // `absence.ts` words them differently.
    return settled[settled.length - 1] ?? matching[matching.length - 1];
  }

  // Intent checks only, whichever strategy. An execution check cannot appear among these — the
  // seller makes that call, not the buyer — but filtering on shape rather than assuming it keeps
  // this correct if a session ever records one (e.g. a seller-recorded session replayed here).
  const candidates = exchangesForTool(exchanges, phase.binding.toolName).filter(isIntentCheck);

  if (selector === 'purchase_intent_only') {
    const purchases = candidates.filter((candidate) => checkedTask(candidate) === 'create_media_buy');
    return purchases[purchases.length - 1];
  }

  // `latest_intent`: the most recent one, so a discovery-only session shows its discovery check and
  // a booking session shows the purchase check.
  return candidates[candidates.length - 1];
}

/** Every phase's state, in registry order. */
export function derivePhases(
  steps: readonly SessionStep[],
  context: DeriveContext,
): PhaseStates {
  const exchanges = pairExchanges(steps);
  const states = new Map<PhaseKey, AnyPhaseState>();

  for (const phase of PHASES) {
    const exchange = exchangeForPhase(phase, exchanges);
    const decoder = DECODERS[phase.key];

    if (
      decoder !== undefined &&
      exchange !== undefined &&
      exchange.result !== undefined &&
      exchange.result.status === 'success'
    ) {
      const data = decoder(exchange, context, exchanges);
      if (data !== null && data !== undefined) {
        const state: PhaseState<unknown> = {
          state: 'ready',
          data,
          ...(exchange.truncation ? { truncation: exchange.truncation } : {}),
        };
        states.set(phase.key, state);
        continue;
      }
    }

    // No decoder, no exchange, an in-flight call, a failure, or a payload the decoder could not use.
    // `classify` decides which absence that is, and never returns a positive state.
    states.set(phase.key, classify(phase, exchange, context.capabilities));
  }

  return states;
}

/** Phase keys whose state is `ready`, in registry order. */
export function readyPhases(states: PhaseStates): PhaseKey[] {
  return PHASES.filter((phase) => states.get(phase.key)?.state === 'ready').map((p) => p.key);
}

/**
 * Entity chips contributed by phases that are ready.
 *
 * Built from real decoded data only. A phase with an entity binding but no usable figure contributes
 * nothing, which is why this returns a list rather than one chip per binding.
 */
export function entityChipsFor(states: PhaseStates): SummaryChip[] {
  const chips: SummaryChip[] = [];

  for (const phase of PHASES) {
    const state = states.get(phase.key);
    if (!state || state.state !== 'ready') continue;
    const bindings = phase.binding.entities;
    if (!bindings) continue;

    if (phase.key === 'discover') {
      const data = state.data as DiscoverData;
      const total = data.groups.reduce((sum, group) => sum + group.count, 0);
      const binding = bindings[0];
      if (binding) {
        chips.push({
          key: binding.key,
          label: binding.label,
          value: discoverNote(data),
          kind: 'entity',
          source: 'tool_result',
          ...(binding.live === true ? { live: true } : {}),
        });
      }
      // `total` is reported inside discoverNote; kept here as the derived figure the note is built
      // from, so a future chip needing the bare count has a real source.
      void total;
    }

    // Book's `status` entity chip: read from the strip's own `statusSummary` rather than re-decoding
    // the raw exchange, so the chip cannot disagree with what the panel shows. For a single buy that
    // is the buy's status (`active`, or `refused by seller`); for several it is a count of how many
    // booked. Emitted only when the strip produced a value, so a session with nothing usable adds no
    // chip.
    if (phase.key === 'book') {
      const strip = state.data as MediaBuyStripData;
      const binding = bindings.find((candidate) => candidate.key === 'status');
      if (binding && strip.statusSummary !== null) {
        chips.push({
          key: binding.key,
          label: binding.label,
          value: strip.statusSummary,
          kind: 'entity',
          source: 'tool_result',
          ...(binding.live === true ? { live: true } : {}),
        });
      }
    }

    if (phase.key === 'activate') {
      const meters = state.data as readonly Meter[];
      for (const binding of bindings) {
        // `status` has no meter of its own on this phase (delivery status is a lifecycle label, not
        // a fillable fraction) -- it is genuinely absent from this build's Activate chips until a
        // real per-media-buy status read exists; only `spend`, which IS a real meter, contributes.
        const meter = meters.find((candidate) => candidate.name === binding.key);
        if (!meter) continue;
        chips.push({
          key: binding.key,
          label: binding.label,
          value: meter.text,
          kind: 'entity',
          source: 'tool_result',
          ...(binding.live === true ? { live: true } : {}),
        });
      }
    }
  }

  return chips;
}

export interface JourneyDerivation {
  readonly states: PhaseStates;
  readonly chips: readonly SummaryChip[];
  /** The seller whose journey is displayed, or null when the session names none. */
  readonly shownSeller: SellerJourney | null;
  readonly otherSellers: readonly SellerJourney[];
  /** Notice text for the other sellers, or null when there are none. */
  readonly othersNotice: string | null;
}

/**
 * Everything the view needs from a session, derived in one pass.
 *
 * Memoised by the caller on step identity. Full re-derivation rather than incremental: incremental
 * would need its own applied-step bookkeeping, a second high-water mark with a second chance to be
 * wrong, and full derivation over a bounded step list is well inside one frame.
 */
export function deriveJourney(
  steps: readonly SessionStep[],
  meta: SessionMeta | null,
  context: DeriveContext,
  plan: PlanValues | null = null,
): JourneyDerivation {
  const states = derivePhases(steps, context);

  // Seller selection comes from the discovery fan-out, which is the first call that names every
  // seller in the session.
  const exchanges = pairExchanges(steps);
  // Taken from the registry rather than written out again. This line held a second copy of the tool
  // name, and it was wrong in the same way the registry was — so seller selection silently found
  // nothing even once the phase binding was corrected. One definition, one thing to get right.
  const discoveryTool = phaseByKey('discover')?.binding.toolName ?? '';
  const discovery = latestExchangeForTool(exchanges, discoveryTool);
  const selection = selectFromOutput(discovery?.result?.output, context.sellerPriority);

  return {
    states,
    chips: summaryChips(steps, meta, plan, entityChipsFor(states)),
    shownSeller: selection.shown,
    otherSellers: selection.others,
    othersNotice: othersNotice(selection.others),
  };
}
