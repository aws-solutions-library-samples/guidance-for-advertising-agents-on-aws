/**
 * Turning recorded steps into a transcript.
 *
 * A recorded conversation is rendered with the same bubbles a live one uses, so a remotely-initiated
 * conversation reads as a conversation rather than as a list of cards. The step types map as:
 *
 *   incoming_request -> the caller's message
 *   thought          -> the model's interleaved reasoning, labelled as such
 *   tool_call        -> a pending entry in the tool log
 *   tool_result      -> that entry completed, plus the seller payloads inline in the transcript
 *   response         -> the agent's reply, markdown-rendered
 *
 * Pure, and incremental: it takes the steps it has not yet applied and returns the additions. That
 * shape is what makes the poll safe, since applying the same step twice is what produced duplicate
 * messages in the vanilla viewer.
 */

import type { Message } from './conversation';
import { notRecordedNote, parseFanOut, recordingTrimNote } from './sellers';
import { stepTimestampLabel } from './sessions';
import type { SessionStep } from './types';
import type { ToolLogAction } from '../hooks/toolLog';

export interface ReplayAdditions {
  messages: Message[];
  toolActions: ToolLogAction[];
}

/**
 * Converts steps into transcript messages and tool-log actions.
 *
 * The seller name comes from each response rather than from the session's recorded
 * `seller_agent_id`: a fan-out turn queried every sales agent, so a single recorded seller would
 * mislabel most of the entries.
 */
export function replaySteps(steps: SessionStep[]): ReplayAdditions {
  const messages: Message[] = [];
  const toolActions: ToolLogAction[] = [];

  for (const step of steps) {
    const content = step.content ?? {};
    const time = stepTimestampLabel(step.timestamp);
    const withTime = (label: string) => (time ? `${label} · ${time}` : label);
    const id = `step-${step.step_index}`;

    switch (step.step_type) {
      case 'incoming_request':
        messages.push({
          kind: 'user',
          id,
          text: content.text ?? '',
          label: withTime(content.from ?? 'Unknown caller'),
        });
        break;

      case 'thought':
        messages.push({
          kind: 'thought',
          id,
          text: content.text ?? '',
          label: withTime('Agent, reasoning'),
        });
        break;

      case 'tool_call':
        toolActions.push({
          type: 'started',
          call: {
            id: content.callId ?? id,
            toolName: content.toolName ?? 'tool',
            status: 'pending',
            input: content.input,
          },
        });
        break;

      case 'tool_result': {
        const callId = content.callId ?? id;
        const unrecorded = notRecordedNote(content);
        if (unrecorded !== '') {
          toolActions.push({ type: 'notRecorded', callId, note: unrecorded });
          break;
        }
        const entries = parseFanOut(content.output);
        toolActions.push({
          type: 'finished',
          callId,
          ...(content.toolName !== undefined ? { toolName: content.toolName } : {}),
          output: content.output,
          ...(content.durationMs !== undefined ? { durationMs: content.durationMs } : {}),
          ok: content.status === 'success',
          trimNote: recordingTrimNote(content),
          ...(entries ? { entries } : {}),
        });
        if (entries) {
          entries.forEach((entry, index) => {
            messages.push({
              kind: 'seller',
              id: `${id}-seller-${index}`,
              toolName: content.toolName ?? '',
              sellerName: entry.seller_name ?? entry.seller_id ?? '',
              payload:
                'error' in entry && entry.error !== undefined
                  ? { error: entry.error }
                  : entry.response,
            });
          });
        }
        break;
      }

      case 'response':
        messages.push({
          kind: 'agent',
          id,
          text: content.text ?? '',
          // Already complete: this is a record, not a stream, so no caret.
          streaming: false,
          label: withTime(`Agent → ${content.to ?? 'caller'}`),
        });
        break;

      default:
        // An unrecognised step type is skipped rather than rendered as an empty bubble. Steps are
        // written by services that may add types this build does not know about.
        break;
    }
  }

  return { messages, toolActions };
}

/**
 * The highest step index in a batch, or the current mark when the batch is empty.
 *
 * Callers advance their mark with this so the same step is never applied twice.
 */
export function highestStepIndex(steps: SessionStep[], current: number): number {
  return steps.reduce(
    (max, step) => (Number.isFinite(step.step_index) ? Math.max(max, step.step_index) : max),
    current,
  );
}
