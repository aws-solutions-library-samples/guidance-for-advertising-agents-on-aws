/**
 * The two governance panels show two different parties' findings.
 *
 * This file exists because they did not. Govern and Score were both bound to
 * `adcp_check_governance` and separated by `selector`, which only chose the first versus the last of
 * that tool's exchanges — so a session that made one check handed both panels the same exchange, and
 * the buyer orchestrator's own intent check was rendered a second time under the heading "Seller's
 * governance agent scores execution". Same ring, same percentages, same explanation, same plan id,
 * twice.
 *
 * Nothing failed. Both panels were `ready`, both showed real recorded data, and the data was real —
 * it was the ATTRIBUTION that was false, which is the failure mode the journey view's whole
 * absence-versus-data design is meant to prevent. A duplicated widget reads as two independent
 * agents agreeing.
 *
 * The two assertions that matter, and the reason there are two:
 *
 *   1. With only intent checks recorded, Score must be ABSENT. Not empty-but-ready, and above all
 *      not filled from Govern's exchange. A session that has not booked has no seller execution
 *      check, because only `create_media_buy` triggers one.
 *   2. With a real execution check in the audit log, the two panels must disagree — different
 *      verdict, different findings, different explanation. Identical output would mean the split is
 *      cosmetic.
 */

import { describe, expect, it } from 'vitest';

import { derivePhases } from './derive';
import type { SessionStep } from '../types';
import type { DeriveContext, GovernanceCheckData } from './types';

const CONTEXT: DeriveContext = {
  capabilities: {
    callableTools: [
      'adcp_get_products',
      'adcp_check_governance',
      'adcp_get_plan_audit_logs',
      'adcp_create_media_buy',
    ],
  },
  sellerPriority: [],
};

let nextIndex = 0;

/** A recorded call/result pair, as the session recorder writes them. */
function steps(toolName: string, input: unknown, output: unknown): SessionStep[] {
  const callId = `c${(nextIndex += 1)}`;
  return [
    {
      step_index: nextIndex * 2,
      step_type: 'tool_call',
      content: { toolName, callId, input },
    } as unknown as SessionStep,
    {
      step_index: nextIndex * 2 + 1,
      step_type: 'tool_result',
      // The recorder stores output as the JSON string the tool returned, so that is what is used
      // here — a fixture built from objects would pass while a real session did not.
      content: { toolName, callId, status: 'success', output: JSON.stringify(output) },
    } as unknown as SessionStep,
  ];
}

/** The buyer's own intent check: `tool_name` + `payload` is what makes it one. */
function intentCheck(task: string, verdict: string, explanation: string, findings: unknown[]) {
  return steps(
    'adcp_check_governance',
    { seller_id: 'reference', plan_id: 'plan-1', tool_name: task, payload: {} },
    {
      check_id: `chk_${task}`,
      verdict,
      plan_id: 'plan-1',
      explanation,
      findings,
      mode: 'enforce',
    },
  );
}

/** An audit-log read-back carrying whichever entries are given. */
function auditLog(...entries: unknown[]) {
  return steps(
    'adcp_get_plan_audit_logs',
    { plan_ids: ['plan-1'], include_entries: true },
    { plans: [{ plan_id: 'plan-1', plan_version: 1, status: 'active', entries }] },
  );
}

const INTENT_FINDING = {
  category_id: 'budget_authority',
  severity: 'info',
  explanation: 'Requested 2,500.00 is within the plan\u2019s remaining authority of 25,000.00.',
  confidence: 1,
};

const EXECUTION_FINDING = {
  category_id: 'channel_compliance',
  severity: 'warning',
  explanation: 'Planned delivery names a channel the plan does not allow.',
  confidence: 0.4,
};

function dataFor(states: ReturnType<typeof derivePhases>, key: 'govern' | 'score') {
  const state = states.get(key);
  return state?.state === 'ready' ? (state.data as GovernanceCheckData) : null;
}

