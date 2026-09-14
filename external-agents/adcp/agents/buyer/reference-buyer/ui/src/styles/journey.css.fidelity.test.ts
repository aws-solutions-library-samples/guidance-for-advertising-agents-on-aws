/**
 * Proves `journey.css` is still the prototype's stylesheet.
 *
 * The visual design of the journey view is fixed: every animation, transition, colour, font,
 * size and easing curve is reproduced exactly from `.kiro/specs/journey.html`. "Copied
 * byte-for-byte" is the kind of claim that decays the first time someone tidies a value, so
 * this test re-extracts the prototype's <style> block on every run and compares declaration
 * bodies. A drifted value fails the build instead of quietly changing the design.
 *
 * It also keeps the prototype as the source of truth: editing `journey.html` fails here
 * rather than leaving the two silently out of step.
 *
 * Only SELECTORS may differ, and only in the two documented ways (scoping prefix, plus four
 * document-level selectors remapped onto the wrapper). Declarations may not differ at all.
 *
 * Runs in the suite's default jsdom environment rather than declaring `@vitest-environment
 * node`. Reading files works either way (vitest runs on node; jsdom only adds DOM globals), and
 * the shared `test/setup.ts` touches `window` at import time, so opting this one file out of
 * jsdom would mean changing shared setup for one test's benefit.
 */

import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const here = dirname(fileURLToPath(import.meta.url));
const PROTOTYPE = resolve(here, '../../../../../../.kiro/specs/journey.html');
const PORTED = resolve(here, 'journey.css');

/** The four selectors that cannot be mechanically prefixed, and what they become. */
const ADAPTED: Record<string, string> = {
  ':root': '.journey-root',
  'html,body': '.journey-root',
  body: '.journey-root',
  '*': '.journey-root *',
};

const SCOPE = '.journey-root';

/**
 * Keyframes are prefixed, because @keyframes are GLOBAL however the selectors are scoped.
 *
 * The prototype's `pulse` was overriding Tailwind's own `pulse`, which the chat view uses for its
 * typing caret. Renaming is the only fix; scoping selectors does nothing for keyframes. The animation
 * content is untouched, so the journey looks identical.
 *
 * This is the one transformation that reaches inside a declaration body, so the tests below apply it
 * to the prototype's text before comparing rather than relaxing the comparison.
 */
const KEYFRAME_PREFIX = 'journey-';

interface Rule {
  selector: string;
  /** Declaration text including the surrounding braces, exactly as written. */
  body: string;
}

/**
 * Split a stylesheet into top-level rules, tracking brace depth so `@keyframes` bodies stay
 * whole. Comments are removed from the prelude only, never from a declaration body.
 */
function parseRules(css: string): Rule[] {
  const rules: Rule[] = [];
  let i = 0;
  while (i < css.length) {
    const open = css.indexOf('{', i);
    if (open === -1) break;
    let depth = 1;
    let k = open + 1;
    while (k < css.length && depth > 0) {
      const ch = css[k];
      if (ch === '{') depth += 1;
      else if (ch === '}') depth -= 1;
      k += 1;
    }
    const selector = css
      .slice(i, open)
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .trim();
    if (selector.length > 0) {
      rules.push({ selector, body: css.slice(open, k) });
    }
    i = k;
  }
  return rules;
}

/** The prototype's design stylesheet: the <style> block that is not the injected theme. */
function prototypeCss(): string {
  const html = readFileSync(PROTOTYPE, 'utf8');
  const blocks = [...html.matchAll(/<style([^>]*)>([\s\S]*?)<\/style>/g)];
  const design = blocks.filter((m) => !(m[1] ?? '').includes('qw-theme'));
  if (design.length !== 1) {
    throw new Error(
      `Expected exactly one design <style> block in journey.html, found ${design.length}. ` +
        'If the prototype gained a stylesheet, this test needs to know which one is the design.',
    );
  }
  return design[0]?.[2] ?? '';
}

/**
 * The transform applied to a source selector, checked FORWARD rather than reversed.
 *
 * Reversing is ambiguous and was a bug in the first version of this test: `:root`, `html,body`
 * and `body` all become `.journey-root`, so a ported selector cannot say which source it came
 * from. Applying the transform to the source and comparing is deterministic.
 */
function scopeSelector(selector: string, keyframeNames: readonly string[]): string {
  if (selector.startsWith('@keyframes')) {
    const name = selector.split(/\s+/)[1] ?? '';
    return `@keyframes ${KEYFRAME_PREFIX}${name}`;
  }
  const adapted = ADAPTED[selector];
  if (adapted !== undefined) return adapted;
  void keyframeNames;
  return selector
    .split(',')
    .map((part) => `${SCOPE} ${part.trim()}`)
    .join(', ');
}

/** Point a source declaration body's `animation:` references at the prefixed keyframe names. */
function renameAnimations(body: string, keyframeNames: readonly string[]): string {
  return keyframeNames.reduce(
    (text, name) =>
      text.replace(
        new RegExp(`(animation:\\s*)${name}\\b`, 'g'),
        `$1${KEYFRAME_PREFIX}${name}`,
      ),
    body,
  );
}

