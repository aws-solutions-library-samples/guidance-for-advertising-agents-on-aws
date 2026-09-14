import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { PHASES, type PhaseKey } from '../lib/journey/phases';
import type { AnyPhaseState, PhaseStates } from '../lib/journey/types';
import { useJourneyPlayback } from './useJourneyPlayback';

function statesWith(ready: readonly PhaseKey[]): PhaseStates {
  const map = new Map<PhaseKey, AnyPhaseState>();
  for (const phase of PHASES) {
    const state: AnyPhaseState = ready.includes(phase.key)
      ? { state: 'ready', data: {} }
      : { state: 'not_reached' };
    map.set(phase.key, state);
  }
  return map;
}

const keyAt = (index: number): PhaseKey => {
  const phase = PHASES[index];
  if (!phase) throw new Error(`no phase at ${index}`);
  return phase.key;
};

const indexOf = (key: PhaseKey): number => PHASES.findIndex((p) => p.key === key);

/** Let effects settle. Enough iterations to walk the whole registry if a walk is running. */
async function settle(times = PHASES.length + 3) {
  for (let i = 0; i < times; i += 1) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(700);
    });
  }
}

/** One effect tick with no time passing, which is all an instant catch-up needs per phase. */
async function tick(times = PHASES.length + 3) {
  for (let i = 0; i < times; i += 1) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
  }
}

