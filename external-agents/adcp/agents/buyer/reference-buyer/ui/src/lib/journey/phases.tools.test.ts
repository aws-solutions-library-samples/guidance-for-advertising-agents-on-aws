/**
 * Does the UI name tools the agent actually registers?
 *
 * This exists because the answer was **no, for every single binding**, and nothing caught it. The
 * registry was written from AdCP's task list, so it looked for `get_products` while the buyer agent
 * records `adcp_get_products`. The protocol task name and the agent's local tool name are different
 * things, and conflating them is invisible at every layer that could have complained:
 *
 *   - types pass, because both are strings;
 *   - unit tests pass, because the fixtures used the same wrong names as the code;
 *   - the build passes;
 *   - the deployed app renders an absence for every phase that looks intentional.
 *
 * The result was a screen that reported "this build cannot call these tools" about tools the build
 * calls constantly.
 *
 * So the agent's own source is read here and treated as the authority. It is a static scan: no build,
 * no deploy, no AWS, milliseconds.
 */

import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import { PHASES } from './phases';

const here = dirname(fileURLToPath(import.meta.url));
const ADCP_TOOLS_PY = resolve(here, '../../../../adcp_tools.py');

/**
 * Every tool the buyer agent registers, read from its source.
 *
 * The agent decorates each with `@tool` and defines it as a module-level function, so the function
 * names are the tool names. Read from the file rather than from a copy, so the agent gaining or
 * renaming a tool shows up here.
 */
function registeredTools(): string[] {
  const source = readFileSync(ADCP_TOOLS_PY, 'utf8');
  const names: string[] = [];
  // Keyed on the `@tool` DECORATOR rather than on the function name's shape. That is what actually
  // registers a tool, so a decorated function is a tool and an undecorated helper is not, whatever it
  // happens to be called. The definitions are nested inside a factory and are `async def`, so both
  // the indentation and the `async` have to be allowed for.
  for (const match of source.matchAll(/@tool\s*(?:\([^)]*\))?\s*(?:async\s+)?def\s+([a-z0-9_]+)\s*\(/g)) {
    const name = match[1];
    if (name !== undefined) names.push(name);
  }
  return names.sort();
}

/**
 * Bindings for tools that do not exist yet, declared so their absence is deliberate.
 *
 * These are the phases later units add. Listing them is what makes the check useful in both
 * directions: an unbuilt tool is allowed to be absent, and **when the unit that builds it lands, the
 * name here has to be reconciled with the name it actually registers** — which is precisely the step
 * that was skipped the first time.
 *
 * A phase whose tool is on this list renders `not_implemented`, which is correct and complete for it.
 */
const NOT_BUILT_YET: readonly string[] = [
  // adcp_sync_governance, adcp_sync_plans, adcp_check_governance, adcp_get_signals,
  // adcp_create_media_buy, adcp_report_plan_outcome, adcp_get_media_buy_delivery and
  // adcp_get_creative_features were all HERE and have been removed: the buyer now registers all of
  // them (U2 buyer-governance + U4/Part 1 creative-and-media-buy). Leaving them listed would exempt
  // them from the very check this list exists to force. 'declares nothing as unbuilt that the agent
  // has since started registering' (below) already fails when a name here is actually registered --
  // that guard is what caught each of these reconciliations.
];

describe('the UI names tools the agent actually registers', () => {
  it('can read the agent\u2019s tool source at all', () => {
    // If this file moves, every assertion below would pass vacuously on an empty list.
    const tools = registeredTools();
    expect(tools.length).toBeGreaterThan(0);
    expect(tools).toContain('adcp_get_products');
  });

  it('every phase binding is either registered or declared not built yet', () => {
    const registered = new Set(registeredTools());
    const unaccounted = PHASES.map((phase) => phase.binding.toolName).filter(
      (name) => !registered.has(name) && !NOT_BUILT_YET.includes(name),
    );
    expect(unaccounted).toEqual([]);
  });

  it('every EVALUATOR tool is either registered or declared not built yet', () => {
    // `evaluatorToolName` is a second tool a phase reports on without being supplied by it. Creative is
    // the case, and it was added after this guard existed — so without this test the field would have been
    // exempt from the very check that caught the original tool-name defect.
    const registered = new Set(registeredTools());
    const evaluators = PHASES.map((phase) => phase.binding.evaluatorToolName).filter(
      (name): name is string => typeof name === 'string',
    );
    expect(evaluators.length).toBeGreaterThan(0);
    const unaccounted = evaluators.filter(
      (name) => !registered.has(name) && !NOT_BUILT_YET.includes(name),
    );
    expect(unaccounted).toEqual([]);
  });

  it('Creative is supplied by a seller task and evaluated by a governance task', () => {
    // The AdCP alignment this closes. `list_creative_formats` is a SELLER task and is what fills the
    // panel; `get_creative_features` is a GOVERNANCE-agent task and supplies the verdict the authored
    // subtitle promises. Binding the panel to the governance task named the wrong party and could never
    // populate, because this build originally had no evaluator.
    const creative = PHASES.find((phase) => phase.key === 'creative');
    expect(creative?.binding.toolName).toBe('adcp_list_creative_formats');
    expect(creative?.binding.evaluatorToolName).toBe('adcp_get_creative_features');
    expect(creative?.party).toBe('seller');
    // Both the panel's own tool and the evaluator are callable now (U4/Part 1). A card with no
    // evaluator result for its own format still shows no verdict/scan -- that is ScanBody's own
    // per-card rule (S22), not a build-capability gap any more.
    expect(registeredTools()).toContain('adcp_list_creative_formats');
    expect(registeredTools()).toContain('adcp_get_creative_features');
  });

  it('every entity chip source is either registered or declared not built yet', () => {
    const registered = new Set(registeredTools());
    const sources = PHASES.flatMap((phase) =>
      (phase.binding.entities ?? [])
        .map((entity) => entity.sourceToolName)
        .filter((name): name is string => typeof name === 'string'),
    );
    const unaccounted = sources.filter(
      (name) => !registered.has(name) && !NOT_BUILT_YET.includes(name),
    );
    expect(unaccounted).toEqual([]);
  });

  it('Discover binds to the tool the agent really registers for it', () => {
    // The one phase that can hold data in this build, so the one whose name being wrong emptied the
    // entire screen.
    const discover = PHASES.find((phase) => phase.key === 'discover');
    expect(discover?.binding.toolName).toBe('adcp_get_products');
    expect(registeredTools()).toContain(discover?.binding.toolName);
  });

  it('does not confuse the AdCP task name with the agent\u2019s tool name', () => {
    // `op` is the protocol task and display copy; `toolName` is what the recorder wrote. For Discover
    // they differ by the agent's prefix, and asserting the difference stops the two collapsing back
    // into one another.
    const discover = PHASES.find((phase) => phase.key === 'discover');
    expect(discover?.op).toBe('get_products');
    expect(discover?.binding.toolName).not.toBe(discover?.op);
  });

  it('declares nothing as unbuilt that the agent has since started registering', () => {
    // The reconciliation step. When a later unit registers one of these, this fails and the entry has
    // to be removed — which is the moment to check the name matches rather than assuming it does.
    const registered = new Set(registeredTools());
    const stale = NOT_BUILT_YET.filter((name) => registered.has(name));
    expect(stale).toEqual([]);
  });
});
