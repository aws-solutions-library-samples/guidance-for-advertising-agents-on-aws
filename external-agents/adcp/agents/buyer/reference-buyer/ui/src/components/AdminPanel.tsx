/**
 * Cognito user administration.
 *
 * Two truths this panel has to keep straight:
 *
 *   - Cognito's `UserStatus` is shown as it is. An unrecognised or absent status renders as "unknown"
 *     rather than being defaulted.
 *   - `is_admin === null` means group membership could not be read, not that the user is not an
 *     admin. Rendering it as "no" would report the wrong thing about someone's access.
 *
 * The temporary password for a new user is shown once. The pool has no email configuration, so
 * Cognito sends nothing and the value cannot be recovered afterwards; the copy says so plainly rather
 * than implying it can be looked up again.
 */

import { useEffect, useState } from 'react';

import type { UserAdminState } from '../hooks/useUserAdmin';
import type { PoolUser } from '../lib/types';
import { Icon } from './Icon';

export interface AdminPanelProps {
  admin: UserAdminState;
  /** Names the pool being administered, so it is clear which one this affects. */
  clientId: string | null;
  onClose: () => void;
}

export function AdminPanel({ admin, clientId, onClose }: AdminPanelProps) {
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');

  useEffect(() => {
    void admin.refresh();
    // Loaded once when the panel opens. Polling a user list would be motion without purpose.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[rgba(15,23,41,0.45)] p-6"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      onKeyDown={(e) => {
        if (e.key === 'Escape') onClose();
      }}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Manage users"
        className="flex max-h-[85vh] w-full max-w-[760px] flex-col rounded-[14px] border border-line bg-surface shadow-[var(--shadow-card)]"
      >
        <div className="flex flex-none items-center gap-3 border-b border-line px-5 py-3">
          <Icon name="users" size={16} color="var(--color-blue)" />
          <h2 className="m-0 text-[15px] font-bold">Manage users</h2>
          {clientId !== null && (
            <span className="font-mono text-[13px] text-muted">
              client {clientId.slice(0, 8)}…
            </span>
          )}
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="ml-auto rounded-full border border-line p-1.5 text-ink-2 hover:bg-line-soft"
          >
            <Icon name="close" size={13} />
          </button>
        </div>

        {admin.error !== null && (
          <p className="m-0 flex-none border-b border-line-soft bg-[#fde8e8] px-5 py-2 text-[13px] text-red">
            {admin.error}
          </p>
        )}

        {admin.created !== null && (
          <div className="flex-none border-b border-line-soft bg-[#fff8e6] px-5 py-3 text-[13px]">
            <p className="m-0 font-semibold">
              User created. Hand these over now, the temporary password is not shown again.
            </p>
            <dl className="mt-2 mb-0 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 font-mono text-[13px]">
              <dt className="text-muted">Username</dt>
              <dd className="m-0">{admin.created.username}</dd>
              <dt className="text-muted">Temp password</dt>
              <dd className="m-0 select-all">{admin.created.temporary_password}</dd>
            </dl>
            <p className="mt-2 mb-0 text-[13px] text-ink-2">
              {admin.created.must_change_password === true
                ? 'They must set their own password at first sign-in, and this temporary one stops working then.'
                : // Reports what Cognito actually said rather than assuming the usual flow.
                  `Cognito reports status "${admin.created.status ?? 'unknown'}", not ` +
                  'FORCE_CHANGE_PASSWORD, so this user may not be prompted to change their password.'}
            </p>
            <button
              type="button"
              onClick={admin.dismissCreated}
              className="mt-2 rounded-full border border-line bg-surface px-3 py-1 text-[13px] font-semibold text-ink-2"
            >
              Dismiss
            </button>
          </div>
        )}

        <form
          className="flex flex-none flex-wrap items-end gap-2 border-b border-line-soft px-5 py-3"
          onSubmit={(e) => {
            e.preventDefault();
            const u = username.trim();
            const m = email.trim();
            if (u === '' || m === '') return;
            void admin.createUser(u, m).then(() => {
              setUsername('');
              setEmail('');
            });
          }}
        >
          <label className="flex flex-col gap-1">
            <span className="text-[13px] font-semibold text-ink-2">New username</span>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-[180px] rounded-lg border border-line px-2.5 py-1.5 text-[13px] outline-none focus:border-sky"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[13px] font-semibold text-ink-2">Email</span>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-[220px] rounded-lg border border-line px-2.5 py-1.5 text-[13px] outline-none focus:border-sky"
            />
          </label>
          <button
            type="submit"
            disabled={admin.busy || username.trim() === '' || email.trim() === ''}
            className="rounded-lg bg-ink px-3.5 py-2 text-[13px] font-semibold text-white disabled:opacity-40"
          >
            {admin.busy ? 'Creating…' : 'Create user'}
          </button>
        </form>

        <div className="scroll-column flex-1">
          {admin.loading ? (
            <p className="m-0 px-5 py-4 text-[13px] text-muted">Loading users…</p>
          ) : admin.users.length === 0 ? (
            <p className="m-0 px-5 py-4 text-[13px] text-muted">
              {admin.error !== null
                ? 'Could not load users.'
                : 'No users returned for this pool.'}
            </p>
          ) : (
            <table className="w-full border-collapse text-[13px]">
              <thead>
                <tr className="text-left text-[13px] tracking-wide text-muted uppercase">
                  <th className="px-5 py-2 font-semibold">Username</th>
                  <th className="px-3 py-2 font-semibold">Email</th>
                  <th className="px-3 py-2 font-semibold">Status</th>
                  <th className="px-5 py-2 font-semibold">Group</th>
                </tr>
              </thead>
              <tbody>
                {admin.users.map((user) => (
                  <tr key={user.username} className="border-t border-line-soft">
                    <td className="px-5 py-2 font-mono font-semibold">{user.username}</td>
                    <td className="px-3 py-2 text-muted">{user.email ?? ''}</td>
                    <td className="px-3 py-2">
                      <StatusPill status={user.status} />
                    </td>
                    <td className="px-5 py-2">
                      <GroupCell user={user} adminGroup={admin.adminGroup} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}

/** Cognito's UserStatus, shown as it is. */
function StatusPill({ status }: { status: string | undefined }) {
  if (status === 'CONFIRMED') {
    return <Pill className="text-green bg-[#e3f8ee]">confirmed</Pill>;
  }
  if (status === 'FORCE_CHANGE_PASSWORD') {
    return <Pill className="text-orange bg-[#fef3e2]">must change password</Pill>;
  }
  // Anything else is reported verbatim rather than defaulted.
  return <Pill className="text-muted bg-line-soft">{status ?? 'unknown'}</Pill>;
}

function GroupCell({ user, adminGroup }: { user: PoolUser; adminGroup: string | null }) {
  if (user.is_admin === true) {
    return <Pill className="text-purple bg-[#f3eeff]">{adminGroup ?? 'admin'}</Pill>;
  }
  if (user.is_admin === null) {
    // Membership could not be read, and "no" would state the wrong thing about access.
    return <Pill className="text-muted bg-line-soft">unknown</Pill>;
  }
  return null;
}

function Pill({ children, className }: { children: React.ReactNode; className: string }) {
  return (
    <span className={`rounded-full px-2 py-0.5 text-[13px] font-bold ${className}`}>{children}</span>
  );
}
