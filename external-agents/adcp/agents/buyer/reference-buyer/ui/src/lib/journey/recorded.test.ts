/**
 * Derivation against a recorded session.
 *
 * Every other test in this directory uses fixtures I wrote, and that is exactly how the tool-name
 * defect survived: the fixtures named `get_products` because the code did, so they agreed with each
 * other and disagreed with the agent. A hundred passing tests said nothing about whether the screen
 * would show anything, and it showed nothing.
 *
 * `__fixtures__/recorded-discovery-session.json` is not written by hand. It is three steps captured
 * from a real session in the sessions table — the incoming brief, the `adcp_get_products` call, and its
 * result — trimmed to two products per seller because this is about SHAPE, not volume. It is realistic
 * test input, which is the one legitimate use of captured data.
 *
 * What it pins down, none of which the hand-written fixtures could:
 *   - the recorded tool name is `adcp_get_products`, with the agent's prefix;
 *   - `content.output` arrives as a JSON STRING, not an object;
 *   - a product's format lives in `format_ids[0].id` as an object, not a bare string;
 *   - `audio_30s` resolves to the `audio` group by its leading segment.
 */

import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import type { SessionStep } from '../types';
import { deriveJourney, derivePhases } from './derive';
import { PHASES } from './phases';
import type { DiscoverData } from './types';

const here = dirname(fileURLToPath(import.meta.url));
const FIXTURE = resolve(here, '__fixtures__/recorded-discovery-session.json');

const steps = JSON.parse(readFileSync(FIXTURE, 'utf8')) as SessionStep[];

/** The real tool set, as `JourneyView` passes it. */
const CONTEXT = {
  capabilities: {
    callableTools: [
      'adcp_get_products',
      'adcp_get_capabilities',
      'adcp_list_creative_formats',
    ],
  },
  sellerPriority: [] as readonly string[],
};

describe('a real recorded session', () => {
  it('is shaped the way the fixture claims, so the assertions below mean something', () => {
    const call = steps.find((step) => step.step_type === 'tool_call');
    const result = steps.find((step) => step.step_type === 'tool_result');
    // The prefix. This is the whole defect, captured.
    expect(call?.content?.toolName).toBe('adcp_get_products');
    expect(result?.content?.toolName).toBe('adcp_get_products');
    // A JSON string, not an object. The parse has to happen somewhere, and it does.
    expect(typeof result?.content?.output).toBe('string');
  });

  it('derives Discover as ready', () => {
    // The assertion that would have failed while the binding named the AdCP task instead of the tool,
    // and the one that matches what a person sees on the screen.
    const states = derivePhases(steps, CONTEXT);
    const discover = states.get('discover');
    expect(discover?.state).toBe('ready');
  });

  it('groups the real products by format', () => {
    const states = derivePhases(steps, CONTEXT);
    const discover = states.get('discover');
    if (discover?.state !== 'ready') throw new Error('Discover did not derive');
    const data = discover.data as DiscoverData;
    expect(data.groups.length).toBeGreaterThan(0);
    // `format_ids: [{ id: 'audio_30s', ... }]` resolves to the `audio` group by its leading segment.
    expect(data.groups.map((group) => group.formatKey)).toContain('audio');
    const counted = data.groups.reduce((sum, group) => sum + group.count, 0);
    expect(counted).toBeGreaterThan(0);
  });

  it('tells the real products apart by what the seller said about them', () => {
    // Against captured output, not a fixture written to pass. Both recorded products are
    // `audio_30s` at a CPM from the same seller, so title, format and seller cannot separate them —
    // which is exactly how ten Gotham rows came to look identical. The facts are what differ.
    const states = derivePhases(steps, CONTEXT);
    const discover = states.get('discover');
    if (discover?.state !== 'ready') throw new Error('Discover did not derive');
    const products = (discover.data as DiscoverData).groups.flatMap((group) => group.products);
    expect(products.length).toBeGreaterThan(1);

    for (const product of products) {
      expect(product.facts?.length ?? 0).toBeGreaterThan(0);
    }
    const rendered = products.map((p) => p.facts!.map((f) => f.value).join(' '));
    expect(new Set(rendered).size).toBe(products.length);

    // The specific fields this recording carries, so a seller dropping one is caught here rather
    // than showing up as a blander screen nobody notices.
    const first = products[0]!.facts!.map((f) => f.value);
    expect(first).toContain('Boston, MA');
    expect(first).toContain('preroll, midroll, postroll');
    expect(first).toContain('non-guaranteed');
  });

  it('names the real seller, so the headline can name it too', () => {
    const journey = deriveJourney(steps, null, CONTEXT);
    // Seller selection reads the same registry binding the phase does; it once held its own copy of
    // the tool name and was wrong in the same way.
    expect(journey.shownSeller).not.toBeNull();
    expect(journey.shownSeller?.sellerName.length).toBeGreaterThan(0);
  });

  it('carries the brief through to a chip', () => {
    const journey = deriveJourney(steps, null, CONTEXT);
    const brief = journey.chips.find((chip) => chip.key === 'brief');
    expect(brief?.value.length).toBeGreaterThan(0);
  });

  it('gives the flow a frontier, so something is actually shown', () => {
    // The user-visible consequence: with no phase ready there is no frontier, the flow renders empty,
    // and Bind and Plan never appear either because they only show behind a frontier.
    const states = derivePhases(steps, CONTEXT);
    const frontier = PHASES.map((phase) => states.get(phase.key)?.state === 'ready').lastIndexOf(
      true,
    );
    expect(frontier).toBeGreaterThanOrEqual(0);
    // The frontier is the phase this session actually reached. For a discovery-only recording that
    // is Discover itself, which now LEADS the rail.
    expect(frontier).toBe(PHASES.findIndex((phase) => phase.key === 'discover'));
    // Bind and Plan now sit AHEAD of that frontier rather than behind it, so this recording does not
    // reveal them -- correctly, because it never reached them. The old assertion here said the
    // opposite (`frontier > index('plan')`), which was only true while those two led the rail.
    //
    // This is the visible consequence of leading with Discover, and it is the right one: a
    // discovery-only session shows the phase it reached and leaves the rest dim, instead of opening
    // with two panels explaining why they are empty.
    expect(frontier).toBeLessThan(PHASES.findIndex((phase) => phase.key === 'plan'));
  });
});
