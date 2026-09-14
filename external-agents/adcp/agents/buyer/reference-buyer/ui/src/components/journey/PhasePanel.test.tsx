// `fireEvent` rather than user-event, matching the existing component tests: it wraps the dispatch
// in `act`, so React has flushed by the time the assertion runs, and it avoids adding a dependency
// this unit did not plan for.
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { DEFAULT_TOUR_INTERVAL_MS } from '../../hooks/useNodeTour';
import { DEFAULT_LAYOUT } from '../../lib/journey/gravityGraph';
import { phaseByKey } from '../../lib/journey/phases';
import type { AnyPhaseState, DiscoverData, FormatGroupProduct } from '../../lib/journey/types';
import { PhasePanel } from './PhasePanel';

afterEach(cleanup);

const DISCOVER = phaseByKey('discover')!;
const GOVERN = phaseByKey('govern')!;

const discoverData: DiscoverData = {
  groups: [
    {
      formatKey: 'audio',
      label: 'Streaming audio',
      cssVar: '--audio',
      products: [
        // Two sellers in one group, which is the case the labels exist for: the icons alone cannot
        // say which of them offered what.
        {
          key: 'triton agent#p1#0',
          title: 'Drive Time National',
          seller: 'triton agent',
          formatId: 'audio_30s',
          price: 'USD 24.00 CPM',
        },
        { key: 'triton agent#p2#1', title: 'Weekend Music', seller: 'triton agent' },
        { key: 'gotham agent#p3#0', title: 'Morning Briefing', seller: 'gotham agent' },
      ],
      count: 3,
      attribution: ['triton agent', 'gotham agent'],
    },
  ],
  unmatched: 0,
  sellersQueried: 1,
  sellersResponded: 1,
  sellersErrored: 0,
};

function renderPanel(state: AnyPhaseState, revealed = true) {
  return render(
    <PhasePanel phase={DISCOVER} state={state} revealed={revealed} active={false} note="a note" />,
  );
}

describe('PhasePanel chrome', () => {
  it('renders the authored strings verbatim', () => {
    renderPanel({ state: 'not_reached' });
    expect(screen.getByText(DISCOVER.title)).toBeTruthy();
    expect(screen.getByText(DISCOVER.sub)).toBeTruthy();
    expect(screen.getByText(DISCOVER.op)).toBeTruthy();
    expect(screen.getByText(DISCOVER.n)).toBeTruthy();
  });

  it('is .pending until revealed', () => {
    const { container } = renderPanel({ state: 'not_reached' }, false);
    expect(container.querySelector('.panel')?.className).toContain('pending');
  });

  it('drops .pending once revealed', () => {
    const { container } = renderPanel({ state: 'ready', data: discoverData }, true);
    expect(container.querySelector('.panel')?.className).not.toContain('pending');
  });

  it('is .awake only while active', () => {
    const { container } = render(
      <PhasePanel
        phase={DISCOVER}
        state={{ state: 'ready', data: discoverData }}
        revealed
        active
        note=""
      />,
    );
    expect(container.querySelector('.panel')?.className).toContain('awake');
  });
});

