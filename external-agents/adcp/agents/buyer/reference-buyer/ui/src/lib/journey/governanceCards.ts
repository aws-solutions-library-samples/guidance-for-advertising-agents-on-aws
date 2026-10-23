/**
 * Turning real `sync_governance` and `sync_plans` results into card rows.
 *
 * Both decoders follow one rule, which is the whole reason this file is separate and tested: **a row
 * exists only when an agent actually supplied the value.** No dash, no "n/a", no zero standing in for
 * a field a counterparty never mentioned. An absent field means the panel is shorter, not that it
 * shows a placeholder that reads like a measurement.
 *
 * The two shapes are different in a way that matters:
 *
 *   sync_governance  goes to SELLERS, so its result is a fan-out: {task, results: [{seller_id,
 *                    response: {accounts: [...]}}]}. One panel, possibly several sellers.
 *   sync_plans       goes to the GOVERNANCE AGENT, so its result is one plain response:
 *                    {status, replayed, plans: [{plan_id, status, version, categories, ...}]}.
 *
 * Provenance is the other thing this module tracks. The governance agent's `sync_plans` response does
 * NOT echo budget, flight or channels — it returns the plan's identity, version, and the categories
 * it will evaluate. Those terms are real, but they are real as **what the buyer sent**, read from the
 * recorded tool call. So they are labelled `(submitted)`, because "the plan the agent holds has this
 * budget" is a claim the response does not support, so presenting a sent value as a confirmed one
 * would say something the response does not.
 *
 * Equally: registering a plan is not approval. `categories` is what the agent says it WILL evaluate,
 * so the row is labelled as such rather than as a verdict.
 */

import { parseConditions } from './conditions';
import { RADIAL_COLOURS, RADIAL_RADII } from './phases';
import { parseFanOut, sellerEntryFailed, sellerEntryName } from '../sellers';
import type { CreativeCard } from '../../components/journey/bodies/ScanBody';
import type { Meter } from './types';
import type {
  AccountCard,
  AccountStripData,
  Arc,
  BindData,
  BindLink,
  CardRow,
  GovernanceCheckData,
  HumanReviewNote,
  MediaBuyStripData,
  PackageCard,
  PlanData,
  ToolExchange,
} from './types';

/** A JSON-ish record, after a shape check. */
type Rec = Record<string, unknown>;

