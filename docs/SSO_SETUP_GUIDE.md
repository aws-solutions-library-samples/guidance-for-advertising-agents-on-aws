# SSO Setup Guide

Enable Single Sign-On (SSO) for the Angular UI using any OIDC-compatible identity provider (Okta, Auth0, Azure AD, AWS IAM Identity Center, etc.).

## Prerequisites

- A deployed A4A stack (Phases 1-12 complete)
- Access to your OIDC identity provider's admin console
- AWS CLI configured with permissions to modify Cognito

## Overview

The SSO flow uses Cognito as a federation broker:

```
Angular UI → Cognito Hosted UI → Your IDP (login) → Cognito → Angular UI (authenticated)
```

The Angular UI code is already SSO-ready — you just need to configure the IDP connection and regenerate the UI config.

---

## Step 1: Note Your Cognito Domain

Your Cognito domain was created during initial deployment. Find it:

```bash
aws cognito-idp describe-user-pool \
  --user-pool-id <USER_POOL_ID> \
  --region <REGION> \
  --query "UserPool.Domain" --output text
```

Your Cognito callback URL (needed in Step 2):
```
https://<DOMAIN>.auth.<REGION>.amazoncognito.com/oauth2/idpresponse
```

---

## Step 2: Create a Service Profile in Your IDP

In your identity provider's admin console, create a new OIDC application/client:

| Setting | Value |
|---------|-------|
| **Protocol** | OIDC (OpenID Connect) |
| **Grant Type** | Authorization Code |
| **Redirect URI** | `https://<DOMAIN>.auth.<REGION>.amazoncognito.com/oauth2/idpresponse` |
| **Scopes** | `openid`, `email`, `profile` |

After creation, note:
- **Client ID** — e.g., `my-app-client-id`
- **Client Secret** — e.g., `abc123...`
- **Issuer URL** — e.g., `https://login.example.com` or `https://your-tenant.okta.com`

---

## Step 3: Add the IDP to Cognito

```bash
aws cognito-idp create-identity-provider \
  --user-pool-id <USER_POOL_ID> \
  --provider-name <IDP_NAME> \
  --provider-type OIDC \
  --provider-details '{
    "client_id": "<CLIENT_ID>",
    "client_secret": "<CLIENT_SECRET>",
    "oidc_issuer": "<ISSUER_URL>",
    "authorize_scopes": "openid email profile",
    "attributes_request_method": "GET"
  }' \
  --attribute-mapping '{"email": "email", "username": "sub"}' \
  --region <REGION>
```

Replace:
- `<IDP_NAME>` — A short name (e.g., `OktaOIDC`, `AzureAD`, `MyCompanySSO`)
- `<CLIENT_ID>` — From Step 2
- `<CLIENT_SECRET>` — From Step 2
- `<ISSUER_URL>` — From Step 2

---

## Step 4: Update the Cognito App Client

Add the IDP to the client's supported providers and add your CloudFront URL to callbacks:

```bash
# Get your CloudFront domain
CF_DOMAIN=$(aws cloudfront get-distribution \
  --id <CLOUDFRONT_DISTRIBUTION_ID> \
  --query "Distribution.DomainName" --output text)

# Update the client
aws cognito-idp update-user-pool-client \
  --user-pool-id <USER_POOL_ID> \
  --client-id <CLIENT_ID> \
  --region <REGION> \
  --supported-identity-providers COGNITO <IDP_NAME> \
  --callback-urls \
    "http://localhost:4200" "http://localhost:4200/" \
    "https://${CF_DOMAIN}" "https://${CF_DOMAIN}/" \
  --logout-urls \
    "http://localhost:4200" "http://localhost:4200/" \
    "https://${CF_DOMAIN}" "https://${CF_DOMAIN}/" \
  --allowed-o-auth-flows code implicit \
  --allowed-o-auth-scopes email openid profile \
  --allowed-o-auth-flows-user-pool-client \
  --explicit-auth-flows ALLOW_REFRESH_TOKEN_AUTH ALLOW_USER_SRP_AUTH
```

---

## Step 5: Regenerate the UI Config

```bash
python scripts/generate_aws_config.py generate \
  --prefix <STACK_PREFIX> \
  --suffix <UNIQUE_ID> \
  --region <REGION> \
  --profile <AWS_PROFILE> \
  --sso-provider <IDP_NAME> \
  --sso-label "Sign in with SSO"
```

This adds the `sso` section to `aws-config.json`:
```json
{
  "sso": {
    "enabled": true,
    "providerName": "<IDP_NAME>",
    "label": "Sign in with SSO",
    "cognitoDomain": "<DOMAIN>.auth.<REGION>.amazoncognito.com"
  }
}
```

---

## Step 6: Rebuild and Deploy the UI

```bash
cd bedrock-adtech-demo
ng build --configuration production
aws s3 sync dist/bedrock-adtech-demo s3://<UI_BUCKET>/ --delete
aws cloudfront create-invalidation --distribution-id <CF_ID> --paths "/*"
```

---

## Result

The login page now shows:
1. **SSO button** (primary) — redirects to your IDP
2. **Email/password form** (secondary) — for admin/demo accounts

The user's email from the IDP token is displayed in the top bar after login.

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `redirect_mismatch` error | Verify the redirect URI in your IDP matches exactly: `https://<domain>.auth.<region>.amazoncognito.com/oauth2/idpresponse` |
| SSO button not showing | Check that `aws-config.json` has the `sso` section with `enabled: true` |
| "Configuration not loaded" | The config file hasn't loaded yet — refresh the page |
| "User cancelled OAuth flow" | Clear localStorage and try again (PKCE state mismatch) |
| Login works but no AWS credentials | Verify the Identity Pool's `CognitoIdentityProviders` includes your User Pool |
| Email not showing in top bar | The IDP must return `email` claim in the ID token |

---

## Removing SSO

To disable SSO without removing the IDP:

1. Re-run `generate_aws_config.py` without `--sso-provider` (omit the flag)
2. Rebuild and deploy the UI
3. The SSO button disappears; email/password login remains

To fully remove:
```bash
aws cognito-idp delete-identity-provider \
  --user-pool-id <USER_POOL_ID> \
  --provider-name <IDP_NAME> \
  --region <REGION>
```
