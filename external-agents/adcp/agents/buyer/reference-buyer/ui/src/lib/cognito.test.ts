import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  CognitoError,
  cognitoSetNewPassword,
  cognitoSignIn,
  evaluatePasswordRules,
  isAdminUser,
  passwordRulesMet,
  tokenGroups,
} from './cognito';
import type { NewPasswordChallenge } from './types';

const config = { region: 'us-east-1', clientId: 'abc123' };

/** Stubs one fetch response. */
function stubFetch(body: unknown, ok = true, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok,
    status,
    json: async () => body,
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

/**
 * Builds an unsigned JWT with the given claims. Decoded, never verified, by tokenGroups.
 *
 * Uses `btoa` rather than node's Buffer so the test exercises the same base64url handling the
 * browser does, including the `-`/`_` substitution tokenGroups has to undo.
 */
function jwtWith(claims: Record<string, unknown>): string {
  const b64url = (s: string) =>
    btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${b64url('{"alg":"none"}')}.${b64url(JSON.stringify(claims))}.sig`;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('cognitoSignIn', () => {
  it('returns the token set on a completed sign-in', async () => {
    stubFetch({ AuthenticationResult: { AccessToken: 'tok' } });
    const outcome = await cognitoSignIn(config, 'sam', 'pw');
    expect(outcome.authResult?.AccessToken).toBe('tok');
    expect(outcome.challenge).toBeUndefined();
  });

  it('returns the NEW_PASSWORD_REQUIRED challenge rather than treating it as failure', async () => {
    // An admin-created user starts in FORCE_CHANGE_PASSWORD, so this is a normal path.
    stubFetch({
      ChallengeName: 'NEW_PASSWORD_REQUIRED',
      Session: 'sess-1',
      ChallengeParameters: { USER_ID_FOR_SRP: 'canonical-sam', requiredAttributes: '["email"]' },
    });
    const outcome = await cognitoSignIn(config, 'sam', 'temp');
    expect(outcome.challenge?.name).toBe('NEW_PASSWORD_REQUIRED');
    expect(outcome.challenge?.session).toBe('sess-1');
    expect(outcome.challenge?.requiredAttributes).toEqual(['email']);
  });

  it('echoes back the canonical username, not what was typed', async () => {
    // Answering the challenge with the typed value fails when it was an alias.
    stubFetch({
      ChallengeName: 'NEW_PASSWORD_REQUIRED',
      Session: 's',
      ChallengeParameters: { USER_ID_FOR_SRP: 'canonical-sam' },
    });
    const outcome = await cognitoSignIn(config, 'sam@example.com', 'temp');
    expect(outcome.challenge?.username).toBe('canonical-sam');
  });

  it('falls back to the typed username when Cognito names none', async () => {
    stubFetch({ ChallengeName: 'NEW_PASSWORD_REQUIRED', Session: 's' });
    const outcome = await cognitoSignIn(config, 'sam', 'temp');
    expect(outcome.challenge?.username).toBe('sam');
  });

  it('survives a malformed requiredAttributes rather than failing the sign-in', async () => {
    stubFetch({
      ChallengeName: 'NEW_PASSWORD_REQUIRED',
      Session: 's',
      ChallengeParameters: { requiredAttributes: 'not json' },
    });
    const outcome = await cognitoSignIn(config, 'sam', 'temp');
    expect(outcome.challenge?.requiredAttributes).toEqual([]);
  });

  it('names an unsupported challenge instead of failing vaguely', async () => {
    stubFetch({ ChallengeName: 'SMS_MFA' });
    await expect(cognitoSignIn(config, 'sam', 'pw')).rejects.toThrow(/SMS_MFA/);
  });

  it('reports a response that carries neither tokens nor a challenge', async () => {
    stubFetch({});
    await expect(cognitoSignIn(config, 'sam', 'pw')).rejects.toThrow(/did not return tokens/);
  });

  it('preserves the Cognito error type, which decides the recovery path', async () => {
    stubFetch(
      { __type: 'com.amazonaws.cognito.identity#NotAuthorizedException', message: 'Incorrect username or password.' },
      false,
      400,
    );
    await expect(cognitoSignIn(config, 'sam', 'wrong')).rejects.toMatchObject({
      cognitoType: 'NotAuthorizedException',
      message: 'Incorrect username or password.',
    });
  });

  it('still reports something useful when the error body has no message', async () => {
    stubFetch({}, false, 503);
    await expect(cognitoSignIn(config, 'sam', 'pw')).rejects.toThrow(/HTTP 503/);
  });

  it('calls the pool region endpoint with the JSON 1.1 protocol headers', async () => {
    const fetchMock = stubFetch({ AuthenticationResult: { AccessToken: 't' } });
    await cognitoSignIn(config, 'sam', 'pw');
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('https://cognito-idp.us-east-1.amazonaws.com/');
    expect((init.headers as Record<string, string>)['X-Amz-Target']).toBe(
      'AWSCognitoIdentityProviderService.InitiateAuth',
    );
  });
});

describe('cognitoSetNewPassword', () => {
  const challenge: NewPasswordChallenge = {
    name: 'NEW_PASSWORD_REQUIRED',
    session: 'sess-1',
    username: 'canonical-sam',
    requiredAttributes: [],
  };

  it('returns the token set once the password is accepted', async () => {
    stubFetch({ AuthenticationResult: { AccessToken: 'tok2' } });
    const result = await cognitoSetNewPassword(config, challenge, 'Str0ngPass');
    expect(result.AccessToken).toBe('tok2');
  });

  it('reports a further challenge distinctly from an outright failure', async () => {
    stubFetch({ ChallengeName: 'MFA_SETUP' });
    await expect(cognitoSetNewPassword(config, challenge, 'Str0ngPass')).rejects.toThrow(
      /Password set, but another challenge followed: MFA_SETUP/,
    );
  });

  it('surfaces a rejected password with its Cognito type', async () => {
    stubFetch({ __type: 'x#InvalidPasswordException', message: 'Password not long enough' }, false, 400);
    await expect(cognitoSetNewPassword(config, challenge, 'short')).rejects.toBeInstanceOf(
      CognitoError,
    );
  });
});

describe('tokenGroups', () => {
  it('reads a group array', () => {
    expect(tokenGroups(jwtWith({ 'cognito:groups': ['admin', 'ops'] }))).toEqual(['admin', 'ops']);
  });

  it('reads a delimited group string', () => {
    expect(tokenGroups(jwtWith({ 'cognito:groups': 'admin ops' }))).toEqual(['admin', 'ops']);
  });

  it('returns nothing for a token with no groups', () => {
    expect(tokenGroups(jwtWith({ sub: 'x' }))).toEqual([]);
  });

  it('returns nothing rather than throwing for an undecodable token', () => {
    // The effect of confusion here must be fewer options, never more; the runtime authorises
    // every request server-side regardless.
    expect(tokenGroups('not-a-jwt')).toEqual([]);
    expect(tokenGroups('')).toEqual([]);
    expect(tokenGroups(null)).toEqual([]);
    expect(tokenGroups(undefined)).toEqual([]);
  });
});

describe('isAdminUser', () => {
  it('is true only for a token carrying the admin group', () => {
    expect(isAdminUser(jwtWith({ 'cognito:groups': ['admin'] }))).toBe(true);
    expect(isAdminUser(jwtWith({ 'cognito:groups': ['ops'] }))).toBe(false);
    expect(isAdminUser(null)).toBe(false);
  });
});

describe('evaluatePasswordRules', () => {
  it('reports each rule independently', () => {
    expect(evaluatePasswordRules('Str0ngPass', 'Str0ngPass')).toEqual({
      length: true,
      upper: true,
      lower: true,
      digit: true,
      match: true,
    });
  });

  it('fails match when the confirmation differs', () => {
    expect(evaluatePasswordRules('Str0ngPass', 'Str0ngPas').match).toBe(false);
  });

  it('does not count two empty fields as matching', () => {
    expect(evaluatePasswordRules('', '').match).toBe(false);
  });

  it('identifies exactly which rule is unmet', () => {
    expect(evaluatePasswordRules('str0ngpass', 'str0ngpass').upper).toBe(false);
    expect(evaluatePasswordRules('STR0NGPASS', 'STR0NGPASS').lower).toBe(false);
    expect(evaluatePasswordRules('StrongPass', 'StrongPass').digit).toBe(false);
    expect(evaluatePasswordRules('Sh0rt', 'Sh0rt').length).toBe(false);
  });
});

describe('passwordRulesMet', () => {
  it('requires every rule', () => {
    expect(passwordRulesMet(evaluatePasswordRules('Str0ngPass', 'Str0ngPass'))).toBe(true);
    expect(passwordRulesMet(evaluatePasswordRules('Str0ngPass', 'other'))).toBe(false);
  });
});
