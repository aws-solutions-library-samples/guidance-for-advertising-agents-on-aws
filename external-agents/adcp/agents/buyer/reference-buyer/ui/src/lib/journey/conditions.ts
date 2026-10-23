/**
 * `check_governance` conditions, classified by whether the buyer can act on them.
 *
 * A `conditions` verdict is not a denial and not an approval: it says the action would be permitted with
 * changes. The useful question for a reader is therefore **who has to do something**, and AdCP answers it
 * with a field presence rather than a flag:
 *
 *   - `required_value` PRESENT  -> the agent has named the value that would pass. A buyer, or its
 *     orchestrator, can apply it and re-call. Machine-actionable.
 *   - `required_value` ABSENT   -> the agent has stated a problem without a mechanical fix. Someone has
 *     to read `reason` and decide. Human judgement.
 *
 * That distinction is AdCP's, not this file's, and it is the difference between a caller that can
 * self-correct and one that needs a person. Inventing a `required_value` where the agent gave none would
 * turn "a human must judge this" into "set this field to X", which is the more dangerous of the two
 * mistakes available here.
 *
 * ## Routing: which panel a condition belongs to
 *
 * `field` is a dot-path, and its ROOT says which object the path is relative to:
 *
 *   | `field` starts with   | Relative to        | Panel  | Buyer-actionable |
 *   |-----------------------|--------------------|--------|------------------|
 *   | `payload.`            | the proposed action| Govern | yes              |
 *   | `planned_delivery.`   | the seller's plan  | Score  | **no**           |
 *   | anything else         | the proposed action| Govern | yes              |
 *
 * The `planned_delivery.` case is the one worth stating. Those conditions are about what the SELLER
 * intends to deliver, so the buyer cannot satisfy them by changing its own request -- presenting one as
 * buyer-actionable would send a buyer to edit a field it does not own, and it would keep failing.
 *
 * A bare path with no recognised root (`budget.total`) routes to Govern. That is the reference governance
 * agent's own emission: the spec defines `field` as a path *within* payload or planned_delivery, so the
 * prefix is optional and its absence means the default object, not an unknown one.
 */
import type { ConditionItem, ConditionSet } from './types';

/* eslint-disable @typescript-eslint/no-unnecessary-condition -- the runtime shapes here come off the
   wire, so the parameter types describe what we hope for, not what may arrive. */

type Rec = Record<string, unknown>;

function isRec(value: unknown): value is Rec {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function str(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null;
}

/** The prefix that means "this is about the seller's delivery, not the buyer's request". */
const PLANNED_DELIVERY_PREFIX = 'planned_delivery.';
const PAYLOAD_PREFIX = 'payload.';

/**
 * Parse a `conditions[]` array into classified items.
 *
 * Returns null when the array is **absent**, and an empty set when it is present but empty. The
 * difference is load-bearing for the caller: absent means the agent said nothing about conditions, and a
 * `conditions` verdict with no array must render as the verdict alone rather than as "no conditions" --
 * which would assert that the agent checked and found none.
 */
export function parseConditions(response: Rec | null | undefined): ConditionSet | null {
  if (!isRec(response)) return null;
  const raw = response['conditions'];
  if (!Array.isArray(raw)) return null;

  const items: ConditionItem[] = [];
  for (const entry of raw) {
    if (!isRec(entry)) continue;
    const item = parseCondition(entry);
    if (item !== null) items.push(item);
  }
  return {
    items,
    // Counted rather than recomputed by the caller, so a panel heading cannot disagree with its own list.
    buyerActionableCount: items.filter((item) => item.buyerActionable).length,
  };
}

function parseCondition(entry: Rec): ConditionItem | null {
  // `field_name` is the reference governance agent's wire key for this; `field` is the schema's. Both
  // accepted rather than guessing which one a given agent emits.
  const field = str(entry['field']) ?? str(entry['field_name']);
  const reason = str(entry['reason']);
  // A condition with neither a field nor a reason says nothing a reader could act on or understand, so
  // it is dropped rather than rendered as an empty row.
  if (field === null && reason === null) return null;

  // Presence, not truthiness. `required_value: 0` is a real required value -- a falsy check here would
  // silently reclassify "set this to zero" as "a human must decide", which is the exact absent-vs-zero
  // confusion this project's display rules exist to prevent.
  const hasRequiredValue = 'required_value' in entry && entry['required_value'] !== null;

  const scope = field !== null && field.startsWith(PLANNED_DELIVERY_PREFIX) ? 'delivery' : 'action';

  return {
    field,
    reason,
    requiredValue: hasRequiredValue ? entry['required_value'] : null,
    hasRequiredValue,
    scope,
    // A delivery-scoped condition is never buyer-actionable, however completely the agent specified it:
    // the buyer does not own the field. `required_value` tells the SELLER what to change.
    buyerActionable: scope === 'action' && hasRequiredValue,
    panel: scope === 'delivery' ? 'score' : 'govern',
  };
}

/**
 * The path with its root prefix removed, for display.
 *
 * `payload.budget.total` and `budget.total` name the same field; showing the prefix on one and not the
 * other would read as two different fields. The prefix survives in `scope`, which is where it matters.
 */
export function displayField(item: ConditionItem): string | null {
  if (item.field === null) return null;
  if (item.field.startsWith(PAYLOAD_PREFIX)) return item.field.slice(PAYLOAD_PREFIX.length);
  if (item.field.startsWith(PLANNED_DELIVERY_PREFIX)) {
    return item.field.slice(PLANNED_DELIVERY_PREFIX.length);
  }
  return item.field;
}