describe('PhasePanel handles every absence state', () => {
  it('renders no body for a phase with no data', () => {
    const { container } = renderPanel({ state: 'not_reached' });
    // The authored .pending appearance is the whole treatment: no groups, no substitute content.
    expect(container.querySelector('.fmtgrids')).toBeNull();
    expect(container.querySelector('.fmtitems')).toBeNull();
  });

  it.each([
    ['not_reached', { state: 'not_reached' } as AnyPhaseState, /has not run/i],
    [
      'not_implemented',
      { state: 'not_implemented', detail: 'This build cannot call it.' } as AnyPhaseState,
      /cannot call/i,
    ],
    [
      'unreachable',
      { state: 'unreachable', detail: 'agent timed out' } as AnyPhaseState,
      /timed out/i,
    ],
  ])('offers the real reason on demand for %s', (_label, state, expected) => {
    // The three reasons must stay distinguishable: "not built yet" and "the agent is unreachable" are
    // different sentences to say to someone standing at a booth.
    render(<PhasePanel phase={GOVERN} state={state} revealed active />);
    fireEvent.click(screen.getByRole('button', { name: /why is this empty/i }));
    expect(screen.getByText(expected)).toBeTruthy();
  });

  it('keeps the reason hidden until asked, so the authored appearance is untouched', () => {
    render(<PhasePanel phase={GOVERN} state={{ state: 'not_reached' }} revealed active />);
    expect(screen.queryByText(/has not run/i)).toBeNull();
    expect(
      screen.getByRole('button', { name: /why is this empty/i }).getAttribute('aria-expanded'),
    ).toBe('false');
  });

  it('puts the reason behind a real button, so it is reachable without a pointer', () => {
    // A hover-only affordance would be unreachable by keyboard and on touch. A <button> with an
    // accessible name is focusable and activatable by both.
    render(<PhasePanel phase={GOVERN} state={{ state: 'not_reached' }} revealed active />);
    const control = screen.getByRole('button', { name: /why is this empty/i });
    expect(control.tagName).toBe('BUTTON');
    fireEvent.click(control);
    expect(screen.getByText(/has not run/i)).toBeTruthy();
    expect(control.getAttribute('aria-expanded')).toBe('true');
  });

  it('toggles the reason closed again', () => {
    render(<PhasePanel phase={GOVERN} state={{ state: 'not_reached' }} revealed active />);
    const control = screen.getByRole('button', { name: /why is this empty/i });
    fireEvent.click(control);
    fireEvent.click(control);
    expect(screen.queryByText(/has not run/i)).toBeNull();
  });
});

