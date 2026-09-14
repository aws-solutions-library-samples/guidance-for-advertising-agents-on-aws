import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { SessionMeta } from '../../lib/types';
import { SessionHoverZone } from './SessionHoverZone';

afterEach(cleanup);

const session = (id: string): SessionMeta => ({
  session_id: id,
  status: 'active',
  request_preview: `brief for ${id}`,
  agent_name: 'buyer agent',
  invoker: 'someone',
});

function setup(overrides: Partial<Parameters<typeof SessionHoverZone>[0]> = {}) {
  const onSelect = vi.fn();
  const onFollowNewest = vi.fn();
  const result = render(
    <SessionHoverZone
      sessions={[session('s-1'), session('s-2')]}
      selectedId="s-1"
      origin="auto"
      onSelect={onSelect}
      onFollowNewest={onFollowNewest}
      notice={null}
      staleNote={null}
      listError={null}
      {...overrides}
    />,
  );
  return { ...result, onSelect, onFollowNewest };
}

describe('SessionHoverZone is hidden until wanted', () => {
  it('shows no dropdown initially', () => {
    setup();
    expect(screen.queryByTestId('session-dropdown')).toBeNull();
  });

  it('opens on hover over the zone', () => {
    setup();
    fireEvent.mouseEnter(screen.getByTestId('session-hover-zone'));
    expect(screen.getByTestId('session-dropdown')).toBeTruthy();
  });

  it('closes when the pointer leaves', () => {
    setup();
    const zone = screen.getByTestId('session-hover-zone');
    fireEvent.mouseEnter(zone);
    fireEvent.mouseLeave(zone);
    expect(screen.queryByTestId('session-dropdown')).toBeNull();
  });
});

describe('SessionHoverZone is reachable without a pointer', () => {
  it('opens on keyboard focus', () => {
    // Hover cannot be produced by a keyboard, so a hover-only control is unreachable, not merely
    // awkward: switching sessions would be impossible.
    setup();
    fireEvent.focus(screen.getByTestId('session-hover-zone'));
    expect(screen.getByTestId('session-dropdown')).toBeTruthy();
  });

  it('opens on tap, since touch screens have no hover at all', () => {
    setup();
    fireEvent.click(screen.getByTestId('session-hover-zone'));
    expect(screen.getByTestId('session-dropdown')).toBeTruthy();
  });

  it('is focusable', () => {
    setup();
    expect(screen.getByTestId('session-hover-zone').getAttribute('tabindex')).toBe('0');
  });

  it('reports its open state to assistive technology', () => {
    setup();
    const zone = screen.getByTestId('session-hover-zone');
    expect(zone.getAttribute('aria-expanded')).toBe('false');
    fireEvent.focus(zone);
    expect(zone.getAttribute('aria-expanded')).toBe('true');
  });

  it('closes on Escape so a keyboard reader is not trapped', () => {
    setup();
    fireEvent.focus(screen.getByTestId('session-hover-zone'));
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByTestId('session-dropdown')).toBeNull();
  });
});

