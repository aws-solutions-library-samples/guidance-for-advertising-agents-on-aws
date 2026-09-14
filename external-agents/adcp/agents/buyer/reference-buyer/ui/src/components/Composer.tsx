/**
 * The prompt input.
 *
 * Enter sends, shift-Enter inserts a newline: a brief is often several lines, and losing one to an
 * accidental send is worse than needing a modifier.
 */

import { useEffect, useRef, useState } from 'react';

import { Icon } from './Icon';

export interface ComposerProps {
  disabled: boolean;
  onSend: (prompt: string) => void;
  /** Replaces the input entirely while a recorded session is on screen, which is read-only. */
  readOnlyNote?: string;
}

export function Composer({ disabled, onSend, readOnlyNote }: ComposerProps) {
  const [value, setValue] = useState('');
  const areaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const area = areaRef.current;
    if (!area) return;
    // Grow with the content up to a bound, so a long brief is visible while typing without the
    // input taking over the column.
    area.style.height = 'auto';
    area.style.height = `${Math.min(area.scrollHeight, 180)}px`;
  }, [value]);

  if (readOnlyNote !== undefined) {
    return (
      <div className="flex-none border-t border-line bg-canvas px-5 py-3 text-[13px] text-muted">
        {readOnlyNote}
      </div>
    );
  }

  const submit = () => {
    const prompt = value.trim();
    if (prompt === '' || disabled) return;
    onSend(prompt);
    setValue('');
  };

  return (
    <form
      className="flex flex-none items-end gap-2 border-t border-line bg-surface px-5 py-3"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <textarea
        ref={areaRef}
        rows={1}
        value={value}
        disabled={disabled}
        placeholder="Ask about inventory, pricing, formats…"
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
        className="max-h-[180px] min-h-[40px] flex-1 resize-none rounded-xl border border-line px-3.5 py-2.5 text-[14px] outline-none focus:border-sky disabled:bg-canvas"
      />
      <button
        type="submit"
        disabled={disabled || value.trim() === ''}
        aria-label="Send"
        className="flex h-10 w-10 flex-none items-center justify-center rounded-xl bg-ink text-white disabled:opacity-40"
      >
        <Icon name="send" size={16} />
      </button>
    </form>
  );
}