describe('the buyer\u2019s intent check never fills the seller\u2019s panel', () => {
  it('leaves Score absent when the session made only intent checks', () => {
    const states = derivePhases(intentCheck('get_products', 'approved', 'Within plan.', [INTENT_FINDING]), CONTEXT);

    expect(states.get('govern')?.state).toBe('ready');
    // The regression. `ready` here would mean the intent check was borrowed.
    expect(states.get('score')?.state).not.toBe('ready');
  });

  it('leaves Score absent even when several intent checks were made', () => {
    // The precise shape of the old bug: with two checks, first-versus-last produced two DIFFERENT
    // exchanges, so both panels looked populated and plausible — and both were intent checks, one of
    // them attributed to the seller's governance agent. More data made it harder to spot, not easier.
    const states = derivePhases(
      [
        ...intentCheck('get_products', 'approved', 'Discovery is within plan.', [INTENT_FINDING]),
        ...intentCheck('create_media_buy', 'approved', 'Purchase is within plan.', [INTENT_FINDING]),
      ],
      CONTEXT,
    );

    expect(states.get('score')?.state).not.toBe('ready');
  });

  it('shows the LATEST intent check on Govern, not the first', () => {
    const states = derivePhases(
      [
        ...intentCheck('get_products', 'approved', 'Discovery is within plan.', [INTENT_FINDING]),
        ...intentCheck('create_media_buy', 'conditions', 'Purchase needs adjusting.', [INTENT_FINDING]),
      ],
      CONTEXT,
    );

    expect(dataFor(states, 'govern')?.verdict.title).toBe('Purchase needs adjusting.');
  });

  it('reports Score as absent when the audit log holds no execution entry', () => {
    // The audit log was read and answered; it simply contains no seller check yet. That is a real
    // state of a session that has not booked, and it must not be dressed up as one.
    const states = derivePhases(
      [
        ...intentCheck('get_products', 'approved', 'Within plan.', [INTENT_FINDING]),
        ...auditLog({
          id: 'chk_1',
          type: 'check',
          timestamp: '2026-08-27T10:00:00Z',
          check_type: 'intent',
          verdict: 'approved',
          explanation: 'Within plan.',
          findings: [INTENT_FINDING],
          mode: 'enforce',
        }),
      ],
      CONTEXT,
    );

    expect(states.get('score')?.state).not.toBe('ready');
  });
});

describe('the two panels carry two different parties\u2019 findings', () => {
  const states = () =>
    derivePhases(
      [
        ...intentCheck('create_media_buy', 'approved', 'Within plan authority.', [INTENT_FINDING]),
        ...auditLog(
          {
            id: 'chk_1',
            type: 'check',
            timestamp: '2026-08-27T10:00:00Z',
            check_type: 'intent',
            verdict: 'approved',
            explanation: 'Within plan authority.',
            findings: [INTENT_FINDING],
            mode: 'enforce',
          },
          {
            id: 'chk_2',
            type: 'check',
            timestamp: '2026-08-27T10:00:05Z',
            check_type: 'execution',
            verdict: 'conditions',
            explanation: 'Planned delivery diverges from the plan.',
            findings: [EXECUTION_FINDING],
            mode: 'enforce',
          },
        ),
      ],
      CONTEXT,
    );

  it('populates both', () => {
    const derived = states();
    expect(derived.get('govern')?.state).toBe('ready');
    expect(derived.get('score')?.state).toBe('ready');
  });

  it('does not render the same verdict twice', () => {
    const derived = states();
    const govern = dataFor(derived, 'govern');
    const score = dataFor(derived, 'score');

    expect(govern?.verdict.pill).toBe('approved');
    expect(score?.verdict.pill).toBe('conditions');
    expect(govern?.verdict.title).not.toBe(score?.verdict.title);
  });

  it('does not render the same findings twice', () => {
    const derived = states();
    const govern = dataFor(derived, 'govern');
    const score = dataFor(derived, 'score');

    expect(govern?.arcs.map((a) => a.categoryId)).toEqual(['budget_authority']);
    expect(score?.arcs.map((a) => a.categoryId)).toEqual(['channel_compliance']);
    // The confidences differ too, so the two rings cannot draw identically.
    expect(govern?.arcs[0]?.confidence).toBe(1);
    expect(score?.arcs[0]?.confidence).toBe(0.4);
  });

  it('takes the most recent execution check when the seller re-checked', () => {
    const derived = derivePhases(
      auditLog(
        {
          id: 'chk_1',
          type: 'check',
          timestamp: '2026-08-27T10:00:00Z',
          check_type: 'execution',
          verdict: 'conditions',
          explanation: 'First pass needed adjusting.',
          findings: [EXECUTION_FINDING],
          mode: 'enforce',
        },
        {
          id: 'chk_2',
          type: 'check',
          timestamp: '2026-08-27T10:00:09Z',
          check_type: 'execution',
          verdict: 'approved',
          explanation: 'Adjusted delivery is within plan.',
          findings: [],
          mode: 'enforce',
        },
      ),
      CONTEXT,
    );

    const score = dataFor(derived, 'score');
    expect(score?.verdict.title).toBe('Adjusted delivery is within plan.');
    // A clean check with no findings is a real render, not an absence: `approved` with nothing found
    // is what a passing execution check looks like.
    expect(score?.arcs).toHaveLength(0);
    expect(score?.verdict.count).toBe(0);
  });

  it('ignores outcome entries, which carry no verdict', () => {
    // `report_plan_outcome` writes `type: "outcome"` entries into the same list. One of those has no
    // verdict and no check type, and treating it as a check would put an outcome under a heading that
    // promises a governance score.
    const derived = derivePhases(
      auditLog({
        id: 'out_1',
        type: 'outcome',
        timestamp: '2026-08-27T10:00:20Z',
        outcome: 'completed',
        outcome_status: 'accepted',
        committed_budget: 2500,
      }),
      CONTEXT,
    );

    expect(derived.get('score')?.state).not.toBe('ready');
  });
});