function isRec(value: unknown): value is Rec {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * A tool result as an object.
 *
 * The recorder stores a tool's output as the JSON **string** the tool returned, so a decoder that
 * only handled objects would find nothing in a real session while passing on hand-built fixtures.
 * Both are accepted for that reason.
 */
function asRecord(output: unknown): Rec | null {
  if (isRec(output)) return output;
  if (typeof output === 'string') {
    try {
      const parsed: unknown = JSON.parse(output);
      return isRec(parsed) ? parsed : null;
    } catch {
      return null;
    }
  }
  return null;
}

function str(value: unknown): string | null {
  if (typeof value === 'string' && value.trim().length > 0) return value.trim();
  // Numbers count: `version: 1` must render as "1". Only NaN/Infinity are rejected.
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  return null;
}

/** `{brand: {domain}, operator}` rendered as the account's natural key, as the seller keys it. */
function accountLabel(account: unknown): string | null {
  if (!isRec(account)) return null;
  const brand = isRec(account['brand']) ? str(account['brand']['domain']) : null;
  const operator = str(account['operator']);
  if (brand && operator) return `${brand} / ${operator}`;
  // An `account_id`-only reference is the other legal shape AdCP allows, so it is shown as itself
  // rather than discarded.
  return brand ?? operator ?? str(account['account_id']);
}

/**
 * A governance agent's URL, shortened to the part a reader can act on.
 *
 * AgentCore invoke URLs are long and percent-encode a whole ARN, so the raw value overflows the row
 * and tells a viewer nothing. The runtime id is the identifying part. This chooses how much of the
 * URL to show; it does not alter it.
 */
function agentLabel(url: unknown): string | null {
  const raw = str(url);
  if (raw === null) return null;
  const decoded = ((): string => {
    try {
      return decodeURIComponent(raw);
    } catch {
      return raw;
    }
  })();
  const runtime = /runtime\/([^/?]+)/.exec(decoded);
  if (runtime?.[1]) return runtime[1];
  try {
    return new URL(decoded).host;
  } catch {
    return decoded;
  }
}

/**
 * Accounts: the advertiser account each seller now holds, from `sync_accounts`.
 *
 * The first real call in every governed session and the prerequisite for Bind — a seller that has never
 * been told about the account answers `ACCOUNT_NOT_FOUND` to `sync_governance`. It had no panel, so the
 * rail opened on Bind and read as though that were the first thing the buyer did.
 *
 * `action` and `status` are both REQUIRED on AdCP's `SyncAccountsAccount`, and neither is a success flag:
 * `action` is one of `created`/`updated`/`unchanged`/**`failed`**, and `status` one of
 * `active`/`pending_approval`/`rejected`/`payment_required`/`suspended`/`closed`. Both are rendered as
 * returned. A `failed` action or a non-active status is a real outcome, and collapsing either into
 * "account ready" is the same defect `decodeBookCard` was carrying for refused buys.
 *
 * Returns null when no seller supplied a usable account, which the caller reports as a classified
 * absence rather than as an empty card.
 */
export function decodeAccountStrip(exchange: ToolExchange): AccountStripData | null {
  const entries = parseFanOut(exchange.result?.output);
  if (entries === null) return null;

  const cards: AccountCard[] = [];
  const sellers = new Set<string>();
  let brand: string | null = null;
  let operator: string | null = null;
  let keySeq = 0;

  for (const entry of entries) {
    if (sellerEntryFailed(entry)) continue;
    const response = asRecord((entry as Rec)['response']);
    const accounts = response?.['accounts'];
    if (!Array.isArray(accounts)) continue;

    // Which seller this card belongs to. sync_accounts fans out to every seller, so without the
    // attribution the cards are indistinguishable. Same source decodeCreativeCards uses for formats
    // and decodeBindCard uses for links.
    const seller = sellerEntryName(entry);

    for (const account of accounts) {
      if (!isRec(account)) continue;

      // Checked before the identity fields, for the same reason `decodeMediaBuyStrip` checks a
      // refusal first: a failure must not be describable by the fields a success would have populated.
      const errors = account['errors'];
      const failure = Array.isArray(errors)
        ? errors.find((e) => isRec(e) && str(e['code']) !== null)
        : undefined;
      const action = str(account['action']);
      const failed = isRec(failure) || action === 'failed';

      // brand/operator are the account's natural key and identical across sellers, so the first seen
      // is lifted to the header rather than repeated on every card. A different pair would be a
      // different account, so capturing one value is not lossy.
      const brandRec = asRecord(account['brand']);
      const domain = str(brandRec?.['domain']);
      const op = str(account['operator']);
      if (brand === null && domain !== null) brand = domain;
      if (operator === null && op !== null) operator = op;

      sellers.add(seller);

      if (failed) {
        cards.push({
          key: `acct-${keySeq++}`,
          seller,
          outcome: 'failed',
          action: action ?? 'failed',
          status: null,
          accountId: null,
          errorCode: isRec(failure) ? str(failure['code']) : null,
          errorDetail: isRec(failure) ? str(failure['message']) : null,
        });
        continue;
      }

      cards.push({
        key: `acct-${keySeq++}`,
        seller,
        outcome: 'active',
        action,
        status: str(account['status']),
        accountId: str(account['account_id']),
        errorCode: null,
        errorDetail: null,
      });
    }
  }

  if (cards.length === 0) return null;
  return { cards, brand, operator, sellerCount: sellers.size };
}

/**
 * Bind: what each seller confirmed about the governance binding.
 *
 * Returns null when no seller supplied a usable account, which the caller reports as an absence
 * rather than as an empty card.
 */
export function decodeBindCard(exchange: ToolExchange): BindData | null {
  const output = exchange.result?.output;
  const entries = parseFanOut(output);

  // A failed seller contributes no link. Its failure is reported by the seller notice rather than
  // shown here as a binding that did not happen.
  const responded = (entries ?? []).filter((entry) => !sellerEntryFailed(entry));
  const links: BindLink[] = [];

  for (const entry of responded) {
    const response = asRecord((entry as Rec)['response']);
    const accounts = response?.['accounts'];
    if (!Array.isArray(accounts)) continue;
    const sellerId = str((entry as Rec)['seller_id']);
    for (const account of accounts) {
      if (!isRec(account)) continue;
      const agents = account['governance_agents'];
      const agent = Array.isArray(agents)
        ? (agents.map((a) => (isRec(a) ? agentLabel(a['url']) : null)).find((a) => a !== null) ??
          null)
        : null;
      const link: BindLink = {
        sellerId,
        account: accountLabel(account['account']),
        status: str(account['status']),
        agent,
      };
      // A link with nothing in it is not a binding. Skipped rather than rendered as an empty row.
      if (link.account === null && link.status === null && link.agent === null) continue;
      links.push(link);
    }
  }

  // No links is not an empty success: it means nothing usable came back, and the caller turns null
  // into a classified absence with a reason a demonstrator can read out.
  if (links.length === 0) return null;
  return { links };
}

/**
 * `{total, currency}` split for display.
 *
 * Null when the amount is missing: a currency with no figure is not a budget, and rendering "USD"
 * alone under a large blank would read as a measurement that failed to load.
 */
function splitBudget(budget: unknown): { amount: string; currency: string | null } | null {
  if (!isRec(budget)) return null;
  const total = budget['total'];
  // `0` is a real submitted figure and must survive. Only a non-number or NaN/Infinity is absent.
  if (typeof total !== 'number' || !Number.isFinite(total)) return null;
  return { amount: total.toLocaleString('en-US'), currency: str(budget['currency']) };
}

/** An ISO date or datetime reduced to its date part, which is what the flight window shows. */
function dayOf(value: unknown): string | null {
  const raw = str(value);
  if (raw === null) return null;
  const match = /^(\d{4}-\d{2}-\d{2})/.exec(raw);
  return match?.[1] ?? raw;
}

/**
 * Plan: what the governance agent registered, plus the terms it was given.
 *
 * The response carries the plan's identity, version and the categories the agent will assess. It does
 * NOT carry budget, flight or channels, so those are read from the recorded request and the panel
 * labels them as submitted — see this module's header for why that distinction is not cosmetic.
 */
export function decodePlanCard(exchange: ToolExchange): PlanData | null {
  const response = asRecord(exchange.result?.output);
  const plans = response?.['plans'];
  const plan = Array.isArray(plans) && isRec(plans[0]) ? (plans[0] as Rec) : null;
  const input = isRec(exchange.input) ? exchange.input : null;

  const budget = input
    ? splitBudget({ total: input['budget_total'], currency: input['currency'] ?? 'USD' })
    : null;

  const start = input ? dayOf(input['flight_start']) : null;
  const end = input ? dayOf(input['flight_end']) : null;

  const rawChannels = input?.['channels'];
  const channels = Array.isArray(rawChannels)
    ? rawChannels.map((channel) => str(channel)).filter((c): c is string => c !== null)
    : [];

  // What the agent will evaluate. NOT a verdict: `sync_plans` registers a plan, it does not judge
  // one, so anything reading like an approval here would overstate it. The chips are tinted
  // neutrally for the same reason.
  const rawCategories = plan?.['categories'];
  const willEvaluate = Array.isArray(rawCategories)
    ? rawCategories
        .map((category) => (isRec(category) ? str(category['category_id']) : str(category)))
        .filter((id): id is string => id !== null)
    : [];

  // `[]` is an answer: the agent resolved no policies, and the panel says so. A non-array means the
  // field was absent, which is not an answer, so it stays null and the panel says nothing at all —
  // that is the difference between "none" and "not asked".
  const rawPolicies = plan?.['resolved_policies'];
  const resolvedPolicies = Array.isArray(rawPolicies)
    ? rawPolicies
        .map((policy) =>
          isRec(policy) ? (str(policy['policy_id']) ?? str(policy['name'])) : str(policy),
        )
        .filter((name): name is string => name !== null)
    : null;

  const data: PlanData = {
    planId: plan ? str(plan['plan_id']) : null,
    status: plan ? str(plan['status']) : null,
    version: plan ? str(plan['version']) : null,
    budgetAmount: budget?.amount ?? null,
    currency: budget?.currency ?? null,
    flightStart: start,
    flightEnd: end,
    channels,
    willEvaluate,
    resolvedPolicies,
  };

  // Nothing usable came back. Null rather than an empty panel, so the caller reports a classified
  // absence with a reason a demonstrator can read out.
  const empty =
    data.planId === null &&
    data.status === null &&
    data.budgetAmount === null &&
    data.flightStart === null &&
    data.channels.length === 0 &&
    data.willEvaluate.length === 0;
  if (empty) return null;
  return data;
}

/**
 * Govern / Score: a `check_governance` response, decoded into arcs and a verdict.
 *
 * Calls the GOVERNANCE agent, so its result is one plain response, not a fan-out (see this module's
 * header for the same distinction on `sync_plans`).
 *
 * Absence rules this decoder enforces (BR-U2-19..23), each traced to a specific AdCP fact rather than
 * a house style choice:
 *   - no `findings[]` at all -> zero arcs. Normal on a healthy `approved`, not an error.
 *   - a finding with no `confidence` -> that arc's `confidence` is null, which RadialBody renders as
 *     no bar rather than a zero-length one.
 *   - `category_id` is never pattern-matched; colour and radius assign by ARRAY POSITION only, so an
 *     agent-defined label cannot select styling.
 *   - `mode` absent -> `modeNote` reads "mode not reported", never assumed to be `enforce`.
 *   - `status` of `submitted`/`working` (human review in progress) -> `awaitingHumanReview: true`,
 *     which the caller must render as a distinct pending state, never as any verdict.
 *   - a `CAMPAIGN_SUSPENDED` error -> also `awaitingHumanReview: true`. The plan is frozen pending a
 *     human, which is the same situation as `submitted` and must not render as the `denied` verdict the
 *     refusal technically carries. A refusal for suspension and a denial for non-compliance are
 *     different facts, and only one of them is about the buyer's request.
 */
export function decodeGovernanceCheckCard(exchange: ToolExchange): GovernanceCheckData | null {
  const response = asRecord(exchange.result?.output);
  if (response === null) return null;

  // Signal 1, unchanged. Kept as the first check rather than folded into a combined predicate: this is
  // the one the four existing readers already exercise, and narrowing it is out of scope.
  const status = str(response['status']);
  if (status === 'submitted' || status === 'working') {
    return awaitingReview(null);
  }

  // Signal 2. Before the verdict is read, because a suspended plan's response carries `verdict: denied`
  // and reading that first would render the refusal as a compliance denial.
  const suspension = suspensionNote(response);
  if (suspension !== null) return awaitingReview(suspension);

  const verdictWord = str(response['verdict']);
  if (verdictWord === null) return null;

  return verdictFrom(response, verdictWord);
}

/** The pending-review shape, with whatever detail the agent supplied. */
function awaitingReview(humanReview: HumanReviewNote | null): GovernanceCheckData {
  return {
    arcs: [],
    verdict: { count: 0, countSuffix: '', title: '', sub: '', pill: '', modeNote: null },
    awaitingHumanReview: true,
    humanReview,
    // Not read while review is pending. A suspended plan was never evaluated, so any conditions on the
    // response would describe a different, earlier check.
    conditions: null,
  };
}

/**
 * A `CAMPAIGN_SUSPENDED` error on either of AdCP's two error layers, as a review note.
 *
 * **Both layers are checked**, and that is not belt-and-braces. AdCP's two-layer model puts the
 * canonical array at `errors[]` and an extractable single error at `adcp_error`; an agent may populate
 * either or both, and a reader that watched only one would miss a suspension from an agent that chose
 * the other. `errors[]` is preferred when present because it is the normative shape and carries
 * `suggestion`.
 *
 * Returns null for every other error code. A `PERMISSION_DENIED` or a `VALIDATION_ERROR` is not a
 * pending review, and showing it as one would tell a buyer to wait for a human who was never asked.
 */
function suspensionNote(response: Rec): HumanReviewNote | null {
  const candidates: Rec[] = [];
  const errors = response['errors'];
  if (Array.isArray(errors)) {
    for (const error of errors) if (isRec(error)) candidates.push(error);
  }
  const envelope = asRecord(response['adcp_error']);
  if (envelope !== null) candidates.push(envelope);

  for (const error of candidates) {
    if (str(error['code']) !== 'CAMPAIGN_SUSPENDED') continue;
    return {
      reason: str(error['message']),
      suggestion: str(error['suggestion']),
      resolution: null,
      resolvedAt: null,
    };
  }
  return null;
}

/**
 * Signal 3: an unresolved escalation in a `get_plan_audit_logs` response.
 *
 * The durable one. Signals 1 and 2 are properties of a single check response and are gone once the
 * session moves on; the audit log answers "is this plan awaiting review" at any later moment, which is
 * what makes the panel survive a reload.
 *
 * Unresolved is `resolved_at` absent, which is the agent's own representation -- there is no separate
 * status field on an escalation and none is needed.
 *
 * Returns the most recently RESOLVED escalation's note when none is outstanding, so the panel can say
 * what a human decided rather than silently reverting to a bare verdict. RQ3's constraint is exactly
 * this: resolution happens out of band, and the UI has to reflect it from the data it already reads.
 */
export function auditEscalationNote(
  response: Rec,
): { readonly awaiting: boolean; readonly note: HumanReviewNote } | null {
  const plans = response['plans'];
  if (!Array.isArray(plans)) return null;

  const escalations: Rec[] = [];
  for (const plan of plans) {
    if (!isRec(plan)) continue;
    const summary = asRecord(plan['summary']);
    const list = summary?.['escalations'];
    if (!Array.isArray(list)) continue;
    for (const escalation of list) if (isRec(escalation)) escalations.push(escalation);
  }
  if (escalations.length === 0) return null;

  const outstanding = escalations.filter((escalation) => str(escalation['resolved_at']) === null);
  // Outstanding wins over resolved regardless of order: one unresolved escalation freezes the plan
  // however many others have been closed.
  const chosen = outstanding[0] ?? escalations[escalations.length - 1];
  if (chosen === undefined) return null;

  return {
    awaiting: outstanding.length > 0,
    note: {
      reason: str(chosen['reason']),
      // An escalation carries no `suggestion` -- that is an error-object field. Null rather than a
      // borrowed string.
      suggestion: null,
      resolution: str(chosen['resolution']),
      resolvedAt: str(chosen['resolved_at']),
    },
  };
}

/**
 * Findings mapped onto the authored arcs.
 *
 * Shared by the intent decoder and the execution decoder, because the two read the SAME finding
 * shape from two different envelopes — AdCP's audit-log `entries[].findings` is documented as "same
 * structure as check_governance response findings". Two copies of this would be two chances for the
 * seller-side panel to style or truncate findings differently from the buyer-side one, and the two
 * panels sharing `RadialBody` exists to stop exactly that drift.
 */
function arcsFromFindings(findings: readonly Rec[]): Arc[] {
  return findings.slice(0, RADIAL_RADII.length).map((finding, index) => {
    const rawConfidence = finding['confidence'];
    const confidence =
      typeof rawConfidence === 'number' && Number.isFinite(rawConfidence) ? rawConfidence : null;
    return {
      categoryId: str(finding['category_id']) ?? 'unspecified',
      confidence,
      severity: str(finding['severity']) ?? '',
      explanation: str(finding['explanation']) ?? '',
      radius: RADIAL_RADII[index] ?? RADIAL_RADII[RADIAL_RADII.length - 1] ?? 0,
      colour: RADIAL_COLOURS[index] ?? RADIAL_COLOURS[RADIAL_COLOURS.length - 1] ?? '#888',
    };
  });
}

const PILL_BY_VERDICT: Record<string, string> = {
  approved: 'approved',
  conditions: 'conditions',
  denied: 'denied',
};

/**
 * A verdict-carrying record (a `check_governance` response, or one audit-log check entry) turned
 * into arcs and a verdict.
 *
 * Both envelopes carry `verdict`, `explanation`, `plan_id`, `mode` and `findings` under those exact
 * names, so one reader serves both.
 */
function verdictFrom(record: Rec, verdictWord: string): GovernanceCheckData {
  const rawFindings = record['findings'];
  const findings = Array.isArray(rawFindings) ? rawFindings.filter(isRec) : [];

  const mode = str(record['mode']);
  const modeNote = mode === null ? 'mode not reported' : mode === 'enforce' ? null : mode;

  const planId = str(record['plan_id']);

  return {
    arcs: arcsFromFindings(findings),
    verdict: {
      count: findings.length,
      countSuffix: findings.length === 1 ? 'finding' : 'findings',
      title: str(record['explanation']) ?? verdictWord,
      sub: planId !== null ? `plan ${planId}` : '',
      pill: PILL_BY_VERDICT[verdictWord] ?? verdictWord,
      modeNote,
    },
    awaitingHumanReview: false,
    humanReview: null,
    conditions: parseConditions(record),
  };
}

/**
 * Score: the SELLER's execution check, read out of a `get_plan_audit_logs` response.
 *
 * This is the fix for the two governance panels showing one result set twice. Score used to select
 * among the buyer's own `adcp_check_governance` exchanges by position — last instead of first —
 * which is the same exchange whenever the session made one check, and is an INTENT check in every
 * case regardless. See `phases.ts`'s `SelectorStrategy` doc for why no selector over that tool could
 * have worked.
 *
 * The discriminator is AdCP's own `entries[].check_type`, never position and never the entry's
 * `caller`. `check_type` is the field the spec defines for this ("inferred from the fields present on
 * the original check request"), and the reference governance agent already sets it. Matching on
 * `caller` instead would work only for as long as seller URLs stay distinguishable from buyer ones.
 *
 * Returns null — so the caller reports an absence — when:
 *   - the plan has no entries (`include_entries` was false, or nothing has happened yet);
 *   - entries exist but none is an execution check, which is the normal state of any session that
 *     has not booked, since only `create_media_buy` triggers the seller's check.
 *
 * Null is the important half. Borrowing Govern's intent check to fill this panel is precisely the
 * defect being removed: it would attribute the buyer's own check to the seller's governance agent.
 */
export function decodeExecutionCheckCard(exchange: ToolExchange): GovernanceCheckData | null {
  const response = asRecord(exchange.result?.output);
  const plans = response?.['plans'];
  if (!Array.isArray(plans)) return null;

  const executionChecks: Rec[] = [];
  for (const plan of plans) {
    if (!isRec(plan)) continue;
    const entries = plan['entries'];
    if (!Array.isArray(entries)) continue;
    for (const entry of entries) {
      if (!isRec(entry)) continue;
      if (str(entry['type']) !== 'check') continue;
      if (str(entry['check_type']) !== 'execution') continue;
      executionChecks.push(entry);
    }
  }

  // The last one in the list, which is the most recent where the agent's timestamps distinguish them.
  // A re-check after applying conditions supersedes the one before it, so later wins.
  //
  // One limitation, stated because it is not visible from here: the reference governance agent stamps
  // `created_at` at whole-second resolution and sorts entries by it, so two checks inside the same
  // second have no defined order. This picks one of them. A single execution check per booking is the
  // normal case; a same-second re-check is the case this cannot resolve.
  // Signal 3, before any verdict is read. An outstanding escalation means the plan is frozen, and the
  // execution check recorded against it is the agent's pre-review RECOMMENDATION rather than an answer
  // -- rendering it as a verdict would show a denial a human may be about to reverse.
  const escalation = response === null ? null : auditEscalationNote(response);
  if (escalation !== null && escalation.awaiting) return awaitingReview(escalation.note);

  const latest = executionChecks[executionChecks.length - 1];
  if (latest === undefined) return null;

  const verdictWord = str(latest['verdict']);
  if (verdictWord === null) return null;

  const decoded = verdictFrom(latest, verdictWord);
  // A RESOLVED escalation is kept alongside the verdict, not instead of it: by then the verdict IS the
  // human's decision (the agent overwrites it on resolution), and the note is what says a human made it.
  // Dropping the note here is what would make a reviewed plan indistinguishable from one that was never
  // escalated -- the specific thing RQ3 asks the UI to reflect once resolution happens out of band.
  if (escalation !== null) return { ...decoded, humanReview: escalation.note };

  return decoded;
}

/**
 * Report: a `report_plan_outcome` response, decoded into card rows.
 *
 * Report's `type` is `card`, same as Book, so this returns `readonly CardRow[]` directly -- matching
 * the existing `card` dispatch convention in PhasePanel.tsx -- rather than a wrapper object.
 *
 * `committed_budget` is the seller's ACTUAL confirmed amount (BR-U2-17/18), never the originally
 * requested figure -- rendered under the key `committed`, deliberately distinct from Activate's
 * `spend` chip (see phases.ts's binding comment on the `report` phase for why the two must not merge).
 */
export function decodeReportOutcomeCard(exchange: ToolExchange): readonly CardRow[] | null {
  const response = asRecord(exchange.result?.output);
  if (response === null) return null;

  const rows: CardRow[] = [];
  const outcomeState = str(response['outcome_state']);
  if (outcomeState !== null) rows.push({ key: 'outcome_state', value: outcomeState });

  const committed = response['committed_budget'];
  if (typeof committed === 'number' && Number.isFinite(committed)) {
    rows.push({ key: 'committed', value: committed.toLocaleString('en-US') });
  }

  const summary = asRecord(response['plan_summary']);
  const remaining = summary?.['budget_remaining'];
  if (typeof remaining === 'number' && Number.isFinite(remaining)) {
    rows.push({ key: 'budget_remaining', value: remaining.toLocaleString('en-US') });
  }

  if (rows.length === 0) return null;
  return rows;
}

/**
 * Creative: one card per (seller, format) pair from a real `list_creative_formats` fan-out.
 *
 * No dedup across sellers (Q7 of the U4 Functional Design plan): two sellers both accepting
 * `display_static` is two real facts about two real sellers, not one fact shown twice. Each card
 * carries the seller that returned it, via `sellerEntryName` -- the same fan-out attribution
 * `productGroups.ts::toGroupProduct` already uses for Discover (Q9's resolution: use the project's
 * existing prescribed pattern, not a new one).
 *
 * `evaluatorResults`, when supplied, is this session's most recent `get_creative_features` response
 * for the ONE format this build has a real fixture asset for (`display_300x250` --
 * `fixtures/creatives/`). Every other format's card gets `clean: null` -- no evaluator ran against
 * it, so `ScanBody` correctly shows "not evaluated" rather than a verdict this evaluator never
 * produced for that format.
 */
export function decodeCreativeCards(
  exchange: ToolExchange,
  evaluatorResults?: { readonly formatId: string; readonly clean: boolean; readonly features: readonly string[] } | null,
): readonly CreativeCard[] | null {
  const entries = parseFanOut(exchange.result?.output);
  if (!entries) return null;

  const cards: CreativeCard[] = [];
  for (const entry of entries) {
    if (sellerEntryFailed(entry)) continue;
    const response = entry.response;
    const formats = Array.isArray(response?.formats) ? response.formats : [];
    const seller = sellerEntryName(entry);

    for (const format of formats) {
      if (!isRec(format)) continue;
      const formatId = isRec(format['format_id']) ? str(format['format_id']['id']) : null;
      if (formatId === null) continue;
      const name = str(format['name']) ?? formatId;
      const evaluated = evaluatorResults?.formatId === formatId ? evaluatorResults : null;
      cards.push({
        format: formatId,
        name,
        seller,
        clean: evaluated ? evaluated.clean : null,
        features: evaluated ? evaluated.features : [],
      });
    }
  }

  if (cards.length === 0) return null;
  return cards;
}

/**
 * The real `get_creative_features` response, reduced to what `decodeCreativeCards` needs to attach
 * a verdict to the one format this build has fixture content for.
 *
 * `results[]` carries `CreativeFeatureResult` objects (`feature_id`, `value`, ...) -- not a single
 * clean/blocked flag, so "clean" here is derived: true unless any feature's own value reads as a
 * real problem (`dimension_conformance: false`, or a boolean feature that is itself the problem,
 * e.g. `urgency_claim: true`). `contrast_ratio` is numeric and reported as a feature chip, not folded
 * into the clean/blocked verdict, since a specific number is more useful on screen than a second
 * boolean collapsing it.
 *
 * Returns null on an error response, and on `status: "working"`/`"submitted"`.
 *
 * The error branch is the one that fires in practice, and for a while it fired on EVERY session: the
 * governance agent's cold-cache path returned `{status: "working"}` with neither `results` nor `errors`,
 * which matches no variant of that task's response union, so the call came back
 * `VALIDATION_ERROR[/]: oneOf composition failed` and this decoder correctly refused to invent a
 * verdict. The panel showed cards with no scan, which was honest and looked like a missing feature.
 *
 * The `working`/`submitted` branch is not dead code. AdCP's creative-governance page describes a real
 * `working` path for slow evaluation — sandboxed malware scanning is its example — with results delivered
 * by webhook rather than polling. This project's own governance agent does not take it (its work is one
 * fetch plus three local measurements, inside the 30-second rule's `completed`-inline band, and it
 * implements no webhook), but a third-party evaluator legitimately would. Treating an unfinished
 * evaluation as a verdict is the one outcome this panel must never produce, so the branch stays.
 */
export function decodeCreativeEvaluation(
  exchange: ToolExchange,
  formatId: string,
): { readonly formatId: string; readonly clean: boolean; readonly features: readonly string[] } | null {
  const response = asRecord(exchange.result?.output);
  if (response === null) return null;

  const status = str(response['status']);
  if (status === 'working' || status === 'submitted') return null;
  if (Array.isArray(response['errors'])) return null;

  const rawResults = response['results'];
  if (!Array.isArray(rawResults)) return null;

  let clean = true;
  const features: string[] = [];
  for (const result of rawResults) {
    if (!isRec(result)) continue;
    const featureId = str(result['feature_id']);
    if (featureId === null) continue;
    const value = result['value'];
    if (featureId === 'dimension_conformance' && value === false) clean = false;
    if (featureId === 'urgency_claim' && value === true) clean = false;
    if (featureId === 'contrast_ratio' && typeof value === 'number') {
      features.push(`contrast ${value.toFixed(2)}`);
    } else if (value === true) {
      features.push(featureId);
    }
  }

  return { formatId, clean, features };
}

/** A finite number, or null. `0` is a number and passes. */
function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/** An ISO datetime reduced to its date portion, for compact display. Left as-is if not ISO-shaped. */
function isoDate(value: unknown): string | null {
  const s = str(value);
  if (s === null) return null;
  const t = s.indexOf('T');
  return t > 0 ? s.slice(0, t) : s;
}

/** The truthy strings of an array, in order. */
function strList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const out: string[] = [];
  for (const item of value) {
    const s = str(item);
    if (s !== null) out.push(s);
  }
  return out;
}

