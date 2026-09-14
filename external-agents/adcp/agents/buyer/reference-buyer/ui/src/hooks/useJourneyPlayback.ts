/**
 * Sequencing the phase reveals.
 *
 * Two rules govern everything here.
 *
 * **1. The flow shows how far the session got, and stops there.** The frontier is the last phase with
 * data; phases at or behind it are revealed, phases beyond it are not. The view waits at the frontier
 * and advances when new data arrives. It never runs ahead of what happened.
 *
 * **2. The staggered walk is only ever the result of Replay.** Catching up — on first load, on a
 * session switch, or as a new phase arrives — happens at once. Each panel still animates itself in,
 * because `.panel` carries a 0.6s opacity and transform transition; what Replay adds is the 640ms
 * pause BETWEEN phases, which is the part that reads as a performance. Performing the sequence
 * unprompted narrates a journey as though it were happening now when it already happened.
 *
 * Reveals follow REGISTRY order rather than the order data arrived. A seller answering
 * `list_creative_formats` before `get_products` must not make Creative appear before Discover: the
 * rail is an authored narrative, not a race result.
 *
 * The rule that needs a test rather than care: a session completing WHILE BEING WATCHED must not
 * restart. Its status flips from `active` to `completed`, and a naive implementation keyed on status
 * would re-enter replay from the beginning and redraw everything the viewer just watched. Revealed
 * phases stay revealed.
 *
 * Timers are tagged with the session generation and checked at the point of effect. Clearing timers
 * on a switch is not sufficient on its own: a scheduled callback that has already fired but not yet
 * applied has no timer left to clear.
 */

import { useEffect, useMemo, useRef, useState } from 'react';

import { PHASES, type PhaseDefinition, type PhaseKey } from '../lib/journey/phases';
import type { PhaseStates } from '../lib/journey/types';

/** Delay between one phase revealing and the next being considered. The prototype's own pacing. */
export const REVEAL_INTERVAL_MS = 640;

export type PlaybackMode = 'live' | 'replay';

export interface RailStep {
  readonly key: PhaseKey;
  readonly n: string;
  readonly name: string;
  readonly col: string;
  readonly status: 'idle' | 'active' | 'done';
}

export interface PlaybackState {
  readonly revealed: ReadonlySet<PhaseKey>;
  /** The phase currently animating, or null. Exactly one at a time. */
  readonly active: PhaseKey | null;
  readonly mode: PlaybackMode;
  readonly rail: readonly RailStep[];
  /** The active phase's authored glow, for the ribbon. Null when nothing is active. */
  readonly glow: string | null;
  /** Whether the sequence is held. Nothing already revealed is withdrawn while paused. */
  readonly paused: boolean;
  /** Everything the session reached has been revealed, so there is a run to replay. */
  readonly finished: boolean;
  /** True only during a Replay walk. Catching up to a session's current state is not a walk. */
  readonly walking: boolean;
  /** Re-run the reveal sequence for the same session from the beginning. */
  readonly replay: () => void;
  readonly togglePaused: () => void;
}

function modeFor(status: string | undefined): PlaybackMode {
  // Anything other than an explicitly active session is replayed. An unrecognised status is not
  // treated as live, because claiming a finished session is still running is the worse error.
  return status === 'active' ? 'live' : 'replay';
}

/** Whether the reader has asked for animation to be suppressed. */
function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

