import { Injectable } from '@angular/core';
import { BehaviorSubject, Observable, Subject } from 'rxjs';
import { v4 } from 'uuid';

export interface SessionInfo {
  sessionId: string;
  userId?: string;
  customerName?: string;
  createdAt: Date;
  lastUsed: Date;
  messageCount?: number;
  title?: string;
  /** Which tab owns this session. Sessions are never shared between tabs. */
  tabId?: string;
}

/**
 * Session ownership
 * -----------------
 * Sessions live in memory, per tab, and are never persisted.
 *
 * They used to be held in localStorage under one `activeSessionId` shared by the
 * whole app. That is wrong on both counts: every tab runs its own conversation,
 * so a single active id meant whichever tab initialised last silently re-keyed
 * the others (a tab's transcript stayed on screen while its session id changed
 * underneath it, and the agent then had no history to restore); and persisting
 * across reloads handed a new page a session id whose server-side conversation
 * it could no longer see.
 *
 * A reload therefore starts fresh sessions. That is deliberate: the transcript is
 * component state and does not survive a reload either, so a "resumed" id would
 * only reattach an empty chat to a conversation the user can no longer see.
 */
@Injectable({
  providedIn: 'root'
})
export class SessionManagerService {
  /** Sessions for this page load, newest last, keyed by nothing but their id. */
  private sessions = new Map<string, SessionInfo>();

  /** The session each tab is currently using. */
  private activeSessionIdByTab = new Map<string, string>();

  /**
   * Callers that predate per-tab sessions (and the header pill) pass no tabId.
   * They all mean "the tab the user is looking at", so they share one bucket
   * rather than each minting a session of their own.
   */
  private static readonly DEFAULT_TAB_ID = '__default__';

  /**
   * The tab the user is currently on. Surfaces like the header session pill have
   * no tabId of their own, and must show the session actually in use rather than
   * minting one in a bucket no tab reads.
   */
  private activeTabId: string | null = null;

  /** Called by a tab when it becomes the one the user is interacting with. */
  setActiveTab(tabId: string): void {
    const trimmed = (tabId || '').trim();
    if (trimmed) {
      this.activeTabId = trimmed;
    }
  }

  /**
   * Any tab that names itself while asking for a session is, by definition, the
   * one in use — so no-arg callers (the header pill) follow it without every
   * surface having to know a tab id.
   */
  private claimActiveTab(tabId: string): void {
    if (tabId !== SessionManagerService.DEFAULT_TAB_ID) {
      this.activeTabId = tabId;
    }
  }

  private currentSession: SessionInfo | null = null;
  private sessionSubject = new BehaviorSubject<SessionInfo | null>(null);

  public session$: Observable<SessionInfo | null> = this.sessionSubject.asObservable();

  // Emits when a UI surface (e.g. the header button) requests a fresh session.
  // The active chat interface listens to this and performs the full reset.
  private sessionRefreshSubject = new Subject<void>();
  public sessionRefresh$: Observable<void> = this.sessionRefreshSubject.asObservable();

  constructor() {
  }

  /**
   * Request that the active chat interface start a brand-new session and
   * clear its conversation state. Used by the header refresh button so the
   * chat-local reset logic stays in the chat interface.
   */
  requestSessionRefresh(): void {
    this.sessionRefreshSubject.next();
  }

  /**
   * Single entry point for session management: the calling tab's session,
   * created on first use.
   */
  getOrCreateSession(tabId?: string): SessionInfo {
    const tab = this.resolveTabId(tabId);
    this.claimActiveTab(tab);
    const existingId = this.activeSessionIdByTab.get(tab);
    const existing = existingId ? this.sessions.get(existingId) : undefined;

    if (existing) {
      existing.lastUsed = new Date();
      this.setCurrent(existing);
      return existing;
    }

    return this.createSessionForTab(tab);
  }

  /**
   * Unconditionally creates a new session for the calling tab, replacing any
   * session it already had. Used by the refresh button and by "new session".
   */
  forceNewSession(tabId?: string): SessionInfo {
    return this.createSessionForTab(this.resolveTabId(tabId));
  }

  /**
   * Initialize or update the current session with user information.
   * Delegates to getOrCreateSession() — kept for backward compatibility.
   */
  initializeSession(userId?: string | null, customerName?: string | null, tabId?: string): SessionInfo {
    return this.getOrCreateSession(tabId);
  }

  /**
   * Sessions from this page load, most recently used first. Scoped to the
   * calling tab, since one tab must not offer to resume another's conversation.
   */
  getSessions(tabId?: string): SessionInfo[] {
    const tab = this.resolveTabId(tabId);
    this.claimActiveTab(tab);
    return Array.from(this.sessions.values())
      .filter(s => s.tabId === tab)
      .sort((a, b) => b.lastUsed.getTime() - a.lastUsed.getTime());
  }

  /**
   * Switch the calling tab to one of its own earlier sessions.
   */
  switchSession(sessionId: string, tabId?: string): SessionInfo | null {
    const tab = this.resolveTabId(tabId);
    this.claimActiveTab(tab);
    const session = this.sessions.get(sessionId);
    if (!session || session.tabId !== tab) {
      return null;
    }
    session.lastUsed = new Date();
    this.activeSessionIdByTab.set(tab, sessionId);
    this.setCurrent(session);
    return session;
  }

  /**
   * Count a message against a session. Drives "does this session have activity",
   * which is how a tab decides whether an existing session is worth resuming.
   */
  updateSessionMessageCount(sessionId: string): void {
    const session = this.sessions.get(sessionId);
    if (session) {
      session.messageCount = (session.messageCount || 0) + 1;
      session.lastUsed = new Date();
    }
  }

  /**
   * Delete a session. If it was the active one for its tab, that tab gets a new
   * session so it always has one.
   */
  deleteSession(sessionId: string): void {
    const session = this.sessions.get(sessionId);
    this.sessions.delete(sessionId);

    const tab = session?.tabId;
    if (tab && this.activeSessionIdByTab.get(tab) === sessionId) {
      this.activeSessionIdByTab.delete(tab);
      this.createSessionForTab(tab);
      return;
    }

    if (this.currentSession?.sessionId === sessionId) {
      this.getOrCreateSession(tab);
    }
  }

  getCurrentSession(userId?: string | null, customerName?: string | null, tabId?: string): SessionInfo {
    return this.getOrCreateSession(tabId);
  }

  getCurrentSessionId(userId?: string | null, customerName?: string | null, tabId?: string): string {
    return this.getOrCreateSession(tabId).sessionId;
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
    return this.currentSession !== null;
  }

  getSessionInfo(): SessionInfo | null {
    return this.currentSession;
  }

  private resolveTabId(tabId?: string): string {
    const trimmed = (tabId || '').trim();
    if (trimmed) {
      return trimmed;
    }
    return this.activeTabId || SessionManagerService.DEFAULT_TAB_ID;
  }

  private createSessionForTab(tab: string): SessionInfo {
    const session: SessionInfo = {
      sessionId: this.generateSessionId(),
      tabId: tab,
      createdAt: new Date(),
      lastUsed: new Date(),
      messageCount: 0,
      title: this.generateSessionTitle(new Date())
    };
    this.sessions.set(session.sessionId, session);
    this.activeSessionIdByTab.set(tab, session.sessionId);
    this.claimActiveTab(tab);
    this.setCurrent(session);
    return session;
  }

  private setCurrent(session: SessionInfo): void {
    this.currentSession = session;
    this.sessionSubject.next(session);
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
}
