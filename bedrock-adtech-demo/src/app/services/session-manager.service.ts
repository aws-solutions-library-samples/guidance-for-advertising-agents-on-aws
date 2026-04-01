import { Injectable } from '@angular/core';
import { BehaviorSubject, Observable } from 'rxjs';
import { v4 } from 'uuid';

export interface SessionInfo {
  sessionId: string;
  userId?: string;
  customerName?: string;
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
  private readonly ACTIVE_SESSION_KEY = 'agentcore-session';
  private readonly SESSIONS_LIST_KEY = 'agentcore-sessions';
  private readonly SESSION_EXPIRY_HOURS = 24;

  public session$: Observable<SessionInfo | null> = this.sessionSubject.asObservable();
  
  constructor() {
  }

  /**
   * Single entry point for session management.
   * Checks localStorage 'agentcore-session' for an existing valid session.
   * If valid and not expired (24h), updates lastUsed and returns it.
   * If missing or expired, creates a new session with 33-char hex ID.
   */
  getOrCreateSession(): SessionInfo {
    const storedData = this.loadStoredSessions(this.ACTIVE_SESSION_KEY);

    // Check for existing valid session
    if (storedData.activeSessionId) {
      const activeSession = storedData.sessions.find(s => s.sessionId === storedData.activeSessionId);
      if (activeSession && !this.isSessionExpired(activeSession)) {
        activeSession.lastUsed = new Date();
        this.currentSession = activeSession;
        this.saveStoredSessions(this.ACTIVE_SESSION_KEY, storedData);
        this.sessionSubject.next(this.currentSession);
        return this.currentSession;
      }
    }

    // Create new session
    const newSession = this.createNewSessionInternal();
    storedData.sessions.push(newSession);
    storedData.activeSessionId = newSession.sessionId;
    this.saveStoredSessions(this.ACTIVE_SESSION_KEY, storedData);

    this.currentSession = newSession;
    this.sessionSubject.next(this.currentSession);
    return newSession;
  }

  /**
   * Unconditionally creates a new session, ignoring any existing valid session.
   * Used by the refresh button to force a fresh session.
   * Stores in localStorage, adds to sessions list, sets as active, emits via session$.
   */
  forceNewSession(): SessionInfo {
    const storedData = this.loadStoredSessions(this.ACTIVE_SESSION_KEY);

    const newSession = this.createNewSessionInternal();
    storedData.sessions.push(newSession);
    storedData.activeSessionId = newSession.sessionId;
    this.saveStoredSessions(this.ACTIVE_SESSION_KEY, storedData);

    this.currentSession = newSession;
    this.sessionSubject.next(this.currentSession);
    return newSession;
  }

  /**
   * Initialize or update the current session with user information.
   * Delegates to getOrCreateSession() — kept for backward compatibility.
   */
  initializeSession(userId?: string | null, customerName?: string | null, tabId?: string): SessionInfo {
    return this.getOrCreateSession();
  }

  /**
   * Get all sessions. Uses the consolidated sessions list key (no tab component).
   */
  getSessions(): SessionInfo[] {
    const storedData = this.loadStoredSessions(this.ACTIVE_SESSION_KEY);

    // Filter out expired sessions
    const validSessions = storedData.sessions.filter(s => !this.isSessionExpired(s));

    // Update storage if we filtered out any sessions
    if (validSessions.length !== storedData.sessions.length) {
      storedData.sessions = validSessions;
      this.saveStoredSessions(this.ACTIVE_SESSION_KEY, storedData);
    }

    // Sort by last used (most recent first)
    return validSessions.sort((a, b) =>
      new Date(b.lastUsed).getTime() - new Date(a.lastUsed).getTime()
    );
  }

  /**
   * Switch to a different session
   */
  switchSession(sessionId: string): SessionInfo | null {
    const storedData = this.loadStoredSessions(this.ACTIVE_SESSION_KEY);

    const session = storedData.sessions.find(s => s.sessionId === sessionId);
    if (session && !this.isSessionExpired(session)) {
      session.lastUsed = new Date();
      storedData.activeSessionId = sessionId;
      this.saveStoredSessions(this.ACTIVE_SESSION_KEY, storedData);
      this.currentSession = session;
      this.sessionSubject.next(this.currentSession);
      return session;
    }

    return null;
  }

  /**
   * Update session message count
   */
  updateSessionMessageCount(sessionId: string): void {
    const storedData = this.loadStoredSessions(this.ACTIVE_SESSION_KEY);

    const session = storedData.sessions.find(s => s.sessionId === sessionId);
    if (session) {
      session.messageCount = (session.messageCount || 0) + 1;
      session.lastUsed = new Date();
      this.saveStoredSessions(this.ACTIVE_SESSION_KEY, storedData);
    }
  }

  /**
   * Delete a session. If the deleted session was the active one,
   * automatically creates a new session via getOrCreateSession().
   */
  deleteSession(sessionId: string): void {
    const storedData = this.loadStoredSessions(this.ACTIVE_SESSION_KEY);

    storedData.sessions = storedData.sessions.filter(s => s.sessionId !== sessionId);

    if (storedData.activeSessionId === sessionId) {
      storedData.activeSessionId = null;
    }

    this.saveStoredSessions(this.ACTIVE_SESSION_KEY, storedData);

    if (this.currentSession?.sessionId === sessionId) {
      // Create a new session to replace the deleted active session
      this.getOrCreateSession();
    }
  }

  getCurrentSession(userId?: string | null, customerName?: string | null, tabId?: string): SessionInfo {
    return this.getOrCreateSession();
  }

  getCurrentSessionId(userId?: string | null, customerName?: string | null, tabId?: string): string {
    return this.getOrCreateSession().sessionId;
  }

  /**
   * Reset session state. Delegates to getOrCreateSession() to ensure
   * a valid session always exists after the reset.
   */
  updateCustomer(): SessionInfo {
    this.currentSession = null;
    this.sessionSubject.next(null);
    return this.getOrCreateSession();
  }

  isSessionValid(): boolean {
    if (!this.currentSession) return false;
    return !this.isSessionExpired(this.currentSession);
  }

  getSessionInfo(): SessionInfo | null {
    return this.currentSession;
  }

  private createNewSessionInternal(): SessionInfo {
    const sessionId = this.generateSessionId();
    return {
      sessionId,
      createdAt: new Date(),
      lastUsed: new Date(),
      messageCount: 0,
      title: this.generateSessionTitle(new Date())
    };
  }

  private generateSessionId(): string {
    // AgentCore runtimeSessionId requires >= 33 chars; UUID v4 without hyphens is 32 hex chars.
    // Append one extra hex nibble to reach exactly 33.
    const hex = v4().replace(/-/g, '');
    return hex + Math.floor(Math.random() * 16).toString(16);
  }

  private generateSessionTitle(date: Date): string {
    const dateStr = date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    const timeStr = date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
    return `${dateStr} at ${timeStr}`;
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