describe('useJourneyPlayback — the flow stops where the session stopped', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('reveals nothing when no phase has data', async () => {
    // Honest, and it is what the prototype shows before anything runs: an empty flow.
    const { result } = renderHook(() => useJourneyPlayback(statesWith([]), 'active', 0));
    await settle();
    expect(result.current.revealed.size).toBe(0);
    expect(result.current.active).toBeNull();
    expect(result.current.finished).toBe(false);
  });

  it('stops at the frontier and does not run through the rest of the lifecycle', async () => {
    // THE REGRESSION. Revealing every phase walked the animation to Activate and parked on an empty
    // panel, reporting a journey that had not happened.
    const { result } = renderHook(() => useJourneyPlayback(statesWith(['discover']), 'active', 0));
    await settle();
    const frontier = indexOf('discover');
    expect(result.current.revealed.size).toBe(frontier + 1);
    expect(result.current.active).toBe('discover');
    // Nothing beyond the frontier appears.
    PHASES.slice(frontier + 1).forEach((phase) => {
      expect(result.current.revealed.has(phase.key)).toBe(false);
    });
  });

  it('still reveals phases BEHIND the frontier that hold no data', async () => {
    // The other defect, on the far side of the same line: a phase the session passed over has a
    // reason worth reading, and `.panel.pending` is `opacity:0; pointer-events:none`, so leaving it
    // unrevealed makes that reason unreachable.
    //
    // The frontier here is Book, so Discover, Bind and Plan are all behind it and all hold no data.
    // This used to make Discover the frontier and assert on Bind and Plan, which worked only while
    // those two led the rail; now that Discover leads, nothing sits behind it and that arrangement
    // could no longer express the rule. The rule itself is unchanged.
    const { result } = renderHook(() => useJourneyPlayback(statesWith(['book']), 'active', 0));
    await settle();
    expect(result.current.revealed.has('discover')).toBe(true);
    expect(result.current.revealed.has('bind')).toBe(true);
    expect(result.current.revealed.has('plan')).toBe(true);
  });

  it('advances as the frontier advances, without replaying what was already shown', async () => {
    const { result, rerender } = renderHook(
      ({ ready }: { ready: PhaseKey[] }) => useJourneyPlayback(statesWith(ready), 'active', 0),
      { initialProps: { ready: ['discover'] as PhaseKey[] } },
    );
    await settle();
    expect(result.current.active).toBe('discover');

    rerender({ ready: ['discover', 'book'] as PhaseKey[] });
    await tick();
    // Reached the new frontier, and Discover was not withdrawn on the way.
    expect(result.current.active).toBe('book');
    expect(result.current.revealed.has('discover')).toBe(true);
    expect(result.current.revealed.size).toBe(indexOf('book') + 1);
  });

  it('catches up instantly rather than performing the sequence', async () => {
    // Rule 2. Arriving at a session that already finished must not narrate it as though it were
    // happening now. No timers are advanced here at all.
    const { result } = renderHook(() => useJourneyPlayback(statesWith(['book']), 'active', 0));
    await tick();
    expect(result.current.revealed.size).toBe(indexOf('book') + 1);
    expect(result.current.walking).toBe(false);
  });

  it('reveals in REGISTRY order, not the order the data arrived', async () => {
    // `book` is later in the registry than `discover`. Both ready at once must still reveal in
    // registry order: the rail is an authored narrative.
    const { result } = renderHook(() =>
      useJourneyPlayback(statesWith(['book', 'discover']), 'active', 0),
    );
    act(() => result.current.replay());
    const seen: PhaseKey[] = [];
    for (let i = 0; i < PHASES.length + 2; i += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(700);
      });
      const current = result.current.active;
      if (current && seen[seen.length - 1] !== current) seen.push(current);
    }
    const positions = seen.map(indexOf);
    expect(positions).toEqual([...positions].sort((a, b) => a - b));
    expect(seen[0]).toBe(keyAt(0));
  });

  it('keeps exactly one rail step active', async () => {
    const { result } = renderHook(() => useJourneyPlayback(statesWith(['discover']), 'active', 0));
    await settle();
    expect(result.current.rail.filter((step) => step.status === 'active')).toHaveLength(1);
  });

  it('never ticks a rail step for a phase that had no data, even once revealed', async () => {
    // This used to hold by accident, while "revealed" and "had data" were the same set. Now that
    // phases behind the frontier reveal without data, the rail has to check for itself — a tick
    // against a phase that never ran reports work that did not happen.
    const { result } = renderHook(() => useJourneyPlayback(statesWith(['discover']), 'active', 0));
    await settle();
    const byKey = new Map(result.current.rail.map((step) => [step.key, step.status]));
    expect(byKey.get('discover')).toBe('active');
    expect(byKey.get('bind')).toBe('idle');
    expect(byKey.get('plan')).toBe('idle');
  });

  it('marks a revealed phase that had data done once it is no longer the active one', async () => {
    const { result } = renderHook(() =>
      useJourneyPlayback(statesWith(['discover', 'book']), 'active', 0),
    );
    await settle();
    const byKey = new Map(result.current.rail.map((step) => [step.key, step.status]));
    expect(byKey.get('book')).toBe('active');
    expect(byKey.get('discover')).toBe('done');
  });

  it('does NOT restart when a watched session completes', async () => {
    // Status flips active -> completed while on screen; a naive implementation keyed on status would
    // replay from the beginning and redraw everything the viewer just watched.
    const states = statesWith(['discover']);
    const { result, rerender } = renderHook(
      ({ status }: { status: string }) => useJourneyPlayback(states, status, 0),
      { initialProps: { status: 'active' } },
    );
    await settle();
    expect(result.current.revealed.has('discover')).toBe(true);

    rerender({ status: 'completed' });
    await tick(2);
    expect(result.current.revealed.has('discover')).toBe(true);
    expect(result.current.mode).toBe('replay');
  });

  it('restarts at the head for a new session, and catches up instantly', async () => {
    const { result, rerender } = renderHook(
      ({ generation, ready }: { generation: number; ready: PhaseKey[] }) =>
        useJourneyPlayback(statesWith(ready), 'active', generation),
      { initialProps: { generation: 0, ready: ['book'] as PhaseKey[] } },
    );
    await settle();
    expect(result.current.active).toBe('book');

    // A shorter session. Switching is not a replay, so it must not walk.
    rerender({ generation: 1, ready: ['discover'] as PhaseKey[] });
    await tick();
    expect(result.current.walking).toBe(false);
    expect(result.current.active).toBe('discover');
    expect(result.current.revealed.has('book')).toBe(false);
  });

  it('reports live for an active session and replay for anything else', () => {
    const states = statesWith([]);
    expect(renderHook(() => useJourneyPlayback(states, 'active', 0)).result.current.mode).toBe(
      'live',
    );
    expect(renderHook(() => useJourneyPlayback(states, 'completed', 0)).result.current.mode).toBe(
      'replay',
    );
    // An unrecognised status is replayed, not treated as live: claiming a finished session is
    // still running is the worse of the two errors.
    expect(renderHook(() => useJourneyPlayback(states, 'weird', 0)).result.current.mode).toBe(
      'replay',
    );
    expect(renderHook(() => useJourneyPlayback(states, undefined, 0)).result.current.mode).toBe(
      'replay',
    );
  });

  it('exposes the frontier phase\u2019s authored glow for the ribbon', async () => {
    const { result } = renderHook(() => useJourneyPlayback(statesWith(['discover']), 'active', 0));
    await settle();
    expect(result.current.active).toBe('discover');
    expect(result.current.glow).toBe(PHASES.find((p) => p.key === 'discover')?.glow);
  });

  it('has no glow before anything is active', () => {
    const { result } = renderHook(() => useJourneyPlayback(statesWith([]), 'active', 0));
    expect(result.current.glow).toBeNull();
  });

  it('produces one rail step per registry phase, whatever the frontier', () => {
    // The rail always shows the whole workflow. Only the FLOW stops at the frontier.
    const { result } = renderHook(() => useJourneyPlayback(statesWith(['discover']), 'active', 0));
    expect(result.current.rail).toHaveLength(PHASES.length);
    expect(result.current.rail.map((s) => s.key)).toEqual(PHASES.map((p) => p.key));
  });

  it('carries the authored ordinal and colour onto each rail step', () => {
    const { result } = renderHook(() => useJourneyPlayback(statesWith([]), 'active', 0));
    result.current.rail.forEach((step, index) => {
      expect(step.n).toBe(PHASES[index]?.n);
      expect(step.col).toBe(PHASES[index]?.col);
    });
  });
});

