/**
 * Which view each route renders.
 *
 * The journey view is the default because a booth screen should show it without anyone driving. The
 * chat view still exists, unchanged, at `/chat`. An unknown path renders the journey rather than a
 * 404, matching the CloudFront rewrite that serves `index.html` for any unmatched path.
 */

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { App } from './App';
import type { AppConfig } from './lib/types';

const config: AppConfig = {
  model_id: 'us.anthropic.claude-sonnet-5',
  cognito_region: 'us-east-1',
  cognito_client_id: 'client-id',
  agent_runtime_arn: 'arn:aws:bedrock-agentcore:us-east-1:1:runtime/adcp_buyer_agent-X',
  aws_region: 'us-east-1',
  seller_agents: [],
  default_seller_agent_id: '',
  agents: [{ id: 'buyer-a2a', name: 'AdCP Buyer Agent (A2A)' }],
  default_agent_id: 'buyer-a2a',
  agents_error: '',
  ui_published_at: '',
};

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  sessionStorage.setItem(
    'adcp_buyer_agent_session',
    JSON.stringify({ username: 'sam', accessToken: 'tok' }),
  );
  // `/config` answers; anything else (the runtime's own action calls) is refused, so the journey
  // view exercises its failure path.
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation(async (input: unknown) => {
      const url = String(input);
      if (url.includes('config')) {
        // `headers` carries the content type the loader checks: under CloudFront a miss answers 200
        // with the HTML shell, so the status alone does not identify the config.
        return {
          ok: true,
          status: 200,
          headers: { get: () => 'application/json' },
          json: async () => config,
        };
      }
      return { ok: false, status: 503, body: null, headers: { get: () => null }, json: async () => ({}) };
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('routing', () => {
  it('renders the journey view at /', async () => {
    renderAt('/');
    await waitFor(() => {
      // The headline's subject, which is the part that is not session data. The seller and the
      // governance agent are filled from the session, so a routing test must not assert them: doing
      // so once tied this test to the prototype's demo names.
      expect(screen.getByText(/AgentCore Buyer Agent/)).toBeTruthy();
    });
    // And not the chat composer.
    expect(screen.queryByRole('button', { name: 'Send' })).toBeNull();
  });

  it('renders the chat view at /chat, unchanged', async () => {
    renderAt('/chat');
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Send' })).toBeTruthy();
    });
    expect(screen.getByPlaceholderText(/Ask about inventory/)).toBeTruthy();
    // The chat view keeps its agent selector.
    expect(screen.getByRole('option', { name: 'AdCP Buyer Agent (A2A)' })).toBeTruthy();
  });

  it('renders the journey view for an unknown path rather than nothing', async () => {
    // The deployed site serves index.html for any unmatched path, so a mistyped URL must land
    // somewhere useful instead of on a blank screen.
    renderAt('/does-not-exist');
    await waitFor(() => {
      expect(screen.getByText(/AgentCore Buyer Agent/)).toBeTruthy();
    });
  });

  it('sends an old /legacy bookmark to the chat view', async () => {
    renderAt('/legacy');
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Send' })).toBeTruthy();
    });
  });

  it('shows the sign-in screen on any route when signed out', async () => {
    sessionStorage.clear();
    renderAt('/');
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /^Sign in$/ })).toBeTruthy();
    });
    // The gate precedes the routes, so the journey view is not reachable unauthenticated.
    expect(screen.queryByText(/AgentCore Buyer Agent/)).toBeNull();
  });

  it('renders every phase panel, including the ones with no data in this build', async () => {
    renderAt('/');
    await waitFor(() => {
      expect(screen.getByText('Offered products')).toBeTruthy();
    });
    // Eight phases, and the ones this build cannot supply are present and pending rather than absent.
    expect(screen.getByText('Governance binding')).toBeTruthy();
    expect(screen.getByText('Campaign plan registered')).toBeTruthy();
    expect(screen.getByText('Intent governance')).toBeTruthy();
    expect(screen.getByText('In-flight delivery')).toBeTruthy();
  });
});
