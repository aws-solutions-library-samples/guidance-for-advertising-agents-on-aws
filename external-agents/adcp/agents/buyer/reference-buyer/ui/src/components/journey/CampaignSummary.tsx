/**
 * The sticky campaign summary: headline, brief chips, and the step rail.
 *
 * One load-bearing detail carried from the prototype: this element GROWS as entity chips arrive, and
 * anything scrolling a panel into view underneath it must re-measure its height on every call. The
 * prototype comments this; a cached height drifts the scroll target as chips accumulate. The ref is
 * exposed for exactly that.
 */

import { forwardRef } from 'react';

import awsLogo from '../../styles/AWS_logo_RGB_1c_White.svg';
import type { RailStep } from '../../hooks/useJourneyPlayback';
import type { SummaryChip } from '../../lib/journey/types';
import { PhaseRail } from './PhaseRail';

export interface CampaignSummaryProps {
  readonly eyebrow: string;
  readonly headline: string;
  readonly dek: string;
  readonly chips: readonly SummaryChip[];
  readonly rail: readonly RailStep[];
}

export const CampaignSummary = forwardRef<HTMLDivElement, CampaignSummaryProps>(
  function CampaignSummary({ eyebrow, headline, dek, chips, rail }, ref) {
    return (
      <div className="summary" ref={ref}>
        {/* The AWS mark sits beside the headline rather than above it, so the row reads as one
            attribution of the agent named in the sentence. `<img>` with the SVG as a URL rather than an
            inlined component: it is a trademark asset, and keeping it a file means it is replaced by
            swapping the file rather than by editing JSX. Marked decorative, because "AgentCore Buyer
            Agent" in the adjacent h1 already carries the same information to a screen reader, and a
            second reading of it would be noise. */}
        <div className="summary-head">
          <img className="summary-logo" src={awsLogo} alt="" aria-hidden="true" />
          {/* Eyebrow, headline and dek are one column beside the mark, so all three share the same left
              edge. The dek sits INSIDE this column rather than below the row: outside it, it started at
              the mark's left edge and the header read as two unrelated blocks. */}
          <div className="summary-head-text">
            <div className="eyebrow">{eyebrow}</div>
            <h1>{headline}</h1>
            <p className="dek">{dek}</p>
          </div>
        </div>
        <div className="brief">
          {chips.map((chip) => (
            <span
              className={`bchip${chip.kind === 'entity' ? ' entity on' : ''}`}
              key={chip.key}
              // Provenance is not displayed but is inspectable, because the likeliest bug here is
              // a chip showing a correct value from the wrong source, which no assertion on the
              // value itself would catch.
              data-source={chip.source}
            >
              <b>{chip.label}</b>{' '}
              {chip.live === true ? <span className="live-dot" /> : null}
              {chip.value}
            </span>
          ))}
        </div>
        <PhaseRail steps={rail} />
      </div>
    );
  },
);
