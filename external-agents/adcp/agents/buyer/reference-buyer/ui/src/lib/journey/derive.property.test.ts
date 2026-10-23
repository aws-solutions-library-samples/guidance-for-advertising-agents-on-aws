/**
 * Property tests for the journey's derivation layer.
 *
 * These target the invariants the absence rules depend on, because those are the ones an
 * example-based test can pass by luck. The generators are domain-shaped rather than raw primitives:
 * a random string in a `format_ids` slot exercises nothing real, whereas a randomly chosen real
 * format id exercises the segment matching that a prefix test once got wrong.
 *
 * Scope is the partial PBT set agreed for this project: pure functions and round-trips.
 */

import fc from 'fast-check';
import { describe, expect, it } from 'vitest';

import type { Product, SellerEntry, SessionStep } from '../types';
import { classify } from './absence';
import { derivePhases } from './derive';
import { pairExchanges } from './exchanges';
import { PHASES, phaseByKey } from './phases';
import { deriveDiscover } from './productGroups';
import { rank } from './sellerJourneys';
import { plannedChips } from './summary';
import type { DeriveContext, ToolExchange } from './types';

/** Real format ids, including the shapes that have caused bugs. */
const formatId = fc.constantFrom(
  'audio_30s',
  'audio_15s',
  'podcast_midroll',
  'podcast_preroll',
  'display_300x250',
  'gotham_video',
  'nyt_display_728x90',
  'interactive_endcard',
  'carousel_rich', // matches no authored group, on purpose
  'unknown',
);

const arbProduct: fc.Arbitrary<Product> = fc.record({
  product_id: fc.string({ minLength: 1, maxLength: 12 }),
  format_ids: fc.array(formatId, { minLength: 0, maxLength: 2 }),
});

/** A seller entry that either responded with products or failed, both realistically shaped. */
const arbSellerEntry: fc.Arbitrary<SellerEntry> = fc.oneof(
  fc.record({
    seller_id: fc.string({ minLength: 1, maxLength: 8 }),
    seller_name: fc.string({ minLength: 1, maxLength: 12 }),
    response: fc.record({ products: fc.array(arbProduct, { maxLength: 8 }) }),
  }),
  fc.record({
    seller_id: fc.string({ minLength: 1, maxLength: 8 }),
    seller_name: fc.string({ minLength: 1, maxLength: 12 }),
    error: fc.constantFrom('TimeoutError', 'ConnectionError: refused', 'RuntimeError: boom'),
  }),
  fc.record({
    seller_id: fc.string({ minLength: 1, maxLength: 8 }),
    response: fc.record({ mcp_error: fc.constant('Unknown tool') }),
  }),
);

const arbFanOut = fc.array(arbSellerEntry, { minLength: 1, maxLength: 5 }).map((results) => ({
  task: 'adcp_get_products',
  sellers_queried: results.length,
  results,
}));

const U1: DeriveContext = {
  capabilities: { callableTools: ['adcp_get_products', 'adcp_list_creative_formats'] },
  sellerPriority: [],
};

describe('property: phase state coverage', () => {
  it('assigns exactly one state to every phase, for any session', () => {
    fc.assert(
      fc.property(
        fc.array(
          fc.record({
            step_index: fc.nat({ max: 50 }),
            step_type: fc.constantFrom(
              'incoming_request',
              'thought',
              'tool_call',
              'tool_result',
              'response',
            ),
            content: fc.record({
              text: fc.string({ maxLength: 20 }),
              toolName: fc.constantFrom('adcp_get_products', 'adcp_check_governance', 'unrelated_tool'),
              callId: fc.constantFrom('c1', 'c2'),
              status: fc.constantFrom('success', 'error'),
            }),
          }),
          { maxLength: 12 },
        ),
        (steps) => {
          const states = derivePhases(steps as SessionStep[], U1);
          expect(states.size).toBe(PHASES.length);
          PHASES.forEach((phase) => expect(states.get(phase.key)).toBeDefined());
        },
      ),
      { numRuns: 200 },
    );
  });
});

