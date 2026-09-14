/**
 * Drives the clock explicitly rather than using `waitFor`.
 *
 * `waitFor` deadlocks against vitest's fake timers: it waits on a real-time interval that the fake
 * clock never advances. Advancing the clock by hand and asserting straight afterwards is both
 * deterministic and a closer description of what is being tested, since the polls are the
 * thing under test.
 */

import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { BUYER_A2A_AGENT_ID } from '../lib/sessions';
import type { InvokeEvent, SessionMeta, SessionStep } from '../lib/types';
import { useJourneySession } from './useJourneySession';

/** A session this runtime recorded. `agent_id` is what the view filters on, so it is not optional. */
const session = (id: string, status = 'active'): SessionMeta => ({
  session_id: id,
  status,
  agent_id: BUYER_A2A_AGENT_ID,
  request_preview: `brief for ${id}`,
});

/** A session some other writer sharing the table recorded: a seller, or the HTTP runtime. */
const otherAgentSession = (id: string, agentId: string): SessionMeta => ({
  ...session(id),
  agent_id: agentId,
});

const step = (index: number, text: string): SessionStep => ({
  step_index: index,
  step_type: 'incoming_request',
  content: { text },
});

/**
 * A scriptable stand-in for the runtime.
 *
 * Answers from mutable state, so a test can change what the store contains between polls, which is
 * exactly what auto-switching has to react to.
 */
function harness() {
  const state = {
    sessions: [] as SessionMeta[],
    stepsBySession: new Map<string, SessionStep[]>(),
    metaBySession: new Map<string, SessionMeta | null>(),
    failListWith: null as string | null,
    failStepsWith: null as string | null,
    stepCalls: [] as Array<{ sessionId: string; sinceIndex: number }>,
    listCalls: [] as Array<{ agentId: string | undefined }>,
    /**
     * Whether the fake server honours `agent_id`, as the real one does.
     *
     * Switchable so a test can stand in for a runtime that predates the parameter — the state a newly
     * served bundle is in for the minutes between the UI and runtime deploys.
     */
    serverFiltersByAgent: true,
  };

  const invoke = vi.fn(async (payload: unknown): Promise<InvokeEvent[]> => {
    const request = payload as {
      action: string;
      session_id?: string;
      since_index?: number;
      agent_id?: string;
    };
    if (request.action === 'list_sessions') {
      if (state.failListWith !== null) throw new Error(state.failListWith);
      state.listCalls.push({ agentId: request.agent_id });
      const scoped =
        state.serverFiltersByAgent && request.agent_id !== undefined
          ? state.sessions.filter((s) => s.agent_id === request.agent_id)
          : state.sessions;
      return [{ type: 'sessions', sessions: scoped }];
    }
    if (request.action === 'get_session_steps') {
      if (state.failStepsWith !== null) throw new Error(state.failStepsWith);
      const id = request.session_id ?? '';
      const since = request.since_index ?? -1;
      state.stepCalls.push({ sessionId: id, sinceIndex: since });
      const all = state.stepsBySession.get(id) ?? [];
      const meta = state.metaBySession.has(id) ? state.metaBySession.get(id)! : session(id);
      return [
        { type: 'session_steps', meta, steps: all.filter((s) => s.step_index > since) },
      ];
    }
    return [];
  });

  return { state, invoke };
}

/** Advance the fake clock and let every resulting promise settle. */
async function tick(ms = 10) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

/** One full round: the list poll, then the step poll it triggers. */
async function settle() {
  await tick(10);
  await tick(10);
  await tick(10);
}

/** Past both poll intervals, so a changed store is picked up. */
async function nextRound() {
  await tick(5100);
  await settle();
}

