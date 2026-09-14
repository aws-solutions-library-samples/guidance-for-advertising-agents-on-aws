/**
 * The two behaviours of the journey shell that are not presentation.
 *
 * Both are things a reader at a booth notices immediately and neither is covered by the pure-module
 * tests, because both are about where things sit on the page and when the page moves:
 *
 *   1. The other-sellers summary belongs to Discover, so it sits directly above the Discover panel.
 *      It used to sit at the top of the flow, which put a line about product counts above the two
 *      governance panels, where it read as a summary of those.
 *   2. The active phase scrolls up under the sticky summary, in both playback modes, and a catch-up
 *      burst produces ONE scroll rather than a queue of them.
 *
 * `useJourneySession` is the only thing mocked: it polls a runtime that does not exist here. Everything
 * downstream of it — `deriveJourney`, `useJourneyPlayback`, the panels — runs for real, so the test
 * exercises the same derivation the browser does.
 */

import { cleanup, render } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SCROLL_SETTLE_MS } from '../../lib/journey/scroll';
import type { SessionStep } from '../../lib/types';

const here = dirname(fileURLToPath(import.meta.url));
const FIXTURE = resolve(
  here,
  '../../lib/journey/__fixtures__/recorded-discovery-session.json',
);

/** The real captured session — see recorded.test.ts for what it pins down. */
const recorded = JSON.parse(readFileSync(FIXTURE, 'utf8')) as SessionStep[];

/**
 * The same session with a second seller in the fan-out.
 *
 * The captured session queried one seller, so it produces no "other sellers" at all. A second entry
 * is added here, in the shape the recorder writes, purely so the notice under test has something to
 * say. Test input, in a test file — nothing here reaches a rendered surface outside this file.
 */
function withTwoSellers(steps: readonly SessionStep[]): SessionStep[] {
  return steps.map((step) => {
    if (step.step_type !== 'tool_result') return step;
    const raw = (step.content as { output?: unknown }).output;
    const payload = typeof raw === 'string' ? JSON.parse(raw) : raw;
    const results = [...payload.results];
    results.push({
      seller_id: 'gotham',
      seller_name: 'The Gotham Gazette (sandbox, MCP)',
      response: { products: [] },
    });
    const next = { ...payload, results, sellers_queried: results.length };
    return {
      ...step,
      content: { ...step.content, output: JSON.stringify(next) },
    } as SessionStep;
  });
}

const sessionState = (steps: readonly SessionStep[]) => ({
  sessionId: 'adcp-buyer-agent-test-session-0000000000',
  meta: { session_id: 'adcp-buyer-agent-test-session-0000000000', status: 'completed' },
  steps,
  sessions: [],
  origin: 'auto' as const,
  generation: 1,
  staleNote: null,
  status: null,
  selectSession: () => {},
  followNewest: () => {},
});

const sessionMock = vi.hoisted(() => ({ current: null as unknown }));

vi.mock('../../hooks/useJourneySession', () => ({
  useJourneySession: () => sessionMock.current,
}));

// Imported after the mock is registered, so the component picks up the mocked hook.
const { JourneyView } = await import('./JourneyView');

