import { Component, Input, Output, EventEmitter, OnInit, OnDestroy, ChangeDetectionStrategy } from '@angular/core';
import { EnrichedAgent } from 'src/app/models/application-models';

export interface AgentSummary {
  agentName: string;
  agentDisplayName: string;
  messageCount: number;
  keyInsights: string[];
  recommendations: string[];
  dataPoints: string[];
  rawSummary: string;
  generatedAt: Date;
  agent:EnrichedAgent|null;
}

export interface SummaryModalState {
  isVisible: boolean;
  isLoading: boolean;
  summary: AgentSummary | null;
  error: string | null;
}

@Component({
  selector: 'app-agent-summary-modal',
  templateUrl: './agent-summary-modal.component.html',
  styleUrls: ['./agent-summary-modal.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class AgentSummaryModalComponent implements OnInit, OnDestroy {
  @Input() summaryModalState: SummaryModalState = {
    isVisible: false,
    isLoading: false,
    summary: null,
    error: null
  };
  
  @Input() summaryRetryCount: number = 0;
  @Input() maxSummaryRetries: number = 3;
  @Input() getAgentColor: (agentName: string) => string = () => '#667eea';

  @Output() closeModal = new EventEmitter<void>();
  @Output() retryGeneration = new EventEmitter<void>();
  @Output() showErrorHelp = new EventEmitter<string>();

  constructor() { }

  ngOnInit(): void {
  }

  ngOnDestroy(): void {
  }

  // Event handlers
  onCloseModal(): void {
    this.closeModal.emit();
  }

  onRetryGeneration(): void {
    this.retryGeneration.emit();
  }

  onShowErrorHelp(error: string): void {
    this.showErrorHelp.emit(error);
  }

  // Utility methods for error handling
  isRetryableError(error: string): boolean {
    if (!error) return false;
    
    const retryableErrors = [
      'throttling',
      'rate limit',
      'timeout',
      'network',
      'connection',
      'temporary',
      'service unavailable',
      'internal server error'
    ];
    
    const errorLower = error.toLowerCase();
    return retryableErrors.some(retryableError => errorLower.includes(retryableError));
  }

  getErrorIcon(error: string): string {
    if (!error) return 'error';
    
    const errorLower = error.toLowerCase();
    
    if (errorLower.includes('network') || errorLower.includes('connection')) {
      return 'wifi_off';
    } else if (errorLower.includes('timeout')) {
      return 'schedule';
    } else if (errorLower.includes('rate limit') || errorLower.includes('throttling')) {
      return 'speed';
    } else if (errorLower.includes('permission') || errorLower.includes('access')) {
      return 'lock';
    } else if (errorLower.includes('not found')) {
      return 'search_off';
    } else {
      return 'error';
    }
  }

  getErrorTitle(error: string): string {
    if (!error) return 'Unknown Error';
    
    const errorLower = error.toLowerCase();
    
    if (errorLower.includes('network') || errorLower.includes('connection')) {
      return 'Connection Issue';
    } else if (errorLower.includes('timeout')) {
      return 'Request Timeout';
    } else if (errorLower.includes('rate limit') || errorLower.includes('throttling')) {
      return 'Rate Limit Exceeded';
    } else if (errorLower.includes('permission') || errorLower.includes('access')) {
      return 'Access Denied';
    } else if (errorLower.includes('not found')) {
      return 'Resource Not Found';
    } else {
      return 'Summary Generation Failed';
    }
  }

  shouldShowHelpButton(error: string): boolean {
    if (!error) return false;
    
    const errorLower = error.toLowerCase();
    return errorLower.includes('permission') || 
           errorLower.includes('access') || 
           errorLower.includes('configuration') ||
           errorLower.includes('not found');
  }

  getErrorHelpText(error: string): string | null {
    if (!error) return null;
    
    const errorLower = error.toLowerCase();
    
    if (errorLower.includes('permission') || errorLower.includes('access')) {
      return 'Check that your AWS credentials have the necessary permissions for Bedrock access.';
    } else if (errorLower.includes('configuration')) {
      return 'Verify that your AWS configuration is correct and the Bedrock service is available in your region.';
    } else if (errorLower.includes('not found')) {
      return 'The requested agent or model may not be available. Check your agent configuration.';
    }
    
    return null;
  }

  // Formatting methods
  formatRelativeTime(date: Date): string {
    if (!date) return 'Unknown';
    
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMinutes = Math.floor(diffMs / (1000 * 60));
    
    if (diffMinutes < 1) {
      return 'Just now';
    } else if (diffMinutes < 60) {
      return `${diffMinutes}m ago`;
    } else if (diffMinutes < 1440) {
      const hours = Math.floor(diffMinutes / 60);
      return `${hours}h ago`;
    } else {
      const days = Math.floor(diffMinutes / 1440);
      return `${days}d ago`;
    }
  }

  formatTextWithMarkdown(text: string): string {
    if (!text) return '';
    
    // Simple markdown formatting
    return text
      .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(.*?)\*/g, '<em>$1</em>')
      .replace(/`(.*?)`/g, '<code>$1</code>')
      .replace(/\n/g, '<br>');
  }

  formatFallbackContent(content: string): string {
    if (!content) return '';
    
    // Format fallback content with basic HTML
    return content
      .replace(/\n\n/g, '</p><p>')
      .replace(/\n/g, '<br>')
      .replace(/^/, '<p>')
      .replace(/$/, '</p>');
  }

  // Summary content checks
  hasSummaryNewItems(type: string): boolean {
    const summary = this.summaryModalState.summary;
    if (!summary) return false;

    // For now, we'll consider all items as potentially new
    // This could be enhanced with actual tracking logic
    switch (type) {
      case 'insights':
        return summary.keyInsights && summary.keyInsights.length > 0;
      case 'recommendations':
        return summary.recommendations && summary.recommendations.length > 0;
      case 'datapoints':
        return summary.dataPoints && summary.dataPoints.length > 0;
      default:
        return false;
    }
  }

  isSummaryItemNew(type: string, item: string): boolean {
    // For now, we'll return false as we don't have tracking logic
    // This could be enhanced with actual item tracking
    return false;
  }

  getAgentColorForAgent(agentName: string): string {
    return this.getAgentColor(agentName);
  }

  getAgentTeam(agentName: string): string | null {
    // This would need to be injected or passed from parent component
    // For now, return null - this should be implemented by the parent
    return null;
  }
}