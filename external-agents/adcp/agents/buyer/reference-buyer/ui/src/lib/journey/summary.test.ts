import { describe, expect, it } from 'vitest';

import type { SessionMeta, SessionStep } from '../types';
import { briefChip, mergeEntityChips, plannedChips, readBrief, summaryChips } from './summary';
import type { SummaryChip } from './types';

const request = (text: string, index = 0): SessionStep => ({
  step_index: index,
  step_type: 'incoming_request',
  content: { from: 'someone', text },
});

const meta = (preview?: string): SessionMeta => ({
  session_id: 's',
  status: 'active',
  ...(preview === undefined ? {} : { request_preview: preview }),
});

describe('readBrief', () => {
  it('prefers the full incoming_request text', () => {
    const brief = readBrief([request('Reach A25-54 US commuters')], meta('Reach A25-54'));
    expect(brief?.text).toBe('Reach A25-54 US commuters');
    expect(brief?.source).toBe('request_text');
  });

  it('falls back to request_preview and says so, since it is truncated to 80 chars', () => {
    const brief = readBrief([], meta('Reach A25-54 US commuters'));
    expect(brief?.text).toBe('Reach A25-54 US commuters');
    expect(brief?.source).toBe('request_preview');
  });

  it('returns null when nothing recorded a brief', () => {
    expect(readBrief([], meta())).toBeNull();
    expect(readBrief([], null)).toBeNull();
  });

  it('ignores an empty or whitespace-only text', () => {
    expect(readBrief([request('   ')], null)).toBeNull();
  });

  it('takes the earliest incoming_request by step_index', () => {
    const brief = readBrief([request('second', 5), request('first', 1)], null);
    expect(brief?.text).toBe('first');
  });
});

describe('briefChip', () => {
  it('renders the real request text', () => {
    const chip = briefChip([request('premium streaming audio')], null);
    expect(chip).toEqual({
      key: 'brief',
      label: 'brief',
      value: 'premium streaming audio',
      kind: 'brief',
      source: 'request_text',
    });
  });

  it('is null when no brief was recorded, so no chip renders', () => {
    expect(briefChip([], null)).toBeNull();
  });
});

describe('plannedChips: plan takes precedence', () => {
  it('uses the plan value and records the provenance', () => {
    const chips = plannedChips(
      { budget: '$250,000', channel: 'audio', flight: 'Aug 1 - Sep 30' },
      'a brief mentioning $120,000',
    );
    const budget = chips.find((c) => c.key === 'budget');
    expect(budget?.value).toBe('$250,000');
    // The plan is the authorisation of record; the prose is not. Provenance makes a regression here
    // detectable, which no assertion on the value alone would be.
    expect(budget?.source).toBe('plan');
  });

  it('ignores an empty plan field and falls through to extraction', () => {
    const chips = plannedChips({ budget: '   ' }, 'total of $120,000 committed');
    expect(chips.find((c) => c.key === 'budget')?.source).toBe('request_text');
  });
});

describe('plannedChips: extraction from brief prose', () => {
  it('extracts an explicitly marked amount', () => {
    const chips = plannedChips(null, 'Reach A25-54 US commuters, premium streaming audio, $120K');
    expect(chips.find((c) => c.key === 'budget')?.value).toBe('$120K');
  });

  it('omits the budget when the brief names more than one amount', () => {
    // A brief stating a total and a CPM would otherwise show the CPM as the campaign budget.
    const chips = plannedChips(null, 'Budget $120,000 at no more than $28 CPM');
    expect(chips.find((c) => c.key === 'budget')).toBeUndefined();
  });

  it('omits the budget for a bare number with no currency marker', () => {
    // 120000 could be impressions, an audience size, or a date. Only a marked amount is taken.
    const chips = plannedChips(null, 'we want 120000 of them');
    expect(chips.find((c) => c.key === 'budget')).toBeUndefined();
  });

  it('extracts channels and drops the broader term when a specific one matched', () => {
    const chips = plannedChips(null, 'premium streaming audio and podcast inventory');
    const channel = chips.find((c) => c.key === 'channel')?.value ?? '';
    expect(channel).toContain('streaming audio');
    expect(channel).toContain('podcast');
    // "audio" is contained within "streaming audio" and must not also be reported.
    expect(channel.split(' + ')).not.toContain('audio');
  });

  it('extracts a flight only when both ends are stated', () => {
    expect(plannedChips(null, 'flight Aug 6 - Sep 6').find((c) => c.key === 'flight')?.value).toBe(
      'Aug 6 - Sep 6',
    );
    expect(
      plannedChips(null, '2026-08-06 to 2026-09-06').find((c) => c.key === 'flight')?.value,
    ).toBe('2026-08-06 - 2026-09-06');
  });

  it('omits the flight for a single date', () => {
    // One date could be a launch, a deadline, or when the brief was written.
    expect(plannedChips(null, 'launching Aug 6').find((c) => c.key === 'flight')).toBeUndefined();
  });

  it('produces no chips at all from a brief that states none of them', () => {
    expect(plannedChips(null, 'find me something good')).toEqual([]);
  });

  it('produces no chips when there is no brief text and no plan', () => {
    expect(plannedChips(null, null)).toEqual([]);
  });

  it('never produces an empty, placeholder or zero-valued chip', () => {
    const briefs = [
      '',
      'nothing here',
      '$',
      'Aug',
      'audio',
      'Reach A25-54 US commuters, premium streaming audio, $120K, Aug 6 - Sep 6',
    ];
    briefs.forEach((text) => {
      plannedChips(null, text).forEach((chip) => {
        expect(chip.value.trim().length).toBeGreaterThan(0);
        expect(chip.value).not.toBe('0');
        expect(chip.value).not.toBe('-');
      });
    });
  });

  it('orders chips channel, budget, flight as the prototype lays them out', () => {
    const chips = plannedChips(
      { channel: 'audio', budget: '$1', flight: 'Aug 1 - Aug 2' },
      null,
    );
    expect(chips.map((c) => c.key)).toEqual(['channel', 'budget', 'flight']);
  });
});

