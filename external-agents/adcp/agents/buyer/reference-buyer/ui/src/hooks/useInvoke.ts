/**
 * Calling the runtime's JSON actions.
 *
 * One place for the header the platform requires and the SSE framing the responses use, so callers
 * deal in actions and events rather than in transport details.
 */

import { useCallback, useMemo, useRef } from 'react';

import { actionSessionId, invokeUrl } from '../lib/config';
import { createReadInvoke } from '../lib/sessionReads';
import { readSseJson } from '../lib/sse';
import { authHeader, type StoredSession } from '../lib/session';
import type { AppConfig, InvokeEvent } from '../lib/types';

export type InvokeFn = (payload: unknown, signal?: AbortSignal) => Promise<InvokeEvent[]>;

/**
 * Calls the runtime, except for the two session read actions, which go straight to DynamoDB when an
 * identity pool is configured.
 *
 * Wrapped here rather than in each hook: `useJourneySession`, `useSessionViewer` and
 * `useSessionsList` all take an `InvokeFn`, so one wrapper reaches all three and none of their
 * duplicate-message guards are touched. Every other action, chat included, is delegated unchanged.
 *
 * Measured: `get_session_steps` was 1,089 ms through the runtime and is ~24 ms direct, with ~843 ms
 * of the old figure being AgentCore transport rather than work.
 */
export function useInvoke(config: AppConfig | null, session: StoredSession | null): InvokeFn {
  const runtimeInvoke = useRuntimeInvoke(config, session);
  // Announced once, to the console only: a reader cannot act on "the pool is misconfigured", and the
  // dashboard is still correct on the fallback path, just slower. Silence would be worse — an
  // operator wondering why it feels sluggish would have nothing to go on.
  const announced = useRef(false);

  return useMemo(
    () =>
      createReadInvoke(
        config,
        session?.idToken ?? null,
        runtimeInvoke as (payload: unknown, signal?: AbortSignal) => Promise<unknown[]>,
        (reason) => {
          if (announced.current) return;
          announced.current = true;
          console.warn(`[sessions] ${reason}`);
        },
      ) as InvokeFn,
    [config, session?.idToken, runtimeInvoke],
  );
}

/** The plain runtime call, unchanged. Also the fallback the wrapper above delegates to. */
function useRuntimeInvoke(config: AppConfig | null, session: StoredSession | null): InvokeFn {
  return useCallback(
    async (payload: unknown, signal?: AbortSignal) => {
      const header = authHeader(session);
      if (!config || !header) throw new Error('Not signed in.');
      const res = await fetch(invokeUrl(config), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: header,
          // A one-off action belongs to no conversation, but the platform still requires the header
          // and enforces a minimum length on it.
          'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': actionSessionId(),
        },
        body: JSON.stringify(payload),
        ...(signal ? { signal } : {}),
      });
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
      return readSseJson<InvokeEvent>(res.body);
    },
    [config, session],
  );
}
