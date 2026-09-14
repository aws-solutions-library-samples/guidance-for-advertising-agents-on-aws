/**
 * The conversation itself.
 *
 * A product list becomes the card stack; every other payload keeps the monospace JSON it always had,
 * because a shape this UI does not model specially is more useful unaltered than reformatted by
 * guesswork.
 */

import { useEffect, useRef } from 'react';

import { renderMarkdown } from '../lib/markdown';
import type { Message } from '../lib/conversation';
import type { SellerResponse } from '../lib/types';
import { ProductStack } from './ProductStack';

export interface MessageListProps {
  messages: Message[];
  pending: boolean;
}

export function MessageList({ messages, pending }: MessageListProps) {
  const endRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    // Only follow new content when the reader is already at the bottom. Scrolling someone back down
    // while they are reading earlier messages takes the view away from them.
    const distanceFromBottom =
      container.scrollHeight - container.scrollTop - container.clientHeight;
    if (distanceFromBottom < 120) {
      endRef.current?.scrollIntoView({ block: 'end' });
    }
  }, [messages, pending]);

  return (
    <div ref={containerRef} className="scroll-column flex flex-1 flex-col gap-4 p-5">
      {messages.map((message) => (
        <MessageRow key={message.id} message={message} />
      ))}
      {pending && <PendingIndicator />}
      <div ref={endRef} />
    </div>
  );
}

function MessageRow({ message }: { message: Message }) {
  switch (message.kind) {
    case 'user':
      return (
        <div className="scroll-column-item flex max-w-[720px] flex-col gap-1 self-end">
          {/* Replay names the caller and the time here; a live turn is simply "You". */}
          <span className="text-right text-[13px] font-semibold text-muted">
            {message.label ?? 'You'}
          </span>
          <div className="rounded-[14px] rounded-br-[4px] bg-ink px-4 py-3 text-[14.5px] leading-relaxed break-words whitespace-pre-wrap text-white">
            {message.text}
          </div>
        </div>
      );

    case 'thought':
      return (
        <div className="scroll-column-item flex max-w-[720px] flex-col gap-1 self-start">
          {/* Labelled as reasoning so it is not read as the reply. */}
          <span className="text-[13px] font-semibold text-purple">
            {message.label ?? 'Agent, reasoning'}
          </span>
          <div className="rounded-[10px] border border-dashed border-line bg-canvas px-3 py-2 text-[13px] leading-relaxed break-words whitespace-pre-wrap text-ink-2 italic">
            {message.text}
          </div>
        </div>
      );

    case 'agent':
      return (
        <div className="scroll-column-item flex max-w-[720px] flex-col gap-1 self-start">
          <span className="text-[13px] font-semibold text-muted">{message.label ?? 'Agent'}</span>
          <div className="rounded-[14px] rounded-bl-[4px] border border-line bg-surface px-4 py-3 text-[14.5px] leading-relaxed text-ink">
            {/* Sanitised in renderMarkdown before it reaches here; model output is untrusted text. */}
            <div
              className="[&_a]:text-sky [&_a]:underline [&_code]:font-mono [&_code]:text-[13px] [&_li]:my-0.5 [&_ol]:my-1 [&_ol]:ml-5 [&_p]:my-0 [&_p+p]:mt-2 [&_pre]:overflow-auto [&_pre]:rounded-lg [&_pre]:bg-canvas [&_pre]:p-2.5 [&_table]:text-[13px] [&_td]:border [&_td]:border-line [&_td]:px-2 [&_td]:py-1 [&_th]:border [&_th]:border-line [&_th]:px-2 [&_th]:py-1 [&_ul]:my-1 [&_ul]:ml-5"
              dangerouslySetInnerHTML={{ __html: renderMarkdown(message.text) }}
            />
            {message.streaming && <Caret />}
          </div>
        </div>
      );

    case 'seller':
      return <SellerMessage message={message} />;

    case 'note':
      return (
        <div className="scroll-column-item flex max-w-[720px] flex-col gap-1 self-start">
          <span className="text-[13px] font-semibold text-orange">{message.label}</span>
          <div className="rounded-[10px] border border-line bg-canvas px-3 py-2 text-[13px] break-words whitespace-pre-wrap text-ink-2">
            {message.text}
          </div>
        </div>
      );
  }
}

function SellerMessage({ message }: { message: Extract<Message, { kind: 'seller' }> }) {
  const label = [message.sellerName, message.toolName].filter(Boolean).join(' — ') || 'Response';
  const payload = message.payload;
  const isProductList =
    payload !== null &&
    typeof payload === 'object' &&
    Array.isArray((payload as SellerResponse).products);

  return (
    <div
      className={`scroll-column-item flex flex-col gap-1 self-start ${
        isProductList ? 'max-w-[860px]' : 'max-w-[720px]'
      }`}
    >
      <span className="text-[13px] font-semibold text-sky">{label}</span>
      {isProductList ? (
        // Wider than a text bubble: a title, chips and two score bars per row read badly wrapped
        // into a narrow column.
        <div className="overflow-hidden rounded-[14px] rounded-bl-[4px] border border-line bg-surface">
          <ProductStack response={payload as SellerResponse} />
        </div>
      ) : (
        <pre className="m-0 max-h-[280px] overflow-y-auto rounded-[14px] rounded-bl-[4px] border border-line bg-surface px-4 py-3 font-mono text-[13px] break-words whitespace-pre-wrap text-ink-2">
          {typeof payload === 'string' ? payload : JSON.stringify(payload, null, 2)}
        </pre>
      )}
    </div>
  );
}

/** Marks a reply still arriving, so a pause reads as "still going" rather than "finished". */
function Caret() {
  return (
    <span className="ml-0.5 inline-block h-[1em] w-[2px] translate-y-[2px] animate-pulse bg-sky" />
  );
}

function PendingIndicator() {
  return (
    <div className="scroll-column-item flex max-w-[720px] flex-col gap-1 self-start">
      <span className="text-[13px] font-semibold text-muted">Agent</span>
      <div className="flex gap-1 rounded-[14px] rounded-bl-[4px] border border-line bg-surface px-4 py-3">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted"
            style={{ animationDelay: `${i * 120}ms` }}
          />
        ))}
      </div>
    </div>
  );
}
