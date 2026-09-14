import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { TestBriefs } from './TestBriefs';
import { BRIEF_CATEGORIES, allTestBriefs } from '../lib/testBriefs';

describe('TestBriefs', () => {
  it('offers every brief in the catalogue', () => {
    render(<TestBriefs onPick={vi.fn()} />);
    for (const brief of allTestBriefs()) {
      expect(screen.getByTestId(`test-brief-${brief.id}`)).toBeTruthy();
    }
  });

  it('names both categories, so the reader can tell the two halves apart', () => {
    render(<TestBriefs onPick={vi.fn()} />);
    for (const category of BRIEF_CATEGORIES) {
      expect(screen.getByText(category.title)).toBeTruthy();
    }
  });

  it('sends the full prompt, not the button label', () => {
    // The label is a summary. Sending it would give the agent three words and look like the example
    // itself is broken.
    const onPick = vi.fn();
    render(<TestBriefs onPick={onPick} />);
    const brief = allTestBriefs()[0]!;
    fireEvent.click(screen.getByTestId(`test-brief-${brief.id}`));
    expect(onPick).toHaveBeenCalledWith(brief.prompt);
  });

  it('carries the prompt as the hover title, so a click holds no surprise', () => {
    render(<TestBriefs onPick={vi.fn()} />);
    const brief = allTestBriefs()[0]!;
    expect(screen.getByTestId(`test-brief-${brief.id}`).getAttribute('title')).toBe(brief.prompt);
  });

  it('disables every button when there is nowhere to send a turn', () => {
    const onPick = vi.fn();
    render(<TestBriefs onPick={onPick} disabledReason="No agents are configured." />);
    const brief = allTestBriefs()[0]!;
    const button = screen.getByTestId(`test-brief-${brief.id}`);
    expect(button.hasAttribute('disabled')).toBe(true);
    // And says why, rather than being inert with no explanation.
    expect(button.getAttribute('title')).toBe('No agents are configured.');
    fireEvent.click(button);
    expect(onPick).not.toHaveBeenCalled();
  });
});