describe('PhasePanel renders real Discover data', () => {
  it('renders one dot per product actually returned', () => {
    const { container } = renderPanel({ state: 'ready', data: discoverData });
    expect(container.querySelectorAll('.dot-t')).toHaveLength(3);
  });

  it('labels the group with its real count', () => {
    const { container } = renderPanel({ state: 'ready', data: discoverData });
    const label = container.querySelector('.fglabel')?.textContent ?? '';
    expect(label).toContain('Streaming audio');
    expect(label).toContain('3');
  });

  it('reports what was recorded when the list was trimmed', () => {
    const { container } = renderPanel({
      state: 'ready',
      data: {
        ...discoverData,
        groups: [{ ...discoverData.groups[0]!, truncation: { kept: 3, total: 200 } }],
      },
    });
    // "3" must never stand for the seller's whole answer when it sent 200.
    expect(container.querySelector('.fglabel')?.textContent).toContain('3 of 200 recorded');
  });

  it('renders no group at all when the fan-out returned nothing', () => {
    const { container } = renderPanel({ state: 'ready', data: { ...discoverData, groups: [] } });
    expect(container.querySelectorAll('.fmtgroup')).toHaveLength(0);
    expect(container.querySelectorAll('.dot-t')).toHaveLength(0);
    expect(container.querySelectorAll('.fmtitem')).toHaveLength(0);
  });

  it('names every product beside its icon', () => {
    // The panel is headed "offered products". A grid of bare icons named none of them, which is what
    // these labels exist to fix.
    renderPanel({ state: 'ready', data: discoverData });
    expect(screen.getByText('Drive Time National')).toBeTruthy();
    expect(screen.getByText('Weekend Music')).toBeTruthy();
    expect(screen.getByText('Morning Briefing')).toBeTruthy();
  });

  it('attributes each product to the seller that returned it', () => {
    // Per row, not per group: this group holds products from two sellers, and a reader must not have
    // to guess which offered which.
    const { container } = renderPanel({ state: 'ready', data: discoverData });
    const rows = Array.from(container.querySelectorAll('.fmtitem'));
    expect(rows).toHaveLength(3);
    const attributed = rows.map((row) => [
      row.querySelector('.fmtitem-title')?.textContent,
      row.querySelector('.fmtitem-seller')?.textContent,
    ]);
    expect(attributed).toEqual([
      ['Drive Time National', 'triton agent'],
      ['Weekend Music', 'triton agent'],
      ['Morning Briefing', 'gotham agent'],
    ]);
  });

  it('shows the details a seller sent and nothing for the ones it did not', () => {
    // An unpriced product shows no price, not $0 and not a dash: both would read as a quoted figure.
    const { container } = renderPanel({ state: 'ready', data: discoverData });
    const rows = Array.from(container.querySelectorAll('.fmtitem'));
    const priced = rows[0]!.querySelectorAll('.fmtitem-fact');
    expect(Array.from(priced).map((n) => n.textContent)).toEqual(['audio_30s', 'USD 24.00 CPM']);

    const bare = rows[1]!;
    expect(bare.querySelectorAll('.fmtitem-fact')).toHaveLength(0);
    expect(bare.textContent).not.toMatch(/0\.00|\$|—|-{2}/);
  });

  it('keeps one icon per product alongside the labels', () => {
    // The authored mark is unchanged; the label is added beside it, not instead of it.
    const { container } = renderPanel({ state: 'ready', data: discoverData });
    expect(container.querySelectorAll('.fmtitem .dot-t')).toHaveLength(3);
  });

  it('renders the note it was given and nothing more', () => {
    renderPanel({ state: 'ready', data: discoverData });
    expect(screen.getByText('a note')).toBeTruthy();
  });

  it('scrolls a short list without autoscrolling it', () => {
    // Three rows fit inside the box; nothing needs to move on its own to read all of them.
    const { container } = renderPanel({ state: 'ready', data: discoverData });
    expect(container.querySelector('.fmtscroll')).not.toBeNull();
    expect(container.querySelector('.fmtscroll-auto')).toBeNull();
  });

  it('autoscrolls a long list, doubling its rows for a seamless loop', () => {
    const many = Array.from({ length: 10 }, (_, i) => ({
      key: `p${i}`,
      title: `Product ${i}`,
      seller: 'gotham agent',
    }));
    const { container } = renderPanel({
      state: 'ready',
      data: { ...discoverData, groups: [{ ...discoverData.groups[0]!, products: many, count: 10 }] },
    });
    expect(container.querySelector('.fmtscroll-auto')).not.toBeNull();
    // Doubled: ten real rows plus ten duplicates for the loop, not twenty distinct products.
    expect(container.querySelectorAll('.fmtitem')).toHaveLength(20);
    expect(screen.getAllByText('Product 0')).toHaveLength(2);
    // The duplicate half is hidden from assistive tech, since it repeats content already announced.
    expect(container.querySelectorAll('.fmtitem[aria-hidden="true"]')).toHaveLength(10);
  });

  it('caps the scroll box to the constellation\u2019s own height', () => {
    const { container } = renderPanel({ state: 'ready', data: discoverData });
    const box = container.querySelector('.fmtscroll') as HTMLElement;
    expect(box.style.maxHeight).toBe(`${DEFAULT_LAYOUT.size}px`);
  });
});

/**
 * The characteristics that tell two rows apart.
 *
 * Ten products sharing a title, format and rate rendered as ten identical rows because the row showed
 * only those three things. These assert that whatever the deriver put on a product reaches the screen,
 * and — the other half — that a product carrying none of it gains no empty scaffolding.
 */
