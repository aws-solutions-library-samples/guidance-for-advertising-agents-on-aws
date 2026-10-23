/**
 * Agent-registration admin: types, form validation, and event parsing.
 *
 * All non-presentational logic for the admin screen lives here so it can be tested without a DOM,
 * following this project's "logic in lib/, tested" discipline. Nothing here holds a secret: a token a
 * user types in the form is passed straight through to the runtime and never stored client-side, and
 * the runtime never sends a token value back (the listing carries only `has_stored_token`).
 */

import type { InvokeEvent } from './types';

export type AgentKind = 'seller' | 'governance';
export type Transport = 'mcp' | 'a2a';
export type AuthType = 'static_bearer' | 'cognito_bearer' | 'm2m_oauth';

/** One row in the admin view of a registry: environment built-in or stored registration. */
export interface AgentRegistration {
  id: string;
  name: string;
  url: string;
  transport: string;
  /** "env" for a configuration built-in (read-only here), "stored" for a runtime registration. */
  source: 'env' | 'stored' | string;
  /** True when a static bearer token is held server-side. Never the token itself. */
  has_stored_token: boolean;
  /** True when an m2m_oauth client_secret is held server-side. Never the secret itself. */
  has_stored_secret?: boolean;
  /** True for stored entries the admin may edit or delete. */
  editable: boolean;
  /** True when this entry has been removed from the effective registry (a hidden env built-in). */
  hidden?: boolean;
  auth_type?: AuthType | string;
  agent_url?: string;
  // Non-secret m2m_oauth fields, surfaced for display/prefill (the client_secret never is).
  client_id?: string;
  token_url?: string;
  scope?: string;
  audience?: string;
}

/** The editable form for creating or editing a registration. */
export interface RegistrationForm {
  id: string;
  name: string;
  url: string;
  /** Sellers only; the seller's canonical AdCP identity. Blank falls back to `url` server-side. */
  agent_url: string;
  transport: Transport;
  auth_type: AuthType;
  /** The bearer token for a static_bearer external agent. Blank on edit means "keep the stored one". */
  auth_token: string;
  // m2m_oauth (OAuth2 client_credentials) fields. client_secret blank on edit means "keep the stored one".
  client_id: string;
  client_secret: string;
  token_url: string;
  scope: string;
  audience: string;
}

export function emptyForm(): RegistrationForm {
  return {
    id: '',
    name: '',
    url: '',
    agent_url: '',
    transport: 'mcp',
    auth_type: 'static_bearer',
    auth_token: '',
    client_id: '',
    client_secret: '',
    token_url: '',
    scope: '',
    audience: '',
  };
}

/** Prefill a form from an existing registration for editing. The token is never present to prefill. */
export function formFromRegistration(reg: AgentRegistration): RegistrationForm {
  return {
    id: reg.id,
    name: reg.name,
    url: reg.url,
    agent_url: reg.agent_url ?? '',
    transport: reg.transport === 'a2a' ? 'a2a' : 'mcp',
    // Preserve the auth_type across the three known values; anything else falls back to static_bearer.
    // (The old code coerced everything non-cognito to static_bearer, which silently downgraded m2m.)
    auth_type:
      reg.auth_type === 'cognito_bearer' || reg.auth_type === 'm2m_oauth'
        ? reg.auth_type
        : 'static_bearer',
    auth_token: '',
    // Non-secret m2m fields prefill from the (redacted) registration; client_secret never does.
    client_id: reg.client_id ?? '',
    client_secret: '',
    token_url: reg.token_url ?? '',
    scope: reg.scope ?? '',
    audience: reg.audience ?? '',
  };
}

export type FieldErrors = Partial<Record<keyof RegistrationForm, string>>;

export interface ValidationResult {
  ok: boolean;
  errors: FieldErrors;
  /** The entry payload to send, present only when ok. */
  entry?: Record<string, unknown>;
}

/**
 * Validate a form, mirroring the server-side `validate_seller_entry` / `validate_governance_entry`
 * checks so the user sees problems before the round-trip. The server re-validates regardless.
 *
 * `hasStoredToken` is whether the entry being edited already has a token held server-side; when true,
 * a blank token on a static_bearer entry is allowed and means "keep the existing token".
 */
