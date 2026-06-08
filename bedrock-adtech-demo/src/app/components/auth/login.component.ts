import { Component, OnInit } from '@angular/core';
import { Router } from '@angular/router';
import { AwsConfigService } from '../../services/aws-config.service';

@Component({
  selector: 'app-login',
  templateUrl: './login.component.html',
  styleUrls: ['./login.component.scss']
})
export class LoginComponent implements OnInit {
  email = '';
  password = '';
  newPassword = '';
  confirmPassword = '';
  isLoading = false;
  error = '';
  showNewPasswordForm = false;
  newPasswordSession: any = null;

  // SSO config — loaded from aws-config.json
  ssoEnabled = false;
  ssoLabel = 'Sign in with SSO';
  private ssoConfig: any = null;

  constructor(
    private awsConfig: AwsConfigService,
    private router: Router
  ) {}

  ngOnInit() {
    // Load SSO config from aws-config.json
    this.loadSsoConfig();

    // Handle OAuth callback (code in URL from SSO redirect)
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.has('code')) {
      this.isLoading = true;
      // Wait for config to load before processing callback
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

  private async waitForConfigThenHandleCallback(code: string) {
    // Retry getting config for up to 5 seconds (config loads async from aws-config.json)
    for (let i = 0; i < 10; i++) {
      const config = this.awsConfig.getConfig();
      if (config && (config as any).sso) {
        this.handleOAuthCallback(code);
        return;
      }
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    this.error = 'Configuration not loaded. Please refresh.';
    this.isLoading = false;
  }

  private async loadSsoConfig() {
    // Retry for up to 3 seconds (config loads async)
    for (let i = 0; i < 6; i++) {
      try {
        const config = this.awsConfig.getConfig();
        if (!config) {
          await new Promise(resolve => setTimeout(resolve, 500));
          continue;
        }
        const sso = (config as any).sso;
        if (sso && sso.enabled) {
          this.ssoEnabled = true;
          this.ssoLabel = sso.label || 'Sign in with SSO';
          this.ssoConfig = sso;
        }
        return;
      } catch (e) {
        await new Promise(resolve => setTimeout(resolve, 500));
      }
    }
  }

  async signInWithSSO() {
    if (!this.ssoConfig) return;
    this.isLoading = true;
    this.error = '';

    const config = this.awsConfig.getConfig();
    if (!config) return;
    const domain = this.ssoConfig.cognitoDomain;
    const clientId = (config as any).aws?.cognito?.userPoolWebClientId;
    const redirectUri = encodeURIComponent(window.location.origin + '/');
    const idpName = this.ssoConfig.providerName;
    const url = `https://${domain}/oauth2/authorize?response_type=code&client_id=${clientId}&redirect_uri=${redirectUri}&identity_provider=${idpName}&scope=openid+email+profile`;
    window.location.href = url;
  }

  private async handleOAuthCallback(code: string) {
    try {
      const config = this.awsConfig.getConfig();
      if (!config) {
        this.error = 'Configuration not loaded. Please refresh.';
        this.isLoading = false;
        return;
      }
      const sso = (config as any).sso;
      if (!sso || !sso.cognitoDomain) {
        this.error = 'SSO not configured. Please use email/password.';
        this.isLoading = false;
        return;
      }

      const domain = sso.cognitoDomain;
      const clientId = (config as any).aws?.cognito?.userPoolWebClientId;
      const redirectUri = window.location.origin + '/';

      const response = await fetch(`https://${domain}/oauth2/token`, {
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
        const errorText = await response.text();
        console.error('Token exchange failed:', errorText);
        this.error = 'SSO authentication failed. Please try again.';
        this.isLoading = false;
        return;
      }

      const tokens = await response.json();
      console.log('SSO tokens received:', Object.keys(tokens));

      // Store tokens in localStorage for Amplify to pick up
      const keyPrefix = `CognitoIdentityServiceProvider.${clientId}`;
      const idPayload = JSON.parse(atob(tokens.id_token.split('.')[1]));
      const username = idPayload.sub || idPayload.email || 'sso-user';

      localStorage.setItem(`${keyPrefix}.LastAuthUser`, username);
      localStorage.setItem(`${keyPrefix}.${username}.idToken`, tokens.id_token);
      localStorage.setItem(`${keyPrefix}.${username}.accessToken`, tokens.access_token);
      if (tokens.refresh_token) {
        localStorage.setItem(`${keyPrefix}.${username}.refreshToken`, tokens.refresh_token);
      }

      // Clear the code from URL and reload to let Amplify pick up the tokens
      window.history.replaceState({}, '', '/');
      window.location.reload();
    } catch (error: any) {
      console.error('OAuth callback error:', error);
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
      await this.waitForCredentials();
      this.router.navigate(['/']);
    } catch (error: any) {
      console.error('New password error:', error);
      this.error = this.getErrorMessage(error);
    } finally {
      this.isLoading = false;
    }
  }

  private async waitForCredentials(): Promise<void> {
    const maxAttempts = 10;
    const delay = 500;
    
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
    
    console.warn('Proceeding without full credential verification');
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
