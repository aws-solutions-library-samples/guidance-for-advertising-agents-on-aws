/**
 * One runtime session id per tab, used by conversations AND by JSON actions.
 *
 * ## The bug this pins
 *
 * `actionSessionId()` returned a fresh `crypto.randomUUID()` on every call, reasoning that "a one-off
 * JSON action belongs to no conversation". Defensible on paper, expensive in practice: every JSON
 * action goes through `useInvoke`, so the step poll minted a brand new AgentCore runtime session on
 * every tick.
 *
 * Measured against the deployed runtime, same session, ten polls each:
 *
 *     fresh id per poll    p50 1079 ms   p90 9929 ms   max 9955 ms
 *     one id, reused       p50  518 ms   p90  618 ms   max 1075 ms
 *     no header at all     p50  930 ms   p90  959 ms   max  976 ms
 *
 * The p90 is what a reader actually felt. A ~10s poll sitting behind the single-flight guard freezes
 * the dashboard for ten seconds — which is what "the dashboard is slow to pick up changes" was.
 *
 * A tab holds one conversation, so it has one session id. Reads against that session are part of it,
 * not separate conversations.
 */
import { beforeEach, describe, expect, it } from 'vitest';
import { actionSessionId } from './config';
import {
  MIN_RUNTIME_SESSION_ID_LENGTH,
  newRuntimeSessionId,
  runtimeSessionId,
} from './session';

beforeEach(() => {
  sessionStorage.clear();
});

describe('actionSessionId', () => {
  it('returns the SAME id across calls, not a fresh uuid', () => {
    // The regression itself. This fails against the old implementation.
    expect(actionSessionId()).toBe(actionSessionId());
  });

  it('is the tab\'s conversation session id, not a parallel one', () => {
    // If actions used their own id, the runtime would route them to a different session from the
    // conversation they are reading — paying a cold route every time.
    expect(actionSessionId()).toBe(runtimeSessionId());
  });

  it('still satisfies the platform minimum length', () => {
    // AgentCore rejects anything shorter outright, so this is not cosmetic.
    expect(actionSessionId().length).toBeGreaterThanOrEqual(MIN_RUNTIME_SESSION_ID_LENGTH);
  });

  it('follows the conversation when a new session is deliberately started', () => {
    const before = actionSessionId();
    const fresh = newRuntimeSessionId();

    expect(fresh).not.toBe(before);
    // Actions must not keep reading the old session after the reader starts a new conversation.
    expect(actionSessionId()).toBe(fresh);
  });
});