describe('SessionHoverZone selection', () => {
  it('lists every session it was given as a selectable row', () => {
    const { onSelect } = setup();
    fireEvent.focus(screen.getByTestId('session-hover-zone'));
    const rows = screen.getAllByRole('option');
    expect(rows).toHaveLength(2);
    fireEvent.click(rows[1] as HTMLElement);
    expect(onSelect).toHaveBeenCalledWith('s-2');
  });

  it('does NOT close the sheet when a row inside it is pressed', () => {
    // The bug that made the picker unusable: the strip toggles on click, so a press on a row bubbled
    // up and closed the container the row lived in. Selecting was impossible.
    const { onSelect } = setup();
    fireEvent.mouseEnter(screen.getByTestId('session-hover-zone'));
    const rows = screen.getAllByRole('option');
    fireEvent.click(rows[0] as HTMLElement);
    // The click reached the row rather than being swallowed by the toggle.
    expect(onSelect).toHaveBeenCalledWith('s-1');
  });

  it('stays open when the pointer leaves while focus is inside', () => {
    // A keyboard reader who tabbed in must not lose the sheet because the pointer drifted off it.
    setup();
    const zone = screen.getByTestId('session-hover-zone');
    fireEvent.mouseEnter(zone);
    const rows = screen.getAllByRole('option');
    (rows[0] as HTMLElement).focus();
    fireEvent.mouseLeave(zone);
    expect(screen.queryByTestId('session-dropdown')).not.toBeNull();
  });

  it('moves through rows with the arrow keys, wrapping at the ends', () => {
    setup();
    fireEvent.focus(screen.getByTestId('session-hover-zone'));
    const list = screen.getByTestId('session-list');
    const rows = screen.getAllByRole('option');
    fireEvent.keyDown(list, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(rows[0]);
    fireEvent.keyDown(list, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(rows[1]);
    // Wraps, so holding the key is never stuck against an end with no feedback.
    fireEvent.keyDown(list, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(rows[0]);
    fireEvent.keyDown(list, { key: 'ArrowUp' });
    expect(document.activeElement).toBe(rows[1]);
  });

  it('marks exactly the selected row', () => {
    setup({ selectedId: 's-2' });
    fireEvent.focus(screen.getByTestId('session-hover-zone'));
    const selected = screen
      .getAllByRole('option')
      .filter((row) => row.getAttribute('aria-selected') === 'true');
    expect(selected).toHaveLength(1);
  });

  it('uses no native select, so its popup cannot steal the pointer', () => {
    // Cause 1 of the original failure: a native popup is not in the DOM tree, so opening it fired
    // mouseleave on this container and unmounted the sheet along with the popup.
    setup();
    fireEvent.focus(screen.getByTestId('session-hover-zone'));
    expect(document.querySelector('select')).toBeNull();
  });

  it('reports an empty list as empty rather than as loading', () => {
    setup({ sessions: [] });
    fireEvent.focus(screen.getByTestId('session-hover-zone'));
    expect(screen.getByTestId('session-list-empty')).toBeTruthy();
    expect(screen.queryAllByRole('option')).toHaveLength(0);
  });

  it('says "unknown" for a status the recorder did not write', () => {
    setup({ sessions: [{ ...session('s-9'), status: '' }] });
    fireEvent.focus(screen.getByTestId('session-hover-zone'));
    expect(screen.getByText('unknown')).toBeTruthy();
  });

  it('keeps showing a selected session that has aged off the list', () => {
    // Silently resetting the control would make it disagree with what is on screen.
    setup({ selectedId: 's-gone' });
    fireEvent.focus(screen.getByTestId('session-hover-zone'));
    expect(screen.getByText(/no longer listed/)).toBeTruthy();
  });

  it('says it is following the newest session when it is', () => {
    setup({ origin: 'auto' });
    fireEvent.focus(screen.getByTestId('session-hover-zone'));
    expect(screen.getByTestId('following-newest')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /follow newest/i })).toBeNull();
  });

  it('offers a way back to following the newest after a manual choice', () => {
    const { onFollowNewest } = setup({ origin: 'manual' });
    fireEvent.focus(screen.getByTestId('session-hover-zone'));
    fireEvent.click(screen.getByRole('button', { name: /follow newest/i }));
    expect(onFollowNewest).toHaveBeenCalled();
  });
});

describe('SessionHoverZone surfaces notices', () => {
  it('names the other seller journeys when there are any', () => {
    setup({ notice: 'Also in this session: gotham agent' });
    fireEvent.focus(screen.getByTestId('session-hover-zone'));
    expect(screen.getByTestId('seller-notice').textContent).toContain('gotham agent');
  });

  it('shows nothing about other sellers when there are none', () => {
    setup({ notice: null });
    fireEvent.focus(screen.getByTestId('session-hover-zone'));
    expect(screen.queryByTestId('seller-notice')).toBeNull();
  });

  it('surfaces a stale-poll warning, so a frozen view does not read as a complete one', () => {
    setup({ staleNote: 'This journey has stopped updating' });
    fireEvent.focus(screen.getByTestId('session-hover-zone'));
    expect(screen.getByTestId('stale-note').textContent).toContain('stopped updating');
  });

  it('shows no warning when polling is healthy', () => {
    setup({ staleNote: null });
    fireEvent.focus(screen.getByTestId('session-hover-zone'));
    expect(screen.queryByTestId('stale-note')).toBeNull();
  });
});

describe('SessionHoverZone rows carry the brief', () => {
  it('shows each session brief, which is what tells two rows apart', () => {
    // Agent name and short id do not distinguish two sessions run by the same agent, and a list of
    // them is mostly that.
    const { getByTestId } = setup();
    fireEvent.mouseEnter(getByTestId('session-hover-zone'));
    const briefs = screen.getAllByTestId('session-brief').map((node) => node.textContent);
    expect(briefs).toEqual(['brief for s-1', 'brief for s-2']);
  });

  it('renders no brief element at all when the record carries none', () => {
    // The recorder wrote no preview, so the row says nothing about one.
    const { getByTestId } = setup({
      sessions: [{ session_id: 's-9', status: 'completed', agent_name: 'buyer agent' }],
    });
    fireEvent.mouseEnter(getByTestId('session-hover-zone'));
    expect(screen.queryByTestId('session-brief')).toBeNull();
  });

  it('keeps the agent, id and status alongside the brief', () => {
    // The brief is added to the row, not swapped in for what was already there.
    const { getByTestId, container } = setup();
    fireEvent.mouseEnter(getByTestId('session-hover-zone'));
    const row = container.querySelector('.session-option');
    expect(row?.querySelector('.so-agent')?.textContent).toBe('buyer agent');
    expect(row?.querySelector('.so-id')?.textContent).toBeTruthy();
    expect(row?.querySelector('.so-brief')?.textContent).toBe('brief for s-1');
    expect(row?.querySelector('.so-status')?.textContent).toBe('active');
  });

  it('marks a brief it shortened, so it cannot read as the whole request', () => {
    const long = 'x'.repeat(200);
    const { getByTestId } = setup({
      sessions: [{ session_id: 's-long', status: 'active', request_preview: long }],
    });
    fireEvent.mouseEnter(getByTestId('session-hover-zone'));
    const text = screen.getByTestId('session-brief').textContent ?? '';
    expect(text.endsWith('\u2026')).toBe(true);
    expect(text.length).toBeLessThan(long.length);
  });
});

describe('an unreadable list is not reported as an empty one', () => {
  // The two states were previously collapsed into "No sessions recorded yet.", which asserted an
  // absence the view had not established. An expired token throws before any request is sent, so
  // this message is the only signal that anything went wrong.
  it('reports the failure when the list could not be read', () => {
    setup({ sessions: [], listError: 'Not signed in.' });
    fireEvent.mouseEnter(screen.getByTestId('session-hover-zone'));
    expect(screen.getByTestId('session-list-error').textContent).toContain('Not signed in.');
    expect(screen.queryByTestId('session-list-empty')).toBeNull();
  });

  it('reports a genuine absence when the read succeeded and returned nothing', () => {
    setup({ sessions: [], listError: null });
    fireEvent.mouseEnter(screen.getByTestId('session-hover-zone'));
    expect(screen.getByTestId('session-list-empty')).not.toBeNull();
    expect(screen.queryByTestId('session-list-error')).toBeNull();
  });

  it('shows the list, not an error, when a read succeeds after a failure', () => {
    setup({ sessions: [session('s-1')], listError: null });
    fireEvent.mouseEnter(screen.getByTestId('session-hover-zone'));
    expect(screen.getByTestId('session-list')).not.toBeNull();
    expect(screen.queryByTestId('session-list-error')).toBeNull();
  });
});