describe('PhasePanel shows what distinguishes one product from another', () => {
  const withProducts = (products: FormatGroupProduct[]): DiscoverData => ({
    ...discoverData,
    groups: [{ ...discoverData.groups[0]!, products, count: products.length }],
  });

  it('renders every fact, in the order the deriver put them in', () => {
    const { container } = renderPanel({
      state: 'ready',
      data: withProducts([
        {
          key: 'k',
          title: 'Gotham Daily — Investigations',
          seller: 'gotham agent',
          formatId: 'gotham_display',
          price: 'USD 9.00 CPM',
          facts: [
            { key: 'placement', value: 'right-rail' },
            { key: 'spec:Edition', label: 'Edition', value: 'US National' },
            { key: 'channels', value: 'display' },
          ],
        },
      ]),
    });
    const facts = Array.from(container.querySelectorAll('.fmtitem-fact')).map((n) => n.textContent);
    // Facts lead, then the format id and price they used to stand alone with.
    expect(facts).toEqual([
      'right-rail',
      'Edition US National',
      'display',
      'gotham_display',
      'USD 9.00 CPM',
    ]);
  });

  it('dims a label without hiding it, and renders none where a fact needs none', () => {
    const { container } = renderPanel({
      state: 'ready',
      data: withProducts([
        {
          key: 'k',
          title: 't',
          seller: 's',
          facts: [
            { key: 'group', label: 'group', value: 'Granite Peak Audio' },
            { key: 'placement', value: 'right-rail' },
          ],
        },
      ]),
    });
    const labels = Array.from(container.querySelectorAll('.fmtitem-fact-label'));
    expect(labels.map((n) => n.textContent?.trim())).toEqual(['group']);
  });

  it('gives two products that differ only by placement two different rows', () => {
    // The reported defect, at the render layer.
    const { container } = renderPanel({
      state: 'ready',
      data: withProducts([
        {
          key: 'a',
          title: 'Gotham Daily — Investigations',
          seller: 'gotham agent',
          facts: [{ key: 'placement', value: 'right-rail' }],
        },
        {
          key: 'b',
          title: 'Gotham Daily — Investigations',
          seller: 'gotham agent',
          facts: [{ key: 'placement', value: 'leaderboard' }],
        },
      ]),
    });
    const rows = Array.from(container.querySelectorAll('.fmtitem')).map((n) => n.textContent);
    expect(rows).toHaveLength(2);
    expect(rows[0]).not.toBe(rows[1]);
    expect(rows[0]).toContain('right-rail');
    expect(rows[1]).toContain('leaderboard');
  });

  it('shows the reach figure, and not a disclaimer beside it', () => {
    const { container } = renderPanel({
      state: 'ready',
      data: withProducts([
        {
          key: 'k',
          title: 't',
          seller: 's',
          reach: { audience: 187328, unit: 'audience' },
        },
      ]),
    });
    const measures = container.querySelector('.fmtitem-measures');
    expect(measures?.textContent).toContain('187,328');
    // The row is not the place to restate what the seller's own name says. Anything longer than
    // the figure and its unit here repeated "(sandbox, MCP)" on every product in the panel.
    expect(measures?.textContent).toBe('187,328 reachable');
  });

  it('shows a reach of zero, which is a figure the seller measured', () => {
    const { container } = renderPanel({
      state: 'ready',
      data: withProducts([{ key: 'k', title: 't', seller: 's', reach: { audience: 0, unit: 'audience' } }]),
    });
    expect(container.querySelector('.fmtitem-reach')?.textContent).toContain('0');
  });

  it('lists the figures for each scored axis, tinted per axis', () => {
    // The per-row job is precision. The distribution is the scatter's job, below the rows.
    const { container } = renderPanel({
      state: 'ready',
      data: withProducts([
        {
          key: 'k',
          title: 't',
          seller: 's',
          similarity: [
            { axis: 'content', value: 0.77, fraction: 0.77 },
            { axis: 'audience', value: 0.88, fraction: 0.88 },
          ],
        },
      ]),
    });
    expect(container.querySelectorAll('.fmtitem-sim')).toHaveLength(2);
    expect(container.querySelector('.fmtitem-sim.content')?.textContent).toContain('0.770');
    expect(container.querySelector('.fmtitem-sim.audience')?.textContent).toContain('0.880');
  });

  it('draws no per-row similarity graphic, because the values are too close to differ', () => {
    // Two were tried and removed: a progress bar, then an angle dial. Content similarity spans
    // 0.412 to 0.457 across every recorded row, so an absolute per-row mark is the same mark ten
    // times. This pins the removal so neither comes back by accident.
    const { container } = renderPanel({
      state: 'ready',
      data: withProducts([
        {
          key: 'k',
          title: 't',
          seller: 's',
          similarity: [{ axis: 'content', value: 0.46, fraction: 0.46 }],
        },
      ]),
    });
    expect(container.querySelector('.fmtitem-bar-track')).toBeNull();
    expect(container.querySelector('.fmtitem-dial')).toBeNull();
  });

  it('prints a negative cosine as sent, rather than clamping it away', () => {
    // The bar clamped its width to 0..1, so -0.4 and -0.9 both drew an empty track.
    const { container } = renderPanel({
      state: 'ready',
      data: withProducts([
        {
          key: 'k',
          title: 't',
          seller: 's',
          similarity: [{ axis: 'content', value: -0.4, fraction: 0 }],
        },
      ]),
    });
    expect(container.querySelector('.fmtitem-sim-value')?.textContent).toBe('-0.400');
  });

  it('names the similarity figures, so they are not read as percentages', () => {
    const { container } = renderPanel({
      state: 'ready',
      data: withProducts([
        {
          key: 'k',
          title: 't',
          seller: 's',
          similarity: [{ axis: 'content', value: 0.46, fraction: 0.46 }],
        },
      ]),
    });
    const sims = container.querySelector('.fmtitem-sims');
    expect(sims?.querySelector('.fmtitem-sims-label')?.textContent).toBe('brief similarity');
    expect(sims?.getAttribute('title')).toMatch(/not a percentage/i);
    expect(sims?.textContent).not.toMatch(/%/);
  });

  it('prints an out-of-domain score as sent', () => {
    const { container } = renderPanel({
      state: 'ready',
      data: withProducts([
        { key: 'k', title: 't', seller: 's', similarity: [{ axis: 'content', value: 1.4, fraction: 1 }] },
      ]),
    });
    expect(container.querySelector('.fmtitem-sim-value')?.textContent).toBe('1.400');
  });

  it('renders no measurement line at all when the seller measured nothing', () => {
    // The whole point of the absent-versus-empty rule: no empty track, no zeroed figure.
    const { container } = renderPanel({
      state: 'ready',
      data: withProducts([{ key: 'k', title: 't', seller: 's' }]),
    });
    expect(container.querySelector('.fmtitem-measures')).toBeNull();
    expect(container.querySelector('.fmtitem-dial')).toBeNull();
    expect(container.querySelector('.fmtitem')?.textContent).not.toMatch(/0\.000|reachable/);
  });
});

