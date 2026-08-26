import { NgModule } from '@angular/core';
import { RouterModule, Routes } from '@angular/router';
import { LoginComponent } from './components/auth/login.component';
import { AuthGuard } from './guards/auth.guard';
import { GenericTabComponent } from './components/generic-tab/generic-tab.component';

const routes: Routes = [
  // The identity provider redirects back to the site root with ?code=... The
  // wildcard route below would redirect to /login and drop the query string, so the
  // root path is routed explicitly to keep the OAuth parameters intact.
  { path: '', component: LoginComponent },
  { path: 'login', component: LoginComponent },
  { 
    path: 'campaign-planning', 
    component: GenericTabComponent, 
    canActivate: [AuthGuard] 
  },
  { path: '**', redirectTo: 'login' }
];

@NgModule({
  imports: [RouterModule.forRoot(routes)],
  exports: [RouterModule]
})
export class AppRoutingModule { } 