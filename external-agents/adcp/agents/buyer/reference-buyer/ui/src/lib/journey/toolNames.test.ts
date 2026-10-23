/**
 * The two recorded spellings of an AdCP tool name, and the guarantee that both bind.
 *
 * These cases come from a live defect, not from imagination. A session produced by an external system
 * calling the reference seller loaded with data and left every phase dark. The recorded step was:
 *
 *     session_id : adcp-seller-reference-8fb87602d92a477583b85708d06a97377
 *     step_type  : tool_call
 *     toolName   : "get_products"
 *
 * while `phases.ts` binds Discover to `adcp_get_products`. Exact string comparison dropped it.
 *
 * `phases.tools.test.ts` did not catch this, and the reason is worth keeping: it reads the buyer's own
 * `adcp_tools.py` registrations, so it verifies the bindings against ONE of the two producers. A test
 * that agrees with the code it is checking cannot see a producer it never looks at.
 */
import { describe, expect, it } from 'vitest';

import { exchangesForTool, latestExchangeForTool } from './exchanges';
import { PHASES } from './phases';
import { matchesToolName, normaliseToolName } from './toolNames';
import type { ToolExchange } from './types';

function exchange(toolName: string, output?: unknown): ToolExchange {
  return {
    toolName,
    callId: `${toolName}-1`,
    input: {},
    ...(output === undefined ? {} : { result: { output } }),
  } as ToolExchange;
}

describe('normaliseToolName', () => {
  // The four spellings actually present in the sessions table, found by scanning every recorded
  // `content.toolName` rather than by reading the two producers and assuming symmetry.
  it.each([
    ['adcp_get_products', 'get_products'],
    ['get_products', 'get_products'],
    ['adcp_get_capabilities', 'get_capabilities'],
    ['get_adcp_capabilities', 'get_capabilities'],
  ])('normalises %s -> %s', (input, expected) => {
    expect(normaliseToolName(input)).toBe(expected);
  });

  it('leaves non-AdCP tools alone', () => {
    expect(normaliseToolName('vendor_get_market_rates')).toBe('vendor_get_market_rates');
    expect(normaliseToolName('vendor_get_trust_score')).toBe('vendor_get_trust_score');
  });

  it('only removes whole segments, not substrings inside a word', () => {
    // "adcpx" is not the marker segment, so it survives.
    expect(normaliseToolName('adcpx_get_products')).toBe('adcpx_get_products');
  });

  it('stays total when the name is nothing but the marker', () => {
    expect(normaliseToolName('adcp')).toBe('adcp');
  });
});

describe('matchesToolName', () => {
  it('matches the seller-recorded name against the buyer-registered binding', () => {
    expect(matchesToolName('get_products', 'adcp_get_products')).toBe(true);
  });

  it('matches in both directions', () => {
    expect(matchesToolName('adcp_get_products', 'get_products')).toBe(true);
  });

  it('matches the capabilities pair, where the marker moves position', () => {
    // The case a leading-prefix strip missed: the seller names this `get_adcp_capabilities`.
    expect(matchesToolName('get_adcp_capabilities', 'adcp_get_capabilities')).toBe(true);
  });

  it('still distinguishes genuinely different tasks', () => {
    expect(matchesToolName('get_products', 'adcp_create_media_buy')).toBe(false);
    expect(matchesToolName('adcp_sync_plans', 'adcp_sync_governance')).toBe(false);
  });
});

describe('phase bindings stay unambiguous once normalised', () => {
  it('no two phases collide on the normalised name AND selector together', () => {
    // The safety condition that makes prefix-insensitive matching sound: if two phases were
    // indistinguishable, one phase's data could render under another's heading -- worse than a dark
    // panel, because it would look like an answer.
    //
    // The pair, not the tool name alone, because `phasesForTool` returns a list and a shared tool
    // name separated by `selector` is a legal shape.
    //
    // No phase pair uses it today. Govern and Score did — both bound to `check_governance`, told
    // apart by `selector` — and that is the arrangement that put one result set on screen twice: the
    // selector chose first-versus-last exchange, which is the same exchange when a session makes one
    // check. Score now reads the seller's execution check from the audit log instead. The key stays a
    // pair so the ambiguity check still holds if a tool is ever legitimately shared again.
    const seen = new Map<string, string>();
    for (const phase of PHASES) {
      const key = `${normaliseToolName(phase.binding.toolName)}#${phase.binding.selector ?? 'default'}`;
      const previous = seen.get(key);
      expect(previous, `phases ${previous} and ${phase.key} both resolve to ${key}`).toBeUndefined();
      seen.set(key, phase.key);
    }
  });

  it('phases sharing a tool name are separated by distinct selectors', () => {
    // Guards the assumption the test above relies on: that a shared tool name always comes with
    // selectors that actually differ. A shared name with a shared selector would be ambiguous.
    const byTool = new Map<string, string[]>();
    for (const phase of PHASES) {
      const tool = normaliseToolName(phase.binding.toolName);
      byTool.set(tool, [...(byTool.get(tool) ?? []), phase.binding.selector ?? 'default']);
    }
    for (const [tool, selectors] of byTool) {
      expect(new Set(selectors).size, `${tool} has duplicate selectors: ${selectors.join(', ')}`).toBe(
        selectors.length,
      );
    }
  });
});

describe('the live recorded step now binds', () => {
  const discover = PHASES.find((p) => normaliseToolName(p.binding.toolName) === 'get_products');

  it('has a phase bound to the products task at all', () => {
    expect(discover).toBeDefined();
  });

  it('finds the seller-recorded exchange for that phase', () => {
    const exchanges = [exchange('get_products', { products: [] })];
    const found = latestExchangeForTool(exchanges, discover!.binding.toolName);
    expect(found).toBeDefined();
    expect(found!.toolName).toBe('get_products');
  });

  it('finds the buyer-recorded exchange for the same phase', () => {
    const exchanges = [exchange('adcp_get_products', { products: [] })];
    expect(latestExchangeForTool(exchanges, discover!.binding.toolName)).toBeDefined();
  });

  it('returns both spellings together, newest last', () => {
    // A session can contain both if our agent ran and a seller relay was recorded under the same id.
    const exchanges = [exchange('adcp_get_products'), exchange('get_products')];
    expect(exchangesForTool(exchanges, 'adcp_get_products')).toHaveLength(2);
    expect(latestExchangeForTool(exchanges, 'adcp_get_products')!.toolName).toBe('get_products');
  });
});
