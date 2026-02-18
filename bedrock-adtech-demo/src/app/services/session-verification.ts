/**
 * Session Consolidation Verification Script
 * 
 * This script can be used to verify that session IDs are properly consolidated
 * across different components and services.
 */

import { SessionManagerService } from './session-manager.service';
import { BedrockService } from './bedrock.service';
import { DemoTrackingService } from './demo-tracking.service';

export class SessionVerification {
  
  constructor(
    private sessionManager: SessionManagerService,
    private bedrockService: BedrockService,
    private demoTrackingService: DemoTrackingService
  ) {}

  /**
   * Verify that all services use the same session ID for the same user/customer
   */
  verifySessionConsistency(userId: string, customerName: string): boolean {
    // Get session ID from session manager
    const sessionId1 = this.sessionManager.getCurrentSessionId(userId, customerName);
    
    // Get session ID from bedrock service (should delegate to session manager)
    const sessionId2 = this.bedrockService.generateCustomSessionId(userId, customerName);
    
    // Verify they are the same
    const isConsistent = sessionId1 === sessionId2;
    
    return isConsistent;
  }

  /**
   * Verify that session updates when customer changes
   */
  verifySessionUpdate(userId: string, customer1: string, customer2: string): boolean {
    // Get initial session
    const sessionId1 = this.sessionManager.getCurrentSessionId(userId, customer1);
    
    // Update customer
    const updatedSession = this.sessionManager.updateCustomer(customer2);
    
    // Verify session changed
    const sessionChanged = sessionId1 !== updatedSession.sessionId;
    
    return sessionChanged;
  }

  /**
   * Verify session ID meets AgentCore requirements (33+ characters)
   */
  verifyAgentCoreCompatibility(userId: string, customerName: string): boolean {
    const sessionId = this.sessionManager.getCurrentSessionId(userId, customerName);
    const isCompatible = sessionId.length >= 33;
    
    return isCompatible;
  }

  /**
   * Run all verification tests
   */
  runAllTests(): boolean {
    console.log('🧪 Running Session Consolidation Verification Tests...\n');
    
    const testUserId = 'test-user';
    const testCustomer1 = 'test-customer-1';
    const testCustomer2 = 'test-customer-2';
    
    const test1 = this.verifySessionConsistency(testUserId, testCustomer1);
    console.log('');
    
    const test2 = this.verifySessionUpdate(testUserId, testCustomer1, testCustomer2);
    console.log('');
    
    const test3 = this.verifyAgentCoreCompatibility(testUserId, testCustomer1);
    console.log('');
    
    const allPassed = test1 && test2 && test3;
    
    return allPassed;
  }
}

// Export for use in components or testing
export function createSessionVerification(
  sessionManager: SessionManagerService,
  bedrockService: BedrockService,
  demoTrackingService: DemoTrackingService
): SessionVerification {
  return new SessionVerification(sessionManager, bedrockService, demoTrackingService);
}