/** A brand-ref (`{domain}`) or plain string vendor, reduced to a name. */
function vendorName(value: unknown): string | null {
  if (isRec(value)) return str(value['domain']) ?? str(value['name']);
  return str(value);
}

/** The non-`warning` error a seller declined with, or null. */
function findFailure(errors: unknown): Rec | null {
  if (!Array.isArray(errors)) return null;
  const failure = errors.find(
    (e) => isRec(e) && e['severity'] !== 'warning' && str(e['code']) !== null,
  );
  return isRec(failure) ? failure : null;
}

/** The named formats a package carries, from `format_ids[].id` (or a bare string id). */
function packageFormats(pkg: Rec): string[] {
  const out: string[] = [];
  const ids = pkg['format_ids'];
  if (Array.isArray(ids)) {
    for (const ref of ids) {
      const id = isRec(ref) ? str(ref['id']) : str(ref);
      if (id !== null) out.push(id);
    }
  }
  return out;
}

/**
 * A package's optimization goals, one line each.
 *
 * A metric goal names a `metric`; an event goal names its event source's `event_type`. Either may
 * carry a `target` of `{kind, value}` (e.g. `cost_per 0.08`). Only fields the seller actually sent
 * are rendered — a goal with no target renders its subject alone rather than an invented threshold.
 */
