import { cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { RADIAL_COLOURS, RADIAL_RADII } from '../../../lib/journey/phases';
import type { Arc } from '../../../lib/journey/types';
import { RadialBody, type Verdict } from './RadialBody';

afterEach(cleanup);

const arc = (overrides: Partial<Arc> = {}): Arc => ({
  categoryId: 'budget_authority',
  confidence: 0.99,
  severity: 'info',
  explanation: 'within delegated authority',
  radius: RADIAL_RADII[0],
  colour: RADIAL_COLOURS[0],
  ...overrides,
});

const verdict: Verdict = {
  count: 0,
  countSuffix: 'findings',
  title: 'intent cleared to send',
  sub: 'governance_context signed',
  pill: 'signed',
  modeNote: null,
};

describe('RadialBody: absent confidence is not zero confidence', () => {
  it('draws a bar for a measured confidence', () => {
    const { container } = render(
      <RadialBody arcs={[arc({ confidence: 0.96 })]} verdict={verdict} revealed />,
    );
    expect(container.querySelectorAll('circle.bar')).toHaveLength(1);
  });

  it('draws NO bar when the finding reported no confidence', () => {
    // A zero-length arc would read as a measured zero-confidence finding, which is a different claim
    // from "this finding carried no confidence", and the reader cannot tell them apart.
    const { container } = render(
      <RadialBody arcs={[arc({ confidence: null })]} verdict={verdict} revealed />,
    );
    expect(container.querySelectorAll('circle.bar')).toHaveLength(0);
    // The track still shows, so the category is visibly present but unmeasured.
    expect(container.querySelectorAll('circle.track')).toHaveLength(1);
  });

  it('says "not reported" rather than 0% in the legend', () => {
    const { container } = render(
      <RadialBody arcs={[arc({ confidence: null })]} verdict={verdict} revealed />,
    );
    const value = container.querySelector('.rv')?.textContent;
    expect(value).toBe('not reported');
    expect(value).not.toContain('0%');
  });

  it('draws a real measured zero as a bar, because zero is a measurement', () => {
    // Absent and zero are different. A finding that genuinely scored 0 must be shown, not hidden.
    const { container } = render(
      <RadialBody arcs={[arc({ confidence: 0 })]} verdict={verdict} revealed />,
    );
    expect(container.querySelectorAll('circle.bar')).toHaveLength(1);
    expect(container.querySelector('.rv')?.textContent).toBe('0%');
  });
});

describe('RadialBody rendering', () => {
  it('renders one track and one legend row per arc', () => {
    const arcs = [
      arc({ categoryId: 'a', radius: RADIAL_RADII[0] }),
      arc({ categoryId: 'b', radius: RADIAL_RADII[1] }),
    ];
    const { container } = render(<RadialBody arcs={arcs} verdict={verdict} revealed />);
    expect(container.querySelectorAll('circle.track')).toHaveLength(2);
    expect(container.querySelectorAll('.rlegend .rl')).toHaveLength(2);
  });

  it('shows the category id as given, without interpreting it', () => {
    // AdCP states category ids are agent-defined labels and must not be pattern-matched.
    const { container } = render(
      <RadialBody arcs={[arc({ categoryId: 'vendor_specific_thing' })]} verdict={verdict} revealed />,
    );
    expect(container.textContent).toContain('vendor_specific_thing');
  });

  it('holds the arcs unlit until revealed', () => {
    const { container } = render(
      <RadialBody arcs={[arc()]} verdict={verdict} revealed={false} />,
    );
    // `getAttribute` rather than `.className`: on an SVG element that is an SVGAnimatedString, and
    // querySelector types it as a plain Element here.
    expect(container.querySelector('circle.bar')?.getAttribute('class') ?? '').not.toContain('on');
    expect(container.querySelector('.verdict')?.className).not.toContain('on');
  });

  it('renders the verdict count and its authored suffix', () => {
    const { container } = render(<RadialBody arcs={[arc()]} verdict={verdict} revealed />);
    expect(container.querySelector('.big')?.textContent).toBe('0');
    expect(container.querySelector('.bigsuf')?.textContent).toBe('findings');
  });
});

describe('RadialBody qualifies the verdict by governance mode', () => {
  it('shows no mode note when the check was enforced', () => {
    const { container } = render(<RadialBody arcs={[arc()]} verdict={verdict} revealed />);
    expect(container.querySelectorAll('.pill')).toHaveLength(1);
  });

  it('shows the mode when it was not enforce', () => {
    // An `approved` from an audit-mode agent means the agent always approves and could not have
    // blocked anything. Presenting that identically to an enforced approval overstates the strongest
    // claim on the screen.
    const { container } = render(
      <RadialBody
        arcs={[arc()]}
        verdict={{ ...verdict, modeNote: 'audit mode' }}
        revealed
      />,
    );
    expect(container.textContent).toContain('audit mode');
    expect(container.querySelectorAll('.pill')).toHaveLength(2);
  });

  it('shows "not reported" as its own state rather than assuming enforce', () => {
    const { container } = render(
      <RadialBody arcs={[arc()]} verdict={{ ...verdict, modeNote: 'mode not reported' }} revealed />,
    );
    expect(container.textContent).toContain('mode not reported');
  });
});
