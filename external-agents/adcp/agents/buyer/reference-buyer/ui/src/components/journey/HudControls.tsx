/**
 * The sticky control set at the foot of the flow.
 *
 * The prototype's `.hud` holds two buttons: a primary "Run the buy" that starts the scripted
 * sequence, and a ghost "Replay" that is disabled until a run has finished. The styling here is the
 * authored `.hud`, `.btn`, `.btn.ghost` and `.btn:disabled` unchanged — those rules were ported with
 * the rest of the CSS and only this markup was missing.
 *
 * The LABELS differ from the prototype's, deliberately, and the reason is the same rule that governs
 * every figure on this screen. This view plays back a recorded session; it does not place a buy. A
 * button reading "Run the buy" would state that pressing it buys media, which is false, and a false
 * label on a control is worse than a false one on a chip because a reader can act on it.
 *
 * So the pair maps to what the controls really do:
 *
 *   primary  Replay      re-run the reveal sequence for the session already loaded
 *   ghost    Pause/Play  hold the sequence so a panel can be talked about, then release it
 *
 * Pause replaces "Run the buy" because our sequence starts on its own — the booth demo is driven
 * from another system and has to react without anyone pressing anything. Once playback is automatic,
 * the control that is actually missing is the one that stops it.
 */

export interface HudControlsProps {
  /** True when there is a session loaded to play. Both controls are inert without one. */
  readonly hasSession: boolean;
  readonly paused: boolean;
  /** Every phase has been revealed, so there is a complete run to replay. */
  readonly finished: boolean;
  readonly onReplay: () => void;
  readonly onTogglePaused: () => void;
}

export function HudControls({
  hasSession,
  paused,
  finished,
  onReplay,
  onTogglePaused,
}: HudControlsProps) {
  // Mirrors the prototype's own `disabled` semantics on its Replay button: there is nothing to
  // replay until a run has been watched through.
  const replayDisabled = !hasSession || !finished;

  return (
    <div className="hud">
      <button
        className="btn"
        type="button"
        onClick={onReplay}
        disabled={replayDisabled}
        data-testid="journey-hud-replay"
      >
        Replay
      </button>
      <button
        className="btn ghost"
        type="button"
        onClick={onTogglePaused}
        disabled={!hasSession}
        aria-pressed={paused}
        data-testid="journey-hud-pause"
      >
        {paused ? 'Resume' : 'Pause'}
      </button>
    </div>
  );
}