export function validateRegistrationForm(
  kind: AgentKind,
  form: RegistrationForm,
  hasStoredToken = false,
  hasStoredSecret = false,
): ValidationResult {
  const errors: FieldErrors = {};
  const id = form.id.trim();
  const name = form.name.trim();
  const url = form.url.trim();
  const token = form.auth_token.trim();
  const clientId = form.client_id.trim();
  const clientSecret = form.client_secret.trim();
  const tokenUrl = form.token_url.trim();

  if (!id) errors.id = 'An id is required.';
  else if (!/^[A-Za-z0-9._-]+$/.test(id))
    errors.id = 'Id may contain letters, digits and . _ - only.';
  if (!name) errors.name = 'A display name is required.';
  if (!url) errors.url = 'A URL is required.';
  else if (!/^https?:\/\//i.test(url)) errors.url = 'URL must start with http:// or https://.';

  if (form.auth_type === 'static_bearer' && !token && !hasStoredToken) {
    errors.auth_token = 'A bearer token is required for an external (static token) agent.';
  }

  if (form.auth_type === 'm2m_oauth') {
    if (!clientId) errors.client_id = 'A client id is required for M2M OAuth.';
    if (!tokenUrl) errors.token_url = 'A token URL is required for M2M OAuth.';
    else if (!/^https?:\/\//i.test(tokenUrl))
      errors.token_url = 'Token URL must start with http:// or https://.';
    // client_secret required on create; on edit a blank means "keep the stored one".
    if (!clientSecret && !hasStoredSecret)
      errors.client_secret = 'A client secret is required for M2M OAuth.';
  }

  if (Object.keys(errors).length > 0) return { ok: false, errors };

  const entry: Record<string, unknown> = {
    id,
    name,
    url,
    transport: form.transport,
    auth_type: form.auth_type,
  };
  // agent_url is a seller-only concept; governance has no separate canonical identity field.
  if (kind === 'seller' && form.agent_url.trim()) entry.agent_url = form.agent_url.trim();
  // Only send the token when the user actually typed one. Blank on an edit preserves the stored token
  // server-side; sending "" would be read as "clear it".
  if (form.auth_type === 'static_bearer' && token) entry.auth_token = token;
  if (form.auth_type === 'm2m_oauth') {
    entry.client_id = clientId;
    entry.token_url = tokenUrl;
    if (form.scope.trim()) entry.scope = form.scope.trim();
    if (form.audience.trim()) entry.audience = form.audience.trim();
    // Same rule as the bearer token: only send the secret when typed; blank on edit keeps the stored one.
    if (clientSecret) entry.client_secret = clientSecret;
  }

  return { ok: true, errors: {}, entry };
}

// --- Event parsing ---------------------------------------------------------

export interface AgentRegistrationsEvent {
  type: 'agent_registrations';
  kind: AgentKind;
  agents: AgentRegistration[];
}
export interface AgentSavedEvent {
  type: 'agent_registration_saved';
  kind: AgentKind;
  agent: AgentRegistration;
  agents: AgentRegistration[];
}
export interface AgentDeletedEvent {
  type: 'agent_registration_deleted';
  kind: AgentKind;
  id: string;
  existed: boolean;
  agents: AgentRegistration[];
}
export interface ConnectionTestEvent {
  type: 'agent_connection_test';
  kind: AgentKind;
  id: string;
  ok: boolean;
  message: string;
  result?: unknown;
  duration_ms?: number;
}

function findEvent<T extends { type: string }>(events: InvokeEvent[], type: string): T | null {
  const event = events.find((e) => e.type === type);
  return event ? (event as unknown as T) : null;
}

export function findRegistrationsEvent(events: InvokeEvent[]): AgentRegistrationsEvent | null {
  return findEvent<AgentRegistrationsEvent>(events, 'agent_registrations');
}
export function findSavedEvent(events: InvokeEvent[]): AgentSavedEvent | null {
  return findEvent<AgentSavedEvent>(events, 'agent_registration_saved');
}
export function findDeletedEvent(events: InvokeEvent[]): AgentDeletedEvent | null {
  return findEvent<AgentDeletedEvent>(events, 'agent_registration_deleted');
}
export function findConnectionTestEvent(events: InvokeEvent[]): ConnectionTestEvent | null {
  return findEvent<ConnectionTestEvent>(events, 'agent_connection_test');
}
