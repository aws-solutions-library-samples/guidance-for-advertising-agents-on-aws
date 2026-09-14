import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { App } from './App';
import { runtimeSessionId } from './lib/session';
import { allTestBriefs } from './lib/testBriefs';
import type { AppConfig } from './lib/types';

/**
 * Mounts the app at a chosen route.
 *
 * The chat view moved to `/chat` when the journey view became the default, so these tests name the
 * route they are exercising. The gate states (loading, unreadable config, no pool, signed out) render
 * before any route is matched, so the path is irrelevant to them and harmless.
 *
 * The router lives in `main.tsx` rather than in `App`, which is what lets this choose a route.
 */
function renderApp(path = '/chat') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

/**
 * The gate's job is to tell four situations apart: config still loading, config unreadable, a pool
 * that is not configured, and signed out versus signed in. Collapsing any two of them produces a
 * screen that states the wrong reason nothing is usable.
 */

const config: AppConfig = {
  model_id: 'us.anthropic.claude-sonnet-5',
  cognito_region: 'us-east-1',
  cognito_client_id: '4ja9m026rj02qhk92knbq68ij6',
  agent_runtime_arn:
    'arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/adcp_buyer_agent-EXAMPLE123',
  aws_region: 'us-east-1',
  seller_agents: [],
  default_seller_agent_id: '',
  agents: [],
  default_agent_id: '',
  agents_error: '',
  ui_published_at: '2026-08-06T14:15:25Z',
};

/**
 * Stubs the two-step config fetch: `/config` then `./config.json`.
 *
 * The `headers` are not decoration. The loader checks the content type, because a CloudFront
 * client-side-routing rewrite answers a miss with 200 and the HTML app shell — so a status alone
 * cannot distinguish the config from the page. A double without headers is not a Response.
 */
function stubConfig(body: unknown, ok = true, status = 200, contentType = 'application/json') {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok,
      status,
      headers: { get: (name: string) => (name.toLowerCase() === 'content-type' ? contentType : null) },
      json: async () => body,
    }),
  );
}

