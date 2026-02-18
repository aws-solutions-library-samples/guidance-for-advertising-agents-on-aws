import { Component, Input, Output, EventEmitter, OnInit, OnDestroy, OnChanges, AfterViewInit, ElementRef, ViewChild } from '@angular/core';
import { AgentMentionService } from '../../services/agent-mention.service';
import { AwsConfigService } from '../../services/aws-config.service';
import { AgentConfigService } from 'src/app/services/agent-config.service';
import { TextUtils } from 'src/app/utils/text-utils';
import { AgentSuggestion, EnrichedAgent } from 'src/app/models/application-models';

@Component({
  selector: 'app-agent-mention-typeahead',
  templateUrl: './agent-mention-typeahead.component.html',
  styleUrls: ['./agent-mention-typeahead.component.scss']
})
export class AgentMentionTypeaheadComponent implements OnInit, OnDestroy, OnChanges, AfterViewInit {
  @Input() inputText: string = '';
  @Input() isVisible: boolean = false;
  @Input() position: { top: number; left: number } = { top: 0, left: 0 };
  @Input() availableAgentTypes: string[] = []; // Filtered agent types for this context
  @Output() agentSelected = new EventEmitter<AgentSuggestion>();
  @Output() closeTypeahead = new EventEmitter<void>();

  @ViewChild('typeaheadContainer') typeaheadContainer!: ElementRef;

  suggestions: AgentSuggestion[] = [];
  selectedIndex: number = 0;
  searchText: string = '';

  constructor(
    private agentMentionService: AgentMentionService,
    private awsConfig: AwsConfigService,
    private agentConfig: AgentConfigService
  ) { }

  ngOnInit(): void {
    this.updateSuggestions();
    document.addEventListener('click', this.onDocumentClick.bind(this));
  }

  onKeyDown(event: KeyboardEvent): void {

    switch (event.key) {
      case 'ArrowDown':
        event.preventDefault();
        event.stopPropagation();
        this.navigateDown();
        break;
      case 'ArrowUp':
        event.preventDefault();
        event.stopPropagation();
        this.navigateUp();
        break;
      case 'Enter':
      case 'Tab':
        event.preventDefault();
        this.selectCurrent();
        event.stopPropagation();
        break;
      case 'Escape':
        event.preventDefault();
        event.stopPropagation();
        this.close();
        break;
      default:
        // Let other keys pass through
        return;
    }

  }

  ngAfterViewInit(): void {
    // Set up keyboard event listeners after view is initialized
    if (this.typeaheadContainer) {
      const element = this.typeaheadContainer.nativeElement;

      // Only use keydown events to avoid duplicate triggers
      element.addEventListener('keydown', (event: KeyboardEvent) => {
        this.onKeyDown(event);
      });

      // Handle custom events as fallback for parent component control
      element.addEventListener('navigateDown', () => {
        this.navigateDown();
      });

      element.addEventListener('navigateUp', () => {
        this.navigateUp();
      });

      element.addEventListener('selectCurrent', () => {
        this.selectCurrent();
      });

    }
  }

  ngOnDestroy(): void {
    document.removeEventListener('click', this.onDocumentClick.bind(this));

    // Clean up all event listeners
    if (this.typeaheadContainer) {
      const element = this.typeaheadContainer.nativeElement;
      // Note: We can't remove the exact listeners without keeping references,
      // but the component is being destroyed anyway so this is acceptable
    }
  }

  ngOnChanges(): void {
    if (this.isVisible) {
      this.updateSuggestions();
    }
  }

  private onDocumentClick(event: Event): void {
    // Close typeahead if clicking outside, but not if clicking on the input
    if (this.isVisible && this.typeaheadContainer && !this.typeaheadContainer.nativeElement.contains(event.target as Node)) {
      // Check if the click target is the message input textarea
      const target = event.target as HTMLElement;
      if (target && target.classList.contains('message-textarea')) {
        return; // Don't close if clicking on the input
      }

      this.closeTypeahead.emit();
    }
  }
  inList(allowedAgents:any[], suggestion:any):boolean{
    return allowedAgents.findIndex(aa=>aa.agentType.toLowerCase() == suggestion.agentType.toLowerCase())>-1;
  }

  updateSuggestions(): void {
    // Use the input text directly since it's already the search term
    this.searchText = this.inputText || '';

    // Get suggestions using the centralized agent configuration service
    let suggestions = this.agentMentionService.getAgentSuggestions(this.searchText, 10);

    // Filter suggestions based on available agent types for this tab context
    if (this.availableAgentTypes && this.availableAgentTypes.length > 0) {
      if (this.searchText.length > 0) {
        suggestions = suggestions.filter(suggestion =>
          this.inList(this.availableAgentTypes,suggestion)&&suggestion.agentType!="AdFabricAgent"
        );
      }

    }

    // Limit to top 4 suggestions
    const newSuggestions = suggestions.slice(0, suggestions.length);

    // Only reset selection if the suggestions actually changed
    const suggestionsChanged = !this.suggestionsEqual(this.suggestions, newSuggestions);

    this.suggestions = newSuggestions;

    if (suggestionsChanged) {
      // Reset selection to first item only when suggestions actually change
      this.selectedIndex = 0;
    } else {
      // Keep current selection, but ensure it's within bounds
      this.selectedIndex = Math.min(this.selectedIndex, this.suggestions.length - 1);
    }
  }

  private suggestionsEqual(oldSuggestions: AgentSuggestion[], newSuggestions: AgentSuggestion[]): boolean {
    if (oldSuggestions.length !== newSuggestions.length) {
      return false;
    }

    for (let i = 0; i < oldSuggestions.length; i++) {
      if (oldSuggestions[i].key !== newSuggestions[i].key) {
        return false;
      }
    }

    return true;
  }

  selectAgent(agent: AgentSuggestion): void {
    this.agentSelected.emit(agent);
  }

  // Public methods for external navigation control
  navigateDown(): void {
    if (!this.isVisible || this.suggestions.length === 0) {
      return;
    }

    const oldIndex = this.selectedIndex;
    this.selectedIndex = Math.min(this.selectedIndex + 1, this.suggestions.length - 1);
  }

  navigateUp(): void {
    if (!this.isVisible || this.suggestions.length === 0) {
      return;
    }

    const oldIndex = this.selectedIndex;
    this.selectedIndex = Math.max(this.selectedIndex - 1, 0);
  }

  selectCurrent(): void {
    if (!this.isVisible || this.suggestions.length === 0) {
      return;
    }

    if (this.suggestions[this.selectedIndex]) {
      this.selectAgent(this.suggestions[this.selectedIndex]);
    } else {
      console.warn('🎯 selectCurrent: No agent at selectedIndex', this.selectedIndex);
    }
  }

  close(): void {
    this.closeTypeahead.emit();
  }

  // Getter for current state (useful for debugging)
  getCurrentSelection(): { selectedIndex: number; suggestion?: AgentSuggestion } {
    return {
      selectedIndex: this.selectedIndex,
      suggestion: this.suggestions[this.selectedIndex]
    };
  }

  getAgentIcon(agentKey: string): string {
    return 'psychology';
  }

  getAgentDisplayName(agentKey: string): string {
    return TextUtils.pascalOrCamelToDisplayName(agentKey);
  }

  highlightSearchText(text: string): string {
    if (!this.searchText) return text;

    const regex = new RegExp(`(${this.searchText})`, 'gi');
    return text.replace(regex, '<mark>$1</mark>');
  }

  getAgentTeam(agentKey: string): string | null {
    const agent = this.agentConfig.getAgent(agentKey);
    return (agent as any)?.teamName || null;
  }
} 