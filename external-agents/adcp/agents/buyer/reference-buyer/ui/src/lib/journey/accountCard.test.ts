/**
 * The Accounts panel's decoder.
 *
 * `sync_accounts` fans out to EVERY seller, and each returns the same account — keyed on the
 * (brand domain, operator) pair the buyer declared — with its own `account_id`. So the panel is one
 * card per seller, with the shared brand/operator lifted to the header; a flat row list of the same
 * data was a stack of near-identical blocks, which is what this strip replaces.
 *
 * The success payload below is the real shape captured from a deployed seller. The failure cases are
 * built from AdCP's own vocabulary: `SyncAccountsAccount` REQUIRES both `action` and `status`, and
 * neither is a success flag — `action` is one of `created`/`updated`/`unchanged`/`failed` and `status`
 * one of `active`/`pending_approval`/`rejected`/`payment_required`/`suspended`/`closed`. A `failed`
 * action or an `errors[]` entry renders its own failed card and withholds the `account_id`, the same
 * honesty rule `decodeMediaBuyStrip` keeps for a refused buy, so it is tested here rather than left to
 * inspection.
 */

import { describe, expect, it } from 'vitest';

import { decodeAccountStrip } from './governanceCards';
import type { AccountCard, ToolExchange } from './types';

/** One seller's `sync_accounts` reply, in the fan-out envelope every seller-facing tool uses. */
function exchange(accounts: readonly unknown[] | unknown, sellerFailed = false): ToolExchange {
  const entry: Record<string, unknown> = { seller_id: 'poseidon' };
  if (sellerFailed) {
    entry['error'] = 'seller unreachable';
  } else {
    entry['response'] = { accounts, sandbox: true, status: 'completed' };
  }
  return {
    callId: 'tooluse_accounts',
    toolName: 'adcp_sync_accounts',
    input: { brand_domain: 'nike.example', operator: 'acme-agency' },
    callIndex: 0,
    result: { output: { task: 'sync_accounts', results: [entry] }, status: 'success', index: 1 },
  };
}

/** A multi-seller fan-out: the real "several near-identical blocks" case. */
function fanOut(entries: readonly Record<string, unknown>[]): ToolExchange {
  return {
    callId: 'tooluse_accounts',
    toolName: 'adcp_sync_accounts',
    input: { brand_domain: 'northwind-coffee.example', operator: 'meridian-media.example' },
    callIndex: 0,
    result: { output: { task: 'sync_accounts', results: entries }, status: 'success', index: 1 },
  };
}

/** The real reply from a deployed seller, verbatim. */
const LIVE_ACCOUNT = {
  account_id: 'acc_verify_poseidon_538fc4f3_example_verify_poseidon_operator_538fc4f3',
  brand: { domain: 'verify-poseidon-538fc4f3.example' },
  operator: 'verify-poseidon-operator-538fc4f3',
  action: 'created',
  status: 'active',
};

/** The single card of a one-seller reply, for the common assertions. */
function onlyCard(accounts: readonly unknown[]): AccountCard {
  const data = decodeAccountStrip(exchange(accounts));
  expect(data).not.toBeNull();
  expect(data?.cards).toHaveLength(1);
  return data!.cards[0]!;
}

