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
  isLoading = false;
  error = '';
  success = '';
  needsNewPassword = false;
  currentUser: any = null;
  showDemoCredentials = false;

  constructor(
    private awsConfig: AwsConfigService,
    private router: Router
  ) {}

  ngOnInit(): void {
    // Check if user is already authenticated
    this.awsConfig.user$.subscribe(user => {
      if (user && !this.needsNewPassword) {
        this.router.navigate(['/']);
      }
    });
  }

  async signIn(): Promise<void> {
    if (!this.email || !this.password) {
      this.error = 'Please enter both email and password';
      return;
    }

    this.isLoading = true;
    this.error = '';
    this.success = '';

    try {
      const user = await this.awsConfig.signIn(this.email, this.password);
      
      if (user.challengeName === 'NEW_PASSWORD_REQUIRED') {
        this.needsNewPassword = true;
        this.currentUser = user.session;
        this.success = 'Please set a new permanent password';
      } else {
        this.success = 'Successfully signed in!';
        setTimeout(() => {
          this.router.navigate(['/']);
        }, 1000);
      }
    } catch (error: any) {
      console.error('Sign in error:', error);
      this.handleAuthError(error);
    } finally {
      this.isLoading = false;
    }
  }

  async setNewPassword(): Promise<void> {
    if (!this.newPassword || this.newPassword.length < 8) {
      this.error = 'New password must be at least 8 characters long';
      return;
    }

    this.isLoading = true;
    this.error = '';

    try {
      await this.awsConfig.completeNewPassword(this.currentUser, this.newPassword);
      this.success = 'Password updated successfully!';
      this.needsNewPassword = false;
      
      setTimeout(() => {
        window.location.reload();
      }, 1000);
    } catch (error: any) {
      console.error('Set new password error:', error);
      this.handleAuthError(error);
    } finally {
      this.isLoading = false;
    }
  }

  private handleAuthError(error: any): void {
    switch (error.code || error.name) {
      case 'UserNotConfirmedException':
        this.error = 'Please check your email and confirm your account';
        break;
      case 'NotAuthorizedException':
        this.error = 'Invalid email or password';
        break;
      case 'UserNotFoundException':
        this.error = 'User not found. Please check your email address.';
        break;
      case 'TooManyRequestsException':
        this.error = 'Too many failed attempts. Please try again later.';
        break;
      case 'InvalidPasswordException':
        this.error = 'Password does not meet requirements';
        break;
      case 'InvalidParameterException':
        this.error = 'Invalid email format';
        break;
      default:
        this.error = error.message || 'An error occurred during sign in';
    }
  }
} 