/**
 * The Accounts strip's rendering honesty.
 *
 * The decoder is tested exhaustively in `lib/journey/accountCard.test.ts`; this asserts the two
 * things the component itself is responsible for:
 *
 *   - the shared account key is shown once in the header and each seller is named on its own card,
 *     so a multi-seller fan-out is not a stack of look-alikes;
 *   - a failed reply renders its error state, NOT an account_id, so a failure is never shown with
 *     the chrome of a held account.
 *
 * A single active card cycles on a timer; the multi-card test renders two but only asserts on
 * content that is present regardless of which one is active (both sellers' names, both ids), so the
 * timer does not make it flaky.
 */
import { cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import type { AccountCard, AccountStripData } from '../../../lib/journey/types';
import { AccountStripBody } from './AccountStripBody';

afterEach(cleanup);

function mkCard(overrides: Partial<AccountCard>): AccountCard {
  return {
    key: 'acct-0',
    seller: 'Reference Seller',
    outcome: 'active',
    action: 'created',
    status: 'active',
    accountId: 'acc_ref',
    errorCode: null,
    errorDetail: null,
    ...overrides,
  };
}

function mkData(cards: readonly AccountCard[]): AccountStripData {
  return {
    cards,
    brand: 'northwind-coffee.example',
    operator: 'meridian-media.example',
    sellerCount: cards.length,
  };
}

describe('AccountStripBody', () => {
  it('shows the shared account key once in the header and names each seller on its card', () => {
    const { container, getByText } = render(
      <AccountStripBody
        data={mkData([
          mkCard({ key: 'a', seller: 'Reference Seller', accountId: 'acc_ref' }),
          mkCard({ key: 'b', seller: 'Poseidon', accountId: 'acc_pos' }),
        ])}
        revealed
      />,
    );

    // The natural key appears once, in the header.
    expect(container.querySelectorAll('.acct-account-key')).toHaveLength(1);
    expect(getByText('northwind-coffee.example / meridian-media.example')).toBeTruthy();
    // Both sellers are named, and both ids are present regardless of which card is active.
    expect(getByText('Reference Seller')).toBeTruthy();
    expect(getByText('Poseidon')).toBeTruthy();
    expect(getByText('acc_ref')).toBeTruthy();
    expect(getByText('acc_pos')).toBeTruthy();
  });

  it('renders a failed reply as its error, never an account_id', () => {
    const { container, getByText } = render(
      <AccountStripBody
        data={mkData([
          mkCard({
            outcome: 'failed',
            action: 'failed',
            status: null,
            accountId: null,
            errorCode: 'VALIDATION_ERROR',
            errorDetail: 'operator is not authorised for this brand',
          }),
        ])}
        revealed
      />,
    );

    expect(getByText('VALIDATION_ERROR')).toBeTruthy();
    expect(getByText(/not authorised/)).toBeTruthy();
    expect(container.querySelector('.acct-id')).toBeNull();
  });
});
