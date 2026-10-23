/**
 * Config loading, and one regression in particular.
 *
 * The first test here is the outage: with a CloudFront client-side-routing rewrite in place, `/config`
 * answers **200 with the HTML app shell** rather than 403, because the rewrite maps 403 and 404 to
 * `/index.html` with a 200. A loader that treats `res.ok` as "this is the config" then parses HTML as
 * JSON and the app cannot start.
 *
 * That combination reached production once. It is asserted here because no test of either change alone
 * would have caught it: the rewrite was correct, the loader was correct, and the pair was not.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { fetchAppConfig } from './config';

const SHELL = '<!doctype html>\n<html lang="en"><head></head><body></body></html>';

const CONFIG = {
  model_id: 'global.anthropic.claude-sonnet-5',
  cognito_region: 'us-east-1',
  cognito_client_id: 'abc123',
  agent_runtime_arn: 'arn:aws:bedrock-agentcore:us-east-1:1:runtime/x',
  aws_region: 'us-east-1',
  seller_agents: [],
  default_seller_agent_id: '',
  agents: [],
  default_agent_id: '',
  agents_error: '',
  seller_priority: [],
  ui_published_at: '2026-08-08T01:44:03Z',
};

function response(
  body: string,
  { status = 200, contentType = 'application/json' } = {},
): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name: string) => (name.toLowerCase() === 'content-type' ? contentType : null) },
    json: async () => JSON.parse(body) as unknown,
  } as unknown as Response;
}

/** Route by URL, so tests describe what each location answers rather than call order. */
function serve(routes: Record<string, Response | 'network-error'>) {
  const fetchMock = vi.fn(async (url: string) => {
    const answer = routes[url];
    if (answer === undefined) throw new Error(`unexpected fetch: ${url}`);
    if (answer === 'network-error') throw new TypeError('Failed to fetch');
    return answer;
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

describe('fetchAppConfig', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('falls back when /config returns 200 with the SPA shell', async () => {
    // THE REGRESSION. A CloudFront rewrite turns every miss into 200 + HTML, so a status check alone
    // concludes that the app shell is the configuration.
    const fetchMock = serve({
      '/config': response(SHELL, { contentType: 'text/html; charset=utf-8' }),
      './config.json': response(JSON.stringify(CONFIG)),
    });
    const config = await fetchAppConfig();
    expect(config.ui_published_at).toBe(CONFIG.ui_published_at);
    // Both were consulted: the first answered, just not with JSON.
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('uses /config when it really is JSON, which is the local path', async () => {
    const fetchMock = serve({ '/config': response(JSON.stringify(CONFIG)) });
    const config = await fetchAppConfig();
    expect(config.cognito_client_id).toBe('abc123');
    // No second request: the preferred location answered.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('falls back on a non-OK status, which is the behaviour that already worked', async () => {
    serve({
      '/config': response('', { status: 403, contentType: 'application/xml' }),
      './config.json': response(JSON.stringify(CONFIG)),
    });
    await expect(fetchAppConfig()).resolves.toMatchObject({ aws_region: 'us-east-1' });
  });

  it('falls back when /config is declared JSON but is not parseable', async () => {
    serve({
      '/config': response('not json at all'),
      './config.json': response(JSON.stringify(CONFIG)),
    });
    await expect(fetchAppConfig()).resolves.toMatchObject({ model_id: CONFIG.model_id });
  });

  it('falls back when the first request fails at the network level', async () => {
    serve({
      '/config': 'network-error',
      './config.json': response(JSON.stringify(CONFIG)),
    });
    await expect(fetchAppConfig()).resolves.toMatchObject({ model_id: CONFIG.model_id });
  });

  it('throws with both locations named when neither answers with JSON', async () => {
    // The message has to say where it looked, because it reaches a console with no surrounding
    // explanation and this failure spans the app, the deploy script and CloudFront. It stays to one
    // line: the gate in App.tsx renders its own hint, and duplicating it would print it twice.
    serve({
      '/config': response(SHELL, { contentType: 'text/html' }),
      './config.json': response(SHELL, { contentType: 'text/html' }),
    });
    await expect(fetchAppConfig()).rejects.toThrow(/\/config/);
    await expect(fetchAppConfig()).rejects.toThrow(/config\.json/);
    await expect(fetchAppConfig()).rejects.toThrow(/JSON/);
  });

  it('accepts a content type that carries parameters or a vendor suffix', async () => {
    // `application/json; charset=utf-8` and `application/vnd.api+json` are both JSON.
    serve({
      '/config': response(JSON.stringify(CONFIG), { contentType: 'application/vnd.api+JSON' }),
    });
    await expect(fetchAppConfig()).resolves.toMatchObject({ model_id: CONFIG.model_id });
  });
});