describe('journey.css is the prototype stylesheet', () => {
  const source = parseRules(prototypeCss());
  const ported = parseRules(readFileSync(PORTED, 'utf8'));
  const keyframeNames = source
    .filter((rule) => rule.selector.startsWith('@keyframes'))
    .map((rule) => rule.selector.split(/\s+/)[1] ?? '');

  it('extracts a non-trivial stylesheet from the prototype', () => {
    // Guards against the extraction silently matching nothing and the whole suite passing
    // vacuously, which would be worse than failing.
    expect(source.length).toBeGreaterThan(100);
  });

  it('ports every rule, none added and none dropped', () => {
    expect(ported.length).toBe(source.length);
  });

  it('keeps every declaration body byte-identical, apart from the keyframe rename', () => {
    const drifted: string[] = [];
    source.forEach((rule, index) => {
      const mirror = ported[index];
      if (!mirror) {
        drifted.push(`${rule.selector}: missing from journey.css`);
        return;
      }
      // The source body with only the documented keyframe rename applied. Everything else must match
      // exactly: a changed length, colour, easing curve or duration fails here.
      const expected = renameAnimations(rule.body, keyframeNames);
      if (mirror.body !== expected) {
        drifted.push(
          `${rule.selector}\n  expected: ${expected}\n  ported  : ${mirror.body}`,
        );
      }
    });
    expect(drifted).toEqual([]);
  });

  it('changes nothing in a declaration body except an animation name', () => {
    // Guards the rename from becoming a licence to edit values. Every difference between source and
    // port must be accounted for by swapping a keyframe identifier and nothing else.
    source.forEach((rule, index) => {
      const mirror = ported[index];
      if (!mirror) return;
      const strippedSource = keyframeNames.reduce(
        (text, name) => text.replace(new RegExp(`\\b${name}\\b`, 'g'), ''),
        rule.body,
      );
      const strippedPort = keyframeNames.reduce(
        (text, name) => text.replace(new RegExp(`\\b${KEYFRAME_PREFIX}${name}\\b`, 'g'), ''),
        mirror.body,
      );
      expect(strippedPort).toBe(strippedSource);
    });
  });

  it('scopes every rule to .journey-root, so nothing leaks into the chat view', () => {
    const unscoped = ported
      .filter((rule) => !rule.selector.startsWith('@keyframes'))
      .filter((rule) =>
        rule.selector
          .split(',')
          .some((part) => !part.trim().startsWith(SCOPE)),
      )
      .map((rule) => rule.selector);
    expect(unscoped).toEqual([]);
  });

  it('adapts only the four documented document-level selectors', () => {
    const adaptedInSource = source
      .map((rule) => rule.selector)
      .filter((selector) => selector in ADAPTED);
    // Every one of the four appears in the prototype exactly once, and no others are adapted.
    expect(new Set(adaptedInSource)).toEqual(new Set(Object.keys(ADAPTED)));
  });

  it('applies only the scoping transform to selectors', () => {
    const normalise = (s: string) => s.replace(/\s+/g, ' ').replace(/\s*,\s*/g, ', ').trim();
    const changed: string[] = [];
    source.forEach((rule, index) => {
      const mirror = ported[index];
      if (!mirror) return;
      const expected = scopeSelector(rule.selector, keyframeNames);
      if (normalise(mirror.selector) !== normalise(expected)) {
        changed.push(
          `${rule.selector}\n  expected: ${expected}\n  actual  : ${mirror.selector}`,
        );
      }
    });
    expect(changed).toEqual([]);
  });

  it('prefixes the keyframe names, so they cannot collide with the app\u2019s own', () => {
    const portedNames = ported
      .filter((r) => r.selector.startsWith('@keyframes'))
      .map((r) => r.selector.split(/\s+/)[1]);
    expect(keyframeNames).toEqual(['scan', 'reject', 'pulse']);
    expect(portedNames).toEqual(['journey-scan', 'journey-reject', 'journey-pulse']);
  });

  it('declares no unprefixed keyframe, since the app defines a `pulse` of its own', () => {
    // Tailwind's `animate-pulse` fades to .5 and the prototype's fades to .35. Whichever is emitted
    // last wins for BOTH, so an unprefixed `pulse` here silently changed the chat view's typing caret.
    // Scoping selectors does not scope keyframes; this is the assertion that keeps them apart.
    ported
      .filter((r) => r.selector.startsWith('@keyframes'))
      .forEach((r) => {
        expect(r.selector).toContain(KEYFRAME_PREFIX);
      });
  });

  it('points every animation declaration at a prefixed keyframe', () => {
    // A renamed keyframe with an un-renamed reference would silently stop animating.
    const referenced = ported.flatMap((rule) => [
      ...rule.body.matchAll(/animation:\s*([A-Za-z0-9_-]+)/g),
    ]).map((match) => match[1]);
    expect(referenced.length).toBeGreaterThan(0);
    referenced.forEach((name) => expect(name).toContain(KEYFRAME_PREFIX));
  });

  it('hoists keyframes to the top level rather than nesting them', () => {
    // @keyframes inside a style rule is invalid CSS, so a nesting-based port would break them.
    const keyframes = ported.filter((r) => r.selector.startsWith('@keyframes'));
    expect(keyframes).toHaveLength(3);
    keyframes.forEach((r) => expect(r.selector).not.toContain(SCOPE));
  });
});