describe('useJourneyPlayback controls', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('walks only when replayed, and the walk is observable', async () => {
    // The rule stated as a test: `walking` is false on arrival and true only after Replay.
    const { result } = renderHook(() => useJourneyPlayback(statesWith(['book']), 'active', 0));
    await tick();
    expect(result.current.walking).toBe(false);

    act(() => result.current.replay());
    expect(result.current.walking).toBe(true);
    expect(result.current.revealed.size).toBe(0);
  });

  it('takes real time to replay, unlike catching up', async () => {
    const { result } = renderHook(() => useJourneyPlayback(statesWith(['book']), 'active', 0));
    await tick();
    const target = indexOf('book') + 1;
    expect(result.current.revealed.size).toBe(target);

    act(() => result.current.replay());
    // One tick with no time passing reveals at most the first phase, where catching up revealed all.
    await tick(3);
    expect(result.current.revealed.size).toBeLessThan(target);

    await settle();
    expect(result.current.revealed.size).toBe(target);
  });

  it('replays only as far as the frontier', async () => {
    const { result } = renderHook(() => useJourneyPlayback(statesWith(['discover']), 'active', 0));
    act(() => result.current.replay());
    await settle();
    expect(result.current.revealed.size).toBe(indexOf('discover') + 1);
    expect(result.current.active).toBe('discover');
  });

  it('holds the walk while paused and releases it on resume', async () => {
    const { result } = renderHook(() => useJourneyPlayback(statesWith(['book']), 'active', 0));
    act(() => result.current.replay());
    await settle(2);
    const held = result.current.revealed.size;
    expect(held).toBeGreaterThan(0);
    expect(held).toBeLessThan(indexOf('book') + 1);

    act(() => result.current.togglePaused());
    expect(result.current.paused).toBe(true);
    await settle(4);
    // Nothing advanced, and nothing already revealed was withdrawn.
    expect(result.current.revealed.size).toBe(held);

    act(() => result.current.togglePaused());
    await settle();
    expect(result.current.revealed.size).toBe(indexOf('book') + 1);
  });

  it('clears a pause when replayed, so replay cannot leave a dead flow', async () => {
    const { result } = renderHook(() => useJourneyPlayback(statesWith(['book']), 'active', 0));
    act(() => result.current.replay());
    await settle(2);
    act(() => result.current.togglePaused());
    expect(result.current.paused).toBe(true);

    act(() => result.current.replay());
    expect(result.current.paused).toBe(false);
    await settle();
    expect(result.current.revealed.size).toBe(indexOf('book') + 1);
  });

  it('replays without looking like a session switch', async () => {
    // Replay must not bump the generation: the generation identifies which SESSION a reveal set
    // belongs to, and a replay is the same session watched twice. If a replay looked like a switch,
    // the guard that stops a stale poll painting over a newly selected session would lose the
    // distinction it is built on. Observable here as `walking` staying true — a generation change
    // clears it.
    const { result } = renderHook(() => useJourneyPlayback(statesWith(['discover']), 'active', 7));
    await tick();
    act(() => result.current.replay());
    await settle(2);
    expect(result.current.walking).toBe(true);
  });

  it('reports finished against the frontier, not against the whole registry', async () => {
    // Requiring all nine phases would leave Replay permanently disabled for every session this build
    // can produce, since only Discover can hold data.
    const { result } = renderHook(() => useJourneyPlayback(statesWith(['discover']), 'active', 0));
    await settle();
    expect(result.current.finished).toBe(true);
    // Finished with six of the nine phases never revealed, because the session never reached them.
    expect(result.current.revealed.size).toBeLessThan(PHASES.length);

    // And it becomes unfinished again the moment the frontier moves, because there is now more to
    // show than has been shown.
    const { result: mid } = renderHook(() => useJourneyPlayback(statesWith([]), 'active', 0));
    await settle();
    expect(mid.current.finished).toBe(false);
  });

  it('is never finished when the session reached nothing', async () => {
    const { result } = renderHook(() => useJourneyPlayback(statesWith([]), 'active', 0));
    await settle();
    expect(result.current.finished).toBe(false);
  });
});