function optimizationGoalLines(pkg: Rec): string[] {
  const goals = pkg['optimization_goals'];
  if (!Array.isArray(goals)) return [];
  const out: string[] = [];
  for (const goal of goals) {
    if (!isRec(goal)) continue;
    let subject = str(goal['metric']);
    if (subject === null) {
      const sources = goal['event_sources'];
      const first = Array.isArray(sources) && isRec(sources[0]) ? sources[0] : null;
      if (first !== null) subject = str(first['event_type']) ?? str(first['custom_event_name']);
    }
    if (subject === null) subject = str(goal['kind']);
    const target = isRec(goal['target']) ? goal['target'] : null;
    const targetKind = target ? str(target['kind']) : null;
    const targetValue = target ? num(target['value']) : null;
    let line = subject ?? 'goal';
    if (targetKind !== null && targetValue !== null) line += ` \u2192 ${targetKind} ${targetValue}`;
    else if (targetKind !== null) line += ` \u2192 ${targetKind}`;
    out.push(line);
  }
  return out;
}

/** A package's targeting overlay, as geo and frequency-cap lines. */
function targetingLines(pkg: Rec): string[] {
  const overlay = isRec(pkg['targeting_overlay']) ? pkg['targeting_overlay'] : null;
  if (overlay === null) return [];
  const out: string[] = [];
  const geo = [...strList(overlay['geo_countries']), ...strList(overlay['geo_regions'])];
  if (geo.length > 0) out.push(`geo ${geo.join(', ')}`);
  const cap = isRec(overlay['frequency_cap']) ? overlay['frequency_cap'] : null;
  if (cap !== null) {
    const max = num(cap['max_impressions']);
    const per = str(cap['window']) ?? str(cap['per']);
    if (max !== null && per !== null) out.push(`freq ${max} / ${per}`);
    else if (max !== null) out.push(`freq ${max}`);
  }
  return out;
}

