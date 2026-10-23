import { describe, expect, it } from 'vitest';

import {
  emptyForm,
  findConnectionTestEvent,
  findRegistrationsEvent,
  findSavedEvent,
  formFromRegistration,
  validateRegistrationForm,
  type AgentRegistration,
  type RegistrationForm,
} from './agentRegistry';

function form(over: Partial<RegistrationForm> = {}): RegistrationForm {
  return {
    ...emptyForm(),
    id: 'acme',
    name: 'Acme',
    url: 'https://acme.example/adcp/mcp',
    auth_token: 'tok',
    ...over,
  };
}

describe('validateRegistrationForm', () => {
  it('accepts a well-formed static_bearer seller and builds the entry', () => {
    const r = validateRegistrationForm('seller', form({ agent_url: 'https://acme.example' }));
    expect(r.ok).toBe(true);
    expect(r.entry).toMatchObject({
      id: 'acme',
      url: 'https://acme.example/adcp/mcp',
      auth_type: 'static_bearer',
      auth_token: 'tok',
      agent_url: 'https://acme.example',
    });
  });

  it('requires id, name and url', () => {
    const r = validateRegistrationForm('seller', form({ id: '', name: '', url: '' }));
    expect(r.ok).toBe(false);
    expect(r.errors.id).toBeDefined();
    expect(r.errors.name).toBeDefined();
    expect(r.errors.url).toBeDefined();
  });

  it('rejects a url without an http(s) scheme', () => {
    const r = validateRegistrationForm('seller', form({ url: 'acme.example/mcp' }));
    expect(r.ok).toBe(false);
    expect(r.errors.url).toBeDefined();
  });

  it('requires a token for a new static_bearer entry', () => {
    const r = validateRegistrationForm('seller', form({ auth_token: '' }));
    expect(r.ok).toBe(false);
    expect(r.errors.auth_token).toBeDefined();
  });

  it('allows a blank token when editing an entry that already has one stored', () => {
    const r = validateRegistrationForm('seller', form({ auth_token: '' }), true);
    expect(r.ok).toBe(true);
    // No token is sent, so the server keeps the stored one rather than clearing it.
    expect(r.entry).not.toHaveProperty('auth_token');
  });

  it('does not require a token for a cognito_bearer entry', () => {
    const r = validateRegistrationForm('seller', form({ auth_type: 'cognito_bearer', auth_token: '' }));
    expect(r.ok).toBe(true);
    expect(r.entry).not.toHaveProperty('auth_token');
  });

  it('omits agent_url for governance entries', () => {
    const r = validateRegistrationForm('governance', form({ agent_url: 'https://ignored.example' }));
    expect(r.ok).toBe(true);
    expect(r.entry).not.toHaveProperty('agent_url');
  });

  it('accepts a well-formed m2m_oauth entry and builds the entry', () => {
    const r = validateRegistrationForm(
      'seller',
      form({
        auth_type: 'm2m_oauth',
        auth_token: '',
        client_id: 'cid',
        client_secret: 'csec',
        token_url: 'https://auth.acme.example/oauth2/token',
        scope: 'adcp/read',
      }),
    );
    expect(r.ok).toBe(true);
    expect(r.entry).toMatchObject({
      auth_type: 'm2m_oauth',
      client_id: 'cid',
      client_secret: 'csec',
      token_url: 'https://auth.acme.example/oauth2/token',
      scope: 'adcp/read',
    });
    expect(r.entry).not.toHaveProperty('auth_token');
    // audience omitted when blank
    expect(r.entry).not.toHaveProperty('audience');
  });

  it('requires client_id, token_url and client_secret for a new m2m_oauth entry', () => {
    const r = validateRegistrationForm(
      'seller',
      form({ auth_type: 'm2m_oauth', auth_token: '', client_id: '', client_secret: '', token_url: '' }),
    );
    expect(r.ok).toBe(false);
    expect(r.errors.client_id).toBeDefined();
    expect(r.errors.token_url).toBeDefined();
    expect(r.errors.client_secret).toBeDefined();
  });

  it('rejects an m2m token_url without an http(s) scheme', () => {
    const r = validateRegistrationForm(
      'seller',
      form({ auth_type: 'm2m_oauth', auth_token: '', client_id: 'cid', client_secret: 'csec', token_url: 'auth/token' }),
    );
    expect(r.ok).toBe(false);
    expect(r.errors.token_url).toBeDefined();
  });

  it('allows a blank client_secret when editing an m2m entry that already has one stored', () => {
    const r = validateRegistrationForm(
      'seller',
      form({
        auth_type: 'm2m_oauth',
        auth_token: '',
        client_id: 'cid',
        client_secret: '',
        token_url: 'https://auth.acme.example/oauth2/token',
      }),
      false,
      true,
    );
    expect(r.ok).toBe(true);
    // Secret not sent, so the server keeps the stored one.
    expect(r.entry).not.toHaveProperty('client_secret');
    expect(r.entry).toMatchObject({ client_id: 'cid' });
  });
});

describe('formFromRegistration', () => {
  it('prefills fields but never a token', () => {
    const reg: AgentRegistration = {
      id: 'acme',
      name: 'Acme',
      url: 'https://acme.example/adcp/mcp',
      transport: 'a2a',
      source: 'stored',
      has_stored_token: true,
      editable: true,
      auth_type: 'static_bearer',
      agent_url: 'https://acme.example',
    };
    const f = formFromRegistration(reg);
    expect(f).toMatchObject({ id: 'acme', transport: 'a2a', auth_type: 'static_bearer', auth_token: '' });
  });

  it('preserves the m2m_oauth auth_type and prefills its non-secret fields but never the secret', () => {
    const reg: AgentRegistration = {
      id: 'acme-m2m',
      name: 'Acme M2M',
      url: 'https://acme.example/adcp/mcp',
      transport: 'mcp',
      source: 'stored',
      has_stored_token: false,
      has_stored_secret: true,
      editable: true,
      auth_type: 'm2m_oauth',
      client_id: 'cid',
      token_url: 'https://auth.acme.example/oauth2/token',
      scope: 'adcp/read',
    };
    const f = formFromRegistration(reg);
    expect(f).toMatchObject({
      auth_type: 'm2m_oauth',
      client_id: 'cid',
      token_url: 'https://auth.acme.example/oauth2/token',
      scope: 'adcp/read',
      client_secret: '',
    });
  });
});

describe('event finders', () => {
  it('finds a registrations event', () => {
    const ev = findRegistrationsEvent([
      { type: 'agent_registrations', kind: 'seller', agents: [] },
    ]);
    expect(ev?.kind).toBe('seller');
  });

  it('finds a saved event', () => {
    const ev = findSavedEvent([
      { type: 'agent_registration_saved', kind: 'seller', agent: { id: 'x' }, agents: [] },
    ]);
    expect(ev).not.toBeNull();
  });

  it('finds a connection-test event with its outcome', () => {
    const ev = findConnectionTestEvent([
      { type: 'agent_connection_test', kind: 'seller', id: 'x', ok: false, message: 'boom' },
    ]);
    expect(ev?.ok).toBe(false);
    expect(ev?.message).toBe('boom');
  });

  it('returns null when the event is absent', () => {
    expect(findRegistrationsEvent([{ type: 'error', message: 'no' }])).toBeNull();
  });
});
