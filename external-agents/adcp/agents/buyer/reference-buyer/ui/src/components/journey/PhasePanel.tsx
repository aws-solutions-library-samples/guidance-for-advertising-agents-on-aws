/**
 * One phase's panel: the authored chrome, plus a body dispatched by panel type.
 *
 * Two things this component is responsible for.
 *
 * First, EXHAUSTIVE state handling. `PhaseState` has four variants and the switch below covers all
 * four with a `never`-typed fallthrough, so adding a fifth becomes a compile error here rather than
 * silently falling through to whichever branch happened to be last. That matters because the person
 * this screen is for cannot ask what a panel means.
 *
 * Second, rendering NOTHING for a phase without data. The panel keeps its authored `.pending`
 * appearance and no body is produced. The reason is available on demand instead, so a demonstrator can
 * tell "not built yet" from "the agent is unreachable" without the panel itself changing.
 */

import { useState } from 'react';

import { describeReason } from '../../lib/journey/absence';
import { displayField } from '../../lib/journey/conditions';
import type { PhaseDefinition } from '../../lib/journey/phases';
import {
  assertNever,
  type AccountStripData,
  type AnyPhaseState,
  type BindData,
  type CardRow,
  type ConditionSet,
  type DiscoverData,
  type GovernanceCheckData,
  type MediaBuyStripData,
  type Meter,
  type PlanData,
} from '../../lib/journey/types';
import { AccountStripBody } from './bodies/AccountStripBody';
import { BindFlowBody } from './bodies/BindFlowBody';
import { CardBody } from './bodies/CardBody';
import { FormatGridBody } from './bodies/FormatGridBody';
import { MediaBuyStripBody } from './bodies/MediaBuyStripBody';
import { MetersBody } from './bodies/MetersBody';
import { PlanVizBody } from './bodies/PlanVizBody';
import { RadialBody, type Verdict } from './bodies/RadialBody';
import { ScanBody, type CreativeCard } from './bodies/ScanBody';

export interface PhasePanelProps {
  readonly phase: PhaseDefinition;
  readonly state: AnyPhaseState;
  readonly revealed: boolean;
  readonly active: boolean;
  /** The note under the dot grid, when this phase is Discover and has data. */
  readonly note?: string;
}

