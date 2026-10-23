/**
 * The registry's job is to be the only definition of phase identity, and to carry the
 * prototype's authored presentation strings without editing them. Both are asserted here.
 *
 * The authored-string checks read `.kiro/specs/journey.html` at test time rather than comparing
 * against a copy, so the prototype stays the source of truth: changing it fails here instead of
 * silently disagreeing with the registry.
 */

import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import {
  BOUND_TOOL_NAMES,
  FORMAT_KEYS,
  FORMAT_META,
  PHASES,
  RADIAL_COLOURS,
  RADIAL_RADII,
  phaseByKey,
  phaseOrder,
  phasesForTool,
  type PanelType,
  type PhaseKey,
} from './phases';

const here = dirname(fileURLToPath(import.meta.url));
const PROTOTYPE = resolve(here, '../../../../../../../.kiro/specs/journey.html');

/**
 * The prototype's source text, with JavaScript `\uXXXX` escapes decoded.
 *
 * The decoding is the point. The prototype writes its em dashes and middots as escape sequences
 * (`\u2014`, `\u00b7`) rather than as characters, so a raw substring search for the registry's
 * `sub` string finds nothing even when the two agree exactly. Comparing decoded text is what
 * makes "this string is the authored one" a real assertion rather than an accidental pass.
 */
function prototypeSource(): string {
  return readFileSync(PROTOTYPE, 'utf8').replace(/\\u([0-9a-fA-F]{4})/g, (_, hex: string) =>
    String.fromCharCode(Number.parseInt(hex, 16)),
  );
}

/**
 * Phases the prototype itself authored. `bind`, `plan` and `report` are this project's additions:
 * the two AdCP setup tasks that precede any check, and the one that closes the loop after the buy.
 */
const AUTHORED: readonly PhaseKey[] = [
  'discover',
  'govern',
  'score',
  'creative',
  'book',
  'activate',
];
//: Phases this project added beyond the prototype. `accounts` is the newest: `sync_accounts` was the
//: first recorded call in every real session and the prerequisite for Bind (`sync_governance` answers
//: `ACCOUNT_NOT_FOUND` without it), yet had no panel — so the rail opened on Bind and read as though that
//: were the buyer's first act.
const ADDED: readonly PhaseKey[] = ['accounts', 'bind', 'plan', 'report'];

const PANEL_TYPES: readonly PanelType[] = [
  'fmtgrid',
  'radial',
  'scan',
  'card',
  'meters',
  'bindflow',
  'planviz',
  'mediabuy',
  'accounts',
];