/** Assigned creatives, as `creative_id (w:weight)` when a weight was given. */
function creativeAssignmentLines(pkg: Rec): string[] {
  const assigns = pkg['creative_assignments'];
  if (!Array.isArray(assigns)) return [];
  const out: string[] = [];
  for (const a of assigns) {
    if (!isRec(a)) continue;
    const id = str(a['creative_id']);
    if (id === null) continue;
    const weight = num(a['weight']);
    out.push(weight !== null ? `${id} (w:${weight})` : id);
  }
  return out;
}

/** The agreed billing/makegood terms, when present. */
function measurementLines(pkg: Rec): string[] {
  const terms = isRec(pkg['measurement_terms']) ? pkg['measurement_terms'] : null;
  if (terms === null) return [];
  const out: string[] = [];
  const billing = isRec(terms['billing_measurement']) ? terms['billing_measurement'] : null;
  if (billing !== null) {
    const vendor = vendorName(billing['vendor']);
    out.push(vendor !== null ? `billing ${vendor}` : 'billing measured');
  }
  if (isRec(terms['makegood_policy'])) out.push('makegood policy');
  return out;
}

/** Agreed performance standards, as `metric >= threshold (standard)`. */
function performanceStandardLines(pkg: Rec): string[] {
  const standards = pkg['performance_standards'];
  if (!Array.isArray(standards)) return [];
  const out: string[] = [];
  for (const s of standards) {
    if (!isRec(s)) continue;
    const metric = str(s['metric']);
    if (metric === null) continue;
    const threshold = num(s['threshold']);
    const standard = str(s['standard']);
    let line = threshold !== null ? `${metric} >= ${threshold}` : metric;
    if (standard !== null) line += ` (${standard})`;
    out.push(line);
  }
  return out;
}

