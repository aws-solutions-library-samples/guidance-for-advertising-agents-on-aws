/**
 * The tool activity column.
 *
 * A keyed list, which is the part React genuinely simplifies: the vanilla version hand-wrote row
 * reconciliation to stop a periodic rebuild destroying a button mid-press, and that reconciliation is
 * simply not needed here.
 *
 * Raw JSON deliberately. This column is the mechanical record of a call, which is what JSON is good
 * for; the product cards live in the chat column, where the seller's answer belongs.
 */

import type { ToolCall, ToolStatus } from '../hooks/toolLog';

export interface ToolLogProps {
  calls: ToolCall[];
  /** Explains an empty log when tool activity is not observable, rather than implying none happened. */
  note: string | null;
}

export function ToolLog({ calls, note }: ToolLogProps) {
  return (
    <div className="scroll-column flex flex-1 flex-col gap-2.5 p-3">
      {note !== null && (
        <p className="m-0 rounded-lg border border-dashed border-line px-3 py-2 text-[13px] text-muted">
          {note}
        </p>
      )}
      {calls.length === 0 && note === null && (
        <p className="m-0 px-1 py-2 text-[13px] text-muted">No tool calls yet.</p>
      )}
      {calls.map((call) => (
        <ToolCard key={call.id} call={call} />
      ))}
    </div>
  );
}

function ToolCard({ call }: { call: ToolCall }) {
  return (
    <div className="scroll-column-item overflow-hidden rounded-[10px] border border-line bg-surface">
      <div className="flex items-center gap-2 border-b border-line bg-line-soft px-3 py-2.5 font-mono text-[13px] font-semibold">
        {call.status === 'pending' && <Spinner />}
        <span className="min-w-0 break-all">{call.toolName}</span>
        <StatusPill status={call.status} />
      </div>
      {call.body !== undefined && call.body !== '' && (
        <pre className="m-0 max-h-[220px] overflow-y-auto px-3 py-2.5 font-mono text-[13px] break-words whitespace-pre-wrap text-ink-2">
          {call.body}
        </pre>
      )}
      {call.sellers !== undefined && call.sellers.length > 0 && (
        <ul className="m-0 list-none border-t border-line-soft p-0">
          {call.sellers.map((seller) => (
            <li
              key={seller.id}
              className="border-b border-line-soft px-3 py-2 last:border-b-0"
            >
              <div className="flex items-center gap-1.5">
                <span
                  className="font-mono text-[13px] font-bold"
                  style={{ color: seller.failed ? 'var(--color-red)' : 'var(--color-green)' }}
                >
                  {seller.failed ? '✗' : '✓'}
                </span>
                <span className="text-[13px] font-semibold text-ink">{seller.name}</span>
              </div>
              {seller.summary !== '' && (
                <div className="mt-0.5 pl-4 text-[13px] text-muted">{seller.summary}</div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Status labels and colours per the style guide's mapping. */
const STATUS_STYLE: Record<ToolStatus, { label: string; className: string }> = {
  pending: { label: 'Calling', className: 'text-sky bg-[#e8f6fc]' },
  success: { label: 'Success', className: 'text-green bg-[#e3f8ee]' },
  error: { label: 'Error', className: 'text-red bg-[#fde8e8]' },
  // Distinct from Error on purpose: the call may have succeeded and only its record is missing.
  not_recorded: { label: 'Not recorded', className: 'text-orange bg-[#fef3e2]' },
  abandoned: { label: 'No result', className: 'text-muted bg-line-soft' },
};

function StatusPill({ status }: { status: ToolStatus }) {
  const { label, className } = STATUS_STYLE[status];
  return (
    <span
      className={`ml-auto flex-none rounded-full px-2 py-0.5 text-[13px] font-bold tracking-wide uppercase ${className}`}
    >
      {label}
    </span>
  );
}

function Spinner() {
  return (
    <span
      // Reflects a real pending call, never a timed animation: it is removed when the recorded
      // result arrives, and replaced with "No result" if the turn ends without one.
      className="h-[11px] w-[11px] flex-none animate-spin rounded-full border-2 border-[#cfe9f5] border-t-sky"
    />
  );
}
