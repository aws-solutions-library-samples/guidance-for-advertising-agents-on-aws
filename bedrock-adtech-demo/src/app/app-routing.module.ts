import { NgModule } from '@angular/core';
import { RouterModule, Routes } from '@angular/router';
import { LoginComponent } from './components/auth/login.component';
import { AuthGuard } from './guards/auth.guard';
import { GenericTabComponent } from './components/generic-tab/generic-tab.component';

const routes: Routes = [
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