describe('mergeEntityChips', () => {
  const chip = (key: string, value: string): SummaryChip => ({
    key,
    label: key,
    value,
    kind: 'entity',
    source: 'tool_result',
  });

  it('replaces a chip in place when a later phase updates the same key', () => {
    // The prototype's `status` chip moves from one value to another rather than appearing twice.
    const merged = mergeEntityChips([chip('status', 'live'), chip('status', 'delivering')]);
    expect(merged).toHaveLength(1);
    expect(merged[0]?.value).toBe('delivering');
  });

  it('keeps distinct keys', () => {
    const merged = mergeEntityChips([chip('reach', '2M'), chip('status', 'live')]);
    expect(merged.map((c) => c.key)).toEqual(['reach', 'status']);
  });
});

describe('summaryChips', () => {
  it('puts the brief first, then planned chips, then entity chips', () => {
    const chips = summaryChips(
      [request('premium streaming audio, $120K')],
      null,
      null,
      [{ key: 'products', label: 'products', value: '12', kind: 'entity', source: 'tool_result' }],
    );
    expect(chips[0]?.key).toBe('brief');
    expect(chips.map((c) => c.key)).toContain('channel');
    expect(chips[chips.length - 1]?.key).toBe('products');
  });

  it('produces only entity chips when no brief was recorded', () => {
    const chips = summaryChips(
      [],
      null,
      null,
      [{ key: 'products', label: 'products', value: '3', kind: 'entity', source: 'tool_result' }],
    );
    expect(chips.map((c) => c.key)).toEqual(['products']);
  });

  it('produces nothing at all for an empty session', () => {
    expect(summaryChips([], null, null, [])).toEqual([]);
  });
});


/**
 * The brief the header shows once the agent has understood the request.
 *
 * A user's opening message is usually a paragraph of context. What the agent distilled from it is the
 * `brief` string it sent to the sellers, and that string is what determined which inventory came back,
 * so it is both the more accurate and the more useful thing to display.
 *
 * It is read verbatim from the recorded tool call. Nothing is paraphrased or summarised here.
 */
const getProducts = (brief: string, index: number): SessionStep => ({
  step_index: index,
  step_type: 'tool_call',
  content: { toolName: 'adcp_get_products', input: { brief }, callId: `c${index}` },
});

describe('readBrief prefers the brief the agent actually sent', () => {
  it('uses the get_products brief over the raw request once one exists', () => {
    const brief = readBrief(
      [
        request('Hi, we are planning something for the holidays, lots of background context...', 0),
        getProducts('streaming audio for US commuters, holiday shampoo campaign', 1),
      ],
      meta('Hi, we are planning'),
    );
    expect(brief?.text).toBe('streaming audio for US commuters, holiday shampoo campaign');
    expect(brief?.source).toBe('agent_brief');
  });

  it('falls back to the request until the agent has asked anyone', () => {
    // Before any get_products call, the request it was given is the best available answer, and saying
    // so is better than showing nothing.
    const brief = readBrief([request('Reach A25-54 US commuters', 0)], meta());
    expect(brief?.text).toBe('Reach A25-54 US commuters');
    expect(brief?.source).toBe('request_text');
  });

  it('takes the LATEST brief when the agent refined and asked again', () => {
    // The panel below shows the later call's products, so the header has to agree with it.
    const brief = readBrief(
      [
        request('anything', 0),
        getProducts('first attempt, too broad', 1),
        getProducts('refined: podcast only, US', 2),
      ],
      meta(),
    );
    expect(brief?.text).toBe('refined: podcast only, US');
  });

  it('ignores a get_products call carrying no brief', () => {
    // A browse-mode call has no brief. That must not blank the header or read as an empty brief.
    const noBrief: SessionStep = {
      step_index: 1,
      step_type: 'tool_call',
      content: { toolName: 'adcp_get_products', input: { buying_mode: 'browse' }, callId: 'c1' },
    };
    const brief = readBrief([request('Reach A25-54 US commuters', 0), noBrief], meta());
    expect(brief?.source).toBe('request_text');
  });

  it('ignores a whitespace-only brief rather than showing a blank header', () => {
    const brief = readBrief([request('Reach A25-54', 0), getProducts('   ', 1)], meta());
    expect(brief?.source).toBe('request_text');
  });

  it('does not mistake another tool call for a brief', () => {
    const otherTool: SessionStep = {
      step_index: 1,
      step_type: 'tool_call',
      content: { toolName: 'adcp_sync_plans', input: { objectives: 'not the brief' }, callId: 'c1' },
    };
    const brief = readBrief([request('Reach A25-54', 0), otherTool], meta());
    expect(brief?.text).toBe('Reach A25-54');
    expect(brief?.source).toBe('request_text');
  });

  it('reports the brief verbatim, never a shortened version of it', () => {
    // The header is allowed to be long. Truncating here would present a clipped brief as the whole one,
    // which is the same defect `request_preview` is reported separately to avoid.
    const long = `streaming audio ${'and podcast '.repeat(20)}across the US`;
    const brief = readBrief([request('x', 0), getProducts(long, 1)], meta());
    expect(brief?.text).toBe(long);
  });
});
