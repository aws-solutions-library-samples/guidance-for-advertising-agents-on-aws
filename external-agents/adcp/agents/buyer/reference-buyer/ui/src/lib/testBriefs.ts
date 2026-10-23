/**
 * Example briefs offered in the chat's empty state.
 *
 * The composer accepts free text, which is the right interface and a poor starting point: a reader
 * facing an empty box has to guess both what this agent can do and how to phrase it. These are
 * starting points, not a menu -- every one is ordinary prose that could have been typed.
 *
 * ## Why two categories
 *
 * The agent's tool surface splits in two, and a brief written for one half does nothing useful against
 * the other. `discover` maps to the tools that look for inventory before anything is bought
 * (`get_products`, `get_signals`, `list_creative_formats`, market rates). `measure` maps to the tools
 * that read back what already exists (`get_media_buys`, `get_media_buy_delivery`,
 * `get_plan_audit_logs`). Asking for delivery numbers before a campaign exists is a reasonable thing
 * to try and a confusing first experience, so the two are labelled rather than mixed.
 *
 * Every prompt here is phrased to exercise a tool the buyer agent actually has -- see the `@tool`
 * definitions in `adcp_tools.py`. They are not scripted: the agent decides what to call, and a brief
 * that finds nothing shows that honestly.
 */

export interface TestBrief {
  /** Stable identifier, used as a React key and a `data-testid` suffix. */
  readonly id: string;
  /** Button text. Short enough to scan; the prompt carries the detail. */
  readonly label: string;
  /** What is sent as the turn, verbatim. */
  readonly prompt: string;
}

/**
 * The advertiser account every brief here refers to.
 *
 * One account, shared by all of them, because `(brand_domain, operator)` is the account's natural key at
 * a seller — two briefs with different pairs would declare two different accounts, and a media buy made
 * under one would be invisible to a "list my campaigns" brief written for the other.
 *
 * Both are `.example` domains. That TLD is reserved by RFC 2606 and is the same choice
 * `verify_governance_journey_live.py` makes, for the same reason: a demo must not declare an account
 * against a domain that could be mistaken for a real advertiser's.
 *
 * Naming these is what stops the agent asking. `adcp_sync_accounts`'s own docstring tells it to ask
 * rather than guess the brand domain, so a brief that omits it is correctly answered with a question.
 */
export const EXAMPLE_ACCOUNT = {
  brandDomain: 'northwind-coffee.example',
  operator: 'meridian-media.example',
} as const;

export interface Flight {
  /** ISO `YYYY-MM-DD`. */
  readonly start: string;
  readonly end: string;
}

/**
 * A flight window for the example briefs: the whole of next month.
 *
 * Computed rather than written down. A hardcoded window is in the future on the day it is typed and in
 * the past a few months later, and a brief carrying past dates produces MORE follow-up questions than one
 * carrying none — which is the opposite of what these are for.
 *
 * Whole calendar months read naturally in a brief ("2026-10-01 to 2026-10-31") and are unambiguous about
 * inclusivity in a way that "the next 30 days" is not.
 */
export function exampleFlight(now: Date): Flight {
  const year = now.getUTCFullYear();
  const month = now.getUTCMonth();
  const iso = (d: Date): string => d.toISOString().slice(0, 10);
  // Day 1 of the next month, and day 0 of the month after that — which is the last day of the next
  // month, and lets the calendar handle month lengths and leap years.
  return {
    start: iso(new Date(Date.UTC(year, month + 1, 1))),
    end: iso(new Date(Date.UTC(year, month + 2, 0))),
  };
}

const FLIGHT = exampleFlight(new Date());

/** Names the account, so the agent does not have to ask who is buying. */
function forAccount(prompt: string): string {
  return (
    `${prompt} The advertiser is ${EXAMPLE_ACCOUNT.brandDomain} and the operator declaring the ` +
    `account is ${EXAMPLE_ACCOUNT.operator}.`
  );
}

/** Names the flight window, so the agent does not have to ask when it runs. */
function forFlight(prompt: string): string {
  return `${prompt} Flight dates are ${FLIGHT.start} to ${FLIGHT.end}.`;
}

