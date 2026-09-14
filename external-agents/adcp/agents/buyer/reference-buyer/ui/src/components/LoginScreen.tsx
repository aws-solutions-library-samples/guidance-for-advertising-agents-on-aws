/**
 * Sign-in, including the first-login password change.
 *
 * Credentials go straight to Cognito's own APIs from the browser; this project's server never sees
 * a password. The second step appears only when Cognito answers with NEW_PASSWORD_REQUIRED, which
 * happens for a user an admin created with a temporary password. Cognito issues no tokens until
 * that challenge is answered, so the step cannot be skipped.
 */

import { useEffect, useState } from 'react';

import { evaluatePasswordRules, passwordRulesMet } from '../lib/cognito';
import type { AuthState } from '../hooks/useAuth';
import { Icon } from './Icon';

const RULE_LABELS: Array<[keyof ReturnType<typeof evaluatePasswordRules>, string]> = [
  ['length', 'At least 8 characters'],
  ['upper', 'An uppercase letter'],
  ['lower', 'A lowercase letter'],
  ['digit', 'A number'],
  ['match', 'Both entries match'],
];

export interface LoginScreenProps {
  auth: AuthState;
  /** Names the pool being authenticated against. Absent until config loads. */
  clientId: string | null;
}

export function LoginScreen({ auth, clientId }: LoginScreenProps) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirm, setConfirm] = useState('');

  const inChallenge = auth.challenge !== null;
  const rules = evaluatePasswordRules(newPassword, confirm);
  const rulesMet = passwordRulesMet(rules);

  useEffect(() => {
    // Clear both password fields whenever the challenge step opens or closes, so a value typed
    // for one step is never carried into the other.
    setNewPassword('');
    setConfirm('');
    if (inChallenge) setPassword('');
  }, [inChallenge]);

  return (
    <div className="flex h-full items-center justify-center bg-canvas p-6">
      <div className="w-full max-w-[400px] rounded-[14px] border border-line bg-surface p-7 shadow-[var(--shadow-card)]">
        <Icon name="lock" size={20} color="var(--color-blue)" />
        <h1 className="mt-3 mb-1 text-[19px] font-bold">AdCP Buyer Agent</h1>
        <p className="m-0 text-[13px] text-muted">
          {inChallenge
            ? 'This account was created with a temporary password. Set a permanent one to continue.'
            : 'Sign in with your Cognito account to continue.'}
        </p>

        {auth.error !== null && (
          <p className="mt-4 mb-0 rounded-lg bg-[#fde8e8] px-3 py-2 text-[13px] text-red">
            {auth.error}
          </p>
        )}

        {!inChallenge ? (
          <form
            className="mt-5 flex flex-col gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              void auth.signIn(username.trim(), password);
            }}
          >
            <Field
              id="loginUsername"
              label="Username"
              type="text"
              autoComplete="username"
              value={username}
              onChange={setUsername}
            />
            <Field
              id="loginPassword"
              label="Password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={setPassword}
            />
            <button
              type="submit"
              disabled={auth.busy || username.trim() === '' || password === ''}
              className="mt-1 rounded-lg bg-ink px-4 py-2.5 text-[13.5px] font-semibold text-white disabled:opacity-50"
            >
              {auth.busy ? 'Signing in…' : 'Sign in'}
            </button>
          </form>
        ) : (
          <form
            className="mt-5 flex flex-col gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              void auth.submitNewPassword(newPassword);
            }}
          >
            <Field
              id="newPassword"
              label="New password"
              type="password"
              autoComplete="new-password"
              value={newPassword}
              onChange={setNewPassword}
            />
            <Field
              id="newPasswordConfirm"
              label="Confirm new password"
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={setConfirm}
            />
            <ul className="m-0 flex list-none flex-col gap-1 p-0">
              {RULE_LABELS.map(([key, label]) => (
                <li
                  key={key}
                  className={`flex items-center gap-2 text-[13px] ${
                    rules[key] ? 'text-green' : 'text-muted'
                  }`}
                >
                  {/* Tinted to its state rather than left as ink, so the list reads at a glance. */}
                  <Icon
                    name={rules[key] ? 'check' : 'close'}
                    size={11}
                    color={rules[key] ? 'var(--color-green)' : 'var(--color-muted)'}
                  />
                  {label}
                </li>
              ))}
            </ul>
            <button
              type="submit"
              // Cognito rejects a non-compliant password regardless; this only avoids a round trip
              // that is certain to fail.
              disabled={auth.busy || !rulesMet}
              className="mt-1 rounded-lg bg-ink px-4 py-2.5 text-[13.5px] font-semibold text-white disabled:opacity-50"
            >
              {auth.busy ? 'Setting password…' : 'Set password and sign in'}
            </button>
            <button
              type="button"
              onClick={auth.cancelChallenge}
              className="rounded-lg border border-line px-4 py-2 text-[13px] font-semibold text-ink-2"
            >
              Back to sign in
            </button>
          </form>
        )}

        <p className="mt-5 mb-0 text-[13px] leading-relaxed text-muted">
          Authenticated against a real Amazon Cognito user pool (
          <span className="font-mono">
            {clientId ? `client ${clientId.slice(0, 8)}…` : 'loading…'}
          </span>
          ). Credentials are sent directly to Cognito&apos;s <code>InitiateAuth</code> and{' '}
          <code>RespondToAuthChallenge</code> APIs; this server never sees your password.
        </p>
      </div>
    </div>
  );
}

interface FieldProps {
  id: string;
  label: string;
  type: 'text' | 'password';
  autoComplete: string;
  value: string;
  onChange: (value: string) => void;
}

function Field({ id, label, type, autoComplete, value, onChange }: FieldProps) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-[13px] font-semibold text-ink-2">
        {label}
      </label>
      <input
        id={id}
        type={type}
        autoComplete={autoComplete}
        required
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-lg border border-line px-3 py-2 text-[13.5px] outline-none focus:border-sky"
      />
    </div>
  );
}
