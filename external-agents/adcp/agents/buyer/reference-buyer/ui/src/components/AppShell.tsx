/**
 * The signed-in layout: header, chat column, tool activity column.
 *
 * The flex discipline here is load-bearing and was arrived at by debugging the vanilla UI. `main`
 * and both columns carry `min-h-0`, so the scrolling child is what overflows rather than the column
 * growing past the viewport and having its tail clipped unreachably. The scrolling children's own
 * children then need `flex-none` (see `scroll-column-item`), or they compress to share the height
 * instead of overflowing, which is what silently stopped the log panel scrolling and squeezed every
 * card thinner as more arrived.
 */

import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { Icon } from './Icon';

export interface AppShellProps {
  username: string;
  /** What the header reports about the endpoint. Never a placeholder that implies more is known. */
  endpointLabel: string;
  buildLabel: string;
  isAdmin: boolean;
  onSignOut: () => void;
  onOpenAdmin: () => void;
  headerControls?: ReactNode;
  chat: ReactNode;
  log: ReactNode;
}

export function AppShell({
  username,
  endpointLabel,
  buildLabel,
  isAdmin,
  onSignOut,
  onOpenAdmin,
  headerControls,
  chat,
  log,
}: AppShellProps) {
  return (
    <div className="flex h-full flex-col">
      <header className="flex flex-none items-center gap-3 border-b border-line bg-surface px-5 py-2.5">
        <Icon name="network" size={18} color="var(--color-blue)" />
        <div className="min-w-0">
          <h1 className="m-0 text-[15px] font-bold leading-tight">AdCP Buyer Agent</h1>
          <div className="truncate font-mono text-[13px] text-muted">{endpointLabel}</div>
        </div>

        <div className="ml-auto flex items-center gap-2">
          {headerControls}
          <span className="text-[13px] text-muted">{username}</span>
          {/* Agents and Users are the two admin configuration surfaces, so they sit together.
              Both are hidden for non-admins as a convenience, not as the access control: every
              admin action is re-checked server-side against the verified token. */}
          {isAdmin && (
            <Link
              to="/admin/agents"
              data-testid="open-agent-admin"
              title="Add sellers and manage governance agents"
              className="flex items-center gap-1.5 rounded-full border border-line px-3 py-1.5 text-[13px] font-semibold text-ink-2 hover:bg-line-soft"
            >
              <Icon name="network" size={13} />
              Agents
            </Link>
          )}
          {isAdmin && (
            <button
              type="button"
              onClick={onOpenAdmin}
              title="Manage Cognito users"
              className="flex items-center gap-1.5 rounded-full border border-line px-3 py-1.5 text-[13px] font-semibold text-ink-2 hover:bg-line-soft"
            >
              <Icon name="users" size={13} />
              Users
            </button>
          )}
          <button
            type="button"
            onClick={onSignOut}
            className="flex items-center gap-1.5 rounded-full border border-line px-3 py-1.5 text-[13px] font-semibold text-ink-2 hover:bg-line-soft"
          >
            <Icon name="signout" size={13} />
            Sign out
          </button>
        </div>
      </header>

      <main className="flex min-h-0 flex-1 overflow-hidden">
        <section className="flex min-h-0 min-w-0 flex-1 flex-col border-r border-line">{chat}</section>

        <section className="hidden min-h-0 w-[380px] flex-none flex-col bg-surface min-[901px]:flex">
          <div className="flex-none border-b border-line px-4 py-3 text-[13px] font-bold uppercase tracking-wide text-muted">
            Live tool activity
          </div>
          {/* Which build is loaded, so "is this the version I just deployed" is answerable by
              looking. A served page cannot report what the browser kept in memory, and that
              ambiguity has already cost debugging time. */}
          <div className="flex-none border-b border-line-soft px-4 py-1.5 font-mono text-[13px] text-muted">
            {buildLabel}
          </div>
          {log}
        </section>
      </main>
    </div>
  );
}
