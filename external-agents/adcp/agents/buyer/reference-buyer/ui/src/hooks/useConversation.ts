/**
 * Runs a turn and keeps the conversation and tool log in step.
 *
 * The turn has two independent sources, because the A2A stream carries text only:
 *
 *   1. the stream itself, which supplies the agent's reply, and
 *   2. recorded reasoning steps, polled while the turn is in flight, which supply tool calls,
 *      tool results and the per-seller payloads.
 *
 * Two details here were learned by debugging the vanilla UI and are preserved deliberately:
 *
 *   - A baseline high-water mark is taken **before** the turn is sent. Steps accumulate for the life
 *     of the session, so polling from -1 replays every earlier turn's tool calls into this turn's
 *     panel, which is what produced several identical cards showing the same duration.
 *   - Polls do not overlap. The interval is shorter than a slow response, and without a guard two
 *     polls read the same index and render the same steps twice, which is exactly the duplicate
 *     message bug that reached production in the vanilla viewer.
 */

import { useCallback, useEffect, useReducer, useRef, useState } from 'react';

import { streamAgentTurn, type ChatTarget } from '../lib/a2a';
import { actionSessionId, invokeUrl } from '../lib/config';
import {
  conversationReducer,
  initialConversation,
  type ConversationState,
  type Message,
} from '../lib/conversation';
import { notRecordedNote, parseFanOut, recordingTrimNote } from '../lib/sellers';
import { readSseJson } from '../lib/sse';
import {
  adoptRuntimeSessionId,
  authHeader,
  newRuntimeSessionId,
  runtimeSessionId,
  type StoredSession,
} from '../lib/session';
import type { AppConfig, InvokeEvent, SessionStep } from '../lib/types';
import type { ToolCall } from './toolLog';
import { toolLogReducer, initialToolLog, type ToolLogState } from './toolLog';

/** How often recorded steps are polled while a turn is running. */
const STEP_POLL_MS = 1000;

export interface ConversationApi {
  conversation: ConversationState;
  toolLog: ToolLogState;
  send: (prompt: string) => void;
  /** Reports when the selected agent's tool activity is not observable, rather than showing none. */
  toolActivityNote: string | null;
  /**
   * Starts a new conversation: clears the transcript and the tool log, and mints a new runtime
   * session id.
   *
   * All three together on purpose. Clearing the transcript alone would leave the agent answering in
   * the context of turns the reader can no longer see, which reads as the agent inventing history.
   */
  startNewSession: () => void;
  /**
   * Resumes a recorded conversation: adopts its session id as the live one and loads its bubbles
   * into the transcript, so the next turn continues it. Returns whether the session id was adopted
   * (false only if it was too short for the platform, which no real recorded id is).
   */
  resume: (sessionId: string, history: readonly Message[]) => boolean;
}

