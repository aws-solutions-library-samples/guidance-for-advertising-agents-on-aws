/**
 * The three independent signals for "this plan is awaiting human review", and the conditions parser.
 *
 * ## Why three, and why each is tested alone
 *
 * A governance agent can report the same situation in three places, and until now the UI watched one of
 * them. The panel would show `denied` — a real verdict, from real data — for a plan that was frozen
 * pending a human, which is the same class of failure as the duplicated-panel bug
 * `governanceSplit.test.ts` records: nothing errors, everything renders, and the ATTRIBUTION is false.
 *
 * Each signal is asserted **independently**. That is deliberate: the obvious refactor is to fold them
 * into one predicate, and the obvious way to get that wrong is to make signals 2 and 3 work while
 * quietly dropping signal 1 — the one the four existing readers already depend on. A combined test
 * would pass throughout.
 *
 *   1. envelope `status` of `submitted` / `working` — review in progress
 *   2. `CAMPAIGN_SUSPENDED` on either error layer — plan frozen pending review
 *   3. an unresolved escalation in a `get_plan_audit_logs` read — the durable record of (2)
 *
 * Signal 3 is the one that survives a reload, and it is what makes RQ3 work: resolution happens out of
 * band via an operator script, and the UI has to reflect it from data it already reads rather than from
 * a notification nobody sends.
 */
import { describe, expect, it } from 'vitest';

import { displayField, parseConditions } from './conditions';
import { decodeExecutionCheckCard, decodeGovernanceCheckCard } from './governanceCards';
import type { ToolExchange } from './types';

function exchange(output: unknown, toolName = 'adcp_check_governance'): ToolExchange {
  return {
    callId: 'call-1',
    toolName,
    input: {},
    callIndex: 0,
    result: { output, status: 'success', index: 1 },
  };
}

/** An audit-log response, which is where signal 3 lives. */
function auditResponse(options: {
  readonly escalations?: readonly unknown[];
  readonly entries?: readonly unknown[];
}): unknown {
  return {
    plans: [
      {
        plan_id: 'plan-1',
        plan_version: 1,
        status: 'suspended',
        budget: { committed: 0 },
        summary: { checks_performed: 1, escalations: options.escalations ?? [] },
        governed_actions: [],
        entries: options.entries ?? [],
      },
    ],
  };
}

const EXECUTION_ENTRY = {
  id: 'chk_exec1',
  type: 'check',
  check_type: 'execution',
  timestamp: '2026-12-05T10:00:00Z',
  verdict: 'approved',
  explanation: 'Within authority.',
  findings: [{ category_id: 'budget_authority', severity: 'info', explanation: 'ok', confidence: 1 }],
};

describe('signal 1 — the envelope status', () => {
  it.each(['submitted', 'working'])('%s is awaiting review, not a verdict', (status) => {
    const decoded = decodeGovernanceCheckCard(exchange({ status, verdict: 'approved' }));
    expect(decoded?.awaitingHumanReview).toBe(true);
    // The verdict in the payload is deliberately `approved` here: if this signal were dropped, the
    // panel would show an APPROVAL for a check no human has finished, which is worse than showing a
    // denial.
    expect(decoded?.verdict.pill).toBe('');
  });

  it('carries no review note, because the envelope status states no cause', () => {
    const decoded = decodeGovernanceCheckCard(exchange({ status: 'working' }));
    expect(decoded?.humanReview).toBeNull();
  });

  it('reaches the decoded card, so a conditions verdict carries its classified conditions', () => {
    // The wiring assertion. `conditions.ts` classifies correctly in isolation either way; this is what
    // proves the panel can actually see the result.
    const decoded = decodeGovernanceCheckCard(
      exchange({
        verdict: 'conditions',
        plan_id: 'plan-1',
        explanation: 'above remaining authority',
        conditions: [
          { field_name: 'budget.total', reason: 'exceeds remaining', required_value: 50000 },
          { field_name: 'planned_delivery.total_budget', reason: 'seller-side', required_value: 1 },
        ],
      }),
    );
    expect(decoded?.conditions?.items).toHaveLength(2);
    expect(decoded?.conditions?.buyerActionableCount).toBe(1);
  });
});