export type BriefCategoryId = 'discover' | 'measure';

export interface BriefCategory {
  readonly id: BriefCategoryId;
  readonly title: string;
  /** One line on what this group is for, so the split is self-explanatory. */
  readonly blurb: string;
  readonly briefs: readonly TestBrief[];
}

/**
 * Sales agents are named where the brief genuinely targets one.
 *
 * Poseidon sells audio — broadcast radio, streaming stations and podcasts — and Gotham is a news
 * publisher, so a drive-time audio brief and a food-section brief each have an obvious counterparty.
 * The rest say "all connected sales agents", which answers "which seller?" just as definitively without
 * asserting a specialism a seller may not have.
 *
 * ## Name what you want. Never negate.
 *
 * A brief is matched semantically against the seller's catalogue, so the words in it pull toward
 * inventory rather than filtering it out. Two things follow, both measured against the deployed sellers
 * rather than assumed:
 *
 *   - **A negation is not a filter, and can empty the result.** "Food, puzzles, product reviews and
 *     sport sections. Not news, politics, opinion, world or investigations." returned **0 products**
 *     from Gotham. The exclusions did not narrow the match; they destroyed it.
 *   - **The positive words win, whatever else the brief says.** This list used to carry "brand-safe
 *     placements alongside news content, avoiding politics", which returned **10 of 10 products from
 *     `Gotham Daily`, every one of them political** — because "news" is a precise match for Gotham
 *     Daily, whose sub-sections are Top Stories, Politics, World and Investigations. The
 *     "avoiding politics" clause did nothing.
 *
 * So a brand-safety brief names the sections it wants. Gotham has eight verticals and only two are
 * politics-adjacent; measured, one vertical per brief, 0 political products in each:
 *
 *   | brief names                                   | lands on           |
 *   |-----------------------------------------------|--------------------|
 *   | recipes, weeknight dinners, baking            | Gotham Kitchen     |
 *   | crossword, word games, logic puzzles          | Gotham Puzzles     |
 *   | product recommendations, buying guides        | Gotham Picks       |
 *   | markets, economy, personal finance            | Gotham Markets     |
 *   | football, basketball, soccer                  | Gotham Sports Desk |
 *
 * Spanish-language culture and current affairs lands on `Gotham en Español`, where 2 of 10 fall in
 * `Opinión` — fine for a Spanish-language brief, which is why that one is not also the brand-safety
 * brief.
 *
 * ## Objectives
 *
 * Each brief that leads toward a plan/buy carries an explicit `Objective:` sentence (a KPI plus a
 * pacing/constraint), so the agent has what it needs to proceed instead of stalling to ask "what are
 * your objectives?". The two pure capability queries here (creative formats, "what can you do?") and
 * the read-back briefs in `MEASURE` carry none deliberately: they commit to no spend, so stating a
 * buy objective would be inventing intent the brief does not have.
 */