/** Promoted catalogs, as `name (type)`. */
function catalogLines(pkg: Rec): string[] {
  const catalogs = pkg['catalogs'];
  if (!Array.isArray(catalogs)) return [];
  const out: string[] = [];
  for (const c of catalogs) {
    if (!isRec(c)) continue;
    const label = str(c['name']) ?? str(c['catalog_id']);
    if (label === null) continue;
    const type = str(c['type']);
    out.push(type !== null ? `${label} (${type})` : label);
  }
  return out;
}

/** The package's opaque `context` object, compacted to a bounded mono string. */
function contextString(pkg: Rec): string | null {
  const ctx = pkg['context'];
  if (!isRec(ctx) || Object.keys(ctx).length === 0) return null;
  const json = JSON.stringify(ctx);
  return json.length > 120 ? `${json.slice(0, 117)}\u2026` : json;
}

/** A package's budget rendered with its currency: `$X` for USD, `X CUR` otherwise, bare when absent. */
function formatMoney(amount: number, currency: string | null): string {
  const n = amount.toLocaleString('en-US');
  if (currency === null) return n;
  if (currency === 'USD') return `$${n}`;
  return `${n} ${currency}`;
}

/** The buyer's requested product for a buy, from the recorded request (single or packages[0]). */
function requestProductId(input: Rec | null): string | null {
  if (input === null) return null;
  const direct = str(input['product_id']);
  if (direct !== null) return direct;
  const pkgs = input['packages'];
  if (Array.isArray(pkgs) && isRec(pkgs[0])) return str(pkgs[0]['product_id']);
  return null;
}

/** A card with every field at its empty default. Callers override only what a real response supplied. */
function blankCard(key: string, title: string): PackageCard {
  return {
    key,
    title,
    productId: null,
    packageId: null,
    mediaBuyId: null,
    status: null,
    outcome: 'no_id',
    refusalCode: null,
    refusalDetail: null,
    refusalField: null,
    budget: null,
    currency: null,
    bidPrice: null,
    pricingOptionId: null,
    pacing: null,
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
  };
}

/** A tool error's message, whether it came as a string or a list of strings. */
function errorMessageText(value: unknown): string | null {
  const single = str(value);
  if (single !== null) return single;
  if (Array.isArray(value)) {
    const parts = value.map((v) => str(v)).filter((v): v is string => v !== null);
    return parts.length > 0 ? parts.join('; ') : null;
  }
  return null;
}

/**
 * A card's product identity, used to collapse the strip. A media buy is booked for a product/package,
 * so that — not the per-attempt media_buy_id — is what makes two cards "the same buy" across a failed
 * attempt and the booking that followed it.
 */
function productIdentity(card: PackageCard): string | null {
  return card.productId ?? card.packageId;
}

