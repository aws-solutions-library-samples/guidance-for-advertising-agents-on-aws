/**
 * The prototype's icons, verbatim.
 *
 * Path data copied from `.kiro/specs/journey.html`'s `ICONS` map and its inline check mark. Stroke
 * only, no fills, tinted by the `--fc` custom property the authored CSS sets, exactly as the
 * prototype does.
 *
 * Kept as JSX rather than as HTML strings so nothing has to be injected with `innerHTML`. The shapes
 * are identical; only the syntax differs.
 */

import type { JSX } from 'react';

import type { FormatKey } from '../../lib/journey/phases';

/** One format mark. `viewBox` and stroke behaviour come from the authored `.dot-t svg` rule. */
export function FormatIcon({ format }: { format: FormatKey }): JSX.Element {
  return <svg viewBox="0 0 24 24">{FORMAT_PATHS[format]}</svg>;
}

const FORMAT_PATHS: Record<FormatKey, JSX.Element> = {
  audio: (
    <>
      <path d="M3 14v-3a9 9 0 0 1 18 0v3" />
      <path d="M21 16a2 2 0 0 1-2 2h-1v-5h1a2 2 0 0 1 2 2z" />
      <path d="M3 16a2 2 0 0 0 2 2h1v-5H5a2 2 0 0 0-2 2z" />
    </>
  ),
  podcast: (
    <>
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M6 11a6 6 0 0 0 12 0" />
      <path d="M12 17v4" />
      <path d="M9 21h6" />
    </>
  ),
  display: (
    <>
      <rect x="3" y="4" width="18" height="14" rx="2" />
      <path d="M3 9h18" />
    </>
  ),
  video: (
    <>
      <polygon points="10 8 16 12 10 16 10 8" />
      <rect x="3" y="4" width="18" height="16" rx="2" />
    </>
  ),
  endcard: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M8 9l3 3-3 3" />
      <path d="M13 15h3" />
    </>
  ),
};

/** The check mark the rail and the verdict pill both use. */
export function CheckIcon(): JSX.Element {
  return (
    <span className="ic">
      <svg viewBox="0 0 24 24">
        <polyline points="20 6 9 17 4 12" />
      </svg>
    </span>
  );
}
