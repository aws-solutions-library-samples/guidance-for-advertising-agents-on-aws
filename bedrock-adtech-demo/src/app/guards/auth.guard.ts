import { Injectable } from '@angular/core';
import { CanActivate, Router } from '@angular/router';
import { Observable } from 'rxjs';
import { map } from 'rxjs/operators';
import { AwsConfigService } from '../services/aws-config.service';

@Injectable({
  providedIn: 'root'
})
export class AuthGuard implements CanActivate {
  constructor(
    private awsConfig: AwsConfigService,
    private router: Router
  ) {}

  canActivate(): Observable<boolean> {
    return this.awsConfig.user$.pipe(
      map(user => {
        if (user) {
          return true;
        } else {
          this.router.navigate(['/login']);
          return false;
        }
      })
    );
  }
} 