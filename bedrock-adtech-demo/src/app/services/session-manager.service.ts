import { Injectable } from '@angular/core';
import { BehaviorSubject, Observable } from 'rxjs';
import { v4 } from 'uuid';

export interface SessionInfo {
  sessionId: string;
  userId?: string;
  customerName?: string;
  tabId?: string;
  createdAt: Date;
  lastUsed: Date;
  messageCount?: number;
  title?: string;
}

interface StoredTabSessions {
  sessions: SessionInfo[];
  activeSessionId: string | null;
}

@Injectable({
  providedIn: 'root'
})
export class SessionManagerService {
  private currentSession: SessionInfo | null = null;
  private sessionSubject = new BehaviorSubject<SessionInfo | null>(null);
  private readonly STORAGE_KEY_PREFIX = 'tab-sessions';
  private readonly SESSION_EXPIRY_HOURS = 24;
  
  // Deduplication: track recent session creations to prevent duplicates
  private recentSessionCreations = new Map<string, { sessionId: string; timestamp: number }>();
  private readonly SESSION_CREATION_DEBOUNCE_MS = 2000; // 2 second window to prevent duplicates
  
  // Track which tabs have already been initialized this page load
  // Using sessionStorage to persist across component re-renders within the same page load
  private readonly PAGE_LOAD_ID_KEY = 'session-manager-page-load-id';
  private readonly INITIALIZED_TABS_KEY = 'session-manager-initialized-tabs';
  private pageLoadId: string;

  public session$: Observable<SessionInfo | null> = this.sessionSubject.asObservable();
  
