import { Component, Input, Output, EventEmitter, OnInit, OnChanges, SimpleChanges } from '@angular/core';
import { SessionInfo } from '../../services/session-manager.service';

export interface SessionSummary {
  sessionId: string;
  startTime: Date;
  lastActivity: Date;
  messageCount: number;
  summary: string;
  topics: string[];
  isLoading: boolean;
  error?: string;
}

@Component({
  selector: 'app-session-prompt-modal',
  templateUrl: './session-prompt-modal.component.html',
  styleUrls: ['./session-prompt-modal.component.scss']
})
export class SessionPromptModalComponent implements OnInit, OnChanges {
  @Input() isVisible: boolean = false;
  @Input() currentSession: SessionInfo | null = null;
  @Input() sessionSummary: SessionSummary | null = null;
  @Input() isLoadingSummary: boolean = false;
  
  @Output() continueSession = new EventEmitter<SessionInfo>();
  @Output() startNewSession = new EventEmitter<void>();
  @Output() modalClosed = new EventEmitter<void>();

  ngOnInit(): void {}

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['isVisible'] && this.isVisible) {
      // Modal just became visible
    }
  }

  onContinueSession(): void {
    if (this.currentSession) {
      this.continueSession.emit(this.currentSession);
    }
    this.closeModal();
  }

  onStartNewSession(): void {
    this.startNewSession.emit();
    this.closeModal();
  }

  closeModal(): void {
    this.modalClosed.emit();
  }

  formatDateTime(date: Date | string | undefined): string {
    if (!date) return 'Unknown';
    const d = new Date(date);
    return d.toLocaleDateString('en-US', {
      weekday: 'short',
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    });
  }

  formatRelativeTime(date: Date | string | undefined): string {
    if (!date) return '';
    const d = new Date(date);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMins / 60);
    const diffDays = Math.floor(diffHours / 24);

    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `${diffMins} minute${diffMins > 1 ? 's' : ''} ago`;
    if (diffHours < 24) return `${diffHours} hour${diffHours > 1 ? 's' : ''} ago`;
    if (diffDays < 7) return `${diffDays} day${diffDays > 1 ? 's' : ''} ago`;
    return this.formatDateTime(date);
  }

  getSessionDuration(): string {
    if (!this.currentSession?.createdAt || !this.currentSession?.lastUsed) return '';
    const start = new Date(this.currentSession.createdAt);
    const end = new Date(this.currentSession.lastUsed);
    const diffMs = end.getTime() - start.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMins / 60);

    if (diffMins < 1) return 'Less than a minute';
    if (diffMins < 60) return `${diffMins} minute${diffMins > 1 ? 's' : ''}`;
    if (diffHours < 24) {
      const remainingMins = diffMins % 60;
      return `${diffHours}h ${remainingMins}m`;
    }
    const diffDays = Math.floor(diffHours / 24);
    return `${diffDays} day${diffDays > 1 ? 's' : ''}`;
  }
}
