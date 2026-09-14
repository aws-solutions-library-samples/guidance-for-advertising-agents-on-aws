/**
 * The journey view.
 *
 * Composition only: it selects a session, derives every phase's state, sequences the reveals, and
 * hands the results to renderers. It holds no derivation logic of its own, which is what keeps the
 * absence rules in tested pure modules rather than spread across components.
 *
 * Reveals follow REGISTRY order rather than the order data arrived, because the rail is an authored
 * narrative. A seller answering one call before another must not reorder the story.
 */

import { Fragment, useEffect, useMemo, useRef, type CSSProperties } from 'react';

import { useFlowGeometry } from '../../hooks/useFlowGeometry';
import { useJourneyPlanFocus } from '../../hooks/useJourneyPlanFocus';
import { useJourneyPlayback } from '../../hooks/useJourneyPlayback';
import { useJourneySession } from '../../hooks/useJourneySession';
import type { InvokeFn } from '../../hooks/useInvoke';
import { deriveJourney } from '../../lib/journey/derive';
import {
  packFlowItems,
  type FlowItem,
  type FlowItemKind,
} from '../../lib/journey/flowLayout';
import { PHASES, isWidePanel, type PhaseKey } from '../../lib/journey/phases';
import { discoverNote } from '../../lib/journey/productGroups';
import { SCROLL_SETTLE_MS, scrollBehaviourFor, scrollTargetFor } from '../../lib/journey/scroll';
import type { DeriveContext, DiscoverData } from '../../lib/journey/types';
import type { AppConfig } from '../../lib/types';
import { CampaignSummary } from './CampaignSummary';
import { FlowConnectors } from './FlowConnectors';
import { HudControls } from './HudControls';
import { PhasePanel } from './PhasePanel';
import { PlanPicker } from './PlanPicker';
import { Ribbon } from './Ribbon';
import { SellerJourneyNotice } from './SellerJourneyNotice';
import { SessionHoverZone } from './SessionHoverZone';

/**
 * Header copy. Static: it describes the view, not the session, so it is not derived from session data.
 *
 * The h1 was previously assembled per session by `buildHeadline`, which is deleted. The seller is named
 * by the seller notice and the chips instead. Keep the dek to one short line; the panels carry their own
 * titles and task names.
 */
const EYEBROW = 'ADCP SESSIONS';
const HEADLINE = 'AgentCore Buyer Agent';
const DEK = 'Ad Context Protocol workflows, as they run.';

/**
 * Offset the plan-focus generation into its own range so switching between session-follow and a
 * focused plan always changes the generation playback keys on, resetting the reveal cleanly.
 */
const PLAN_GENERATION_OFFSET = 1_000_000;


/**
 * Tools this build can actually call.
 *
 * Drives the difference between "has not happened yet" and "cannot happen in this build", which is
 * the distinction a demonstrator needs when asked why a panel is dark.
 *
 * These are the buyer agent's **registered tool names**, taken from `adcp_tools.py`, not AdCP task
 * names. Every entry here was previously wrong — written from the protocol's task list, so
 * `get_products` instead of `adcp_get_products` — and the effect was that no phase ever matched its
 * data and the whole flow stayed empty while looking merely unfinished. `phases.tools.test.ts` reads
 * the agent's real registrations and fails if this list drifts from them.
 */
const CALLABLE_TOOLS = [
  // Order here is presentational only -- this is a capability SET, and `classify` does a membership
  // test against it. It does not imply a call sequence; see `phases.ts` for why the rail leads with
  // Discover rather than with the setup tasks.
  'adcp_sync_accounts',
  'adcp_sync_governance',
  'adcp_sync_plans',
  'adcp_check_governance',
  'adcp_get_products',
  'adcp_get_capabilities',
  'adcp_list_creative_formats',
  'adcp_get_signals',
  'adcp_create_media_buy',
  'adcp_report_plan_outcome',
  'adcp_get_plan_audit_logs',
  'adcp_get_media_buy_delivery',
  'adcp_get_media_buys',
  // U4/Part 1: `get_creative_features` is a governance-agent TASK (the creative evaluator), but this
  // buyer registers its OWN wrapper tool for it (adcp_get_creative_features in adcp_tools.py), same
  // as it does for check_governance/report_plan_outcome -- calling a governance-agent task always
  // goes through a buyer-side tool, never directly. So it belongs in this build's own capability
  // set. An earlier comment here said the opposite; that was true before this unit added the tool.
  'adcp_get_creative_features',
] as const;