describe('phase registry', () => {
  it('has a unique key per phase', () => {
    const keys = PHASES.map((p) => p.key);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it('covers exactly the authored phases plus this project\u2019s additions', () => {
    expect(new Set(PHASES.map((p) => p.key))).toEqual(new Set([...ADDED, ...AUTHORED]));
  });

  it('uses only known panel types', () => {
    // The prototype's five, plus the ones this project's added phases introduced: bindflow and
    // planviz (Bind/Plan), and mediabuy (Book's package strip).
    PHASES.forEach((p) => expect(PANEL_TYPES).toContain(p.type));
  });

  it('reuses one radial renderer for both governance panels', () => {
    // The reason five renderers serve nine phases. If these ever differ, the buyer-side and
    // seller-side panels can drift apart visually, which the exact-reproduction rule forbids.
    expect(phaseByKey('govern')?.type).toBe('radial');
    expect(phaseByKey('score')?.type).toBe('radial');
  });

  it('puts the two setup phases before any governance check', () => {
    // The dependency that is genuinely a dependency: sync_governance before sync_plans, and both
    // before either check. A check cannot precede the plan it validates against, and a seller with
    // no registered governance agent has nothing to consult.
    //
    // Note what is NOT asserted here: that either precedes Discover. Discovery is a read and needs
    // neither (see the O1 tests below).
    expect(phaseOrder('bind')).toBeLessThan(phaseOrder('plan'));
    expect(phaseOrder('plan')).toBeLessThan(phaseOrder('govern'));
    expect(phaseOrder('plan')).toBeLessThan(phaseOrder('score'));
  });

  it('orders the lifecycle so a buy cannot precede its own INTENT check', () => {
    // The intent check must precede Book: the buyer cannot send a spend-commit without the token that
    // check produces. That is the AdCP MUST, and it is what this test is about.
    expect(phaseOrder('govern')).toBeLessThan(phaseOrder('book'));
    expect(phaseOrder('book')).toBeLessThan(phaseOrder('activate'));
    expect(phaseOrder('discover')).toBeLessThan(phaseOrder('book'));
  });

  it('puts the seller\u2019s EXECUTION check after the buy, because it happens inside it', () => {
    // This previously asserted the inverse — `score < book` — under the heading "a buy cannot precede its
    // own governance". That heading is true of Govern and false of Score, and conflating the two put the
    // panel two slots ahead of the event it reports.
    //
    // The seller's execution check runs from inside `create_media_buy`, so it is a consequence of Book.
    // From a real run's audit trail: intent at 21:46:45, execution at 21:47:04, 19 seconds later with the
    // buy in between. Score's own data source, `get_plan_audit_logs`, was the LAST call in every session.
    expect(phaseOrder('book')).toBeLessThan(phaseOrder('score'));
    // Still after the plan it judges, and still before the outcome is confirmed.
    expect(phaseOrder('plan')).toBeLessThan(phaseOrder('score'));
    expect(phaseOrder('score')).toBeLessThan(phaseOrder('report'));
  });

  it('declares the advertiser account before binding governance to it', () => {
    // A genuine dependency, not narrative: every account-scoped task is keyed on the (brand domain,
    // operator) pair, and a seller that has never been told about the account answers
    // `ACCOUNT_NOT_FOUND` to `sync_governance`.
    expect(phaseOrder('accounts')).toBeLessThan(phaseOrder('bind'));
    // But not before Discover, which is a read that needs no account (see the O1 tests).
    expect(phaseOrder('discover')).toBeLessThan(phaseOrder('accounts'));
  });

  it('labels `n` consistently with registry position', () => {
    // `n` is display only, but a label disagreeing with the order it labels would be its own
    // small lie on screen.
    PHASES.forEach((phase, index) => {
      expect(phase.n).toBe(String(index + 1).padStart(2, '0'));
    });
  });

  it('gives every phase a bound tool name', () => {
    PHASES.forEach((p) => expect(p.binding.toolName.length).toBeGreaterThan(0));
  });

  it('feeds the two governance panels from two different tools', () => {
    // REVISED. This used to assert that both governance panels bound to `adcp_check_governance` and
    // were told apart by `selector` ('latest_intent' vs 'execution'). That design could not work and
    // the test was pinning it in place: the selector only chose first-versus-last exchange, so a
    // session with one check gave both panels the SAME exchange, and the buyer's own intent check
    // rendered twice — once captioned "Seller's governance agent scores execution".
    //
    // The seller's execution check is not in the buyer's session at all; it is an outbound call the
    // seller makes to the governance agent. So Score reads it back from the audit log, and the two
    // panels are now separated by their SOURCE rather than by a position within one tool's calls.
    const governance = phasesForTool('adcp_check_governance');
    expect(governance.map((p) => p.key)).toEqual(['govern']);

    const score = phaseByKey('score');
    expect(score?.binding.toolName).toBe('adcp_get_plan_audit_logs');
    // And it does not select by position any more. Its decoder partitions on `check_type`.
    expect(score?.binding.selector).toBeUndefined();
  });

  it('names the task Score actually calls, separately from the task that produced its finding', () => {
    // `op` is the task that produced the fact; `sourceOp` is the task the buyer invoked. Score is the
    // one phase where they differ, and absence messaging uses the second — otherwise a failed
    // audit-log read would be reported as "check_governance returned an error", naming a call this
    // path never makes.
    const score = phaseByKey('score');
    expect(score?.op).toBe('check_governance');
    expect(score?.binding.sourceOp).toBe('get_plan_audit_logs');
    expect(score?.party).toBe('seller governance agent');
  });

  it('reports every bound tool name once', () => {
    expect(new Set(BOUND_TOOL_NAMES).size).toBe(BOUND_TOOL_NAMES.length);
    expect(BOUND_TOOL_NAMES).toContain('adcp_get_products');
    expect(BOUND_TOOL_NAMES).toContain('adcp_check_governance');
    expect(BOUND_TOOL_NAMES).toContain('adcp_get_plan_audit_logs');
  });

  it('returns undefined for an unknown key rather than a placeholder', () => {
    expect(phaseByKey('nope' as PhaseKey)).toBeUndefined();
    expect(phaseOrder('nope' as PhaseKey)).toBe(-1);
  });
});

/**
 * The four open items, once answered, are decisions this file now has to hold still. Each of these
 * fails if the decision is quietly reversed, which is the only reason they are worth writing.
 */
describe('answered open items', () => {
  it('O1: leads with Discover, then the two setup phases', () => {
    // REVISED. This test previously pinned bind=0, plan=1, discover=2, on the reading that AdCP's
    // minimal sequence begins with sync_governance and sync_plans. That over-read the dependency:
    // the requirement is that governance is bound and a plan registered before a seller is asked to
    // ACT (Book), not before it is asked to READ. `get_products` takes a brand and a brief, not an
    // account, and consults no governance agent.
    //
    // It also misdescribed the session: a brief arrives, inventory is discovered, and Bind and Plan
    // happen once there is a commitment to make. Leading with them left two panels at the head of
    // the rail waiting on work not yet reached, which reads as a stall rather than as absence.
    //
    // The real dependencies are asserted separately below and above, and all still hold.
    //
    // `accounts` now sits between Discover and Bind: `sync_accounts` is the prerequisite for
    // `sync_governance`, and it is a buyer/seller setup step that Discover does not need.
    expect(phaseOrder('discover')).toBe(0);
    expect(phaseOrder('accounts')).toBe(1);
    expect(phaseOrder('bind')).toBe(2);
    expect(phaseOrder('plan')).toBe(3);
  });

  it('O1: Discover needs neither setup phase, because it is a read', () => {
    // The substance of the reordering, asserted rather than left to the comment above: Discover is
    // bound to a SELLER read whose own binding carries no account and no governance selector.
    const discover = phaseByKey('discover');
    expect(discover?.party).toBe('seller');
    expect(discover?.binding.selector).toBeUndefined();
    // And it still precedes the commitment, which is the ordering that does matter.
    expect(phaseOrder('discover')).toBeLessThan(phaseOrder('book'));
  });

  it('O2: reports the plan outcome after the buy, and before delivery', () => {
    // Not last. `report_plan_outcome` confirms the plan's execution, which is known at confirmation
    // time; it is not a report of delivery results and does not wait on Activate.
    expect(phaseOrder('book')).toBeLessThan(phaseOrder('report'));
    expect(phaseOrder('report')).toBeLessThan(phaseOrder('activate'));
  });

  it('O2: keeps committed spend and delivered spend on separate chip keys', () => {
    // Chips merge last-wins by key. Sharing `spend` would let Activate's delivered figure replace
    // Report's committed figure under an unchanged label, with nothing on screen to show which won.
    const committed = phaseByKey('report')?.binding.entities?.map((e) => e.key) ?? [];
    const delivered = phaseByKey('activate')?.binding.entities?.map((e) => e.key) ?? [];
    expect(committed).toContain('committed');
    expect(delivered).toContain('spend');
    expect(committed.some((key) => delivered.includes(key))).toBe(false);
  });

  it('O3: Govern selects the latest buyer-side intent check, not purchase-only', () => {
    // Purchase-only would leave the panel empty in a discovery-only session, which is the session
    // the first increment actually demonstrates.
    expect(phaseByKey('govern')?.binding.selector).toBe('latest_intent');
  });

  it('O4: sources the audience chip from the tool that measures it', () => {
    const audience = phaseByKey('govern')?.binding.entities?.find((e) => e.key === 'audience');
    expect(audience).toBeDefined();
    // Revealed beside Govern, because that is where the prototype puts it. Measured by signals,
    // because a governance check does not measure audience coverage.
    expect(audience?.sourceToolName).toBe('adcp_get_signals');
    expect(phaseByKey('govern')?.binding.toolName).toBe('adcp_check_governance');
  });

  it('O4: says nothing about "match" anywhere in the registry', () => {
    // The chip's value must be expressed as coverage. A real coverage figure labelled "match" is
    // worse than an absent chip: the number withstands scrutiny and the claim it makes does not.
    expect(JSON.stringify(PHASES).toLowerCase()).not.toContain('match');
  });
});

describe('authored presentation strings match the prototype', () => {
  const source = prototypeSource();

  /**
   * Fields that deliberately diverge from the prototype, with the reason.
   *
   * Listed rather than skipped, so the divergence is a recorded decision and any OTHER drift in the same
   * field still fails.
   */
  const AUTHORED_EXCEPTIONS: Partial<Record<PhaseKey, readonly string[]>> = {
    // The prototype authored `get_creative_features` as Creative's op. That is a GOVERNANCE-agent task
    // (in the SDK it is overridden with validation only on `GovernanceHandler`), while the panel shows one
    // card per format a seller accepts — which comes from `list_creative_formats`, a seller task, exactly
    // as R21 said. Keeping the authored value would put a task name against a panel that task did not
    // supply. The evaluator lives on `binding.evaluatorToolName` instead.
    creative: ['op'],
    // The prototype authored Book's title as "Booked at Triton", naming one seller in copy for a panel
    // whose seller varies per session. Any other seller confirming a buy would have been labelled as
    // Triton's, and a second audio seller now exists, so the title is seller-neutral here. The seller is
    // identified by the headline and the seller notice instead.
    //
    // Recorded as an exception rather than changed in the prototype: the prototype is the authored
    // reference and this is a correctness deviation from it, not a redesign of it. Every OTHER Book
    // field is still held to the authored value by this test.
    //
    // `sub` diverges for the same class of reason as `title`, one step further. The prototype authored
    // "create_media_buy accepted — the buy goes live", which asserts an OUTCOME. A seller that refuses
    // returns `errors[]` and no `media_buy_id`, so the panel rendered `status: refused by seller` under
    // copy saying the buy was accepted and live. Reproduced live against Poseidon. Both fields now name
    // the step and leave the outcome to the rows, which are derived.
    book: ['title', 'sub'],
  };

  it.each(AUTHORED)('%s keeps its authored title, sub, party, op, col and glow', (key) => {
    const phase = phaseByKey(key);
    expect(phase).toBeDefined();
    if (!phase) return;
    const exempt = new Set(AUTHORED_EXCEPTIONS[key] ?? []);
    // Each authored string must appear verbatim in the prototype. `sub` is the one that matters
    // most: several contain em dashes that the house style guide would otherwise remove, and
    // removing them would breach the exact-reproduction constraint.
    // `title` is gated the same way `op` is. It was not, so an exception could be declared for it and
    // still fail -- the exceptions list has to actually cover every field it can name.
    if (!exempt.has('title')) expect(source).toContain(phase.title);
    if (!exempt.has('sub')) expect(source).toContain(phase.sub);
    if (!exempt.has('op')) expect(source).toContain(phase.op);
    expect(source).toContain(phase.col);
    expect(source).toContain(phase.glow);
    expect(source).toContain(phase.name);
  });

  it('preserves the em dashes in authored copy', () => {
    // Subs listed in AUTHORED_EXCEPTIONS are no longer the prototype's string, so they cannot be
    // required to appear in it. They are still held to the divergence being declared: the check above
    // fails for any sub that drifts without an entry here.
    const authoredSubs = AUTHORED.filter(
      (k) => !(AUTHORED_EXCEPTIONS[k] ?? []).includes('sub'),
    ).map((k) => phaseByKey(k)?.sub ?? '');
    const withEmDash = authoredSubs.filter((s) => s.includes('\u2014'));
    // Three of the prototype's subtitles use one. If a well-meaning edit strips them this fails,
    // which is the intent.
    expect(withEmDash.length).toBeGreaterThan(0);
    withEmDash.forEach((s) => expect(source).toContain(s));
  });

  it('does not use em dashes in the copy this project added', () => {
    // New copy follows the house style guide; only the authored strings are exempt.
    ADDED.forEach((key) => {
      const phase = phaseByKey(key);
      expect(phase?.sub).not.toContain('\u2014');
      expect(phase?.title).not.toContain('\u2014');
    });
  });

  it('carries no placeholder values from the prototype', () => {
    // The prototype's PHASES held demo figures inline. None may reach the registry: they were
    // standing in for recorded data, and shipping them would put unmeasured numbers on a screen
    // that reports measured ones.
    const serialised = JSON.stringify(PHASES);
    [
      'triton_9f2c0847',
      '2.4M matched',
      '103% of plan',
      '$71.4K',
      '$120K',
      '18 audio',
      'Clean Rooms',
      'Boltive',
      '92% match',
    ].forEach((placeholder) => expect(serialised).not.toContain(placeholder));
  });

  it('carries no numeric demo values from the prototype', () => {
    const serialised = JSON.stringify(PHASES);
    // The authored confidence percentages and product counts.
    ['96', '99', '92', '88', '95'].forEach((n) =>
      expect(serialised).not.toContain(`:${n}`),
    );
  });
});

describe('radial and meter presentation config', () => {
  it('keeps the prototype\u2019s arc radii, outermost first', () => {
    expect([...RADIAL_RADII]).toEqual([76, 60, 44, 28]);
    const descending = [...RADIAL_RADII].every(
      (r, i, all) => i === 0 || r < (all[i - 1] ?? Number.POSITIVE_INFINITY),
    );
    expect(descending).toBe(true);
  });

  it('has a colour for every radius, so assignment by position cannot run short', () => {
    expect(RADIAL_COLOURS).toHaveLength(RADIAL_RADII.length);
  });

  it('takes its arc colours from the prototype', () => {
    const source = prototypeSource();
    RADIAL_COLOURS.forEach((c) => expect(source).toContain(c));
  });
});

describe('format metadata', () => {
  it('matches the prototype\u2019s FMT_META keys', () => {
    expect([...FORMAT_KEYS]).toEqual(['audio', 'podcast', 'display', 'video', 'endcard']);
  });

  it('keeps the authored labels and css variables', () => {
    const source = prototypeSource();
    FORMAT_KEYS.forEach((key) => {
      const meta = FORMAT_META[key];
      expect(source).toContain(meta.label);
      expect(source).toContain(meta.cssVar);
    });
  });
});
