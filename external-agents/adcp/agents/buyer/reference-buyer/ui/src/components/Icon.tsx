/**
 * Stroke-only icons.
 *
 * Per the style guide: single-colour SVG using `stroke="currentColor"`, never a filled shape and
 * never emoji. Each icon is sized by its wrapper's font-size rather than intrinsically, so a bare
 * icon cannot expand to fill its container.
 *
 * The vanilla UI kept these in a 312-line `svg()` function keyed by name. Only the marks this
 * build actually renders are carried over; an unused icon is dead weight in a bundle.
 */

export type IconName =
  | 'network'
  | 'lock'
  | 'users'
  | 'signout'
  | 'send'
  | 'close'
  | 'check'
  | 'chevron-down';

const PATHS: Record<IconName, string> = {
  network:
    '<circle cx="12" cy="5" r="2.5"/><circle cx="5" cy="19" r="2.5"/><circle cx="19" cy="19" r="2.5"/><path d="M12 7.5v4M12 11.5L6.5 17M12 11.5L17.5 17"/>',
  lock: '<rect x="4" y="10" width="16" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
  users:
    '<circle cx="9" cy="8" r="3"/><path d="M3 20a6 6 0 0 1 12 0"/><path d="M16 5.5a3 3 0 0 1 0 5M17 20a6 6 0 0 0-2-4.4"/>',
  signout: '<path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 8l-4 4 4 4M6 12h9"/>',
  send: '<path d="M4 12l16-8-6 8 6 8z"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  check: '<path d="M5 13l4 4L19 7"/>',
  'chevron-down': '<path d="M6 9l6 6 6-6"/>',
};

export interface IconProps {
  name: IconName;
  /** Sets the wrapper's font-size; the glyph is 1em square. */
  size?: number;
  /** A design token, e.g. `var(--color-blue)`. Status icons must be tinted, never left as ink. */
  color?: string;
  className?: string;
  /** Supply when the icon is the only label for a control; omit when adjacent text names it. */
  title?: string;
}

export function Icon({ name, size = 15, color, className, title }: IconProps) {
  return (
    <span
      className={className}
      style={{ fontSize: `${size}px`, ...(color ? { color } : {}), lineHeight: 0 }}
      {...(title ? { role: 'img', 'aria-label': title } : { 'aria-hidden': true })}
    >
      <svg
        width="1em"
        height="1em"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
        dangerouslySetInnerHTML={{ __html: PATHS[name] }}
      />
    </span>
  );
}
