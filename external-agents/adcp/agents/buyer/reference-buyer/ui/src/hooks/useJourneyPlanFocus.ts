/**
 * Following a PLAN rather than a session, for the journey view.
 *
 * A plan is worked on across one or more conversations, so keying the journey on the session shows
 * only the slice of a plan that happened in the newest conversation. This hook lets the reader focus
 * a plan instead: it lists the recent plans (for a picker) and, when one is focused, polls the
 * plan's steps merged across every session it appeared in (`get_plan_steps`).
 *
 * It is a LAYER over `useJourneySession`, not a replacement: with no plan focused the journey follows
 * the newest session exactly as before (which is also the only thing that can show the pre-plan
 * phases — Discover, Accounts, Bind — since those carry no plan_id). Focusing a plan is an explicit
 * act by the reader.
 *
 * The plan-steps poll is a full replace, not an incremental append: `get_plan_steps` returns the
 * whole merged, time-ordered set each call (a plan's step count is small and TTL'd at 24h), so there
 * is no per-step high-water mark to get wrong. The one guard kept from the session hook is the
 * poll-owner tag, so a poll left over from a plan the reader has switched away from cannot paint into
 * the new one.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import {
  findErrorMessage,
  findPlanStepsEvent,
  findPlansEvent,
  type PlanSummary,
  type SessionMeta,
  type SessionStep,
} from '../lib/types';
import type { InvokeFn } from './useInvoke';

const LIST_POLL_MS = 5000;
const STEP_POLL_MS = 1000;
const PLAN_LIST_LIMIT = 50;

export interface JourneyPlanFocusState {
  /** Recent plans, most-recently-worked-on first, for the picker. */
  readonly plans: readonly PlanSummary[];
  /** The focused plan, or null when the journey is following the newest session. */
  readonly planId: string | null;
  /** The focused plan's steps, merged across its sessions, time-ordered. Empty when unfocused. */
  readonly steps: readonly SessionStep[];
  /** META of the plan's most recently updated session, for the header. */
  readonly meta: SessionMeta | null;
  /** The sessions the focused plan spans. */
  readonly sessions: readonly string[];
  /** Increments on every plan switch, so playback resets when the focus changes. */
  readonly generation: number;
  /** What to say instead of a journey while a plan is focused (loading / nothing recorded). */
  readonly status: string | null;
  /** Why the plan list could not be read, or null. */
  readonly listError: string | null;
  readonly focusPlan: (planId: string) => void;
  /** Return to following the newest session. */
  readonly clearPlan: () => void;
}

export function useJourneyPlanFocus(invoke: InvokeFn, enabled: boolean): JourneyPlanFocusState {
  const [plans, setPlans] = useState<PlanSummary[]>([]);
  const [planId, setPlanId] = useState<string | null>(null);
  const [steps, setSteps] = useState<SessionStep[]>([]);
  const [meta, setMeta] = useState<SessionMeta | null>(null);
  const [sessions, setSessions] = useState<string[]>([]);
  const [generation, setGeneration] = useState(0);
  const [status, setStatus] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);

  const listInFlight = useRef(false);
  const stepInFlight = useRef(false);
  /** Which plan the in-flight step poll belongs to, so a stale one cannot paint into a new focus. */
  const stepOwner = useRef<string | null>(null);

  const focusPlan = useCallback((next: string) => {
    setPlanId((current) => {
      if (current === next) return current;
      setSteps([]);
      setMeta(null);
      setSessions([]);
      setStatus('Loading plan\u2026');
      stepInFlight.current = false;
      stepOwner.current = null;
      setGeneration((g) => g + 1);
      return next;
    });
  }, []);

  const clearPlan = useCallback(() => {
    setPlanId((current) => {
      if (current === null) return current;
      setSteps([]);
      setMeta(null);
      setSessions([]);
      setStatus(null);
      stepInFlight.current = false;
      stepOwner.current = null;
      setGeneration((g) => g + 1);
      return null;
    });
  }, []);

  // --- the plan list --------------------------------------------------------------------------
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;

    const poll = async () => {
      if (listInFlight.current) return;
      listInFlight.current = true;
      try {
        const events = await invoke({ action: 'list_plans', limit: PLAN_LIST_LIMIT });
        if (cancelled) return;
        const event = findPlansEvent(events);
        if (!event) {
          const message = findErrorMessage(events);
          if (message !== null) setListError(message);
          return;
        }
        setPlans(Array.isArray(event.plans) ? event.plans : []);
        setListError(null);
      } catch (err) {
        if (cancelled) return;
        setListError(err instanceof Error ? err.message : 'Could not read the plan list.');
      } finally {
        listInFlight.current = false;
      }
    };

    void poll();
    const timer = setInterval(() => void poll(), LIST_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [enabled, invoke]);

  // --- the focused plan's steps ---------------------------------------------------------------
  useEffect(() => {
    if (!enabled) return;
    const current = planId;
    if (current === null) return;
    let cancelled = false;

    const poll = async () => {
      if (stepInFlight.current) return;
      stepInFlight.current = true;
      stepOwner.current = current;
      try {
        const events = await invoke({ action: 'get_plan_steps', plan_id: current });
        if (cancelled) return;
        const event = findPlanStepsEvent(events);
        if (!event) {
          const message = findErrorMessage(events);
          if (message !== null) throw new Error(message);
          return;
        }
        // Full replace: the backend returns the whole merged, time-ordered set each call.
        const incoming = Array.isArray(event.steps) ? event.steps : [];
        setSteps(incoming);
        setMeta(event.meta ?? null);
        setSessions(Array.isArray(event.sessions) ? event.sessions : []);
        setStatus(
          incoming.length > 0
            ? null
            : 'No steps recorded for this plan yet. It may be removed 24 hours after it starts.',
        );
      } catch {
        // A missed poll self-corrects on the next tick; the plan view is a full replace, so a single
        // failure does not accumulate a wrong transcript.
      } finally {
        if (stepOwner.current === current) stepInFlight.current = false;
      }
    };

    void poll();
    const timer = setInterval(() => void poll(), STEP_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [enabled, invoke, planId]);

  return {
    plans,
    planId,
    steps,
    meta,
    sessions,
    generation,
    status,
    listError,
    focusPlan,
    clearPlan,
  };
}
