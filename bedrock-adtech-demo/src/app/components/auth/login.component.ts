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

  constructor(
    private awsConfig: AwsConfigService,
    private router: Router
  ) {}

  ngOnInit() {
    // Check if user is already authenticated
    this.awsConfig.user$.subscribe(user => {
      if (user) {
        this.router.navigate(['/']);
      }
    });
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