  constructor() {
    // Generate or retrieve a unique ID for this page load
    // This ensures we can track initialization across component re-renders
    const existingPageLoadId = sessionStorage.getItem(this.PAGE_LOAD_ID_KEY);
    if (existingPageLoadId) {
      this.pageLoadId = existingPageLoadId;
    } else {
      this.pageLoadId = `page-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
      sessionStorage.setItem(this.PAGE_LOAD_ID_KEY, this.pageLoadId);
      // Clear any previously initialized tabs from a different page load
      sessionStorage.removeItem(this.INITIALIZED_TABS_KEY);
    }
  }
  
  /**
   * Check if a tab has already been initialized this page load
   * Uses sessionStorage to persist across component re-renders
   */
  hasTabBeenInitialized(tabId: string): boolean {
    try {
      const initializedTabsJson = sessionStorage.getItem(this.INITIALIZED_TABS_KEY);
      if (!initializedTabsJson) return false;
      
      const initializedTabs: string[] = JSON.parse(initializedTabsJson);
      return initializedTabs.includes(tabId);
    } catch (error) {
      console.warn('Error checking initialized tabs:', error);
      return false;
    }
  }
  
  /**
   * Mark a tab as initialized for this page load
   * Uses sessionStorage to persist across component re-renders
   */
  markTabAsInitialized(tabId: string): void {
    try {
      const initializedTabsJson = sessionStorage.getItem(this.INITIALIZED_TABS_KEY);
      const initializedTabs: string[] = initializedTabsJson ? JSON.parse(initializedTabsJson) : [];
      
      if (!initializedTabs.includes(tabId)) {
        initializedTabs.push(tabId);
        sessionStorage.setItem(this.INITIALIZED_TABS_KEY, JSON.stringify(initializedTabs));
        console.log(`✅ Marked tab ${tabId} as initialized for page load ${this.pageLoadId}`);
      }
    } catch (error) {
      console.warn('Error marking tab as initialized:', error);
    }
  }

  /**
   * Initialize or update the current session with user information
   */
  initializeSession(userId?: string | null, customerName?: string | null, tabId?: string): SessionInfo {
    const normalizedUserId = userId || undefined;
    const normalizedCustomerName = customerName || undefined;

    // Load stored sessions for this tab
    const storageKey = this.getStorageKey(normalizedUserId, normalizedCustomerName, tabId);
    const storedData = this.loadStoredSessions(storageKey);

    // Check if we have an active session
    if (storedData.activeSessionId) {
      const activeSession = storedData.sessions.find(s => s.sessionId === storedData.activeSessionId);
      if (activeSession && !this.isSessionExpired(activeSession)) {
        activeSession.lastUsed = new Date();
        this.currentSession = activeSession;
        this.saveStoredSessions(storageKey, storedData);
        this.sessionSubject.next(this.currentSession);
        return this.currentSession;
      }
    }

    // Create new session
    const newSession = this.createNewSessionInternal(normalizedUserId, normalizedCustomerName, tabId);
    storedData.sessions.push(newSession);
    storedData.activeSessionId = newSession.sessionId;
    this.saveStoredSessions(storageKey, storedData);

    this.currentSession = newSession;
    this.sessionSubject.next(this.currentSession);
    return newSession;
  }

  /**
   * Generate a cryptographically secure random string of the requested length
   */
  private generateSecureRandomString(length: number): string {
    const array = new Uint8Array(length);
    window.crypto.getRandomValues(array);
    // Convert each byte to base36 (0-9, a-z); pad if needed.
    return Array.from(array, byte => byte.toString(36).padStart(2, '0')).join('').substr(0, length);
  }

  /**
   * Get all sessions for a specific tab
   */
  getTabSessions(userId?: string, customerName?: string, tabId?: string): SessionInfo[] {
    const storageKey = this.getStorageKey(userId, customerName, tabId);
    const storedData = this.loadStoredSessions(storageKey);

    // Filter out expired sessions
    const validSessions = storedData.sessions.filter(s => !this.isSessionExpired(s));

    // Update storage if we filtered out any sessions
    if (validSessions.length !== storedData.sessions.length) {
      storedData.sessions = validSessions;
      this.saveStoredSessions(storageKey, storedData);
    }

    // Sort by last used (most recent first)
    return validSessions.sort((a, b) =>
      new Date(b.lastUsed).getTime() - new Date(a.lastUsed).getTime()
    );
  }

  /**
   * Create a new session for a tab
   */
  createNewSession(userId?: string, customerName?: string, tabId?: string): SessionInfo {
    const storageKey = this.getStorageKey(userId, customerName, tabId);
    
    // Check for recent session creation to prevent duplicates
    const recentCreation = this.recentSessionCreations.get(storageKey);
    const now = Date.now();
    
    if (recentCreation && (now - recentCreation.timestamp) < this.SESSION_CREATION_DEBOUNCE_MS) {
      // Return the recently created session instead of creating a new one
      console.log(`⚠️ Session creation debounced for ${storageKey}, returning existing session: ${recentCreation.sessionId}`);
      const storedData = this.loadStoredSessions(storageKey);
      const existingSession = storedData.sessions.find(s => s.sessionId === recentCreation.sessionId);
      if (existingSession) {
        this.currentSession = existingSession;
        this.sessionSubject.next(this.currentSession);
        return existingSession;
      }
    }
    
    const storedData = this.loadStoredSessions(storageKey);

    const newSession = this.createNewSessionInternal(userId, customerName, tabId);
    storedData.sessions.push(newSession);
    storedData.activeSessionId = newSession.sessionId;
    this.saveStoredSessions(storageKey, storedData);

    this.currentSession = newSession;
    this.sessionSubject.next(this.currentSession);
    
    // Track this creation to prevent duplicates
    this.recentSessionCreations.set(storageKey, { sessionId: newSession.sessionId, timestamp: now });
    
    // Clean up old entries after the debounce window
    setTimeout(() => {
      this.recentSessionCreations.delete(storageKey);
    }, this.SESSION_CREATION_DEBOUNCE_MS + 100);
    
    return newSession;
  }

  /**
   * Switch to a different session
   */
  switchSession(sessionId: string, userId?: string, customerName?: string, tabId?: string): SessionInfo | null {
    const storageKey = this.getStorageKey(userId, customerName, tabId);
    const storedData = this.loadStoredSessions(storageKey);

    const session = storedData.sessions.find(s => s.sessionId === sessionId);
    if (session && !this.isSessionExpired(session)) {
      session.lastUsed = new Date();
      storedData.activeSessionId = sessionId;
      this.saveStoredSessions(storageKey, storedData);
      this.currentSession = session;
      this.sessionSubject.next(this.currentSession);
      return session;
    }

    return null;
  }

  /**
   * Update session message count
   */
  updateSessionMessageCount(sessionId: string, userId?: string, customerName?: string, tabId?: string): void {
    const storageKey = this.getStorageKey(userId, customerName, tabId);
    const storedData = this.loadStoredSessions(storageKey);

    const session = storedData.sessions.find(s => s.sessionId === sessionId);
    if (session) {
      session.messageCount = (session.messageCount || 0) + 1;
      session.lastUsed = new Date();
      this.saveStoredSessions(storageKey, storedData);
    }
  }

  /**
   * Delete a session
   */
  deleteSession(sessionId: string, userId?: string, customerName?: string, tabId?: string): void {
    const storageKey = this.getStorageKey(userId, customerName, tabId);
    const storedData = this.loadStoredSessions(storageKey);

    storedData.sessions = storedData.sessions.filter(s => s.sessionId !== sessionId);

    if (storedData.activeSessionId === sessionId) {
      storedData.activeSessionId = null;
    }

    this.saveStoredSessions(storageKey, storedData);

    if (this.currentSession?.sessionId === sessionId) {
      this.currentSession = null;
      this.sessionSubject.next(null);
    }
  }

  getCurrentSession(userId?: string | null, customerName?: string | null, tabId?: string): SessionInfo {
    // Generate or retrieve tab-specific ID from sessionStorage
    if (!tabId) {
      tabId = sessionStorage.getItem('browserTabId') || undefined;
      if (!tabId) {
        tabId = `tab-${Date.now()}-${this.generateSecureRandomString(9)}`;
        sessionStorage.setItem('browserTabId', tabId);
      }
    }

    if (!this.currentSession) {
      return this.initializeSession(userId, customerName, tabId);
    }
    this.currentSession.lastUsed = new Date();
    return this.currentSession;
  }

  getCurrentSessionId(userId?: string | null, customerName?: string | null, tabId?: string): string {
    return this.getCurrentSession(userId, customerName, tabId).sessionId;
  }

  clearSession(): void {
    this.currentSession = null;
    this.sessionSubject.next(null);
  }

  updateCustomer(customerName?: string | null, tabId?: string): SessionInfo {
    const userId = this.currentSession?.userId;
    this.clearSession();
    return this.initializeSession(userId, customerName, tabId);
  }

  isSessionValid(): boolean {
    if (!this.currentSession) return false;
    return !this.isSessionExpired(this.currentSession);
  }

  getSessionInfo(): SessionInfo | null {
    return this.currentSession;
  }

  private createNewSessionInternal(userId?: string, customerName?: string, tabId?: string): SessionInfo {
    const sessionId = this.generateSessionId(userId, customerName, tabId);
    return {
      sessionId,
      userId,
      customerName,
      tabId,
      createdAt: new Date(),
      lastUsed: new Date(),
      messageCount: 0,
      title: this.generateSessionTitle(new Date())
    };
  }

  private generateSessionId(userId?: string | null, customerName?: string | null, tabId?: string): string {
    const baseId = v4();
    const sanitizedUserId = userId ? userId.replace(/[^a-zA-Z0-9]/g, '-') : 'anonymous';
    const sanitizedCustomerName = customerName ? customerName.replace(/[^a-zA-Z0-9]/g, '-') : `demo-${Date.now()}`;
    const tabPart = tabId ? `-${tabId}` : '';
    return `${sanitizedUserId}-${sanitizedCustomerName}${tabPart}-${baseId}`;
  }

  private generateSessionTitle(date: Date): string {
    const dateStr = date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    const timeStr = date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
    return `${dateStr} at ${timeStr}`;
  }

  private getStorageKey(userId?: string, customerName?: string, tabId?: string): string {
    const userPart = userId || 'anonymous';
    const customerPart = customerName ? `-${customerName}` : '';
    const tabPart = tabId ? `-${tabId}` : '';
    return `${this.STORAGE_KEY_PREFIX}-${userPart}${customerPart}${tabPart}`;
  }

  private isSessionExpired(session: SessionInfo): boolean {
    const now = new Date().getTime();
    const lastUsed = new Date(session.lastUsed).getTime();
    const hoursSinceActivity = (now - lastUsed) / (1000 * 60 * 60);
    return hoursSinceActivity > this.SESSION_EXPIRY_HOURS;
  }

  private loadStoredSessions(storageKey: string): StoredTabSessions {
    try {
      const stored = localStorage.getItem(storageKey);
      if (stored) {
        const parsed = JSON.parse(stored);
        parsed.sessions = parsed.sessions.map((s: any) => ({
          ...s,
          createdAt: new Date(s.createdAt),
          lastUsed: new Date(s.lastUsed)
        }));
        return parsed;
      }
    } catch (error) {
      console.warn('Failed to load sessions from localStorage:', error);
    }
    return { sessions: [], activeSessionId: null };
  }

  private saveStoredSessions(storageKey: string, data: StoredTabSessions): void {
    try {
      localStorage.setItem(storageKey, JSON.stringify(data));
    } catch (error) {
      console.error('Failed to save sessions to localStorage:', error);
    }
  }
}