function renderJourney(steps: readonly SessionStep[]) {
  sessionMock.current = sessionState(steps);
  return render(<JourneyView invoke={vi.fn()} config={null} enabled />);
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('the other-sellers summary sits with the panel it describes', () => {
  it('renders directly above the Discover panel, not at the top of the flow', () => {
    const { container } = renderJourney(withTwoSellers(recorded));
    const notice = container.querySelector('[data-testid="seller-journey-notice"]');
    const discover = container.querySelector('[data-phase="discover"]');
    expect(notice).not.toBeNull();
    expect(discover).not.toBeNull();

    const flowChildren = Array.from(container.querySelector('.flow')?.children ?? []);
    expect(flowChildren.indexOf(discover as Element) - flowChildren.indexOf(notice as Element)).toBe(
      1,
    );
  });

  it('sits immediately above the Discover panel, the one thing it summarises', () => {
    // The defect this guards: a line reading "33 of 48 products recorded" floating free at the top of
    // the flow, where it reads as a summary of the whole session or of whichever panel follows it.
    //
    // This used to be expressed as "sits below the governance panels", which worked only while Bind
    // and Plan led the rail. Now that Discover leads, the notice is near the top again -- but
    // ATTACHED to Discover rather than detached from everything, which is the property that actually
    // prevents the misreading. So assert adjacency directly instead of a position that stood in for it.
    const { container } = renderJourney(withTwoSellers(recorded));
    const notice = container.querySelector('[data-testid="seller-journey-notice"]')!;
    const discover = container.querySelector('[data-phase="discover"]')!;

    // The notice precedes the Discover panel...
    expect(
      discover.compareDocumentPosition(notice) & Node.DOCUMENT_POSITION_PRECEDING,
    ).toBeTruthy();
    // ...and nothing comes between them.
    expect(notice.nextElementSibling).toBe(discover);

    // And the Discover panel separates it from Bind, so it cannot read as a governance summary.
    const bind = container.querySelector('[data-phase="bind"]');
    if (bind) {
      expect(discover.compareDocumentPosition(bind) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
  });

  it('renders no notice when the session queried one seller', () => {
    // The captured session as recorded. One seller means there are no others, so there is no notice —
    // not an empty one.
    const { container } = renderJourney(recorded);
    expect(container.querySelector('[data-testid="seller-journey-notice"]')).toBeNull();
  });
});

describe('the flow places every cell explicitly, in order', () => {
  /**
   * Masonry needs explicit `grid-row`/`grid-column` on every child, computed from a description of the
   * flow built alongside the render rather than counted during it. That description and the actual
   * children can drift: `PHASES.map` returns `null` for a phase with no state and does not advance a
   * counter, so an off-by-one gives every card the wrong height and position — silently, because each
   * placement is individually valid.
   *
   * jsdom lays nothing out, so every measured height is 0 and every card packs at the assumed height into
   * column 1. That still catches drift: the row starts must be non-decreasing in DOM order, which is false
   * the moment the index mapping slips.
   */
  const rowStartOf = (el: Element): number =>
    Number(/^(\d+)/.exec((el as HTMLElement).style.gridRow)?.[1]);

  it('gives each flow child a grid row, non-decreasing in DOM order', () => {
    const { container } = renderJourney(withTwoSellers(recorded));
    const placed = Array.from(container.querySelectorAll('.flow > [style*="grid-row"]'));
    expect(placed.length).toBeGreaterThan(1);

    const rows = placed.map(rowStartOf);
    rows.forEach((row) => expect(Number.isFinite(row)).toBe(true));
    // Non-decreasing, not increasing: two cards filling two columns legitimately start level. What must
    // not happen is a later card starting ABOVE an earlier one, since the flow is a chronology.
    rows.forEach((row, i) => {
      if (i === 0) return;
      expect(row).toBeGreaterThanOrEqual(rows[i - 1] as number);
    });
  });

  it('spans each card by its own height rather than sharing a row height', () => {
    // The gap this replaces: a row-based grid makes every card in a row as tall as the tallest, stranding
    // dead space under the short one. A per-card span is what makes the columns independent.
    const { container } = renderJourney(recorded);
    const cell = container.querySelector<HTMLElement>('.flow > [data-phase]')!;
    expect(cell.style.gridRow).toMatch(/^\d+ \/ span \d+$/);
  });

  it('places every rendered phase cell, none skipped', () => {
    // A cell whose index lookup missed would render with no inline placement and be auto-placed into
    // whatever gap the grid had left, which under a serpentine is rarely the right one.
    const { container } = renderJourney(recorded);
    const cells = Array.from(container.querySelectorAll('.flow > [data-phase]'));
    expect(cells.length).toBeGreaterThan(0);
    cells.forEach((cell) => expect((cell as HTMLElement).style.gridRow).not.toBe(''));
  });

  it('keeps the notice on its own row, immediately above Discover', () => {
    const { container } = renderJourney(withTwoSellers(recorded));
    const notice = container.querySelector<HTMLElement>('[data-testid="seller-journey-notice"]')!;
    const discover = container.querySelector<HTMLElement>('[data-phase="discover"]')!;
    // Placement is on the notice's own root, not a wrapper: a wrapper would break the adjacency the
    // tests above pin, and the notice would stop being Discover's previous sibling.
    expect(notice.style.gridRow).not.toBe('');
    expect(rowStartOf(notice)).toBeLessThan(rowStartOf(discover));
    expect(notice.nextElementSibling).toBe(discover);
  });

  it('spans the products panel across every column', () => {
    const { container } = renderJourney(recorded);
    const discover = container.querySelector<HTMLElement>('[data-phase="discover"]')!;
    expect(discover.className).toContain('flow-cell-wide');
    expect(discover.style.gridColumn).toMatch(/span/);
  });
});

describe('the active phase scrolls up under the summary', () => {
  let scrollTo: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    scrollTo = vi.fn();
    vi.stubGlobal('scrollTo', scrollTo);
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('scrolls once the settle window has passed', () => {
    renderJourney(recorded);
    // Nothing yet: the window is what collapses a catch-up burst into a single scroll.
    expect(scrollTo).not.toHaveBeenCalled();
    vi.advanceTimersByTime(SCROLL_SETTLE_MS);
    expect(scrollTo).toHaveBeenCalledTimes(1);
  });

  it('animates, rather than jumping, whatever the playback mode', () => {
    // The recorded session is replayed, a live one is not, and the prototype animates in both. Making
    // this conditional is what left a live session jumping.
    renderJourney(recorded);
    vi.advanceTimersByTime(SCROLL_SETTLE_MS);
    expect(scrollTo).toHaveBeenCalledWith(
      expect.objectContaining({ behavior: 'smooth' }),
    );
  });

  it('never scrolls to a negative offset', () => {
    renderJourney(recorded);
    vi.advanceTimersByTime(SCROLL_SETTLE_MS);
    const [arg] = scrollTo.mock.calls[0] as [{ top: number }];
    expect(arg.top).toBeGreaterThanOrEqual(0);
  });

  it('collapses a burst of phase changes into a single scroll', () => {
    // A catch-up reveals several phases within a few frames. One animated scroll per reveal queues
    // animations that chase each other down the page.
    renderJourney(recorded);
    vi.advanceTimersByTime(SCROLL_SETTLE_MS - 1);
    vi.advanceTimersByTime(1);
    expect(scrollTo.mock.calls.length).toBeLessThanOrEqual(1);
  });
});
