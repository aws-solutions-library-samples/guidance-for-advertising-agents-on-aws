/**
 * The chat column: transcript plus composer.
 *
 * `min-h-0` on the wrapper so the transcript is what scrolls, rather than the column growing past
 * the viewport and having its tail clipped.
 */

import type { ConversationState } from '../lib/conversation';
import { Composer } from './Composer';
import { MessageList } from './MessageList';

export interface ChatColumnProps {
  conversation: ConversationState;
  onSend: (prompt: string) => void;
  /** Replaces the composer while a recorded session is on screen. */
  readOnlyNote?: string;
  emptyState?: React.ReactNode;
}

export function ChatColumn({ conversation, onSend, readOnlyNote, emptyState }: ChatColumnProps) {
  const empty = conversation.messages.length === 0 && !conversation.pending;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {empty && emptyState !== undefined ? (
        <div className="scroll-column flex-1 p-5">{emptyState}</div>
      ) : (
        <MessageList messages={conversation.messages} pending={conversation.pending} />
      )}
      <Composer
        disabled={conversation.pending}
        onSend={onSend}
        {...(readOnlyNote !== undefined ? { readOnlyNote } : {})}
      />
    </div>
  );
}
