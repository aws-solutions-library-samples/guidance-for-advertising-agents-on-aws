import { describe, expect, it } from 'vitest';

import {
  agentLabelFor,
  invokerLabelFor,
  isUncorrelatedToolCall,
  SESSION_BRIEF_MAX_CHARS,
  sessionBriefLabel,
  sessionOptionLabel,
  sessionStartedLabel,
  sessionStatusTone,
  shortSessionId,
  stepTimestampLabel,
} from './sessions';
import type { SessionMeta } from './types';

const meta = (over: Partial<SessionMeta> = {}): SessionMeta => ({
  session_id: 'adcp-buyer-agent-a2a-1234567890abcdef',
  status: 'completed',
  ...over,
});

describe('shortSessionId', () => {
  it('elides the middle of a long id', () => {
    expect(shortSessionId('adcp-buyer-agent-a2a-1234567890abcdef')).toBe('adcp-buy…cdef');
  });

  it('leaves a short id alone', () => {
    expect(shortSessionId('short-id')).toBe('short-id');
  });

  it('handles absence', () => {
    expect(shortSessionId(null)).toBe('');
    expect(shortSessionId(undefined)).toBe('');
  });
});

describe('agentLabelFor', () => {
  it('prefers the reported name, then the id', () => {
    expect(agentLabelFor(meta({ agent_name: 'Buyer (A2A)', agent_id: 'buyer-a2a' }))).toBe(
      'Buyer (A2A)',
    );
    expect(agentLabelFor(meta({ agent_id: 'buyer-a2a' }))).toBe('buyer-a2a');
  });

  it('says unknown rather than assuming it was the buyer agent', () => {
    // Several agents record into one store, so attribution by assumption would be wrong.
    expect(agentLabelFor(meta())).toBe('unknown agent');
  });
});

describe('invokerLabelFor', () => {
  it('names the caller, or says it is unknown', () => {
    expect(invokerLabelFor(meta({ invoker: 'External Agent' }))).toBe('External Agent');
    expect(invokerLabelFor(meta())).toBe('Unknown invoker');
  });
});

describe('sessionOptionLabel', () => {
  it('carries agent, invoker, preview and status', () => {
    const label = sessionOptionLabel(
      meta({
        agent_name: 'Buyer (A2A)',
        invoker: 'External Agent',
        request_preview: 'Find display inventory',
        status: 'active',
      }),
    );
    expect(label).toBe('Buyer (A2A) · External Agent — Find display inventory [active]');
  });

  it('truncates a long preview', () => {
    const label = sessionOptionLabel(meta({ request_preview: 'x'.repeat(60) }));
    expect(label).toContain('…');
    expect(label).toContain('x'.repeat(32));
    expect(label).not.toContain('x'.repeat(33));
  });
});

describe('sessionStartedLabel', () => {
  it('shows a time for today and a full date for older records', () => {
    const today = sessionStartedLabel(meta({ started_at: new Date().toISOString() }));
    expect(today).not.toContain('/');
    const older = sessionStartedLabel(meta({ started_at: '2020-01-02T03:04:05Z' }));
    // A bare time is ambiguous once a record is more than a day old.
    expect(older.length).toBeGreaterThan(today.length);
  });

  it('says unknown rather than showing the epoch', () => {
    expect(sessionStartedLabel(meta())).toBe('unknown');
    expect(sessionStartedLabel(meta({ started_at: 'not a date' }))).toBe('unknown');
  });
});

describe('stepTimestampLabel', () => {
  it('formats a valid timestamp and returns nothing for an invalid one', () => {
    expect(stepTimestampLabel('2026-08-06T12:00:00Z')).not.toBe('');
    expect(stepTimestampLabel('nonsense')).toBe('');
    expect(stepTimestampLabel(undefined)).toBe('');
  });
});

describe('sessionStatusTone', () => {
  it('maps the statuses the recorder writes', () => {
    expect(sessionStatusTone('active')).toEqual({ tone: 'live', label: 'live' });
    expect(sessionStatusTone('completed')).toEqual({ tone: 'completed', label: 'completed' });
    expect(sessionStatusTone('error')).toEqual({ tone: 'error', label: 'error' });
  });

  it('does not default an unrecognised status', () => {
    // Reporting an unknown status as completed would claim the conversation finished, on no evidence.
    expect(sessionStatusTone('weird')).toEqual({ tone: 'unknown', label: 'weird' });
    expect(sessionStatusTone(undefined)).toEqual({ tone: 'unknown', label: 'unknown' });
  });
});

describe('isUncorrelatedToolCall', () => {
  it('recognises a standalone seller tool-call record', () => {
    expect(isUncorrelatedToolCall('adcp-seller-uncorrelated-abc123')).toBe(true);
  });

  it('does not match a real conversation', () => {
    expect(isUncorrelatedToolCall('adcp-buyer-agent-a2a-abc')).toBe(false);
    expect(isUncorrelatedToolCall(undefined)).toBe(false);
  });
});

describe('sessionBriefLabel', () => {
  it('returns the brief the recorder previewed', () => {
    expect(sessionBriefLabel(meta({ request_preview: 'CTV news inventory, US, Q3' }))).toBe(
      'CTV news inventory, US, Q3',
    );
  });

  it('is null when the record carries no preview', () => {
    // Null so the row renders nothing, rather than empty quotes or "no brief" — neither of which the
    // recorder said.
    expect(sessionBriefLabel(meta())).toBeNull();
  });

  it('is null for a preview that is only whitespace', () => {
    expect(sessionBriefLabel(meta({ request_preview: '   \n  ' }))).toBeNull();
  });

  it('collapses newlines, because the row is one line', () => {
    // The words are untouched; only the whitespace between them changes.
    expect(
      sessionBriefLabel(meta({ request_preview: 'rewarded video\n\n  for casual games' })),
    ).toBe('rewarded video for casual games');
  });

  it('marks a brief it shortened with an ellipsis', () => {
    // A silently-cut brief reads as the whole of what was asked, which is the one thing this must not
    // do.
    const long = 'a'.repeat(SESSION_BRIEF_MAX_CHARS + 40);
    const label = sessionBriefLabel(meta({ request_preview: long }))!;
    expect(label).toHaveLength(SESSION_BRIEF_MAX_CHARS);
    expect(label.endsWith('\u2026')).toBe(true);
  });

  it('does not shorten a brief that already fits', () => {
    const exact = 'b'.repeat(SESSION_BRIEF_MAX_CHARS);
    expect(sessionBriefLabel(meta({ request_preview: exact }))).toBe(exact);
    expect(sessionBriefLabel(meta({ request_preview: exact }))).not.toContain('\u2026');
  });

  it('honours a caller-supplied limit', () => {
    expect(sessionBriefLabel(meta({ request_preview: 'abcdefghij' }), 5)).toBe('abcd\u2026');
  });
});