const DISCOVER: readonly TestBrief[] = [
  {
    id: 'discover-ctv-sports',
    label: 'CTV sports, $50k',
    prompt: forFlight(
      forAccount(
        'I have $50,000 to spend on connected TV around live sports in the US. Ask all connected ' +
          'sales agents. What inventory can you find, and which packages fit best? Objective: ' +
          'maximise reach among live-sports viewers within the $50,000 budget, with even pacing ' +
          'across the flight.',
      ),
    ),
  },
  {
    id: 'discover-audio-drive-time',
    label: 'Drive-time audio',
    prompt: forFlight(
      forAccount(
        'Looking for drive-time audio inventory in major US metros. Poseidon Inventory Seller is the ' +
          'sales agent to ask. Budget is flexible around $25,000. What is available and what would it ' +
          'cost? Objective: maximise drive-time listener reach across major metros, even pacing, ' +
          'keeping spend near $25,000.',
      ),
    ),
  },
  {
    id: 'discover-brand-safe-food',
    label: 'Brand-safe food',
    prompt: forFlight(
      forAccount(
        'I need brand-safe inventory in the food and cooking sections: recipes, weeknight dinners ' +
          'and baking. Ask The Gotham Gazette. What inventory fits, and what are the brand safety ' +
          'controls? Objective: maximise reach in brand-safe food and cooking sections, prioritising ' +
          'viewability and brand safety over raw scale.',
      ),
    ),
  },
  {
    id: 'discover-personal-finance',
    label: 'Personal finance',
    prompt: forFlight(
      forAccount(
        'Markets, economy and personal finance content for an investor audience. Ask all connected ' +
          'sales agents — I want both the publisher sections and the finance podcasts. What fits? ' +
          'Objective: reach an investor audience across finance sections and finance podcasts with a ' +
          'balanced split and even pacing.',
      ),
    ),
  },
  {
    id: 'discover-true-crime-podcasts',
    label: 'True crime podcasts',
    prompt: forFlight(
      forAccount(
        'True crime podcasts with engaged listeners. Poseidon Inventory Seller is the sales agent ' +
          'to ask. Around $30,000 — what is available, and what does mid-roll cost? Objective: ' +
          'maximise engaged-listener reach on true-crime podcasts via mid-roll within ~$30,000.',
      ),
    ),
  },
  {
    id: 'discover-spanish-language',
    label: 'Spanish-language',
    prompt: forFlight(
      forAccount(
        'Spanish-language inventory for US Hispanic audiences — culture and current affairs ' +
          'sections, plus Spanish-language radio. Ask all connected sales agents. Objective: ' +
          'maximise reach among US Hispanic audiences across Spanish-language sections and radio, ' +
          'with even pacing.',
      ),
    ),
  },
  {
    id: 'discover-audience-signals',
    label: 'Audience signals',
    prompt: forAccount(
      'What audience signals can I target for in-market car buyers, and which sales agents support ' +
        'them? Objective: identify targetable in-market car-buyer signals for an upcoming campaign.',
    ),
  },
  {
    id: 'discover-creative-formats',
    label: 'Creative formats',
    prompt: 'Which creative formats do your sales agents accept, and what are the specs for each?',
  },
  {
    id: 'discover-capabilities',
    label: 'What can you do?',
    prompt:
      'Which sales agents are you connected to, and what AdCP capabilities does each one support?',
  },
];

const MEASURE: readonly TestBrief[] = [
  {
    id: 'measure-list-campaigns',
    label: 'List my campaigns',
    prompt: forAccount(
      'List the media buys that already exist for my account, with their current status.',
    ),
  },
  {
    id: 'measure-delivery',
    label: 'Delivery so far',
    prompt: forAccount(
      'How are my active campaigns delivering so far? Show impressions, spend and pacing against ' +
        'what was booked.',
    ),
  },
  {
    id: 'measure-underdelivering',
    label: 'Anything underdelivering?',
    prompt: forAccount(
      'Is anything underdelivering or off pace? If so, which line items, and by how much?',
    ),
  },
  {
    id: 'measure-spend-by-seller',
    label: 'Spend by sales agent',
    prompt: forAccount('Break down my spend to date by sales agent and by format.'),
  },
  {
    id: 'measure-audit-log',
    label: 'Plan audit trail',
    prompt: forFlight(
      forAccount(
        'Show the audit log for my current plan: what was decided, when, and which agent reported it.',
      ),
    ),
  },
];

export const BRIEF_CATEGORIES: readonly BriefCategory[] = [
  {
    id: 'discover',
    title: 'Discover inventory',
    blurb: 'Find and price inventory before anything is booked.',
    briefs: DISCOVER,
  },
  {
    id: 'measure',
    title: 'Check existing campaigns',
    blurb: 'Read back status, delivery and measurement for buys that already exist.',
    briefs: MEASURE,
  },
];

/** Flat view, for lookups by id. */
export function allTestBriefs(): readonly TestBrief[] {
  return BRIEF_CATEGORIES.flatMap((category) => category.briefs);
}

export function findTestBrief(id: string): TestBrief | null {
  return allTestBriefs().find((brief) => brief.id === id) ?? null;
}