describe('property: classify never returns ready', () => {
  it('returns an absence variant for every input', () => {
    const discover = phaseByKey('discover')!;
    fc.assert(
      fc.property(
        // Built with a `map` rather than a flat record because `exactOptionalPropertyTypes` forbids
        // assigning `undefined` to an optional property: an in-flight exchange must OMIT `result`,
        // not set it to undefined. The compiler enforcing that is the same distinction this whole
        // module is about, applied to its own test data.
        fc.option(
          fc
            .record({
              callId: fc.string({ minLength: 1, maxLength: 4 }),
              toolName: fc.constantFrom('adcp_get_products', 'other'),
              callIndex: fc.nat({ max: 20 }),
              result: fc.option(
                fc.record({
                  output: fc.oneof(fc.string(), fc.constant({}), arbFanOut),
                  status: fc.constantFrom('success' as const, 'error' as const),
                  index: fc.nat({ max: 20 }),
                }),
                { nil: null },
              ),
            })
            .map(
              ({ callId, toolName, callIndex, result }): ToolExchange => ({
                callId,
                toolName,
                input: {},
                callIndex,
                ...(result === null ? {} : { result }),
              }),
            ),
          { nil: undefined },
        ),
        fc.array(fc.constantFrom('adcp_get_products', 'adcp_check_governance'), { maxLength: 3 }),
        (exchange, callableTools) => {
          const state = classify(discover, exchange, { callableTools });
          // The load-bearing property: deciding data exists is the caller's job, so this function
          // must never report success.
          expect(state.state).not.toBe('ready');
          expect(['not_reached', 'not_implemented', 'unreachable']).toContain(state.state);
        },
      ),
      { numRuns: 300 },
    );
  });
});

describe('property: discover counts match the payload', () => {
  it('group counts sum to the products actually matched, never more', () => {
    fc.assert(
      fc.property(arbFanOut, (output) => {
        const data = deriveDiscover(output);
        if (!data) return;
        const grouped = data.groups.reduce((sum, group) => sum + group.count, 0);

        // Total products the responding sellers actually returned.
        const returned = output.results
          .filter((entry) => 'response' in entry && !('mcp_error' in (entry.response ?? {})))
          .reduce(
            (sum, entry) =>
              sum + (Array.isArray(entry.response?.products) ? entry.response.products.length : 0),
            0,
          );

        expect(grouped + data.unmatched).toBe(returned);
        expect(grouped).toBeLessThanOrEqual(returned);
      }),
      { numRuns: 300 },
    );
  });

  it('never produces a group with a zero count', () => {
    // An empty group would assert "this format was offered and is empty", which is a different claim
    // from "this format was not part of the answer".
    fc.assert(
      fc.property(arbFanOut, (output) => {
        const data = deriveDiscover(output);
        if (!data) return;
        data.groups.forEach((group) => expect(group.count).toBeGreaterThan(0));
      }),
      { numRuns: 300 },
    );
  });

  it('accounts for every seller as responded or errored, never dropped', () => {
    fc.assert(
      fc.property(arbFanOut, (output) => {
        const data = deriveDiscover(output);
        if (!data) return;
        expect(data.sellersResponded + data.sellersErrored).toBe(data.sellersQueried);
        expect(data.sellersQueried).toBe(output.results.length);
      }),
      { numRuns: 300 },
    );
  });

  it('never attributes a group to a seller twice', () => {
    fc.assert(
      fc.property(arbFanOut, (output) => {
        const data = deriveDiscover(output);
        if (!data) return;
        data.groups.forEach((group) => {
          expect(new Set(group.attribution).size).toBe(group.attribution.length);
        });
      }),
      { numRuns: 200 },
    );
  });
});

