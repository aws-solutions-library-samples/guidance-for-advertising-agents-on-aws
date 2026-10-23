/**
 * Cognito calls, made straight from the browser.
 *
 * No server proxy: Cognito's own endpoints send CORS headers that allow this, which is how the
 * vanilla UI works today. Ported from static/index.html with the behaviour intact, including the
 * details that were arrived at the hard way and are noted where they occur.
 */

import type { AuthResult, NewPasswordChallenge, SignInOutcome } from './types';

export interface CognitoConfig {
  region: string;
  clientId: string;
}

/**
 * A Cognito failure, carrying the service's own error type.
 *
 * The type (`InvalidPasswordException`, `NotAuthorizedException`, ...) decides which recovery the
 * UI offers, so it is preserved rather than flattened into a message string.
 */
export class CognitoError extends Error {
  readonly cognitoType: string;

  constructor(message: string, cognitoType: string) {
    super(message);
    this.name = 'CognitoError';
    this.cognitoType = cognitoType;
  }
}

interface CognitoErrorBody {
  message?: string;
  __type?: string;
}

async function cognitoCall<T>(
  config: CognitoConfig,
  target: string,
  body: unknown,
): Promise<T> {
  const res = await fetch(`https://cognito-idp.${config.region}.amazonaws.com/`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-amz-json-1.1',
      'X-Amz-Target': `AWSCognitoIdentityProviderService.${target}`,
    },
    body: JSON.stringify(body),
  });

  const data: unknown = await res.json();
  if (!res.ok) {
    const err = (data ?? {}) as CognitoErrorBody;
    // `__type` arrives as `com.amazonaws...#InvalidPasswordException`; only the last part is
    // the exception name.
    const type = (err.__type ?? '').split('#').pop() ?? '';
    throw new CognitoError(err.message || err.__type || `HTTP ${res.status}`, type);
  }
  return data as T;
}

interface InitiateAuthResponse {
  AuthenticationResult?: AuthResult;
  ChallengeName?: string;
  Session?: string;
  ChallengeParameters?: Record<string, string>;
}

/**
 * Signs in with username and password.
 *
 * Returns either a token set, or the NEW_PASSWORD_REQUIRED challenge. Both are normal outcomes:
 * an admin-created user starts in FORCE_CHANGE_PASSWORD, and Cognito hands back a session instead
 * of tokens until a permanent password is supplied.
 */
export async function cognitoSignIn(
  config: CognitoConfig,
  username: string,
  password: string,
): Promise<SignInOutcome> {
  const data = await cognitoCall<InitiateAuthResponse>(config, 'InitiateAuth', {
    AuthFlow: 'USER_PASSWORD_AUTH',
    ClientId: config.clientId,
    AuthParameters: { USERNAME: username, PASSWORD: password },
  });

  if (data.AuthenticationResult) {
    return { authResult: data.AuthenticationResult };
  }

  if (data.ChallengeName === 'NEW_PASSWORD_REQUIRED') {
    let requiredAttributes: string[] = [];
    try {
      const parsed: unknown = JSON.parse(data.ChallengeParameters?.['requiredAttributes'] ?? '[]');
      if (Array.isArray(parsed)) requiredAttributes = parsed.map(String);
    } catch {
      // A malformed attribute list is not worth failing the sign-in over; the challenge itself
      // is still answerable.
      requiredAttributes = [];
    }
    const challenge: NewPasswordChallenge = {
      name: 'NEW_PASSWORD_REQUIRED',
      session: data.Session ?? '',
      // `USER_ID_FOR_SRP` is the canonical username to echo back. It can differ from what was
      // typed (an alias, for instance), and answering with the typed value fails.
      username: data.ChallengeParameters?.['USER_ID_FOR_SRP'] || username,
      requiredAttributes,
    };
    return { challenge };
  }

  // Any other challenge (MFA, SRP, device confirmation) genuinely is not implemented. Name the
  // one that came back rather than failing vaguely.
  throw new Error(
    data.ChallengeName
      ? `Unsupported challenge: ${data.ChallengeName}`
      : 'Sign-in did not return tokens.',
  );
}

/**
 * Answers NEW_PASSWORD_REQUIRED. On success Cognito makes the password permanent, moving the user
 * out of FORCE_CHANGE_PASSWORD, and returns the same token set a normal sign-in would.
 */
export async function cognitoSetNewPassword(
  config: CognitoConfig,
  challenge: NewPasswordChallenge,
  newPassword: string,
): Promise<AuthResult> {
  const data = await cognitoCall<InitiateAuthResponse>(config, 'RespondToAuthChallenge', {
    ChallengeName: 'NEW_PASSWORD_REQUIRED',
    ClientId: config.clientId,
    Session: challenge.session,
    ChallengeResponses: { USERNAME: challenge.username, NEW_PASSWORD: newPassword },
  });

  if (!data.AuthenticationResult) {
    throw new Error(
      data.ChallengeName
        ? `Password set, but another challenge followed: ${data.ChallengeName}`
        : 'Password change did not return tokens.',
    );
  }
  return data.AuthenticationResult;
}

/**
 * The `cognito:groups` claim from an access token.
 *
 * Decoded, not verified: this only decides what the UI offers, and the runtime authorises every
 * request server-side regardless. A token that cannot be decoded yields no groups, so the effect
 * of any confusion is fewer options rather than more.
 */
export function tokenGroups(accessToken: string | null | undefined): string[] {
  if (!accessToken) return [];
  try {
    const segment = accessToken.split('.')[1];
    if (!segment) return [];
    const json = atob(segment.replace(/-/g, '+').replace(/_/g, '/'));
    const claims: unknown = JSON.parse(json);
    const groups = (claims as Record<string, unknown> | null)?.['cognito:groups'];
    if (Array.isArray(groups)) return groups.map(String);
    if (typeof groups === 'string') return groups.split(/[,\s]+/).filter(Boolean);
    return [];
  } catch {
    return [];
  }
}

export function isAdminUser(accessToken: string | null | undefined): boolean {
  return tokenGroups(accessToken).includes('admin');
}

export interface PasswordRules {
  length: boolean;
  upper: boolean;
  lower: boolean;
  digit: boolean;
  match: boolean;
}

/**
 * Which password rules a candidate satisfies.
 *
 * Mirrors the pool's own policy so the form can show progress, but it is not the authority:
 * Cognito rejects a non-compliant password regardless, and its error is what the UI reports.
 */
export function evaluatePasswordRules(password: string, confirm: string): PasswordRules {
  return {
    length: password.length >= 8,
    upper: /[A-Z]/.test(password),
    lower: /[a-z]/.test(password),
    digit: /[0-9]/.test(password),
    match: password.length > 0 && password === confirm,
  };
}

export function passwordRulesMet(rules: PasswordRules): boolean {
  return Object.values(rules).every(Boolean);
}