beforeEach(() => {
  sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('App gate', () => {
  it('says it is loading before the config arrives', () => {
    // A never-resolving fetch holds the app in its loading state.
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise(() => {})));
    renderApp();
    expect(screen.getByText(/Loading configuration/i)).toBeTruthy();
  });

  it('reports an unreadable config as itself, not as a sign-in problem', async () => {
    stubConfig({}, false, 500);
    renderApp();
    await waitFor(() => {
      expect(screen.getByText(/Could not load configuration/i)).toBeTruthy();
    });
    // The distinction that matters: this is not a failed login.
    expect(screen.queryByLabelText('Password')).toBeNull();
  });

  it('reports a missing user pool rather than offering a login that cannot work', async () => {
    stubConfig({ ...config, cognito_region: '', cognito_client_id: '' });
    renderApp();
    await waitFor(() => {
      expect(screen.getByText(/No Cognito user pool is configured/i)).toBeTruthy();
    });
    expect(screen.queryByRole('button', { name: /^Sign in$/ })).toBeNull();
  });

  it('shows the sign-in form when configured and signed out', async () => {
    stubConfig(config);
    renderApp();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /^Sign in$/ })).toBeTruthy();
    });
    expect(screen.getByLabelText('Username')).toBeTruthy();
    expect(screen.getByLabelText('Password')).toBeTruthy();
  });

  it('names the pool client on the sign-in screen', async () => {
    stubConfig(config);
    renderApp();
    await waitFor(() => {
      expect(screen.getByText(/client 4ja9m026/)).toBeTruthy();
    });
  });

  it('keeps sign-in disabled until both fields are filled', async () => {
    stubConfig(config);
    renderApp();
    await waitFor(() => screen.getByRole('button', { name: /^Sign in$/ }));
    expect(screen.getByRole('button', { name: /^Sign in$/ }).hasAttribute('disabled')).toBe(true);
  });

  it('goes straight to the app when the tab already holds a session', async () => {
    // Restoring rather than flashing the login screen is the point.
    sessionStorage.setItem(
      'adcp_buyer_agent_session',
      JSON.stringify({ username: 'sam', accessToken: 'tok' }),
    );
    stubConfig(config);
    renderApp();
    await waitFor(() => {
      expect(screen.getByText('sam')).toBeTruthy();
    });
    expect(screen.getByRole('button', { name: /Sign out/i })).toBeTruthy();
  });

  it('shows the build stamp the config reported', async () => {
    sessionStorage.setItem(
      'adcp_buyer_agent_session',
      JSON.stringify({ username: 'sam', accessToken: 'tok' }),
    );
    stubConfig(config);
    renderApp();
    await waitFor(() => {
      expect(screen.getByText(/^build /)).toBeTruthy();
    });
  });

  it('says "served locally" when there is no publish time', async () => {
    sessionStorage.setItem(
      'adcp_buyer_agent_session',
      JSON.stringify({ username: 'sam', accessToken: 'tok' }),
    );
    stubConfig({ ...config, ui_published_at: '' });
    renderApp();
    await waitFor(() => {
      expect(screen.getByText('served locally')).toBeTruthy();
    });
  });

  it('names the runtime it will send turns to', async () => {
    sessionStorage.setItem(
      'adcp_buyer_agent_session',
      JSON.stringify({ username: 'sam', accessToken: 'tok' }),
    );
    stubConfig(config);
    renderApp();
    await waitFor(() => {
      expect(screen.getByText(/adcp_buyer_agent-EXAMPLE123/)).toBeTruthy();
    });
  });

  it('says so when no runtime is configured, rather than showing a bare region', async () => {
    sessionStorage.setItem(
      'adcp_buyer_agent_session',
      JSON.stringify({ username: 'sam', accessToken: 'tok' }),
    );
    stubConfig({ ...config, agent_runtime_arn: '' });
    renderApp();
    await waitFor(() => {
      expect(screen.getByText('no runtime configured')).toBeTruthy();
    });
  });

  it('renders the chat column with a composer once signed in', async () => {
    sessionStorage.setItem(
      'adcp_buyer_agent_session',
      JSON.stringify({ username: 'sam', accessToken: 'tok' }),
    );
    stubConfig({
      ...config,
      agents: [{ id: 'buyer-a2a', name: 'AdCP Buyer Agent (A2A)' }],
      default_agent_id: 'buyer-a2a',
    });
    renderApp();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Send' })).toBeTruthy();
    });
    expect(screen.getByPlaceholderText(/Ask about inventory/)).toBeTruthy();
    // Says which agent turns go to, rather than leaving it implied. Named in two places, the
    // selector and the empty state, so the assertion targets the selector specifically.
    expect(screen.getByRole('option', { name: 'AdCP Buyer Agent (A2A)' })).toBeTruthy();
    expect(screen.getByText(/Turns go to AdCP Buyer Agent \(A2A\)/)).toBeTruthy();
  });

  it('says there is nothing to send to when no agent is configured', async () => {
    sessionStorage.setItem(
      'adcp_buyer_agent_session',
      JSON.stringify({ username: 'sam', accessToken: 'tok' }),
    );
    stubConfig({ ...config, agents: [], default_agent_id: '' });
    renderApp();
    await waitFor(() => {
      expect(screen.getByText(/No chat agents are configured/)).toBeTruthy();
    });
    // No composer, because there is nothing it could do.
    expect(screen.queryByRole('button', { name: 'Send' })).toBeNull();
  });

  it('surfaces a registry error instead of an unexplained empty selector', async () => {
    sessionStorage.setItem(
      'adcp_buyer_agent_session',
      JSON.stringify({ username: 'sam', accessToken: 'tok' }),
    );
    stubConfig({ ...config, agents: [], default_agent_id: '', agents_error: 'table not found' });
    renderApp();
    await waitFor(() => {
      expect(screen.getByText(/No agents are available: table not found/)).toBeTruthy();
    });
  });

  it('offers the Users button only to an admin', async () => {
    // Non-admin token first.
    sessionStorage.setItem(
      'adcp_buyer_agent_session',
      JSON.stringify({ username: 'sam', accessToken: jwtWith({ 'cognito:groups': ['ops'] }) }),
    );
    stubConfig(config);
    const plain = renderApp();
    await waitFor(() => screen.getByText('sam'));
    expect(screen.queryByRole('button', { name: /Users/i })).toBeNull();
    plain.unmount();

    sessionStorage.setItem(
      'adcp_buyer_agent_session',
      JSON.stringify({ username: 'root', accessToken: jwtWith({ 'cognito:groups': ['admin'] }) }),
    );
    renderApp();
    await waitFor(() => screen.getByText('root'));
    expect(screen.getByRole('button', { name: /Users/i })).toBeTruthy();
  });
});

