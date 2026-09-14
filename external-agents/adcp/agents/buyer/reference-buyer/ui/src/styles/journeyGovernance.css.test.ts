/**
 * The Bind/Plan stylesheet is held to the same discipline as the ported one.
 *
 * `journey.css` has a fidelity test that, among other things, asserts every rule is scoped to
 * `.journey-root` so nothing leaks into the chat view. `journeyGovernance.css` exists precisely
 * because that file may not gain rules, which means it sits outside that test's reach — so the same
 * guard is applied here explicitly rather than left as an assumption.
 *
 * A static read of the file: no build, no DOM.
 */

import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const here = dirname(fileURLToPath(import.meta.url));
const CSS = readFileSync(resolve(here, 'journeyGovernance.css'), 'utf8');

/** Selectors, with comments and declaration bodies removed. */
function selectors(): string[] {
  const withoutComments = CSS.replace(/\/\*[\s\S]*?\*\//g, '');
  const found: string[] = [];
  for (const match of withoutComments.matchAll(/([^{}]+)\{[^{}]*\}/g)) {
    const selector = (match[1] ?? '').trim();
    if (selector.length > 0) found.push(selector);
  }
  return found;
}

describe('journeyGovernance.css', () => {
  it('is a real stylesheet, not an empty file', () => {
    // Without this every assertion below could pass vacuously.
    expect(selectors().length).toBeGreaterThan(10);
  });

  it('scopes every rule to .journey-root, so nothing leaks into the chat view', () => {
    const unscoped = selectors().filter((selector) => {
      // At-rule preludes (`@keyframes`, `@media`) and the keyframe stops inside them are not
      // selectors and carry no scope of their own.
      if (selector.startsWith('@')) return false;
      if (/^(from|to|\d+%)/.test(selector)) return false;
      return !selector.split(',').every((part) => part.trim().startsWith('.journey-root'));
    });
    expect(unscoped).toEqual([]);
  });

  it('prefixes its keyframes, so it cannot collide with the ported ones', () => {
    // @keyframes are GLOBAL regardless of selector scoping, which journey.css's own header calls out
    // as a real bug it fixes rather than tidiness. Same hazard here.
    const names = [...CSS.matchAll(/@keyframes\s+([A-Za-z0-9_-]+)/g)].map((m) => m[1]);
    expect(names.length).toBeGreaterThan(0);
    names.forEach((name) => expect(name).toMatch(/^journey-/));
  });

  it('reuses the prototype palette rather than starting a second one', () => {
    // Accent colours come from journey.css's tokens. A raw hex accent here would be a parallel
    // palette, which the style guide forbids and which drifts on the first theme change.
    expect(CSS).toContain('var(--cyan)');
    expect(CSS).toContain('var(--peri)');
    expect(CSS).toContain('var(--purple)');
    expect(CSS).toContain('var(--mono)');
  });

  it('honours prefers-reduced-motion', () => {
    // The travelling pulse and the status dot both animate; a user asking for reduced motion must get
    // the same information without them.
    expect(CSS).toContain('prefers-reduced-motion');
  });

  it('never tints an evaluation chip as a pass', () => {
    // `will evaluate` lists what the governance agent SAID IT WILL assess. Green is this project's
    // success colour, and a green chip beside "geo_compliance" would claim a check that has not run.
    const willEval = /\.gchip\.willeval\s*\{([^}]*)\}/.exec(CSS)?.[1] ?? '';
    expect(willEval.length).toBeGreaterThan(0);
    expect(willEval).not.toMatch(/--green|#4ade80|#006c24/);
  });
});

describe('journeyGovernance.css: the bind arrow turns with the layout', () => {
  /**
   * The defect: `.gbind` is a wrapping flex row, so on a narrow card the account node drops below the
   * agent node while the link stays beside the agent — and the arrow then pointed rightward at empty
   * space, with its target underneath it. An arrow that points at nothing is worse than no arrow,
   * because direction is the only thing it carries.
   */
  const stacked = (() => {
    const start = CSS.indexOf('@container panel');
    return start === -1 ? '' : CSS.slice(start);
  })();

  it('re-points the arrow with a container query, not a media query', () => {
    // The panel is half the stage at desktop widths and the whole stage below the flow's breakpoint, so
    // the viewport says nothing about how much room this body has.
    expect(CSS).toContain('@container panel');
  });

  it('stacks the binding and turns the head downward', () => {
    expect(stacked).toMatch(/\.gbind\s*\{[^}]*flex-direction:\s*column/);
    // A downward CSS triangle: transparent sides, a solid top edge, and the bottom edge cleared so the
    // horizontal form's `border-left` cannot survive into this one.
    expect(stacked).toMatch(/\.gtip\s*\{[^}]*border-top:\s*6px solid var\(--cyan\)/);
    expect(stacked).toMatch(/\.gtip\s*\{[^}]*border-bottom:\s*none/);
  });

  it('sends the travelling pulse down the same channel', () => {
    // The horizontal keyframe animates `left`, which on a vertical link would drive the dot sideways out
    // of the card.
    expect(stacked).toContain('journey-gbind-descend');
    expect(CSS).toMatch(/@keyframes journey-gbind-descend[\s\S]*?top:\s*calc\(100% - 6px\)/);
  });

  it('parks the pulse on the vertical axis under reduced motion', () => {
    // The unstacked reduced-motion rule parks the dot by setting `left`, which is mid-track only on a
    // horizontal link. On a vertical one it lands half a card to the side of the channel.
    expect(stacked).toMatch(/prefers-reduced-motion[\s\S]*?top:\s*calc\(50% - 3px\)/);
  });
});
