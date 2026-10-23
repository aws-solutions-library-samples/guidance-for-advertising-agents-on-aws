/**
 * Loading the app's configuration.
 *
 * The same build is served two ways, so the config is fetched two ways: `/config` is a live route
 * app.py serves during local development, and `./config.json` is a static file deploy_ui.py
 * generates from the same .env values on every publish. `/config` is tried first because it is the
 * richer local path; on any non-OK response the static file is used.
 *
 * ## Why the check is on the CONTENT TYPE and not only on the status
 *
 * This is the interesting part, and it is the fix for a real outage of this screen.
 *
 * The original version treated `res.ok` as "this is the config" and fell back only on a non-OK
 * status. That worked because under CloudFront `/config` returned **403**: the path is not an object
 * in the bucket, and with an OAC policy granting `s3:GetObject` but not `s3:ListBucket`, S3 answers a
 * missing key with AccessDenied rather than 404.
 *
 * Then the distribution gained a client-side-routing rewrite mapping **403 and 404 to `/index.html`
 * with status 200**, so a refresh on `/chat` would work. Both changes were correct on their own.
 * Together they broke this function: `/config` began answering **200 with the HTML app shell**, the
 * fallback never ran, `res.json()` failed on `<!doctype`, and the app could not start at all.
 *
 * The lesson worth keeping next to the code: **a catch-all rewrite makes "not found" unobservable
 * from the status line.** Anything that probed for absence by status has to probe by content instead.
 * A 200 is now the answer to every request, so it has stopped being evidence of anything.
 */

import { runtimeSessionId } from './session';
import type { AppConfig } from './types';

/**
 * Fetch one candidate location, returning null when it did not answer with usable JSON.
 *
 * Null rather than throwing, because "this location is not the config" is an ordinary step in the
 * search rather than a failure. A wrong content type, an unparseable body and a non-OK status are all
 * the same fact here: keep looking.
 */
async function readConfigFrom(url: string): Promise<AppConfig | null> {
  let res: Response;
  try {
    res = await fetch(url);
  } catch {
    // Network-level failure for this candidate. The other one may still answer.
    return null;
  }
  if (!res.ok) return null;

  // The guard. An SPA rewrite answers with the app shell and a 200, so the status cannot be trusted
  // to mean "this is the thing you asked for".
  //
  // Read defensively: a response object without usable headers is treated as "not the config" rather
  // than throwing. This function's whole job is to survive a location that answers unhelpfully, so
  // failing on a malformed response would defeat it at the one moment it matters.
  const contentType = res.headers?.get?.('content-type') ?? '';
  if (!contentType.toLowerCase().includes('json')) return null;

  try {
    return (await res.json()) as AppConfig;
  } catch {
    // Declared as JSON and is not. Still not the config.
    return null;
  }
}

/** Candidate locations, in order of preference. */
const CONFIG_URLS = ['/config', './config.json'] as const;

export async function fetchAppConfig(): Promise<AppConfig> {
  for (const url of CONFIG_URLS) {
    const config = await readConfigFrom(url);
    if (config !== null) return config;
  }
  // Names both locations, because this string reaches a console and a log where there is no
  // surrounding explanation. The gate in App.tsx renders its own hint beside it, so this stays to one
  // line rather than repeating that hint on screen.
  throw new Error(`neither ${CONFIG_URLS.join(' nor ')} answered with JSON`);
}

/**
 * The invoke URL for the deployed AgentCore Runtime.
 *
 * The browser calls this endpoint directly rather than routing through our own server; AWS sends
 * `Access-Control-Allow-Origin: *` on it, which is verified live and is what makes that possible.
 */
export function invokeUrl(config: Pick<AppConfig, 'agent_runtime_arn' | 'aws_region'>): string {
  if (!config.agent_runtime_arn) {
    throw new Error('No AGENT_RUNTIME_ARN configured on the server (.env).');
  }
  const arn = encodeURIComponent(config.agent_runtime_arn);
  return `https://bedrock-agentcore.${config.aws_region}.amazonaws.com/runtimes/${arn}/invocations?qualifier=DEFAULT`;
}

/**
 * The session id for AgentCore's runtime-session header.
 *
 * One per tab, shared by the conversation and by every JSON action, because a tab holds one
 * conversation. `newRuntimeSessionId()` is how a new one is started, deliberately.
 */
export function actionSessionId(): string {
  // Kept as a re-export of the tab's one runtime session id, NOT a fresh uuid. A tab has one
  // conversation and therefore one session id; JSON actions are reads against that same session, not
  // separate conversations.
  //
  // The fresh-uuid version was measurably expensive. Every JSON action goes through `useInvoke`, so
  // the 1s step poll minted a new runtime session on every tick. Measured against the deployed
  // runtime, same session, ten polls each:
  //
  //     fresh id per poll    p50 1079 ms   p90 9929 ms   max 9955 ms
  //     one id, reused       p50  518 ms   p90  618 ms   max 1075 ms
  //     no header at all     p50  930 ms   p90  959 ms   max  976 ms
  //
  // The p90 is the point: a ~10s poll behind a single-flight guard freezes the dashboard for ten
  // seconds, which is what "slow to pick up changes" actually was.
  return runtimeSessionId();
}

/** How the loaded build should be described, for the build stamp. */
export function describeBuild(publishedAt: string | undefined): string {
  if (publishedAt === undefined) {
    // A config written before the field existed. Different from "served locally", and only one of
    // the two is actionable, so they are not collapsed.
    return 'build unknown (config predates the build stamp)';
  }
  if (publishedAt === '') return 'served locally';
  const when = new Date(publishedAt);
  return Number.isNaN(when.getTime())
    ? `build ${publishedAt}`
    : `build ${when.toLocaleString()}`;
}
