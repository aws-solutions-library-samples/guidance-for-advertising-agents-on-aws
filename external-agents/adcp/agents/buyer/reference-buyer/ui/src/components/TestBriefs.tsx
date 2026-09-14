/**
 * Example briefs, grouped by what they exercise, shown in the chat's empty state.
 *
 * Clicking one sends it as an ordinary turn. Nothing is scripted or replayed: the prompt goes to the
 * agent exactly as a typed one would, and whatever comes back is the real answer. That matters here
 * more than usual, because a grid of neat buttons is the kind of surface that reads as a canned demo.
 *
 * Disabled as a set when there is nothing to send to, so a button cannot look available while the
 * composer beside it says no agent is configured.
 */

import { BRIEF_CATEGORIES, type TestBrief } from '../lib/testBriefs';

export interface TestBriefsProps {
  readonly onPick: (prompt: string) => void;
  /** Set when turns cannot be sent, e.g. no agent configured. Explains itself on hover. */
  readonly disabledReason?: string;
}

export function TestBriefs({ onPick, disabledReason }: TestBriefsProps) {
  const disabled = disabledReason !== undefined;

  return (
    <div className="mt-6" data-testid="test-briefs">
      {BRIEF_CATEGORIES.map((category) => (
        <section key={category.id} className="mt-5 first:mt-0">
          <h3 className="m-0 text-[13px] font-semibold tracking-wide text-ink-2 uppercase">
            {category.title}
          </h3>
          <p className="mt-1 mb-2 text-[13px] leading-relaxed text-muted">{category.blurb}</p>
          <div className="flex flex-wrap gap-2">
            {category.briefs.map((brief) => (
              <BriefButton
                key={brief.id}
                brief={brief}
                disabled={disabled}
                {...(disabledReason !== undefined ? { title: disabledReason } : {})}
                onPick={onPick}
              />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function BriefButton({
  brief,
  disabled,
  title,
  onPick,
}: {
  brief: TestBrief;
  disabled: boolean;
  title?: string;
  onPick: (prompt: string) => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => onPick(brief.prompt)}
      data-testid={`test-brief-${brief.id}`}
      // The full prompt on hover: the label is a summary, and sending something other than what the
      // reader expected is worse than a long tooltip.
      title={title ?? brief.prompt}
      className="rounded-full border border-line bg-surface px-3 py-1.5 text-[13px] text-ink-2 transition-colors hover:border-sky hover:text-ink disabled:cursor-not-allowed disabled:opacity-50"
    >
      {brief.label}
    </button>
  );
}