export function JourneyView({
  invoke,
  config,
  enabled,
}: {
  invoke: InvokeFn;
  config: AppConfig | null;
  enabled: boolean;
}) {
  const session = useJourneySession(invoke, enabled);
  const plan = useJourneyPlanFocus(invoke, enabled);
  const summaryRef = useRef<HTMLDivElement | null>(null);
  const flowRef = useRef<HTMLDivElement | null>(null);

  // When a plan is focused, the journey derives from that plan's steps merged across every session it
  // was worked on in; otherwise it follows the newest session — which is also the only mode that can
  // show the pre-plan phases (Discover, Accounts, Bind), since those carry no plan_id. A distinct
  // generation range per mode makes playback reset cleanly when the reader switches modes.
  const focusingPlan = plan.planId !== null;
  const focusSteps = focusingPlan ? plan.steps : session.steps;
  const focusMeta = focusingPlan ? plan.meta : session.meta;
  const focusStatus = focusingPlan ? plan.status : session.status;
  const focusGeneration = focusingPlan ? PLAN_GENERATION_OFFSET + plan.generation : session.generation;

  const context = useMemo<DeriveContext>(
    () => ({
      capabilities: { callableTools: [...CALLABLE_TOOLS] },
      sellerPriority: config?.seller_priority ?? [],
    }),
    [config],
  );

  // Full re-derivation on every step change. Incremental derivation would need its own applied-step
  // bookkeeping, a second high-water mark with a second chance to be wrong, and this fits inside one
  // frame for a session's worth of steps.
  const journey = useMemo(
    () => deriveJourney(focusSteps, focusMeta, context),
    [focusSteps, focusMeta, context],
  );

  const playback = useJourneyPlayback(journey.states, focusMeta?.status, focusGeneration);

  /**
   * The flow's children, described in DOM order so the serpentine can be computed over them.
   *
   * It has to be the same order and the same count as what renders, which is why the indices are built
   * here rather than counted during the render below: a `.map` that returns `null` for a phase with no
   * state does not advance a counter, and an index that drifts by one places every card in the wrong
   * cell.
   */
  const flowLayout = useMemo(() => {
    const phases = PHASES.filter((phase) => journey.states.has(phase.key));
    const kinds: FlowItemKind[] = [];
    const cellIndexByPhase = new Map<PhaseKey, number>();
    let noticeIndex = -1;

    // Prose about the run, not a card in it.
    const statusIndex = focusStatus !== null ? kinds.push('wide') - 1 : -1;

    for (const phase of phases) {
      // Rendered inside Discover's fragment, and given its own full row so it stays directly above the
      // panel it summarises rather than landing in a column beside an unrelated phase.
      if (phase.key === 'discover' && journey.otherSellers.length > 0) {
        noticeIndex = kinds.push('wide') - 1;
      }
      cellIndexByPhase.set(phase.key, kinds.push(isWidePanel(phase.type) ? 'wide' : 'card') - 1);
    }

    return { phases, kinds, statusIndex, noticeIndex, cellIndexByPhase };
  }, [journey.states, journey.otherSellers.length, focusStatus]);

  /**
   * Changes whenever the set, order or width of cells changes, so geometry is re-measured. Reveal count
   * is included because a body revealing changes its card's height and therefore every position below
   * it — the ResizeObserver catches that too, but only after a frame.
   */
  const flowSignature = `${flowLayout.kinds.join('')}|${playback.revealed.size}`;
  const geometry = useFlowGeometry(flowRef, flowSignature);

  /**
   * Packed into columns from the measured heights.
   *
   * Heights are an INPUT to placement here, which is why the geometry hook measures them. There is no
   * loop: a card's height comes from its content and its column's width, and placement changes only where
   * it sits vertically — so measuring after placing yields the same number.
   */
  const placements = useMemo(() => {
    const items: FlowItem[] = flowLayout.kinds.map((kind, index) => {
      const height = geometry.heights[index];
      return height === undefined ? { kind } : { kind, height };
    });
    return packFlowItems(items, geometry.columns);
  }, [flowLayout.kinds, geometry.heights, geometry.columns]);

  /**
   * Explicit placement, because masonry cannot be expressed as auto-flow.
   *
   * The row values are 1px units — see `flowLayout.ts` — so `grid-row: 341 / span 320` means "start 340px
   * down this column and be 320px tall". `.flow` sets `grid-auto-rows: 1px` and `row-gap: 0` to match.
   */
  const cellStyle = (index: number): CSSProperties => {
    const placement = placements[index];
    if (!placement) return {};
    return {
      gridColumn: `${placement.column} / span ${placement.span}`,
      gridRow: `${placement.rowStart} / span ${placement.rowSpan}`,
    };
  };

  const phaseColours = useMemo(
    () => new Map(PHASES.map((phase) => [phase.key as string, phase.col])),
    [],
  );

  // Bring the active phase up under the sticky summary, in BOTH modes and animated in both, which is
  // what the prototype does — see lib/journey/scroll.ts for why this is not conditional on Replay.
  //
  // The timeout is the coalescing window, not a delay for its own sake: the cleanup cancels a pending
  // scroll whenever the active phase changes again, so a catch-up burst produces one scroll to the
  // phase it ended on instead of a queue of animations chasing each other down the page. Deliberately
  // NOT keyed on playback.walking: the destination and the animation are the same either way, and
  // depending on it would re-fire the scroll when a Replay merely started or stopped.
  useEffect(() => {
    const active = playback.active;
    if (active === null) return;
    if (typeof window === 'undefined') return;
    const id = window.setTimeout(() => {
      // The wrapper, not the `.panel` inside it: a panel being revealed carries a translateY that has
      // not finished transitioning, and measuring it would aim 26px below where it settles.
      const wrapper = document.querySelector<HTMLElement>(`[data-phase="${active}"]`);
      if (!wrapper) return;
      const reduced =
        typeof window.matchMedia === 'function' &&
        window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      window.scrollTo({
        top: scrollTargetFor({
          panelTop: wrapper.getBoundingClientRect().top,
          scrollY: window.scrollY,
          // Measured now, never remembered: the summary grows as entity chips arrive.
          summaryHeight: summaryRef.current?.getBoundingClientRect().height ?? 0,
        }),
        behavior: scrollBehaviourFor(reduced),
      });
    }, SCROLL_SETTLE_MS);
    return () => window.clearTimeout(id);
  }, [playback.active]);

  const discoverState = journey.states.get('discover');
  const note =
    discoverState?.state === 'ready' ? discoverNote(discoverState.data as DiscoverData) : '';


  return (
    <div className="journey-root">
      <Ribbon glow={playback.glow} />
      <SessionHoverZone
        sessions={session.sessions}
        selectedId={session.sessionId}
        origin={session.origin}
        // Picking or following a session exits plan focus: the two are alternative lenses on the same
        // store, and leaving a plan focused while a session is chosen would show neither clearly.
        onSelect={(id) => {
          plan.clearPlan();
          session.selectSession(id);
        }}
        onFollowNewest={() => {
          plan.clearPlan();
          session.followNewest();
        }}
        notice={journey.othersNotice}
        staleNote={session.staleNote}
        listError={session.listError}
      />
      <div className="stage">
        <CampaignSummary
          ref={summaryRef}
          eyebrow={EYEBROW}
          headline={HEADLINE}
          dek={DEK}
          chips={journey.chips}
          rail={playback.rail}
        />
        <PlanPicker
          plans={plan.plans}
          planId={plan.planId}
          onFocusPlan={plan.focusPlan}
          onClear={plan.clearPlan}
          error={plan.listError}
        />
        <div className="flow" ref={flowRef}>
          {/* First child, and out of flow via `position: absolute`, so it is not a grid item and does
              not shift the sibling indices the notice-adjacency tests read. */}
          <FlowConnectors
            keys={flowLayout.phases.map((phase) => phase.key)}
            revealed={playback.revealed as ReadonlySet<string>}
            rects={geometry.rects}
            colours={phaseColours}
          />
          {/* `data-flow-cell` marks a packed child. Measurement reads these in DOM order, which is the
              order `flowLayout.kinds` describes them in — that correspondence is what keeps heights
              aligned with items. */}
          {focusStatus !== null ? (
            <div className="dg-note" data-flow-cell style={cellStyle(flowLayout.statusIndex)}>
              {focusStatus}
            </div>
          ) : null}
          {PHASES.map((phase) => {
            const state = journey.states.get(phase.key);
            if (!state) return null;
            return (
              <Fragment key={phase.key}>
                {/* What the other sellers returned, immediately above the panel it describes.
                    It used to sit at the top of the flow, detached from every panel, where a line
                    about product counts read as a summary of the whole session or of whichever panel
                    happened to follow it. It summarises neither: it comes from the discovery fan-out
                    and belongs to Discover.
                    Since Discover now leads the rail, this is once again near the top of the flow —
                    but ATTACHED to Discover, inside its Fragment, which is the property that prevents
                    the misreading. Adjacency is what matters here, not absolute position.
                    Gated on Discover being present, which is also when `otherSellers` can be
                    non-empty, since both are derived from the same fan-out. */}
                {phase.key === 'discover' ? (
                  <SellerJourneyNotice
                    others={journey.otherSellers}
                    packed
                    style={cellStyle(flowLayout.noticeIndex)}
                  />
                ) : null}
                {/* Still a DIRECT child of `.flow`, and deliberately so: the flow is a grid, and a
                    wrapper row per pair would both break the notice's adjacency to Discover and stop
                    the grid from reflowing cards as the viewport changes. Placement is a style on the
                    cell, not a change to the tree. */}
                <div
                  data-phase={phase.key}
                  data-flow-cell
                  className={isWidePanel(phase.type) ? 'flow-cell flow-cell-wide' : 'flow-cell'}
                  style={cellStyle(flowLayout.cellIndexByPhase.get(phase.key) ?? -1)}
                >
                  <PhasePanel
                    phase={phase}
                    state={state}
                    revealed={playback.revealed.has(phase.key)}
                    active={playback.active === phase.key}
                    {...(phase.key === 'discover' ? { note } : {})}
                  />
                </div>
              </Fragment>
            );
          })}
        </div>
      </div>
      <HudControls
        hasSession={session.sessionId !== null || focusingPlan}
        paused={playback.paused}
        finished={playback.finished}
        onReplay={playback.replay}
        onTogglePaused={playback.togglePaused}
      />
    </div>
  );
}
