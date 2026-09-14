import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useSessionViewer } from './useSessionViewer';
import type { InvokeEvent, SessionStep } from '../lib/types';

/**
 * The duplicate-message bug, guarded at the hook level.
 *
 * The vanilla viewer polled on a 2s interval around a request that regularly took longer, with no
 * in-flight guard. A second tick read the same step index, asked for the same range and rendered it
 * again, so every message appeared exactly twice with identical text and timestamp. These tests drive
 * the real hook with a deliberately slow transport, which is the condition that triggered it.
 */

const STEPS: SessionStep[] = [
  {
    step_index: 0,
    step_type: 'incoming_request',
    content: { from: 'External Agent', text: 'Find inventory' },
  },
  { step_index: 1, step_type: 'thought', content: { text: 'considering' } },
  { step_index: 2, step_type: 'response', content: { to: 'External Agent', text: 'here you go' } },
];

/** An invoke that answers after `delayMs`, honouring since_index, and counts its calls. */
function slowInvoke(delayMs: number) {
  const calls: number[] = [];
  const fn = vi.fn(async (payload: unknown): Promise<InvokeEvent[]> => {
    const since = (payload as { since_index: number }).since_index;
    calls.push(since);
    await new Promise((resolve) => setTimeout(resolve, delayMs));
    return [
      {
        type: 'session_steps',
        meta: { session_id: 's1', status: 'completed', invoker: 'External Agent' },
        steps: STEPS.filter((s) => s.step_index > since),
      },
    ];
  });
  return { fn, calls };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('useSessionViewer', () => {
  it('renders each recorded step exactly once', async () => {
    const { fn } = slowInvoke(10);
    const { result } = renderHook(() => useSessionViewer(fn));

    act(() => result.current.view('s1'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });

    expect(result.current.messages.map((m) => m.kind)).toEqual(['user', 'thought', 'agent']);
  });

  it('does not start a second poll while one is outstanding', async () => {
    // 5s response against a 2s interval: two ticks fire before the first answer lands.
    const { fn, calls } = slowInvoke(5000);
    const { result } = renderHook(() => useSessionViewer(fn));

    act(() => result.current.view('s1'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4500);
    });

    // Without the guard this would be three requests, all asking from -1.
    expect(calls).toEqual([-1]);
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('renders nothing twice even across many overlapping ticks', async () => {
    const { fn } = slowInvoke(5000);
    const { result } = renderHook(() => useSessionViewer(fn));

    act(() => result.current.view('s1'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20000);
    });

    const ids = result.current.messages.map((m) => m.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('advances the step mark so a later poll asks only for what is new', async () => {
    const { fn, calls } = slowInvoke(10);
    const { result } = renderHook(() => useSessionViewer(fn));

    act(() => result.current.view('s1'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    expect(calls[0]).toBe(-1);
    // Everything after the first request asks from the high-water mark, not from the start.
    expect(calls.slice(1).every((since) => since === 2)).toBe(true);
  });

  it('replays a different session from scratch without mixing in the previous one', async () => {
    const { fn } = slowInvoke(10);
    const { result } = renderHook(() => useSessionViewer(fn));

    act(() => result.current.view('s1'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });
    expect(result.current.messages).toHaveLength(3);

    act(() => result.current.view('s2'));
    expect(result.current.messages).toHaveLength(0);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });
    expect(result.current.messages).toHaveLength(3);
  });

  it('a poll left over from the previous session does not block the new one', async () => {
    // The hole in the first version of this fix: the stale poll released the guard, or held it.
    const { fn, calls } = slowInvoke(3000);
    const { result } = renderHook(() => useSessionViewer(fn));

    act(() => result.current.view('s1'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    act(() => result.current.view('s2'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    // The new session got its own request, asking from the start.
    expect(calls.filter((since) => since === -1).length).toBeGreaterThanOrEqual(2);
    expect(result.current.viewedSessionId).toBe('s2');
  });

  it('stops polling once the viewer is closed', async () => {
    const { fn } = slowInvoke(10);
    const { result } = renderHook(() => useSessionViewer(fn));

    act(() => result.current.view('s1'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });
    const before = fn.mock.calls.length;

    act(() => result.current.stop());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });

    expect(fn.mock.calls.length).toBe(before);
    expect(result.current.messages).toHaveLength(0);
  });

  it('says a session has expired rather than spinning on Loading', async () => {
    const fn = vi.fn(async (): Promise<InvokeEvent[]> => [
      { type: 'session_steps', meta: null, steps: [] },
    ]);
    const { result } = renderHook(() => useSessionViewer(fn));

    act(() => result.current.view('gone'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });

    // No META record at all means expired or never existed, which is not the same as "no steps yet".
    expect(result.current.status).toMatch(/No record found/);
  });

  it('distinguishes a live session with no steps yet from an expired one', async () => {
    const fn = vi.fn(async (): Promise<InvokeEvent[]> => [
      { type: 'session_steps', meta: { session_id: 's1', status: 'active' }, steps: [] },
    ]);
    const { result } = renderHook(() => useSessionViewer(fn));

    act(() => result.current.view('s1'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });

    expect(result.current.status).toMatch(/No steps recorded yet/);
  });

  it('surfaces a failed turn message from the meta record, once', async () => {
    const fn = vi.fn(async (): Promise<InvokeEvent[]> => [
      {
        type: 'session_steps',
        meta: { session_id: 's1', status: 'error', error_message: 'RuntimeError: boom' },
        steps: [],
      },
    ]);
    const { result } = renderHook(() => useSessionViewer(fn));

    act(() => result.current.view('s1'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });

    // On the meta record rather than in the steps, so without this the transcript just stops.
    const notes = result.current.messages.filter((m) => m.kind === 'note');
    expect(notes).toHaveLength(1);
    expect(notes[0]?.kind === 'note' ? notes[0].text : '').toContain('boom');
  });

  it('admits it has stopped updating after a run of failures', async () => {
    const fn = vi.fn(async (): Promise<InvokeEvent[]> => {
      throw new Error('HTTP 503');
    });
    const { result } = renderHook(() => useSessionViewer(fn));

    act(() => result.current.view('s1'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });

    // A frozen transcript that looks complete is worse than one that says it is stale.
    expect(result.current.staleNote).toMatch(/stopped updating/);
    expect(result.current.staleNote).toContain('HTTP 503');
  });

  it('does not warn about a single missed poll', async () => {
    let calls = 0;
    const fn = vi.fn(async (): Promise<InvokeEvent[]> => {
      calls += 1;
      if (calls === 1) throw new Error('transient');
      return [{ type: 'session_steps', meta: { session_id: 's1', status: 'completed' }, steps: STEPS }];
    });
    const { result } = renderHook(() => useSessionViewer(fn));

    act(() => result.current.view('s1'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    expect(result.current.staleNote).toBeNull();
    // Asserted directly rather than with waitFor: waitFor polls on real timers, which never advance
    // while fake ones are installed, so it would simply time out.
    expect(result.current.messages.length).toBeGreaterThan(0);
  });
});
