/**
 * Matching recorded tool names against phase bindings.
 *
 * Its own module rather than a helper inside `exchanges.ts` because `phases.ts`, `absence.ts` and
 * `exchanges.ts` all need it, and `phases.ts` importing from `exchanges.ts` would couple the phase
 * registry to the step-pairing layer for one string comparison.
 *
 * ## Why matching cannot be exact
 *
 * Two producers write `content.toolName`, and neither is wrong:
 *
 * - `reasoning_hooks.py` records the **buyer's Strands tool registration** — `adcp_get_products`.
 * - `seller_session_recorder.py` records the **AdCP skill name as invoked on the seller** —
 *   `get_products`. That is the seller's own MCP tool name; prefixing it would rename the call.
 *
 * So a session produced by an external system calling a seller carries `get_products`, while one
 * produced by our own agent carries `adcp_get_products`. Exact comparison matches whichever spelling
 * the bindings happen to use and silently drops the other, which presents as a session that loads with
 * data and leaves every phase dark. That has now happened in both directions: once when the
 * bindings used protocol task names and our agent's steps did not match, and again when they used
 * `adcp_` names and seller-recorded steps did not.
 *
 * Normalising to the unprefixed AdCP task name is only sound while every binding stays distinct once
 * the prefix is removed — otherwise one phase's data would render under another's heading, which is
 * worse than a dark panel. `toolNames.test.ts` asserts that rather than assuming it.
 */

/**
 * The marker segment both naming conventions use, in different positions.
 *
 * Dropping it as a whole `_`-delimited SEGMENT, rather than stripping a leading prefix, is what makes
 * the capabilities pair match. A scan of every `toolName` in the sessions table found exactly four
 * spellings across the two producers:
 *
 *     adcp_get_products       (buyer)   <-> get_products           (seller)
 *     adcp_get_capabilities   (buyer)   <-> get_adcp_capabilities  (seller)
 *
 * The second pair carries `adcp` in the MIDDLE, because that is the AdCP task's own name on the
 * seller. A leading-prefix strip fixes the first pair and silently leaves the second broken -- which
 * is what the first version of this module did, and why the count came from a real scan rather than
 * from reading the two producers and assuming their conventions were mirror images.
 *
 * Segment-wise, so a tool whose name merely contains the letters "adcp" inside a longer word is
 * untouched, and a vendor-namespaced tool is unaffected because it carries no such segment.
 */
const ADCP_SEGMENT = 'adcp';

/** The AdCP task name with the protocol marker segment removed, whatever position it occupied. */
export function normaliseToolName(toolName: string): string {
  const kept = toolName.split('_').filter((segment) => segment !== ADCP_SEGMENT);
  // An input of exactly "adcp" would otherwise normalise to the empty string and match nothing
  // meaningful; returning it unchanged keeps the function total.
  return kept.length > 0 ? kept.join('_') : toolName;
}

/** True when a recorded tool name denotes the same task as a phase binding. */
export function matchesToolName(recorded: string, bound: string): boolean {
  return normaliseToolName(recorded) === normaliseToolName(bound);
}
