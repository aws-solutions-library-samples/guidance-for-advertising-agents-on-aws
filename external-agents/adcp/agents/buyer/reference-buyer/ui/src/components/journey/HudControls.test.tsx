/**
 * The HUD's job is small but it has one property worth asserting hard: it must not claim to do
 * something it does not do. The prototype's primary button reads "Run the buy"; this view plays back
 * a recorded session and places no buy, so that label is checked for absence rather than presence.
 */

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { HudControls } from './HudControls';

afterEach(cleanup);

function setup(overrides: Partial<Parameters<typeof HudControls>[0]> = {}) {
  const onReplay = vi.fn();
  const onTogglePaused = vi.fn();
  render(
    <HudControls
      hasSession
      paused={false}
      finished
      onReplay={onReplay}
      onTogglePaused={onTogglePaused}
      {...overrides}
    />,
  );
  const replay = screen.getByTestId('journey-hud-replay') as HTMLButtonElement;
  const pause = screen.getByTestId('journey-hud-pause') as HTMLButtonElement;
  return { onReplay, onTogglePaused, replay, pause };
}

describe('HudControls', () => {
  it('uses the authored hud and button classes', () => {
    const { replay, pause } = setup();
    expect(replay.className).toBe('btn');
    // The ghost variant, exactly as the prototype's second button.
    expect(pause.className).toBe('btn ghost');
    expect(replay.closest('.hud')).not.toBeNull();
  });

  it('does not claim to run a buy', () => {
    setup();
    // A false label on a control is worse than a false one on a chip, because a reader can act on it.
    expect(screen.queryByText(/run the buy/i)).toBeNull();
  });

  it('replays on click', () => {
    const { onReplay, replay } = setup();
    fireEvent.click(replay);
    expect(onReplay).toHaveBeenCalledTimes(1);
  });

  it('cannot replay a run that has not finished', () => {
    const { replay } = setup({ finished: false });
    expect(replay.disabled).toBe(true);
  });

  it('cannot replay without a session', () => {
    const { replay } = setup({ hasSession: false, finished: true });
    expect(replay.disabled).toBe(true);
  });

  it('both controls are inert without a session', () => {
    const { replay, pause } = setup({ hasSession: false });
    expect(replay.disabled).toBe(true);
    expect(pause.disabled).toBe(true);
  });

  it('toggles the hold', () => {
    const { onTogglePaused, pause } = setup();
    expect(pause.textContent).toBe('Pause');
    expect(pause.getAttribute('aria-pressed')).toBe('false');
    fireEvent.click(pause);
    expect(onTogglePaused).toHaveBeenCalledTimes(1);
  });

  it('reads Resume while held, so the label states the action and not the state', () => {
    const { pause } = setup({ paused: true });
    expect(pause.textContent).toBe('Resume');
    expect(pause.getAttribute('aria-pressed')).toBe('true');
  });
});
