/**
 * The bar shown while a recorded session is on screen.
 *
 * States which agent ran the conversation, not just who asked: with several agents recording into one
 * store, that is the first thing a reader needs. It also has to be unmistakably read-only, since the
 * transcript otherwise looks exactly like the reader's own conversation.
 */

import { agentLabelFor, invokerLabelFor, sessionStatusTone, shortSessionId } from '../lib/sessions';
import type { SessionMeta } from '../lib/types';
import { Icon } from './Icon';

export interface ViewerBannerProps {
  sessionId: string;
  meta: SessionMeta | null;
  onExit: () => void;
  /** When set, offers to continue this recorded conversation as the live one. */
  onResume?: () => void;
}

export function ViewerBanner({ sessionId, meta, onExit, onResume }: ViewerBannerProps) {
  // "loading…" rather than a guessed status: the meta record may not have arrived yet.
  const status = meta ? sessionStatusTone(meta.status).label : 'loading…';

  return (
    <div className="flex flex-none items-center gap-2 border-b border-line bg-[#fff8e6] px-5 py-2 text-[13px] text-ink-2">
      <Icon name="lock" size={13} color="var(--color-orange)" />
      <span>
        Read-only replay of a conversation on{' '}
        <strong>{meta ? agentLabelFor(meta) : 'unknown agent'}</strong>, started by{' '}
        <strong>{meta ? invokerLabelFor(meta) : 'Unknown invoker'}</strong> ({status})
        {meta?.seller_agent_id !== undefined && meta.seller_agent_id !== ''
          ? ` · seller ${meta.seller_agent_id}`
          : ''}
        .
      </span>
      <span className="font-mono text-[13px] text-muted">{shortSessionId(sessionId)}</span>
      {onResume ? (
        <button
          type="button"
          onClick={onResume}
          data-testid="resume-session"
          title="Continue this conversation: load its history and send the next turn on it"
          className="ml-auto rounded-full border border-sky bg-sky/10 px-3 py-1 text-[13px] font-semibold text-ink hover:bg-sky/20"
        >
          Resume this conversation
        </button>
      ) : null}
      <button
        type="button"
        onClick={onExit}
        className={`${onResume ? '' : 'ml-auto '}rounded-full border border-line bg-surface px-3 py-1 text-[13px] font-semibold text-ink-2 hover:bg-line-soft`}
      >
        Back to my session
      </button>
    </div>
  );
}