/**
 * The group-level scatter.
 *
 * One plot per format group rather than a mark per row, because the values are too close together for
 * an absolute per-row mark to differ. The axes are FITTED to the products shown, which is the thing
 * that makes the spread visible and also the thing that could mislead, so the fitted extents must
 * always be on screen.
 */
describe('PhasePanel plots the offer set around the brief', () => {
  const withProducts = (products: FormatGroupProduct[]): DiscoverData => ({
    ...discoverData,
    groups: [{ ...discoverData.groups[0]!, products, count: products.length }],
  });

  const placed = (
    key: string,
    relevance: number,
    reach?: number,
    cpm?: number,
  ): FormatGroupProduct => ({
    key,
    title: `product ${key}`,
    seller: 'gotham agent',
    relevance,
    ...(reach === undefined ? {} : { reach: { audience: reach, unit: 'audience' as const } }),
    ...(cpm === undefined ? {} : { cpm, currency: 'USD' }),
  });

  it('draws one node per scored product, plus the brief at the centre', () => {
    const { container } = renderPanel({
      state: 'ready',
      data: withProducts([placed('a', 0.44, 187328, 9), placed('b', 0.34, 41569, 14)]),
    });
    expect(container.querySelectorAll('.gravity-node')).toHaveLength(2);
    expect(container.querySelector('.gravity-core')).not.toBeNull();
  });

  it('puts the better-ranked product nearer the brief', () => {
    // The spec's inverse mapping, at the render layer: high relevance is close in.
    const { container } = renderPanel({
      state: 'ready',
      data: withProducts([placed('near', 0.44), placed('far', 0.34)]),
    });
    const core = container.querySelector('.gravity-core')!;
    const cx = Number(core.getAttribute('cx'));
    const cy = Number(core.getAttribute('cy'));
    const [first, second] = Array.from(container.querySelectorAll('.gravity-node')).map((n) =>
      Math.hypot(Number(n.getAttribute('cx')) - cx, Number(n.getAttribute('cy')) - cy),
    );
    expect(first).toBeLessThan(second!);
  });

  it('sizes nodes by reach', () => {
    const { container } = renderPanel({
      state: 'ready',
      data: withProducts([placed('big', 0.44, 10000), placed('small', 0.34, 2500)]),
    });
    const [big, small] = Array.from(container.querySelectorAll('.gravity-node')).map((n) =>
      Number(n.getAttribute('r')),
    );
    expect(big!).toBeGreaterThan(small!);
  });

  it('marks a product whose reach nobody measured, so its size is not read as a figure', () => {
    const { container } = renderPanel({
      state: 'ready',
      data: withProducts([placed('measured', 0.44, 5000), placed('bare', 0.34)]),
    });
    expect(container.querySelectorAll('.gravity-node.unmeasured')).toHaveLength(1);
  });

  it('tints nodes along the CPM ramp, and leaves an unpriced one off it', () => {
    const { container } = renderPanel({
      state: 'ready',
      data: withProducts([placed('cheap', 0.44, 100, 5), placed('dear', 0.34, 100, 20)]),
    });
    const strokes = Array.from(container.querySelectorAll<SVGCircleElement>('.gravity-node')).map(
      (n) => n.style.stroke,
    );
    expect(strokes[0]).not.toBe(strokes[1]);
    expect(strokes[0]).toMatch(/^rgb\(/);
  });

  it('draws the reference rings labelled with real scores, not invented percentages', () => {
    const { container } = renderPanel({
      state: 'ready',
      data: withProducts([placed('a', 0.44), placed('b', 0.34)]),
    });
    expect(container.querySelectorAll('.gravity-ring').length).toBeGreaterThanOrEqual(3);
    const labels = Array.from(container.querySelectorAll('.gravity-ring-label')).map(
      (n) => n.textContent,
    );
    expect(labels).toContain('0.340');
    expect(labels.join(' ')).not.toMatch(/%/);
  });

  it('tethers every node to the brief', () => {
    const { container } = renderPanel({
      state: 'ready',
      data: withProducts([placed('a', 0.44), placed('b', 0.34)]),
    });
    expect(container.querySelectorAll('.gravity-tether')).toHaveLength(2);
  });

  it('says the tether weight is unencoded, since no seller returns an outcome probability', () => {
    // Otherwise uniform tethers read as a measurement that happens to be constant.
    const { container } = renderPanel({
      state: 'ready',
      data: withProducts([placed('a', 0.44), placed('b', 0.34)]),
    });
    expect(container.querySelector('.gravity-legend')?.textContent).toMatch(
      /not encoded|outcome probability/i,
    );
  });

  it('states that the radial scale is fitted to these products', () => {
    const { container } = renderPanel({
      state: 'ready',
      data: withProducts([placed('a', 0.44), placed('b', 0.34)]),
    });
    expect(container.querySelector('.gravity-sub')?.textContent).toMatch(/fitted to these 2/);
  });

  it('reports products it could not place rather than parking them on a ring', () => {
    const { container } = renderPanel({
      state: 'ready',
      data: withProducts([
        placed('a', 0.44),
        placed('b', 0.34),
        { key: 'c', title: 'unscored', seller: 'triton agent' },
      ]),
    });
    expect(container.querySelectorAll('.gravity-node')).toHaveLength(2);
    expect(container.querySelector('.gravity-sub')?.textContent).toMatch(/1 unscored, not placed/);
  });

  it('shows a product\'s figures on hover, and only the ones it carries', () => {
    const { container } = renderPanel({
      state: 'ready',
      data: withProducts([placed('a', 0.44, 187328), placed('b', 0.34)]),
    });
    const nodes = container.querySelectorAll('.gravity-node');
    fireEvent.mouseEnter(nodes[0]!);
    const detail = container.querySelector('.gravity-detail')!;
    expect(detail.textContent).toContain('0.440');
    expect(detail.textContent).toContain('187,328');
    // That product has no rate, so no rate row appears.
    expect(detail.textContent).not.toMatch(/CPM/);
  });

  it('labels a forecast figure as impressions rather than as reachable people', () => {
    // Gotham measures people, Triton forecasts impressions. One labelled as the other misstates an
    // audience by whatever the frequency happens to be.
    const { container } = renderPanel({
      state: 'ready',
      data: withProducts([
        {
          key: 'a',
          title: 'triton product',
          seller: 'triton agent',
          relevance: 0.44,
          reach: { audience: 28647, unit: 'impressions' },
        },
        placed('b', 0.34),
      ]),
    });
    fireEvent.mouseEnter(container.querySelectorAll('.gravity-node')[0]!);
    expect(container.querySelector('.gravity-detail')?.textContent).toMatch(/impressions/);
  });

  it('selects a node on click and releases it on a second click', () => {
    const { container } = renderPanel({
      state: 'ready',
      data: withProducts([placed('a', 0.44), placed('b', 0.34)]),
    });
    const node = container.querySelectorAll('.gravity-node')[0]!;
    fireEvent.click(node);
    expect(node.classList.contains('picked')).toBe(true);
    fireEvent.click(node);
    expect(node.classList.contains('picked')).toBe(false);
  });

  it('offers a CPM ceiling bounded by the real rates on offer', () => {
    const { container } = renderPanel({
      state: 'ready',
      data: withProducts([placed('a', 0.44, 100, 5), placed('b', 0.34, 100, 20)]),
    });
    const slider = container.querySelector<HTMLInputElement>('.gravity-filter input')!;
    expect(slider.min).toBe('5');
    expect(slider.max).toBe('20');
  });

  it('fades products above the ceiling and counts them', () => {
    const { container } = renderPanel({
      state: 'ready',
      data: withProducts([placed('a', 0.44, 100, 5), placed('b', 0.34, 100, 20)]),
    });
    const slider = container.querySelector<HTMLInputElement>('.gravity-filter input')!;
    fireEvent.change(slider, { target: { value: '6' } });
    expect(container.querySelectorAll('.gravity-node.out')).toHaveLength(1);
    expect(container.querySelector('.gravity-filter-label')?.textContent).toMatch(/1 hidden/);
  });

  it('never filters out a product the seller did not price', () => {
    // The seller did not say it costs more than the ceiling; hiding it would be an assumption.
    const { container } = renderPanel({
      state: 'ready',
      // Three products: two priced so there is a range to slide over, one unpriced.
      data: withProducts([
        placed('cheap', 0.44, 100, 5),
        placed('dear', 0.4, 100, 20),
        placed('unpriced', 0.34, 100),
      ]),
    });
    const slider = container.querySelector<HTMLInputElement>('.gravity-filter input')!;
    fireEvent.change(slider, { target: { value: '5' } });
    // Only the dear one is above the ceiling. The unpriced one stays.
    expect(container.querySelectorAll('.gravity-node.out')).toHaveLength(1);
    expect(container.querySelector('.gravity-filter-label')?.textContent).toMatch(/1 hidden/);
  });

  it('offers no CPM filter when nothing was priced', () => {
    const { container } = renderPanel({
      state: 'ready',
      data: withProducts([placed('a', 0.44), placed('b', 0.34)]),
    });
    expect(container.querySelector('.gravity-filter')).toBeNull();
  });

  it('draws nothing at all when no seller returned a ranking score', () => {
    // Triton and the reference seller send none today, so this is the common case.
    const { container } = renderPanel({ state: 'ready', data: discoverData });
    expect(container.querySelector('.gravity')).toBeNull();
  });
});

/**
 * The unattended tour.
 *
 * The constellation's detail panel only fills on hover, and at a booth there is no pointer. This walks
 * the nodes in the seller's own ranking order so the figures arrive without anyone reaching for a
 * mouse - and gets out of the way the moment someone does.
 */
describe('the constellation presents itself when nobody is hovering', () => {
  const withProducts = (products: FormatGroupProduct[]): DiscoverData => ({
    ...discoverData,
    groups: [{ ...discoverData.groups[0]!, products, count: products.length }],
  });

  const scored = (key: string, relevance: number): FormatGroupProduct => ({
    key,
    title: `product ${key}`,
    seller: 'gotham agent',
    relevance,
  });

  // Deliberately not in ranking order, so a passing test cannot be an accident of input order.
  const THREE = [scored('mid', 0.40), scored('worst', 0.34), scored('best', 0.45)];

  const render3 = () => renderPanel({ state: 'ready', data: withProducts(THREE) });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('fills the panel with the best-ranked product straight away', () => {
    // Without this the panel sits on its "hover to read" hint for the whole demo.
    const { container } = render3();
    const detail = container.querySelector('.gravity-detail');
    expect(detail).not.toBeNull();
    expect(detail!.textContent).toContain('product best');
    expect(container.querySelector('.gravity-hint')).toBeNull();
  });

  it('says which of the set it is showing, so it is not taken for a selection', () => {
    const { container } = render3();
    expect(container.querySelector('.gravity-detail')?.textContent).toMatch(/1 of 3, best first/);
  });

  it('walks best to worst, in the seller\'s ranking order', () => {
    vi.useFakeTimers();
    const { container } = render3();
    const showing = () => container.querySelector('.gravity-detail')?.textContent ?? '';
    expect(showing()).toContain('product best');
    act(() => void vi.advanceTimersByTime(DEFAULT_TOUR_INTERVAL_MS));
    expect(showing()).toContain('product mid');
    act(() => void vi.advanceTimersByTime(DEFAULT_TOUR_INTERVAL_MS));
    expect(showing()).toContain('product worst');
    // And round again, so an unattended screen keeps cycling.
    act(() => void vi.advanceTimersByTime(DEFAULT_TOUR_INTERVAL_MS));
    expect(showing()).toContain('product best');
  });

  it('marks the node it is presenting, the same way a hover would', () => {
    const { container } = render3();
    expect(container.querySelectorAll('.gravity-node.toured')).toHaveLength(1);
    expect(container.querySelectorAll('.gravity-node.on')).toHaveLength(1);
  });

  it('hands over to a real hover and does not take the panel back', () => {
    vi.useFakeTimers();
    const { container } = render3();
    const nodes = container.querySelectorAll('.gravity-node');
    // Node order is fan-out order, so index 0 is `mid` - deliberately not the one being toured.
    fireEvent.mouseEnter(nodes[0]!);
    expect(container.querySelector('.gravity-detail')?.textContent).toContain('product mid');
    // The ticks that would have moved the tour on must not steal the panel back.
    act(() => void vi.advanceTimersByTime(DEFAULT_TOUR_INTERVAL_MS * 3));
    expect(container.querySelector('.gravity-detail')?.textContent).toContain('product mid');
    expect(container.querySelectorAll('.gravity-node.toured')).toHaveLength(0);
  });

  it('stops for good once a node is clicked', () => {
    vi.useFakeTimers();
    const { container } = render3();
    const node = container.querySelectorAll('.gravity-node')[0]!;
    fireEvent.click(node);
    fireEvent.mouseLeave(node);
    act(() => void vi.advanceTimersByTime(DEFAULT_TOUR_INTERVAL_MS * 2));
    // The click selection survives; the tour does not resume around it.
    expect(container.querySelector('.gravity-detail')?.textContent).toContain('product mid');
    expect(container.querySelector('.gravity-touring')).toBeNull();
  });

  it('drops the position line once the visitor is driving', () => {
    const { container } = render3();
    fireEvent.mouseEnter(container.querySelectorAll('.gravity-node')[0]!);
    expect(container.querySelector('.gravity-touring')).toBeNull();
  });

  it('does not tour a single product, which has nothing to cycle', () => {
    const { container } = renderPanel({
      state: 'ready',
      data: withProducts([scored('only', 0.4)]),
    });
    expect(container.querySelector('.gravity-touring')).toBeNull();
    expect(container.querySelector('.gravity-node.toured')).toBeNull();
  });
});
