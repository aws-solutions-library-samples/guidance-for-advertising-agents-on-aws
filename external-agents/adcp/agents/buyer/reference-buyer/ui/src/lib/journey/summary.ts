/**
 * The sticky summary's chips.
 *
 * The prototype's four brief chips carried authored values. Only one of them has a real source in a
 * recorded session: the brief text itself. `channel`, `budget` and `flight` are not recorded as
 * fields anywhere, and exist only inside the prose of the brief if the person happened to state them.
 *
 * So the precedence is strict, and omission is a first-class outcome:
 *
 *   1. the campaign plan, once one is synced. A plan states these as structured fields and is the
 *      authorisation of record.
 *   2. a HIGH-CONFIDENCE extraction from the brief text.
 *   3. no chip at all.
 *
 * There is no fourth step. A low-confidence extraction omits the chip rather than showing a guess:
 * a budget inferred from prose that never named one looks identical on screen to one the brief
 * actually stated.
 *
 * Every chip carries `source`, which is not displayed. It exists because the likeliest future bug
 * here is a chip showing a correct value from the wrong place, and no assertion about the VALUE would
 * catch that. An assertion about its provenance does.
 */

import type { SessionMeta, SessionStep } from '../types';
import { matchesToolName } from './toolNames';
import { channelsInText, describeChannels } from './vocabulary';
import type { ChipSource, SummaryChip } from './types';

/** Structured plan values, once a plan has been synced. */
export interface PlanValues {
  readonly budget?: string;
  readonly channel?: string;
  readonly flight?: string;
}

/** The brief, as the session recorded it. */
export interface BriefText {
  readonly text: string;
  readonly source: ChipSource;
}

/**
 * The brief the agent is actually working to, read from a recorded `adcp_get_products` call.
 *
 * This is the agent's UNDERSTOOD requirement rather than the request it was given: the user's opening
 * message is often a paragraph of context, and what the agent distilled from it is the `brief` string
 * it sent to the sellers. That string is the one that determined which inventory came back, so it is
 * the more useful and the more accurate thing to show once it exists.
 *
 * It is real recorded data, not a summary produced here. Nothing is paraphrased, condensed or inferred
 * — this reads the exact argument the agent sent over the wire.
 *
 * The LAST such call wins. A session that refines its brief and asks again has moved on, and the panel
 * below is showing that later call's products, so the header must agree with it.
 */
function readAgentBrief(steps: readonly SessionStep[]): string | null {
  const briefs = [...steps]
    .sort((a, b) => a.step_index - b.step_index)
    .filter((step) => step.step_type === 'tool_call')
    .filter((step) => matchesToolName(step.content?.toolName ?? '', 'adcp_get_products'))
    .map((step) => {
      const input = step.content?.input;
      if (typeof input !== 'object' || input === null) return null;
      const brief = (input as Record<string, unknown>)['brief'];
      return typeof brief === 'string' && brief.trim().length > 0 ? brief.trim() : null;
    })
    .filter((brief): brief is string => brief !== null);
  return briefs.length > 0 ? (briefs[briefs.length - 1] ?? null) : null;
}

/**
 * The brief text from the session.
 *
 * Preference order, best first:
 *
 *   agent_brief      what the agent actually asked the sellers (see `readAgentBrief`)
 *   request_text     the full `incoming_request` the agent was given
 *   request_preview  the recorder's 80-character truncation of it
 *
 * The agent's own brief leads because it is the understood requirement, and because it is what the
 * products on screen were actually matched against. Until the agent has asked anyone, the request it
 * was given is the best available answer, and each source is reported distinctly so a caller can say
 * which one it is showing rather than presenting a clipped or superseded brief as the whole one.
 */
export function readBrief(
  steps: readonly SessionStep[],
  meta: SessionMeta | null,
): BriefText | null {
  const agentBrief = readAgentBrief(steps);
  if (agentBrief !== null) {
    return { text: agentBrief, source: 'agent_brief' };
  }
  const incoming = [...steps]
    .sort((a, b) => a.step_index - b.step_index)
    .find((step) => step.step_type === 'incoming_request');
  const text = incoming?.content?.text;
  if (typeof text === 'string' && text.trim().length > 0) {
    return { text: text.trim(), source: 'request_text' };
  }
  const preview = meta?.request_preview;
  if (typeof preview === 'string' && preview.trim().length > 0) {
    return { text: preview.trim(), source: 'request_preview' };
  }
  return null;
}

/** The `brief` chip, or null when the session recorded no request text. */
export function briefChip(
  steps: readonly SessionStep[],
  meta: SessionMeta | null,
): SummaryChip | null {
  const brief = readBrief(steps, meta);
  if (!brief) return null;
  return {
    key: 'brief',
    label: 'brief',
    value: brief.text,
    kind: 'brief',
    source: brief.source,
  };
}

/**
 * An extraction and how much it can be trusted.
 *
 * `confident` is false for anything the pattern matched only loosely. The caller drops those, so an
 * unsure extraction becomes an absent chip rather than a hedged one.
 */
interface Extraction {
  readonly value: string;
  readonly confident: boolean;
}