function jwtWith(claims: Record<string, unknown>): string {
  const b64url = (s: string) => btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${b64url('{"alg":"none"}')}.${b64url(JSON.stringify(claims))}.sig`;
}

/**
 * The empty state's example briefs, and the New session button beside them.
 *
 * Tested through `App` rather than against `TestBriefs` in isolation, because the unit tests already
 * cover the catalogue and the buttons. What is worth checking here is the wiring: that the briefs are
 * reachable on `/chat` when the transcript is empty, that a click sends the prompt rather than the
 * label, and that New session actually changes the runtime session id -- the last of which is invisible
 * on screen and is the part that would silently do nothing.
 */
describe('chat empty state: example briefs and New session', () => {
  const withAgent = {
    ...config,
    agents: [{ id: 'buyer-a2a', name: 'AdCP Buyer Agent (A2A)' }],
    default_agent_id: 'buyer-a2a',
  };

  function signIn() {
    sessionStorage.setItem(
      'adcp_buyer_agent_session',
      JSON.stringify({ username: 'sam', accessToken: 'tok' }),
    );
  }

  it('offers both brief categories while the transcript is empty', async () => {
    signIn();
    stubConfig(withAgent);
    renderApp();
    await waitFor(() => {
      expect(screen.getByText('Discover inventory')).toBeTruthy();
    });
    expect(screen.getByText('Check existing campaigns')).toBeTruthy();
  });

  it('offers a New session button on the chat route', async () => {
    signIn();
    stubConfig(withAgent);
    renderApp();
    await waitFor(() => {
      expect(screen.getByTestId('chat-new-session-button')).toBeTruthy();
    });
  });

  it('mints a new runtime session id when New session is clicked', async () => {
    signIn();
    stubConfig(withAgent);
    renderApp();
    await waitFor(() => {
      expect(screen.getByTestId('chat-new-session-button')).toBeTruthy();
    });

    // Establish the current id the way a turn would, then check the button replaces it. A cleared
    // transcript that kept this id would leave the agent answering with history the reader cannot see.
    const before = runtimeSessionId();
    fireEvent.click(screen.getByTestId('chat-new-session-button'));
    await waitFor(() => {
      expect(runtimeSessionId()).not.toBe(before);
    });
  });

  it('sends a brief’s full prompt, not its label', async () => {
    signIn();
    stubConfig(withAgent);
    renderApp();
    const brief = allTestBriefs()[0]!;
    await waitFor(() => {
      expect(screen.getByTestId(`test-brief-${brief.id}`)).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId(`test-brief-${brief.id}`));
    // The prompt becomes the reader's own turn in the transcript, which is also what replaces the
    // empty state -- so finding it proves both that it was sent and that it was sent in full.
    await waitFor(() => {
      expect(screen.getByText(brief.prompt)).toBeTruthy();
    });
  });

  it('hides the briefs once the transcript has a turn in it', async () => {
    signIn();
    stubConfig(withAgent);
    renderApp();
    const brief = allTestBriefs()[0]!;
    await waitFor(() => {
      expect(screen.getByTestId('test-briefs')).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId(`test-brief-${brief.id}`));
    await waitFor(() => {
      expect(screen.queryByTestId('test-briefs')).toBeNull();
    });
  });

  it('brings the briefs back after New session, having cleared the transcript', async () => {
    // The whole point of the button: send a turn, clear it, and be offered the examples again. Tested
    // as one sequence because each half is unremarkable alone -- it is the return to the empty state
    // that makes New session useful rather than just a session-id reset nobody can see.
    signIn();
    stubConfig(withAgent);
    renderApp();
    const brief = allTestBriefs()[0]!;
    await waitFor(() => {
      expect(screen.getByTestId(`test-brief-${brief.id}`)).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId(`test-brief-${brief.id}`));
    await waitFor(() => {
      expect(screen.queryByTestId('test-briefs')).toBeNull();
    });

    fireEvent.click(screen.getByTestId('chat-new-session-button'));
    await waitFor(() => {
      expect(screen.getByTestId('test-briefs')).toBeTruthy();
    });
    // The turn that was sent is gone, not merely scrolled out of view.
    expect(screen.queryByText(brief.prompt)).toBeNull();
    expect(screen.getByText('Discover inventory')).toBeTruthy();
  });

  it('disables the briefs when no agent is configured, rather than offering a dead button', async () => {
    signIn();
    stubConfig(config); // agents: []
    renderApp();
    const brief = allTestBriefs()[0]!;
    await waitFor(() => {
      expect(screen.getByTestId(`test-brief-${brief.id}`)).toBeTruthy();
    });
    expect(screen.getByTestId(`test-brief-${brief.id}`).hasAttribute('disabled')).toBe(true);
  });
});
