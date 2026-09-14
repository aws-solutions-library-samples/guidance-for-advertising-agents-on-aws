/**
 * The plan focus control.
 *
 * A plan can be worked on across several conversations, so following the newest session shows only
 * the newest slice of it. This lets the reader focus a plan and see its whole flow merged across
 * sessions, or return to "Live" (follow the newest session), which is also the only mode that shows
 * the pre-plan phases (Discover, Accounts, Bind), since those carry no plan_id.
 *
 * Hidden until at least one plan has been recorded — before then there is nothing to pick, and a
 * control offering only "Live" would be noise.
 */

import type { PlanSummary } from '../../lib/types';

export interface PlanPickerProps {
  readonly plans: readonly PlanSummary[];
  readonly planId: string | null;
  readonly onFocusPlan: (planId: string) => void;
  readonly onClear: () => void;
  readonly error: string | null;
}

export function PlanPicker({ plans, planId, onFocusPlan, onClear, error }: PlanPickerProps) {
  // Nothing to pick and not already focused: render nothing rather than an empty control.
  if (plans.length === 0 && planId === null) return null;

  return (
    <div className="plan-picker" data-testid="plan-picker">
      <span className="plan-picker-label">Plan</span>
      <select
        className="plan-picker-select"
        data-testid="plan-picker-select"
        value={planId ?? ''}
        onChange={(event) => {
          const value = event.target.value;
          if (value === '') onClear();
          else onFocusPlan(value);
        }}
      >
        <option value="">Live · newest session</option>
        {plans.map((plan) => (
          <option key={plan.plan_id} value={plan.plan_id}>
            {plan.plan_id}
            {typeof plan.session_count === 'number' && plan.session_count > 1
              ? ` · ${plan.session_count} sessions`
              : ''}
          </option>
        ))}
      </select>
      {error !== null ? <span className="plan-picker-error">{error}</span> : null}
    </div>
  );
}