export function useJourneyPlayback(
  states: PhaseStates,
  sessionStatus: string | undefined,
  generation: number,
): PlaybackState {
  const [revealed, setRevealed] = useState<ReadonlySet<PhaseKey>>(new Set());
  const [paused, setPaused] = useState(false);
  /**
   * Whether the staggered walk is running.
   *
   * False by default, and set true only by `replay`. That is the user-facing rule this hook exists to
   * hold: **the flow-through animation is only ever the result of Replay.** Catching up to where a
   * session already is happens at once.
   */
  const [walking, setWalking] = useState(false);

  /** The generation the current reveal set belongs to. */
  const revealGeneration = useRef(generation);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // A new session clears the reveal set. Keys on `generation`, NOT on session status: a session
  // completing mid-watch keeps its generation and therefore keeps what it has revealed.
  useEffect(() => {
    if (revealGeneration.current === generation) return;
    revealGeneration.current = generation;
    setRevealed(new Set());
    // A new session catches up instantly rather than performing. Switching sessions is not a replay.
    setWalking(false);
    setPaused(false);
    if (timer.current !== null) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  }, [generation]);

  /**
   * How far the session has actually got: the LAST phase in registry order that has data.
   *
   * This is the frontier, and it is the whole of what the view is allowed to show. Nothing beyond it
   * has happened, so nothing beyond it appears — the flow stops where the session stopped and waits
   * there. `-1` means no phase has data yet, and then the flow is empty, which is also what the
   * prototype shows before anything runs.
   *
   * Phases BEHIND the frontier reveal whether or not they hold data. That distinction is the point:
   * a phase the session passed without data has a reason worth reading ("this build cannot call
   * sync_governance"), while a phase the session has not reached yet has nothing to say beyond its own
   * absence. Revealing the first and withholding the second is what makes the flow report progress
   * rather than either hiding it or overstating it.
   *
   * Two defects sit on either side of this line, and both were real:
   *   - revealing ONLY phases with data left the passed-over ones at `opacity:0; pointer-events:none`,
   *     so the control disclosing why a panel was empty could not be reached at all;
   *   - revealing EVERY phase ran the animation through the entire lifecycle to Activate and parked
   *     on an empty panel, reporting a journey that had not happened.
   */
  const frontierIndex = useMemo(() => {
    let last = -1;
    PHASES.forEach((phase, index) => {
      if (states.get(phase.key)?.state === 'ready') last = index;
    });
    return last;
  }, [states]);

  /**
   * The next phase to reveal: the first unrevealed phase AT OR BEHIND the frontier.
   *
   * Reduced to a key rather than an object, so the reveal effect below depends on a primitive. The
   * caller may build a fresh `states` map on every render, and an effect keyed on object identity
   * would then tear down and re-arm its timer on every render and never fire.
   */
  const nextKey = useMemo<PhaseKey | null>(() => {
    const next = PHASES.slice(0, frontierIndex + 1).find((phase) => !revealed.has(phase.key));
    return next?.key ?? null;
  }, [revealed, frontierIndex]);

  useEffect(() => {
    if (nextKey === null) return;
    // Paused holds the sequence where it is. Nothing already revealed is withdrawn.
    if (paused) return;

    const scheduledGeneration = generation;
    const reveal = () => {
      // Checked at the point of EFFECT. A callback that already fired for a previous session has no
      // timer left to clear, so the tag is the only thing that can stop it painting here.
      if (scheduledGeneration !== revealGeneration.current) return;
      setRevealed((current) => new Set([...current, nextKey]));
      timer.current = null;
    };

    // The staggered walk belongs to Replay and to nothing else.
    //
    // Arriving at a session mid-flight, or on a session that finished before anyone looked, must not
    // perform the sequence: it would animate through phases as though they were happening now, when
    // they already happened. So catching up is instant. Each panel still animates itself in, because
    // `.panel` carries a 0.6s opacity and transform transition — what is dropped is the 640ms pause
    // BETWEEN phases, which is the part that reads as a performance.
    //
    // Reduced motion takes the same path, so there is one behaviour to reason about rather than two.
    if (!walking || prefersReducedMotion()) {
      reveal();
      return;
    }

    timer.current = setTimeout(reveal, revealed.size === 0 ? 0 : REVEAL_INTERVAL_MS);
    return () => {
      if (timer.current !== null) {
        clearTimeout(timer.current);
        timer.current = null;
      }
    };
  }, [nextKey, generation, paused, walking, revealed.size]);

  /**
   * The phase currently lit: the LAST revealed one in registry order.
   *
   * Derived rather than stored. Holding it as its own state meant it could disagree with `revealed`,
   * and it did: clearing it when nothing was left to reveal switched the lamp off the moment the
   * final phase arrived, so a finished run showed no active phase at all.
   */
  const active = useMemo<PhaseKey | null>(() => {
    let latest: PhaseKey | null = null;
    for (const phase of PHASES) if (revealed.has(phase.key)) latest = phase.key;
    return latest;
  }, [revealed]);

  const mode = modeFor(sessionStatus);

  /**
   * The rail.
   *
   * `done` requires that the phase actually HAD data. Being revealed is not enough, and the
   * difference used to be invisible: while only phases with data were revealed, "revealed" and "had
   * data" were the same set, so this promise held by accident. Now that every phase reveals, it has
   * to be checked here. A tick against a phase that never ran reports work that did not happen.
   *
   * So a phase with no data passes through `active` while it is being narrated and returns to `idle`
   * behind the sequence, never to `done`.
   */
  const rail = useMemo<readonly RailStep[]>(
    () =>
      PHASES.map((phase: PhaseDefinition): RailStep => {
        const hadData = states.get(phase.key)?.state === 'ready';
        let status: RailStep['status'] = 'idle';
        if (phase.key === active) status = 'active';
        else if (revealed.has(phase.key) && hadData) status = 'done';
        return { key: phase.key, n: phase.n, name: phase.name, col: phase.col, status };
      }),
    [active, revealed, states],
  );

  const glow = useMemo(() => {
    if (active === null) return null;
    return PHASES.find((phase) => phase.key === active)?.glow ?? null;
  }, [active]);

  /**
   * Everything the session has reached has been revealed, so there is something to replay.
   *
   * Measured against the FRONTIER, not against the registry: a discovery-only session is complete at
   * Discover, and requiring all nine phases would leave Replay permanently disabled for every session
   * this build can actually produce.
   */
  const finished = frontierIndex >= 0 && revealed.size === frontierIndex + 1;

  /**
   * Replay: clear the reveal set and let the sequence run again.
   *
   * Deliberately NOT bumping the generation. The generation identifies which SESSION a reveal set
   * belongs to, and a replay is the same session watched twice. Bumping it here would make a replay
   * indistinguishable from a session switch, and the guard that stops a stale poll painting over a
   * new session is built on exactly that distinction.
   */
  const replay = useMemo(
    () => () => {
      if (timer.current !== null) {
        clearTimeout(timer.current);
        timer.current = null;
      }
      setPaused(false);
      setWalking(true);
      setRevealed(new Set());
    },
    [],
  );

  const togglePaused = useMemo(() => () => setPaused((held) => !held), []);

  return { revealed, active, mode, rail, glow, paused, finished, walking, replay, togglePaused };
}