/**
 * Clean the strip so it shows real buys, not the noise of getting there.
 *
 * Two automatic rules, both safe against the honesty the decoder enforces per-card:
 *
 *   1. **Supersede**: once a product has a `booked` card, its non-booked cards for the SAME product
 *      (a `no_prior_check` failure, an in-flight attempt, an earlier refusal) are dropped. The booking
 *      is the product's real outcome; the failed attempts on the way to it are the "junk" a retrying
 *      agent leaves behind. A product that was NEVER booked keeps its failure/refusal card, so a real
 *      final failure is never hidden.
 *   2. **Dedupe**: identical cards (same media_buy_id, product, package and outcome) collapse to one.
 *      A plan viewed across sessions merges the same recorded buy from more than one session, which
 *      otherwise shows the same booking twice.
 */
function cleanMediaBuyCards(cards: readonly PackageCard[]): PackageCard[] {
  const bookedProducts = new Set<string>();
  for (const card of cards) {
    const identity = productIdentity(card);
    if (card.outcome === 'booked' && identity !== null) bookedProducts.add(identity);
  }

  const seen = new Set<string>();
  const kept: PackageCard[] = [];
  for (const card of cards) {
    const identity = productIdentity(card);
    // Superseded: a non-booked attempt for a product that was ultimately booked.
    if (card.outcome !== 'booked' && identity !== null && bookedProducts.has(identity)) continue;
    // Exact duplicate (e.g. the same buy recorded in two sessions of one plan).
    const dedupeKey = [card.mediaBuyId, card.productId, card.packageId, card.outcome].join('|');
    if (seen.has(dedupeKey)) continue;
    seen.add(dedupeKey);
    kept.push(card);
  }
  return kept;
}

/**
 * Book: every `create_media_buy` in the session, rendered as a strip of package cards plus an
 * aggregate header.
 *
 * Replaces the two-row card and the flat roster with the richer view the mockup describes, over the
 * same real data. The mockup's whole vocabulary — budget bars, pacing, pricing, flight windows,
 * optimization goals, targeting, creatives, measurement, catalogs — maps one-to-one onto AdCP's
 * `Package` object as echoed on the `create_media_buy` response, so nothing here is invented; a field
 * a seller omitted is omitted from the card, never shown as a dash or a zero.
 *
 * This build sends one package per call, so a session that books several products makes several
 * calls, each its own media buy. The strip shows one card per package across all of them, and every
 * card names its own `media_buy_id` — two buys are never merged under one heading. The header sums
 * only what stays honest across buys: budget (single currency), the buy and package counts, and the
 * overall flight window.
 *
 * The honesty rules that were pinned by live incidents are preserved:
 *
 *   - a refusal (`errors[]` with a non-`warning` code) is a `refused` card, checked BEFORE any success
 *     field, so a declined buy can never be described by the fields a booked one would carry;
 *   - a `warning`-severity error is not a refusal;
 *   - a success with no `media_buy_id` is a `no_id` card reporting its status as it stands, never
 *     dressed up as booked;
 *   - a call still in flight is `in_flight`, distinct from one that never happened, so the strip does
 *     not blank between products while a batch is being booked — the card COUNT does not depend on
 *     which call is in flight.
 *
 * Returns null when nothing usable was recorded, so the caller classifies the absence.
 */