export function PhasePanel({ phase, state, revealed, active, note }: PhasePanelProps) {
  const [reasonShown, setReasonShown] = useState(false);

  const classes = ['panel'];
  if (!revealed) classes.push('pending');
  if (active) classes.push('awake');

  return (
    <div className={classes.join(' ')} style={{ ['--rc' as string]: phase.col }}>
      <div className="phead">
        <span className="pnum">{phase.n}</span>
        <span className="panel-t">{phase.title}</span>
        <span className="panel-role">
          {phase.party} / <span className="op">{phase.op}</span>
        </span>
      </div>
      <div className="panel-s">{phase.sub}</div>
      {renderBody()}
    </div>
  );

  function renderBody() {
    switch (state.state) {
      case 'ready':
        return renderReady();
      case 'not_reached':
      case 'not_implemented':
      case 'unreachable':
        return renderAbsent();
      default:
        return assertNever(state);
    }
  }

  function renderReady() {
    if (state.state !== 'ready') return null;
    switch (phase.type) {
      case 'fmtgrid':
        return (
          <FormatGridBody
            data={state.data as DiscoverData}
            note={note ?? ''}
            revealed={revealed}
          />
        );
      case 'bindflow':
        return <BindFlowBody data={state.data as BindData} revealed={revealed} />;
      case 'planviz':
        return <PlanVizBody data={state.data as PlanData} revealed={revealed} />;
      case 'card':
        // Report's panel type. Every row came from an agent's real response, and the deriver emits
        // no row for a field nobody supplied, so there is nothing here to guard against an empty
        // value. Bind, Plan and Book used this and now have their own bodies.
        return <CardBody rows={state.data as readonly CardRow[]} revealed={revealed} />;
      case 'accounts':
        // Accounts' panel type: one card per seller that declared the account, as a highlighted
        // strip. The shared account key is in the header; a failed reply renders its own error
        // state rather than the fields a held account would carry (enforced in the decoder).
        return <AccountStripBody data={state.data as AccountStripData} revealed={revealed} />;
      case 'mediabuy':
        // Book's panel type: the media buy's packages as a strip. The body itself only renders a
        // field a seller supplied (S22-style honesty is enforced per-card there), and shows a
        // refused/in-flight buy as its own distinct card state rather than as a booked one.
        return (
          <MediaBuyStripBody data={state.data as MediaBuyStripData} revealed={revealed} />
        );
      case 'radial':
        return renderRadial(state.data as GovernanceCheckData);
      case 'scan':
        // Creative's panel type (U4). ScanBody itself enforces "no scan animation without a real
        // evaluator verdict" (S22) — see that component's own header comment.
        return (
          <ScanBody cards={state.data as readonly CreativeCard[]} revealed={revealed} />
        );
      case 'meters':
        // Activate's panel type (U4). A meter this build cannot source stays unfilled — see
        // MetersBody's own header comment (fill rate has no honest denominator anywhere in AdCP).
        return <MetersBody meters={state.data as readonly Meter[]} revealed={revealed} />;
      default:
        return assertNever(phase.type);
    }
  }

  /**
   * The Govern / Score radial. `awaitingHumanReview` renders a distinct pending state (BR-U2-23)
   * rather than any verdict, since `submitted`/`working` means a human has not yet decided.
   */
  function renderRadial(data: GovernanceCheckData) {
    if (data.awaitingHumanReview) {
      const note = data.humanReview;
      return (
        <div className="panel-s" data-testid="govern-awaiting-review">
          <div>Awaiting human review.</div>
          {/* Each line appears only when the agent actually reported it. A hardcoded cause would be
              the one fabrication that matters on this panel: it would tell a buyer why its plan is
              frozen, and be wrong. */}
          {note?.reason !== null && note?.reason !== undefined ? (
            <div data-testid="govern-review-reason" style={{ opacity: 0.75 }}>
              {note.reason}
            </div>
          ) : null}
          {note?.suggestion !== null && note?.suggestion !== undefined ? (
            <div data-testid="govern-review-suggestion" style={{ opacity: 0.75 }}>
              {note.suggestion}
            </div>
          ) : null}
        </div>
      );
    }
    const verdict: Verdict = data.verdict;
    return (
      <>
        <RadialBody arcs={data.arcs} verdict={verdict} revealed={revealed} />
        {/* A resolved escalation, shown alongside the verdict rather than instead of it: by this point
            the verdict IS the human's decision, and this is what says a human made it. Without it, a
            reviewed plan is indistinguishable from one that was never escalated. */}
        {data.humanReview?.resolution !== null && data.humanReview?.resolution !== undefined ? (
          <div className="panel-s" data-testid="govern-review-resolution" style={{ opacity: 0.75 }}>
            {`Human review: ${data.humanReview.resolution}`}
            {data.humanReview.resolvedAt !== null ? ` (${data.humanReview.resolvedAt})` : ''}
          </div>
        ) : null}
        {renderConditions(data.conditions)}
      </>
    );
  }

  /**
   * The `conditions[]` a `conditions` verdict came with.
   *
   * Null renders nothing at all -- an agent that supplied no array said nothing about conditions, and
   * "no conditions" would assert it checked and found none. An empty array renders nothing either, for
   * the same reason a zero-length list is not worth a heading.
   *
   * Each row says who has to act, which is the only classification AdCP actually supports here:
   * `required_value` present means a value the caller can apply, absent means someone has to read the
   * reason and judge. A `planned_delivery.` condition is never buyer-actionable however completely it is
   * specified -- the field belongs to the seller.
   */
  function renderConditions(conditions: ConditionSet | null) {
    if (conditions === null || conditions.items.length === 0) return null;
    return (
      <div className="panel-s" data-testid="govern-conditions">
        {conditions.items.map((item, index) => {
          const field = displayField(item);
          return (
            <div
              key={`${field ?? 'condition'}-${index}`}
              data-testid={
                item.buyerActionable ? 'govern-condition-actionable' : 'govern-condition-judgement'
              }
              style={{ opacity: 0.75 }}
            >
              {field !== null ? `${field}: ` : ''}
              {item.reason ?? ''}
              {item.hasRequiredValue ? ` (set to ${String(item.requiredValue)})` : ''}
              {item.scope === 'delivery' ? ' — seller-side' : ''}
            </div>
          );
        })}
      </div>
    );
  }

  /**
   * A phase with no data.
   *
   * No body, so the panel looks exactly as the prototype's `.pending` state does. The reason sits
   * behind a control rather than on the panel, which keeps the authored appearance untouched while
   * still making the three absence states distinguishable.
   */
  function renderAbsent() {
    return (
      <div className="panel-s">
        <button
          type="button"
          onClick={() => setReasonShown((shown) => !shown)}
          aria-expanded={reasonShown}
          style={{
            background: 'none',
            border: 'none',
            padding: 0,
            font: 'inherit',
            color: 'inherit',
            opacity: 0.65,
            cursor: 'pointer',
            textDecoration: 'underline',
          }}
        >
          {reasonShown ? 'hide status' : 'why is this empty?'}
        </button>
        {reasonShown ? <div className="dg-note">{describeReason(phase, state)}</div> : null}
      </div>
    );
  }
}
