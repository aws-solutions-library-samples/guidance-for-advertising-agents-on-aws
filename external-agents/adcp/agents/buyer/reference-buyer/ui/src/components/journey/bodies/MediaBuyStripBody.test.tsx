/**
 * The Media Buy strip's rendering honesty.
 *
 * The decoder is tested exhaustively in `lib/journey/mediaBuyStrip.test.ts`; this asserts the two
 * things the component itself is responsible for:
 *
 *   - a refused or failed buy renders its own state, NOT a budget bar, so a declined buy is never
 *     shown with the chrome of a booked one;
 *   - a detail section with nothing in it renders nothing, rather than an empty grid cell or a dash.
 */
import { cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import type { MediaBuyStripData, PackageCard } from '../../../lib/journey/types';
import { MediaBuyStripBody } from './MediaBuyStripBody';

afterEach(cleanup);

function mkCard(overrides: Partial<PackageCard>): PackageCard {
  return {
    key: 'pkg-0',
    title: 'prod-x',
    productId: 'prod-x',
    packageId: null,
    mediaBuyId: 'mb-1',
    status: 'active',
    outcome: 'booked',
    refusalCode: null,
    refusalDetail: null,
    refusalField: null,
    budget: 2800,
    currency: 'USD',
    bidPrice: null,
    pricingOptionId: null,
    pacing: 'even',
    impressions: null,
    formats: [],
    paused: false,
    startTime: null,
    endTime: null,
    confirmedAt: null,
    creativeDeadline: null,
    revision: null,
    agencyEstimate: null,
    optimizationGoals: [],
    targeting: [],
    creativeAssignments: [],
    measurement: [],
    performanceStandards: [],
    catalogs: [],
    context: null,
    ...overrides,
  };
}

// A single card, so no active-card cycle runs during the assertion.
function mkData(card: PackageCard): MediaBuyStripData {
  return {
    cards: [card],
    totalBudget: '$2,800',
    currency: 'USD',
    packageCount: 1,
    flightStart: null,
    flightEnd: null,
    maxBudget: card.budget ?? 0,
    statusSummary: card.status,
  };
}

describe('MediaBuyStripBody', () => {
  it('renders a booked package with a budget bar and its open drawer rows', () => {
    const { container, getByText } = render(
      <MediaBuyStripBody
        data={mkData(mkCard({ title: 'Podcast Mid-Roll', optimizationGoals: ['listen_complete'] }))}
        revealed
      />,
    );

    expect(getByText('Podcast Mid-Roll')).toBeTruthy();
    expect(container.querySelector('.mb-bar')).not.toBeNull();
    // The single card is active, so its drawer content is present.
    expect(getByText('listen_complete')).toBeTruthy();
  });

  it('renders a refused buy as a refusal, never a budget bar', () => {
    const { container, getByText } = render(
      <MediaBuyStripBody
        data={mkData(
          mkCard({
            outcome: 'refused',
            status: 'refused by seller',
            budget: null,
            mediaBuyId: null,
            refusalCode: 'VALIDATION_ERROR',
          }),
        )}
        revealed
      />,
    );

    expect(getByText(/refused by seller/)).toBeTruthy();
    expect(container.querySelector('.mb-bar')).toBeNull();
  });

  it('renders no detail section when the package carried no detail fields', () => {
    const { container } = render(
      <MediaBuyStripBody data={mkData(mkCard({}))} revealed />,
    );

    expect(container.querySelector('.mb-section')).toBeNull();
  });
});