export function decodeMediaBuyStrip(
  exchanges: readonly ToolExchange[],
  // Optional product_id -> friendly name, harvested from the session's own get_products answers, so a
  // booked card can read "Reference Podcast Mid-roll Audio" instead of "ref-audio-podcast-01". The
  // name is the seller's own real catalogue value; AdCP's Package carries no name, so this is the
  // honest source. Absent (or a product not in the map) falls back to the product_id, never invented.
  productNames?: ReadonlyMap<string, string>,
): MediaBuyStripData | null {
  const cards: PackageCard[] = [];
  let keySeq = 0;
  const nextKey = () => `pkg-${keySeq++}`;
  const nameFor = (productId: string | null): string | null =>
    productId !== null ? (productNames?.get(productId) ?? null) : null;

  for (const exchange of exchanges) {
    const input = asRecord(exchange.input);
    const reqProduct = requestProductId(input);

    if (exchange.result === undefined) {
      cards.push({
        ...blankCard(nextKey(), reqProduct ?? 'media buy'),
        outcome: 'in_flight',
        productId: reqProduct,
      });
      continue;
    }
    if (exchange.result.status !== 'success') {
      cards.push({
        ...blankCard(nextKey(), reqProduct ?? 'media buy'),
        outcome: 'call_failed',
        productId: reqProduct,
      });
      continue;
    }

    const response = asRecord(exchange.result.output);
    if (response === null) {
      cards.push({
        ...blankCard(nextKey(), reqProduct ?? 'media buy'),
        outcome: 'call_failed',
        productId: reqProduct,
      });
      continue;
    }

    // Refusal FIRST — a failure must not be describable by the fields a success would populate.
    const failure = findFailure(response['errors']);
    if (failure !== null) {
      cards.push({
        ...blankCard(nextKey(), reqProduct ?? 'refused buy'),
        outcome: 'refused',
        productId: reqProduct,
        status: 'refused by seller',
        refusalCode: str(failure['code']),
        refusalDetail: str(failure['message']),
        refusalField: str(failure['field']),
      });
      continue;
    }

    // The OTHER failure shape: a top-level `{error, message}` envelope (a buyer-side tool error such
    // as `no_prior_check` — a booking attempted before governance approval — or an MCP error). It has
    // no `errors[]` array, so without this it would fall through to a `no_id` card and read as a
    // success that merely lacks an id. It is a failed attempt, and the strip's cleanup supersedes it
    // once the product is booked.
    const topError = str(response['error']);
    if (topError !== null) {
      cards.push({
        ...blankCard(nextKey(), reqProduct ?? 'media buy'),
        outcome: 'call_failed',
        productId: reqProduct,
        refusalCode: topError,
        refusalDetail: errorMessageText(response['message']),
      });
      continue;
    }

    const mediaBuyId = str(response['media_buy_id']);
    // The media buy status, never the envelope `completed` task status when a specific one exists.
    const status = str(response['media_buy_status']) ?? str(response['status']);
    const confirmedAt = isoDate(response['confirmed_at']);
    const buyDeadline = isoDate(response['creative_deadline']);
    const revision = str(response['revision']);
    const buyCurrency = str(response['currency']);
    const outcome: PackageCard['outcome'] = mediaBuyId !== null ? 'booked' : 'no_id';

    const packages = Array.isArray(response['packages']) ? response['packages'] : [];
    const realPackages = packages.filter(isRec);

    if (realPackages.length === 0) {
      // Booked (or succeeded without an id), but the seller echoed no packages array. One card for it.
      cards.push({
        ...blankCard(nextKey(), reqProduct ?? mediaBuyId ?? 'media buy'),
        outcome,
        productId: reqProduct,
        mediaBuyId,
        status,
        confirmedAt,
        creativeDeadline: buyDeadline,
        revision,
        currency: buyCurrency,
      });
      continue;
    }

    for (const pkg of realPackages) {
      const productId = str(pkg['product_id']) ?? reqProduct;
      const packageId = str(pkg['package_id']);
      cards.push({
        key: nextKey(),
        title: nameFor(productId) ?? productId ?? packageId ?? `package ${cards.length + 1}`,
        productId,
        packageId,
        mediaBuyId,
        status,
        outcome,
        refusalCode: null,
        refusalDetail: null,
        refusalField: null,
        budget: num(pkg['budget']),
        currency: str(pkg['currency']) ?? buyCurrency,
        bidPrice: num(pkg['bid_price']),
        pricingOptionId: str(pkg['pricing_option_id']),
        pacing: str(pkg['pacing']),
        impressions: num(pkg['impressions']),
        formats: packageFormats(pkg),
        paused: pkg['paused'] === true,
        startTime: isoDate(pkg['start_time']),
        endTime: isoDate(pkg['end_time']),
        confirmedAt,
        creativeDeadline: isoDate(pkg['creative_deadline']) ?? buyDeadline,
        revision,
        agencyEstimate: str(pkg['agency_estimate_number']),
        optimizationGoals: optimizationGoalLines(pkg),
        targeting: targetingLines(pkg),
        creativeAssignments: creativeAssignmentLines(pkg),
        measurement: measurementLines(pkg),
        performanceStandards: performanceStandardLines(pkg),
        catalogs: catalogLines(pkg),
        context: contextString(pkg),
      });
    }
  }

  // Collapse retry noise and cross-session duplicates before anything downstream counts or renders.
  const cleaned = cleanMediaBuyCards(cards);
  if (cleaned.length === 0) return null;

  const booked = cleaned.filter((c) => c.outcome === 'booked' || c.outcome === 'no_id');
  const budgets = booked.map((c) => c.budget).filter((b): b is number => b !== null);
  const currencies = new Set(booked.map((c) => c.currency).filter((c): c is string => c !== null));
  const oneCurrency = currencies.size === 1 ? [...currencies][0]! : null;
  // A total only when a single currency is in play; two currencies cannot be summed into one figure.
  const totalBudget =
    budgets.length > 0 && currencies.size <= 1
      ? formatMoney(
          budgets.reduce((a, b) => a + b, 0),
          oneCurrency,
        )
      : null;
  const maxBudget = budgets.length > 0 ? Math.max(...budgets) : 0;

  const starts = cleaned.map((c) => c.startTime).filter((s): s is string => s !== null).sort();
  const ends = cleaned.map((c) => c.endTime).filter((s): s is string => s !== null).sort();

  // The status chip does not distinguish the individual buys — each card carries its own status in
  // its detail. When every card is booked and shares one status it is reported as itself; otherwise
  // a booked count, which is a true fact that does not hide a refusal (the count would not include
  // it, and the refused card shows its own state in the panel).
  const statuses = new Set(booked.map((c) => c.status).filter((s): s is string => s !== null));
  const allBooked = cleaned.every((c) => c.outcome === 'booked' || c.outcome === 'no_id');
  const bookedCount = cleaned.filter((c) => c.outcome === 'booked').length;
  const statusSummary =
    allBooked && statuses.size === 1
      ? [...statuses][0]!
      : bookedCount > 0
        ? `${bookedCount} booked`
        : (cleaned[0]?.status ?? null);

  return {
    cards: cleaned,
    totalBudget,
    currency: oneCurrency,
    packageCount: booked.length,
    flightStart: starts[0] ?? null,
    flightEnd: ends[ends.length - 1] ?? null,
    maxBudget,
    statusSummary,
  };
}

/**
 * Activate: real in-flight delivery meters from `get_media_buy_delivery`.
 *
 * Two meters this build can show honestly today (Q4's resolution): spend vs the plan's own
 * submitted budget, and pacing from `by_package[].pacing_index` (a real float on this response,
 * `1.0` = on track). `fill_rate`/`drift` stay permanently unfired here -- see MetersBody's own header
 * comment for fill rate's reason (no availability figure exists anywhere in AdCP's delivery
 * metrics); drift's real field (`geo_distribution`/`channel_distribution`) exists only on the
 * SELLER's own report to its governance agent (`check_governance` phase=delivery), a path U4 Part 2
 * builds, not on this buyer-facing response.
 *
 * `planBudget` is the plan's own submitted total (read by the caller from the recorded `sync_plans`
 * request, the same "submitted, not confirmed" provenance `decodePlanCard` already uses for budget)
 * -- passed in rather than re-derived here, since this decoder only sees the delivery exchange.
 */
export function decodeActivateMeters(
  exchange: ToolExchange,
  planBudget: number | null,
): readonly Meter[] | null {
  const response = asRecord(exchange.result?.output);
  if (response === null) return null;

  const meters: Meter[] = [];

  const totals = asRecord(response['totals']) ?? asRecord(response['aggregated_totals']);
  const spend = totals?.['spend'];
  if (typeof spend === 'number' && Number.isFinite(spend)) {
    const fraction =
      planBudget !== null && planBudget > 0 ? Math.min(spend / planBudget, 1) : null;
    meters.push({
      name: 'spend',
      fraction,
      text:
        planBudget !== null
          ? `$${spend.toLocaleString('en-US')} of $${planBudget.toLocaleString('en-US')}`
          : `$${spend.toLocaleString('en-US')}`,
    });
  }

  const byPackage = response['by_package'];
  const firstPackage = Array.isArray(byPackage) && isRec(byPackage[0]) ? byPackage[0] : null;
  const pacingIndex = firstPackage ? firstPackage['pacing_index'] : null;
  if (typeof pacingIndex === 'number' && Number.isFinite(pacingIndex)) {
    // Real float, unbounded above; the meter fill caps at "on track or ahead" (>=1.0 renders full)
    // per the Functional Design plan's Q5 resolution -- the real magnitude is carried in the text,
    // not invented as a second scale for the fill itself.
    const fraction = Math.min(pacingIndex, 1.0);
    const label =
      pacingIndex >= 1.05 ? 'ahead of plan' : pacingIndex <= 0.95 ? 'behind plan' : 'on track';
    meters.push({ name: 'pacing', fraction, text: `${pacingIndex.toFixed(2)}x \u2014 ${label}` });
  }

  if (meters.length === 0) return null;
  return meters;
}
