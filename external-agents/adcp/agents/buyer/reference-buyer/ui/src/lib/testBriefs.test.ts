import { describe, expect, it } from 'vitest';

import {
  BRIEF_CATEGORIES,
  EXAMPLE_ACCOUNT,
  allTestBriefs,
  exampleFlight,
  findTestBrief,
  type BriefCategoryId,
} from './testBriefs';

describe('the example brief catalogue', () => {
  it('offers both halves of the agent’s tool surface', () => {
    // The split is the point: a discovery brief and a measurement brief exercise different tools, and
    // offering only one half makes the other look unsupported.
    const ids = BRIEF_CATEGORIES.map((c) => c.id);
    expect(ids).toEqual<BriefCategoryId[]>(['discover', 'measure']);
  });

  it('gives every category something to click', () => {
    for (const category of BRIEF_CATEGORIES) {
      expect(category.briefs.length).toBeGreaterThan(0);
    }
  });

  it('keeps ids unique, since they are React keys and test handles', () => {
    const ids = allTestBriefs().map((b) => b.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('carries a real prompt for every button, not a label reused as one', () => {
    // A button whose prompt is just its own label sends the agent two or three words and reads as a
    // broken example rather than a starting point.
    for (const brief of allTestBriefs()) {
      expect(brief.label.trim()).not.toBe('');
      expect(brief.prompt.trim()).not.toBe('');
      expect(brief.prompt.length).toBeGreaterThan(brief.label.length);
      expect(brief.prompt.split(/\s+/).length).toBeGreaterThanOrEqual(8);
    }
  });

  it('describes each category, so the split explains itself', () => {
    for (const category of BRIEF_CATEGORIES) {
      expect(category.title.trim()).not.toBe('');
      expect(category.blurb.trim()).not.toBe('');
    }
  });

  it('finds a brief by id and reports an unknown one as absent', () => {
    const first = allTestBriefs()[0];
    expect(first).toBeDefined();
    expect(findTestBrief(first!.id)).toEqual(first);
    expect(findTestBrief('no-such-brief')).toBeNull();
  });

  it('asks the measurement briefs about existing buys rather than new inventory', () => {
    // Guards the categorisation itself: these prompts have to read back state, because that is what
    // separates them from the discovery set.
    const measure = BRIEF_CATEGORIES.find((c) => c.id === 'measure');
    expect(measure).toBeDefined();
    for (const brief of measure!.briefs) {
      expect(brief.prompt.toLowerCase()).toMatch(
        /campaign|media buy|deliver|spend|pacing|audit|underdeliver/,
      );
    }
  });
});

describe('the briefs carry the detail the agent would otherwise ask for', () => {
  /**
   * The agent is instructed to ask rather than guess an account's identity — `adcp_sync_accounts`'s
   * docstring says so explicitly, because the wrong brand domain declares an account for the wrong
   * advertiser. So a brief that omits it is answered with a question, which is correct behaviour and a
   * poor first experience. These assert the briefs supply it.
   */
  const accountScoped = allTestBriefs().filter((b) =>
    b.prompt.includes(EXAMPLE_ACCOUNT.brandDomain),
  );

  it('names the advertiser and the operator together, never just one', () => {
    // Both halves are the account's natural key. A brief naming only the brand still gets asked for the
    // operator, so it is no better than naming neither.
    for (const brief of accountScoped) {
      expect(brief.prompt).toContain(EXAMPLE_ACCOUNT.operator);
    }
    expect(accountScoped.length).toBeGreaterThan(5);
  });

  it('uses ONE account across every brief', () => {
    // `(brand_domain, operator)` is the account's key at the seller. Two briefs with different pairs
    // declare two different accounts, and a buy made under one is invisible to a "list my campaigns"
    // brief written for the other.
    const brands = new Set<string>();
    for (const brief of allTestBriefs()) {
      for (const match of brief.prompt.matchAll(/[a-z0-9-]+\.example/g)) brands.add(match[0]);
    }
    expect([...brands].sort()).toEqual(
      [EXAMPLE_ACCOUNT.brandDomain, EXAMPLE_ACCOUNT.operator].sort(),
    );
  });

  it('keeps the example account on a reserved TLD', () => {
    // RFC 2606 reserves `.example`. A demo must not declare an account against a domain that could be
    // mistaken for a real advertiser's — the same choice verify_governance_journey_live.py makes.
    expect(EXAMPLE_ACCOUNT.brandDomain).toMatch(/\.example$/);
    expect(EXAMPLE_ACCOUNT.operator).toMatch(/\.example$/);
  });

  it('gives every brief that plans a flight an explicit ISO date range', () => {
    const dated = allTestBriefs().filter((b) => b.prompt.includes('Flight dates are'));
    expect(dated.length).toBeGreaterThan(0);
    for (const brief of dated) {
      expect(brief.prompt).toMatch(/Flight dates are \d{4}-\d{2}-\d{2} to \d{4}-\d{2}-\d{2}\./);
    }
  });

  it('names a sales agent or says to ask them all, so "which seller?" is answered', () => {
    const discover = BRIEF_CATEGORIES.find((c) => c.id === 'discover')!;
    const targeted = discover.briefs.filter((b) =>
      /Poseidon|Gotham|all connected sales agents/.test(b.prompt),
    );
    expect(targeted.length).toBeGreaterThan(2);
  });
});

describe('exampleFlight', () => {
  it('returns the whole of next month', () => {
    expect(exampleFlight(new Date('2026-08-28T00:00:00Z'))).toEqual({
      start: '2026-09-01',
      end: '2026-09-30',
    });
  });

  it('rolls over the year end', () => {
    expect(exampleFlight(new Date('2026-12-15T00:00:00Z'))).toEqual({
      start: '2027-01-01',
      end: '2027-01-31',
    });
  });

  it('lets the calendar handle February, including a leap year', () => {
    expect(exampleFlight(new Date('2027-01-10T00:00:00Z')).end).toBe('2027-02-28');
    expect(exampleFlight(new Date('2028-01-10T00:00:00Z')).end).toBe('2028-02-29');
  });

  it('is always in the future, whatever the day', () => {
    // The reason the dates are computed rather than written down: a brief carrying PAST dates produces
    // more follow-up questions than one carrying none, which is the opposite of the point.
    for (const day of ['2026-01-01', '2026-01-31', '2026-02-28', '2026-06-30', '2026-12-31']) {
      const now = new Date(`${day}T00:00:00Z`);
      const flight = exampleFlight(now);
      expect(new Date(`${flight.start}T00:00:00Z`).getTime()).toBeGreaterThan(now.getTime());
      expect(flight.end >= flight.start).toBe(true);
    }
  });
});


describe('briefs name what they want instead of negating what they do not', () => {
  /**
   * A brief is matched semantically against the seller's catalogue, so its words pull toward inventory
   * rather than filtering it. Two measurements against the deployed sellers, both from the incident
   * that produced these tests:
   *
   *   - "Brand-safe placements alongside news content, avoiding politics" returned 10 of 10 products
   *     from `Gotham Daily`, every one political. "news" matched the vertical whose sub-sections are
   *     Top Stories, Politics, World and Investigations; the "avoiding politics" clause did nothing.
   *   - "Food, puzzles, product reviews and sport sections. Not news, politics, opinion, world or
   *     investigations." returned 0 products. The exclusions did not narrow the match, they destroyed it.
   *
   * So these assert the phrasing rule rather than the outcome, which is all a unit test can see. The
   * outcome is verified against real sellers, and the measured mapping is recorded in the module
   * docstring.
   */

  const NEGATIONS = [
    'avoiding',
    'avoid ',
    'not news',
    'no news',
    'exclude',
    'excluding',
    'without politics',
    'no politics',
  ];

  it('never asks a seller to avoid a topic, because that empties the result', () => {
    for (const brief of allTestBriefs()) {
      const prompt = brief.prompt.toLowerCase();
      for (const negation of NEGATIONS) {
        expect(
          prompt.includes(negation),
          `${brief.id} relies on the negation "${negation.trim()}", which returns fewer or zero ` +
            `products. Name the sections you want instead.`,
        ).toBe(false);
      }
    }
  });

  it('states the brand-safety brief as sections, and does not send it after news', () => {
    const brief = findTestBrief('discover-brand-safe-food');

    expect(brief, 'the brand-safety brief should exist').not.toBeNull();
    // Names the sections. Measured: lands on Gotham Kitchen, 0 of 10 political.
    expect(brief!.prompt).toMatch(/recipes/i);
    expect(brief!.prompt).toMatch(/brand.safe/i);
    // The word that broke the old one. "news" matches Gotham Daily, which is where politics lives.
    expect(brief!.prompt.toLowerCase()).not.toContain('news content');
  });

  it('covers more than one Gotham vertical, so the demo is not all one section', () => {
    // Each of these was measured returning one vertical with 0 political products. Together they show
    // that a news publisher has non-news inventory, which is the point of the set.
    const themes = [/recipes/i, /personal finance/i, /true crime/i, /spanish/i];
    const prompts = allTestBriefs().map((brief) => brief.prompt);

    for (const theme of themes) {
      expect(
        prompts.some((prompt) => theme.test(prompt)),
        `no brief covers ${theme}`,
      ).toBe(true);
    }
  });
});
