/**
 * Per-agent filtering and recency ordering for the session lists.
 *
 * The ids below are real, taken from the live sessions table. One A2A buyer invocation produced all
 * four:
 *
 *     5c6d6c96168d4eb2ba70abb29aea3fe5f            agent_id=buyer-a2a        <- the buyer session
 *     adcp-seller-reference-5c6d6c96168d4eb2ba70…  agent_id=reference-seller
 *     adcp-seller-triton-5c6d6c96168d4eb2ba70…     agent_id=triton-seller
 *     adcp-seller-uncorrelated-108f50e6-…          agent_id=gotham-seller
 *
 * The seller ids EMBED the buyer session id, so they are children of one journey. Listing them beside
 * their own parent made the dropdown several times longer than the number of real journeys and pushed
 * the newest buyer session off index 0 -- and auto-switch reads `sessions[0]`, so one cause produced
 * both reported symptoms: bad ordering, and no switch on a new invocation.
 */
import { describe, expect, it } from 'vitest';

import { BUYER_A2A_AGENT_ID, isSessionForAgent, sessionRecency, sortByRecency } from './sessions';
import type { SessionMeta } from './types';

const BUYER = '5c6d6c96168d4eb2ba70abb29aea3fe5f';
const SELLER_REFERENCE = `adcp-seller-reference-${BUYER}`;
const SELLER_TRITON = `adcp-seller-triton-${BUYER}`;
const SELLER_UNCORRELATED = 'adcp-seller-uncorrelated-108f50e6-b181-4000-9000-000000000000';

function meta(
  session_id: string,
  updated_at?: string,
  started_at?: string,
  agent_id: string | undefined = BUYER_A2A_AGENT_ID,
): SessionMeta {
  return {
    session_id,
    status: 'completed',
    ...(agent_id === undefined ? {} : { agent_id }),
    ...(updated_at === undefined ? {} : { updated_at }),
    ...(started_at === undefined ? {} : { started_at }),
  } as SessionMeta;
}

describe('isSessionForAgent', () => {
  it('accepts the A2A buyer session', () => {
    expect(isSessionForAgent(meta(BUYER), BUYER_A2A_AGENT_ID)).toBe(true);
  });

  it.each([
    ['reference seller', SELLER_REFERENCE, 'reference-seller'],
    ['triton seller', SELLER_TRITON, 'triton-seller'],
    ['uncorrelated seller tool call', SELLER_UNCORRELATED, 'gotham-seller'],
  ])('rejects the %s recording', (_label, id, agentId) => {
    expect(isSessionForAgent(meta(id, undefined, undefined, agentId), BUYER_A2A_AGENT_ID)).toBe(
      false,
    );
  });

  it('rejects the HTTP runtime, which the earlier id-prefix test let through', () => {
    // The reported symptom: a list asked to hold A2A journeys carried `AdCP Buyer Agent (HTTP)` rows.
    // Its session ids look exactly like the A2A runtime's, so only `agent_id` separates them.
    expect(isSessionForAgent(meta('7c1e…', undefined, undefined, 'buyer-http'), BUYER_A2A_AGENT_ID))
      .toBe(false);
  });

  it('rejects a record whose writer reported no agent', () => {
    // Not looking like anyone else's is not evidence of being this agent's. Records predating agent
    // identity carry no `agent_id` at all.
    const anonymous: SessionMeta = { session_id: BUYER, status: 'completed' };
    expect(isSessionForAgent(anonymous, BUYER_A2A_AGENT_ID)).toBe(false);
  });

  it('keeps one buyer session out of the four records one invocation produces', () => {
    const all = [
      meta(BUYER),
      meta(SELLER_REFERENCE, undefined, undefined, 'reference-seller'),
      meta(SELLER_TRITON, undefined, undefined, 'triton-seller'),
      meta(SELLER_UNCORRELATED, undefined, undefined, 'gotham-seller'),
    ];
    expect(
      all.filter((s) => isSessionForAgent(s, BUYER_A2A_AGENT_ID)).map((s) => s.session_id),
    ).toEqual([BUYER]);
  });
});

describe('sessionRecency', () => {
  it('prefers updated_at', () => {
    expect(sessionRecency(meta('a', '2026-08-08T18:51:45', '2026-08-08T18:00:00'))).toBe(
      '2026-08-08T18:51:45',
    );
  });

  it('falls back to started_at when a session has never been updated', () => {
    expect(sessionRecency(meta('a', undefined, '2026-08-08T18:00:00'))).toBe('2026-08-08T18:00:00');
  });

  it('yields an empty string when neither is present, so it sorts last rather than throwing', () => {
    expect(sessionRecency(meta('a'))).toBe('');
  });
});

describe('sortByRecency', () => {
  it('puts the newest first, which is what auto-switch reads', () => {
    const sorted = sortByRecency([
      meta('older', '2026-08-08T18:40:18'),
      meta('newest', '2026-08-08T18:51:45'),
      meta('middle', '2026-08-08T18:42:15'),
    ]);
    expect(sorted.map((s) => s.session_id)).toEqual(['newest', 'middle', 'older']);
  });

  it('does not mutate its input', () => {
    const input = [meta('a', '2026-01-01T00:00:00'), meta('b', '2026-02-01T00:00:00')];
    const before = input.map((s) => s.session_id);
    sortByRecency(input);
    expect(input.map((s) => s.session_id)).toEqual(before);
  });

  it('orders a session with only started_at against one with updated_at', () => {
    const sorted = sortByRecency([
      meta('updated-older', '2026-08-08T18:00:00'),
      meta('started-newer', undefined, '2026-08-08T19:00:00'),
    ]);
    expect(sorted[0]!.session_id).toBe('started-newer');
  });

  it('keeps equal timestamps in their existing order, so the list does not shuffle between polls', () => {
    const same = '2026-08-08T18:51:45';
    const sorted = sortByRecency([meta('first', same), meta('second', same), meta('third', same)]);
    expect(sorted.map((s) => s.session_id)).toEqual(['first', 'second', 'third']);
  });

  it('filters then sorts to leave exactly the A2A buyer journeys, newest first', () => {
    // The end-to-end shape of what the poll now does.
    const fromServer = [
      meta(SELLER_UNCORRELATED, '2026-08-08T18:50:54', undefined, 'gotham-seller'),
      meta(BUYER, '2026-08-08T18:51:45'),
      meta(SELLER_TRITON, '2026-08-08T18:50:52', undefined, 'triton-seller'),
      meta('1acbb6a0eeba41f4a35e6886e39c6e09e', '2026-08-08T18:40:18'),
      meta(SELLER_REFERENCE, '2026-08-08T18:50:51', undefined, 'reference-seller'),
      meta('8715b0d993314f3b9a91e116bc7e68a10', '2026-08-08T18:42:15'),
      meta('f0e1d2c3b4a5968778695a4b3c2d1e0f0', '2026-08-08T18:51:50', undefined, 'buyer-http'),
    ];
    const shown = sortByRecency(
      fromServer.filter((s) => isSessionForAgent(s, BUYER_A2A_AGENT_ID)),
    );
    expect(shown.map((s) => s.session_id)).toEqual([
      BUYER,
      '8715b0d993314f3b9a91e116bc7e68a10',
      '1acbb6a0eeba41f4a35e6886e39c6e09e',
    ]);
    // The newest buyer session is at index 0, which is the fact auto-switch depends on.
    expect(shown[0]!.session_id).toBe(BUYER);
  });
});
