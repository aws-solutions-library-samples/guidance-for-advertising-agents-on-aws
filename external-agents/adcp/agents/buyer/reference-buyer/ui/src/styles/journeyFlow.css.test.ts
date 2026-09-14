/**
 * The flow-layout stylesheet is held to the same discipline as the ported one.
 *
 * Same reasoning as `journeyGovernance.css.test.ts`: this file exists because `journey.css` may not
 * gain rules, which puts it outside the fidelity test's reach, so that test's guarantees are asserted
 * here explicitly rather than assumed.
 *
 * Two of these are about the grid specifically, and both encode a decision that is a one-word edit to
 * undo and produces a subtly wrong chronology when undone.
 *
 * A static read of the file: no build, no DOM.
 */
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const here = dirname(fileURLToPath(import.meta.url));
const CSS = readFileSync(resolve(here, 'journeyFlow.css'), 'utf8');

/**
 * The stylesheet with every comment removed.
 *
 * The "must not contain" assertions below have to read this rather than the raw file. Both of the
 * properties they forbid are NAMED in the comments that explain why they are forbidden, so asserting
 * against the raw text failed on the documentation of the rule rather than on any declaration — and
 * the only way to make it pass would have been to delete the explanation.
 */
const DECLARATIONS = CSS.replace(/\/\*[\s\S]*?\*\//g, '');

/** Selectors, with comments and declaration bodies removed. */
function selectors(): string[] {
  const found: string[] = [];
  for (const match of DECLARATIONS.matchAll(/([^{}]+)\{[^{}]*\}/g)) {
    const selector = (match[1] ?? '').trim();
    if (selector.length > 0) found.push(selector);
  }
  return found;
}

describe('journeyFlow.css', () => {
  it('is a real stylesheet, not an empty file', () => {
    expect(selectors().length).toBeGreaterThan(3);
  });

  it('scopes every rule to .journey-root, so nothing leaks into the chat view', () => {
    const unscoped = selectors().filter((selector) => {
      if (selector.startsWith('@')) return false;
      if (/^(from|to|\d+%)/.test(selector)) return false;
      return !selector.split(',').every((part) => part.trim().startsWith('.journey-root'));
    });
    expect(unscoped).toEqual([]);
  });

  it('overrides .flow rather than leaving the ported column layout in place', () => {
    expect(CSS).toMatch(/\.journey-root \.flow\s*\{[\s\S]*display:\s*grid/);
  });
});

describe('journeyFlow.css: masonry, not rows', () => {
  it('uses one row unit per pixel, so a card can span its own height', () => {
    // The whole mechanism. Without it every card in a row shares that row's height and a short card
    // leaves dead space beneath it the size of the difference.
    expect(DECLARATIONS).toMatch(/grid-auto-rows:\s*1px/);
  });

  it('sets row-gap to zero and only column-gap to 20px', () => {
    // A real `row-gap` would be inserted between every one of the thousands of 1px rows a card spans, so a
    // 300px card would gain 300 gaps. The vertical gap is baked into each span by flowLayout.ts instead.
    expect(DECLARATIONS).toMatch(/row-gap:\s*0/);
    expect(DECLARATIONS).toMatch(/column-gap:\s*20px/);
    // A shorthand `gap` would silently reintroduce the row gap.
    expect(DECLARATIONS).not.toMatch(/[^-]gap:\s*20px/);
  });

  it('does not stretch cards to the span they were given', () => {
    // The span is rounded up and carries the trailing gap, so a stretched card would grow into the space
    // meant to separate it from the next one.
    expect(DECLARATIONS).toMatch(/align-items:\s*start/);
  });
});

describe('journeyFlow.css: the grid decisions that are easy to undo', () => {
  it('does not pack densely, so a later phase cannot backfill an earlier gap', () => {
    // `grid-auto-flow: dense` would reorder against explicit placement. The flow is a chronology.
    expect(DECLARATIONS).not.toContain('dense');
  });

  it('uses auto-fill rather than auto-fit, so placed cards keep their width', () => {
    // `auto-fit` collapses empty tracks: the first card to arrive would be full width and then jump to
    // a third of it as its neighbours arrived, relaying out everything already on screen mid-reveal.
    expect(DECLARATIONS).toContain('auto-fill');
    expect(DECLARATIONS).not.toContain('auto-fit');
  });

  it('gives non-card children the whole row', () => {
    // The other-sellers notice must stay adjacent to the Discover panel it describes; a column slot
    // beside an unrelated phase is exactly the misreading it was moved to prevent.
    expect(CSS).toContain("[data-testid='seller-journey-notice']");
    expect(CSS).toMatch(/grid-column:\s*1\s*\/\s*-1/);
  });

  it('collapses to a single column when two tracks no longer fit', () => {
    expect(CSS).toContain('@media');
    expect(CSS).toMatch(/grid-template-columns:\s*minmax\(0,\s*1fr\)/);
  });
});

describe('journeyFlow.css: the reveal sequence', () => {
  it('holds the card at zero opacity before fading it, so the arrow lands first', () => {
    // The whole reason the sequence needs no change to `useJourneyPlayback`: the card is placed in the
    // grid as soon as its phase is revealed and only its opacity waits.
    expect(DECLARATIONS).toContain('--flow-hold-ms');
    expect(DECLARATIONS).toMatch(/transition:[^;]*opacity[^;]*var\(--flow-hold-ms\)/);
  });

  it('does not delay box-shadow along with opacity', () => {
    // box-shadow is the hover and awake response. A delayed one feels broken to a pointer.
    expect(DECLARATIONS).toMatch(/transition:\s*box-shadow 0\.4s,/);
  });

  it('draws the arrows with clip-path rather than a scale transform', () => {
    // `transform: scaleX` would stretch each dash as the line grew, so the dashes would start long and
    // shrink — which reads as a rendering fault, not as a line being drawn.
    expect(DECLARATIONS).toContain('clip-path: inset(');
    expect(DECLARATIONS).not.toMatch(/animation[^;]*scaleX/);
    expect(DECLARATIONS).not.toMatch(/transform:\s*scaleX/);
  });

  it('pulses the wake glow a finite number of times', () => {
    // `.awake` persists until the next phase reveals, so an infinite pulse leaves the last card of a
    // finished run throbbing indefinitely.
    expect(DECLARATIONS).toMatch(/animation:\s*journey-flow-wake[^;]*\s2;/);
    expect(DECLARATIONS).not.toMatch(/journey-flow-wake[^;]*infinite/);
  });

  it('animates an overlay rather than restating the ported awake shadow', () => {
    // Keyframing `.awake`'s own box-shadow would need its exact values as the start and end stops — a
    // second copy of a byte-checked declaration, free to drift from the original.
    expect(DECLARATIONS).toMatch(/\.panel\.awake::after/);
    expect(DECLARATIONS).toMatch(/@keyframes journey-flow-wake[\s\S]*?opacity/);
  });

  it('prefixes every keyframe, so it cannot collide with the ported ones', () => {
    const names = [...DECLARATIONS.matchAll(/@keyframes\s+([A-Za-z0-9_-]+)/g)].map((m) => m[1]);
    expect(names.length).toBeGreaterThan(0);
    names.forEach((name) => expect(name).toMatch(/^journey-/));
  });

  it('drops the hold as well as the motion under prefers-reduced-motion', () => {
    // A one-second wait before a card becomes legible is a delay, not motion a reader asked to be
    // spared. Leaving it in would make the reduced-motion path slower to read than the default.
    expect(DECLARATIONS).toContain('prefers-reduced-motion');
    const reduced = DECLARATIONS.slice(DECLARATIONS.indexOf('prefers-reduced-motion'));
    expect(reduced).toMatch(/transition:[\s\S]*opacity 0\.2s/);
    expect(reduced).not.toContain('--flow-hold-ms');
    expect(reduced).toContain('clip-path: none');
  });
});

describe('journeyFlow.css: the grid resolves to two tracks, so rows pack', () => {
  it('floors the column wide enough that three tracks can never fit the stage', () => {
    // The stage caps at 1600px with 34px padding either side, so the flow is at most 1532px. Three
    // tracks plus their gaps must exceed that, or a third column appears at wide viewports and splits
    // the pairs that belong together (Bind with Plan, Govern with Score).
    const floor = Number(/minmax\((\d+)px,\s*1fr\)/.exec(DECLARATIONS)?.[1]);
    expect(Number.isFinite(floor)).toBe(true);
    const gap = 20;
    const widestFlow = 1600 - 34 * 2;
    expect(floor * 3 + gap * 2).toBeGreaterThan(widestFlow);
    // ...and two must still fit, or the desktop layout is a single column.
    expect(floor * 2 + gap).toBeLessThanOrEqual(widestFlow);
  });

  it('makes each panel a query container, so a body can size against its card', () => {
    // A panel is about half the stage above the breakpoint and the whole stage below it, so a viewport
    // media query cannot tell a body how much room it has. Bind's arrow depends on this.
    expect(DECLARATIONS).toMatch(/\.panel\s*\{[^}]*container-type:\s*inline-size/);
    expect(DECLARATIONS).toContain('container-name: panel');
  });
});