describe('property: exchange pairing round-trips', () => {
  it('produces one exchange per named tool_call, no more and no fewer', () => {
    fc.assert(
      fc.property(
        fc.array(
          fc.record({
            toolName: fc.constantFrom('adcp_get_products', 'adcp_check_governance', 'adcp_sync_plans'),
            callId: fc.string({ minLength: 1, maxLength: 6 }),
            withResult: fc.boolean(),
          }),
          { maxLength: 8 },
        ),
        (specs) => {
          // Distinct callIds, since a repeated id is the same call by definition.
          const unique = specs.filter(
            (spec, index) => specs.findIndex((s) => s.callId === spec.callId) === index,
          );
          const steps: SessionStep[] = [];
          let index = 0;
          for (const spec of unique) {
            steps.push({
              step_index: index++,
              step_type: 'tool_call',
              content: { toolName: spec.toolName, callId: spec.callId },
            });
            if (spec.withResult) {
              steps.push({
                step_index: index++,
                step_type: 'tool_result',
                content: { callId: spec.callId, status: 'success', output: {} },
              });
            }
          }
          const exchanges = pairExchanges(steps);
          expect(exchanges).toHaveLength(unique.length);
          // A call without a result is in flight, never dropped and never a failure.
          const inFlight = exchanges.filter((e) => e.result === undefined).length;
          expect(inFlight).toBe(unique.filter((s) => !s.withResult).length);
        },
      ),
      { numRuns: 200 },
    );
  });

  it('orders exchanges by step_index regardless of input order', () => {
    fc.assert(
      fc.property(fc.array(fc.nat({ max: 100 }), { minLength: 1, maxLength: 8 }), (indices) => {
        const unique = [...new Set(indices)];
        const steps: SessionStep[] = unique.map((i) => ({
          step_index: i,
          step_type: 'tool_call',
          content: { toolName: 'adcp_get_products', callId: `c${i}` },
        }));
        const exchanges = pairExchanges([...steps].reverse());
        const order = exchanges.map((e) => e.callIndex);
        expect(order).toEqual([...order].sort((a, b) => a - b));
      }),
      { numRuns: 200 },
    );
  });
});

describe('property: seller ranking keeps everyone', () => {
  it('returns every seller, whatever the priority list', () => {
    fc.assert(
      fc.property(
        fc.array(arbSellerEntry, { minLength: 1, maxLength: 6 }),
        fc.array(fc.string({ minLength: 1, maxLength: 8 }), { maxLength: 4 }),
        (entries, priority) => {
          const ranked = rank(entries, priority);
          // A priority list is a display preference, not an allowlist.
          expect(ranked).toHaveLength(entries.length);
        },
      ),
      { numRuns: 200 },
    );
  });

  it('classifies every seller as responded or errored, never neither', () => {
    fc.assert(
      fc.property(fc.array(arbSellerEntry, { minLength: 1, maxLength: 6 }), (entries) => {
        rank(entries, []).forEach((journey) => {
          expect(['responded', 'errored']).toContain(journey.outcome);
        });
      }),
      { numRuns: 200 },
    );
  });
});

describe('property: chips are never empty or placeholder', () => {
  it('produces no chip with a blank, zero or dash value', () => {
    fc.assert(
      fc.property(fc.string({ maxLength: 120 }), (text) => {
        plannedChips(null, text).forEach((chip) => {
          expect(chip.value.trim().length).toBeGreaterThan(0);
          expect(['0', '-', '\u2014', 'null', 'undefined', 'NaN']).not.toContain(chip.value);
        });
      }),
      { numRuns: 400 },
    );
  });

  it('always records a provenance for every chip it does produce', () => {
    fc.assert(
      fc.property(
        fc.option(fc.record({ budget: fc.string({ minLength: 1, maxLength: 10 }) }), {
          nil: null,
        }),
        fc.string({ maxLength: 80 }),
        (plan, text) => {
          plannedChips(plan, text).forEach((chip) => {
            expect(['request_text', 'request_preview', 'plan', 'tool_result']).toContain(
              chip.source,
            );
          });
        },
      ),
      { numRuns: 300 },
    );
  });
});
