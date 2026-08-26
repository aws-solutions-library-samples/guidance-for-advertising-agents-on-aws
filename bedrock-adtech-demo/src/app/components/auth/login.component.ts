import { Component, OnInit } from '@angular/core';
import { Router } from '@angular/router';
import { AwsConfigService } from '../../services/aws-config.service';
import { SsoConfig } from '../../models/application-models';

@Component({
  selector: 'app-login',
  templateUrl: './login.component.html',
  styleUrls: ['./login.component.scss']
})
export class LoginComponent implements OnInit {
  /** aws-config.json loads async; poll for up to 5s before giving up. */
  private static readonly CONFIG_WAIT_ATTEMPTS = 10;
  private static readonly CONFIG_WAIT_INTERVAL_MS = 500;

  email = '';
  password = '';
  newPassword = '';
  confirmPassword = '';
  isLoading = false;
  error = '';
  showNewPasswordForm = false;
  newPasswordSession: any = null;

  // Federated sign-in, driven entirely by the `sso` block in aws-config.json.
  // No provider or domain is hardcoded here.
  ssoEnabled = false;
  ssoLabel = 'Sign in with SSO';
  private ssoConfig: SsoConfig | null = null;

  constructor(
    private awsConfig: AwsConfigService,
    private router: Router
  ) {}

  ngOnInit() {
    this.loadSsoConfig();

    // Returning from the identity provider: the authorization code is in the query
    // string. Handle it instead of the already-authenticated check below, because
    // there is no session yet.
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.has('code')) {
      this.isLoading = true;
      this.waitForConfigThenHandleCallback(urlParams.get('code')!);
      return;
    }

