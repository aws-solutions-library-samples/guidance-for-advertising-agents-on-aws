/**
 * The plan focus control.
 *
 * Covers what the component itself owns: it hides when there is nothing to pick, lists the plans,
 * and maps the "Live" option back to clearing focus rather than focusing a plan named "".
 */
import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { PlanSummary } from '../../lib/types';
import { PlanPicker } from './PlanPicker';

afterEach(cleanup);

const PLANS: PlanSummary[] = [
  { plan_id: 'plan-alpha', session_count: 2 },
  { plan_id: 'plan-beta', session_count: 1 },
];

describe('PlanPicker', () => {
  it('renders nothing when there are no plans and none is focused', () => {
    const { container } = render(
      <PlanPicker plans={[]} planId={null} onFocusPlan={vi.fn()} onClear={vi.fn()} error={null} />,
    );
    expect(container.querySelector('[data-testid="plan-picker"]')).toBeNull();
  });

  it('lists the plans, noting how many sessions a multi-session plan spans', () => {
    const { getByText, getByTestId } = render(
      <PlanPicker plans={PLANS} planId={null} onFocusPlan={vi.fn()} onClear={vi.fn()} error={null} />,
    );
    expect(getByTestId('plan-picker')).toBeTruthy();
    // The multi-session plan says so; the single-session one does not claim a count.
    expect(getByText('plan-alpha · 2 sessions')).toBeTruthy();
    expect(getByText('plan-beta')).toBeTruthy();
  });

  it('focuses a plan when one is chosen', () => {
    const onFocusPlan = vi.fn();
    const onClear = vi.fn();
    const { getByTestId } = render(
      <PlanPicker plans={PLANS} planId={null} onFocusPlan={onFocusPlan} onClear={onClear} error={null} />,
    );
    fireEvent.change(getByTestId('plan-picker-select'), { target: { value: 'plan-beta' } });
    expect(onFocusPlan).toHaveBeenCalledWith('plan-beta');
    expect(onClear).not.toHaveBeenCalled();
  });

  it('clears focus when "Live" is chosen, rather than focusing an empty plan id', () => {
    const onFocusPlan = vi.fn();
    const onClear = vi.fn();
    const { getByTestId } = render(
      <PlanPicker
        plans={PLANS}
        planId="plan-alpha"
        onFocusPlan={onFocusPlan}
        onClear={onClear}
        error={null}
      />,
    );
    fireEvent.change(getByTestId('plan-picker-select'), { target: { value: '' } });
    expect(onClear).toHaveBeenCalled();
    expect(onFocusPlan).not.toHaveBeenCalled();
  });
});
