/**
 * Do Bind and Plan populate from a session the deployed system really recorded?
 *
 * Every other test here builds its own steps. That is the right default, but it cannot catch a
 * disagreement between what the recorder writes and what derivation reads — both sides get their
 * shape from the same hand-written fixture, so they agree by construction. That is precisely how the
 * original tool-name defect survived a full test suite (see `phases.tools.test.ts`).
 *
 * So the fixture here is not written by hand. It is the verbatim DynamoDB output of one real run of
 * `verify_governance_journey_live.py`: a genuine invocation of the deployed buyer agent, which called
 * `adcp_sync_accounts` and `adcp_sync_governance` against the deployed reference seller and
 * `adcp_sync_plans` against the deployed governance agent. Captured with:
 *
 *     session_store.get_session_steps('adcp-buyer-agent-a3b53ac1-33e5-4408-bfd2-e22c36b9eb22')
 *
 * This is a recorded fixture: the real output of that run, checked in so the decoding path can be
 * tested without a deploy. It contains no credential: `sync_governance` mints
 * the governance token inside the tool body, so it never reaches the recorded `input`, and AdCP
 * treats credentials as write-only so the seller's response omits it too. Both were verified before
 * committing (no `credentials` key, no JWT-shaped string in the file).
 *
 * If a later change to the recorder's item shape, the pairing logic or a decoder breaks the reading
 * of recorded data, this fails while the hand-written tests keep passing. That gap is the whole point.
 */

import { describe, expect, it } from 'vitest';

import type { SessionStep } from '../types';
import { derivePhases } from './derive';
import type { AccountStripData, BindData, DeriveContext, PlanData } from './types';

import recorded from './__fixtures__/recorded-governance-session.json';

const steps = recorded.steps as unknown as SessionStep[];

/**
 * The real build's capability set, including the two governance tools.
 *
 * Deliberately lists what `JourneyView`'s `CALLABLE_TOOLS` lists. If those diverge,
 * `phases.tools.test.ts` is the guard that fires; here the point is that with the governance tools
 * callable, an empty Bind or Plan panel can no longer be excused as `not_implemented`.
 */
const WITH_GOVERNANCE: DeriveContext = {
  capabilities: {
    callableTools: [
      'adcp_sync_accounts',
      'adcp_sync_governance',
      'adcp_sync_plans',
      'adcp_get_products',
      'adcp_get_capabilities',
      'adcp_list_creative_formats',
    ],
  },
  sellerPriority: [],
};