describe('decodeAccountStrip', () => {
  it('reads the real reply from a deployed seller', () => {
    const data = decodeAccountStrip(exchange([LIVE_ACCOUNT]));
    expect(data).not.toBeNull();
    // brand/operator are the account's natural key, lifted to the header rather than onto the card.
    expect(data?.brand).toBe('verify-poseidon-538fc4f3.example');
    expect(data?.operator).toBe('verify-poseidon-operator-538fc4f3');
    const card = data!.cards[0]!;
    expect(card.seller).toBe('poseidon');
    expect(card.outcome).toBe('active');
    expect(card.action).toBe('created');
    expect(card.status).toBe('active');
    expect(card.accountId).toContain('acc_');
  });

  it('names each seller and counts the distinct sellers in a multi-seller fan-out', () => {
    // The real "four identical blocks" report: same account from every seller, only the account_id
    // differing. Each card is attributed by seller_name, falling back to seller_id (gotham named
    // none), and the shared account key is carried once in the header.
    const data = decodeAccountStrip(
      fanOut([
        { seller_id: 'reference', seller_name: 'Reference Seller', response: { accounts: [{ ...LIVE_ACCOUNT, account_id: 'acc_ref' }] } },
        { seller_id: 'poseidon', seller_name: 'Poseidon', response: { accounts: [{ ...LIVE_ACCOUNT, account_id: 'acc_pos' }] } },
        { seller_id: 'gotham', response: { accounts: [{ ...LIVE_ACCOUNT, account_id: 'gg-acct' }] } },
      ]),
    );
    expect(data).not.toBeNull();
    expect(data?.cards.map((c) => c.seller)).toEqual(['Reference Seller', 'Poseidon', 'gotham']);
    expect(data?.cards.map((c) => c.accountId)).toEqual(['acc_ref', 'acc_pos', 'gg-acct']);
    expect(data?.sellerCount).toBe(3);
  });

  it('reports `updated` and `unchanged` as themselves, not as created', () => {
    for (const action of ['updated', 'unchanged']) {
      expect(onlyCard([{ ...LIVE_ACCOUNT, action }]).action).toBe(action);
    }
  });

  it('reports a non-active status rather than implying the account is usable', () => {
    // `pending_approval` and `suspended` are real AdCP statuses. An account in either is not ready, and
    // a card that showed only "created" would say the opposite of what the seller reported.
    for (const status of ['pending_approval', 'rejected', 'payment_required', 'suspended', 'closed']) {
      expect(onlyCard([{ ...LIVE_ACCOUNT, status }]).status).toBe(status);
    }
  });

  it('shows a failed action as a failure, and withholds the account_id', () => {
    // `action: "failed"` is in AdCP's own enum. Reporting an account_id beside it would describe a
    // failure with the field a success populates.
    const card = onlyCard([
      {
        account_id: 'acc_should_be_withheld',
        brand: { domain: 'nike.example' },
        operator: 'acme-agency',
        action: 'failed',
        status: 'rejected',
        errors: [{ code: 'VALIDATION_ERROR', message: 'operator is not authorised for this brand' }],
      },
    ]);
    expect(card.outcome).toBe('failed');
    expect(card.action).toBe('failed');
    expect(card.errorCode).toBe('VALIDATION_ERROR');
    expect(card.errorDetail).toContain('not authorised');
    expect(card.accountId).toBeNull();
    expect(card.status).toBeNull();
  });

  it('reports an errors[] entry even when the action does not say failed', () => {
    // The two signals can disagree; the error is the one that cannot be optimistic.
    const card = onlyCard([
      {
        account_id: 'acc_1',
        brand: { domain: 'nike.example' },
        operator: 'acme-agency',
        action: 'created',
        status: 'active',
        errors: [{ code: 'PERMISSION_DENIED', message: 'not your account' }],
      },
    ]);
    expect(card.outcome).toBe('failed');
    expect(card.errorCode).toBe('PERMISSION_DENIED');
    expect(card.accountId).toBeNull();
  });

  it('skips a seller that failed outright rather than showing an account that never came back', () => {
    expect(decodeAccountStrip(exchange([LIVE_ACCOUNT], true))).toBeNull();
  });

  it('returns null for an empty accounts list', () => {
    // Not an empty success: nothing usable came back, and the caller turns null into a classified
    // absence with a reason a demonstrator can read out.
    expect(decodeAccountStrip(exchange([]))).toBeNull();
  });

  it('returns null when accounts is absent or not a list', () => {
    expect(decodeAccountStrip(exchange(undefined))).toBeNull();
    expect(decodeAccountStrip(exchange({ account_id: 'acc_1' }))).toBeNull();
  });

  it('returns null for a body that is not a fan-out at all', () => {
    const bare: ToolExchange = {
      callId: 'c',
      toolName: 'adcp_sync_accounts',
      input: {},
      callIndex: 0,
      result: { output: { accounts: [LIVE_ACCOUNT] }, status: 'success', index: 1 },
    };
    expect(decodeAccountStrip(bare)).toBeNull();
  });
});