describe('signal 2 — a CAMPAIGN_SUSPENDED error', () => {
  const SUSPENDED = {
    code: 'CAMPAIGN_SUSPENDED',
    message: 'Plan plan-1 is suspended pending human review.',
    recovery: 'transient',
    suggestion: 'contact the plan operator',
  };

  it('is awaiting review even though the response carries verdict denied', () => {
    // This is the whole point of the signal. The governance agent's refusal for a suspended plan is
    // `verdict: denied` with a `CAMPAIGN_SUSPENDED` error, and reading the verdict first renders a
    // frozen plan as a non-compliant one. Two different facts, and only one is about the request.
    const decoded = decodeGovernanceCheckCard(
      exchange({ verdict: 'denied', plan_id: 'plan-1', explanation: 'suspended', errors: [SUSPENDED] }),
    );
    expect(decoded?.awaitingHumanReview).toBe(true);
    expect(decoded?.humanReview?.reason).toBe(SUSPENDED.message);
    expect(decoded?.humanReview?.suggestion).toBe('contact the plan operator');
  });

  it('is found on the envelope layer too, not only the payload array', () => {
    // AdCP's two-layer error model lets an agent populate `errors[]`, `adcp_error`, or both. A reader
    // that watched one layer would miss a suspension from an agent that chose the other.
    const decoded = decodeGovernanceCheckCard(
      exchange({ verdict: 'denied', plan_id: 'plan-1', adcp_error: SUSPENDED }),
    );
    expect(decoded?.awaitingHumanReview).toBe(true);
    expect(decoded?.humanReview?.reason).toBe(SUSPENDED.message);
  });

  it('does not treat any other error code as a pending review', () => {
    // A PERMISSION_DENIED is a refusal nobody is reviewing. Reporting it as pending would tell a buyer
    // to wait for a human who was never asked.
    const decoded = decodeGovernanceCheckCard(
      exchange({
        verdict: 'denied',
        plan_id: 'plan-1',
        explanation: 'not your plan',
        errors: [{ code: 'PERMISSION_DENIED', message: 'no' }],
      }),
    );
    expect(decoded?.awaitingHumanReview).toBe(false);
    expect(decoded?.verdict.pill).not.toBe('');
  });
});

describe('signal 3 — an unresolved escalation in the audit log', () => {
  it('overrides the recorded execution verdict', () => {
    // The recorded verdict is the agent's pre-review RECOMMENDATION while an escalation is open.
    // Rendering it as an answer shows a denial a human may be about to reverse.
    const decoded = decodeExecutionCheckCard(
      exchange(
        auditResponse({
          entries: [{ ...EXECUTION_ENTRY, verdict: 'denied' }],
          escalations: [{ check_id: 'chk_exec1', reason: 'Trailing-window spend crossed the threshold.' }],
        }),
        'adcp_get_plan_audit_logs',
      ),
    );
    expect(decoded?.awaitingHumanReview).toBe(true);
    expect(decoded?.humanReview?.reason).toBe('Trailing-window spend crossed the threshold.');
    expect(decoded?.humanReview?.resolution).toBeNull();
  });

  it('stays awaiting while ANY escalation is outstanding, however many are closed', () => {
    const decoded = decodeExecutionCheckCard(
      exchange(
        auditResponse({
          entries: [EXECUTION_ENTRY],
          escalations: [
            { check_id: 'a', reason: 'first', resolution: 'approved_by_human', resolved_at: '2026-12-05T09:00:00Z' },
            { check_id: 'b', reason: 'second' },
          ],
        }),
        'adcp_get_plan_audit_logs',
      ),
    );
    expect(decoded?.awaitingHumanReview).toBe(true);
    expect(decoded?.humanReview?.reason).toBe('second');
  });

  it('reflects a resolution alongside the verdict once a human has answered — RQ3', () => {
    // The direct test of RQ3's constraint. Resolution happens out of band (an operator script, since
    // AdCP has no task for it), so the only way the panel can learn about it is the audit log it
    // already reads. Dropping the note here would make a reviewed plan indistinguishable from one that
    // was never escalated.
    const decoded = decodeExecutionCheckCard(
      exchange(
        auditResponse({
          entries: [EXECUTION_ENTRY],
          escalations: [
            {
              check_id: 'chk_exec1',
              reason: 'Trailing-window spend crossed the threshold.',
              resolution: 'approved_by_human',
              resolved_at: '2026-12-05T11:30:00Z',
            },
          ],
        }),
        'adcp_get_plan_audit_logs',
      ),
    );
    expect(decoded?.awaitingHumanReview).toBe(false);
    expect(decoded?.verdict.pill).not.toBe('');
    expect(decoded?.humanReview?.resolution).toBe('approved_by_human');
    expect(decoded?.humanReview?.resolvedAt).toBe('2026-12-05T11:30:00Z');
  });

  it('carries no conditions, because AdCP audit entries have no conditions field', () => {
    // Worth pinning: an audit entry can report the verdict but not what would have satisfied it, and a
    // reader that invented an empty set here would say the agent asked for nothing.
    const decoded = decodeExecutionCheckCard(
      exchange(auditResponse({ entries: [{ ...EXECUTION_ENTRY, verdict: 'conditions' }] }), 'adcp_get_plan_audit_logs'),
    );
    expect(decoded?.conditions).toBeNull();
  });

  it('an audit log with no escalations decodes as an ordinary verdict', () => {
    const decoded = decodeExecutionCheckCard(
      exchange(auditResponse({ entries: [EXECUTION_ENTRY] }), 'adcp_get_plan_audit_logs'),
    );
    expect(decoded?.awaitingHumanReview).toBe(false);
    expect(decoded?.humanReview).toBeNull();
  });
});