describe('useJourneySession', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('does nothing while disabled', async () => {
    const { invoke } = harness();
    const { result } = renderHook(() => useJourneySession(invoke, false));
    await settle();
    expect(invoke).not.toHaveBeenCalled();
    expect(result.current.sessionId).toBeNull();
  });

  it('adopts the newest session and loads its steps', async () => {
    const { state, invoke } = harness();
    state.sessions = [session('s-new'), session('s-old')];
    state.stepsBySession.set('s-new', [step(0, 'premium streaming audio')]);

    const { result } = renderHook(() => useJourneySession(invoke, true));
    await settle();
    expect(result.current.sessionId).toBe('s-new');
    expect(result.current.origin).toBe('auto');
    expect(result.current.steps).toHaveLength(1);
  });

  it('filters out standalone tool-call records so auto-switch cannot chase noise', async () => {
    // One verification run produced 95 uncorrelated records out of 101, enough to fill the page the
    // dropdown reads and push every real conversation off it. They are seller-written, so the agent
    // scope is what excludes them.
    const { state, invoke } = harness();
    state.sessions = [
      otherAgentSession('adcp-seller-uncorrelated-abc', 'gotham-seller'),
      session('s-real'),
    ];

    const { result } = renderHook(() => useJourneySession(invoke, true));
    await settle();
    expect(result.current.sessionId).toBe('s-real');
    expect(result.current.sessions.map((s) => s.session_id)).toEqual(['s-real']);
  });

  it('asks the server for this runtime only, rather than filtering a page of everyone else', async () => {
    const { state, invoke } = harness();
    state.sessions = [session('s')];

    renderHook(() => useJourneySession(invoke, true));
    await settle();
    expect(state.listCalls[0]?.agentId).toBe(BUYER_A2A_AGENT_ID);
  });

  it('switches to a newer session, interrupting whatever was on screen', async () => {
    const { state, invoke } = harness();
    state.sessions = [session('s-first')];
    state.stepsBySession.set('s-first', [step(0, 'first brief')]);

    const { result } = renderHook(() => useJourneySession(invoke, true));
    await settle();
    expect(result.current.sessionId).toBe('s-first');
    const firstGeneration = result.current.generation;

    // A new brief is triggered from the other system.
    state.sessions = [session('s-second'), session('s-first')];
    state.stepsBySession.set('s-second', [step(0, 'second brief')]);
    await nextRound();

    expect(result.current.sessionId).toBe('s-second');
    // The generation increments so consumers discard work scheduled for the previous session.
    expect(result.current.generation).toBeGreaterThan(firstGeneration);
    // The previous session's steps do not linger.
    expect(result.current.steps.some((s) => s.content?.text === 'first brief')).toBe(false);
  });

  it('shows only this runtime, not the sellers or the HTTP runtime', async () => {
    // Real ids from the live table. One A2A buyer invocation produces all the seller ones, and they
    // EMBED the buyer session id -- children of one journey, not separate journeys. The HTTP row is the
    // reported symptom: its ids are shaped exactly like this runtime's, so only `agent_id` separates
    // them.
    const buyer = '5c6d6c96168d4eb2ba70abb29aea3fe5f';
    const { state, invoke } = harness();
    state.sessions = [
      otherAgentSession('adcp-seller-uncorrelated-108f50e6', 'gotham-seller'),
      otherAgentSession(`adcp-seller-triton-${buyer}`, 'triton-seller'),
      otherAgentSession('f0e1d2c3b4a5968778695a4b3c2d1e0f0', 'buyer-http'),
      session(buyer),
      otherAgentSession(`adcp-seller-reference-${buyer}`, 'reference-seller'),
    ];
    const { result } = renderHook(() => useJourneySession(invoke, true));
    await settle();
    expect(result.current.sessions.map((s) => s.session_id)).toEqual([buyer]);
    expect(result.current.sessionId).toBe(buyer);
  });

  it('still shows only this runtime when the server ignores the agent parameter', async () => {
    // The window between deploys: the UI publishes in seconds and a runtime takes minutes, so a new
    // bundle runs against the previous runtime for a while. Without the check on arrival, that window
    // shows every writer's sessions again.
    const { state, invoke } = harness();
    state.serverFiltersByAgent = false;
    state.sessions = [
      otherAgentSession('http-newest', 'buyer-http'),
      session('a2a-session'),
      otherAgentSession('adcp-seller-triton-abc', 'triton-seller'),
    ];
    const { result } = renderHook(() => useJourneySession(invoke, true));
    await settle();
    expect(result.current.sessions.map((s) => s.session_id)).toEqual(['a2a-session']);
    expect(result.current.sessionId).toBe('a2a-session');
  });

  it('adopts the newest session even when the server does not return it first', async () => {
    // The regression this ordering fix exists for. The backend orders by its GSI sort key, and its own
    // docstring notes "a conversation in progress sorted below a newer abandoned one" -- so index 0 is
    // not reliably the newest. Auto-switch reads `sessions[0]`, so the list must be sorted by activity
    // here or the view follows the wrong session. The previous test never caught this because its
    // harness handed the list already newest-first and with no timestamps at all.
    const { state, invoke } = harness();
    state.sessions = [
      { ...session('older'), updated_at: '2026-08-08T18:40:18' },
      { ...session('newest'), updated_at: '2026-08-08T18:51:45' },
      { ...session('middle'), updated_at: '2026-08-08T18:42:15' },
    ];
    const { result } = renderHook(() => useJourneySession(invoke, true));
    await settle();
    expect(result.current.sessions.map((s) => s.session_id)).toEqual(['newest', 'middle', 'older']);
    expect(result.current.sessionId).toBe('newest');
  });

  it('switches when a new invocation arrives out of order in the list', async () => {
    const { state, invoke } = harness();
    state.sessions = [{ ...session('s-first'), updated_at: '2026-08-08T18:00:00' }];
    const { result } = renderHook(() => useJourneySession(invoke, true));
    await settle();
    expect(result.current.sessionId).toBe('s-first');

    // A new brief arrives, and the server happens to list it LAST.
    state.sessions = [
      { ...session('s-first'), updated_at: '2026-08-08T18:00:00' },
      { ...session('s-new'), updated_at: '2026-08-08T19:00:00' },
    ];
    await nextRound();
    expect(result.current.sessionId).toBe('s-new');
  });

  it('asks only for steps past the high-water mark', async () => {
    // The second guard against the duplicate-message bug: the request itself is bounded.
    const { state, invoke } = harness();
    state.sessions = [session('s')];
    state.stepsBySession.set('s', [step(0, 'a'), step(1, 'b')]);

    renderHook(() => useJourneySession(invoke, true));
    await settle();
    expect(state.stepCalls[0]?.sinceIndex).toBe(-1);

    await tick(2100);
    await settle();
    const last = state.stepCalls[state.stepCalls.length - 1];
    expect(last?.sinceIndex).toBe(1);
  });

  it('never applies the same step twice', async () => {
    const { state, invoke } = harness();
    state.sessions = [session('s')];
    state.stepsBySession.set('s', [step(0, 'only once')]);

    const { result } = renderHook(() => useJourneySession(invoke, true));
    await settle();
    // Several more polls, each of which would re-send step 0 if the mark were ignored.
    await tick(2100);
    await settle();
    await tick(2100);
    await settle();
    expect(result.current.steps.filter((s) => s.step_index === 0)).toHaveLength(1);
  });

  it('a manual selection suspends auto-switching', async () => {
    const { state, invoke } = harness();
    state.sessions = [session('s-new'), session('s-chosen')];

    const { result } = renderHook(() => useJourneySession(invoke, true));
    await settle();
    expect(result.current.sessionId).toBe('s-new');

    await act(async () => {
      result.current.selectSession('s-chosen');
    });
    await settle();
    expect(result.current.origin).toBe('manual');
    expect(result.current.sessionId).toBe('s-chosen');

    // Something newer arrives; the reader's choice wins.
    state.sessions = [session('s-newest'), session('s-new'), session('s-chosen')];
    await nextRound();
    expect(result.current.sessionId).toBe('s-chosen');
  });

  it('followNewest resumes automatic switching', async () => {
    const { state, invoke } = harness();
    state.sessions = [session('s-a'), session('s-b')];

    const { result } = renderHook(() => useJourneySession(invoke, true));
    await settle();
    await act(async () => {
      result.current.selectSession('s-b');
    });
    await settle();
    expect(result.current.origin).toBe('manual');

    await act(async () => {
      result.current.followNewest();
    });
    await settle();
    expect(result.current.origin).toBe('auto');
    expect(result.current.sessionId).toBe('s-a');
  });

  it('reports a run of failures without clearing what is on screen', async () => {
    // A booth screen frozen on a stale session with no indication is the failure this guards.
    const { state, invoke } = harness();
    state.sessions = [session('s')];
    state.stepsBySession.set('s', [step(0, 'a real brief')]);

    const { result } = renderHook(() => useJourneySession(invoke, true));
    await settle();
    expect(result.current.steps).toHaveLength(1);

    state.failStepsWith = 'network down';
    for (let i = 0; i < 4; i += 1) {
      await tick(2100);
      await settle();
    }

    expect(result.current.staleNote).not.toBeNull();
    expect(result.current.staleNote).toContain('stopped updating');
    expect(result.current.staleNote).toContain('network down');
    // The last known state is still there.
    expect(result.current.steps).toHaveLength(1);
  });

  it('does not warn on a single failure', async () => {
    const { state, invoke } = harness();
    state.sessions = [session('s')];

    const { result } = renderHook(() => useJourneySession(invoke, true));
    await settle();
    state.failStepsWith = 'blip';
    await tick(2100);
    await settle();
    expect(result.current.staleNote).toBeNull();
  });

  it('distinguishes an expired session from one whose first step has not landed', async () => {
    const { state, invoke } = harness();
    state.sessions = [session('s-gone')];
    state.metaBySession.set('s-gone', null);

    const { result } = renderHook(() => useJourneySession(invoke, true));
    await settle();
    expect(result.current.status).toContain('No record found');

    // A session that exists but has recorded nothing yet is a different answer.
    state.sessions = [session('s-empty'), session('s-gone')];
    state.metaBySession.set('s-empty', session('s-empty'));
    await nextRound();
    expect(result.current.status).toContain('No steps recorded yet');
  });

  it('keeps steps ordered by step_index regardless of arrival order', async () => {
    const { state, invoke } = harness();
    state.sessions = [session('s')];
    state.stepsBySession.set('s', [step(2, 'third'), step(0, 'first'), step(1, 'second')]);

    const { result } = renderHook(() => useJourneySession(invoke, true));
    await settle();
    expect(result.current.steps.map((s) => s.step_index)).toEqual([0, 1, 2]);
  });

  it('survives a failing session list without losing the current session', async () => {
    const { state, invoke } = harness();
    state.sessions = [session('s')];
    state.stepsBySession.set('s', [step(0, 'a')]);

    const { result } = renderHook(() => useJourneySession(invoke, true));
    await settle();
    expect(result.current.sessionId).toBe('s');

    state.failListWith = 'list unavailable';
    await nextRound();
    // A failing list poll does not blank the view; the step poll owns staleness reporting.
    expect(result.current.sessionId).toBe('s');
    expect(result.current.steps).toHaveLength(1);
  });
});