    // Check if user is already authenticated
    this.awsConfig.user$.subscribe(user => {
      if (user) {
        this.router.navigate(['/campaign-planning']);
      }
    });
  }

  /** Read the SSO block once config has loaded. Absent or disabled means no button. */
  private async loadSsoConfig(): Promise<void> {
    const sso = await this.waitForSsoConfig();
    if (sso?.enabled) {
      this.ssoEnabled = true;
      this.ssoLabel = sso.label || 'Sign in with SSO';
      this.ssoConfig = sso;
    }
  }

  /**
   * aws-config.json loads asynchronously, so poll briefly for it.
   * Resolves to the SSO block, or null if config never arrives or carries no SSO.
   */
  private async waitForSsoConfig(): Promise<SsoConfig | null> {
    for (let attempt = 0; attempt < LoginComponent.CONFIG_WAIT_ATTEMPTS; attempt++) {
      const config = this.awsConfig.getConfig();
      if (config) {
        return config.sso ?? null;
      }
      await new Promise(resolve => setTimeout(resolve, LoginComponent.CONFIG_WAIT_INTERVAL_MS));
    }
    return null;
  }

  private async waitForConfigThenHandleCallback(code: string): Promise<void> {
    const sso = await this.waitForSsoConfig();
    if (!sso?.cognitoDomain) {
      // Distinguish the two failures: config never loaded, versus loaded without SSO.
      this.error = this.awsConfig.getConfig()
        ? 'SSO is not configured for this deployment. Please sign in with email.'
        : 'Configuration did not load. Please refresh and try again.';
      this.isLoading = false;
      return;
    }
    this.handleOAuthCallback(code, sso);
  }

  async signInWithSSO(): Promise<void> {
    const config = this.awsConfig.getConfig();
    if (!this.ssoConfig || !config) {
      return;
    }

    this.isLoading = true;
    this.error = '';

    const clientId = config.aws?.cognito?.userPoolWebClientId;
    const redirectUri = encodeURIComponent(window.location.origin + '/');
    const url =
      `https://${this.ssoConfig.cognitoDomain}/oauth2/authorize` +
      `?response_type=code&client_id=${clientId}` +
      `&redirect_uri=${redirectUri}` +
      `&identity_provider=${encodeURIComponent(this.ssoConfig.providerName)}` +
      `&scope=openid+email+profile`;
    window.location.href = url;
  }

  private async handleOAuthCallback(code: string, sso: SsoConfig): Promise<void> {
    try {
      const config = this.awsConfig.getConfig();
      const clientId = config?.aws?.cognito?.userPoolWebClientId;
      if (!clientId) {
        this.error = 'SSO authentication failed. Please try again.';
        this.isLoading = false;
        return;
      }
      const redirectUri = window.location.origin + '/';

      const response = await fetch(`https://${sso.cognitoDomain}/oauth2/token`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({
          grant_type: 'authorization_code',
          client_id: clientId,
          code: code,
          redirect_uri: redirectUri,
        }),
      });

      if (!response.ok) {
        // Log the status, not the body: a failed token response can echo request
        // parameters.
        console.error('SSO token exchange failed with status', response.status);
        this.error = 'SSO authentication failed. Please try again.';
        this.isLoading = false;
        return;
      }

      const tokens = await response.json();

      // Store under Amplify's key names so it picks the session up on reload.
      // The username comes from the id token payload. That payload is read without
      // verifying the signature, which is safe here specifically because the token
      // arrived directly from the Cognito token endpoint over TLS in response to a
      // code this application initiated — it is not attacker-supplied input. Do not
      // reuse this pattern where the token comes from somewhere else.
      const keyPrefix = `CognitoIdentityServiceProvider.${clientId}`;
      const idPayload = JSON.parse(atob(tokens.id_token.split('.')[1]));
      const username = idPayload.sub || idPayload.email || 'sso-user';

      localStorage.setItem(`${keyPrefix}.LastAuthUser`, username);
      localStorage.setItem(`${keyPrefix}.${username}.idToken`, tokens.id_token);
      localStorage.setItem(`${keyPrefix}.${username}.accessToken`, tokens.access_token);
      if (tokens.refresh_token) {
        localStorage.setItem(`${keyPrefix}.${username}.refreshToken`, tokens.refresh_token);
      }

      // Drop the code from the URL so a refresh cannot replay it, then reload so
      // Amplify reads the stored tokens.
      window.history.replaceState({}, '', '/');
      window.location.reload();
    } catch (error: any) {
      console.error('SSO callback error:', error?.name || 'unknown error');
      this.error = 'SSO authentication failed. Please try again.';
      this.isLoading = false;
    }
  }

  async signIn() {
    if (!this.email || !this.password) {
      this.error = 'Please enter both email and password';
      return;
    }

    this.isLoading = true;
    this.error = '';

    try {
      const result = await this.awsConfig.signIn(this.email, this.password);
      
      if (result.challengeName === 'NEW_PASSWORD_REQUIRED') {
        this.showNewPasswordForm = true;
        this.newPasswordSession = result.session;
      } else {
        // Successfully signed in - wait for credentials to be available
        await this.waitForCredentials();
        this.router.navigate(['/']);
      }
    } catch (error: any) {
      console.error('Sign in error:', error);
      this.error = this.getErrorMessage(error);
    } finally {
      this.isLoading = false;
    }
  }

  async completeNewPassword() {
    if (!this.newPassword || !this.confirmPassword) {
      this.error = 'Please enter both password fields';
      return;
    }

    if (this.newPassword !== this.confirmPassword) {
      this.error = 'Passwords do not match';
      return;
    }

    if (this.newPassword.length < 8) {
      this.error = 'Password must be at least 8 characters long';
      return;
    }

    this.isLoading = true;
    this.error = '';

    try {
      await this.awsConfig.completeNewPassword(this.newPasswordSession, this.newPassword);
      // Wait for credentials to be available after password change
      await this.waitForCredentials();
      this.router.navigate(['/']);
    } catch (error: any) {
      console.error('New password error:', error);
      this.error = this.getErrorMessage(error);
    } finally {
      this.isLoading = false;
    }
  }

  // Wait for AWS credentials to be properly established
  private async waitForCredentials(): Promise<void> {
    const maxAttempts = 10;
    const delay = 500; // 500ms between attempts
    
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
      try {
        const awsConfig = await this.awsConfig.getAwsConfig();
        if (awsConfig && awsConfig.credentials && 
            (awsConfig.credentials.accessKeyId || awsConfig.credentials.sessionToken)) {
          return;
        }
      } catch (error) {
      }
      
      if (attempt < maxAttempts) {
        await new Promise(resolve => setTimeout(resolve, delay));
      }
    }
    
    console.warn('⚠️ Proceeding without full credential verification - some features may not work initially');
  }

  private getErrorMessage(error: any): string {
    if (error.name === 'NotAuthorizedException') {
      return 'Invalid email or password';
    } else if (error.name === 'UserNotFoundException') {
      return 'User not found';
    } else if (error.name === 'InvalidPasswordException') {
      return 'Password does not meet requirements';
    } else if (error.name === 'TooManyRequestsException') {
      return 'Too many attempts. Please try again later';
    } else if (error.message) {
      return error.message;
    } else {
      return 'An error occurred during sign in';
    }
  }

  onKeyPress(event: KeyboardEvent) {
    if (event.key === 'Enter') {
      if (this.showNewPasswordForm) {
        this.completeNewPassword();
      } else {
        this.signIn();
      }
    }
  }
} 