describe('conditions', () => {
  it('absent is not empty — a conditions verdict with no array renders the verdict alone', () => {
    // The distinction the caller depends on. `null` means the agent said nothing about conditions;
    // rendering "no conditions" would assert that it checked and found none.
    expect(parseConditions({ verdict: 'conditions' })).toBeNull();
    expect(parseConditions({ verdict: 'conditions', conditions: [] })).toEqual({
      items: [],
      buyerActionableCount: 0,
    });
  });

  it('required_value present is machine-actionable', () => {
    const parsed = parseConditions({
      conditions: [{ field: 'budget.total', reason: 'exceeds remaining authority', required_value: 50000 }],
    });
    const item = parsed?.items[0];
    expect(item?.hasRequiredValue).toBe(true);
    expect(item?.buyerActionable).toBe(true);
    expect(item?.panel).toBe('govern');
    expect(parsed?.buyerActionableCount).toBe(1);
  });

  it('required_value absent is human judgement, not a fixable field', () => {
    const parsed = parseConditions({
      conditions: [{ field: 'budget.total', reason: 'the spend aggregate could not be read' }],
    });
    const item = parsed?.items[0];
    expect(item?.hasRequiredValue).toBe(false);
    expect(item?.buyerActionable).toBe(false);
    expect(parsed?.buyerActionableCount).toBe(0);
  });

  it('a required_value of 0 is a real value, not an absence', () => {
    // Presence, not truthiness. A falsy check would reclassify "set this to zero" as "a human must
    // decide" — the absent-versus-zero confusion this project's display rules exist to prevent.
    const parsed = parseConditions({
      conditions: [{ field: 'budget.total', reason: 'no authority left', required_value: 0 }],
    });
    expect(parsed?.items[0]?.hasRequiredValue).toBe(true);
    expect(parsed?.items[0]?.requiredValue).toBe(0);
    expect(parsed?.items[0]?.buyerActionable).toBe(true);
  });

  it('a planned_delivery condition routes to Score and is never buyer-actionable', () => {
    // Even fully specified. The field belongs to the SELLER's planned delivery, so a buyer editing its
    // own request cannot satisfy it — presenting it as actionable sends the buyer to change a field it
    // does not own, and it keeps failing.
    const parsed = parseConditions({
      conditions: [
        { field: 'planned_delivery.total_budget', reason: 'above authorised', required_value: 50000 },
      ],
    });
    const item = parsed?.items[0];
    expect(item?.scope).toBe('delivery');
    expect(item?.panel).toBe('score');
    expect(item?.buyerActionable).toBe(false);
    expect(parsed?.buyerActionableCount).toBe(0);
  });

  it('a bare path routes to Govern, because the prefix is optional in the spec', () => {
    const parsed = parseConditions({ conditions: [{ field: 'budget.total', reason: 'x', required_value: 1 }] });
    expect(parsed?.items[0]?.scope).toBe('action');
    expect(parsed?.items[0]?.panel).toBe('govern');
  });

  it('accepts the reference agent field_name spelling as well as the schema field', () => {
    const parsed = parseConditions({ conditions: [{ field_name: 'budget.total', reason: 'x' }] });
    expect(parsed?.items[0]?.field).toBe('budget.total');
  });

  it('drops a condition that names neither a field nor a reason', () => {
    const parsed = parseConditions({ conditions: [{ required_value: 5 }] });
    expect(parsed?.items).toHaveLength(0);
  });

  it('strips the root prefix for display, so one field does not read as two', () => {
    const parsed = parseConditions({
      conditions: [
        { field: 'payload.budget.total', reason: 'a' },
        { field: 'planned_delivery.total_budget', reason: 'b' },
        { field: 'budget.total', reason: 'c' },
      ],
    });
    expect(parsed?.items.map((item) => displayField(item))).toEqual([
      'budget.total',
      'total_budget',
      'budget.total',
    ]);
  });
});
