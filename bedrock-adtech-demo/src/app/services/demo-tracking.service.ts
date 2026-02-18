import { Injectable } from '@angular/core';
import { CloudWatchService, DemoSessionData, DemoScenarioData } from './cloudwatch.service';
import { SessionManagerService } from './session-manager.service';

export interface DemoTrackingData {
  demoUser: string;
  customerName: string;
  sessionDate: string;
  url: string;
  customers: string[];
}

@Injectable({
  providedIn: 'root'
})
export class DemoTrackingService {
  private readonly STORAGE_KEY = 'demo-tracking-data';

  constructor(
    private cloudWatchService: CloudWatchService,
    private sessionManager: SessionManagerService
  ) {}

  shouldShowDemoModal(): boolean {
    const currentUrl = window.location.href;
    const currentDate = new Date().toISOString().split('T')[0]; // YYYY-MM-DD format
    
    const existingData = this.getDemoTrackingData();
    
    // Show modal if:
    // 1. No existing data
    // 2. URL has changed
    // 3. Date has changed (new day)
    if (!existingData) {
      return true;
    }
    
    if (existingData.url !== currentUrl) {
      return true;
    }
    
    if (existingData.sessionDate !== currentDate) {
      return true;
    }
    
    return false;
  }

  getDemoTrackingData(): DemoTrackingData | null {
    try {
      const data = localStorage.getItem(this.STORAGE_KEY);
      return data ? JSON.parse(data) : null;
    } catch (error) {
      console.error('Error reading demo tracking data:', error);
      return null;
    }
  }

  async saveDemoTrackingData(customerName: string, demoUser: string): Promise<void> {
    const currentUrl = window.location.href;
    const currentDate = new Date().toISOString().split('T')[0];
    
    // Get existing data to preserve customer list
    const existingData = this.getDemoTrackingData();
    const existingCustomers = existingData?.customers || [];
    const previousCustomer = existingData?.customerName;
    
    // Add new customer to list if not already present
    const customers = existingCustomers.includes(customerName) 
      ? existingCustomers 
      : [...existingCustomers, customerName];

    const demoData: DemoTrackingData = {
      demoUser,
      customerName,
      sessionDate: currentDate,
      url: currentUrl,
      customers
    };

    try {
      localStorage.setItem(this.STORAGE_KEY, JSON.stringify(demoData));
      
      // Update session when customer changes
      if (previousCustomer !== customerName) {
        console.log(`🏢 Demo Tracking: Customer changed from ${previousCustomer} to ${customerName}`);
        this.sessionManager.updateCustomer(customerName);
      }
      
      // Log to CloudWatch
      const sessionData: DemoSessionData = {
        demoUser,
        customerName,
        sessionDate: currentDate,
        url: currentUrl
      };
      
      await this.cloudWatchService.logDemoSession(sessionData);
      
    } catch (error) {
      console.error('❌ Error saving demo tracking data:', error);
      throw error;
    }
  }

  async logScenario(prompt: string, agentName: string, agentId: string): Promise<void> {
    const demoData = this.getDemoTrackingData();
    
    if (!demoData) {
      console.warn('⚠️ No demo tracking data found, skipping scenario logging');
      return;
    }

    const scenarioData: DemoScenarioData = {
      demoUser: demoData.demoUser,
      customerName: demoData.customerName,
      prompt,
      agentName,
      agentId,
      timestamp: new Date().toISOString()
    };

    await this.cloudWatchService.logDemoScenario(scenarioData);
  }

  getCustomersList(): string[] {
    const data = this.getDemoTrackingData();
    return data?.customers || [];
  }

  getCurrentCustomer(): string | null {
    const data = this.getDemoTrackingData();
    return data?.customerName || null;
  }

  getCurrentDemoUser(): string | null {
    const data = this.getDemoTrackingData();
    return data?.demoUser || null;
  }

  clearAllData(): void {
    try {
      localStorage.removeItem(this.STORAGE_KEY);
    } catch (error) {
      console.error('❌ Error clearing demo tracking data:', error);
      throw error;
    }
  }
}