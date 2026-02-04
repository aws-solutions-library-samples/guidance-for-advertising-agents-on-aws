import { Component, Input, Output, EventEmitter, ViewChild, ElementRef, AfterViewChecked, ChangeDetectorRef, OnInit, OnChanges, SimpleChanges, OnDestroy, ChangeDetectionStrategy } from '@angular/core';
import { BedrockService } from '../../services/bedrock.service';
import { AwsConfigService } from '../../services/aws-config.service';
import { AgentConfigService } from '../../services/agent-config.service';
import { SessionManagerService, SessionInfo } from '../../services/session-manager.service';
import { TourService } from '../../services/tour.service';
import { AgentMentionService } from '../../services/agent-mention.service';
import { AgentVisualizationService } from '../../services/agent-visualization.service';
import { TranscribeService } from '../../services/transcribe.service';
import { DemoTrackingService } from '../../services/demo-tracking.service';
import { PdfExportService, ChatExportOptions } from '../../services/pdf-export.service';
import { AgentMentionTypeaheadComponent } from '../agent-mention-typeahead/agent-mention-typeahead.component';
import * as partialJson from 'partial-json';
import { TextUtils } from 'src/app/utils/text-utils';
import { Content, Publisher } from 'src/app/models/advertising';
import { AgentSummary, SummaryModalState } from '../agent-summary-modal/agent-summary-modal.component';
import { VisibilitySettings } from '../visibility-settings-modal/visibility-settings-modal.component';
import { BedrockAgentCoreClient, ListEventsCommand } from '@aws-sdk/client-bedrock-agentcore';
import { from } from 'rxjs';
import { ScenarioExample, AgentParticipant, AttachedFile, KnowledgeBaseSource, Message, AgentSuggestion, EnrichedAgent, PendingSpecialistInvocation } from 'src/app/models/application-models';