describe('a really recorded governance session', () => {
  it('is a real recording, not an empty file', () => {
    // Without this, every assertion below could pass vacuously on an empty array.
    expect(steps.length).toBeGreaterThan(0);
    const toolNames = new Set(
      steps.map((step) => step.content?.toolName).filter((name): name is string => !!name),
    );
    expect(toolNames).toContain('adcp_sync_governance');
    expect(toolNames).toContain('adcp_sync_plans');
  });

  it('carries no credential, which is what makes it safe to commit', () => {
    // A regression here would be a leaked secret in the repo, so it is asserted rather than assumed.
    const serialised = JSON.stringify(recorded);
    expect(serialised).not.toContain('credentials');
    // JWTs from Cognito start with this header prefix.
    expect(serialised).not.toContain('eyJ');
  });

  /**
   * Assert the state POSITIVELY, and assert the rows.
   *
   * The first version of these tests asserted only what the state must not be — not
   * `not_implemented`, not `pending`, not `error` — and passed while both panels were in fact
   * `unreachable` with no data at all. `unreachable` was simply not on the list, and `pending`/`error`
   * are not even members of the state union, so two of the three assertions could never have failed.
   *
   * That is worth a comment because it is the same mistake in miniature as everything else this file
   * guards: a check that cannot fail reads exactly like a check that passes. Assert the value, not a
   * few of the values it isn't.
   */
  it('populates Accounts from the recorded sync_accounts exchange', () => {
    // This fixture always contained the `adcp_sync_accounts` exchange — it is the first call the real
    // run made — and nothing read it, because the phase did not exist. Asserted against the real
    // recording rather than a hand-written fixture for the reason this whole file exists: both sides of
    // a hand-written one agree by construction.
    const states = derivePhases(steps, WITH_GOVERNANCE);
    const accounts = states.get('accounts');
    expect(accounts?.state).toBe('ready');

    const data = accounts?.state === 'ready' ? (accounts.data as AccountStripData) : null;
    expect(data).not.toBeNull();
    // The account's natural key, lifted to the header, carries the run's own brand/operator pair.
    expect(data?.brand).toContain('verify-journey-');
    expect(data?.operator).toContain('verify-journey-');
    // The single deployed seller's card.
    const card = data!.cards[0]!;
    expect(card.accountId).toBeDefined();
    // action and status are both required on AdCP's SyncAccountsAccount, and neither is a success flag.
    expect(['created', 'updated', 'unchanged']).toContain(card.action);
    expect(card.status).toBe('active');
  });

  it('populates Bind from the recorded sync_governance exchange', () => {
    const states = derivePhases(steps, WITH_GOVERNANCE);
    const bind = states.get('bind');
    expect(bind?.state).toBe('ready');

    const data = bind?.state === 'ready' ? (bind.data as BindData) : null;
    expect(data?.links).toHaveLength(1);
    const link = data!.links[0]!;
    // The real account the deployed seller keyed the binding on, and the real runtime it bound.
    expect(link.account).toContain('verify-journey-');
    expect(link.status).toBe('synced');
    expect(link.agent).toContain('RefGovernance');
  });

  it('populates Plan from the recorded sync_plans exchange', () => {
    const states = derivePhases(steps, WITH_GOVERNANCE);
    const plan = states.get('plan');
    expect(plan?.state).toBe('ready');

    const data = plan?.state === 'ready' ? (plan.data as PlanData) : null;
    expect(data?.planId).toContain('verify-journey-plan-');
    expect(data?.status).toBe('active');
    expect(data?.version).toBe('1');
    // The four categories the deployed governance agent really returned.
    expect(data?.willEvaluate).toEqual([
      'budget_authority',
      'flight_window',
      'geo_compliance',
      'channel_compliance',
    ]);
    // A real empty array: the agent resolved no policies and said so.
    expect(data?.resolvedPolicies).toEqual([]);
  });

  it('keeps submitted plan terms distinguishable from agent-confirmed ones', () => {
    // The governance agent's response carries no budget, flight or channels. Those come from the
    // recorded request, and the panel labels them "submitted" for that reason: presenting them as
    // confirmed would imply the agent validated terms it never mentioned. See governanceCards.ts.
    //
    // Asserted here at the DATA layer, which is where the provenance split lives -- the decoder reads
    // these three from `exchange.input` and everything else from `exchange.result`.
    const states = derivePhases(steps, WITH_GOVERNANCE);
    const plan = states.get('plan');
    const data = plan?.state === 'ready' ? (plan.data as PlanData) : null;

    expect(data?.budgetAmount).toBe('50,000');
    expect(data?.currency).toBe('USD');
    expect(data?.flightStart).toBe('2026-09-01');
    expect(data?.flightEnd).toBe('2026-09-30');
    expect(data?.channels).toEqual(['streaming_audio']);
  });

  it('never labels a registered plan as approved', () => {
    // `sync_plans` records a plan; it does not judge one, so a row reading like a verdict would
    // overstate what happened.
    const states = derivePhases(steps, WITH_GOVERNANCE);
    const plan = states.get('plan');
    const data = plan?.state === 'ready' ? plan.data : null;
    const text = JSON.stringify(data).toLowerCase();

    for (const verdict of ['approved', 'compliant', 'cleared', 'authorised', 'authorized', 'passed']) {
      expect(text).not.toContain(verdict);
    }
  });

  it('reports phases this session never exercised as not reached', () => {
    // The counterpart assertion. A recorded governance session says nothing about inventory, so
    // Discover must report that it has not run — a derivation that filled it from an unrelated
    // exchange would describe a call that never happened. `not_reached`, specifically: the tool IS
    // callable in this build, so `not_implemented` would assert a limit that does not exist.
    const states = derivePhases(steps, WITH_GOVERNANCE);
    expect(states.get('discover')?.state).toBe('not_reached');
  });

  it('reports Bind as a capability gap only when the tool is genuinely absent', () => {
    // Same steps, a build that cannot call the tool. This is the state the panel showed for most of
    // this feature's life, and it has to remain reachable for the right reason.
    const noTools: DeriveContext = {
      capabilities: { callableTools: [] },
      sellerPriority: [],
    };
    const states = derivePhases(steps, noTools);
    // The steps are present, so Bind's exchange still decodes: a capability list does not erase real
    // recorded data, and it should not. What the list governs is the verdict for a phase with NO
    // exchange — Discover here, which becomes `not_implemented` rather than `not_reached`.
    expect(states.get('bind')?.state).toBe('ready');
    expect(states.get('discover')?.state).toBe('not_implemented');
  });
});
