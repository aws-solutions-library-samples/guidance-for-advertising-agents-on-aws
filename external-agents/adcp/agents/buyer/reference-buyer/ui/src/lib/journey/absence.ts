/**
 * Deciding which kind of absence a phase is in.
 *
 * Three reasons a phase has no data, and they must stay apart:
 *
 *   not_reached      the call has not been made in this session
 *   not_implemented  this build cannot make the call at all
 *   unreachable      the call was made and the agent could not be reached
 *
 * These are three different sentences to say to someone standing at a booth. "That part is not built
 * yet" and "the governance agent is unreachable right now" are not interchangeable, and the second
 * is an operational fact worth knowing during a demo.
 *
 * The ordering of the checks below is the substance of this module, and one rule governs it: the
 * RESIDUAL case is `unreachable`, never `not_reached`. An error nobody anticipated has to land on the
 * variant that admits something went wrong. Defaulting an unfamiliar error to `not_reached` would
 * instead state that the call was never made.
 */

import { sellerEntryFailed, parseFanOut } from '../sellers';
import { matchesToolName } from './toolNames';
import type { PhaseDefinition } from './phases';
import type { BuildCapabilities, PhaseState, ToolExchange } from './types';

/** Substrings that identify an unreachable agent rather than a rejected request. */
const UNREACHABLE_HINTS = [
  'governance_unavailable',
  'timeout',
  'timed out',
  'connectionerror',
  'connect',
  'econnrefused',
  'enotfound',
  'unreachable',
  'network',
  'gateway',
  'service unavailable',
];

function mentionsUnreachable(text: string): boolean {
  const haystack = text.toLowerCase();
  return UNREACHABLE_HINTS.some((hint) => haystack.includes(hint));
}

/** A short, real description of a failed result. Never a synthesised one. */
function describeFailure(output: unknown): string {
  if (typeof output === 'string' && output.trim().length > 0) return output.slice(0, 300);
  if (output === undefined || output === null) return 'The agent reported an error with no detail.';
  try {
    return JSON.stringify(output).slice(0, 300);
  } catch {
    return String(output).slice(0, 300);
  }
}

/**
 * Whether a fan-out result contains nothing usable.
 *
 * A fan-out where EVERY seller failed is reported as unreachable, since there is no data behind the
 * panel. A fan-out where some succeeded is `ready`: the panel shows what came back and reports the
 * failures alongside, because "two sellers could not be asked" and "two sellers have no inventory"
 * are different facts about those sellers' businesses.
 */
function fanOutWhollyFailed(output: unknown): { failed: boolean; detail: string } {
  const entries = parseFanOut(output);
  if (!entries || entries.length === 0) return { failed: false, detail: '' };
  const failures = entries.filter((entry) => sellerEntryFailed(entry));
  if (failures.length < entries.length) return { failed: false, detail: '' };
  return {
    failed: true,
    detail: `All ${entries.length} sales agent${entries.length === 1 ? '' : 's'} returned an error.`,
  };
}

/**
 * Classify a phase that has no data to show.
 *
 * Returns only absence variants. It never returns `ready`, because deciding that a phase HAS data is
 * the caller's job and a fallback to `ready` here would report data this function never saw.
 */
export function classify(
  phase: PhaseDefinition,
  exchange: ToolExchange | undefined,
  capabilities: BuildCapabilities,
): PhaseState<never> {
  // Prefix-insensitive for the same reason as exchange matching: `CALLABLE_TOOLS` lists the buyer's
  // own `adcp_`-prefixed registrations, but a phase may legitimately be bound to the unprefixed AdCP
  // task name that a seller-recorded session carries. Comparing raw strings here would report a tool
  // this build genuinely calls as "cannot happen in this build" — the more misleading of the two
  // wrong answers, since it states a capability limit rather than just showing nothing.
  const callable = capabilities.callableTools.some((tool) =>
    matchesToolName(tool, phase.binding.toolName),
  );

  if (!exchange) {
    // No call recorded. Either this build cannot make it, or it has not happened yet.
    if (!callable) {
      return {
        state: 'not_implemented',
        detail:
          `This build cannot call ${phase.binding.toolName}, so the ${phase.name} phase has no ` +
          'source yet. It is not a failure in this session.',
      };
    }
    return { state: 'not_reached' };
  }

  if (exchange.result === undefined) {
    // In flight. Not a failure: a fan-out across five sellers takes seconds.
    return { state: 'not_reached' };
  }

  if (exchange.truncation?.notRecorded === true) {
    return {
      state: 'unreachable',
      detail:
        'The result was too large to record, so what the agent returned is not available to show. ' +
        (exchange.truncation.reason ?? ''),
    };
  }

  // The task actually called on this path. `op` is the task that PRODUCED the fact a panel shows,
  // which for Score is the seller's own execution check — a call the buyer never makes. Naming it in
  // a failure message would report an error against a task that was not invoked here.
  const calledOp = phase.binding.sourceOp ?? phase.op;

  if (exchange.result.status === 'error') {
    const detail = describeFailure(exchange.result.output);
    return {
      state: 'unreachable',
      detail: mentionsUnreachable(detail)
        ? detail
        : // Not recognised as a transport failure, but it IS a failure, and `unreachable` is the
          // variant that says so. See this module's header for why the residual goes here.
          `${calledOp} returned an error: ${detail}`,
    };
  }

  const wholly = fanOutWhollyFailed(exchange.result.output);
  if (wholly.failed) {
    return { state: 'unreachable', detail: wholly.detail };
  }

  // A successful result with a payload this phase's decoder could not use. Still a failure to show
  // something, and still not `not_reached`.
  return {
    state: 'unreachable',
    detail: `${calledOp} succeeded but returned no data this panel can display.`,
  };
}

/**
 * The sentence a demonstrator can read out.
 *
 * Phrased for someone standing beside the screen being asked why a panel is dark.
 */
export function describeReason(phase: PhaseDefinition, state: PhaseState<unknown>): string {
  switch (state.state) {
    case 'ready':
      return `${phase.name} has data from ${phase.op}.`;
    case 'not_reached':
      return `${phase.name} has not run in this session yet. Nothing has gone wrong.`;
    case 'not_implemented':
      return state.detail;
    case 'unreachable':
      return state.detail;
  }
}

/** Whether a state should be treated as a problem rather than as pending. */
export function isFailure(state: PhaseState<unknown>): boolean {
  return state.state === 'unreachable';
}
