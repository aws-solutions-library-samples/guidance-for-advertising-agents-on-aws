import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { AdminPanel } from './AdminPanel';
import type { UserAdminState } from '../hooks/useUserAdmin';
import type { PoolUser, UserCreatedEvent } from '../lib/types';

/**
 * The two claims this panel must not get wrong:
 *
 *   - an unrecognised Cognito status is reported verbatim rather than defaulted;
 *   - `is_admin === null` means membership could not be read, which is not the same as "not an admin".
 */

function state(over: Partial<UserAdminState> = {}): UserAdminState {
  return {
    users: [],
    adminGroup: null,
    loading: false,
    error: null,
    created: null,
    busy: false,
    refresh: vi.fn().mockResolvedValue(undefined),
    createUser: vi.fn().mockResolvedValue(undefined),
    dismissCreated: vi.fn(),
    ...over,
  };
}

const users: PoolUser[] = [
  { username: 'root', email: 'root@example.test', status: 'CONFIRMED', is_admin: true },
  { username: 'sam', email: 'sam@example.test', status: 'FORCE_CHANGE_PASSWORD', is_admin: false },
  { username: 'mystery', email: 'm@example.test', status: 'ARCHIVED', is_admin: null },
];

afterEach(cleanup);

describe('AdminPanel', () => {
  it('lists the pool users', () => {
    render(<AdminPanel admin={state({ users })} clientId={null} onClose={() => {}} />);
    expect(screen.getByText('root')).toBeTruthy();
    expect(screen.getByText('sam@example.test')).toBeTruthy();
  });

  it('shows Cognito statuses in readable form', () => {
    render(<AdminPanel admin={state({ users })} clientId={null} onClose={() => {}} />);
    expect(screen.getByText('confirmed')).toBeTruthy();
    expect(screen.getByText('must change password')).toBeTruthy();
  });

  it('reports an unrecognised status verbatim rather than defaulting it', () => {
    render(<AdminPanel admin={state({ users })} clientId={null} onClose={() => {}} />);
    // ARCHIVED is not one of the two handled cases; it must not be shown as confirmed.
    expect(screen.getByText('ARCHIVED')).toBeTruthy();
  });

  it('says unknown when a status is missing', () => {
    render(
      <AdminPanel
        admin={state({ users: [{ username: 'x' }] })}
        clientId={null}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText('unknown')).toBeTruthy();
  });

  it('labels the real admin group name', () => {
    render(
      <AdminPanel admin={state({ users, adminGroup: 'pool-admins' })} clientId={null} onClose={() => {}} />,
    );
    expect(screen.getByText('pool-admins')).toBeTruthy();
  });

  it('distinguishes "not an admin" from "could not be read"', () => {
    render(<AdminPanel admin={state({ users, adminGroup: 'admin' })} clientId={null} onClose={() => {}} />);
    // is_admin false shows nothing; is_admin null shows unknown. Collapsing them would state
    // someone's access on the strength of a failed lookup.
    const unknowns = screen.getAllByText('unknown');
    expect(unknowns).toHaveLength(1);
  });

  it('distinguishes loading from an empty pool', () => {
    const loading = render(
      <AdminPanel admin={state({ loading: true })} clientId={null} onClose={() => {}} />,
    );
    expect(screen.getByText(/Loading users/)).toBeTruthy();
    loading.unmount();

    render(<AdminPanel admin={state({ loading: false })} clientId={null} onClose={() => {}} />);
    expect(screen.getByText(/No users returned for this pool/)).toBeTruthy();
  });

  it('says it could not load rather than showing an empty pool, when it failed', () => {
    render(
      <AdminPanel
        admin={state({ error: 'AccessDenied' })}
        clientId={null}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText('AccessDenied')).toBeTruthy();
    expect(screen.getByText(/Could not load users/)).toBeTruthy();
  });

  describe('a newly created user', () => {
    const created: UserCreatedEvent = {
      type: 'user_created',
      username: 'newbie',
      temporary_password: 'Temp-1234',
      status: 'FORCE_CHANGE_PASSWORD',
      must_change_password: true,
    };

    it('shows the credentials and says they are not recoverable', () => {
      render(<AdminPanel admin={state({ created })} clientId={null} onClose={() => {}} />);
      expect(screen.getByText('newbie')).toBeTruthy();
      expect(screen.getByText('Temp-1234')).toBeTruthy();
      expect(screen.getByText(/not shown again/)).toBeTruthy();
    });

    it('warns when Cognito will not force a password change', () => {
      // Reports what Cognito actually said rather than assuming the usual flow.
      render(
        <AdminPanel
          admin={state({
            created: { ...created, must_change_password: false, status: 'CONFIRMED' },
          })}
          clientId={null}
          onClose={() => {}}
        />,
      );
      expect(screen.getByText(/may not be prompted to change their password/)).toBeTruthy();
      expect(screen.getByText(/"CONFIRMED"/)).toBeTruthy();
    });
  });

  it('refreshes the list when it opens', () => {
    const admin = state();
    render(<AdminPanel admin={admin} clientId={null} onClose={() => {}} />);
    expect(admin.refresh).toHaveBeenCalledOnce();
  });

  it('creates a user with the trimmed values', async () => {
    const admin = state();
    render(<AdminPanel admin={admin} clientId={null} onClose={() => {}} />);
    fireEvent.change(screen.getByLabelText('New username'), { target: { value: ' newbie ' } });
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: ' n@example.test ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create user' }));
    await waitFor(() => {
      expect(admin.createUser).toHaveBeenCalledWith('newbie', 'n@example.test');
    });
  });

  it('keeps create disabled until both fields are filled', () => {
    render(<AdminPanel admin={state()} clientId={null} onClose={() => {}} />);
    const button = screen.getByRole('button', { name: 'Create user' });
    expect(button.hasAttribute('disabled')).toBe(true);
    fireEvent.change(screen.getByLabelText('New username'), { target: { value: 'newbie' } });
    expect(button.hasAttribute('disabled')).toBe(true);
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'n@example.test' } });
    expect(button.hasAttribute('disabled')).toBe(false);
  });

  it('names the pool being administered', () => {
    render(
      <AdminPanel admin={state()} clientId="4ja9m026rj02qhk92knbq68ij6" onClose={() => {}} />,
    );
    expect(screen.getByText(/client 4ja9m026/)).toBeTruthy();
  });

  it('closes on the close button', () => {
    const onClose = vi.fn();
    render(<AdminPanel admin={state()} clientId={null} onClose={onClose} />);
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