/**
 * Money stated explicitly, with a currency marker.
 *
 * Requires a currency symbol or code. A bare number in a brief could be anything: impressions, a
 * CPM, a date, an audience size. Only an explicitly marked amount is taken.
 */
function extractBudget(text: string): Extraction | null {
  const matches = [
    ...text.matchAll(/(?:[$£€]|\b(?:USD|GBP|EUR)\s?)\s?([\d,]+(?:\.\d+)?)\s?([KkMm]\b)?/g),
  ];
  if (matches.length === 0) return null;
  // More than one distinct amount and we cannot tell which is the budget. A brief mentioning a
  // total and a CPM would otherwise have its CPM shown as the campaign budget.
  const rendered = matches.map((m) => m[0].trim().replace(/\s+/g, ''));
  const distinct = [...new Set(rendered)];
  if (distinct.length > 1) return { value: distinct[0] ?? '', confident: false };
  return { value: distinct[0] ?? '', confident: true };
}

/**
 * Channels named in the brief, as AdCP protocol values.
 *
 * Delegates to `vocabulary.channelsInText`, which translates the everyday word a person writes into the
 * protocol value the plan needs. This used to report the matched WORD, and that was a latent failure
 * rather than a cosmetic one: a brief saying "audio" produced `channel: audio`, and AdCP's channel enum
 * has no `audio` — it has `streaming_audio`. The SDK rejects a plan whose `channels.allowed` contains
 * `audio`, so the value would have failed validation in `sync_plans` one unit later than the mistake.
 *
 * Confident whenever a channel is recognised, because this is a lookup against a closed enum rather than
 * an inference. Nothing recognised means no chip, not a guess.
 */
function extractChannel(text: string): Extraction | null {
  const channels = channelsInText(text);
  if (channels.length === 0) return null;
  return { value: describeChannels(channels), confident: true };
}

const MONTHS =
  '(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*';

/**
 * A flight window stated as a range.
 *
 * Requires BOTH ends and a separator. A single date is not a flight: it could be a launch date, a
 * deadline, or the date the brief was written.
 */
function extractFlight(text: string): Extraction | null {
  const monthDay = new RegExp(
    `(${MONTHS}\\s+\\d{1,2})\\s*(?:-|\u2013|\u2014|to|through|until)\\s*(${MONTHS}\\s+\\d{1,2})`,
    'i',
  );
  const iso = /(\d{4}-\d{2}-\d{2})\s*(?:-|\u2013|\u2014|to|through|until)\s*(\d{4}-\d{2}-\d{2})/;
  const match = monthDay.exec(text) ?? iso.exec(text);
  if (!match) return null;
  return { value: `${match[1]} - ${match[2]}`, confident: true };
}

const EXTRACTORS: Record<'budget' | 'channel' | 'flight', (text: string) => Extraction | null> = {
  budget: extractBudget,
  channel: extractChannel,
  flight: extractFlight,
};

/**
 * The `channel`, `budget` and `flight` chips.
 *
 * Plan first, then a confident extraction, then omit. A chip is never produced empty, never produced
 * with a placeholder, and never produced from an unsure extraction.
 */
export function plannedChips(
  plan: PlanValues | null,
  briefText: string | null,
): SummaryChip[] {
  const chips: SummaryChip[] = [];
  const order: Array<keyof typeof EXTRACTORS> = ['channel', 'budget', 'flight'];

  for (const key of order) {
    const fromPlan = plan?.[key];
    if (typeof fromPlan === 'string' && fromPlan.trim().length > 0) {
      chips.push({
        key,
        label: key,
        value: fromPlan.trim(),
        kind: 'brief',
        source: 'plan',
      });
      continue;
    }
    if (briefText === null) continue;
    const extracted = EXTRACTORS[key](briefText);
    // The omission point. An unsure extraction produces nothing.
    if (!extracted || !extracted.confident || extracted.value.length === 0) continue;
    chips.push({
      key,
      label: key,
      value: extracted.value,
      kind: 'brief',
      source: 'request_text',
    });
  }
  return chips;
}

/**
 * Entity chips contributed by completed phases.
 *
 * Keyed, so a later phase updating the same key replaces the chip in place, which is how the
 * prototype's `status` chip moves from one value to another. Later entries win, matching the
 * prototype's `upsertEntity`.
 */
export function mergeEntityChips(chips: readonly SummaryChip[]): SummaryChip[] {
  const byKey = new Map<string, SummaryChip>();
  for (const chip of chips) byKey.set(chip.key, chip);
  return [...byKey.values()];
}

/** The full chip list: brief chips first, then entity chips, as the prototype orders them. */
export function summaryChips(
  steps: readonly SessionStep[],
  meta: SessionMeta | null,
  plan: PlanValues | null,
  entities: readonly SummaryChip[],
): SummaryChip[] {
  const brief = briefChip(steps, meta);
  const briefText = brief?.value ?? null;
  return [
    ...(brief ? [brief] : []),
    ...plannedChips(plan, briefText),
    ...mergeEntityChips(entities),
  ];
}