@Component({
  selector: 'app-chat-interface',
  templateUrl: './chat-interface.component.html',
  styleUrls: ['./chat-interface.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class ChatInterfaceComponent implements OnInit, OnChanges, AfterViewChecked, OnDestroy {


  @Input() scenarios: ScenarioExample[] = [];
  @Input() agentType: string = '';
  @Input() tabId: string = '';
  @Input() currentUser: any;
  @Input() contextData: any = null; // New input for context data
  @Input() showScenariosPanel: boolean = false; // Accept scenarios panel state from parent
  @Input() showSessionsPanel: boolean = false; // Accept scenarios panel state from parent
  @Input() autoCreateSession: boolean = true; // Control whether to auto-create session on init
  @Input() currentPublisher: Publisher | null = null;
  @Input() currentCampaign: any = null;
  @Input() currentContent: Content | null = null;
  @Input() publishers: Publisher[] = [];
  @Output() messageEvent = new EventEmitter<{ message: string, agent: EnrichedAgent }>();
  @Output() scenariosPanelToggle = new EventEmitter<void>(); // Emit toggle events to parent
  @Output() publisherSelect = new EventEmitter<Publisher>();
  @Output() contextPanelShow = new EventEmitter<void>();
  @Output() contextPanelHide = new EventEmitter<void>();
  @Output() messagesUpdated = new EventEmitter<Array<any>>();
  @ViewChild('messagesContainer') private messagesContainer!: ElementRef;
  @ViewChild('messageInput') private messageInput!: ElementRef<HTMLTextAreaElement>;
  @ViewChild('fileInput') private fileInput!: ElementRef<HTMLInputElement>;
  @ViewChild('agentTypeahead') private agentTypeahead!: AgentMentionTypeaheadComponent;
  showJsonStatusIndicator: boolean = false;
  messages: Message[] = [];
  currentMessage = '';
  isLoading = false;
  showScenarios = true;
  selectedScenarioIndex: number | null = null;
  @Output() closeContextSelectorEvent: EventEmitter<any> = new EventEmitter<any>();
  private shouldScrollToBottom = false;
  private lastAgent: EnrichedAgent | null = null;
  private referencesExpanded = new Map<string, boolean>();
  private isFirstMessage = true; // Track if this is the first message of the session
  private agentFirstMessages = new Map<string, boolean>(); // Track first message per agent
  private thinkingCollapsed = new Map<string, boolean>();
  private recentMessageHashes = new Set<string>(); // Track recent message content to prevent duplicates
  private changeDetectionPending = false; // Throttle change detection
  rightNow = new Date();
  // Group thread support
  private agentParticipants = new Map<string, AgentParticipant>();
  private currentAgentResponses = new Map<EnrichedAgent, Message>(); // Track ongoing responses per agent

  // Specialist invocation tracking (invoke_specialist / invoke_specialist_with_RAG)
  private pendingSpecialistInvocations = new Map<string, PendingSpecialistInvocation>();
  private readonly SPECIALIST_TIMEOUT_MS = 5 * 60 * 1000; // 5 minutes timeout
  private specialistTimeoutCheckInterval: any = null;

  // Agent mention typeahead support
  showTypeahead = false;
  typeaheadPosition = { top: 0, left: 0 };
  currentMentionText = '';

  // File upload support
  attachedFiles: AttachedFile[] = [];
  isDragOver = false;
  maxFileSize = 10 * 1024 * 1024; // 10MB limit
  allowedFileTypes = [
    'application/pdf',
    'text/plain',
    'text/csv',
    'application/json',
    'image/jpeg',
    'image/png',
    'image/gif',
    'image/webp',
    'application/vnd.ms-excel',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
  ];

  // Agent selection prompt
  showAgentSelector = false;
  selectedAgentForMessage: EnrichedAgent | null = null; // Default to Bid Simulator
  pendingMessage = '';
  pendingAttachedFiles: AttachedFile[] = [];

  // Session management
  availableSessions: SessionInfo[] = [];
  currentSessionInfo: SessionInfo | null = null;

  // Visualization popover properties
  showVisualizationPopover = false;
  popoverAgent: EnrichedAgent | null = null;
  popoverPosition = { top: 0, left: 0 };

  popoverVisualData: {
    metricData?: any;
    channelAllocations?: any;
    channelCards?: any;
    segmentCards?: any;
    creativeData?: any;
    timelineData?: any;
    decisionTreeData?: any;
    histogramData?: any;
    doubleHistogramData?: any;
    barChartData?: any;
    donutChartData?: any;
    // ADCP-specific visualizations
    adcpInventoryData?: any;      // get_products results
    adcpMediaBuyData?: any;       // create_media_buy results
    adcpDeliveryData?: any;       // get_media_buy_delivery results
    adcpSignalsData?: any;        // get_signals results
    // Presentation visualization
    //presentationData?: any;
    // Template IDs for each visualization type
    metricTemplateId?: string;
    allocationsTemplateId?: string;
    channelsTemplateId?: string;
    segmentsTemplateId?: string;
    creativeTemplateId?: string;
    timelineTemplateId?: string;
    decisionTreeTemplateId?: string;
    histogramTemplateId?: string;
    doubleHistogramTemplateId?: string;
    barChartTemplateId?: string;
    donutChartTemplateId?: string;
    //presentationTemplateId?: string;
    
  } = {};
  availableAgents: AgentSuggestion[] = [];

  // Visualization Companion Panel properties
  showCompanionPanel = false;
  companionPanelMessage: {
    id: string;
    agentName: string;
    visualData: any;
    timestamp: Date;
  } | null = null;
  companionPanelAgentColor = '#667eea';
  private messageIntersectionObserver: IntersectionObserver | null = null;
  private observedMessageElements = new Map<string, Element>();
  private currentVisibleVisualizationMessageId: string | null = null;

  // Voice recording support
  isRecording = false;
  private transcriptionSubscription: any = null;
  private partialTranscript = '';

  // Sources modal support
  showSourcesModal = false;
  currentSources: any[] = [];
  currentSourcesMessageId = '';
  currentSourceQueries: string[] = [];

  // Track knowledge base sources by trace ID
  private knowledgeBaseSources = new Map<string, KnowledgeBaseSource[]>();
  private traceQueries = new Map<string, string>();
  // Track sources by base request ID (without -0, -1, -2 suffix)
  private requestSources = new Map<string, KnowledgeBaseSource[]>();
  // Track which message corresponds to which base request ID
  private requestToMessageMap = new Map<string, string>();

  // Memory optimization for sources modal
  private csvPagination = new Map<string, { currentPage: number, pageSize: number, totalRows: number }>();
  private sourceExpanded = new Map<string, boolean>();
  private compressedSources = new Map<string, string>(); // Cache for large text compression

  // Memory leak prevention
  private activeTimeouts = new Set<any>();
  private activeSubscriptions = new Set<any>();
  private destroyed = false;

  // Agent summary modal state
  summaryModalState: SummaryModalState = {
    isVisible: false,
    isLoading: false,
    summary: null,
    error: null
  };

  // Track previous summary state for new item detection
  private previousSummaryState: {
    keyInsights: string[];
    recommendations: string[];
    dataPoints: string[];
  } = {
      keyInsights: [],
      recommendations: [],
      dataPoints: []
    };

  private newSummaryItems: Set<string> = new Set();

  // Retry state management (exposed for template)
  summaryRetryCount = 0;
  maxSummaryRetries = 3;
  private summaryRetryTimeouts = new Set<any>();

  // PDF Export state
  isExportingPdf = false;
  showExportOptions = false;
  exportOptions: ChatExportOptions = {
    includeTimestamps: true,
    includeVisualizations: true,
    includeThinkingProcess: false,
    paperSize: 'a4',
    orientation: 'portrait',
    title: 'AgentCore Agent Conversation',
    subtitle: ''
  };
  hiddenTypes: Array<string> = [];

  // Visibility Settings Modal
  @Input() showVisibilitySettings: boolean = false;
  visibilitySettings: VisibilitySettings = {
    hiddenMessageTypes: ['tool-trace', 'error'],
    includedContextSections: ['currentCampaign', 'selectedAgent', 'userProfile'],
    hiddenAgents: []
  };

  // Generate agent summary using Claude Haiku with comprehensive error handling
  private async getAgentContributionsFromMemory(agent: EnrichedAgent): Promise<void> {
    try {
      // Reset retry count for new generation attempt
      this.summaryRetryCount = 0;

      // Clear any existing retry timeouts
      this.clearSummaryRetryTimeouts();

      // Reset modal state
      this.summaryModalState = {
        isVisible: true,
        isLoading: true,
        summary: null,
        error: null
      };
      this.changeDetectorRef.markForCheck();

      // Validate agent parameter
      if (!agent) {
        throw new Error('Invalid agent parameter provided');
      }

      // Get agent messages
      const agentName: string = agent.name;
      const agentDisplayName = this.getAgentDisplayName(agent);

      if (!agentName) {
        throw new Error('Agent name is required but not provided');
      }

      const agentMessages = this.getAgentMessages(agentName);

      // Enhanced "No messages found" state with detailed information
      if (!agentMessages || agentMessages.length === 0) {
        this.summaryModalState = {
          isVisible: true,
          isLoading: false,
          summary: null,
          error: `No messages found for ${agentDisplayName} in the current conversation. This agent hasn't participated in this thread yet.`
        };
        this.changeDetectorRef.markForCheck();
        return;
      }

      // Validate message content
      const validMessages = agentMessages.filter(msg => msg.text && msg.text.trim().length > 0);
      if (validMessages.length === 0) {
        this.summaryModalState = {
          isVisible: true,
          isLoading: false,
          summary: null,
          error: `${agentDisplayName} has ${agentMessages.length} message(s) but no readable content to summarize.`
        };
        this.changeDetectorRef.markForCheck();
        return;
      }

      // Build prompt for Claude Haiku
      const summaryPrompt = this.buildSummaryPrompt(validMessages, agentDisplayName);

      if (!summaryPrompt || summaryPrompt.trim().length === 0) {
        throw new Error('Failed to build summary prompt from agent messages');
      }

      // Call Claude Haiku using Converse API
      try {
        const summaryText = await this.bedrockService.invokeClaudeHaikuForSummary(summaryPrompt);

        if (!summaryText) {
          throw new Error('No content received from Claude Haiku model');
        }

        if (summaryText.length < 10) {
          throw new Error('Received unusually short response from Claude Haiku');
        }

        // Create structured summary object with enhanced parsing
        const summary: AgentSummary = this.parseSummaryResponse(
          summaryText,
          agentName,
          agentDisplayName,
          validMessages.length,
          agent
        );

        // Validate that we got meaningful content
        if (!this.validateSummaryContent(summary)) {
          console.warn('Summary validation failed, using fallback parsing');
          summary.keyInsights = [this.getFallbackSummaryContent(summaryText)];
        }

        // Reset retry count on successful generation
        this.summaryRetryCount = 0;

        // Detect new items for glow effect
        this.detectNewSummaryItems(summary);

        // Update modal state with successful result
        this.summaryModalState = {
          isVisible: true,
          isLoading: false,
          summary: summary,
          error: null
        };
        this.changeDetectorRef.markForCheck();

      } catch (error) {
        console.error('Error generating agent summary:', error);

        // Try to create a fallback summary from the error response if possible
        const fallbackSummary = this.createFallbackSummary(
          error,
          agentName,
          agentDisplayName,
          validMessages.length,
          agent
        );

        if (fallbackSummary) {
          this.summaryModalState = {
            isVisible: true,
            isLoading: false,
            summary: fallbackSummary,
            error: 'Summary parsing was incomplete, showing available content.'
          };
          this.changeDetectorRef.markForCheck();
        } else {
          // Handle the error using existing error handling logic
          this.handleSummaryGenerationError(error, agent);
        }
      }

    } catch (error) {
      console.error('Error in generateAgentSummary:', error);
      this.handleSummaryGenerationError(error, agent);
    }
  }

  // Comprehensive error handling with user-friendly messages and retry logic
  private handleSummaryGenerationError(error: any, agent: any): void {
    // Check for expired token first
    if ((error as any).isExpiredToken ||
      error.message?.includes('ExpiredTokenException') ||
      error.message?.includes('Your session has expired')) {
      this.showTokenExpiredDialog(error.message || 'Your session has expired. Please refresh the page to continue.');
      return;
    }

    // Determine if this error is retryable
    const isRetryableError = this.isRetryableErrorInternal(error);

    // Get user-friendly error message
    const errorMessage = this.getUserFriendlyErrorMessage(error);

    // If error is retryable and we haven't exceeded max retries, attempt retry
    if (isRetryableError && this.summaryRetryCount < this.maxSummaryRetries) {
      this.summaryRetryCount++;
      const retryDelay = this.calculateRetryDelay(this.summaryRetryCount);

      console.log(`Retrying summary generation (attempt ${this.summaryRetryCount}/${this.maxSummaryRetries}) in ${retryDelay}ms`);

      // Show retry message to user
      this.summaryModalState = {
        isVisible: true,
        isLoading: true,
        summary: null,
        error: `${errorMessage} Retrying... (attempt ${this.summaryRetryCount}/${this.maxSummaryRetries})`
      };
      this.changeDetectorRef.markForCheck();

      // Schedule retry with exponential backoff
      const retryTimeout = this.safeSetTimeout(() => {
        this.summaryRetryTimeouts.delete(retryTimeout);
        this.generateAgentSummaryInternal(agent);
      }, retryDelay);

      this.summaryRetryTimeouts.add(retryTimeout);
    } else {
      // Max retries exceeded or non-retryable error
      let finalErrorMessage = errorMessage;

      if (this.summaryRetryCount >= this.maxSummaryRetries) {
        finalErrorMessage = `Failed to generate summary after ${this.maxSummaryRetries} attempts. ${errorMessage}`;
      }

      this.summaryModalState = {
        isVisible: true,
        isLoading: false,
        summary: null,
        error: finalErrorMessage
      };
      this.changeDetectorRef.markForCheck();

      // Reset retry count
      this.summaryRetryCount = 0;
    }
  }

  // Internal method for retry attempts (avoids resetting retry count)
  private async generateAgentSummaryInternal(agent: EnrichedAgent): Promise<void> {
    try {
      // Get agent messages (same validation as main method)
      const agentName = agent.name;
      const agentDisplayName = agent.displayName || this.getAgentDisplayName(agent);
      const agentMessages = this.getAgentMessages(agentName);

      if (!agentMessages || agentMessages.length === 0) {
        throw new Error('No messages found for agent');
      }

      const validMessages = agentMessages.filter(msg => msg.text && msg.text.trim().length > 0);
      if (validMessages.length === 0) {
        throw new Error('No valid message content found');
      }

      // Build prompt and call model using Converse API
      const summaryPrompt = this.buildSummaryPrompt(validMessages, agentDisplayName);

      try {
        const summaryText = await this.bedrockService.invokeClaudeHaikuForSummary(summaryPrompt);

        if (!summaryText) {
          throw new Error('No content received from Claude Haiku model');
        }

        const summary: AgentSummary = this.parseSummaryResponse(
          summaryText,
          agentName,
          agentDisplayName,
          validMessages.length,
          agent
        );

        if (!this.validateSummaryContent(summary)) {
          summary.keyInsights = [this.getFallbackSummaryContent(summaryText)];
        }

        // Success - reset retry count
        this.summaryRetryCount = 0;

        this.summaryModalState = {
          isVisible: true,
          isLoading: false,
          summary: summary,
          error: null
        };
        this.changeDetectorRef.markForCheck();

      } catch (error) {
        this.handleSummaryGenerationError(error, agent);
      }

    } catch (error) {
      this.handleSummaryGenerationError(error, agent);
    }
  }

  // Determine if an error is retryable (internal method)
  private isRetryableErrorInternal(error: any): boolean {
    if (!error) return false;

    const errorString = (error.message || error.error || error.toString()).toLowerCase();

    // Retryable errors (network, temporary service issues)
    const retryablePatterns = [
      'network',
      'timeout',
      'throttling',
      'rate limit',
      'service unavailable',
      'internal server error',
      'temporary',
      'connection',
      'socket',
      'econnreset',
      'enotfound',
      'etimedout'
    ];

    // Non-retryable errors (authentication, permissions, validation)
    const nonRetryablePatterns = [
      'access denied',
      'unauthorized',
      'forbidden',
      'invalid credentials',
      'authentication',
      'validation exception',
      'invalid request',
      'bad request',
      'not found',
      'no messages found',
      'agent name is required'
    ];

    // Check for non-retryable patterns first
    if (nonRetryablePatterns.some(pattern => errorString.includes(pattern))) {
      return false;
    }

    // Check for retryable patterns
    return retryablePatterns.some(pattern => errorString.includes(pattern));
  }

  // Calculate retry delay with exponential backoff
  private calculateRetryDelay(retryCount: number): number {
    const baseDelay = 1000; // 1 second base delay
    const maxDelay = 10000; // 10 seconds max delay
    const exponentialDelay = baseDelay * Math.pow(2, retryCount - 1);

    // Add jitter to prevent thundering herd
    const jitter = Math.random() * 0.3 * exponentialDelay;

    return Math.min(exponentialDelay + jitter, maxDelay);
  }

  // Get user-friendly error messages based on error type
  private getUserFriendlyErrorMessage(error: any): string {
    if (!error) {
      return 'An unknown error occurred. Please try again.';
    }

    const errorString = (error.message || error.error || error.toString()).toLowerCase();

    // Check for expired token specifically
    if ((error as any).isExpiredToken ||
      errorString.includes('expiredtokenexception') ||
      errorString.includes('security token included in the request is expired') ||
      errorString.includes('your session has expired')) {
      return 'Your session has expired. Please refresh the page to continue.';
    }

    // Authentication and authorization errors
    if (errorString.includes('credentials') || errorString.includes('unauthorized')) {
      return 'Authentication issue. Please sign out and sign in again.';
    }

    if (errorString.includes('access denied') || errorString.includes('forbidden')) {
      return 'Access denied to Claude Haiku model. Please check your permissions.';
    }

    // Validation errors
    if (errorString.includes('validation exception') || errorString.includes('invalid request')) {
      return 'Invalid request format. Please try again with different content.';
    }

    if (errorString.includes('bad request')) {
      return 'The request format was invalid. Please try again.';
    }

    // Network and connectivity errors
    if (errorString.includes('network') || errorString.includes('connection') ||
      errorString.includes('timeout') || errorString.includes('econnreset')) {
      return 'Network connection issue. Please check your internet connection and try again.';
    }

    // Rate limiting and throttling
    if (errorString.includes('throttling') || errorString.includes('rate limit')) {
      return 'Too many requests. Please wait a moment and try again.';
    }

    // Service availability errors
    if (errorString.includes('service unavailable') || errorString.includes('internal server error')) {
      return 'The AI service is temporarily unavailable. Please try again in a few moments.';
    }

    // Model-specific errors
    if (errorString.includes('model') && errorString.includes('not found')) {
      return 'The Claude Haiku model is not available. Please contact support.';
    }

    // Content-related errors
    if (errorString.includes('no content') || errorString.includes('empty response')) {
      return 'The AI model returned no content. Please try again.';
    }

    if (errorString.includes('parsing') || errorString.includes('parse')) {
      return 'Failed to process the AI response. Please try again.';
    }

    // Agent-specific errors
    if (errorString.includes('no messages found')) {
      return 'This agent has no messages to summarize in the current conversation.';
    }

    if (errorString.includes('agent name is required')) {
      return 'Invalid agent selection. Please try clicking on a different agent.';
    }

    // Generic fallback with specific error details if available
    const specificError = error.message || error.error;
    if (specificError && typeof specificError === 'string' && specificError.length < 200) {
      return `Error: ${specificError}`;
    }

    return 'Failed to generate summary. Please try again.';
  }

  // Clear all retry timeouts
  private clearSummaryRetryTimeouts(): void {
    this.summaryRetryTimeouts.forEach(timeout => {
      if (timeout) {
        clearTimeout(timeout);
      }
    });
    this.summaryRetryTimeouts.clear();
  }

  // Enhanced error UI helper methods

  // Check if error is retryable (exposed for template)
  isRetryableError(errorMessage: string): boolean {
    if (!errorMessage) return false;
    return this.isRetryableErrorInternal({ message: errorMessage });
  }

  // Get appropriate error icon based on error type
  getErrorIcon(errorMessage: string): string {
    if (!errorMessage) return 'error_outline';

    const errorString = errorMessage.toLowerCase();

    if (errorString.includes('network') || errorString.includes('connection')) {
      return 'wifi_off';
    } else if (errorString.includes('authentication') || errorString.includes('credentials')) {
      return 'lock';
    } else if (errorString.includes('access denied') || errorString.includes('forbidden')) {
      return 'block';
    } else if (errorString.includes('no messages found')) {
      return 'chat_bubble_outline';
    } else if (errorString.includes('rate limit') || errorString.includes('throttling')) {
      return 'hourglass_empty';
    } else if (errorString.includes('service unavailable')) {
      return 'cloud_off';
    } else if (this.isRetryableError(errorMessage)) {
      return 'refresh';
    } else {
      return 'error_outline';
    }
  }

  // Get appropriate error title based on error type
  getErrorTitle(errorMessage: string): string {
    if (!errorMessage) return 'Summary Generation Failed';

    const errorString = errorMessage.toLowerCase();

    if (errorString.includes('no messages found')) {
      return 'No Messages Available';
    } else if (errorString.includes('network') || errorString.includes('connection')) {
      return 'Connection Issue';
    } else if (errorString.includes('authentication') || errorString.includes('credentials')) {
      return 'Authentication Required';
    } else if (errorString.includes('access denied') || errorString.includes('forbidden')) {
      return 'Access Denied';
    } else if (errorString.includes('rate limit') || errorString.includes('throttling')) {
      return 'Rate Limited';
    } else if (errorString.includes('service unavailable')) {
      return 'Service Unavailable';
    } else if (errorString.includes('retrying') || errorString.includes('attempt')) {
      return 'Retrying Summary Generation';
    } else {
      return 'Summary Generation Failed';
    }
  }

  // Determine if help button should be shown
  shouldShowHelpButton(errorMessage: string): boolean {
    if (!errorMessage) return false;

    const errorString = errorMessage.toLowerCase();

    // Show help for authentication, permission, and configuration issues
    return errorString.includes('authentication') ||
      errorString.includes('access denied') ||
      errorString.includes('credentials') ||
      errorString.includes('forbidden') ||
      errorString.includes('model') ||
      errorString.includes('configuration');
  }

  // Get contextual help text for specific error types
  getErrorHelpText(errorMessage: string): string {
    if (!errorMessage) return '';

    const errorString = errorMessage.toLowerCase();

    if (errorString.includes('authentication') || errorString.includes('credentials')) {
      return 'Try signing out and signing back in to refresh your authentication credentials.';
    } else if (errorString.includes('access denied') || errorString.includes('forbidden')) {
      return 'Your account may not have permission to use the Claude Haiku model. Contact your administrator.';
    } else if (errorString.includes('network') || errorString.includes('connection')) {
      return 'Check your internet connection and try again. If the problem persists, the service may be temporarily unavailable.';
    } else if (errorString.includes('rate limit') || errorString.includes('throttling')) {
      return 'You\'ve made too many requests recently. Wait a few minutes before trying again.';
    } else if (errorString.includes('model')) {
      return 'The Claude Haiku model may not be available in your region or account. Contact support for assistance.';
    } else if (errorString.includes('no messages found')) {
      return 'This agent hasn\'t sent any messages in the current conversation. Try starting a conversation with this agent first.';
    }

    return '';
  }

  // Show error help (could open a modal or navigate to help page)
  showErrorHelp(errorMessage: string): void {
    const helpText = this.getErrorHelpText(errorMessage);
    if (helpText) {
      // For now, just show an alert. In a real app, this could open a help modal
      alert(`Help: ${helpText}`);
    }
  }

  // Show dialog when token has expired
  private showTokenExpiredDialog(errorMessage: string): void {
    const userFriendlyMessage = 'Your session has expired. Please refresh the page to continue.';

    // Show a confirmation dialog with refresh option
    if (confirm(`${userFriendlyMessage}\n\nWould you like to refresh the page now?`)) {
      // User clicked OK, refresh the page
      window.location.reload();
    } else {
      // User clicked Cancel, show the error in chat
      const errorChatMessage: Message = {
        id: `error-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
        text: `🔑 ${userFriendlyMessage}`,
        sender: 'agent',
        timestamp: new Date(),
        type: 'text',
        agentName: 'System',
        displayName: 'System',
        data: {
          isThinking: false,
          finalResponse: userFriendlyMessage,
          messageType: 'error'
        }
      };

      this.messages.push(errorChatMessage);
      this.shouldScrollToBottom = true;
      this.changeDetectorRef.detectChanges();
    }
  }

  // Validation method to ensure error handling implementation is complete
  private validateErrorHandlingImplementation(): boolean {
    // Check that all required methods exist
    const requiredMethods = [
      'generateAgentSummary',
      'handleSummaryGenerationError',
      'isRetryableError',
      'calculateRetryDelay',
      'getUserFriendlyErrorMessage',
      'clearSummaryRetryTimeouts',
      'retrySummaryGeneration',
      'closeSummaryModal'
    ];

    for (const method of requiredMethods) {
      if (typeof (this as any)[method] !== 'function') {
        console.error(`Missing required method: ${method}`);
        return false;
      }
    }

    // Check that retry state is properly initialized
    if (typeof this.summaryRetryCount !== 'number' ||
      typeof this.maxSummaryRetries !== 'number' ||
      !this.summaryRetryTimeouts) {
      console.error('Retry state not properly initialized');
      return false;
    }

    return true;
  }

  // Helper method to extract content from specific sections in the summary
  private extractSectionContent(summaryText: string, sectionName: string): string[] {
    if (!summaryText || !sectionName) {
      return [];
    }

    try {
      // Enhanced regex to handle various section header formats
      const sectionPatterns = [
        // Pattern 1: "1. Key Insights:" or "Key Insights:"
        new RegExp(`(?:^|\\n)\\s*(?:\\d+\\.\\s*)?${sectionName}\\s*:?\\s*([\\s\\S]*?)(?=\\n\\s*(?:\\d+\\.\\s*)?[A-Z][^:]*:|$)`, 'i'),
        // Pattern 2: "**Key Insights**" (markdown bold)
        new RegExp(`(?:^|\\n)\\s*\\*\\*${sectionName}\\*\\*\\s*:?\\s*([\\s\\S]*?)(?=\\n\\s*\\*\\*[A-Z][^*]*\\*\\*|$)`, 'i'),
        // Pattern 3: "# Key Insights" (markdown header)
        new RegExp(`(?:^|\\n)\\s*#+\\s*${sectionName}\\s*:?\\s*([\\s\\S]*?)(?=\\n\\s*#+\\s*[A-Z]|$)`, 'i')
      ];

      let sectionContent = '';

      // Try each pattern until we find a match
      for (const pattern of sectionPatterns) {
        const match = summaryText.match(pattern);
        if (match && match[1]) {
          sectionContent = match[1].trim();
          break;
        }
      }

      // If no structured section found, try fallback extraction
      if (!sectionContent) {
        sectionContent = this.extractSectionContentFallback(summaryText, sectionName);
      }

      if (!sectionContent) {
        return [];
      }

      // Enhanced parsing to handle various list formats
      const items = this.parseListItems(sectionContent);

      // Clean and validate items
      return items
        .map(item => this.cleanListItem(item))
        .filter(item => item.length > 0 && this.isValidListItem(item));

    } catch (error) {
      console.warn(`Error extracting section "${sectionName}":`, error);
      return [];
    }
  }

  // Fallback method for unstructured responses
  private extractSectionContentFallback(summaryText: string, sectionName: string): string {
    if (!summaryText || !sectionName) {
      return '';
    }

    // Look for keywords related to the section in the text
    const keywords = this.getSectionKeywords(sectionName);
    const sentences = summaryText.split(/[.!?]+/).map(s => s.trim()).filter(s => s.length > 0);

    const relevantSentences = sentences.filter(sentence => {
      const lowerSentence = sentence.toLowerCase();
      return keywords.some(keyword => lowerSentence.includes(keyword.toLowerCase()));
    });

    return relevantSentences.slice(0, 3).join('. '); // Limit to 3 most relevant sentences
  }

  // Get keywords associated with each section type
  private getSectionKeywords(sectionName: string): string[] {
    const keywordMap: { [key: string]: string[] } = {
      'Key Insights': ['insight', 'finding', 'analysis', 'observation', 'discovered', 'identified', 'shows', 'indicates'],
      'Recommendations': ['recommend', 'suggest', 'should', 'propose', 'advise', 'consider', 'implement', 'optimize'],
      'Data Points': ['metric', 'data', 'number', 'percent', 'rate', 'value', 'measurement', 'statistic', '$', '%'],
      'Overall Contribution': ['contribution', 'role', 'provided', 'helped', 'assisted', 'delivered', 'value', 'impact']
    };

    return keywordMap[sectionName] || [];
  }

  // Parse various list item formats
  private parseListItems(content: string): string[] {
    if (!content) {
      return [];
    }

    // Split by various list indicators
    const listPatterns = [
      /\n\s*[-•*]\s*/,           // Bullet points: -, •, *
      /\n\s*\d+\.\s*/,          // Numbered lists: 1., 2., etc.
      /\n\s*[a-zA-Z]\.\s*/,     // Lettered lists: a., b., etc.
      /\n\s*[ivx]+\.\s*/i,      // Roman numerals: i., ii., etc.
      /\n\s*>\s*/,              // Quote-style lists: >
      /\n\s*\+\s*/              // Plus signs: +
    ];

    let items: string[] = [];

    // Try each pattern
    for (const pattern of listPatterns) {
      const splitItems = content.split(pattern).filter(item => item.trim().length > 0);
      if (splitItems.length > 1) {
        items = splitItems;
        break;
      }
    }

    // If no list pattern found, split by line breaks and filter meaningful lines
    if (items.length <= 1) {
      items = content.split('\n')
        .map(line => line.trim())
        .filter(line => line.length > 10); // Filter out very short lines
    }

    return items;
  }

  // Clean individual list items
  private cleanListItem(item: string): string {
    if (!item) {
      return '';
    }

    return item
      .replace(/^[-•*+>\s]*/, '') // Remove leading list indicators
      .replace(/^\d+\.\s*/, '')   // Remove leading numbers
      .replace(/^[a-zA-Z]\.\s*/, '') // Remove leading letters
      .replace(/^[ivx]+\.\s*/i, '') // Remove leading roman numerals
      .replace(/\s+/g, ' ')       // Normalize whitespace
      .trim();
  }

  // Validate if an item is meaningful
  private isValidListItem(item: string): boolean {
    if (!item || item.length < 5) {
      return false;
    }

    // Filter out common non-content patterns
    const invalidPatterns = [
      /^[A-Z][^:]*:$/,           // Section headers like "Key Insights:"
      /^overall contribution$/i,  // Standalone section names
      /^recommendations$/i,
      /^key insights$/i,
      /^data points$/i,
      /^\s*$/, // Empty or whitespace only
      /^[.,:;-]+$/ // Only punctuation
    ];

    return !invalidPatterns.some(pattern => pattern.test(item));
  }

  // Format summary content for display with HTML/markdown formatting
  formatSummaryForDisplay(summary: AgentSummary): string {
    if (!summary) {
      return '';
    }

    let formattedContent = '';

    // Add agent header
    formattedContent += `<div class="summary-header">
      <h3>${this.escapeHtml(summary.agentDisplayName)}</h3>
      <p class="summary-meta">${summary.messageCount} messages • Generated ${this.formatRelativeTime(summary.generatedAt)}</p>
    </div>`;

    // Add Key Insights section
    if (summary.keyInsights && summary.keyInsights.length > 0) {
      formattedContent += `<div class="summary-section">
        <h4><i class="fas fa-lightbulb"></i> Key Insights</h4>
        <ul class="summary-list insights-list">`;

      summary.keyInsights.forEach(insight => {
        formattedContent += `<li>${this.formatTextWithMarkdown(insight)}</li>`;
      });

      formattedContent += `</ul></div>`;
    }

    // Add Recommendations section
    if (summary.recommendations && summary.recommendations.length > 0) {
      formattedContent += `<div class="summary-section">
        <h4><i class="fas fa-arrow-right"></i> Recommendations</h4>
        <ul class="summary-list recommendations-list">`;

      summary.recommendations.forEach(recommendation => {
        formattedContent += `<li>${this.formatTextWithMarkdown(recommendation)}</li>`;
      });

      formattedContent += `</ul></div>`;
    }

    // Add Data Points section
    if (summary.dataPoints && summary.dataPoints.length > 0) {
      formattedContent += `<div class="summary-section">
        <h4><i class="fas fa-chart-bar"></i> Data Points</h4>
        <ul class="summary-list data-points-list">`;

      summary.dataPoints.forEach(dataPoint => {
        formattedContent += `<li>${this.formatTextWithMarkdown(dataPoint)}</li>`;
      });

      formattedContent += `</ul></div>`;
    }

    // Add overall contribution if available in raw summary
    const overallContribution = this.extractSectionContent(summary.rawSummary, 'Overall Contribution');
    if (overallContribution && overallContribution.length > 0) {
      formattedContent += `<div class="summary-section">
        <h4><i class="fas fa-user-check"></i> Overall Contribution</h4>
        <div class="summary-text">`;

      overallContribution.forEach(contribution => {
        formattedContent += `<p>${this.formatTextWithMarkdown(contribution)}</p>`;
      });

      formattedContent += `</div></div>`;
    }

    return formattedContent;
  }

  // Format text with basic markdown support
  formatTextWithMarkdown(text: string): string {
    if (!text) {
      return '';
    }

    return this.escapeHtml(text)
      .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>') // Bold
      .replace(/\*(.*?)\*/g, '<em>$1</em>')             // Italic
      .replace(/`(.*?)`/g, '<code>$1</code>')           // Inline code
      .replace(/(\d+(?:\.\d+)?%)/g, '<span class="metric">$1</span>') // Highlight percentages
      .replace(/(\$[\d,]+(?:\.\d{2})?)/g, '<span class="metric">$1</span>') // Highlight currency
      .replace(/(\d+(?:,\d{3})*(?:\.\d+)?)/g, '<span class="metric">$1</span>'); // Highlight large numbers
  }

  // Escape HTML to prevent XSS
  private escapeHtml(text: string): string {
    if (!text) {
      return '';
    }

    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  // Format relative time for display
  formatRelativeTime(date: Date): string {
    if (!date) {
      return 'unknown time';
    }

    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffSeconds = Math.floor(diffMs / 1000);
    const diffMinutes = Math.floor(diffSeconds / 60);
    const diffHours = Math.floor(diffMinutes / 60);

    if (diffSeconds < 60) {
      return 'just now';
    } else if (diffMinutes < 60) {
      return `${diffMinutes} minute${diffMinutes !== 1 ? 's' : ''} ago`;
    } else if (diffHours < 24) {
      return `${diffHours} hour${diffHours !== 1 ? 's' : ''} ago`;
    } else {
      return date.toLocaleDateString();
    }
  }

  // Get fallback content when structured parsing fails
  getFallbackSummaryContent(rawSummary: string): string {
    if (!rawSummary) {
      return 'No summary content available.';
    }

    // Clean up the raw summary for display
    const cleaned = rawSummary
      .replace(/^\s*#+\s*/gm, '') // Remove markdown headers
      .replace(/\*\*(.*?)\*\*/g, '$1') // Remove bold markdown
      .replace(/\*(.*?)\*/g, '$1') // Remove italic markdown
      .trim();

    // Split into paragraphs and limit length
    const paragraphs = cleaned.split('\n\n').filter(p => p.trim().length > 0);
    const limitedContent = paragraphs.slice(0, 3).join('\n\n');

    return limitedContent.length > 500
      ? limitedContent.substring(0, 500) + '...'
      : limitedContent;
  }

  // Extract response text from various response formats (now primarily for fallback scenarios)
  private extractResponseText(response: any): string {
    if (!response) {
      return '';
    }

    // For Converse API responses, the text is already extracted in the service
    if (typeof response === 'string') {
      return response.trim();
    }

    // Try different response format patterns for legacy support
    const textSources = [
      response.content?.text,
      response.rawResponse,
      response.text,
      response.body?.text,
      response.output?.text,
      response.message?.content,
      JSON.stringify(response.content || response)
    ];

    for (const source of textSources) {
      if (source && typeof source === 'string' && source.trim().length > 0) {
        return source.trim();
      }
    }

    return '';
  }

  // Parse summary response into structured format
  private parseSummaryResponse(
    summaryText: string,
    agentName: string,
    agentDisplayName: string,
    messageCount: number,
    agent: EnrichedAgent
  ): AgentSummary {
    return {
      agentName: agentName,
      agentDisplayName: agentDisplayName,
      messageCount: messageCount,
      keyInsights: this.extractSectionContent(summaryText, 'Key Insights'),
      recommendations: this.extractSectionContent(summaryText, 'Recommendations'),
      dataPoints: this.extractSectionContent(summaryText, 'Data Points'),
      rawSummary: summaryText,
      generatedAt: new Date(),
      agent: agent
    };
  }

  // Validate that summary contains meaningful content
  private validateSummaryContent(summary: AgentSummary): boolean {
    if (!summary) {
      return false;
    }

    // Check if we have at least one section with content
    const hasKeyInsights = summary.keyInsights && summary.keyInsights.length > 0;
    const hasRecommendations = summary.recommendations && summary.recommendations.length > 0;
    const hasDataPoints = summary.dataPoints && summary.dataPoints.length > 0;
    const hasRawContent = Boolean(summary.rawSummary && summary.rawSummary.trim().length > 50);

    return hasKeyInsights || hasRecommendations || hasDataPoints || hasRawContent;
  }

  // Create fallback summary when parsing fails
  private createFallbackSummary(
    response: any,
    agentName: string,
    agentDisplayName: string,
    messageCount: number,
    agent: EnrichedAgent
  ): AgentSummary | null {
    // For Converse API, response might be a string or an error object
    let rawText = '';

    if (typeof response === 'string') {
      rawText = response;
    } else {
      rawText = this.extractResponseText(response);
    }

    if (!rawText || rawText.length < 10) {
      return null;
    }

    // Create a basic summary with fallback content
    return {
      agentName: agentName,
      agentDisplayName: agentDisplayName,
      messageCount: messageCount,
      keyInsights: [this.getFallbackSummaryContent(rawText)],
      recommendations: [],
      dataPoints: [],
      rawSummary: rawText,
      generatedAt: new Date(),
      agent: agent
    };
  }

  // Format fallback content for HTML display
  formatFallbackContent(rawSummary: string): string {
    if (!rawSummary) {
      return '<p class="no-content">No summary content available.</p>';
    }

    // Clean and format the raw summary
    const cleaned = rawSummary
      .replace(/^\s*#+\s*/gm, '') // Remove markdown headers
      .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>') // Convert bold markdown to HTML
      .replace(/\*(.*?)\*/g, '<em>$1</em>') // Convert italic markdown to HTML
      .replace(/`(.*?)`/g, '<code>$1</code>') // Convert inline code
      .trim();

    // Split into paragraphs and format
    const paragraphs = cleaned.split('\n\n').filter(p => p.trim().length > 0);

    if (paragraphs.length === 0) {
      return '<p class="no-content">No readable content available.</p>';
    }

    // Format paragraphs with proper HTML structure
    const formattedParagraphs = paragraphs
      .slice(0, 5) // Limit to 5 paragraphs
      .map(paragraph => {
        const trimmed = paragraph.trim();

        // Check if it's a list item
        if (trimmed.match(/^[-•*]\s/)) {
          return `<li>${this.formatTextWithMarkdown(trimmed.replace(/^[-•*]\s/, ''))}</li>`;
        }

        // Regular paragraph
        return `<p>${this.formatTextWithMarkdown(trimmed)}</p>`;
      });

    // Wrap list items in ul tags
    let formattedContent = formattedParagraphs.join('');
    formattedContent = formattedContent.replace(/(<li>.*?<\/li>)+/g, '<ul>$&</ul>');

    return formattedContent;
  }

  // Detect new summary items for glow effect
  private detectNewSummaryItems(summary: AgentSummary): void {
    this.newSummaryItems.clear();

    // Check for new key insights
    if (summary.keyInsights) {
      summary.keyInsights.forEach(insight => {
        if (!this.previousSummaryState.keyInsights.includes(insight)) {
          this.newSummaryItems.add(`insight-${insight.substring(0, 50)}`);
        }
      });
    }

    // Check for new recommendations
    if (summary.recommendations) {
      summary.recommendations.forEach(recommendation => {
        if (!this.previousSummaryState.recommendations.includes(recommendation)) {
          this.newSummaryItems.add(`recommendation-${recommendation.substring(0, 50)}`);
        }
      });
    }

    // Check for new data points
    if (summary.dataPoints) {
      summary.dataPoints.forEach(dataPoint => {
        if (!this.previousSummaryState.dataPoints.includes(dataPoint)) {
          this.newSummaryItems.add(`datapoint-${dataPoint.substring(0, 50)}`);
        }
      });
    }

    // Update previous state
    this.previousSummaryState = {
      keyInsights: summary.keyInsights ? [...summary.keyInsights] : [],
      recommendations: summary.recommendations ? [...summary.recommendations] : [],
      dataPoints: summary.dataPoints ? [...summary.dataPoints] : []
    };

    // Clear new items after glow animation duration (3 seconds)
    if (this.newSummaryItems.size > 0) {
      setTimeout(() => {
        this.newSummaryItems.clear();
        this.changeDetectorRef.markForCheck();
      }, 3000);
    }
  }

  // Check if a summary item is new (for glow effect)
  isSummaryItemNew(type: string, content: string): boolean {
    const itemKey = `${type}-${content.substring(0, 50)}`;
    return this.newSummaryItems.has(itemKey);
  }

  // Check if a summary section has new items
  hasSummaryNewItems(type: string): boolean {
    const summary = this.summaryModalState.summary;
    if (!summary) return false;

    switch (type) {
      case 'insights':
        return summary.keyInsights?.some(insight =>
          this.isSummaryItemNew('insight', insight)) || false;
      case 'recommendations':
        return summary.recommendations?.some(recommendation =>
          this.isSummaryItemNew('recommendation', recommendation)) || false;
      case 'datapoints':
        return summary.dataPoints?.some(dataPoint =>
          this.isSummaryItemNew('datapoint', dataPoint)) || false;
      default:
        return false;
    }
  }

  // Close summary modal with cleanup
  closeSummaryModal(): void {
    // Clear any pending retry timeouts
    this.clearSummaryRetryTimeouts();

    // Reset retry count
    this.summaryRetryCount = 0;

    // Clear new items tracking
    this.newSummaryItems.clear();

    // Close modal
    this.summaryModalState = {
      isVisible: false,
      isLoading: false,
      summary: null,
      error: null
    };
    this.changeDetectorRef.markForCheck();
  }

  // Enhanced retry summary generation with better agent tracking
  retrySummaryGeneration(): void {
    // Clear any existing retry timeouts first
    this.clearSummaryRetryTimeouts();

    let agentToRetry: any = null;

    // Try to find agent from previous summary
    if (this.summaryModalState.summary) {
      const agentName = this.summaryModalState.summary.agentName;

      agentToRetry = Array.from(this.agentParticipants.values()).find(p => p.agent === this.summaryModalState.summary?.agent);
    }

    // If no agent found from summary, try to find from current modal state
    if (!agentToRetry && this.summaryModalState.error) {
      // Try to extract agent name from error message if it contains agent display name
      const participants = Array.from(this.agentParticipants.values());
      agentToRetry = participants.find(p =>
        this.summaryModalState.error?.includes(p.displayName) ||
        this.summaryModalState.error?.includes(p.name)
      );
    }

    // If still no agent found, use the most recent active agent
    if (!agentToRetry) {
      const participants = Array.from(this.agentParticipants.values());
      if (participants.length > 0) {
        // Sort by last activity and take the most recent
        agentToRetry = participants.sort((a, b) =>
          b.lastActivity.getTime() - a.lastActivity.getTime()
        )[0];
      }
    }

    if (agentToRetry) {
      // Reset retry count for manual retry
      this.summaryRetryCount = 0;
      this.getAgentContributionsFromMemory(agentToRetry);
    } else {
      // No agent available for retry
      this.summaryModalState = {
        ...this.summaryModalState,
        error: 'Cannot retry: No agent available. Please try clicking on an agent name again.'
      };
      this.changeDetectorRef.markForCheck();
    }
  }

  // Helper method to handle summary generation with exponential backoff retry
  private async generateAgentSummaryWithRetry(agent: any, retryCount: number = 0): Promise<void> {
    const maxRetries = 3;
    const baseDelay = 1000; // 1 second base delay

    try {
      await this.getAgentContributionsFromMemory(agent);
    } catch (error) {
      if (retryCount < maxRetries) {
        const delay = baseDelay * Math.pow(2, retryCount); // Exponential backoff
        console.log(`Retrying summary generation in ${delay}ms (attempt ${retryCount + 1}/${maxRetries})`);

        this.safeSetTimeout(() => {
          this.generateAgentSummaryWithRetry(agent, retryCount + 1);
        }, delay);
      } else {
        console.error('Max retries reached for summary generation');
        this.summaryModalState = {
          isVisible: true,
          isLoading: false,
          summary: null,
          error: 'Failed to generate summary after multiple attempts. Please try again later.'
        };
        this.changeDetectorRef.markForCheck();
      }
    }
  }

  auto_grow(element) {
    element.style.height = "5px";
    element.style.height = (element.scrollHeight + 10) + "px";
  }

  // Message filtering functionality for agent-specific messages
  private getAgentMessages(agentName: string): Message[] {
    if (!agentName || !this.messages) {
      return [];
    }

    const normalizedTargetName = this.normalizeAgentName(agentName);

    return this.messages.filter(message => {
      if (message.sender !== 'agent') {
        return false;
      }

      // Check both agentName and displayName properties
      const messageAgentName = message.agentName || '';
      const messageDisplayName = message.displayName || '';

      // Normalize all variations for comparison
      const normalizedMessageAgentName = this.normalizeAgentName(messageAgentName);
      const normalizedMessageDisplayName = this.normalizeAgentName(messageDisplayName);

      return normalizedMessageAgentName === normalizedTargetName ||
        normalizedMessageDisplayName === normalizedTargetName;
    });
  }

  // Helper method to normalize agent names for consistent matching
  private normalizeAgentName(agentName: string): string {
    if (!agentName) {
      return '';
    }

    return agentName
      .toLowerCase()
      .replace(/\s+/g, '') // Remove all spaces
      .replace(/[-_]/g, '') // Remove hyphens and underscores
      .trim();
  }
  async getEventsByActorAndSession(actorId) {
    actorId = (this.agentConfig.getAgent(actorId)?.name || actorId).replaceAll(" ", "_")
    const credentials = await this.awsConfig.getAwsConfig();
    if (!credentials?.credentials) {
      throw new Error('AWS credentials not available');
    }
    const client = new BedrockAgentCoreClient({ region: this.awsConfig.getRegion(), credentials: credentials.credentials }); // e.g., "us-east-1"

    const input = {
      memoryId: this.awsConfig.getMemoryRecordId(),
      actorId: actorId,
      sessionId: this.getCurrentSessionId(),
      // Optional: You can add filters, pagination, and payload inclusion
      // filter: {
      //   branch: {
      //     name: "main", // Example filter by branch name
      //   },
      // },
      // includePayloads: true, // Set to true to include event payloads
      // maxResults: 10, // Max number of events to retrieve
      // nextToken: "your-next-token", // For pagination
    };

    try {
      const command = new ListEventsCommand(input);
      const response = await client.send(command);
      console.log(response)
      return response.events; // Returns an array of event objects
    } catch (error) {
      console.error("Error retrieving events:", error);
      throw error;
    }
  }

  // Prompt generation system for Claude Haiku summarization
  private buildSummaryPrompt(agentMessages: Message[], agentDisplayName: string): string {
    if (!agentMessages || agentMessages.length === 0) {
      return '';
    }

    // Format messages for the prompt
    const formattedMessages = agentMessages.map((message, index) => {
      const timestamp = this.formatMessageTimestamp(message.timestamp);
      const messageType = this.getMessageTypeLabel(message);
      const messageContent = this.cleanMessageContent(message.text);

      return `[${timestamp}] ${messageType}: ${messageContent}`;
    }).join('\n\n');

    // Create the structured prompt for Claude Haiku
    const prompt = `You are an AI assistant helping to summarize the key contributions of a specific agent in a multi-agent conversation about advertising and marketing optimization.

Agent: ${agentDisplayName}
Total Messages: ${agentMessages.length}

Messages from this agent:
${formattedMessages}

Please provide a concise summary with the following structure:
1. Key Insights: Main analytical findings and observations made by this agent
2. Recommendations: Specific actionable recommendations provided by this agent
3. Data Points: Important metrics, numbers, or quantitative insights mentioned by this agent
4. Overall Contribution: Brief summary of this agent's role and value in the conversation

Keep the summary concise but comprehensive, focusing on actionable insights and specific recommendations. Use bullet points for clarity and readability.`;

    return prompt;
  }

  // Helper method to format message timestamps for the prompt
  private formatMessageTimestamp(timestamp: Date): string {
    if (!timestamp) {
      return 'Unknown time';
    }

    const now = new Date();
    const diffMs = now.getTime() - timestamp.getTime();
    const diffMinutes = Math.floor(diffMs / (1000 * 60));
    const diffHours = Math.floor(diffMinutes / 60);

    if (diffMinutes < 1) {
      return 'Just now';
    } else if (diffMinutes < 60) {
      return `${diffMinutes}m ago`;
    } else if (diffHours < 24) {
      return `${diffHours}h ago`;
    } else {
      return timestamp.toLocaleString('en-US', {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
      });
    }
  }

  // Helper method to get message type label for the prompt
  private getMessageTypeLabel(message: Message): string {
    if (!message.data) {
      return 'Response';
    }

    const messageType = message.data.messageType;
    switch (messageType) {
      case 'supervisor-to-collaborator':
        return 'Supervisor Instruction';
      case 'collaborator-response':
        return 'Collaborator Response';
      case 'final-response':
        return 'Final Response';
      case 'visualization-data':
        return 'Visualization Data';
      default:
        return message.data.isThinking ? 'Analysis' : 'Response';
    }
  }

  // Helper method to clean message content for the prompt
  private cleanMessageContent(text: string): string {
    if (!text) {
      return '';
    }

    // Remove excessive whitespace and normalize line breaks
    let cleaned = text
      .replace(/\s+/g, ' ') // Replace multiple spaces with single space
      .replace(/\n\s*\n/g, '\n') // Replace multiple line breaks with single line break
      .trim();

    // Remove markdown formatting that might interfere with Claude's processing
    cleaned = cleaned
      .replace(/\*\*(.*?)\*\*/g, '$1') // Remove bold markdown
      .replace(/\*(.*?)\*/g, '$1') // Remove italic markdown
      .replace(/`(.*?)`/g, '$1') // Remove inline code markdown
      .replace(/```[\s\S]*?```/g, '[Code Block]') // Replace code blocks with placeholder
      .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1'); // Remove markdown links, keep text

    // Truncate very long messages to prevent prompt bloat
    const maxLength = 1000;
    if (cleaned.length > maxLength) {
      cleaned = cleaned.substring(0, maxLength) + '... [truncated]';
    }

    return cleaned;
  }

  @Input() tabConfig: any;

  private messageCache = new Map<string, {
    thinkingContent: string;
    finalContent: string;
    isThinking: boolean;
    visualData?: any;
  }>();

  // NEW: LocalStorage functionality
  private storageKey: string = '';

  constructor(
    private bedrockService: BedrockService,
    private awsConfig: AwsConfigService,
    private agentConfig: AgentConfigService,
    private sessionManager: SessionManagerService,
    private changeDetectorRef: ChangeDetectorRef,
    private tourService: TourService,
    private agentMentionService: AgentMentionService,
    private agentVisualizationService: AgentVisualizationService,
    private transcribeService: TranscribeService,
    private demoTrackingService: DemoTrackingService,
    private pdfExportService: PdfExportService
  ) {
    // Session management is now handled by SessionManagerService
  }

  ngOnInit(): void {
    //this.initializeStorageKey();
    //this.loadMessagesFromStorage();
    this.initializeChat();
    this.updateAvailableAgents();

    // Session initialization is now controlled by parent component via autoCreateSession input
    // Do NOT create session here - wait for parent to determine if existing session should be continued

    // Initialize Intersection Observer for companion panel
    this.initializeMessageIntersectionObserver();

    // Initialize specialist invocation timeout checker (runs every 30 seconds)
    this.specialistTimeoutCheckInterval = setInterval(() => {
      this.checkForTimedOutSpecialistInvocations();
    }, 30000);

    // Make test methods available for debugging
  }

  // ==================== SPECIALIST INVOCATION TRACKING ====================
  // These methods track invoke_specialist and invoke_specialist_with_RAG tool calls
  // to correlate supervisor requests with specialist responses

  // Check for specialist invocations that have timed out
  private checkForTimedOutSpecialistInvocations(): void {
    const now = Date.now();
    let timedOutCount = 0;

    this.pendingSpecialistInvocations.forEach((invocation, toolUseId) => {
      if (invocation.status === 'pending' &&
          now - invocation.timestamp.getTime() > this.SPECIALIST_TIMEOUT_MS) {
        invocation.status = 'timeout';
        timedOutCount++;
        this.logSpecialistInvocation('TIMEOUT', toolUseId, {
          targetAgent: invocation.targetAgent,
          supervisorAgent: invocation.supervisorAgent,
          elapsedMs: now - invocation.timestamp.getTime(),
          prompt: invocation.prompt.substring(0, 100) + '...'
        });
      }
    });

    if (timedOutCount > 0) {
      console.warn(`⚠️ [Specialist Tracker] ${timedOutCount} specialist invocation(s) timed out after ${this.SPECIALIST_TIMEOUT_MS / 1000}s`);
      this.changeDetectorRef.markForCheck();
    }
  }

  // Dedicated logging method for specialist invocation tracking
  private logSpecialistInvocation(action: string, toolUseId: string, details: any): void {
    const pendingCount = Array.from(this.pendingSpecialistInvocations.values())
      .filter(inv => inv.status === 'pending').length;
    const completedCount = Array.from(this.pendingSpecialistInvocations.values())
      .filter(inv => inv.status === 'completed').length;
    const timedOutCount = Array.from(this.pendingSpecialistInvocations.values())
      .filter(inv => inv.status === 'timeout').length;

    console.log(`🔧 [Specialist Tracker] ${action}:`, {
      toolUseId,
      ...details,
      stats: { pending: pendingCount, completed: completedCount, timedOut: timedOutCount },
      timestamp: new Date().toISOString()
    });
  }

  // Track a new specialist invocation (called when supervisor-to-collaborator event is received)
  private trackSpecialistInvocation(
    toolUseId: string,
    toolName: string,
    targetAgent: string,
    supervisorAgent: string,
    prompt: string,
    messageId: string
  ): void {
    const invocation: PendingSpecialistInvocation = {
      toolUseId,
      toolName,
      targetAgent,
      supervisorAgent,
      prompt,
      timestamp: new Date(),
      status: 'pending',
      messageId
    };

    this.pendingSpecialistInvocations.set(toolUseId, invocation);

    this.logSpecialistInvocation('INVOCATION_STARTED', toolUseId, {
      toolName,
      targetAgent,
      supervisorAgent,
      promptPreview: prompt.substring(0, 100) + (prompt.length > 100 ? '...' : '')
    });
  }

  // Mark a specialist invocation as completed (called when collaborator-response is received)
  private completeSpecialistInvocation(toolUseId: string, responseMessageId: string): PendingSpecialistInvocation | null {
    const invocation = this.pendingSpecialistInvocations.get(toolUseId);

    if (!invocation) {
      console.warn(`⚠️ [Specialist Tracker] Received response for unknown toolUseId: ${toolUseId}`);
      return null;
    }

    if (invocation.status === 'completed') {
      console.warn(`⚠️ [Specialist Tracker] Duplicate response for already completed toolUseId: ${toolUseId}`);
      return invocation;
    }

    const responseTimestamp = new Date();
    const durationMs = responseTimestamp.getTime() - invocation.timestamp.getTime();

    invocation.status = 'completed';
    invocation.responseMessageId = responseMessageId;
    invocation.responseTimestamp = responseTimestamp;
    invocation.durationMs = durationMs;

    this.logSpecialistInvocation('INVOCATION_COMPLETED', toolUseId, {
      targetAgent: invocation.targetAgent,
      supervisorAgent: invocation.supervisorAgent,
      durationMs,
      durationFormatted: `${(durationMs / 1000).toFixed(2)}s`
    });

    return invocation;
  }

  // Get the pending invocation for a message (used for UI indicators)
  getPendingInvocationForMessage(messageId: string): PendingSpecialistInvocation | null {
    for (const invocation of this.pendingSpecialistInvocations.values()) {
      if (invocation.messageId === messageId) {
        return invocation;
      }
    }
    return null;
  }

  // Check if a message has a pending specialist invocation
  hasPendingSpecialistInvocation(messageId: string): boolean {
    const invocation = this.getPendingInvocationForMessage(messageId);
    return invocation !== null && invocation.status === 'pending';
  }

  // Check if a message's specialist invocation timed out
  hasTimedOutSpecialistInvocation(messageId: string): boolean {
    const invocation = this.getPendingInvocationForMessage(messageId);
    return invocation !== null && invocation.status === 'timeout';
  }

  // Check if a message's specialist invocation was completed
  hasCompletedSpecialistInvocation(messageId: string): boolean {
    const invocation = this.getPendingInvocationForMessage(messageId);
    return invocation !== null && invocation.status === 'completed';
  }

  // Get the linked response message ID for a completed invocation
  getLinkedResponseMessageId(messageId: string): string | null {
    const invocation = this.getPendingInvocationForMessage(messageId);
    return invocation?.responseMessageId || null;
  }

  // Get specialist invocation stats for debugging
  getSpecialistInvocationStats(): { pending: number; completed: number; timedOut: number; total: number } {
    const invocations = Array.from(this.pendingSpecialistInvocations.values());
    return {
      pending: invocations.filter(inv => inv.status === 'pending').length,
      completed: invocations.filter(inv => inv.status === 'completed').length,
      timedOut: invocations.filter(inv => inv.status === 'timeout').length,
      total: invocations.length
    };
  }

  // Update the status of an invocation message in the messages array (for UI indicators)
  private updateInvocationMessageStatus(messageId: string, status: 'pending' | 'completed' | 'timeout', responseMessageId?: string): void {
    const messageIndex = this.messages.findIndex(m => m.id === messageId);
    if (messageIndex === -1) {
      console.warn(`⚠️ [Specialist Tracker] Could not find message to update: ${messageId}`);
      return;
    }

    const message = this.messages[messageIndex];
    if (message.data) {
      message.data.specialistStatus = status;
      if (responseMessageId) {
        message.data.responseMessageId = responseMessageId;
      }
      
      // Trigger change detection to update UI
      this.changeDetectorRef.markForCheck();
    }
  }

  // Check if a message is waiting for a specialist response (used in template)
  isAwaitingSpecialistResponse(message: Message): boolean {
    return message.data?.specialistStatus === 'pending';
  }

  // Check if a specialist invocation timed out (used in template)
  isSpecialistTimeout(message: Message): boolean {
    return message.data?.specialistStatus === 'timeout';
  }

  // Get the correlated response message for an invocation
  getCorrelatedResponseMessage(message: Message): Message | undefined {
    if (!message.data?.responseMessageId) return undefined;
    return this.messages.find(m => m.id === message.data.responseMessageId);
  }

  // ==================== SPECIALIST PROMPT DISPLAY ====================
  // Track expanded state for specialist prompt sections
  private specialistPromptExpanded = new Map<string, boolean>();

  // Toggle the specialist prompt section visibility
  toggleSpecialistPrompt(messageId: string): void {
    const currentState = this.specialistPromptExpanded.get(messageId) || false;
    this.specialistPromptExpanded.set(messageId, !currentState);
    this.changeDetectorRef.markForCheck();
  }

  // Check if specialist prompt is expanded for a message
  isSpecialistPromptExpanded(messageId: string): boolean {
    return this.specialistPromptExpanded.get(messageId) || false;
  }

  // Extract the target agent name from a supervisor-to-collaborator message
  getTargetAgentFromMessage(message: Message): string {
    if (!message.text) return 'Unknown';
    
    // Try to extract from @AgentName pattern
    const mentionMatch = message.text.match(/@(\w+)/);
    if (mentionMatch) {
      return mentionMatch[1];
    }
    
    // Fallback: check pending invocations
    const invocation = this.getPendingInvocationForMessage(message.id);
    if (invocation) {
      return invocation.targetAgent;
    }
    
    return 'Specialist';
  }

  // Get the specialist prompt content (the actual instructions sent)
  getSpecialistPromptContent(message: Message): string {
    if (!message.text) return '';
    
    // Remove the @AgentName prefix if present for cleaner display
    let content = message.text;
    const mentionMatch = content.match(/^@\w+\s*/);
    if (mentionMatch) {
      content = content.substring(mentionMatch[0].length);
    }
    
    return content.trim();
  }

  // Get clean prompt content (removes @mention prefix)
  getCleanPromptContent(prompt: string | undefined): string {
    if (!prompt) return '';
    
    // Remove the @AgentName prefix if present for cleaner display
    let content = prompt;
    const mentionMatch = content.match(/^@\w+\s*/);
    if (mentionMatch) {
      content = content.substring(mentionMatch[0].length);
    }
    
    return content.trim();
  }

  // ==================== END SPECIALIST PROMPT DISPLAY ====================

  // ==================== END SPECIALIST INVOCATION TRACKING ====================

  // Initialize Intersection Observer for tracking visible messages with visualizations
  private initializeMessageIntersectionObserver(): void {
    if (typeof IntersectionObserver === 'undefined') return;

    this.messageIntersectionObserver = new IntersectionObserver(
      (entries) => {
        entries.forEach(entry => {
          const messageId = entry.target.getAttribute('data-message-id');
          if (!messageId) return;

          if (entry.isIntersecting && entry.intersectionRatio > 0.3) {
            // Message is visible - check if it has visualizations
            this.handleMessageVisible(messageId);
          } else if (!entry.isIntersecting && this.currentVisibleVisualizationMessageId === messageId) {
            // Message with visualization exited view
            this.handleMessageHidden(messageId);
          }
        });
      },
      {
        root: null, // viewport
        rootMargin: '-50px 0px -100px 0px', // Account for header and input
        threshold: [0, 0.3, 0.5, 1.0]
      }
    );
  }

  // Handle when a message becomes visible
  private handleMessageVisible(messageId: string): void {
    const message = this.messages.find(m => m.id === messageId);
    if (!message || message.sender !== 'agent') return;

    // Get formatted message to check for visualizations
    const formatted = this.getFormattedAgentMessage(message);
    if (!formatted.visualData || !this.hasAnyVisualizationData(formatted.visualData)) return;

    // Update companion panel with this message's visualization
    this.currentVisibleVisualizationMessageId = messageId;
    this.companionPanelMessage = {
      id: messageId,
      agentName: message.agentName || 'Agent',
      visualData: formatted.visualData,
      timestamp: message.timestamp
    };
    this.companionPanelAgentColor = this.getAgentColor(message.agentName);
    this.showCompanionPanel = true;
    this.changeDetectorRef.markForCheck();
  }

  // Handle when a message exits view
  private handleMessageHidden(messageId: string): void {
    if (this.currentVisibleVisualizationMessageId === messageId) {
      this.showCompanionPanel = false;
      this.currentVisibleVisualizationMessageId = null;
      this.changeDetectorRef.markForCheck();
    }
  }

  // Check if visualData has any actual visualization content
  private hasAnyVisualizationData(visualData: any): boolean {
    if (!visualData) return false;
    return !!(
      visualData.metricData || visualData.channelAllocations || visualData.channelCards ||
      visualData.segmentCards || visualData.creativeData || visualData.timelineData ||
      visualData.histogramData || visualData.doubleHistogramData || visualData.barChartData ||
      visualData.donutChartData || visualData.adcpInventoryData
    );
  }

  // Observe a message element for visibility tracking
  observeMessageElement(element: Element, messageId: string): void {
    if (!this.messageIntersectionObserver || this.observedMessageElements.has(messageId)) return;
    
    element.setAttribute('data-message-id', messageId);
    this.messageIntersectionObserver.observe(element);
    this.observedMessageElements.set(messageId, element);
  }

  // Close companion panel
  onCloseCompanionPanel(): void {
    this.showCompanionPanel = false;
    this.currentVisibleVisualizationMessageId = null;
    this.changeDetectorRef.markForCheck();
  }

  // Initialize session with user information
  private initializeSession(): void {
    const loginId = this.currentUser?.signInDetails?.loginId;
    const customerName = this.demoTrackingService.getCurrentCustomer();

    const sessionInfo = this.sessionManager.initializeSession(loginId, customerName,this.tabId);
    console.log(`💬 Chat Interface initialized session: ${sessionInfo.sessionId}`);
  }


  isVisualizationChunk(messageType) {
    return messageType.endsWith('-visualization')
  }

  /**
   * Check if a visualization type is an ADCP protocol visualization.
   * ADCP visualizations follow the naming convention: adcp_<type>
   * This allows easy extension for future ADCP visualization types.
   * @param visualizationType The visualization type string to check
   * @returns true if the type starts with 'adcp_'
   */
  isAdcpVisualizationType(visualizationType: string): boolean {
    return visualizationType?.startsWith('adcp_') || false;
  }

  /**
   * Registry of AdCP tool result detectors.
   * Each detector defines how to identify and visualize a specific AdCP tool result type.
   * 
   * To add a new AdCP visualization:
   * 1. Add an entry to this registry with the visualization type, data property, and detector function
   * 2. Create the corresponding visualization component
   * 3. Add the data property to the type definitions (popoverVisualData, visualData, etc.)
   * 4. Add the component to the HTML templates
   */
  private adcpToolResultDetectors: Array<{
    visualizationType: string;
    dataProperty: string;
    messageText: (data: any) => string;
    detect: (parsed: any) => boolean;
  }> = [
    {
      // AdCP get_products - inventory discovery
      visualizationType: 'adcp_get_products-visualization',
      dataProperty: 'adcpInventoryData',
      messageText: (data) => `Found ${data.products?.length || 0} advertising products`,
      detect: (parsed) => {
        if (!parsed || !Array.isArray(parsed.products) || parsed.products.length === 0) return false;
        const firstProduct = parsed.products[0];
        const hasProductId = firstProduct.product_id !== undefined;
        const hasName = firstProduct.name !== undefined || firstProduct.product_name !== undefined;
        const hasPricing = firstProduct.pricing_options !== undefined || 
                          firstProduct.cpm_usd !== undefined || firstProduct.rate !== undefined;
        const hasPublisher = firstProduct.publisher_properties !== undefined ||
                            firstProduct.publisher_name !== undefined || firstProduct.publisher_domain !== undefined;
        return hasProductId;
        //return hasProductId && (hasName || hasPricing || hasPublisher);
      }
    },
    {
      // AdCP create_media_buy - media buy confirmation
      visualizationType: 'adcp_media_buy',
      dataProperty: 'adcpMediaBuyData',
      messageText: (data) => `Media buy ${data.status === 'completed' ? 'created' : data.status}: ${data.media_buy_id || data.buyer_ref || ''}`,
      detect: (parsed) => {
        return parsed && (parsed.media_buy_id !== undefined || parsed.buyer_ref !== undefined) &&
               (parsed.packages !== undefined || parsed.status !== undefined);
      }
    },
    {
      // AdCP get_media_buy_delivery - delivery metrics
      visualizationType: 'adcp_delivery',
      dataProperty: 'adcpDeliveryData',
      messageText: (data) => `Delivery report: ${data.summary?.impressions_delivered?.toLocaleString() || 0} impressions`,
      detect: (parsed) => {
        return parsed && parsed.media_buy_id !== undefined && 
               (parsed.summary !== undefined || parsed.packages !== undefined) &&
               (parsed.reporting_period !== undefined || parsed.impressions_delivered !== undefined);
      }
    },
    {
      // AdCP get_signals - signal discovery
      visualizationType: 'adcp_signals',
      dataProperty: 'adcpSignalsData',
      messageText: (data) => `Found ${data.signals?.length || 0} audience signals`,
      detect: (parsed) => {
        if (!parsed || !Array.isArray(parsed.signals) || parsed.signals.length === 0) return false;
        const firstSignal = parsed.signals[0];
        return firstSignal.signal_id !== undefined || firstSignal.segment_id !== undefined;
      }
    }
  ];

  /**
   * Detect if a tool result contains AdCP data that should be visualized.
   * Uses the registry of detectors to identify the visualization type.
   * 
   * @param toolResultText The raw text content of the tool result
   * @returns Object with visualizationType, dataProperty, data, and messageText if detected, null otherwise
   */
  detectAdcpToolResultVisualization(toolResultText: string): {
    visualizationType: string;
    dataProperty: string;
    data: any;
    messageText: string;
  } | null {
    if (!toolResultText || typeof toolResultText !== 'string') {
      return null;
    }
    
    try {
      const parsed = JSON.parse(toolResultText);
      
      // Try each detector in order
      for (const detector of this.adcpToolResultDetectors) {
        if (detector.detect(parsed)) {
          // Add visualizationType if not present
          if (!parsed.visualizationType) {
            parsed.visualizationType.indexOf(detector.visualizationType)>0||detector.visualizationType.indexOf(parsed.visualizationType)>0;
          }
          return {
            visualizationType: detector.visualizationType,
            dataProperty: detector.dataProperty,
            data: parsed,
            messageText: detector.messageText(parsed)
          };
        }
      }
      
      return null;
    } catch (e) {
      return null;
    }
  }

  /**
   * Detect embedded AdCP visualization data in message text wrapped in <visualization-data> XML tags.
   * This handles the case where agents output visualization data inline in their responses.
   * 
   * @param messageText The raw message text that may contain embedded visualization data
   * @returns Object with visualization info and remaining text, or null if not detected
   */
  detectEmbeddedAdcpVisualization(messageText: string): {
    visualizationType: string;
    dataProperty: string;
    data: any;
    messageText: string;
    remainingText: string;
  } | null {
    if (!messageText || typeof messageText !== 'string') {
      return null;
    }
    
    // Look for <visualization-data type="adcp_..."> or type='adcp_...'> tags (handles both single and double quotes)
    const vizDataRegex = /<visualization-data[^>]*type=['"]?(adcp_[^'">\s]+)['"]?[^>]*>([\s\S]*?)<\/visualization-data>/;
    const match = messageText.match(vizDataRegex);
    
    if (!match) {
      // Also try to detect raw JSON with products array that looks like AdCP get_products result
      const toolResult = this.detectAdcpToolResultVisualization(messageText);
      if (toolResult) {
        return {
          ...toolResult,
          remainingText: '' // Raw JSON detection consumes the whole message
        };
      }
      return null;
    }
    
    const visualizationType = match[1];
    const jsonContent = match[2].trim();
    
    try {
      const parsed = JSON.parse(jsonContent);
      
      // Find the matching detector for this visualization type
      const detector = this.adcpToolResultDetectors.find(d => d.visualizationType === visualizationType);
      
      if (detector) {
        // Add visualizationType if not present
        if (!parsed.visualizationType) {
          parsed.visualizationType = visualizationType;
        }
        
        // Remove the visualization data from the message text
        const remainingText = messageText.replace(match[0], '').trim();
        
        return {
          visualizationType: detector.visualizationType,
          dataProperty: detector.dataProperty,
          data: parsed,
          messageText: detector.messageText(parsed),
          remainingText: remainingText
        };
      }
      
      // If no specific detector, use generic handling for adcp_ types
      if (visualizationType.startsWith('adcp_')) {
        const dataProperty = visualizationType === 'adcp_get_products-visualization' ? 'adcpInventoryData' :
                            visualizationType === 'adcp_media_buy-visualization' ? 'adcpMediaBuyData' :
                            visualizationType === 'adcp_delivery-visualization' ? 'adcpDeliveryData' :
                            visualizationType === 'adcp_signals-visualization' ? 'adcpSignalsData' :
                            'adcpInventoryData'; // fallback
        
        const remainingText = messageText.replace(match[0], '').trim();
        
        return {
          visualizationType: visualizationType,
          dataProperty: dataProperty,
          data: parsed,
          messageText: parsed.message || `${visualizationType} data received`,
          remainingText: remainingText
        };
      }
      
      return null;
    } catch (e) {
      console.warn('Failed to parse embedded visualization JSON:', e);
      return null;
    }
  }

  closeContextSelector(): void {
    this.closeContextSelectorEvent.emit();
  }

  // Context panel methods
  getContextButtonLabel(): string {
    return this.tabConfig?.contextButtonLabel || '... context';
  }

  // Publisher selection method
  onPublisherSelect(publisher: Publisher): void {
    this.publisherSelect.emit(publisher);
  }

  // Context panel show/hide methods
  onContextPanelShow(): void {
    this.contextPanelShow.emit();
  }

  onContextPanelHide(): void {
    this.contextPanelHide.emit();
  }



  ngOnChanges(changes: SimpleChanges): void {
    // Reinitialize storage key when contextData changes
    if (changes['contextData'] && !changes['contextData'].firstChange) {
      const oldStorageKey = this.storageKey;
      //this.initializeStorageKey();

      // If storage key changed, reload messages
      if (oldStorageKey !== this.storageKey) {
        //this.loadMessagesFromStorage();
      }
    }

    // Update session when currentUser changes
    if (changes['currentUser'] && !changes['currentUser'].firstChange) {
      this.initializeSession();
    }
  }

  // Generate a unique storage key for this chat instance
  private initializeStorageKey(): void {
    // Try to get tabId from context data first (most specific)
    const tabId = this.contextData?.tabContext?.id || this.contextData?.tabId;

    if (tabId) {
      this.storageKey = `chat-messages-${tabId}`;
    } else {
      // Fallback to agent type for backward compatibility
      this.storageKey = `chat-messages-${this.agentType || 'default'}`;
    }

  }

  // Save messages to localStorage
  private saveMessagesToStorage(): void {
    try {
      const messagesToSave = this.messages.map(msg => ({
        id: msg.id,
        text: msg.text,
        sender: msg.sender,
        timestamp: msg.timestamp,
        type: msg.type,
        agentName: msg.agentName,
        displayName: msg.displayName,
        data: msg.data
      }));

      localStorage.setItem(this.storageKey, JSON.stringify(messagesToSave));
    } catch (error) {
      console.warn('❌ Failed to save messages to localStorage:', error);
    }
  }

  // Load messages from localStorage
  private loadMessagesFromStorage(): void {
    try {
      const storedMessages = localStorage.getItem(this.storageKey);
      if (storedMessages) {
        const parsedMessages = JSON.parse(storedMessages);
        this.messages = parsedMessages.map((msg: any) => ({
          ...msg,
          timestamp: new Date(msg.timestamp) // Convert timestamp back to Date object
        }));
        this.shouldScrollToBottom = true;
      }
    } catch (error) {
      console.warn('❌ Failed to load messages from localStorage:', error);
      this.messages = []; // Reset to empty array on error
    }
  }

  // Clear messages from both memory and localStorage
  clearMessages(): void {
    this.messages = [];

    // Clear source tracking maps
    this.knowledgeBaseSources.clear();
    this.traceQueries.clear();
    this.requestSources.clear();
    this.requestToMessageMap.clear();

    // Clear other state
    this.currentAgentResponses.clear();
    this.recentMessageHashes.clear();
    this.referencesExpanded.clear();
    this.thinkingCollapsed.clear();

    // Clear agent participants to reset color assignments
    this.agentParticipants.clear();

    // Clear agent first message tracking to reset context inclusion
    this.agentFirstMessages.clear();

    // Clear agent color cache to ensure fresh color assignments
    this.agentConfig.clearAgentColorCache();

    try {
      localStorage.removeItem(this.storageKey);

    } catch (error) {
      console.warn('❌ Failed to clear messages from localStorage:', error);
    }
    this.shouldScrollToBottom = true;

  }

  // Get current session ID from session manager
  private getCurrentSessionId(): string {
    const loginId = this.currentUser?.signInDetails?.loginId;
    const customerName = this.demoTrackingService.getCurrentCustomer();
    return this.sessionManager.getCurrentSessionId(loginId, customerName);
  }

  // Helper method to create a hash of message content for duplicate detection
  private createMessageHash(text: string): string {
    // Create a simple hash by removing whitespace and taking first 100 chars
    const normalized = text.replace(/\s+/g, ' ').trim().toLowerCase().substring(0, 100);
    return btoa(normalized).substring(0, 20); // Base64 encode and truncate
  }

  // Helper method to check if a message is a duplicate
  private isDuplicateMessage(text: string): boolean {
    const hash = this.createMessageHash(text);

    if (this.recentMessageHashes.has(hash)) {
      //console.warn('🔄 Duplicate message detected, skipping:', text.substring(0, 50) + '...');
      return true;
    }

    // Add to recent hashes and clean up old ones (keep only last 10)
    this.recentMessageHashes.add(hash);
    if (this.recentMessageHashes.size > 10) {
      const firstHash = this.recentMessageHashes.values().next().value;
      if (firstHash) {
        this.recentMessageHashes.delete(firstHash);
      }
    }

    return false;
  }

  // Helper method to remove duplicate agent mentions and keep only the last one
  private removeDuplicateAgentMentions(message: string, targetAgentType: EnrichedAgent): string {
    if (!message.trim()) {
      return `@[${targetAgentType.name}] `;
    }

    // Regular expression to match agent mentions in format @[Agent Name] or @AgentName
    const agentMentionRegex = /@\[([^\]]+)\]|@(\w+(?:\s+\w+)*)/g;

    // Find all agent mentions in the message
    const mentions: Array<{ match: string, index: number, length: number }> = [];
    let match;

    while ((match = agentMentionRegex.exec(message)) !== null) {
      mentions.push({
        match: match[0],
        index: match.index,
        length: match[0].length
      });
    }

    // If no mentions found, add the target agent mention at the beginning
    if (mentions.length === 0) {
      return `@[${this.getAgentDisplayName(targetAgentType)}] ${message}`;
    }

    // If only one mention found, ensure it matches the target agent, otherwise replace it
    if (mentions.length === 1) {
      const existingMention = mentions[0];
      const targetMention = `@[${this.getAgentDisplayName(targetAgentType)}]`;

      // If the existing mention is the same as target, keep it
      if (existingMention.match === targetMention) {
        return message;
      }

      // Replace the existing mention with the target mention
      return message.substring(0, existingMention.index) +
        targetMention +
        message.substring(existingMention.index + existingMention.length);
    }

    // Multiple mentions found - remove all and add target mention at the beginning

    // Sort mentions by index in descending order to remove from right to left (preserves indices)
    mentions.sort((a, b) => b.index - a.index);

    let cleanedMessage = message;
    for (const mention of mentions) {
      cleanedMessage = cleanedMessage.substring(0, mention.index) +
        cleanedMessage.substring(mention.index + mention.length);
    }

    // Remove any extra whitespace left by removed mentions
    cleanedMessage = cleanedMessage.replace(/\s+/g, ' ').trim();

    // Add the target agent mention at the beginning
    return `@[${this.getAgentDisplayName(targetAgentType)}] ${cleanedMessage}`;
  }

  ngAfterViewChecked() {
    if (this.shouldScrollToBottom) {
      this.scrollToBottom();
      this.shouldScrollToBottom = false;

    }

    // Add click handlers to agent mentions in message text
    this.addAgentMentionClickHandlers();

    // Observe message elements for companion panel visibility tracking
    this.observeNewMessageElements();
  }

  // Observe new message elements for Intersection Observer
  private observeNewMessageElements(): void {
    if (!this.messageIntersectionObserver || !this.messagesContainer) return;

    const container = this.messagesContainer.nativeElement;
    const messageElements = container.querySelectorAll('.message.agent');

    messageElements.forEach((element: Element) => {
      // Find the message ID from the DOM or data attribute
      const messageId = element.getAttribute('data-message-id');
      if (messageId && !this.observedMessageElements.has(messageId)) {
        this.observeMessageElement(element, messageId);
      }
    });
  }

  private scrollToBottom(): void {
    try {
      if (this.messagesContainer) {
        // Use setTimeout to ensure DOM has updated before scrolling
        this.safeSetTimeout(() => {
          if (this.messagesContainer) {
            this.messagesContainer.nativeElement.scrollTop = this.messagesContainer.nativeElement.scrollHeight;
          }
        }, 0);
      }
    } catch (err) {
      console.warn('Could not scroll to bottom:', err);
    }
  }

  private initializeChat(): void {
    // Clear any previous state
    this.recentMessageHashes.clear();
    this.referencesExpanded.clear();
    this.thinkingCollapsed.clear();
    this.agentFirstMessages.clear();
    this.isFirstMessage = true;

    this.messages = [{
      id: '1',
      text: `This playground shows various applications of Amazon Bedrock AgentCore Agents in the Advertising industry. Select one of the example scenarios or speak or type a question to get started.`,
      sender: 'agent',
      timestamp: new Date(),
      data: {
        isWelcomeMessage: true
      }
    }];
  }

  sendMessage(agentType: EnrichedAgent | null): void {
    // Check for empty message
    if (!agentType) agentType = this.lastAgent;
    if (!this.currentMessage.trim() && this.attachedFiles.length === 0) {
      console.warn('⚠️ Cannot send message: empty message');
      return;
    }

    // Check if already loading
    if (this.isLoading) {
      console.warn('⚠️ Cannot send message: already loading');
      return; // Prevent multiple simultaneous requests
    }
    // Check if there's an agent mention in the message
    const parsedAgent = this.agentMentionService.parseAgentMentions(this.currentMessage);

    // Use mentioned agent, provided agentType, selectedAgentForMessage, or lastAgentName as fallback
    const targetAgent = parsedAgent.mentionedAgent || agentType || this.selectedAgentForMessage || this.lastAgent;

    // If still no agent is determined, prompt user to select one
    if (!targetAgent) {
      this.pendingMessage = this.currentMessage;
      this.pendingAttachedFiles = [...this.attachedFiles];
      this.showAgentSelector = true;
      return;
    }
    else {
      console.log('✅ Sending message to agent:', targetAgent, 'isLoading:', this.isLoading);

      // Clean up duplicate agent mentions and keep only the last one
      const cleanedMessage = this.removeDuplicateAgentMentions(this.currentMessage, targetAgent);

      const userMessage: Message = {
        id: `user-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
        text: cleanedMessage,
        sender: 'user',
        timestamp: new Date(),
        attachedFiles: this.attachedFiles.length > 0 ? [...this.attachedFiles] : undefined
      };

      // Use immutable array update for consistency
      this.messages = [...this.messages, userMessage];
      this.messagesUpdated.emit(this.messages);
      //this.saveMessagesToStorage();
      this.shouldScrollToBottom = true; // Trigger auto-scroll
      this.changeDetectorRef.markForCheck(); // OnPush optimization
      this.messageEvent.emit({ message: this.currentMessage, agent: targetAgent });

      // Update lastAgentName for future use
      this.lastAgent = targetAgent;

      // Get agent response
      this.getAgentResponse(this.currentMessage, targetAgent, this.attachedFiles);
      this.currentMessage = '';
      this.selectedAgentForMessage = targetAgent;
      this.attachedFiles = []; // Clear attached files after sending
    }
  }

  selectScenario(scenario: ScenarioExample, index: number): void {
    this.selectedScenarioIndex = index;
    console.log('selected scenario', scenario)
    // Start flip animation
    this.safeSetTimeout(() => {
      this.currentMessage = scenario.query;
      this.scenariosPanelToggle.emit(); // Close panel after selection via parent
      this.selectedScenarioIndex = null;
      let agent = this.agentConfig.getAgent(scenario.agentType);
      if (agent)
        this.sendMessage(agent);
      else if (this.lastAgent)
        this.sendMessage(this.lastAgent)
    }, 300); // Half of the flip animation duration
  }

  toggleScenariosPanel(): void {
    this.scenariosPanelToggle.emit(); // Delegate to parent
  }

  closeScenariosPanel(): void {
    this.scenariosPanelToggle.emit(); // Delegate to parent
  }

  showScenariosAgain(): void {
    this.showScenarios = true;
  }

  private async getAgentResponse(userQuery: string, agent: EnrichedAgent, attachedFiles: AttachedFile[] = []): Promise<void> {
    this.isLoading = true;
    // console.log('agent type: '+agentType)
    // Clear any existing agent responses for this session
    this.currentAgentResponses.clear();

    // Clear source tracking to prevent accumulation from previous requests
    this.knowledgeBaseSources.clear();
    this.traceQueries.clear();
    this.requestSources.clear();
    this.requestToMessageMap.clear();

    // Create a supervisor message to start the response sequence
    const messageId = `${agent.name}_${agent.teamName}-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
    const message: Message = {
      id: messageId,
      text: '',
      sender: 'agent',
      timestamp: new Date(),
      type: 'text',
      agentName: agent.name,
      displayName: agent.name,
      data: {
        isThinking: true,
        finalResponse: '',
        thinkingHistory: [] as string[],
        lastTrace: ''
      }
    };

    // Use immutable array update for better change detection
    //this.addMessageIfNotDuplicate(message, agent, this.getAgentDisplayName(agentType));
    //this.saveMessagesToStorage();
    this.currentAgentResponses.set(agent, message);

    try {
      // Check if there's an agent mention in the user query
      const parsedMessage = this.agentMentionService.parseAgentMentions(userQuery);

      // FIXED: Respect user's agent selection first, then check for mentions, finally fall back to supervisor
      let targetAgent: EnrichedAgent = agent; // Use the explicitly selected agent type first
      if (!targetAgent && parsedMessage.mentionedAgent) {
        targetAgent = parsedMessage.mentionedAgent; // Use mentioned agent if no explicit selection
      }
      // Use the cleaned text (without mentions) for the actual query
      const cleanedQuery = parsedMessage.cleanedText || userQuery;
      let displayName = agent.name
      // Log demo scenario to CloudWatch
      try {
        displayName = this.getAgentDisplayName(targetAgent);
        await this.demoTrackingService.logScenario(
          cleanedQuery,
          displayName,
          targetAgent.name || 'unknown'
        );
      } catch (error) {
        console.warn('⚠️ Failed to log demo scenario:', error);
        // Don't block the user experience if logging fails
      }

      // Prepare the final query with context data for the selected agent (only on first message to this agent)
      let finalQuery = cleanedQuery;
      const isFirstMessageToAgent = !this.agentFirstMessages.has(displayName);

      if (this.contextData && isFirstMessageToAgent) {
        const contextJson = this.formatContextData();
        if(contextJson.length > 0)
          finalQuery = `User Question: ${cleanedQuery}\n\nContext:${contextJson}`;
        else finalQuery =`User Question: ${cleanedQuery}`
        console.log(finalQuery)
        // Mark that we've sent the first message to this agent
        this.agentFirstMessages.set(displayName, true);

      } else if (isFirstMessageToAgent) {
        // Mark first message even without context data
        finalQuery =`User Question: ${cleanedQuery}`
        this.agentFirstMessages.set(displayName, true);
      }

      // Use custom session ID from session manager
      const customSessionId = this.getCurrentSessionId();

      // Use streaming method for real-time feedback with custom session ID

      const streamingSubscription = this.bedrockService.invokeAgentWithStreaming(agent, finalQuery, customSessionId, attachedFiles).subscribe({
        next: (event) => {

          // Handle structured events from the new trace processor
          if (event.agentName) {
            const agentName = event.agentName;
            let agentInMessage = agent;
            if (agentName != agent.name && agent.teamName) {
              let matchingAgent = this.agentConfig.getAgentByAgentNameAndTeam(agentName, agent.teamName);
              agentInMessage = matchingAgent || agent
            }
            else
              console.log(`could not find agent by name ${agentName} and team name ${agent.teamName}`);
            let displayName = `${agentName}`;
            let messageText = event.data;
            const messageType = event.messageType ? event.messageType : (event.type ? event.type : 'final-response');
            console.log(agentName + " messageType: " + messageType)

            // Handle different message types - create separate messages for group thread display
            switch (messageType) {
              case 'supervisor-to-collaborator':
                // Create a separate message for each supervisor-to-collaborator interaction

                const supervisorMessageId = `supervisor-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
                const supervisorMessage: Message = {
                  id: supervisorMessageId,
                  text: messageText,
                  sender: 'agent',
                  timestamp: new Date(),
                  type: 'text',
                  agentName: agentName,
                  displayName: displayName,
                  data: {
                    isThinking: false,
                    finalResponse: messageText,
                    messageType: 'supervisor-to-collaborator'
                  }
                };

                // Track specialist invocations (invoke_specialist / invoke_specialist_with_RAG)
                const metadata = (event as any).metadata;
                console.log('🔍 [Specialist Tracker] Supervisor-to-collaborator event:', {
                  hasMetadata: !!metadata,
                  toolUseId: metadata?.toolUseId,
                  name: metadata?.name,
                  agentName,
                  messagePreview: messageText.substring(0, 100)
                });
                
                // Track if we have a toolUseId - this indicates an agent invocation
                if (metadata?.toolUseId) {
                  const toolUseId = metadata.toolUseId;
                  const toolName = metadata.name || 'unknown_tool';
                  
                  // Extract target agent from the message text (format: @AgentName ...)
                  const targetAgentMatch = messageText.match(/@(\w+)/);
                  const targetAgent = targetAgentMatch ? targetAgentMatch[1] : 'unknown';
                  
                  // Use dedicated tracking method
                  this.trackSpecialistInvocation(
                    toolUseId,
                    toolName,
                    targetAgent,
                    agentName,
                    messageText,
                    supervisorMessageId
                  );
                  
                  // Add toolUseId to message data for UI correlation
                  supervisorMessage.data.toolUseId = toolUseId;
                  supervisorMessage.data.specialistStatus = 'pending';
                }

                this.addMessageIfNotDuplicate(supervisorMessage, agentInMessage);
                //this.saveMessagesToStorage();
                //this.updateAgentParticipant(agentName, displayName);
                this.shouldScrollToBottom = true;

                // Trigger change detection
                if (!this.changeDetectionPending) {
                  this.changeDetectionPending = true;
                  this.safeSetTimeout(() => {
                    this.changeDetectorRef.detectChanges();
                    this.changeDetectionPending = false;
                  }, 50);
                }
                break;
              case 'visualization-data':
                // Handle visualization data separately - just add the message
                {
                  const vizMessageId = `viz-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
                  const vizMessage: Message = {
                    id: vizMessageId,
                    text: messageText,
                    sender: 'agent',
                    timestamp: new Date(),
                    type: 'text',
                    agentName: agentName,
                    displayName: displayName,
                    data: {
                      isThinking: false,
                      finalResponse: messageText,
                      messageType: 'visualization-data'
                    }
                  };
                  this.addMessageIfNotDuplicate(vizMessage, agentInMessage);
                  // Trigger change detection
                  if (!this.changeDetectionPending) {
                    this.changeDetectionPending = true;
                    this.safeSetTimeout(() => {
                      this.changeDetectorRef.detectChanges();
                      this.changeDetectionPending = false;
                    }, 50);
                  }
                }
                break;
              case 'collaborator-response':
                // Create a separate message for each collaborator response - SIMPLIFIED to match working version
                {
                  const collaboratorMessageId = `collaborator-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
                  
                  // Get the toolUseId from metadata to link to original prompt
                  const collaboratorMetadata = (event as any).metadata;
                  const collaboratorToolUseId = collaboratorMetadata?.toolUseId;
                  
                  // Look up the original invocation to get the prompt
                  let originalPrompt: string | undefined;
                  let supervisorAgent: string | undefined;
                  
                  console.log('🔍 [Collaborator Response] Looking for invocation:', {
                    collaboratorToolUseId,
                    agentName,
                    pendingInvocationsCount: this.pendingSpecialistInvocations.size,
                    pendingKeys: Array.from(this.pendingSpecialistInvocations.keys())
                  });
                  
                  if (collaboratorToolUseId) {
                    const invocation = this.pendingSpecialistInvocations.get(collaboratorToolUseId);
                    if (invocation) {
                      originalPrompt = invocation.prompt;
                      supervisorAgent = invocation.supervisorAgent;
                      // Mark the invocation as completed
                      this.completeSpecialistInvocation(collaboratorToolUseId, collaboratorMessageId);
                      console.log('✅ [Collaborator Response] Found invocation by toolUseId:', collaboratorToolUseId);
                    }
                  }
                  
                  // Fallback: If not found by toolUseId, search by target agent name
                  if (!originalPrompt) {
                    for (const [toolUseId, invocation] of this.pendingSpecialistInvocations.entries()) {
                      if (invocation.targetAgent === agentName && invocation.status === 'pending') {
                        originalPrompt = invocation.prompt;
                        supervisorAgent = invocation.supervisorAgent;
                        // Mark as completed
                        this.completeSpecialistInvocation(toolUseId, collaboratorMessageId);
                        console.log('✅ [Collaborator Response] Found invocation by agent name fallback:', agentName, toolUseId);
                        break;
                      }
                    }
                  }
                  
                  console.log('🔍 [Collaborator Response] Final result:', {
                    hasOriginalPrompt: !!originalPrompt,
                    supervisorAgent,
                    promptPreview: originalPrompt?.substring(0, 100)
                  });
                  
                  const collaboratorMessage: Message = {
                    id: collaboratorMessageId,
                    text: messageText,
                    sender: 'agent',
                    timestamp: new Date(),
                    type: 'text',
                    agentName: agentName,
                    displayName: displayName,
                    data: {
                      isThinking: false,
                      finalResponse: messageText,
                      messageType: 'collaborator-response',
                      toolUseId: collaboratorToolUseId,
                      originalPrompt: originalPrompt,
                      supervisorAgent: supervisorAgent
                    }
                  };
                  console.log('response from collaborator:', event, 'originalPrompt:', originalPrompt?.substring(0, 100))

                  this.addMessageIfNotDuplicate(collaboratorMessage, agentInMessage)
                  //this.saveMessagesToStorage();
                  // Trigger change detection
                  if (!this.changeDetectionPending) {
                    this.changeDetectionPending = true;
                    this.safeSetTimeout(() => {
                      this.changeDetectorRef.detectChanges();
                      this.changeDetectionPending = false;
                    }, 50);
                  }
                }
                break;

              case 'final-response':
              case 'tool-result':
              case 'response':
                // Always create a new message for final responses - don't update existing ones
                messageText = this.removeLastFinalResponseText(agentName, messageText);
                const finalResponseMessage: Message = {
                  id: `${agentName}-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
                  text: messageText,
                  sender: 'agent',
                  timestamp: new Date(),
                  type: 'text',
                  agentName: agentName,
                  displayName: displayName,
                  data: {
                    isThinking: false,
                    finalResponse: messageText,
                    messageType: messageType
                  }
                };
                if (this.messages.findIndex(m => m.text == finalResponseMessage.text) == -1) {
                  this.addMessageIfNotDuplicate(finalResponseMessage, agentInMessage);
                }
                break;



              case 'knowledge-base-query':
                // Handle knowledge base search queries
                try {
                  const queryData = JSON.parse(messageText);
                  if (queryData.searchQuery && queryData.traceId) {
                    this.traceQueries.set(queryData.traceId, queryData.searchQuery);
                  }
                } catch (parseError) {
                  console.warn('Failed to parse KB query data:', parseError);
                }
                break;
              case 'sources-update':
                {
                  try {
                    console.log(messageText)
                  }
                  catch (ex) {
                    console.log(ex)
                  }
                }
                break;
              case 'knowledge-base-sources':
                // Handle knowledge base sources
                try {
                  const sourcesData = JSON.parse(messageText);
                  if (sourcesData.sources) {
                    // Extract base request ID (remove -0, -1, -2 suffix)
                    const baseRequestId = sourcesData.traceId.replace(/-\d+$/, '');

                    // Add search query to sources if available
                    const searchQuery = this.traceQueries.get(sourcesData.traceId);
                    const sourcesWithQuery = sourcesData.sources
                      .filter(s => s.location?.s3Location?.uri?.indexOf('visual_templates') == -1)
                      .map((source: any) => ({
                        ...source,
                        searchQuery: searchQuery,
                        traceId: sourcesData.traceId
                      }));

                    // Store individual trace sources
                    this.knowledgeBaseSources.set(sourcesData.traceId, sourcesWithQuery);

                    // Simply collect all sources for this base request ID (deduplication happens in modal)
                    const existingSources = this.requestSources.get(baseRequestId) || [];
                    const allSources = [...existingSources, ...sourcesWithQuery];
                    this.requestSources.set(baseRequestId, allSources);

                    // Find or track the message for this base request ID
                    let targetMessageId = this.requestToMessageMap.get(baseRequestId);
                    let messageIndex = -1;

                    if (targetMessageId) {
                      // Find the message by ID
                      messageIndex = this.messages.findIndex(msg => msg.id === targetMessageId);
                    } else {
                      // Find the most recent agent message without sources and associate it with this request
                      let lastAgentMessageIndex = [...this.messages].reverse().findIndex(msg =>
                        msg.sender === 'agent' && msg.agentName === agentName &&
                        !msg.sources && (msg.data?.messageType !== 'creative-visualization')
                      );

                      if (lastAgentMessageIndex >= 0) {
                        messageIndex = this.messages.length - 1 - lastAgentMessageIndex;
                        targetMessageId = this.messages[messageIndex].id;
                        this.requestToMessageMap.set(baseRequestId, targetMessageId);
                      }
                    }

                    if (messageIndex >= 0 && targetMessageId) {
                      // Get existing sources object or create new one
                      const existingSourcesObj = this.messages[messageIndex].sources || {};

                      // Update sources for this agent
                      const updatedMessage = {
                        ...this.messages[messageIndex],
                        sources: {
                          ...existingSourcesObj,
                          allSources
                        }
                      };

                      this.messages = [
                        ...this.messages.slice(0, messageIndex),
                        updatedMessage,
                        ...this.messages.slice(messageIndex + 1)
                      ];
                      //this.saveMessagesToStorage();
                      this.shouldScrollToBottom = true;
                      // Update bedrock service with current messages
                      this.bedrockService.updateChatMessages(this.messages);
                    }
                  }
                } catch (parseError) {
                  console.warn('Failed to parse KB sources data:', parseError);
                }
                break;

              case 'rationale':
              case 'reasoning':
              case 'thinking':
                // Update existing message in place to prevent flashing
                let currentMessage = this.currentAgentResponses.get(agent);
                if (!currentMessage) {
                  // Create new message for this agent
                  const newMessageId = `${agentName}-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
                  currentMessage = {
                    id: newMessageId,
                    text: '',
                    sender: 'agent',
                    timestamp: new Date(),
                    type: 'text',
                    agentName: agentName,
                    displayName: agentName,
                    data: {
                      isThinking: true,
                      finalResponse: '',
                      thinkingHistory: [] as string[],
                      lastTrace: ''
                    }
                  };

                  this.addMessageIfNotDuplicate(currentMessage, agentInMessage)
                  this.updateAgentParticipant(agent);
                }

                // Find the current message in the messages array and update it directly
                const messageIndex = this.messages.findIndex(m => m.id === currentMessage!.id);
                if (messageIndex !== -1) {
                  const existingMessage = this.messages[messageIndex];
                  const currentHistory = existingMessage.data?.thinkingHistory || [];

                  // Only add if this is new content
                  if (!currentHistory.includes(messageText)) {
                    const updatedHistory = [...currentHistory, messageText];

                    // Update the message object directly to prevent recreation
                    existingMessage.data = {
                      ...existingMessage.data,
                      isThinking: true,
                      thinkingHistory: updatedHistory
                    };
                    existingMessage.text = `${updatedHistory.join('\n')}\n\n_Thinking..._`;

                    // Update the reference in currentAgentResponses
                    this.currentAgentResponses.set(agent, existingMessage);

                    this.shouldScrollToBottom = true;

                    // Use markForCheck instead of detectChanges for OnPush optimization
                    this.changeDetectorRef.markForCheck();
                  }
                }
                break;

              default:
                // Handle any visualization type generically
                if (messageType && messageType.endsWith('-visualization')) {
                  try {
                    const visualizationData = JSON.parse(messageText);
                    const visualizationType = visualizationData.visualizationType || visualizationData.templateId.replace('-visualization', '');

                    if (visualizationType) {
                      // Generate appropriate message text based on visualization type
                      let messageText = '';
                      let dataProperty = '';

                      switch (visualizationType) {
                        case 'creative':
                          messageText = `Generated ${visualizationData.creatives?.length || 0} creative assets for your plan.`;
                          dataProperty = 'creativeData';
                          break;
                        case 'timeline':
                          messageText = `Here's the timeline for ${visualizationData.title || 'your plan'}`;
                          dataProperty = 'timelineData';
                          break;
                        case 'decision-tree':
                          messageText = `Here's the decision analysis for ${visualizationData.title || 'your scenario'}`;
                          dataProperty = 'decisionTreeData';
                          break;
                        case 'metrics':
                          messageText = `Here's the performance analysis for ${visualizationData.title || 'your campaign'}`;
                          dataProperty = 'metricsData';
                          break;
                        case 'allocations':
                          messageText = `Here's the allocation analysis for ${visualizationData.title || 'your campaign'}`;
                          dataProperty = 'allocationsData';
                          break;
                        case 'channels':
                          messageText = `Here's the channel analysis for ${visualizationData.title || 'your campaign'}`;
                          dataProperty = 'channelsData';
                          break;
                        case 'segments':
                          messageText = `Here's the segment analysis for ${visualizationData.title || 'your campaign'}`;
                          dataProperty = 'segmentsData';
                          break;
                        default:
                          messageText = `Here's the ${visualizationType} analysis for ${visualizationData.title || 'your request'}`;
                          dataProperty = `${visualizationType}Data`;
                      }

                      const visualizationMessage: Message = {
                        id: `${visualizationType}-${agentName}-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
                        text: messageText,
                        sender: 'agent',
                        timestamp: new Date(),
                        type: 'text',
                        agentName: agentName,
                        displayName: displayName,
                        data: {
                          isThinking: false,
                          finalResponse: messageText,
                          messageType: 'visualization-data',
                          [dataProperty]: visualizationData
                        }
                      };

                      this.messages = [...this.messages, visualizationMessage];
                      console.log(this.messages)
                      this.updateAgentParticipant(agentInMessage);
                      this.shouldScrollToBottom = true;

                      // Track visualization for popover display
                      // Use the same displayName that was used for the participant
                      const normalizedDisplayName = displayName || TextUtils.pascalOrCamelToDisplayName(agentName || 'unknown');
                      this.agentVisualizationService.updateAgentVisualization(
                        agentName || 'unknown',
                        normalizedDisplayName,
                        { [dataProperty]: visualizationData }
                      );

                      // Trigger change detection
                      if (!this.changeDetectionPending) {
                        this.changeDetectionPending = true;
                        this.safeSetTimeout(() => {
                          this.changeDetectorRef.detectChanges();
                          this.changeDetectionPending = false;
                        }, 50);
                      }
                    }
                  } catch (parseError) {
                    console.warn(`Failed to parse ${messageType} data:`, parseError);
                  }
                  break;
                }

                // Handle other non-visualization message types
                break;
            }

          }
          // Handle chunk events (direct content from bedrock service)
          if (event.type === 'chunk') {
            console.log('🔍 Processing chunk event:', event);

            // Use agent info from event if available, otherwise default to BidSimulatorAgent
            const agentName = event.agentName ? event.agentName : agent.name;
            const teamName = event.teamName ? event.teamName : agent.teamName as string;

            if (!event.data) console.log('got unrecognized event', event)
            const messageText = event.data.toString();
            const messageType = event.messageType ? event.messageType : (event.type ? event.type : 'final-response');

            //console.log('🔍 Chunk messageType:', messageType, 'agentName:', agentName);

            if (messageType === 'collaborator-response') {
              // Check if this is actually an AgentCore agent before treating as such
              const resolvedAgent = this.agentConfig.getAgentByAgentNameAndTeam(agentName, teamName);
              // Get metadata from the event if available
              const metadata = (event as any).metadata || {};
              const contentType = metadata.type || 'response';
              let processedMessageText = messageText; // Initialize processed text

              console.log('✅ Processing AgentCore streaming chunk:', contentType, messageText.substring(0, 100) + '...');

              // ENHANCED: Check for embedded visualization data in the message text
              //const detectedVisualizations = this.detectEmbeddedVisualizations(messageText);

              // if (detectedVisualizations.length > 0) {
              //   // Process each detected visualization as a separate message
              //   detectedVisualizations.forEach((vizData, index) => {
              //     const vizMessageId = `${agentName}-viz-${Date.now()}-${index}-${Math.random().toString(36).substr(2, 9)}`;
              //     const vizDisplayName = this.getAgentDisplayName(resolvedAgent || agent);

              //     // Generate appropriate message text based on visualization type
              //     let vizMessageText = this.generateVisualizationMessageText(vizData);
              //     let dataProperty = `${vizData.visualizationType}Data`;

              //     const visualizationMessage: Message = {
              //       id: vizMessageId,
              //       text: vizMessageText,
              //       sender: 'agent',
              //       timestamp: new Date(),
              //       type: 'text',
              //       agentName: agentName,
              //       displayName: vizDisplayName,
              //       data: {
              //         isThinking: false,
              //         finalResponse: vizMessageText,
              //         messageType: `${vizData.visualizationType}-visualization`,
              //         [dataProperty]: vizData
              //       }
              //     };

              //     this.messages = [...this.messages, visualizationMessage];
              //     this.updateAgentParticipant(resolvedAgent || agent);

              //     // Track visualization for popover display
              //     this.agentVisualizationService.updateAgentVisualization(
              //       agentName,
              //       vizDisplayName,
              //       { [dataProperty]: vizData }
              //     );
              //   });

              //   // Remove visualization JSON from the original message text
              //   processedMessageText = this.removeVisualizationJsonFromText(messageText, detectedVisualizations);

              //   // If the message is now empty or only whitespace, skip creating a text message
              //   if (!processedMessageText.trim()) {
              //     this.shouldScrollToBottom = true;
              //     this.changeDetectorRef.markForCheck();
              //     return;
              //   }
              // }

              // Create appropriate message based on content type
              const newMessageId = `${agentName}-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
              let messageDisplayName = this.getAgentDisplayName(resolvedAgent || agent);
              let messageClass = '';

              // Handle different content types (preserved from AgentCore transformation)
              if (contentType === 'reasoning' || message.data?.contentType === 'rationale' || message.data?.contentType === 'thinking') {
                messageClass = 'reasoning-content';
                processedMessageText = `${processedMessageText}`;
              } else if (contentType === 'tool-agent' || contentType==='collaborator-response') {
                // Use the tool agent's display name
                messageDisplayName = agentName; // agentName is already formatted by formatToolAgentName
                messageClass = 'tool-agent-response';
              } else if (contentType === 'tool-result') {
                // Check if this tool result contains AdCP data that should be visualized
                const adcpVisualization = this.detectAdcpToolResultVisualization(processedMessageText);
                if (adcpVisualization) {
                  // Create a visualization message for the AdCP tool result
                  const vizMessageId = `${agentName}-adcp-viz-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
                  const vizDisplayName = this.getAgentDisplayName(resolvedAgent || agent);
                  
                  const visualizationMessage: Message = {
                    id: vizMessageId,
                    text: adcpVisualization.messageText,
                    sender: 'agent',
                    timestamp: new Date(),
                    type: 'text',
                    agentName: agentName,
                    displayName: vizDisplayName,
                    data: {
                      isThinking: false,
                      finalResponse: adcpVisualization.messageText,
                      messageType: `${adcpVisualization.visualizationType}-visualization`,
                      [adcpVisualization.dataProperty]: adcpVisualization.data
                    }
                  };
                  
                  this.messages = [...this.messages, visualizationMessage];
                  this.updateAgentParticipant(resolvedAgent || agent);
                  
                  // Track visualization for popover display
                  this.agentVisualizationService.updateAgentVisualization(
                    agentName,
                    vizDisplayName,
                    { [adcpVisualization.dataProperty]: adcpVisualization.data } as any
                  );
                  
                  this.shouldScrollToBottom = true;
                  this.changeDetectorRef.markForCheck();
                  return; // Don't create the hidden tool-result message
                }
                
                messageClass = 'tool-result hidden';
                processedMessageText = `${processedMessageText}`;
              }

              const newMessage: Message = {
                id: newMessageId,
                text: processedMessageText,
                sender: 'agent',
                timestamp: new Date(),
                type: 'text',
                agentName: agentName,
                displayName: messageDisplayName,
                data: {
                  isThinking: contentType === 'reasoning',
                  finalResponse: processedMessageText,
                  thinkingHistory: [] as string[],
                  lastTrace: '',
                  messageType: 'streaming-chunk',
                  contentType: contentType,
                  messageClass: messageClass,
                  metadata: metadata
                }
              };

              this.addMessageIfNotDuplicate(newMessage, resolvedAgent || agent);


            } else {
              // Check for embedded visualization data in chunk events (e.g., <visualization-data type="adcp_get_products-visualization">)
              const embeddedViz = this.detectEmbeddedAdcpVisualization(messageText);
              console.log('🔍 Checking for embedded AdCP visualization:', embeddedViz ? 'FOUND' : 'not found', messageType);
              if (embeddedViz) {
                console.log('✅ Creating AdCP visualization message:', embeddedViz.visualizationType);
                const resolvedAgent = this.agentConfig.getAgentByAgentNameAndTeam(agentName, teamName);
                const vizMessageId = `${agentName}-adcp-viz-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
                const vizDisplayName = this.getAgentDisplayName(resolvedAgent || agent);
                
                // Create visualization message
                const visualizationMessage: Message = {
                  id: vizMessageId,
                  text: embeddedViz.messageText,
                  sender: 'agent',
                  timestamp: new Date(),
                  type: 'text',
                  agentName: agentName,
                  displayName: vizDisplayName,
                  data: {
                    isThinking: false,
                    finalResponse: embeddedViz.messageText,
                    messageType: `${embeddedViz.visualizationType}-visualization`,
                    [embeddedViz.dataProperty]: embeddedViz.data
                  }
                };
                
                this.messages = [...this.messages, visualizationMessage];
                this.updateAgentParticipant(resolvedAgent || agent);
                
                // Track visualization for popover display
                this.agentVisualizationService.updateAgentVisualization(
                  agentName,
                  vizDisplayName,
                  { [embeddedViz.dataProperty]: embeddedViz.data } as any
                );
                
                // If there's remaining text after the visualization, create a text message for it
                if (embeddedViz.remainingText && embeddedViz.remainingText.trim()) {
                  const textMessageId = `${agentName}-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
                  const textMessage: Message = {
                    id: textMessageId,
                    text: embeddedViz.remainingText.trim(),
                    sender: 'agent',
                    timestamp: new Date(),
                    type: 'text',
                    agentName: agentName,
                    displayName: vizDisplayName,
                    data: {
                      isThinking: false,
                      finalResponse: embeddedViz.remainingText.trim(),
                      thinkingHistory: [] as string[],
                      lastTrace: '',
                      messageType: messageType
                    }
                  };
                  this.addMessageIfNotDuplicate(textMessage, resolvedAgent || agent);
                }
                
                this.shouldScrollToBottom = true;
                this.changeDetectorRef.markForCheck();
              } else if (messageType === 'final-response' || this.agentConfig.getAgent(agentName)?.deploymentType != "bedrock") {
                console.log('✅ Processing chunk event', event);
                // Handle regular streaming chunks - create new message for each chunk
                const resolvedAgent = this.agentConfig.getAgentByAgentNameAndTeam(agentName, teamName);
                const newMessageId = `${agentName}-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
                const newMessage: Message = {
                  id: newMessageId,
                  text: messageText,
                  sender: 'agent',
                  timestamp: new Date(),
                  type: 'text',
                  agentName: agentName,
                  displayName: agentName,
                  data: {
                    isThinking: false,
                    finalResponse: messageText,
                    thinkingHistory: [] as string[],
                    lastTrace: '',
                    messageType: messageType
                  }
                };
                this.addMessageIfNotDuplicate(newMessage, resolvedAgent || agent);
              }
            }
          }

          // Handle error events from the streaming service
          if (event.type === 'error') {
            console.error('Streaming error event:', event);

            // Check if this is an expired token error
            const metadata = (event as any).metadata || {};
            if (metadata.errorType === 'ExpiredTokenException' || metadata.requiresRefresh) {
              // Show a user-friendly message and prompt to refresh
              this.showTokenExpiredDialog(event.data);
              return;
            }

            // Handle other error types normally
            const errorMessage = event.data || 'An error occurred during streaming';
            console.warn('Streaming error:', errorMessage);
          }
        },
        error: (error) => {
          console.error('Error in streaming agent response:', error);

          // Check if this is a recoverable error that shouldn't stop the stream
          const errorMessage = error?.message || error?.toString() || '';
          const isRecoverableError = errorMessage.includes('InternalServerException') ||
            errorMessage.includes('ThrottlingException') ||
            errorMessage.includes('ServiceUnavailableException') ||
            errorMessage.includes('RequestTimeoutException');

          if (isRecoverableError) {
            console.warn(`⚠️ Recoverable streaming error, not terminating stream: ${errorMessage}`);
            // Don't terminate the stream or show error message for recoverable errors
            // The stream may continue with subsequent chunks
            return;
          }

          // Only handle fatal errors that should stop the stream
          console.error('💥 Fatal streaming error, terminating stream:', error);

          // Update all agent messages with error
          this.currentAgentResponses.forEach((agentMessage, agentKey) => {
            const messageIndex = this.messages.findIndex(m => m.id === agentMessage.id);
            if (messageIndex !== -1) {
              const currentMessage = this.messages[messageIndex];
              const errorMessage = {
                ...currentMessage,
                text: `I apologize, but I'm having trouble connecting to the backend service. Please ensure you are signed in and try again. If the problem persists, the agents may not be deployed yet.\n\nError: ${error instanceof Error ? error.message : 'Unknown error'}`,
                agentName: currentMessage.agentName || '',
                data: {
                  ...currentMessage.data,
                  isThinking: false
                }
              };

              // Update the messages array immutably
              this.messages = [
                ...this.messages.slice(0, messageIndex),
                errorMessage,
                ...this.messages.slice(messageIndex + 1)
              ];
              this.shouldScrollToBottom = true; // Trigger auto-scroll
            }
          });
          this.isLoading = false;
        },
        complete: () => {
          console.log('🏁 [Stream] Complete - Total messages:', this.messages.length, 'Pending invocations:', this.pendingSpecialistInvocations.size);
          console.log('Messages: ',this.messages)
          console.log('🏁 [Stream] Pending invocation IDs:', Array.from(this.pendingSpecialistInvocations.keys()));
          this.isLoading = false;

          // Trigger final change detection
          this.changeDetectorRef.detectChanges();
        }
      });

      // Track the subscription for cleanup
      this.activeSubscriptions.add(streamingSubscription);

    } catch (error) {
      console.error('❌ Error starting agent response stream:', error);

      // Update all agent messages with error
      this.currentAgentResponses.forEach((agentMessage, agentKey) => {
        const messageIndex = this.messages.findIndex(m => m.id === agentMessage.id);
        if (messageIndex !== -1) {
          const currentMessage = this.messages[messageIndex];
          const errorMessage = {
            ...currentMessage,
            text: `I apologize, but I'm having trouble connecting to the backend service. Please ensure you are signed in and try again. If the problem persists, the agents may not be deployed yet.\n\nError: ${error instanceof Error ? error.message : 'Unknown error'}`,
            agentName: currentMessage.agentName || '',
            data: {
              ...currentMessage.data,
              isThinking: false
            }
          };

          // Update the messages array immutably
          this.messages = [
            ...this.messages.slice(0, messageIndex),
            errorMessage,
            ...this.messages.slice(messageIndex + 1)
          ];
          this.shouldScrollToBottom = true;
        }
      });

      // Ensure loading state is reset
      console.log(this.messages)
      this.isLoading = false;
      this.currentAgentResponses.clear();
      this.changeDetectorRef.detectChanges();
    }
  }
  removeLastFinalResponseText(agentName: string, messageText: string): string {
    // Find the most recent message from the same agent where the new message starts with the old message text
    let oldMessage = this.messages
      .filter(m => m.agentName === agentName && messageText.trim().startsWith(m.text.trim()))
      .sort((a: any, b: any) => b.timestamp.getTime() - a.timestamp.getTime())[0]; // Sort by timestamp descending and get the first (most recent)

    if (oldMessage) {
      // Return only the new content (delta)
      return messageText.substring(oldMessage.text.length).trim();
    }

    // If no matching previous message found, return the full message
    return messageText;
  }
  addMessageIfNotDuplicate(newMessage: Message, agent: EnrichedAgent) {
    var messageTextOfOlder = this.messages.find(m => newMessage.text.indexOf(m.text) >= -1 && m.text.length > 0 && (m.agent?.name == agent.name && m.agent?.teamName == m.agent?.teamName));
    if (messageTextOfOlder) {
      console.log('found existing message as the starting portion of this text', messageTextOfOlder)
      newMessage.text = newMessage.text.replace(messageTextOfOlder.text, '');
    }
    if (this.messages.findIndex(m => m.text == newMessage.text) == -1) {
      this.messages = [...this.messages, newMessage];
      this.updateAgentParticipant(agent);
      //this.shouldScrollToBottom = true;

      // Use markForCheck for smooth streaming without flashing

      // Trigger change detection
      if (!this.changeDetectionPending) {
        this.changeDetectionPending = true;
        this.safeSetTimeout(() => {
          this.changeDetectorRef.detectChanges();
          this.changeDetectionPending = false;
        }, 50);
      }
      this.messagesUpdated.emit(this.messages);
      // Update bedrock service with current messages for AgentCore sources tracking
      this.bedrockService.updateChatMessages(this.messages);
    }
  }

  public getKeys(obj: any): string[] {
    return Object.keys(obj);
  }

  // Helper method to determine if a trace is meaningful enough to preserve
  private isMeaningfulTrace(trace: string): boolean {
    // Check if this trace is a duplicate of recent traces
    if (this.isDuplicateMessage(trace)) {
      return false;
    }

    // Be very permissive - preserve almost all traces except truly generic ones
    const genericPatterns = [
      /^🔄 Processing\.\.\.$/,
      /^🤔 Working on your request\.\.\.$/,
      /^_Thinking\.\.\._$/,
      /^🔄 Processing step in progress\.\.\.$/,
      /^Processing\.\.\.$/, // Simple processing messages
      /^Working\.\.\.$/, // Simple working messages
      /Processing \(\d+ → \d+ tokens\)/, // Filter out token processing messages
      /smart_toy.*Processing \(\d+.*tokens\)/, // Filter out token processing with icon
    ];

    // Only exclude these specific generic messages
    if (genericPatterns.some(pattern => pattern.test(trace.trim()))) {
      return false;
    }

    // Include everything else - prioritize traces that show actual progress
    const meaningfulPatterns = [
      /agent/i, // Agent mentions
      /analyzing/i, // Analysis steps
      /processing/i, // Processing with context
      /optimiz/i, // Optimization steps
      /recommend/i, // Recommendations
      /evaluat/i, // Evaluation steps
      /strategy/i, // Strategy mentions
      /campaign/i, // Campaign context
      /bid/i, // Bidding context
      /performance/i, // Performance analysis
      /🧠|💭|🔍|📊|⚡|🎯|💡/  // Thinking/analysis emojis
    ];

    // Prioritize traces with meaningful context
    if (meaningfulPatterns.some(pattern => pattern.test(trace))) {
      return true;
    }

    // Include any trace with substantial content (more than just a few words)
    if (trace.length > 20 && !trace.match(/^\s*(processing|working|thinking)\s*\.{0,3}\s*$/i)) {
      return true;
    }

    return false;
  }

  onKeyPress(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      // Hide typeahead if visible
      if (this.showTypeahead) {
        this.hideTypeahead();
      } else {
        this.sendMessage(this.selectedAgentForMessage); // Don't pass a default agent - let the modal appear
      }
    }
  }

  onMessageInput(event: Event): void {
    const target = event.target as HTMLTextAreaElement;
    this.currentMessage = target.value;

    // Check if user is typing an agent mention
    this.checkForAgentMention();
  }

  onMessageKeyDown(event: KeyboardEvent): void {
    // Handle typeahead navigation directly
    if (this.showTypeahead) {
      //
      // First, let's try a direct approach - wait for the next tick and try again
      if (!this.agentTypeahead) {
        this.safeSetTimeout(() => {
          if (this.agentTypeahead) {
            this.handleTypeaheadKeyboard(event);
          } else {
            console.warn('❌ ViewChild still not available, using DOM fallback');
            this.handleTypeaheadKeyboardFallback(event);
          }
        }, 0);

        // Prevent default to stop the event from bubbling
        event.preventDefault();
        return;
      }

      // ViewChild is available, handle directly
      this.handleTypeaheadKeyboard(event);
      return;
    }

    // Handle regular input
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      if (this.showTypeahead) {
        this.hideTypeahead();
      } else {
        this.sendMessage(this.selectedAgentForMessage);
      }
    }
  }

  private handleTypeaheadKeyboard(event: KeyboardEvent): void {
    if (!this.agentTypeahead) return;

    switch (event.key) {
      case 'ArrowDown':
        event.preventDefault();
        this.agentTypeahead.navigateDown();
        break;
      case 'ArrowUp':
        event.preventDefault();
        this.agentTypeahead.navigateUp();
        break;
      case 'Enter':
      case 'Tab':
        event.preventDefault();
        this.agentTypeahead.selectCurrent();
        break;
      case 'Escape':
        event.preventDefault();
        this.agentTypeahead.close();
        break;
    }
  }

  private handleTypeaheadKeyboardFallback(event: KeyboardEvent): void {
    console.warn('🎯 Using DOM fallback for typeahead keyboard navigation');

    // Try to find the typeahead component instance using Angular's debugging utilities
    const typeaheadElement = document.querySelector('app-agent-mention-typeahead');
    if (typeaheadElement) {

      // If we found an element, get the Angular component instance
      if (typeaheadElement) {

        const componentInstance = (typeaheadElement as any)?.componentInstance || (typeaheadElement as any)?.__ngContext__?.[8];
        if (componentInstance?.onKeyDown && typeof componentInstance.onKeyDown === 'function') {
          componentInstance.onKeyDown(event);
          return;
        }
      }
    } else {
      console.warn('❌ Could not find typeahead element in DOM');
    }
  }

  private checkForAgentMention(): void {
    const inputElement = this.messageInput?.nativeElement;
    if (!inputElement) return;

    const cursorPosition = inputElement.selectionStart || 0;
    const textBeforeCursor = this.currentMessage.substring(0, cursorPosition);

    // Check if we're in the middle of typing an agent mention
    // Match @ followed by any characters (including partial words)
    const mentionMatch = textBeforeCursor.match(/@([^@\s]*)$/);

    if (mentionMatch) {
      this.currentMentionText = mentionMatch[1];
      this.showTypeaheadAtCursor();
    } else {
      this.hideTypeahead();
    }
  }

  private showTypeaheadAtCursor(): void {
    const inputElement = this.messageInput?.nativeElement;
    if (!inputElement) {
      console.warn('❌ No input element found for typeahead positioning');
      return;
    }

    const rect = inputElement.getBoundingClientRect();
    const estimatedTypeaheadHeight = 200;
    const margin = 8;

    // Check if the input element is positioned fixed/sticky
    const computedStyle = window.getComputedStyle(inputElement);
    const isFixedOrSticky = computedStyle.position === 'fixed' || computedStyle.position === 'sticky';

    // For fixed/sticky elements, use viewport-relative positioning (no scroll offset)
    // For normal elements, use document-relative positioning (with scroll offset)
    const scrollOffsetY = isFixedOrSticky ? 0 : window.scrollY;
    const scrollOffsetX = isFixedOrSticky ? 0 : window.scrollX;

    // For chat input at bottom of screen, ALWAYS position above
    // Calculate position above input
    this.typeaheadPosition = {
      top: rect.top + scrollOffsetY - estimatedTypeaheadHeight - margin,
      left: rect.left + scrollOffsetX
    };

    this.showTypeahead = true;

    // Debug logging for typeahead component reference
    //
  }

  hideTypeahead(): void {
    this.showTypeahead = false;
    this.currentMentionText = '';
  }

  onAgentSelected(agent: AgentSuggestion): void {
    // Replace the @mention with the selected agent
    const inputElement = this.messageInput?.nativeElement;
    if (!inputElement) return;

    const cursorPosition = inputElement.selectionStart || 0;
    const textBeforeCursor = this.currentMessage.substring(0, cursorPosition);
    const textAfterCursor = this.currentMessage.substring(cursorPosition);

    // Find the start of the mention
    const mentionMatch = textBeforeCursor.match(/@(\w*)$/);
    if (mentionMatch) {
      const mentionStart = cursorPosition - mentionMatch[0].length;
      const beforeMention = this.currentMessage.substring(0, mentionStart);
      const agentMention = `@[${agent.agent.name}] `;
      this.currentMessage = beforeMention + agentMention + textAfterCursor;

      // Set cursor position after the mention
      this.safeSetTimeout(() => {
        const newCursorPosition = mentionStart + agentMention.length;
        inputElement.setSelectionRange(newCursorPosition, newCursorPosition);
        inputElement.focus();
      }, 0);
    }

    this.hideTypeahead();
  }

  formatMessage(text: string, agentName: string = ''): string {
    if (!text) return '';

    let formatted = text
      // First, strip any markdown code block wrappers that might interfere with rendering
      .replace(/^```markdown\s*/gm, '')
      .replace(/^```\s*$/gm, '')

      // Handle code blocks (preserve legitimate ones but not markdown wrappers)
      .replace(/```(\w+)?\s*([\s\S]*?)```/g, '<pre><code>$2</code></pre>')
      .replace(/`([^`]+)`/g, '<code>$1</code>')

      // Convert newlines to breaks
      .replace(/\n/g, '<br>')

      // Headers (# ## ### etc.)
      .replace(/^### (.*$)/gim, '<h3>$1</h3>')
      .replace(/^## (.*$)/gim, '<h2>$1</h2>')
      .replace(/^# (.*$)/gim, '<h1>$1</h1>')

      // Bold and italic
      .replace(/\*\*\*([^*]+)\*\*\*/g, '<strong><em>$1</em></strong>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/\*([^*]+)\*/g, '<em>$1</em>')

      // Tables - improved handling
      .replace(/\|(.+)\|/g, (match, content) => {
        const cells = content.split('|').map(cell => cell.trim()).filter(cell => cell);

        // Skip separator rows
        if (cells.every(cell => cell.includes('---'))) {
          return '';
        }

        // Determine if it's a header row (first row with meaningful content)
        const isHeader = cells.some(cell => cell.match(/^[A-Z][a-zA-Z\s]+$/) && !cell.includes('%') && !cell.includes('$'));

        const tag = isHeader ? 'th' : 'td';
        const cellsHtml = cells.map(cell => `<${tag}>${cell}</${tag}>`).join('');
        return `<tr>${cellsHtml}</tr>`;
      })
      .replace(/(<tr>.*?<\/tr>)/g, '<table class="markdown-table">$1</table>')
      .replace(/<\/table><br><table class="markdown-table">/g, '') // Merge adjacent tables

      // Lists - improved handling
      .replace(/^[\s]*[-•▲▼→]\s+(.+)$/gm, '<li class="bullet-item">$1</li>')
      .replace(/^[\s]*\d+\.\s+(.+)$/gm, '<li class="numbered-item">$1</li>')

      // Convert list items to proper lists
      .replace(/(<li class="bullet-item">.*?<\/li>)(\s*<br>\s*<li class="bullet-item">.*?<\/li>)*/g, (match) => {
        const items = match.replace(/<br>\s*/g, '').replace(/class="bullet-item"/g, '').trim();
        return `<ul class="markdown-list">${items}</ul>`;
      })
      .replace(/(<li class="numbered-item">.*?<\/li>)(\s*<br>\s*<li class="numbered-item">.*?<\/li>)*/g, (match) => {
        const items = match.replace(/<br>\s*/g, '').replace(/class="numbered-item"/g, '').trim();
        return `<ol class="markdown-list">${items}</ol>`;
      })

      // Clean up extra breaks around block elements
      .replace(/<br>\s*<\/?(h[1-6]|table|ul|ol)>/g, '</$1>')
      .replace(/<\/(h[1-6]|table|ul|ol)>\s*<br>/g, '</$1>')
      .replace(/(<\/(?:h[1-6]|table|ul|ol)>)\s*(<br>\s*)*\s*(<(?:h[1-6]|table|ul|ol)[^>]*>)/g, '$1<br>$3')

      // Final cleanup
      .replace(/<br>\s*<br>/g, '<br>'); // Remove double breaks

    // Apply agent mention coloring
    //formatted = this.formatAgentMentions(formatted);
    return formatted;
  }

  // New method to parse and extract visual data components from agent responses
  // Now supports templateId detection for each visualization type to enable template-specific rendering
  parseVisualDataComponents(text: string): {
    cleanedText: string;
    metricData?: any;
    channelAllocations?: any;
    channelCards?: any;
    segmentCards?: any;
    creativeData?: any;
    timelineData?: any;
    decisionTreeData?: any;
    histogramData?: any;
    doubleHistogramData?: any;
    barChartData?: any;
    donutChartData?: any;
    // ADCP-specific visualizations
    adcpInventoryData?: any;      // get_products results
    adcpMediaBuyData?: any;       // create_media_buy results
    adcpDeliveryData?: any;       // get_media_buy_delivery results
    adcpSignalsData?: any;        // get_signals results
    // Presentation visualization
    //presentationData?: any;
    // Template IDs for each visualization type
    metricTemplateId?: string;
    allocationsTemplateId?: string;
    channelsTemplateId?: string;
    segmentsTemplateId?: string;
    creativeTemplateId?: string;
    timelineTemplateId?: string;
    decisionTreeTemplateId?: string;
    histogramTemplateId?: string;
    doubleHistogramTemplateId?: string;
    barChartTemplateId?: string;
    donutChartTemplateId?: string;
    adcpInventoryTemplateId?: string;
    adcpMediaBuyTemplateId?: string;
    adcpDeliveryTemplateId?: string;
    adcpSignalsTemplateId?: string;
  } {
    if (!text) return { cleanedText: '' };

    let cleanedText = text;
    let metricData: any = null;
    let channelAllocations: any = null;
    let channelCards: any = null;
    let segmentCards: any = null;
    let creativeData: any = null;
    let timelineData: any = null;
    let decisionTreeData: any = null;
    let histogramData: any = null;
    let doubleHistogramData: any = null;
    let barChartData: any = null;
    let donutChartData: any = null;
    // ADCP-specific visualizations
    let adcpInventoryData: any = null;
    // Presentation visualization
    //let presentationData: any = null;

    // Template IDs for each visualization type
    let metricTemplateId: string | undefined = undefined;
    let allocationsTemplateId: string | undefined = undefined;
    let channelsTemplateId: string | undefined = undefined;
    let segmentsTemplateId: string | undefined = undefined;
    let creativeTemplateId: string | undefined = undefined;
    let timelineTemplateId: string | undefined = undefined;
    let decisionTreeTemplateId: string | undefined = undefined;
    let histogramTemplateId: string | undefined = undefined;
    let doubleHistogramTemplateId: string | undefined = undefined;
    let barChartTemplateId: string | undefined = undefined;
    let donutChartTemplateId: string | undefined = undefined;
    let adcpInventoryTemplateId:string | undefined = undefined;
    //let presentationTemplateId: string | undefined = undefined;

    try {

      // NEW JSON-ONLY FORMAT: Look for JSON objects with visualizationType property
      // Handle both cases: with and without ```json markers

      this.parseStandaloneJsonObjects(cleanedText, (parsedJson, jsonText) => {
        if (parsedJson.templateId && !parsedJson.visualizationType)
          parsedJson.visualizationType = parsedJson.templateId.replace('-visualization', '')

        if (parsedJson && parsedJson.visualizationType) {
          //console.log('🔍 Processing visualization type:', parsedJson.visualizationType, 'Title:', parsedJson.title);

          switch (parsedJson.visualizationType) {
            case 'adcp_get_products':
              if (!adcpInventoryData) {
                adcpInventoryData = parsedJson.products;
                adcpInventoryTemplateId = parsedJson.visualizationType;
                cleanedText = cleanedText.replace(jsonText, '');
              }
              break;
            case 'metrics':
              if (!metricData) {
                metricData = parsedJson;
                metricTemplateId = parsedJson.templateId;
                cleanedText = cleanedText.replace(jsonText, '');
              }
              break;

            case 'allocations':
              if (!channelAllocations) {
                //console.log('✅ Found allocations visualization:', parsedJson.title || 'Untitled');
                channelAllocations = parsedJson;
                allocationsTemplateId = parsedJson.templateId;
                cleanedText = cleanedText.replace(jsonText, '');
              }
              break;

            case 'channels':
              if (!channelCards) {
                channelCards = parsedJson;
                channelsTemplateId = parsedJson.templateId;
                cleanedText = cleanedText.replace(jsonText, '');
              }
              break;

            case 'segments':
              if (!segmentCards) {
                segmentCards = parsedJson;
                segmentsTemplateId = parsedJson.templateId;
                cleanedText = cleanedText.replace(jsonText, '');
              }
              break;
            case "creatives":
            case 'creative':
              if (!creativeData) {
                creativeData = parsedJson;
                creativeTemplateId = parsedJson.templateId;
                cleanedText = cleanedText.replace(jsonText, '');
              }
              break;
            case 'timeline':
              if (!timelineData) {
                timelineData = parsedJson;
                timelineTemplateId = parsedJson.templateId;
                cleanedText = cleanedText.replace(jsonText, '');
              }
              break;

            case 'decision-tree':
            case 'decisiontree':
              if (!decisionTreeData) {
                decisionTreeData = parsedJson;
                decisionTreeTemplateId = parsedJson.templateId;
                cleanedText = cleanedText.replace(jsonText, '');
              }
              break;

            case 'weather-analysis':
            case 'weather':
              // Handle weather analysis as a special metrics visualization
              if (!metricData) {
                // Transform weather data to metrics format
                const weatherMetrics = this.transformWeatherDataToMetrics(parsedJson);
                if (weatherMetrics) {
                  metricData = weatherMetrics;
                  metricTemplateId = parsedJson.templateId || 'weather-metrics-visualization';
                  cleanedText = cleanedText.replace(jsonText, '');
                }
              }
              break;

            case 'histogram':
              if (!histogramData) {
                histogramData = parsedJson;
                histogramTemplateId = parsedJson.templateId;
                cleanedText = cleanedText.replace(jsonText, '');
              }
              break;

            case 'double-histogram':
              if (!doubleHistogramData) {
                doubleHistogramData = parsedJson;
                doubleHistogramTemplateId = parsedJson.templateId;
                cleanedText = cleanedText.replace(jsonText, '');
              }
              break;

            case 'bar-chart':
              if (!barChartData) {
                barChartData = parsedJson;
                barChartTemplateId = parsedJson.templateId;
                cleanedText = cleanedText.replace(jsonText, '');
              }
              break;

            case 'donut-chart':
              if (!donutChartData) {
                donutChartData = parsedJson;
                donutChartTemplateId = parsedJson.templateId;
                cleanedText = cleanedText.replace(jsonText, '');
              }
              break;

            // case 'presentation':
            //   if (!presentationData) {
            //     presentationData = parsedJson;
            //     presentationTemplateId = parsedJson.templateId;
            //     cleanedText = cleanedText.replace(jsonText, '');
            //   }
            //   break;

            // ADCP Protocol Visualizations - handle any type starting with 'adcp_'
            default:
              if (parsedJson.visualizationType?.startsWith('adcp_')) {
                const adcpType = parsedJson.visualizationType;
                if (adcpType.startsWith('adcp_get_products') && !adcpInventoryData) {
                  adcpInventoryData = parsedJson;
                  adcpInventoryTemplateId = parsedJson.templateId;
                  cleanedText = cleanedText.replace(jsonText, '');
                }
                // Future ADCP visualization types can be added here
              }
              break;
          }
        }
        // FALLBACK: Legacy format support (without visualizationType)
        else if (this.isWeatherAnalysisJson(parsedJson) && !metricData) {
          // Transform weather data to metrics format
          const weatherMetrics = this.transformWeatherDataToMetrics(parsedJson);
          if (weatherMetrics) {
            metricData = weatherMetrics;
            cleanedText = cleanedText.replace(jsonText, '');
          }
        } else if (this.isMetricDataJson(parsedJson) && !metricData) {
          metricData = parsedJson;
          cleanedText = cleanedText.replace(jsonText, '');
        } else if (this.isChannelAllocationsJson(parsedJson) && !channelAllocations) {
          console.log('✅ Found allocations via fallback detection:', parsedJson.title || 'Untitled');
          channelAllocations = parsedJson;
          cleanedText = cleanedText.replace(jsonText, '');
        } else if (this.isChannelCardsJson(parsedJson) && !channelCards) {
          channelCards = this.normalizeChannelCardsStructure(parsedJson);
          cleanedText = cleanedText.replace(jsonText, '');
        } else if (this.isSegmentCardsJson(parsedJson) && !segmentCards) {
          segmentCards = parsedJson;
          cleanedText = cleanedText.replace(jsonText, '');
        } else {
          // //
        }
      });

      // ADDITIONAL FALLBACK: Look for JSON inside ```json blocks (in case agents still use this format)
      if (!metricData || !channelAllocations || !channelCards || !segmentCards || !creativeData || !timelineData || !histogramData || !doubleHistogramData || !barChartData || !donutChartData || !adcpInventoryData) {

        // Enhanced regex to handle various code block formats
        const jsonBlockRegex = /```(?:json|JSON)?\s*([\s\S]*?)\s*```/g;
        const jsonMatches = [...cleanedText.matchAll(jsonBlockRegex)];

        // Debug: Log how many code blocks we found
        if (jsonMatches.length > 0) {
        }

        for (const match of jsonMatches) {
          const jsonContent = match[1];
          try {
            if (jsonContent && jsonContent.length > 0) {
              // Enhanced JSON cleaning for code blocks
              let processedContent = this.normalizeJsonForParsing(jsonContent);

              const unescapedContent = this.unescapeJsonContent(processedContent);
              // Clean JavaScript-style comments before parsing
              const cleanedContent = this.cleanJsonComments(unescapedContent);

              // Debug logging for problematic JSON
              if (cleanedContent.includes('"visualizationType": "timeline"')) {

              }

              // Use partial JSON parser for code blocks too
              const parseResult = this.parsePartialJson(cleanedContent);

              if (!parseResult.success || !parseResult.data) {
                //console.warn('❌ Failed to parse JSON in code block:', parseResult.error);
                // Additional debug info for timeline JSON
                if (cleanedContent.includes('"visualizationType": "timeline"')) {
                  console.warn('❌ Timeline JSON parsing failed. Content preview:', cleanedContent.substring(0, 500));
                }
                continue;
              }

              const parsedData = parseResult.data;
              if (parseResult.data.templateId && !parseResult.data.visualizationType)
                parseResult.data.visualizationType = parseResult.data.templateId.replace('-visualization', '')

              if (parsedData && parsedData.visualizationType) {
                switch (parsedData.visualizationType) {
                  case 'metrics':
                    if (!metricData) {
                      metricData = parsedData;
                      metricTemplateId = parsedData.templateId;
                      cleanedText = cleanedText.replace(match[0], '');
                    }
                    break;

                  case 'allocations':
                    if (!channelAllocations) {
                      channelAllocations = parsedData;
                      allocationsTemplateId = parsedData.templateId;
                      cleanedText = cleanedText.replace(match[0], '');
                    }
                    break;

                  case 'channels':
                    if (!channelCards) {
                      channelCards = parsedData;
                      channelsTemplateId = parsedData.templateId;
                      cleanedText = cleanedText.replace(match[0], '');
                    }
                    break;

                  case 'segments':
                    if (!segmentCards) {
                      segmentCards = parsedData;
                      segmentsTemplateId = parsedData.templateId;
                      cleanedText = cleanedText.replace(match[0], '');
                    }
                    break;

                  case 'creative':
                  case 'creatives':
                    if (!creativeData) {
                      creativeData = parsedData;
                      creativeTemplateId = parsedData.templateId;
                      cleanedText = cleanedText.replace(match[0], '');
                    }
                    break;

                  case 'timeline':
                    if (!timelineData) {
                      timelineData = parsedData;
                      timelineTemplateId = parsedData.templateId;
                      cleanedText = cleanedText.replace(match[0], '');
                    }
                    break;

                  case 'decision-tree':
                  case 'decisiontree':
                    if (!decisionTreeData) {
                      decisionTreeData = parsedData;
                      decisionTreeTemplateId = parsedData.templateId;
                      cleanedText = cleanedText.replace(match[0], '');
                    }
                    break;

                  case 'weather-analysis':
                  case 'weather':
                    // Handle weather analysis as a special metrics visualization
                    if (!metricData) {
                      // Transform weather data to metrics format
                      const weatherMetrics = this.transformWeatherDataToMetrics(parsedData);
                      if (weatherMetrics) {
                        metricData = weatherMetrics;
                        metricTemplateId = parsedData.templateId || 'weather-metrics-visualization';
                        cleanedText = cleanedText.replace(match[0], '');
                      }
                    }
                    break;

                  case 'histogram':
                    if (!histogramData) {
                      histogramData = parsedData;
                      histogramTemplateId = parsedData.templateId;
                      cleanedText = cleanedText.replace(match[0], '');
                    }
                    break;

                  case 'double-histogram':
                    if (!doubleHistogramData) {
                      doubleHistogramData = parsedData;
                      doubleHistogramTemplateId = parsedData.templateId;
                      cleanedText = cleanedText.replace(match[0], '');
                    }
                    break;

                  case 'bar-chart':
                    if (!barChartData) {
                      barChartData = parsedData;
                      barChartTemplateId = parsedData.templateId;
                      cleanedText = cleanedText.replace(match[0], '');
                    }
                    break;

                  case 'donut-chart':
                    if (!donutChartData) {
                      donutChartData = parsedData;
                      donutChartTemplateId = parsedData.templateId;
                      cleanedText = cleanedText.replace(match[0], '');
                    }
                    break;

                  case 'adcp_get_products':
                    if (!adcpInventoryData) {
                      adcpInventoryData = parsedData;
                      adcpInventoryTemplateId = parsedData.templateId;
                      cleanedText = cleanedText.replace(match[0], '');
                    }
                    break;
                }
              }
              // Legacy format fallback for code blocks
              else if (this.isWeatherAnalysisJson(parsedData) && !metricData) {
                // Transform weather data to metrics format
                const weatherMetrics = this.transformWeatherDataToMetrics(parsedData);
                if (weatherMetrics) {
                  metricData = weatherMetrics;
                  cleanedText = cleanedText.replace(match[0], '');
                }
              } else if (this.isMetricDataJson(parsedData) && !metricData) {
                metricData = parsedData;
                cleanedText = cleanedText.replace(match[0], '');
              } else if (this.isChannelAllocationsJson(parsedData) && !channelAllocations) {
                channelAllocations = parsedData;
                cleanedText = cleanedText.replace(match[0], '');
              } else if (this.isChannelCardsJson(parsedData) && !channelCards) {
                channelCards = this.normalizeChannelCardsStructure(parsedData);
                cleanedText = cleanedText.replace(match[0], '');
              } else if (this.isSegmentCardsJson(parsedData) && !segmentCards) {
                segmentCards = parsedData;
                cleanedText = cleanedText.replace(match[0], '');
              }
            }
          } catch (e) {
            //console.warn('❌ Failed to parse JSON in code block:', e, 'Content:', jsonContent);
          }
        }
      }

      // LEGACY XML TAG SUPPORT (for backward compatibility during transition)
      if (!metricData || !channelAllocations || !channelCards || !segmentCards || !creativeData || !timelineData) {

        // NEW STANDARDIZED FORMAT: Look for <visualization-data type="TYPE"> tags
        const visualizationRegex = /<visualization-data\s+type="([^"]+)"\s*>\s*```(?:json)?\s*([\s\S]*?)\s*```\s*<\/visualization-data>/g;

        const visualMatches = [...text.matchAll(visualizationRegex)];

        for (const match of visualMatches) {
          const type = match[1];
          const jsonContent = match[2];

          try {
            const unescapedContent = this.unescapeJsonContent(jsonContent);
            // Clean JavaScript-style comments before parsing
            const cleanedContent = this.cleanJsonComments(unescapedContent);

            // Use partial JSON parser for XML tags too
            const parseResult = this.parsePartialJson(cleanedContent);

            if (!parseResult.success || !parseResult.data) {
              console.warn(`❌ Failed to parse JSON in XML ${type} visualization:`, parseResult.error);
              continue;
            }

            const parsedData = parseResult.data;

            switch (type) {
              case 'metrics':
                if (!metricData) {
                  metricData = parsedData;
                  metricTemplateId = parsedData.templateId;
                  cleanedText = cleanedText.replace(match[0], '');
                }
                break;

              case 'allocations':
                if (!channelAllocations) {
                  channelAllocations = parsedData;
                  allocationsTemplateId = parsedData.templateId;
                  cleanedText = cleanedText.replace(match[0], '');
                }
                break;

              case 'channels':
                if (!channelCards) {
                  channelCards = parsedData;
                  channelsTemplateId = parsedData.templateId;
                  cleanedText = cleanedText.replace(match[0], '');
                }
                break;

              case 'segments':
                if (!segmentCards) {
                  segmentCards = parsedData;
                  segmentsTemplateId = parsedData.templateId;
                  cleanedText = cleanedText.replace(match[0], '');
                }
                break;

              case 'creatives':
              case 'creative':
              case "images":
                if (!creativeData) {
                  creativeData = parsedData;
                  creativeTemplateId = parsedData.templateId;
                  cleanedText = cleanedText.replace(match[0], '');
                }
                break;

              case 'timeline':
                if (!timelineData) {
                  timelineData = parsedData;
                  timelineTemplateId = parsedData.templateId;
                  cleanedText = cleanedText.replace(match[0], '');
                }
                break;

              case 'decision-tree':
              case 'decisiontree':
                if (!decisionTreeData) {
                  decisionTreeData = parsedData;
                  decisionTreeTemplateId = parsedData.templateId;
                  cleanedText = cleanedText.replace(match[0], '');
                }
                break;

              default:
                // Handle ADCP visualization types (adcp_get_products, adcp_*, etc.)
                if (type.startsWith('adcp_')) {
                  if (type.startsWith('adcp_get_products') && !adcpInventoryData) {
                    adcpInventoryData = parsedData;
                    adcpInventoryTemplateId = parsedData.templateId;
                    cleanedText = cleanedText.replace(match[0], '');
                  }
                  // Future ADCP visualization types can be added here
                } else {
                  console.warn('❓ Unknown legacy XML visualization type:', type);
                }
            }

          } catch (e) {
            console.warn(`❌ Failed to parse legacy XML ${type} visualization JSON:`, e, 'Content:', jsonContent.substring(0, 100));
          }
        }
      }

      // FINAL FALLBACK: Look for JSON patterns that might not be in proper code blocks
      // This handles cases where the markdown formatting might be inconsistent
      if (!metricData || !channelAllocations || !channelCards || !segmentCards || !creativeData || !timelineData || !histogramData || !doubleHistogramData || !barChartData || !donutChartData) {

        // Look for JSON-like patterns that start with { and contain visualizationType
        const jsonPatternRegex = /\{\s*[^}]*"visualizationType"\s*:\s*"([^"]+)"[^}]*[\s\S]*?\}/g;
        const patternMatches = [...cleanedText.matchAll(jsonPatternRegex)];

        for (const match of patternMatches) {
          const jsonText = match[0];
          const visualizationType = match[1];

          try {
            // Clean and parse the JSON
            const cleanedJsonText = this.cleanJsonComments(this.unescapeJsonContent(jsonText));
            const parseResult = this.parsePartialJson(cleanedJsonText);

            if (parseResult.success && parseResult.data && parseResult.data.visualizationType) {

              // Apply the same logic as the other parsing methods
              switch (parseResult.data.visualizationType) {
                case 'metrics':
                  if (!metricData) {
                    metricData = parseResult.data;
                    metricTemplateId = parseResult.data.templateId;
                    cleanedText = cleanedText.replace(jsonText, '');
                  }
                  break;
                case 'timeline':
                  if (!timelineData) {
                    timelineData = parseResult.data;
                    timelineTemplateId = parseResult.data.templateId;
                    cleanedText = cleanedText.replace(jsonText, '');
                  }
                  break;
                case 'allocations':
                  if (!channelAllocations) {
                    channelAllocations = parseResult.data;
                    allocationsTemplateId = parseResult.data.templateId;
                    cleanedText = cleanedText.replace(jsonText, '');
                  }
                  break;
                case 'channels':
                  if (!channelCards) {
                    channelCards = parseResult.data;
                    channelsTemplateId = parseResult.data.templateId;
                    cleanedText = cleanedText.replace(jsonText, '');
                  }
                  break;
                case 'segments':
                  if (!segmentCards) {
                    segmentCards = parseResult.data;
                    segmentsTemplateId = parseResult.data.templateId;
                    cleanedText = cleanedText.replace(jsonText, '');
                  }
                  break;
                case 'creative':
                case 'creatives':
                  if (!creativeData) {
                    creativeData = parseResult.data;
                    creativeTemplateId = parseResult.data.templateId;
                    cleanedText = cleanedText.replace(jsonText, '');
                  }
                  break;
                case 'histogram':
                  if (!histogramData) {
                    histogramData = parseResult.data;
                    histogramTemplateId = parseResult.data.templateId;
                    cleanedText = cleanedText.replace(jsonText, '');
                  }
                  break;
                case 'double-histogram':
                  if (!doubleHistogramData) {
                    doubleHistogramData = parseResult.data;
                    doubleHistogramTemplateId = parseResult.data.templateId;
                    cleanedText = cleanedText.replace(jsonText, '');
                  }
                  break;
                case 'bar-chart':
                  if (!barChartData) {
                    barChartData = parseResult.data;
                    barChartTemplateId = parseResult.data.templateId;
                    cleanedText = cleanedText.replace(jsonText, '');
                  }
                  break;
                case 'donut-chart':
                  if (!donutChartData) {
                    donutChartData = parseResult.data;
                    donutChartTemplateId = parseResult.data.templateId;
                    cleanedText = cleanedText.replace(jsonText, '');
                  }
                  break;
                case 'adcp_get_products':
                  if (!adcpInventoryData) {
                    adcpInventoryData = parseResult.data;
                    adcpInventoryTemplateId = parseResult.data.templateId;
                    cleanedText = cleanedText.replace(jsonText, '');
                  }
                  break;
                case 'weather-analysis':
                case 'weather':
                  // Handle weather analysis as a special metrics visualization
                  if (!metricData) {
                    // Transform weather data to metrics format
                    const weatherMetrics = this.transformWeatherDataToMetrics(parseResult.data);
                    if (weatherMetrics) {
                      metricData = weatherMetrics;
                      metricTemplateId = parseResult.data.templateId || 'weather-metrics-visualization';
                      cleanedText = cleanedText.replace(jsonText, '');
                    }
                  }
                  break;
              }
            }
          } catch (error) {
            console.warn(`❌ Failed to parse JSON pattern for ${visualizationType}:`, error);
          }
        }
      }

      // Clean up text and apply formatting
      cleanedText = cleanedText
        .replace(/\n\s*\n\s*\n/g, '\n\n') // Remove excessive line breaks
        .trim();

    } catch (error) {
      console.warn('Error parsing visual data components:', error);
      return { cleanedText: text };
    }

    const result = {
      cleanedText,
      metricData,
      channelAllocations,
      channelCards,
      segmentCards,
      creativeData,
      timelineData,
      decisionTreeData,
      histogramData,
      doubleHistogramData,
      barChartData,
      donutChartData,
      // ADCP-specific visualizations
      adcpInventoryData,
      // Presentation visualization
      //presentationData,
      // Template IDs for each visualization type
      metricTemplateId,
      allocationsTemplateId,
      channelsTemplateId,
      segmentsTemplateId,
      creativeTemplateId,
      timelineTemplateId,
      decisionTreeTemplateId,
      histogramTemplateId,
      doubleHistogramTemplateId,
      barChartTemplateId,
      donutChartTemplateId,
      //presentationTemplateId
    };

    // Debug templateId detection
    const templateIds = [
      metricTemplateId, allocationsTemplateId, channelsTemplateId, segmentsTemplateId,
      creativeTemplateId, timelineTemplateId, decisionTreeTemplateId, histogramTemplateId,
      doubleHistogramTemplateId, barChartTemplateId, donutChartTemplateId, adcpInventoryTemplateId
    ].filter(id => id !== undefined);

    if (templateIds.length > 0) {
    }

    // Debug only when needed

    return result;
  }

  // Method to format thinking vs final response differently
  formatAgentMessage(message: Message): {
    thinkingContent: string;
    finalContent: string;
    isThinking: boolean;
    delegationContent?: string | undefined;
    visualData?: any
  } {
    const isThinking = message.data?.isThinking || false;
    const thinkingHistory = message.data?.thinkingHistory || [];
    const finalResponse = message.data?.finalResponse || '';
    const delegationContent = message.text?.startsWith("@") ? message.text : undefined
    // Helper function to convert escaped newlines to actual newlines
    const unescapeNewlines = (text: string): string => {
      return text.replace(/\\n/g, '\n');
    };

    // If still thinking, show thinking history + current trace + thinking indicator
    if (isThinking) {
      const rawThinkingContent = thinkingHistory.length > 0
        ? unescapeNewlines(thinkingHistory.join('\n\n'))
        : '';
      //const thinkingContent = this.formatAgentMentions(rawThinkingContent);
      const thinkingContent = rawThinkingContent;

      const currentTrace = message.text.includes('_Thinking..._')
        ? message.text.replace('_Thinking..._', '\n\n**Thinking...**')
        : message.text;

      // If there's no content yet, provide a default thinking indicator
      const rawFinalContent = unescapeNewlines(currentTrace) || (thinkingHistory.length === 0 ? '**Thinking...**' : '');
      //const finalContent = this.formatAgentMentions(rawFinalContent);
      const finalContent = rawFinalContent;

      return {
        thinkingContent,
        finalContent,
        visualData: null,
        delegationContent: delegationContent,

        isThinking: true
      };
    }

    // If complete, show thinking as context and emphasize final response
    if (thinkingHistory.length > 0 || finalResponse) {
      const rawThinkingContent = `**💭 Thinking Process:**\n\n${unescapeNewlines(thinkingHistory.join('\n\n'))}`;
      const thinkingContent = this.formatAgentMentions(rawThinkingContent);

      // Parse visual data components from final response
      const parsedData = this.parseVisualDataComponents(finalResponse);

      // If we have visual data, refresh tour steps to include dynamic steps
      if (parsedData.metricData || parsedData.channelAllocations || parsedData.channelCards || parsedData.segmentCards || parsedData.creativeData || parsedData.timelineData || parsedData.decisionTreeData || parsedData.histogramData || parsedData.doubleHistogramData || parsedData.barChartData || parsedData.donutChartData || parsedData.adcpInventoryData) {
        this.safeSetTimeout(() => {
          this.tourService.refreshSteps();
        }, 500); // Wait for DOM to update
      }

      const visualDataResult = {
        metricData: parsedData.metricData,
        channelAllocations: parsedData.channelAllocations,
        channelCards: parsedData.channelCards,
        segmentCards: parsedData.segmentCards,
        creativeData: parsedData.creativeData,
        timelineData: parsedData.timelineData,
        decisionTreeData: parsedData.decisionTreeData,
        histogramData: parsedData.histogramData,
        doubleHistogramData: parsedData.doubleHistogramData,
        barChartData: parsedData.barChartData,
        donutChartData: parsedData.donutChartData,
        // ADCP-specific visualizations
        adcpInventoryData: parsedData.adcpInventoryData,
        // Presentation visualization
        //presentationData: parsedData.presentationData,
        // Template IDs for each visualization type
        metricTemplateId: parsedData.metricTemplateId,
        allocationsTemplateId: parsedData.allocationsTemplateId,
        channelsTemplateId: parsedData.channelsTemplateId,
        segmentsTemplateId: parsedData.segmentsTemplateId,
        creativeTemplateId: parsedData.creativeTemplateId,
        timelineTemplateId: parsedData.timelineTemplateId,
        decisionTreeTemplateId: parsedData.decisionTreeTemplateId,
        histogramTemplateId: parsedData.histogramTemplateId,
        doubleHistogramTemplateId: parsedData.doubleHistogramTemplateId,
        barChartTemplateId: parsedData.barChartTemplateId,
        donutChartTemplateId: parsedData.donutChartTemplateId,
        adcpInventoryTemplateId: parsedData.adcpInventoryTemplateId
        //presentationTemplateId: parsedData.presentationTemplateId
      };

      // Track visualizations for popover display (NEW - for popover feature)
      const displayName = message.displayName || 'Unknown Agent';
      // Use consistent name format for visualization storage - use the same displayName as the message
      const normalizedDisplayName = displayName || TextUtils.pascalOrCamelToDisplayName(message.agentName || 'unknown');

      this.agentVisualizationService.updateAgentVisualization(
        message.agentName || 'unknown',
        normalizedDisplayName,
        visualDataResult
      );

      return {
        thinkingContent,
        finalContent: unescapeNewlines(parsedData.cleanedText),
        isThinking: false,
        visualData: visualDataResult
      };
    }

    // Handle any visualization messages generically
    if (message.data?.messageType && message.data.messageType.endsWith('-visualization')) {
      const visualizationType = message.data.messageType.replace('-visualization', '');
      const dataProperty = `${visualizationType}Data`;

      // Check if the message has the corresponding data property
      if (message.data[dataProperty]) {
        const thinkingContent = thinkingHistory.length > 0 ?
          `**💭 Thinking Process:**\n\n${unescapeNewlines(thinkingHistory.join('\n\n'))}` : '';

        // Create visual data object with all properties undefined except the current one
        const visualData = {
          metricData: undefined,
          channelAllocations: undefined,
          channelCards: undefined,
          segmentCards: undefined,
          creativeData: undefined,
          timelineData: undefined,
          decisionTreeData: undefined,
          histogramData: undefined,
          doubleHistogramData: undefined,
          barChartData: undefined,
          donutChartData: undefined,
          // Template IDs for each visualization type
          metricTemplateId: undefined,
          allocationsTemplateId: undefined,
          channelsTemplateId: undefined,
          segmentsTemplateId: undefined,
          creativeTemplateId: undefined,
          timelineTemplateId: undefined,
          decisionTreeTemplateId: undefined,
          histogramTemplateId: undefined,
          doubleHistogramTemplateId: undefined,
          barChartTemplateId: undefined,
          donutChartTemplateId: undefined
        };

        // Set the specific data and template ID for this visualization type
        visualData[dataProperty] = message.data[dataProperty];
        const templateIdProperty = `${visualizationType}TemplateId`;
        if (visualData.hasOwnProperty(templateIdProperty)) {
          visualData[templateIdProperty] = message.data[dataProperty]?.templateId;
        }

        // Refresh tour steps for visualization
        this.safeSetTimeout(() => {
          this.tourService.refreshSteps();
        }, 500);

        // Track visualizations for popover display
        const displayName = message.displayName || 'Unknown Agent';
        const normalizedDisplayName = displayName || TextUtils.pascalOrCamelToDisplayName(message.agentName || 'unknown');
        this.agentVisualizationService.updateAgentVisualization(
          message.agentName || 'unknown',
          normalizedDisplayName,
          visualData
        );

        return {
          thinkingContent: this.formatAgentMentions(thinkingContent),
          finalContent: unescapeNewlines(message.data.finalResponse || finalResponse || message.text),
          isThinking: false,
          visualData: visualData
        };
      }
    }

    // Fallback for simple messages - also parse for visual data
    const parsedData = this.parseVisualDataComponents(message.text);

    // If we have visual data, refresh tour steps to include dynamic steps
    if (parsedData.metricData || parsedData.channelAllocations || parsedData.channelCards || parsedData.segmentCards || parsedData.creativeData || parsedData.timelineData || parsedData.decisionTreeData || parsedData.histogramData || parsedData.doubleHistogramData || parsedData.barChartData || parsedData.donutChartData) {
      this.safeSetTimeout(() => {
        this.tourService.refreshSteps();
      }, 500); // Wait for DOM to update
    }

    const fallbackVisualData = {
      metricData: parsedData.metricData,
      channelAllocations: parsedData.channelAllocations,
      channelCards: parsedData.channelCards,
      segmentCards: parsedData.segmentCards,
      creativeData: parsedData.creativeData,
      timelineData: parsedData.timelineData,
      decisionTreeData: parsedData.decisionTreeData,
      histogramData: parsedData.histogramData,
      doubleHistogramData: parsedData.doubleHistogramData,
      barChartData: parsedData.barChartData,
      donutChartData: parsedData.donutChartData,
      // Presentation visualization
      //presentationData: parsedData.presentationData,
      // Template IDs for each visualization type
      metricTemplateId: parsedData.metricTemplateId,
      allocationsTemplateId: parsedData.allocationsTemplateId,
      channelsTemplateId: parsedData.channelsTemplateId,
      segmentsTemplateId: parsedData.segmentsTemplateId,
      creativeTemplateId: parsedData.creativeTemplateId,
      timelineTemplateId: parsedData.timelineTemplateId,
      decisionTreeTemplateId: parsedData.decisionTreeTemplateId,
      histogramTemplateId: parsedData.histogramTemplateId,
      doubleHistogramTemplateId: parsedData.doubleHistogramTemplateId,
      barChartTemplateId: parsedData.barChartTemplateId,
      donutChartTemplateId: parsedData.donutChartTemplateId,
      adcpInventoryTemplateId: parsedData.adcpInventoryTemplateId
    };

    // Track visualizations for popover display (NEW - for popover feature)
    const displayName = message.displayName || 'Unknown Agent';
    // Use consistent name format for visualization storage - use the same displayName as the message
    const normalizedDisplayName = displayName || TextUtils.pascalOrCamelToDisplayName(message.agentName || 'unknown');
    this.agentVisualizationService.updateAgentVisualization(
      message.agentName || 'unknown',
      normalizedDisplayName,
      fallbackVisualData
    );

    return {
      thinkingContent: '',
      finalContent: unescapeNewlines(parsedData.cleanedText), // Agent mention formatting already applied in parseVisualDataComponents
      isThinking: false,
      visualData: fallbackVisualData
    };
  }

  // Cache formatted agent messages to avoid multiple calls
  getFormattedAgentMessage(message: Message): {
    thinkingContent: string;
    finalContent: string;
    isThinking: boolean;
    delegationContent?: string | undefined;
    visualData?: {
      metricData?: any;
      channelAllocations?: any;
      channelCards?: any;
      segmentCards?: any;
      creativeData?: any;
      timelineData?: any;
      decisionTreeData?: any;
      histogramData?: any;
      doubleHistogramData?: any;
      barChartData?: any;
      donutChartData?: any;
      presentationData?: any;
      // ADCP-specific visualizations
      adcpInventoryData?: any;      // get_products results
      adcpMediaBuyData?: any;       // create_media_buy results
      adcpDeliveryData?: any;       // get_media_buy_delivery results
      adcpSignalsData?: any;        // get_signals results
      // Template IDs for each visualization type
      metricTemplateId?: string;
      allocationsTemplateId?: string;
      channelsTemplateId?: string;
      segmentsTemplateId?: string;
      creativeTemplateId?: string;
      timelineTemplateId?: string;
      decisionTreeTemplateId?: string;
      histogramTemplateId?: string;
      doubleHistogramTemplateId?: string;
      barChartTemplateId?: string;
      donutChartTemplateId?: string;
      presentationTemplateId?: string;
      adcpInventoryTemplateId?: string;
      adcpMediaBuyTemplateId?: string;
      adcpDeliveryTemplateId?: string;
      adcpSignalsTemplateId?: string;
    };
  } {
    // Create a cache key based on message content and state
    const cacheKey = `${message.id}-${message.data?.isThinking}-${message.data?.thinkingHistory?.length || 0}-${message.data?.finalResponse?.length || 0}`;

    // Simple cache to avoid reformatting the same message multiple times per render cycle
    if (!this.messageCache) {
      this.messageCache = new Map();
    }

    if (this.messageCache.has(cacheKey)) {
      return this.messageCache.get(cacheKey)!; // Non-null assertion since we just checked has()
    }

    const formatted = this.formatAgentMessage(message);
    this.messageCache.set(cacheKey, formatted);

    // Clear cache if it gets too large (keep only last 10 entries)
    if (this.messageCache.size > 10) {
      const firstKey = this.messageCache.keys().next().value;
      if (firstKey) {
        this.messageCache.delete(firstKey);
      }
    }

    return formatted;
  }

  // Helper method to format agent display names
  private formatAgentDisplayName(agentType: string): string {
    return TextUtils.pascalOrCamelToDisplayName(agentType);
  }

  // Helper method to register or update agent participant
  private updateAgentParticipant(agent: EnrichedAgent): void {


    // Use the canonical agent name as the key to prevent duplicates
    // This ensures the same agent always uses the same key regardless of how it's referenced
    let participantKey = this.getAgentDisplayName(agent);

    // Loop through the keys and find the key that, if lowercased and stripped of all spaces, equals the agentName or displayName

    if (this.agentParticipants.has(this.getAgentDisplayName(agent))) {
      // Update existing entry
      const existingEntry = this.agentParticipants.get(participantKey)!;
      existingEntry.lastActivity = new Date();
      existingEntry.messageCount += 1;


    } else {
      // Create new entry using canonical names
      this.agentParticipants.set(participantKey, {
        name: agent.name,
        agent: agent,
        displayName: agent.name,
        lastActivity: new Date(),
        messageCount: 1
      });
    }
  }

  // Helper method to get active agent participants
  getActiveAgentParticipants(): AgentParticipant[] {
    // Clean up any duplicates before returning
    this.cleanupDuplicateParticipants();

    const participants = Array.from(this.agentParticipants.values())
      .sort((a, b) => b.lastActivity.getTime() - a.lastActivity.getTime());
    return participants;
  }

  // Get truncated session ID for display (first 8 characters)
  getTruncatedSessionId(): string {
    const sessionId = this.getCurrentSessionId();
    if (!sessionId) return 'No session';
    return sessionId.substring(0, 8) + '...';
  }

  // Get full session ID for tooltip
  getFullSessionId(): string {
    return this.getCurrentSessionId() || 'No active session';
  }

  // Copy session ID to clipboard
  copySessionId(): void {
    const sessionId = this.getCurrentSessionId();
    if (sessionId) {
      navigator.clipboard.writeText(sessionId).then(() => {
        // Brief visual feedback could be added here
        console.log('Session ID copied to clipboard:', sessionId);
      }).catch(err => {
        console.error('Failed to copy session ID:', err);
      });
    }
  }

  // Helper method to clean up duplicate participants
  private cleanupDuplicateParticipants(): void {
    const seenAgents = new Map<string, string>(); // normalized name -> canonical key
    const keysToRemove: string[] = [];

    for (const [key, participant] of this.agentParticipants.entries()) {
      // Create a normalized identifier for this agent
      const normalizedName = participant.displayName;

      if (seenAgents.has(normalizedName)) {
        // We've seen this agent before - mark the current key for removal
        const existingKey = seenAgents.get(normalizedName)!;
        const existingParticipant = this.agentParticipants.get(existingKey);

        if (existingParticipant) {
          // Merge the message counts and keep the most recent activity
          existingParticipant.messageCount += participant.messageCount;
          if (participant.lastActivity > existingParticipant.lastActivity) {
            existingParticipant.lastActivity = participant.lastActivity;
          }
        }

        keysToRemove.push(key);
      } else {
        // First time seeing this agent
        seenAgents.set(normalizedName, key);
      }
    }

    // Remove duplicate entries
    keysToRemove.forEach(key => {
      this.agentParticipants.delete(key);
    });
  }

  // Helper method to toggle reference visibility
  toggleReferences(messageId: string): void {
    const currentState = this.referencesExpanded.get(messageId) || false;
    this.referencesExpanded.set(messageId, !currentState);
  }

  // Helper method to check if references are expanded
  areReferencesExpanded(messageId: string): boolean {
    return this.referencesExpanded.get(messageId) || false;
  }

  // Helper method to get appropriate icon for scenario category
  getScenarioIcon(category: string): string {
    const iconMap: { [key: string]: string } = {
      'Campaign Planning': 'campaign',
      'Media Planning': 'tv',
      'Bidding': 'trending_up',
      'Analytics': 'analytics',
      'Optimization': 'tune',
      'default': 'lightbulb'
    };
    return iconMap[category] || iconMap['default'];
  }

  // Get available agent types from context data (for filtering typeahead suggestions)
  getAvailableAgentTypes(): any[] {
    // Fallback to empty array (no filtering) if not specified
    return this.agentConfig.getAvailableAgents();
  }

  // Method to format context data as JSON for the agent
  private formatContextData(): string {
    if (!this.contextData) {
      return '';
    }

    const contextHeader = `The user is currently working with the following ${this.agentType} data. Unless the user provides differing contextual information, please consider this context when providing recommendations and analysis.`;

    try {
      // Filter context data based on visibility settings
      let filteredContextData: any = {};

      // Only include sections that are explicitly selected in visibility settings
      for (const sectionKey of this.visibilitySettings.includedContextSections) {
        if (this.contextData.hasOwnProperty(sectionKey)) {
          filteredContextData[sectionKey] = this.contextData[sectionKey];
        }
      }

      // If no sections are selected, return empty string (no context sent)
      if (Object.keys(filteredContextData).length === 0) {
        return '';
      }

      const formattedJson = JSON.stringify(filteredContextData, null, 2);
      return `${contextHeader}\`\`\`json
${formattedJson}
\`\`\`

`;
    } catch (error) {
      console.warn('Error formatting context data:', error);
      return `${contextHeader}[Context data formatting error]

`;
    }
  }

  isThinkingCollapsed(messageId: string): boolean {
    // Default to collapsed (true) for completed messages, expanded (false) for thinking messages
    const message = this.messages.find(m => m.id === messageId);
    const isCurrentlyThinking = message?.data?.isThinking;

    // If the message is currently thinking, show expanded by default
    // If the message is complete, show collapsed by default
    const defaultState = !isCurrentlyThinking;

    return this.thinkingCollapsed.get(messageId) ?? defaultState;
  }

  toggleThinkingCollapsed(messageId: string): void {
    const current = this.isThinkingCollapsed(messageId);
    this.thinkingCollapsed.set(messageId, !current);
  }

  getThinkingPreview(thinkingContent: string): string {
    if (!thinkingContent) return '';

    const lines = thinkingContent.split('\n').filter(line => line.trim());

    // Take first 2-3 meaningful lines
    const previewLines = lines.slice(0, 3);

    // If there are more lines, add ellipsis
    if (lines.length > 3) {
      return previewLines.join('\n') + '\n...';
    }

    return previewLines.join('\n');
  }

  // Enhanced method to check if a message should be hidden
  shouldHideMessage(message: Message, index: number): boolean {
    if (message.type == 'passthrough') return true;
    // Check if message type should be hidden based on visibility settings
    if (message.data?.messageType && this.visibilitySettings.hiddenMessageTypes.includes(message.data.messageType)) {
      return true;
    }

    // Check legacy hiddenTypes for backward compatibility
    if (message.data?.messageType && this.hiddenTypes.includes(message.data.messageType)) {
      return true;
    }

    // Check if message type matches any hidden types
    if (message.type && this.visibilitySettings.hiddenMessageTypes.includes(message.type)) {
      return true;
    }

    // Check if agent should be hidden
    if (message.agentName && this.visibilitySettings.hiddenAgents.indexOf(message.agentName) > -1) {
      return true;
    }

    // Also check data.agentName for agent messages
    if (message.data?.agentName && this.visibilitySettings.hiddenAgents.indexOf(message.data.agentName) > -1) {
      return true;
    }

    // Skip user messages for duplication check
    if (message.sender !== 'agent') {
      return false;
    }

    if (!message.text || message.text.length == 0)
      return true;

    // Check if the next message exists and is exactly the same from the same agent
    const nextMessage = this.messages[index + 1];
    if (!nextMessage || nextMessage.sender !== 'agent') {
      return false;
    }

    // Hide if both are from the same agent and have identical text content
    return message.displayName === nextMessage.displayName &&
      message.text === nextMessage.text;
  }

  // Helper method to format agent mentions with colors using markdown-compatible HTML
  private formatAgentMentions(text: string): string {

    // Match various @[Agent Name] patterns and replace with markdown-compatible styled HTML
    const result = text.replace(/@\[([^\]]+)\]/g, (match, agentName) => {
      const color = this.getAgentColorSync(agentName);
      // Use inline HTML that markdown will render properly with a data attribute for click handling
      const replacement = `<strong style="color: ${color}; cursor: pointer;" data-agent-mention="${agentName}" class="agent-mention-clickable">*${match}*</strong>`;
      return replacement;
    });

    return result;
  }

  // Add click handlers to agent mentions after markdown rendering
  private addAgentMentionClickHandlers(): void {
    if (!this.messagesContainer) return;

    // Find all agent mentions in the rendered markdown
    const agentMentions = this.messagesContainer.nativeElement.querySelectorAll('.agent-mention-clickable[data-agent-mention]');

    agentMentions.forEach((element: HTMLElement) => {
      // Check if click handler is already attached
      if (!element.dataset['clickHandlerAttached']) {
        element.dataset['clickHandlerAttached'] = 'true';

        element.addEventListener('click', (event: Event) => {
          event.preventDefault();
          event.stopPropagation();

          const agentName = element.dataset['agentMention'];
          if (agentName) {
            // Get the canonical agent info to ensure we're using the right name
            try {
              const agentInfo = this.awsConfig.getAgentByOtherNames(agentName);
              const canonicalDisplayName = agentInfo.displayName;

              // Check if this agent has visualization data using the canonical display name
              if (this.agentVisualizationService.hasVisualizationData(canonicalDisplayName)) {
                const rect = element.getBoundingClientRect();

                const visualization = this.agentVisualizationService.getAgentVisualization(canonicalDisplayName);
                this.popoverVisualData = visualization?.visualData || {};
                this.popoverAgent = canonicalDisplayName;
                this.showVisualizationPopover = true;

                // Trigger change detection
                this.changeDetectorRef.detectChanges();

              } else {
              }
            } catch (error) {
              console.warn(`⚠️ Could not resolve agent name: ${agentName}`, error);
            }
          }
        });
      }
    });
  }

  // Helper method to get performance level color for allocation cards
  getPerformanceColor(performance: string): string {
    switch (performance?.toLowerCase()) {
      case 'high': return '#10b981';
      case 'medium': return '#f59e0b';
      case 'low':
      case 'moderate': return '#ef4444';
      default: return '#6b7280';
    }
  }

  // Helper method to normalize JSON content for parsing
  private normalizeJsonForParsing(content: string): string {
    return content
      // Remove extra whitespace at start/end
      .trim()
      // Normalize line endings
      .replace(/\r\n/g, '\n')
      .replace(/\r/g, '\n')
      // Remove excessive whitespace between lines but preserve structure
      .replace(/\n\s*\n\s*\n/g, '\n\n')
      // Clean up common formatting issues in code blocks
      .replace(/^\s*{\s*$/gm, '{')  // Clean up standalone opening braces
      .replace(/^\s*}\s*$/gm, '}')  // Clean up standalone closing braces
      .replace(/^\s*\[\s*$/gm, '[')  // Clean up standalone opening brackets
      .replace(/^\s*\]\s*$/gm, ']')  // Clean up standalone closing brackets
      // Fix common JSON formatting issues
      .replace(/,\s*\n\s*}/g, '\n}')  // Remove trailing commas before closing braces
      .replace(/,\s*\n\s*]/g, '\n]')  // Remove trailing commas before closing brackets
      // Normalize property spacing
      .replace(/"\s*:\s*/g, '": ')     // Standardize property spacing
      .replace(/,\s*"/g, ', "')        // Standardize comma spacing
      // Handle multi-line string values that might be broken
      .replace(/"\s*\n\s*"/g, ' ')     // Join broken string literals
      // Clean up array and object formatting
      .replace(/\[\s*\n\s*/g, '[\n  ') // Clean array opening
      .replace(/\s*\n\s*\]/g, '\n]')   // Clean array closing
      .replace(/{\s*\n\s*/g, '{\n  ')  // Clean object opening
      .replace(/\s*\n\s*}/g, '\n}');   // Clean object closing
  }

  // Helper method to unescape JSON content with escaped newlines and quotes
  private unescapeJsonContent(content: string): string {
    const result = content
      // Unescape newlines
      .replace(/\\n/g, '\n')
      // Unescape carriage returns
      .replace(/\\r/g, '\r')
      // Unescape tabs
      .replace(/\\t/g, '\t')
      // Unescape quotes
      .replace(/\\"/g, '"')
      // Unescape single quotes (in case they're escaped)
      .replace(/\\'/g, "'")
      // Unescape forward slashes
      .replace(/\\\//g, '/')
      // Unescape backslashes (but do this last to avoid double unescaping)
      .replace(/\\\\/g, '\\')
      // Clean up any extra whitespace
      .trim();

    /* 
     */
    return result;
  }

  // Helper method to get agent purpose based on current context

  // Helper method to parse standalone JSON objects from text
  // Helper method to clean JSON text by removing JavaScript-style comments
  private cleanJsonComments(jsonText: string): string {
    // Remove single-line comments (// comment)
    // But be careful not to remove // inside strings
    let cleaned = '';
    let inString = false;
    let escapeNext = false;
    let i = 0;

    while (i < jsonText.length) {
      const char = jsonText[i];
      const nextChar = jsonText[i + 1];

      if (escapeNext) {
        cleaned += char;
        escapeNext = false;
        i++;
        continue;
      }

      if (char === '\\') {
        escapeNext = true;
        cleaned += char;
        i++;
        continue;
      }

      if (char === '"' && !escapeNext) {
        inString = !inString;
        cleaned += char;
        i++;
        continue;
      }

      // If we're not in a string and we find //, skip to end of line
      if (!inString && char === '/' && nextChar === '/') {
        // Skip to end of line or end of text
        while (i < jsonText.length && jsonText[i] !== '\n' && jsonText[i] !== '\r') {
          i++;
        }
        continue;
      }

      cleaned += char;
      i++;
    }

    return cleaned;
  }

  // Helper method to parse potentially incomplete JSON using partial-json
  private parsePartialJson(jsonText: string): { success: boolean; data?: any; error?: string; isPartial?: boolean } {
    if (!jsonText || jsonText.trim().length === 0) {
      return { success: false, error: 'Empty JSON text' };
    }

    // Try multiple cleaning approaches
    const cleaningAttempts = [
      jsonText, // Original
      jsonText.trim(), // Basic trim
      this.aggressiveJsonClean(jsonText), // Aggressive cleaning
    ];

    for (let i = 0; i < cleaningAttempts.length; i++) {
      const cleanedJson = cleaningAttempts[i];

      try {
        // First, try standard JSON.parse for complete JSON
        const standardParsed = JSON.parse(cleanedJson);
        if (i > 0) {
        }
        return { success: true, data: standardParsed, isPartial: false };
      } catch (standardError) {
        // If standard parsing fails, try partial JSON parsing
        try {
          if (i === 0) {
          }
          const partialParsed = partialJson.parse(cleanedJson);

          // Check if we got a meaningful result
          if (partialParsed && typeof partialParsed === 'object') {
            if (i > 0) {
            }
            return { success: true, data: partialParsed, isPartial: true };
          }
        } catch (partialError) {
          // Continue to next cleaning attempt
          if (i === cleaningAttempts.length - 1) {
            console.warn('❌ All JSON parsing attempts failed:', {
              standardError: standardError instanceof Error ? standardError.message : String(standardError),
              partialError: partialError instanceof Error ? partialError.message : String(partialError),
              jsonSnippet: jsonText.substring(0, 200) + (jsonText.length > 200 ? '...' : '')
            });
          }
        }
      }
    }

    return {
      success: false,
      error: 'All JSON parsing attempts failed',
      isPartial: false
    };
  }

  // Aggressive JSON cleaning for problematic formatting
  private aggressiveJsonClean(jsonText: string): string {
    return jsonText
      .trim()
      // Remove all extra whitespace and normalize
      .replace(/\s+/g, ' ')
      // Fix common JSON issues
      .replace(/,\s*}/g, '}')     // Remove trailing commas before }
      .replace(/,\s*]/g, ']')     // Remove trailing commas before ]
      .replace(/"\s*:\s*/g, '":') // Normalize property spacing
      .replace(/,\s*"/g, ',"')    // Normalize comma spacing
      .replace(/{\s*/g, '{')      // Remove space after {
      .replace(/\s*}/g, '}')      // Remove space before }
      .replace(/\[\s*/g, '[')     // Remove space after [
      .replace(/\s*]/g, ']')      // Remove space before ]
      // Handle broken string concatenation
      .replace(/"\s+"/g, ' ')     // Join broken strings
      // Fix property names that might be broken across lines
      .replace(/"\s*\n\s*:/g, '":')
      // Fix values that might be broken across lines
      .replace(/:\s*\n\s*"/g, ':"');
  }

  private parseStandaloneJsonObjects(text: string, callback: (parsedJson: any, jsonText: string) => void): void {
    // Use a more robust approach - find potential JSON start positions and try to parse from there
    let startIndex = 0;

    while (true) {
      // Find the next potential JSON start
      const openBraceIndex = text.indexOf('{', startIndex);
      if (openBraceIndex === -1) break;

      // Try to find the matching closing brace by counting nested braces
      let braceCount = 0;
      let endIndex = openBraceIndex;
      let inString = false;
      let escapeNext = false;

      for (let i = openBraceIndex; i < text.length; i++) {
        const char = text[i];

        if (escapeNext) {
          escapeNext = false;
          continue;
        }

        if (char === '\\') {
          escapeNext = true;
          continue;
        }

        if (char === '"' && !escapeNext) {
          inString = !inString;
          continue;
        }

        if (!inString) {
          if (char === '{') {
            braceCount++;
          } else if (char === '}') {
            braceCount--;
            if (braceCount === 0) {
              endIndex = i;
              break;
            }
          }
        }
      }

      // Handle both complete and potentially incomplete JSON
      let jsonText: string;
      let isComplete = false;

      if (braceCount === 0 && endIndex > openBraceIndex) {
        // Found complete JSON object
        jsonText = text.substring(openBraceIndex, endIndex + 1);
        isComplete = true;
      } else {
        // Potentially incomplete JSON - take everything from the opening brace to end of text
        jsonText = text.substring(openBraceIndex);
        isComplete = false;
      }

      // First unescape the JSON content (handle escaped quotes and newlines)
      const unescapedJsonText = this.unescapeJsonContent(jsonText);
      // Then clean JavaScript-style comments before parsing
      const cleanedJsonText = this.cleanJsonComments(unescapedJsonText);

      // Use the new partial JSON parser
      const parseResult = this.parsePartialJson(cleanedJsonText);

      if (parseResult.success && parseResult.data) {

        // Only process if it's a meaningful object (not just a simple key-value pair)
        if (typeof parseResult.data === 'object' && parseResult.data !== null && Object.keys(parseResult.data).length > 0) {
          callback(parseResult.data, jsonText);
        }
      } else {

      }

      if (isComplete) {
        startIndex = endIndex + 1;
      } else {
        // For incomplete JSON, we've processed to the end of the text
        break;
      }
    }
  }

  // Helper method to identify metric data JSON
  private isMetricDataJson(obj: any): boolean {
    if (!obj) return false;

    // Check for standard metric data structure
    if (obj.title && Array.isArray(obj.metrics)) {
      return true;
    }

    // Check for direct metrics array
    if (Array.isArray(obj.metrics)) {
      return true;
    }

    // Check for legacy simple metrics object
    if (obj.hasOwnProperty('roas') || obj.hasOwnProperty('ctr') || obj.hasOwnProperty('cpa') || obj.hasOwnProperty('efficiency')) {
      return true;
    }

    // Check for structured metrics with product_line, projected_roas, etc.
    if (Array.isArray(obj.metrics) && obj.metrics.length > 0) {
      const firstMetric = obj.metrics[0];
      if (firstMetric && (
        firstMetric.hasOwnProperty('product_line') ||
        firstMetric.hasOwnProperty('projected_roas') ||
        firstMetric.hasOwnProperty('win_rate') ||
        firstMetric.hasOwnProperty('recommended_budget') ||
        firstMetric.hasOwnProperty('expected_cvr')
      )) {
        return true;
      }
    }

    return false;
  }

  // Helper method to identify channel allocations JSON
  private isChannelAllocationsJson(obj: any): boolean {
    if (!obj) return false;

    // Must have allocations array with valid structure
    if (!Array.isArray(obj.allocations) || obj.allocations.length === 0) {
      return false;
    }

    // Check that ALL allocations have the required structure
    return obj.allocations.every((allocation: any) =>
      allocation &&
      typeof allocation === 'object' &&
      // Must have a name/channel identifier
      (allocation.channel || allocation.name || allocation.title || allocation.placement ||
        allocation.primaryLabel || allocation.secondaryLabel) &&
      // Must have percentage (support both formats)
      ((allocation.percentage !== undefined && typeof allocation.percentage === 'number') ||
        (allocation.allocationPercentage !== undefined && typeof allocation.allocationPercentage === 'number')) &&
      // Must have at least one additional allocation-specific property
      (allocation.performance || allocation.efficiency || allocation.cost_efficiency ||
        allocation.safety || allocation.engagement || allocation.audience ||
        allocation.bid_modifier || allocation.timing || allocation.role || allocation.budget ||
        allocation.performanceLevel || allocation.budgetAmount || allocation.statusIndicator ||
        allocation.confidenceLevel || allocation.riskLevel || allocation.description)
    ) &&
      // Exclude if it looks like metrics data
      !obj.allocations.some((allocation: any) =>
        allocation.product_line || allocation.projected_roas || allocation.win_rate ||
        allocation.recommended_budget || allocation.expected_cvr
      );
  }

  // Helper method to identify channel cards JSON
  private isChannelCardsJson(obj: any): boolean {
    if (!obj) return false;

    // Check if it has a channels array
    if (Array.isArray(obj.channels)) {
      return obj.channels.some((channel: any) =>
        channel.name && (
          channel.projected_ctr || channel.projected_cvr ||
          channel.optimization || channel.risk_factors ||
          channel.safety_score || channel.brand_fit ||
          channel.expected_performance
        )
      );
    }

    // Check if it's a direct array of channels
    if (Array.isArray(obj) && obj.length > 0) {
      return obj.some((item: any) =>
        item.name && (
          item.projected_ctr || item.projected_cvr ||
          item.optimization || item.risk_factors ||
          item.safety_score || item.brand_fit ||
          item.expected_performance
        )
      );
    }

    return false;
  }

  // Helper method to identify segment cards JSON
  private isSegmentCardsJson(obj: any): boolean {
    if (!obj) return false;

    // Check if it has a segments array
    if (Array.isArray(obj.segments)) {
      return obj.segments.some((segment: any) =>
        segment.name && (
          segment.bid_range || segment.new_bid_range ||
          segment.win_rate || segment.target_win_rate ||
          segment.expected_ctr || segment.required_cvr ||
          segment.expected_roas || segment.min_roas ||
          segment.allocation || segment.channels ||
          segment.confidence || segment.recommendation ||
          segment.overlap_risk || segment.monitoring_frequency ||
          segment.adjustment_threshold || Array.isArray(segment.metrics)
        )
      );
    }

    // Check if it's a direct array of segments
    if (Array.isArray(obj) && obj.length > 0) {
      return obj.some((item: any) =>
        item.name && (
          item.bid_range || item.new_bid_range ||
          item.win_rate || item.target_win_rate ||
          item.expected_ctr || item.required_cvr ||
          item.expected_roas || item.min_roas ||
          item.allocation || item.channels ||
          item.confidence || item.recommendation ||
          item.overlap_risk || item.monitoring_frequency
        )
      );
    }

    return false;
  }

  // Helper method to identify weather analysis JSON
  private isWeatherAnalysisJson(obj: any): boolean {
    if (!obj) return false;

    // Check for weather analysis structure
    const hasWeatherStructure = (
      obj.title &&
      obj.sections &&
      Array.isArray(obj.sections) &&
      obj.sections.some((section: any) =>
        section.sectionTitle &&
        section.metrics &&
        Array.isArray(section.metrics) &&
        section.metrics.some((metric: any) =>
          metric.metricName &&
          metric.value &&
          (metric.impactLevel || metric.confidenceLevel || metric.recommendation)
        )
      )
    );

    // Check for weather-specific keywords in title or content
    const hasWeatherKeywords = (
      (obj.title && obj.title.toLowerCase().includes('weather')) ||
      (obj.subtitle && obj.subtitle.toLowerCase().includes('weather')) ||
      (obj.marketSpecificImpacts && Array.isArray(obj.marketSpecificImpacts)) ||
      (obj.weatherTriggeredOptimizations && Array.isArray(obj.weatherTriggeredOptimizations)) ||
      (obj.sections && Array.isArray(obj.sections) &&
        obj.sections.some((section: any) =>
          section.sectionTitle && (
            section.sectionTitle.toLowerCase().includes('weather') ||
            section.sectionTitle.toLowerCase().includes('indoor') ||
            section.sectionTitle.toLowerCase().includes('outdoor') ||
            section.sectionTitle.toLowerCase().includes('mobility') ||
            section.sectionTitle.toLowerCase().includes('seasonal')
          )
        )
      )
    );

    return hasWeatherStructure && hasWeatherKeywords;
  }

  // Helper method to normalize channel cards structure
  private normalizeChannelCardsStructure(obj: any): any {
    // If obj directly has channels array, wrap it
    if (Array.isArray(obj.channels)) {
      return obj;
    }

    // If obj is an array, assume it's the channels array
    if (Array.isArray(obj)) {
      return { channels: obj };
    }

    // If obj has properties that look like channels, try to extract them
    const potentialChannels: any[] = [];
    for (const [key, value] of Object.entries(obj)) {
      if (typeof value === 'object' && value !== null) {
        potentialChannels.push({ name: key, ...value as any });
      }
    }

    if (potentialChannels.length > 0) {
      return { channels: potentialChannels };
    }

    // Default fallback
    return { channels: [] };
  }

  // Helper method to transform weather analysis data to metrics visualization format
  private transformWeatherDataToMetrics(weatherData: any): any {
    if (!weatherData || !weatherData.sections) {
      return null;
    }

    try {
      // Transform weather analysis sections into metrics format
      const metrics: any[] = [];

      weatherData.sections.forEach((section: any) => {
        if (section.metrics && Array.isArray(section.metrics)) {
          const sectionMetrics = {
            primaryLabel: section.sectionTitle || 'Weather Impact',
            secondaryLabel: `Weather-based analysis for ${section.sectionTitle || 'campaign optimization'}`,
            items: section.metrics.map((metric: any) => ({
              primaryLabel: metric.metricName || 'Weather Metric',
              actualValue: metric.value || 'N/A',
              targetValue: '', // Weather data doesn't typically have targets
              forecastedValue: '', // Could be used for predictions
              actualLabel: metric.description || '',
              targetLabel: '',
              forecastedLabel: metric.recommendation || '',
              unit: this.extractUnit(metric.value || ''),
              trend: this.mapImpactToTrend(metric.impactLevel),
              confidenceLevel: metric.confidenceLevel || 'medium',
              isPrimary: metric.impactLevel === 'positive',
              insights: [metric.description || ''],
              risks: metric.impactLevel === 'negative' ? [metric.description || ''] : [],
              recommendations: [metric.recommendation || '']
            }))
          };
          metrics.push(sectionMetrics);
        }
      });

      return {
        visualizationType: 'metrics',
        templateId: 'weather-metrics-visualization',
        title: weatherData.title || 'Weather Impact Analysis',
        subtitle: weatherData.subtitle || 'Weather-based campaign optimization insights',
        collaborativeInsights: {
          primaryAgent: 'WeatherImpactAnalyzerAgent',
          supportingAgent1: 'MediaPlannerAgent',
          supportingAgent2: 'TimingStrategyAgent'
        },
        metrics: metrics
      };
    } catch (error) {
      console.warn('❌ Failed to transform weather data to metrics format:', error);
      return null;
    }
  }

  // Helper method to extract unit from value string (e.g., "+18%" -> "%")
  private extractUnit(value: string): string {
    if (!value) return '';

    const unitMatches = value.match(/[%$€£¥₹]/);
    if (unitMatches) {
      return unitMatches[0];
    }

    // Check for common units at the end
    const endUnitMatches = value.match(/(days?|hours?|minutes?|x|times?)$/i);
    if (endUnitMatches) {
      return endUnitMatches[1];
    }

    return '';
  }

  // Helper method to map weather impact level to trend direction
  private mapImpactToTrend(impactLevel: string): string {
    switch (impactLevel?.toLowerCase()) {
      case 'positive':
        return 'up';
      case 'negative':
        return 'down';
      case 'neutral':
      default:
        return 'stable';
    }
  }

  getAgentDisplayName(agent: EnrichedAgent): string {
    // Use the centralized agent lookup - it handles all the complexity
    return agent ? `${agent.name}` : `invalid`;
  }


  getAgentDisplayNameStr(agent: string): string {
    // Use the centralized agent lookup - it handles all the complexity
    return agent;
  }

  getAgentBGColor(agent: string) {
    if (!agent) return null;
    let color = this.getAgentColor(agent);
    if (color.indexOf('#') > -1) {
      color = color.substring(0, 7);
      return (color += "4D"); // 30% opacity in hex (77 in decimal = 4D in hex)
    } else {
      let parenthesisIndex = color.indexOf('(');
      if (parenthesisIndex > -1) {
        color = color.substring(parenthesisIndex);
        let colors = color.replace('(', '').replace(')', '').split(',')
        color = 'rgba(' + colors[0] + ', ' + colors[1] + ', ' + colors[2] + ', 0.2)'; // 30% opacity
        return color;
      }
    }
    return null;
  }

  getBorderColor(agentName: string) {
    if (!agentName) return null;
    let agent = this.agentConfig.getEnrichedAgents().find(a => a.name == agentName);
    if (!agent) return null;
    let color = agent.color;
    if (color.indexOf('#') > -1) {
      color = color.substring(0, 7);
      return (color += "4D"); // 30% opacity in hex (77 in decimal = 4D in hex)
    } else {
      let parenthesisIndex = color.indexOf('(');
      if (parenthesisIndex > -1) {
        color = color.substring(parenthesisIndex);
        let colors = color.replace('(', '').replace(')', '').split(',')
        color = 'rgba(' + colors[0] + ', ' + colors[1] + ', ' + colors[2] + ', 0.25)'; // 30% opacity
        return color;
      }
    }
    return null;
  }




  getAgentColorSync(agentType: string = ''): string {
    const color = this.agentConfig.getAgentColorSync(agentType);

    return color;
  }

  getAgentBackgroundGradient(agent: EnrichedAgent): string {
    const agentColor = agent.color + "11";
    return agentColor
  }

  // Helper method to normalize agent names for consistent color calculation
  private normalizeAgentNameForColor(agentName: string): string {
    // First, use the centralized normalization utility
    const normalizedName = TextUtils.pascalOrCamelToDisplayName(TextUtils.removeStackPrefixSuffix(agentName, this.awsConfig.getStackPrefix(), this.awsConfig.getStackSuffix()));

    // Then try to get the canonical agent info from AWS config
    try {
      const agentInfo = this.awsConfig.getAgentByOtherNames(normalizedName);
      if (agentInfo && agentInfo.name) {
        return agentInfo.name;
      }
    } catch (error) {
      // If lookup fails, use the normalized name for consistency
    }

    return normalizedName;
  }

  getAgentIcon(agentType: string): string {
    return 'psychology';
  }

  // File upload methods
  onFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    if (input.files && input.files.length > 0) {
      this.handleFiles(Array.from(input.files));

    }
    let inputElement = this.messageInput?.nativeElement;
    // Set cursor position after the mention
    this.safeSetTimeout(() => {
      const newCursorPosition = this.currentMessage.length
      this.currentMessage
      inputElement.setSelectionRange(newCursorPosition, newCursorPosition);
      inputElement.focus();
    }, 0);
  }

  triggerFileSelect(): void {
    this.fileInput?.nativeElement?.click();
  }

  onDragOver(event: DragEvent): void {
    event.preventDefault();
    event.stopPropagation();
    this.isDragOver = true;
  }

  onDragLeave(event: DragEvent): void {
    event.preventDefault();
    event.stopPropagation();
    this.isDragOver = false;
  }

  onDrop(event: DragEvent): void {
    event.preventDefault();
    event.stopPropagation();
    this.isDragOver = false;

    if (event.dataTransfer && event.dataTransfer.files.length > 0) {
      this.handleFiles(Array.from(event.dataTransfer.files));
    }
  }

  private async handleFiles(files: File[]): Promise<void> {
    for (const file of files) {
      if (!this.isValidFile(file)) {
        console.warn(`File ${file.name} is not supported or too large`);
        continue;
      }

      try {
        const base64Content = await this.fileToBase64(file);
        const attachedFile: AttachedFile = {
          name: file.name,
          size: file.size,
          type: file.type,
          base64Content,
          mediaType: file.type
        };

        this.attachedFiles.push(attachedFile);
      } catch (error) {
        console.error(`❌ Error processing file ${file.name}:`, error);
      }
    }

    // Trigger change detection to update UI
    this.changeDetectorRef.detectChanges();
  }

  private isValidFile(file: File): boolean {
    if (file.size > this.maxFileSize) {
      console.warn(`File ${file.name} is too large. Max size: ${this.formatFileSize(this.maxFileSize)}`);
      return false;
    }

    if (!this.allowedFileTypes.includes(file.type)) {
      console.warn(`File type ${file.type} is not supported for ${file.name}`);
      return false;
    }

    return true;
  }

  private fileToBase64(file: File): Promise<string> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        if (typeof reader.result === 'string') {
          // Remove the data URL prefix (e.g., "data:application/pdf;base64,")
          const base64 = reader.result.split(',')[1];
          resolve(base64);
        } else {
          reject(new Error('Failed to read file as base64'));
        }
      };
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
  }

  removeAttachedFile(index: number): void {
    this.attachedFiles.splice(index, 1);
  }

  formatFileSize(bytes: number): string {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  }

  getFileIcon(fileType: string): string {
    if (fileType.startsWith('image/')) {
      return 'image';
    } else if (fileType === 'application/pdf') {
      return 'picture_as_pdf';
    } else if (fileType.includes('spreadsheet') || fileType.includes('excel')) {
      return 'table_chart';
    } else if (fileType.includes('document') || fileType.includes('word')) {
      return 'description';
    } else if (fileType === 'text/plain') {
      return 'text_snippet';
    } else if (fileType === 'text/csv') {
      return 'table_view';
    } else if (fileType === 'application/json') {
      return 'code';
    }
    return 'attach_file';
  }

  // Agent selection methods
  confirmAgentSelection(): void {

    // Restore pending message and files
    this.currentMessage = this.pendingMessage;
    this.attachedFiles = [...this.pendingAttachedFiles];

    // Hide selector and send message
    this.showAgentSelector = false;
    this.sendMessage(this.selectedAgentForMessage);

    // Clear pending data
    this.pendingMessage = '';
    this.pendingAttachedFiles = [];
  }

  cancelAgentSelection(): void {
    this.showAgentSelector = false;
    this.pendingMessage = '';
    this.pendingAttachedFiles = [];
  }

  updateAvailableAgents() {
    // Get all available agents from the centralized service
    this.availableAgents = this.agentMentionService.getAvailableAgents();
  }

  selectAgentOption(agent: EnrichedAgent): void {
    this.selectedAgentForMessage = agent;
  }

  // Agent click handler - generates summary instead of showing visualizations
  onAgentClick(event: MouseEvent, agent: any): void {
    // Prevent event bubbling
    event.stopPropagation();

    // Generate agent summary using Claude Haiku
    this.getAgentContributionsFromMemory(agent);
  }

  onCloseVisualizationPopover(): void {
    this.showVisualizationPopover = false;
    this.popoverAgent = null;
    this.popoverVisualData = {};

    // Trigger change detection to ensure the popover disappears
    this.changeDetectorRef.detectChanges();
  }

  // Check if an agent has visualization data (for UI indicators)
  hasVisualizationData(agent: any): boolean {
    // Get the correct display name from the agent participant object
    const agentDisplayName = agent.displayName || this.getAgentDisplayName(agent.name);

    // First try exact match
    if (this.agentVisualizationService.hasVisualizationData(agentDisplayName)) {
      return true;
    }

    // Then try normalized version
    const normalizedName = TextUtils.pascalOrCamelToDisplayName(agent.name || agentDisplayName);
    const hasData = this.agentVisualizationService.hasVisualizationData(normalizedName);

    return hasData;
  }

  // Helper method to get templateId for a specific visualization type from visual data
  getTemplateId(visualData: any, visualizationType: string): string | undefined {
    if (!visualData) return undefined;

    switch (visualizationType) {
      case 'metrics':
        return visualData.metricTemplateId;
      case 'allocations':
        return visualData.allocationsTemplateId;
      case 'channels':
        return visualData.channelsTemplateId;
      case 'segments':
        return visualData.segmentsTemplateId;
      case 'creative':
      case 'creatives':
        return visualData.creativeTemplateId;
      case 'timeline':
        return visualData.timelineTemplateId;
      case 'decision-tree':
      case 'decisiontree':
        return visualData.decisionTreeTemplateId;
      case 'histogram':
        return visualData.histogramTemplateId;
      case 'double-histogram':
        return visualData.doubleHistogramTemplateId;
      case 'bar-chart':
        return visualData.barChartTemplateId;
      case 'donut-chart':
        return visualData.donutChartTemplateId;
      case 'weather-analysis':
      case 'weather':
        return visualData.metricTemplateId; // Weather analysis uses metrics template
      default:
        return undefined;
    }
  }

  // Get visualization count for an agent (for UI indicators)
  getVisualizationCount(displayName: string): number {
    // First try exact match
    let count = this.agentVisualizationService.getVisualizationCount(displayName);
    if (count > 0) {
      return count;
    }

    // Then try normalized version
    const normalizedName = TextUtils.pascalOrCamelToDisplayName(displayName);
    return this.agentVisualizationService.getVisualizationCount(normalizedName);
  }




  // Test method to verify partial JSON parsing works correctly
  testPartialJsonParsing(): void {

    // Test cases for partial JSON
    const testCases = [
      {
        name: 'Complete JSON',
        json: '{"visualizationType": "metrics", "title": "Test", "metrics": [{"label": "ROAS", "value": "3.5"}]}'
      },
      {
        name: 'Incomplete JSON - missing closing brace',
        json: '{"visualizationType": "metrics", "title": "Test", "metrics": [{"label": "ROAS", "value": "3.5"}'
      },
      {
        name: 'Incomplete JSON - truncated in middle',
        json: '{"visualizationType": "metrics", "title": "Test", "metr'
      },
      {
        name: 'Incomplete JSON - missing quotes',
        json: '{"visualizationType": "metrics", "title": Test", "metrics": []}'
      },
      {
        name: 'Empty JSON',
        json: ''
      },
      {
        name: 'Invalid JSON',
        json: 'not json at all'
      }
    ];

    testCases.forEach(testCase => {

      const result = this.parsePartialJson(testCase.json);

    });

  }

  // Voice recording methods
  async toggleVoiceRecording(): Promise<void> {
    if (this.isRecording) {
      this.stopVoiceRecording();
    } else {
      await this.startVoiceRecording();
    }
  }

  private async startVoiceRecording(): Promise<void> {
    try {
      // Check if transcription is supported
      if (!this.transcribeService.isSupported()) {
        console.warn('Voice recording not supported in this browser');
        return;
      }

      // Request microphone permission
      const hasPermission = await this.transcribeService.requestMicrophonePermission();
      if (!hasPermission) {
        console.warn('Microphone permission denied');
        return;
      }

      this.isRecording = true;
      this.partialTranscript = '';

      // Start transcription
      this.transcriptionSubscription = this.transcribeService.startTranscription().subscribe({
        next: (event) => {

          if (event.type === 'partial') {
            // Update the text area with partial transcript in real-time
            this.partialTranscript = event.text;
            this.currentMessage = event.text;
            this.changeDetectorRef.detectChanges();
          } else if (event.type === 'final') {
            // Final transcript received
            this.currentMessage = event.text;
            this.partialTranscript = event.text;
            this.changeDetectorRef.detectChanges();
          } else if (event.type === 'error') {
            console.error('Transcription error:', event.text);
            this.stopVoiceRecording();
          } else if (event.type === 'complete') {
            this.stopVoiceRecording();
          }
        },
        error: (error) => {
          console.error('Voice recording error:', error);
          this.stopVoiceRecording();
        }
      });

    } catch (error) {
      console.error('Error starting voice recording:', error);
      this.isRecording = false;
    }
  }

  private stopVoiceRecording(): void {

    this.isRecording = false;

    if (this.transcriptionSubscription) {
      this.transcriptionSubscription.unsubscribe();
      this.transcriptionSubscription = null;
    }

    this.transcribeService.stopTranscription();

    // If we have a final transcript, focus the input for potential editing
    if (this.currentMessage.trim()) {
      this.safeSetTimeout(() => {
        if (this.messageInput) {
          this.messageInput.nativeElement.focus();
        }
      }, 100);
    }

    this.changeDetectorRef.detectChanges();
  }

  // Check if voice recording is supported
  isVoiceRecordingSupported(): boolean {
    return this.transcribeService.isSupported();
  }





  // Helper method to extract key-value pairs from unparseable JSON
  private extractKeyValuePairs(jsonText: string): string {
    const keyValuePairs = this.findKeyValuePairs(jsonText);

    if (keyValuePairs.length === 0) {
      return `// Unable to parse JSON content\n// Raw content:\n${jsonText}`;
    }

    // Format as readable key-value pairs
    let formatted = '// Key-Value Pairs (extracted from partial JSON source content)\n\n';

    keyValuePairs.forEach((pair, index) => {
      formatted += `${pair.key}: ${pair.value}\n`;
    });

    formatted += `\n// Note: ${keyValuePairs.length} key-value pair${keyValuePairs.length > 1 ? 's' : ''} extracted from unparseable JSON content`;

    return formatted;
  }

  // Helper method to find key-value pairs in JSON-like text
  private findKeyValuePairs(text: string): Array<{ key: string; value: string }> {
    const pairs: Array<{ key: string; value: string }> = [];

    // Regular expression to match JSON key-value patterns
    // Matches: "key": "value", "key": number, "key": boolean, etc.
    const keyValueRegex = /"([^"]+)"\s*:\s*([^,}\]]+|"[^"]*"|\[[^\]]*\]|\{[^}]*\})/g;

    let match;
    while ((match = keyValueRegex.exec(text)) !== null) {
      const key = match[1];
      let value = match[2].trim();

      // Clean up the value
      if (value.endsWith(',')) {
        value = value.slice(0, -1).trim();
      }

      // Remove trailing brackets or braces if they seem incomplete
      if (value.match(/[\[{]$/) && !value.match(/^[\[{].*[\]}]$/)) {
        value = value.slice(0, -1).trim();
      }

      // Limit value length for display
      if (value.length > 100) {
        value = value.substring(0, 100) + '...';
      }

      pairs.push({ key, value });
    }

    // Also try to match simple patterns without quotes around keys (less strict)
    if (pairs.length === 0) {
      const relaxedRegex = /([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*([^,}\]]+|"[^"]*")/g;

      while ((match = relaxedRegex.exec(text)) !== null) {
        const key = match[1];
        let value = match[2].trim();

        if (value.endsWith(',')) {
          value = value.slice(0, -1).trim();
        }

        if (value.length > 100) {
          value = value.substring(0, 100) + '...';
        }

        pairs.push({ key, value });
      }
    }

    // Remove duplicates and limit to reasonable number
    const uniquePairs = pairs.filter((pair, index, self) =>
      index === self.findIndex(p => p.key === pair.key)
    ).slice(0, 20); // Limit to 20 pairs max

    return uniquePairs;
  }



  // Scenarios panel methods
  onScenarioSelected(data): void {
    this.selectedScenarioIndex = data.index;
    this.selectScenario(data.scenario, data.index);
  }


  hasSources(message: Message): boolean {
    if (!message.sources) return false;
    // Check if any agent has sources
    return Object.keys(message.sources).some(agentName =>
      message.sources![agentName] && message.sources![agentName].length > 0
    );
  }

  getSourceCount(message: Message): number {
    if (!message.sources)
      message.sources = this.bedrockService.getAgentSources(message.agentName as string);
    // Create distinct sources based on file URI and content across all agents
    const distinctSources = new Map<string, KnowledgeBaseSource>();
    if (!message.sources || message.sources.length == 0) return 0;
    (message.sources as Array<any>).forEach(
      source => {
        const uri = source.location?.s3Location?.uri || '';
        const content = source.content?.text || '';

        // Create a unique key combining URI and content hash
        const contentHash = this.createContentHash(content);
        const uniqueKey = `${uri}:${contentHash}`;

        // Only add if we haven't seen this exact combination before
        if (!distinctSources.has(uniqueKey)) {
          distinctSources.set(uniqueKey, source);
        }
      });

    return distinctSources.size;
  }

  // Get detailed source information for button display
  getSourceSummary(message: Message): { totalResults: number; distinctSources: number; distinctFiles: number; buttonText: string; agentCount: number } {
    if (!message.sources || Object.keys(message.sources).length === 0) {
      message.sources = this.bedrockService.getAgentSources(message.agentName as string);
    }

    let totalResults = 0;
    const distinctSources = new Map<string, KnowledgeBaseSource>();
    const distinctFiles = new Set<string>();
    const agentCount = Object.keys(message.sources).length;

    // Iterate through all agents' sources
    Object.values(message.sources).forEach(source => {
      if (source.citations && source.citations.length > 0 && source.citations.filter((citation: any) => citation.retrievedReferences.length > 0).length > 0) {
        const uri = source.location?.s3Location?.uri || '';
        const content = source.content?.text || '';

        // Track distinct files
        if (uri) {
          distinctFiles.add(uri);
        }

        // Create a unique key combining URI and content hash
        const contentHash = this.createContentHash(content);
        const uniqueKey = `${uri}:${contentHash}`;

        // Only add if we haven't seen this exact combination before
        if (!distinctSources.has(uniqueKey)) {
          distinctSources.set(uniqueKey, source);
        }
      }
    });

    const distinctSourceCount = distinctSources.size;
    const distinctFileCount = distinctFiles.size;

    // Create descriptive button text
    let buttonText = '';

    if (agentCount === 1) {
      // Single agent
      if (totalResults === distinctSourceCount) {
        // No duplicates
        if (distinctFileCount === 1) {
          buttonText = `${totalResults} result${totalResults > 1 ? 's' : ''} from 1 file`;
        } else {
          buttonText = `${totalResults} result${totalResults > 1 ? 's' : ''} from ${distinctFileCount} files`;
        }
      } else {
        // Has duplicates - show both counts
        if (distinctFileCount === 1) {
          buttonText = `${totalResults} results (${distinctSourceCount} distinct) from 1 file`;
        } else {
          buttonText = `${totalResults} results (${distinctSourceCount} distinct) from ${distinctFileCount} files`;
        }
      }
    } else {
      // Multiple agents
      buttonText = `${totalResults} results from ${agentCount} agents (${distinctFileCount} files)`;
    }

    return {
      totalResults,
      distinctSources: distinctSourceCount,
      distinctFiles: distinctFileCount,
      buttonText,
      agentCount
    };
  }

  // Helper method to create a simple hash of content for deduplication
  private createContentHash(content: string): string {
    // Create a more robust hash using the entire content
    const normalized = content.replace(/\s+/g, ' ').trim().toLowerCase();

    // Simple hash function (djb2 algorithm)
    let hash = 5381;
    for (let i = 0; i < normalized.length; i++) {
      hash = ((hash << 5) + hash) + normalized.charCodeAt(i);
    }

    // Convert to positive number and return as string
    return Math.abs(hash).toString(36);
  }


  // Helper method to parse CSV content into rows
  private parseCsvContent(csvContent: string, expectedColumns?: number, headers?: string[]): string[][] {
    if (!csvContent) return [];

    try {
      if (headers) {
        csvContent = csvContent.replace(headers?.join(',') + ' ', '');
      }
      console.log('content to be split:', csvContent)
      // Handle different line ending formats
      let lines: Array<Array<string>> = new Array<Array<string>>();
      let sets = 1;
      let parts = this.parseCSVLine(csvContent);

      if (headers) {
        sets = parts.length / headers.length;
        for (let i = 0; i < sets; i++) {
          let nextLineParts = parts.slice(0, headers.length);
          let lastValueBeforeSpaceLineDelimiter = nextLineParts[nextLineParts.length - 1].substring(0, nextLineParts[nextLineParts.length - 1].lastIndexOf(' ')); //this will work most of the time. Splits by the first space found in the last value of what is believed to be one row of values. It's the most reliable way to split lines with this retrieval output.
          let trailingValue = nextLineParts[nextLineParts.length - 1].substring(nextLineParts[nextLineParts.length - 1].indexOf(' ') + 1);
          nextLineParts[nextLineParts.length - 1] = lastValueBeforeSpaceLineDelimiter;
          lines.push(nextLineParts);
          if (headers && parts.length > headers.length) {
            parts = parts.slice(headers?.length);
            parts[0] = trailingValue + parts[0];
          }
        }
      }
      // if (lines.length === 1) {
      //   // Handle case where csvContent is a single long string without newlines
      //   // Split by comma and group into rows based on expectedColumns
      //   if (expectedColumns && expectedColumns > 0) {
      //     const allValues = this.parseCSVLine(csvContent);
      //     const reconstructedLines: string[] = [];
      //     console.log(`${allValues.length} total values and ${expectedColumns} expected colums`)
      //     // Skip the first expectedColumns values (header row) and process the rest
      //     for (let i = expectedColumns + 1; i < allValues.length; i += expectedColumns) {
      //       const rowValues = allValues.slice(i, i + expectedColumns);
      //       console.log(rowValues)
      //       // Pad with empty strings if the last row is incomplete
      //       while (rowValues.length < expectedColumns) {
      //         rowValues.push(' ');
      //       }
      //       // Properly quote values that contain commas when reconstructing
      //       const quotedValues = rowValues.map(value =>
      //         value.includes(',') ? `"${value}"` : value
      //       );
      //       reconstructedLines.push(quotedValues.join(','));
      //     }

      //     // Update lines array with reconstructed rows
      //     lines.push(...reconstructedLines);
      //     console.log(`Reconstructed ${lines.length} CSV rows from single-line content with ${expectedColumns} columns per row`);
      //   }
      // }
      if (lines.length === 0) return [];

      const rows: string[][] = [];

      for (const cells of lines) {
        // If we have an expected column count, pad or truncate as needed
        if (expectedColumns && expectedColumns > 0) {
          while (cells.length < expectedColumns) {
            cells.push('');
          }
          if (cells.length > expectedColumns) {
            cells.splice(expectedColumns);
          }
        }

        rows.push(cells);
      }

      console.log(`Parsed ${rows.length} CSV rows with ${rows[0]?.length || 0} columns`);
      return rows;
    } catch (error) {
      console.error('❌ Error parsing CSV content:', error);
      return [];
    }
  }

  private parseCSVLine(line: string): string[] {
    const result: string[] = [];
    let current = '';
    let inQuotes = false;
    let i = 0;

    while (i < line.length) {
      const char = line[i];

      if (char === '"') {
        if (inQuotes && i + 1 < line.length && line[i + 1] === '"') {
          // Handle escaped quotes ("")
          current += '"';
          i += 2;
        } else {
          // Toggle quote state
          inQuotes = !inQuotes;
          i++;
        }
      } else if (char === ',' && !inQuotes) {
        // End of field
        result.push(current.trim());
        current = '';
        i++;
      } else {
        current += char;
        i++;
      }
    }

    // Add the last field
    result.push(current.trim());

    // Clean up quotes from field values
    return result.map(field => field.replace(/^"(.*)"$/, '$1'));
  }

  getSourceFileName(source: any): string {
    // Try to get filename from citations -> retrievedReferences -> location
    if (source.citations && source.citations.length > 0) {
      const firstCitation = source.citations[0];
      if (firstCitation.retrievedReferences && firstCitation.retrievedReferences.length > 0) {
        const firstRef = firstCitation.retrievedReferences[0];
        if (firstRef.location?.s3Location?.uri) {
          const uri = firstRef.location.s3Location.uri;
          const parts = uri.split('/');
          return parts[parts.length - 1] || 'Unknown file';
        }
        // Try metadata source URI
        if (firstRef.metadata?.['x-amz-bedrock-kb-source-uri']) {
          const uri = firstRef.metadata['x-amz-bedrock-kb-source-uri'];
          const parts = uri.split('/');
          return parts[parts.length - 1] || 'Unknown file';
        }
      }
    }

    // Fallback to old structure for backward compatibility
    if (source.location?.s3Location?.uri) {
      const uri = source.location.s3Location.uri;
      const parts = uri.split('/');
      return parts[parts.length - 1] || 'Unknown file';
    }

    return 'Knowledge Base Source';
  }

  getSourcePreview(source: KnowledgeBaseSource): string {
    // CRITICAL: Check if this source has a formatted_summary from retrieve_and_generate
    // This is the nicely formatted content that Bedrock generated, not the raw chunks
    if ((source as any).formatted_summary) {
      const summary = (source as any).formatted_summary;
      // Return a preview of the formatted summary (first 200 chars)
      return summary.length > 200 ? summary.substring(0, 200) + '...' : summary;
    }

    // Fallback to raw content if no formatted summary exists
    const text = typeof source.content === 'string'
      ? source.content
      : source.content?.text || '';

    // Get content type from source or default to text/plain
    const type = typeof source.content === 'object' && source.content?.type
      ? source.content.type
      : 'text/plain';

    // Use the optimized compressed preview method
    return this.getCompressedSourcePreview({ ...source, content: { text, type } }, 200);
  }

  // REMOVED: isJsonFile and isCsvFile - no longer needed with retrieve_and_generate
  // Bedrock now handles all file type formatting automatically

  getFormattedJson(source: KnowledgeBaseSource, maxLength: number = 5000): string {
    try {
      // Extract text content from source (handle both string and object formats)
      const jsonText = typeof source.content === 'string'
        ? source.content
        : source.content?.text || '';

      const sourceKey = source.location?.s3Location?.uri || `source-${Date.now()}`;

      // Use cached version if available
      const cachedKey = `${sourceKey}-json-${maxLength}`;
      if (this.compressedSources.has(cachedKey)) {
        return this.compressedSources.get(cachedKey)!;
      }

      // For large JSON, truncate before parsing for memory efficiency
      const truncatedText = jsonText.length > maxLength ? jsonText.substring(0, maxLength) + '...' : jsonText;

      // Use partial JSON parser to handle potentially incomplete JSON from knowledge base
      const parseResult = this.parsePartialJson(truncatedText.endsWith('...') ? jsonText : truncatedText);

      if (parseResult.success && parseResult.data) {
        try {
          const formatted = JSON.stringify(parseResult.data, null, 2);

          // Add indicator if this was parsed as partial JSON
          const partialIndicator = parseResult.isPartial ? '\n\n// Note: This JSON was parsed from incomplete data\n' : '';
          const finalResult = (formatted + partialIndicator).length > maxLength ?
            (formatted + partialIndicator).substring(0, maxLength) + '...' :
            (formatted + partialIndicator);

          // Cache the result
          this.compressedSources.set(cachedKey, finalResult);

          return finalResult;
        } catch (stringifyError) {
          console.warn('❌ Failed to stringify parsed JSON:', stringifyError);
          // Try fallback key-value extraction
          const keyValueResult = this.extractKeyValuePairs(truncatedText);
          this.compressedSources.set(cachedKey, keyValueResult);
          return keyValueResult;
        }
      } else {
        console.warn('❌ Failed to parse JSON from knowledge base source:', parseResult.error);
        // Try fallback key-value extraction before giving up
        const keyValueResult = this.extractKeyValuePairs(truncatedText);
        this.compressedSources.set(cachedKey, keyValueResult);
        return keyValueResult;
      }
    } catch (error) {
      console.error('❌ Error in getFormattedJson:', error);
      // Final fallback to key-value extraction
      const jsonText = typeof source.content === 'string'
        ? source.content
        : source.content?.text || '';
      const truncatedText = jsonText.length > maxLength ? jsonText.substring(0, maxLength) + '...' : jsonText;
      return this.extractKeyValuePairs(truncatedText);
    }
  }

  isSourceExpanded(sourceIndex: number): boolean {
    return this.sourceExpanded.get(`source-${sourceIndex}`) || false;
  }

  toggleSourceExpanded(sourceIndex: number): void {
    const key = `source-${sourceIndex}`;
    const currentState = this.sourceExpanded.get(key) || false;
    this.sourceExpanded.set(key, !currentState);
    this.changeDetectorRef.markForCheck(); // OnPush optimization
  }

  // Compress large text content for memory efficiency
  getCompressedSourcePreview(source: KnowledgeBaseSource, maxLength: number = 500): string {
    // Extract text content (handle both string and object formats)
    const text = typeof source.content === 'string'
      ? source.content
      : source.content?.text || '';

    const sourceKey = source.location?.s3Location?.uri || `source-${Date.now()}`;

    // Use cached compressed version if available
    const cachedKey = `${sourceKey}-${maxLength}`;
    if (this.compressedSources.has(cachedKey)) {
      return this.compressedSources.get(cachedKey)!;
    }

    // Create compressed preview
    const compressed = text.length > maxLength ? text.substring(0, maxLength) + '...' : text;
    this.compressedSources.set(cachedKey, compressed);

    return compressed;
  }

  // Get messages sorted by timestamp for display
  get sortedMessages(): Message[] {
    return [...this.messages].sort((a, b) => a.timestamp.getTime() - b.timestamp.getTime());
  }

  // TrackBy functions for performance optimization
  trackBySourceIndex = (index: number, item: KnowledgeBaseSource): string => {
    return item.location?.s3Location?.uri || `source-${index}`;
  }

  trackByMessageId = (index: number, item: Message): string => {
    return item.id;
  }

  ngOnDestroy() {
    this.destroyed = true;

    // Clear all active timeouts
    this.activeTimeouts.forEach(timeoutId => clearTimeout(timeoutId));
    this.activeTimeouts.clear();

    // Clear specialist invocation timeout checker
    if (this.specialistTimeoutCheckInterval) {
      clearInterval(this.specialistTimeoutCheckInterval);
      this.specialistTimeoutCheckInterval = null;
    }
    this.pendingSpecialistInvocations.clear();

    // Clear summary retry timeouts
    this.clearSummaryRetryTimeouts();

    // Unsubscribe from all active subscriptions
    this.activeSubscriptions.forEach(subscription => {
      if (subscription && typeof subscription.unsubscribe === 'function') {
        subscription.unsubscribe();
      }
    });
    this.activeSubscriptions.clear();

    // Stop voice recording if active
    if (this.isRecording) {
      this.stopVoiceRecording();
    }

    // Clear transcription subscription
    if (this.transcriptionSubscription) {
      this.transcriptionSubscription.unsubscribe();
      this.transcriptionSubscription = null;
    }

    // Cleanup BedrockService timers
    this.bedrockService.cleanup();

    // Clear caches
    this.messageCache.clear();
    this.knowledgeBaseSources.clear();
    this.traceQueries.clear();
    this.requestSources.clear();
    this.requestToMessageMap.clear();
    this.recentMessageHashes.clear();
    this.referencesExpanded.clear();
    this.thinkingCollapsed.clear();
    this.agentParticipants.clear();
    this.currentAgentResponses.clear();
    this.agentFirstMessages.clear();

    // Clear memory optimization caches
    this.csvPagination.clear();
    this.sourceExpanded.clear();
    this.compressedSources.clear();

    // Cleanup Intersection Observer for companion panel
    if (this.messageIntersectionObserver) {
      this.messageIntersectionObserver.disconnect();
      this.messageIntersectionObserver = null;
    }
    this.observedMessageElements.clear();
  }

  // Helper method to create tracked timeouts
  private safeSetTimeout(callback: () => void, delay: number): void {
    if (this.destroyed) return;

    const timeoutId = setTimeout(() => {
      this.activeTimeouts.delete(timeoutId);
      if (!this.destroyed) {
        callback();
      }
    }, delay);

    this.activeTimeouts.add(timeoutId);
  }

  // PDF Export Methods

  /**
   * Show the PDF export options modal
   */
  showPdfExportOptions(): void {
    if (this.messages.length === 0) {
      console.warn('No messages to export');
      return;
    }

    // Set default title based on current context
    this.exportOptions.title = this.generateExportTitle();
    this.exportOptions.subtitle = `Generated on ${new Date().toLocaleDateString()} • ${this.messages.length} messages`;

    this.showExportOptions = true;
  }

  // Visibility Settings Methods

  /**
   * Show the visibility settings modal
   */
  showVisibilitySettingsModal(): void {
    this.showVisibilitySettings = true;
  }
  getAgentColor(agentType: string = ''): string {
    // Normalize the agent name to ensure consistent color assignment
    // Use the normalized agent name for color calculation
    const color = this.agentConfig.getEnrichedAgents().find(agent => agent.name == agentType)?.color || "gray";

    // Debug logging for color consistency (only log if names differ)
    //if (normalizedAgentName !== agentType) {
    //console.log(`🎨 Agent color normalization: "${agentType}" -> "${normalizedAgentName}" = ${color}`);
    //}

    return color;
  }

  getAgentColorFromScenario(scenario): string {
    // Normalize the agent name to ensure consistent color assignment
    // Use the normalized agent name for color calculation
    return scenario.agent ? scenario.agent.color : 'gray';
  }
  /**
   * Handle visibility settings changes
   */
  onVisibilitySettingsChanged(settings: VisibilitySettings): void {
    this.visibilitySettings = settings;

    // Update the legacy hiddenTypes array for backward compatibility
    this.hiddenTypes = [...settings.hiddenMessageTypes];

    // Force change detection to update the UI
    this.changeDetectorRef.markForCheck();
  }

  /**
   * Close the visibility settings modal
   */
  onVisibilitySettingsClosed(): void {
    this.showVisibilitySettings = false;
  }


  /**
   * Show sources for a specific agent
   */
  showAgentSources(agentName: string): void {
    const agentSources = this.bedrockService.getAgentSources(agentName);
    if (agentSources && agentSources.length > 0) {
      // Set up the sources modal with agent-specific data
      this.currentSources = agentSources;
      this.currentSourceQueries = this.extractQueriesFromSources(agentSources);
      let sources = {}
      sources[agentName] = agentSources.filter(source => source.output.text != "Sorry, I am unable to assist you with this request.")
      // Wrap sources in object with agent name as key to match new structure
      this.showSources({ id: '', text: '', sender: 'agent', timestamp: new Date(), sources: sources }, agentName);
      this.showSourcesModal = true;
    }
  }

  /**
   * Extract unique queries from sources
   */
  private extractQueriesFromSources(sources: any[]): string[] {
    const queries = new Set<string>();
    sources.forEach(source => {
      if (source.query) {
        queries.add(source.query);
      }
    });
    return Array.from(queries);
  }

  /**
   * Check if an agent is hidden
   */
  isAgentHidden(agentName: string): boolean {
    return this.visibilitySettings.hiddenAgents.indexOf(agentName) > -1;
  }

  /**
   * Toggle visibility of an agent's messages
   */
  toggleAgentVisibility(agentName: string): void {
    let index = this.visibilitySettings.hiddenAgents.indexOf(agentName);
    if (index > -1) {
      // Agent is currently hidden, show it
      this.visibilitySettings.hiddenAgents.splice(index, 1);
    } else {
      // Agent is currently visible, hide it
      this.visibilitySettings.hiddenAgents.push(agentName);
    }

    // Force change detection to update the UI
    this.changeDetectorRef.markForCheck();
  }


  /**
   * Hide the PDF export options modal
   */
  hidePdfExportOptions(): void {
    this.showExportOptions = false;
  }

  /**
   * Export the chat conversation to PDF
   */
  async exportChatToPdf(): Promise<void> {
    if (this.isExportingPdf || this.messages.length === 0) {
      return;
    }

    try {
      this.isExportingPdf = true;
      this.showExportOptions = false;

      // Get the messages container element
      const messagesContainer = this.messagesContainer?.nativeElement;
      if (!messagesContainer) {
        throw new Error('Messages container not found');
      }

      // Check if this is a large conversation
      const messageCount = this.messages.filter(m => m.sender === 'agent' || m.sender === 'user').length;
      const isLarge = this.pdfExportService.isLargeConversation(messageCount);

      if (isLarge) {
        // Show size warning and get user confirmation
        const sizeEstimate = this.pdfExportService.estimatePdfSize(messageCount);
        const confirmed = confirm(
          `This conversation has ${messageCount} messages and will generate approximately ${sizeEstimate.pages} pages (${sizeEstimate.sizeMB.toFixed(1)}MB). Continue with export?`
        );

        if (!confirmed) {
          this.isExportingPdf = false;
          return;
        }

        // Use optimized export for large conversations
        await this.pdfExportService.exportLargeChatToPdf(
          messagesContainer,
          this.exportOptions,
          this.generateFilename()
        );
      } else {
        // Use standard export for smaller conversations
        await this.pdfExportService.exportChatToPdf(
          messagesContainer,
          this.exportOptions,
          this.generateFilename()
        );
      }

      console.log('✅ Chat exported to PDF successfully');

    } catch (error) {
      console.error('❌ PDF export failed:', error);
      alert(`PDF export failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    } finally {
      this.isExportingPdf = false;
    }
  }

  /**
   * Generate a descriptive title for the export
   */
  private generateExportTitle(): string {
    // Try to determine the main topic from agent participants
    const participants = this.getActiveAgentParticipants();

    if (participants.length === 0) {
      return 'AgentCore Agents Conversation';
    }

    if (participants.length === 1) {
      return `${participants[0].displayName} Consultation`;
    }

    // Multiple agents - create a descriptive title
    const agentTypes = participants.map(p => p.displayName.replace(' Agent', '')).slice(0, 3);

    if (agentTypes.some(type => type.toLowerCase().includes('bid'))) {
      return 'Bidding Strategy Consultation';
    } else if (agentTypes.some(type => type.toLowerCase().includes('media'))) {
      return 'Media Planning Session';
    } else if (agentTypes.some(type => type.toLowerCase().includes('campaign'))) {
      return 'Campaign Optimization Session';
    } else {
      return `Multi-Agent Consultation (${agentTypes.join(', ')})`;
    }
  }

  /**
   * Generate filename for the PDF export
   */
  private generateFilename(): string {
    const timestamp = new Date().toISOString().slice(0, 10); // YYYY-MM-DD
    const title = (this.exportOptions.title || 'chat-export')
      .replace(/[^a-zA-Z0-9\s]/g, '')
      .replace(/\s+/g, '-')
      .toLowerCase();

    return `${title}-${timestamp}.pdf`;
  }

  /**
   * Get export statistics for display
   */
  getExportStats(): {
    totalMessages: number;
    userMessages: number;
    agentMessages: number;
    agentCount: number;
    estimatedPages: number;
    estimatedSize: string;
  } {
    const totalMessages = this.messages.length;
    const userMessages = this.messages.filter(m => m.sender === 'user').length;
    const agentMessages = this.messages.filter(m => m.sender === 'agent').filter(m => m.type == "text").length;
    const agentCount = this.getActiveAgentParticipants().length;

    const estimate = this.pdfExportService.estimatePdfSize(totalMessages);

    return {
      totalMessages,
      userMessages,
      agentMessages,
      agentCount,
      estimatedPages: estimate.pages,
      estimatedSize: `${estimate.sizeMB.toFixed(1)} MB`
    };
  }

  /**
   * Toggle export option
   */
  toggleExportOption(option: keyof ChatExportOptions): void {
    if (typeof this.exportOptions[option] === 'boolean') {
      (this.exportOptions[option] as boolean) = !(this.exportOptions[option] as boolean);
    }
  }

  hasAgentSources(agentName) {
    return this.bedrockService.hasAgentSources(agentName)
  }
  /**
   * Update export option value
   */
  updateExportOption(option: keyof ChatExportOptions, value: any): void {
    (this.exportOptions as any)[option] = value;
  }

  // ENHANCED: Detect embedded visualization data in AgentCore responses
  private detectEmbeddedVisualizations(messageText: string): any[] {
    const visualizations: any[] = [];

    if (!messageText || typeof messageText !== 'string') {
      return visualizations;
    }

    // Enhanced JSON detection patterns - look for objects with visualization indicators
    const jsonPatterns = [
      // Pattern 1: Standard JSON objects with visualizationType
      /\{\s*[^}]*"visualizationType"\s*:\s*"([^"]+)"[\s\S]*?\}/g,

      // Pattern 2: JSON objects with templateId (even without visualizationType)
      /\{\s*[^}]*"templateId"\s*:\s*"([^"]+)"[\s\S]*?\}/g,

      // Pattern 3: Objects with common visualization properties
      /\{\s*[^}]*"(metrics|segments|timeline|phases|allocations|channels|creatives)"\s*:\s*\[[\s\S]*?\}/g,

      // Pattern 4: Objects with title and data arrays (common visualization pattern)
      /\{\s*[^}]*"title"\s*:\s*"[^"]*"[^}]*"(metrics|segments|phases|allocations|channels|creatives)"\s*:\s*\[[\s\S]*?\}/g
    ];

    for (const pattern of jsonPatterns) {
      let match;
      while ((match = pattern.exec(messageText)) !== null) {
        const jsonText = match[0];

        try {
          // Try to parse the JSON
          let parsedJson = this.parseVisualizationJson(jsonText);

          if (parsedJson && this.isValidVisualizationData(parsedJson)) {
            // Normalize the visualization type
            parsedJson = this.normalizeVisualizationType(parsedJson);

            // Avoid duplicates
            if (!visualizations.some(v => this.areVisualizationsEqual(v, parsedJson))) {
              visualizations.push(parsedJson);
              console.log('✅ Detected embedded visualization:', parsedJson.visualizationType, parsedJson.title);
            }
          }
        } catch (error) {
          // Try partial JSON parsing for incomplete objects
          try {
            const partialResult = this.parsePartialJson(jsonText);
            if (partialResult.success && this.isValidVisualizationData(partialResult.data)) {
              let normalizedData = this.normalizeVisualizationType(partialResult.data);
              if (!visualizations.some(v => this.areVisualizationsEqual(v, normalizedData))) {
                visualizations.push(normalizedData);
                console.log('✅ Detected partial embedded visualization:', normalizedData.visualizationType, normalizedData.title);
              }
            }
          } catch (partialError) {
            console.warn('❌ Failed to parse potential visualization JSON:', jsonText.substring(0, 100) + '...');
          }
        }
      }
    }

    return visualizations;
  }

  // Parse JSON with better error handling for visualization data
  private parseVisualizationJson(jsonText: string): any {
    try {
      // Clean up the JSON text
      let cleanedJson = jsonText.trim();

      // Handle common formatting issues
      cleanedJson = cleanedJson
        .replace(/,\s*}/g, '}')  // Remove trailing commas
        .replace(/,\s*]/g, ']')  // Remove trailing commas in arrays
        .replace(/([{,]\s*)(\w+):/g, '$1"$2":')  // Quote unquoted keys
        .replace(/:\s*'([^']*)'/g, ': "$1"');  // Convert single quotes to double quotes

      return JSON.parse(cleanedJson);
    } catch (error) {
      // If standard parsing fails, try with partial-json library
      return partialJson.parse(jsonText);
    }
  }

  // Check if parsed data represents valid visualization data
  private isValidVisualizationData(data: any): boolean {
    if (!data || typeof data !== 'object') {
      return false;
    }

    // Check for explicit visualization type
    if (data.visualizationType) {
      return true;
    }

    // Check for templateId that suggests visualization
    if (data.templateId && (
      data.templateId.includes('visualization') ||
      data.templateId.includes('metrics') ||
      data.templateId.includes('timeline') ||
      data.templateId.includes('segments') ||
      data.templateId.includes('allocations') ||
      data.templateId.includes('channels') ||
      data.templateId.includes('creative')
    )) {
      return true;
    }

    // Check for common visualization data structures
    const hasVisualizationData = (
      data.metrics && Array.isArray(data.metrics) ||
      data.segments && Array.isArray(data.segments) ||
      data.phases && Array.isArray(data.phases) ||
      data.allocations && Array.isArray(data.allocations) ||
      data.channels && Array.isArray(data.channels) ||
      data.creatives && Array.isArray(data.creatives) ||
      data.timeline && Array.isArray(data.timeline)
    );

    // Must have title and visualization data
    return data.title && hasVisualizationData;
  }

  // Normalize visualization type from various formats
  private normalizeVisualizationType(data: any): any {
    if (!data) return data;

    // If already has visualizationType, ensure it's clean
    if (data.visualizationType) {
      data.visualizationType = data.visualizationType.replace('-visualization', '');
      return data;
    }

    // Extract from templateId
    if (data.templateId) {
      const templateId = data.templateId.toLowerCase();

      if (templateId.includes('metrics')) {
        data.visualizationType = 'metrics';
      } else if (templateId.includes('timeline')) {
        data.visualizationType = 'timeline';
      } else if (templateId.includes('segments')) {
        data.visualizationType = 'segments';
      } else if (templateId.includes('allocations')) {
        data.visualizationType = 'allocations';
      } else if (templateId.includes('channels')) {
        data.visualizationType = 'channels';
      } else if (templateId.includes('creative')) {
        data.visualizationType = 'creative';
      } else {
        // Extract the base name from templateId
        data.visualizationType = templateId.replace('-visualization', '').replace('_', '-');
      }

      return data;
    }

    // Infer from data structure
    if (data.metrics && Array.isArray(data.metrics)) {
      data.visualizationType = 'metrics';
    } else if (data.segments && Array.isArray(data.segments)) {
      data.visualizationType = 'segments';
    } else if (data.phases && Array.isArray(data.phases)) {
      data.visualizationType = 'timeline';
    } else if (data.allocations && Array.isArray(data.allocations)) {
      data.visualizationType = 'allocations';
    } else if (data.channels && Array.isArray(data.channels)) {
      data.visualizationType = 'channels';
    } else if (data.creatives && Array.isArray(data.creatives)) {
      data.visualizationType = 'creative';
    } else {
      // Default fallback
      data.visualizationType = 'metrics';
    }

    return data;
  }

  // Check if two visualizations are essentially the same (to avoid duplicates)
  private areVisualizationsEqual(viz1: any, viz2: any): boolean {
    if (!viz1 || !viz2) return false;

    return viz1.visualizationType === viz2.visualizationType &&
      viz1.title === viz2.title &&
      JSON.stringify(viz1).length === JSON.stringify(viz2).length;
  }

  // Generate appropriate message text for detected visualizations
  private generateVisualizationMessageText(vizData: any): string {
    const visualizationType = vizData.visualizationType;
    const title = vizData.title || 'Analysis';

    switch (visualizationType) {
      case 'creative':
        return `Generated ${vizData.creatives?.length || 0} creative assets for ${title.toLowerCase()}.`;
      case 'timeline':
        return `Here's the timeline for ${title.toLowerCase()}`;
      case 'decision-tree':
        return `Here's the decision analysis for ${title.toLowerCase()}`;
      case 'metrics':
        return `Here's the performance analysis for ${title.toLowerCase()}`;
      case 'allocations':
        return `Here's the allocation analysis for ${title.toLowerCase()}`;
      case 'channels':
        return `Here's the channel analysis for ${title.toLowerCase()}`;
      case 'segments':
        return `Here's the segment analysis for ${title.toLowerCase()}`;
      default:
        return `Here's the ${visualizationType} analysis for ${title.toLowerCase()}`;
    }
  }

  // Remove visualization JSON from the original message text
  private removeVisualizationJsonFromText(messageText: string, visualizations: any[]): string {
    let cleanedText = messageText;

    // For each detected visualization, try to remove its JSON from the text
    visualizations.forEach(viz => {
      const vizJson = JSON.stringify(viz);

      // Try to find and remove the original JSON pattern
      const jsonPatterns = [
        new RegExp(this.escapeRegExp(vizJson), 'g'),
        /\{\s*[^}]*"visualizationType"\s*:\s*"[^"]+"\s*[\s\S]*?\}/g,
        /\{\s*[^}]*"templateId"\s*:\s*"[^"]+"\s*[\s\S]*?\}/g
      ];

      jsonPatterns.forEach(pattern => {
        cleanedText = cleanedText.replace(pattern, '');
      });
    });

    // Clean up any remaining artifacts
    cleanedText = cleanedText
      .replace(/\n\s*\n\s*\n/g, '\n\n')  // Remove excessive line breaks
      .replace(/^\s*\n+/, '')  // Remove leading whitespace/newlines
      .replace(/\n+\s*$/, '')  // Remove trailing whitespace/newlines
      .trim();

    return cleanedText;
  }

  // Helper to escape special regex characters
  private escapeRegExp(string: string): string {
    return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  // Test method for embedded visualization detection
  testEmbeddedVisualizationDetection(sampleText?: string): any[] {
    console.log('🧪 Testing embedded visualization detection...');

    const testText = sampleText || `Here's the analysis you requested:

{ "visualizationType": "metrics", "title": "Weather Impact Analysis for London", "metrics": [ { "label": "Indoor Media Consumption", "value": "+28%", "unit": "%", "trend": "up", "target": "15%", "confidence": "high", "description": "Cool temperatures driving increased indoor media usage" }, { "label": "Mobile App Engagement", "value": "+32%", "unit": "%", "trend": "up", "target": "20%", "confidence": "high", "description": "Cold mornings increasing commute-time app usage" } ] }

{ "visualizationType": "segments", "title": "Weather-Responsive Audience Segments in London", "segments": [ { "name": "Indoor Entertainment Seekers", "size": 38, "behavior": "Significantly increase streaming and digital content consumption during cooler weather" }, { "name": "Weather-Neutral Commuters", "size": 25, "behavior": "Consistent media consumption patterns regardless of weather conditions" } ] }

This analysis shows the impact of weather on audience behavior.`;

    const detectedVisualizations = this.detectEmbeddedVisualizations(testText);

    console.log(`✅ Detected ${detectedVisualizations.length} visualizations:`);
    detectedVisualizations.forEach((viz, index) => {
      console.log(`  ${index + 1}. Type: ${viz.visualizationType}, Title: ${viz.title}`);
      console.log(`     Data keys: ${Object.keys(viz).filter(k => k !== 'visualizationType' && k !== 'title').join(', ')}`);
    });

    const cleanedText = this.removeVisualizationJsonFromText(testText, detectedVisualizations);
    console.log('🧹 Cleaned text:', cleanedText);

    return detectedVisualizations;
  }

  // Sources modal methods
  showSources(message: Message, agent?: string): void {
    if (!message.sources) return;

    // Deduplicate sources based on query + output text combination
    const uniqueSources: any[] = [];
    const seen = new Set<string>();

    // Determine which agents' sources to show
    const agentsToShow = agent ? [agent] : Object.keys(message.sources);

    // Process sources from specified agent(s)
    for (const agentName of agentsToShow) {
      const agentSources = message.sources[agentName];
      if (!agentSources || agentSources.length === 0) continue;

      for (const source of agentSources) {
        // Create a unique key based on query and response text
        const query = source.query || '';
        const responseText = source.output?.text || '';
        const uniqueKey = `${query}:${responseText.substring(0, 100)}`;

        // Skip if we've already seen this exact source
        if (seen.has(uniqueKey)) {
          continue;
        }

        seen.add(uniqueKey);
        uniqueSources.push(source);
      }
    }

    // Only show modal if we have sources
    if (uniqueSources.length > 0) {
      // Collect unique search queries
      const uniqueQueries = new Set<string>();
      for (const source of uniqueSources) {
        const query = source.query;
        if (query && query.trim()) {
          uniqueQueries.add(query.trim());
        }
      }

      this.currentSources = uniqueSources;
      this.currentSourceQueries = Array.from(uniqueQueries);
      this.showSourcesModal = true;
    }
  }

  closeSources(): void {
    this.showSourcesModal = false;
    this.currentSources = [];
    this.currentSourceQueries = [];
  }

  // Helper to count unique references in a source
  getUniqueReferencesCount(source: any): number {
    if (!source.citations || source.citations.length === 0) {
      return 0;
    }

    const uniqueRefs = new Set<string>();
    for (const citation of source.citations) {
      if (citation.retrievedReferences) {
        for (const ref of citation.retrievedReferences) {
          const uri = ref.location?.s3Location?.uri || ref.metadata?.['x-amz-bedrock-kb-source-uri'] || '';
          const content = ref.content?.text || '';
          const key = `${uri}:${content.substring(0, 50)}`;
          uniqueRefs.add(key);
        }
      }
    }

    return uniqueRefs.size;
  }

  // Session management methods
  loadAvailableSessions(): void {
    const loginId = this.currentUser?.signInDetails?.loginId;
    let customerName: string = this.demoTrackingService.getCurrentCustomer() ? this.demoTrackingService.getCurrentCustomer() as string : 'default';
    const tabId = this.contextData?.tabContext?.id || this.contextData?.tabId || this.tabConfig?.id;
    this.availableSessions = this.sessionManager.getTabSessions(loginId, customerName, tabId);
  }

  toggleSessionsPanel(): void {
    this.showSessionsPanel = !this.showSessionsPanel;
    if (this.showSessionsPanel) {
      this.loadAvailableSessions();
      // Close other panels
      this.showScenariosPanel = false;
    }
  }

  closeSessionsPanel(): void {
    this.showSessionsPanel = false;
  }

  // Track if session creation is in progress to prevent duplicate calls
  private isCreatingSession = false;

  createNewSession(): void {
    // Prevent duplicate session creation within the same component instance
    if (this.isCreatingSession) {
      console.log('⚠️ Session creation already in progress, skipping duplicate call');
      return;
    }
    
    // Additional check: verify this tab hasn't already had a session created this page load
    // This catches cases where component is destroyed and recreated
    if (this.sessionManager.hasTabBeenInitialized(this.tabId)) {
      // Check if we already have a current session for this tab
      const loginId = this.currentUser?.signInDetails?.loginId;
      const customerName = this.demoTrackingService.getCurrentCustomer() || 'default';
      const existingSessions = this.sessionManager.getTabSessions(loginId, customerName, this.tabId);
      
      if (existingSessions.length > 0) {
        console.log(`⚠️ Tab ${this.tabId} already initialized with session, using existing: ${existingSessions[0].sessionId}`);
        this.currentSessionInfo = existingSessions[0];
        this.loadAvailableSessions();
        this.changeDetectorRef.markForCheck();
        return;
      }
    }
    
    this.isCreatingSession = true;

    const loginId = this.currentUser?.signInDetails?.loginId;
    let customerName: string = this.demoTrackingService.getCurrentCustomer() ? this.demoTrackingService.getCurrentCustomer() as string : 'default';
    console.log("TAB DATA",this.contextData)

    // Clear current messages
    this.messages = [{
      id: '1',
      text: `This playground shows various applications of Amazon Bedrock AgentCore Agents in the Advertising industry. Select one of the example scenarios or speak or type a question to get started.`,
      sender: 'agent',
      timestamp: new Date(),
      data: {
        isWelcomeMessage: true
      }
    }];
    this.knowledgeBaseSources.clear();
    this.traceQueries.clear();
    this.requestSources.clear();
    this.requestToMessageMap.clear();
    this.currentAgentResponses.clear();
    this.recentMessageHashes.clear();
    this.referencesExpanded.clear();
    this.thinkingCollapsed.clear();
    this.agentParticipants.clear();
    this.agentFirstMessages.clear();
    this.agentConfig.clearAgentColorCache();
    this.shouldScrollToBottom = true;

    // Create new session
    const newSession = this.sessionManager.createNewSession(loginId, customerName, this.tabId);
    this.currentSessionInfo = newSession;
    
    // Mark this tab as initialized to prevent duplicate session creation on re-renders
    this.sessionManager.markTabAsInitialized(this.tabId);

    // Refresh available sessions
    this.loadAvailableSessions();

    // Close panel
    this.closeSessionsPanel();

    // Trigger change detection
    this.changeDetectorRef.markForCheck();

    console.log(`🆕 Created new session: ${newSession.sessionId}`);

    // Reset the flag after a short delay to allow for legitimate new session requests
    this.safeSetTimeout(() => {
      this.isCreatingSession = false;
    }, 500);
  }

  switchToSession(sessionId: string): void {
    const loginId = this.currentUser?.signInDetails?.loginId;
    let customerName: string = this.demoTrackingService.getCurrentCustomer() ? this.demoTrackingService.getCurrentCustomer() as string : 'default';
    const tabId = this.contextData?.tabContext?.id || this.contextData?.tabId || this.tabConfig?.id;

    // Switch to the selected session
    const session = this.sessionManager.switchSession(sessionId, loginId, customerName, tabId);
    if (session) {
      // Clear current messages (they'll be loaded from the new session context)
      this.messages = [];
      this.knowledgeBaseSources.clear();
      this.traceQueries.clear();
      this.requestSources.clear();
      this.requestToMessageMap.clear();
      this.currentAgentResponses.clear();
      this.recentMessageHashes.clear();
      this.referencesExpanded.clear();
      this.thinkingCollapsed.clear();
      this.agentParticipants.clear();
      this.agentFirstMessages.clear();
      this.agentConfig.clearAgentColorCache();
      this.shouldScrollToBottom = true;

      this.currentSessionInfo = session;

      // Close panel
      this.closeSessionsPanel();

      // Trigger change detection
      this.changeDetectorRef.markForCheck();

      console.log(`🔄 Switched to session: ${session.sessionId}`);
    }
  }

  /**
   * Load a specific session (called from parent component)
   */
  loadSession(session: SessionInfo): void {
    this.switchToSession(session.sessionId);
  }

  deleteSession(sessionId: string, event?: Event): void {
    if (event) {
      event.stopPropagation();
    }

    if (!confirm('Are you sure you want to delete this session? This action cannot be undone.')) {
      return;
    }

    const loginId = this.currentUser?.signInDetails?.loginId;
    let customerName: string = this.demoTrackingService.getCurrentCustomer() ? this.demoTrackingService.getCurrentCustomer() as string : 'default';
    const tabId = this.contextData?.tabContext?.id || this.contextData?.tabId || this.tabConfig?.id;

    // Delete the session
    this.sessionManager.deleteSession(sessionId, loginId, customerName, tabId);

    // If we deleted the current session, create a new one
    if (this.currentSessionInfo?.sessionId === sessionId) {
      this.createNewSession();
    } else {
      // Just refresh the available sessions
      this.loadAvailableSessions();
      this.changeDetectorRef.markForCheck();
    }

    console.log(`🗑️ Deleted session: ${sessionId}`);
  }

  formatSessionDate(date: Date): string {
    const now = new Date();
    const sessionDate = new Date(date);
    const diffMs = now.getTime() - sessionDate.getTime();
    const diffHours = diffMs / (1000 * 60 * 60);
    const diffDays = diffMs / (1000 * 60 * 60 * 24);

    if (diffHours < 1) {
      const diffMinutes = Math.floor(diffMs / (1000 * 60));
      return diffMinutes < 1 ? 'Just now' : `${diffMinutes}m ago`;
    } else if (diffHours < 24) {
      return `${Math.floor(diffHours)}h ago`;
    } else if (diffDays < 7) {
      return `${Math.floor(diffDays)}d ago`;
    } else {
      return sessionDate.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        year: sessionDate.getFullYear() !== now.getFullYear() ? 'numeric' : undefined
      });
    }
  }

  getSessionIcon(session: SessionInfo): string {
    const messageCount = session.messageCount || 0;
    if (messageCount === 0) {
      return 'chat_bubble_outline';
    } else if (messageCount < 5) {
      return 'chat_bubble';
    } else if (messageCount < 20) {
      return 'forum';
    } else {
      return 'question_answer';
    }
  }

  isCurrentSession(sessionId: string): boolean {
    return this.currentSessionInfo?.sessionId === sessionId;
  }

  trackBySessionId = (index: number, item: SessionInfo): string => {
    return item.sessionId;
  }

}