export function useConversation(
  config: AppConfig | null,
  session: StoredSession | null,
  target: ChatTarget | null,
): ConversationApi {
  const [conversation, dispatch] = useReducer(conversationReducer, initialConversation);
  const [toolLog, dispatchTool] = useReducer(toolLogReducer, initialToolLog);
  const [toolActivityNote, setToolActivityNote] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    // Abort an in-flight turn if the component goes away, so its events stop arriving for a view
    // that no longer exists.
    return () => abortRef.current?.abort();
  }, []);

  const postAction = useCallback(
    async (payload: unknown): Promise<InvokeEvent[]> => {
      if (!config || !session) throw new Error('Not signed in.');
      const header = authHeader(session);
      if (!header) throw new Error('Not signed in.');
      const res = await fetch(invokeUrl(config), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: header,
          // A one-off action belongs to no conversation, but the platform still requires the header.
          'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': actionSessionId(),
        },
        body: JSON.stringify(payload),
      });
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
      return readSseJson<InvokeEvent>(res.body);
    },
    [config, session],
  );

  const send = useCallback(
    (prompt: string) => {
      if (!config || !session || !target) return;
      const turnId = crypto.randomUUID();
      const sessionId = runtimeSessionId();
      const controller = new AbortController();
      abortRef.current = controller;

      dispatch({ type: 'send', id: turnId, text: prompt });
      dispatchTool({ type: 'clear' });
      setToolActivityNote(
        target.records_sessions === false
          ? `${target.name} runs outside this project, so its tool calls are not visible here. ` +
              'Only its replies are.'
          : null,
      );

      void (async () => {
        // Read before the turn is sent, so only steps recorded after this point are drawn.
        let sinceIndex = -1;
        const seen = new Set<number>();
        if (target.records_sessions !== false) {
          try {
            const events = await postAction({
              action: 'get_session_steps',
              session_id: sessionId,
              since_index: -1,
            });
            for (const step of stepsFrom(events)) {
              sinceIndex = Math.max(sinceIndex, step.step_index);
            }
          } catch {
            // Baseline unreadable: continue from -1. Earlier turns may then re-render, which is
            // visible and self-explanatory, whereas suppressing this turn's steps would hide real
            // tool activity.
          }
        }

        let polling = false;
        const poll = async () => {
          // The guard that stops two polls rendering the same steps. See the note at the top.
          if (polling || controller.signal.aborted) return;
          polling = true;
          try {
            const events = await postAction({
              action: 'get_session_steps',
              session_id: sessionId,
              since_index: sinceIndex,
            });
            for (const step of stepsFrom(events)) {
              if (seen.has(step.step_index)) continue;
              seen.add(step.step_index);
              sinceIndex = Math.max(sinceIndex, step.step_index);
              applyStep(step, turnId, dispatch, dispatchTool);
            }
          } catch {
            // A missed poll self-corrects on the next one, and the final poll below catches
            // anything the last one did not see.
          } finally {
            polling = false;
          }
        };

        const timer =
          target.records_sessions === false ? null : setInterval(() => void poll(), STEP_POLL_MS);
        if (timer) void poll();

        try {
          await streamAgentTurn({
            target,
            prompt,
            sessionId,
            authHeader: authHeader(session) ?? '',
            runtimeInvokeUrl: invokeUrl(config),
            signal: controller.signal,
            onEvent: (event) => {
              if (event.type === 'text' && typeof event['data'] === 'string') {
                dispatch({ type: 'agentText', id: turnId, text: event['data'] });
              } else if (event.type === 'error') {
                const message =
                  typeof event['message'] === 'string' ? event['message'] : 'Unknown error';
                dispatch({ type: 'turnFailed', id: turnId, message });
              }
            },
          });
          dispatch({ type: 'turnSettled' });
        } catch (err) {
          dispatch({
            type: 'turnFailed',
            id: turnId,
            message: err instanceof Error ? err.message : String(err),
          });
        } finally {
          if (timer) clearInterval(timer);
          await poll();
          // The turn is over, so no result can still arrive for a call left pending. Leaving a
          // spinner running would imply work is in flight; this states what is actually known.
          dispatchTool({ type: 'abandonPending' });
        }
      })();
    },
    [config, session, target, postAction],
  );

  const startNewSession = useCallback(() => {
    // Any turn still streaming belongs to the conversation being left behind, so it is aborted rather
    // than allowed to write into the new one.
    abortRef.current?.abort();
    newRuntimeSessionId();
    dispatch({ type: 'reset' });
    dispatchTool({ type: 'clear' });
    setToolActivityNote(null);
  }, []);

  const resume = useCallback((sessionId: string, history: readonly Message[]) => {
    // Any live turn belongs to the conversation being left, so stop it before adopting another.
    abortRef.current?.abort();
    const adopted = adoptRuntimeSessionId(sessionId);
    // Load the recorded bubbles regardless: seeding the transcript is what makes the resumed
    // conversation read as a continuation. The live tool log starts empty — it tracks new turns'
    // activity; the prior tool calls are already in the transcript as seller-result bubbles.
    dispatch({ type: 'loadHistory', messages: [...history] });
    dispatchTool({ type: 'clear' });
    setToolActivityNote(null);
    return adopted;
  }, []);

  return { conversation, toolLog, send, toolActivityNote, startNewSession, resume };
}

function stepsFrom(events: InvokeEvent[]): SessionStep[] {
  const event = events.find((e) => e.type === 'session_steps');
  if (!event) return [];
  const steps = (event as { steps?: SessionStep[] }).steps;
  return Array.isArray(steps) ? steps : [];
}

/** Turns one recorded step into conversation and tool-log updates. */
function applyStep(
  step: SessionStep,
  turnId: string,
  dispatch: React.Dispatch<Parameters<typeof conversationReducer>[1]>,
  dispatchTool: React.Dispatch<Parameters<typeof toolLogReducer>[1]>,
): void {
  const content = step.content ?? {};

  if (step.step_type === 'tool_call') {
    const call: ToolCall = {
      id: content.callId ?? `step-${step.step_index}`,
      toolName: content.toolName ?? 'tool',
      status: 'pending',
      input: content.input,
    };
    dispatchTool({ type: 'started', call });
    return;
  }

  if (step.step_type !== 'tool_result') {
    // Only tool-side steps are rendered: the agent's own text is already streaming into its bubble,
    // so rendering the recorded `response` step as well would duplicate it.
    return;
  }

  const callId = content.callId ?? `step-${step.step_index}`;
  const unrecorded = notRecordedNote(content);
  if (unrecorded !== '') {
    // The tool did run; what is missing is the record of its result. Calling that an error would
    // blame the tool.
    dispatchTool({ type: 'notRecorded', callId, note: unrecorded });
    return;
  }

  const entries = parseFanOut(content.output);
  dispatchTool({
    type: 'finished',
    callId,
    // Spread rather than passed as possibly-undefined: with exactOptionalPropertyTypes, "absent"
    // and "present but undefined" are different, and only the first means "fall back to the name
    // from the matching tool_call step".
    ...(content.toolName !== undefined ? { toolName: content.toolName } : {}),
    output: content.output,
    ...(content.durationMs !== undefined ? { durationMs: content.durationMs } : {}),
    ok: content.status === 'success',
    trimNote: recordingTrimNote(content),
    ...(entries ? { entries } : {}),
  });

  if (entries) {
    dispatch({
      type: 'sellerResults',
      toolName: content.toolName ?? '',
      entries,
      idPrefix: `${turnId}-${step.step_index}`,
    });
  }
}
