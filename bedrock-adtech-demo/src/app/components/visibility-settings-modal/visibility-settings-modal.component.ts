import { Component, Input, Output, EventEmitter, OnInit } from '@angular/core';

export interface VisibilitySettings {
  hiddenMessageTypes: string[];
  includedContextSections: string[];
  hiddenAgents: string[];
}

@Component({
  selector: 'app-visibility-settings-modal',
  templateUrl: './visibility-settings-modal.component.html',
  styleUrls: ['./visibility-settings-modal.component.scss']
})
export class VisibilitySettingsModalComponent implements OnInit {
  @Input() isVisible: boolean = false;
  @Input() currentSettings: VisibilitySettings = {
    hiddenMessageTypes: [],
    includedContextSections: [],
    hiddenAgents: []
  };
  @Input() contextData: any = null;
  @Input() availableAgents: any[] = [];
  @Output() settingsChanged = new EventEmitter<VisibilitySettings>();
  @Output() modalClosed = new EventEmitter<void>();

  // Available message types that can be hidden
  availableMessageTypes = [
    { key: 'reasoning', label: 'Agent Rationale', description: 'Hide reasoning and thought processes' },
    { key: 'tool-trace', label: 'Tool Traces', description: 'Hide tool execution details' },
    { key: 'error', label: 'Error Messages', description: 'Hide error and warning messages' },
    { key: 'chunk', label: 'Response Chunks', description: 'Hide individual response chunks' },
    { key: 'streaming-chunk', label: 'Streaming Chunks', description: 'Hide streaming response chunks' },
    { key: 'data', label: 'Data Messages', description: 'Hide structured data responses' },
    { key: 'chart', label: 'Chart Messages', description: 'Hide chart and visualization messages' }
  ];

  // Context data sections that can be included
  availableContextSections: { key: string; label: string; description: string; preview: string }[] = [];

  // Available agents that can be hidden
  availableAgentOptions: { key: string; label: string; description: string }[] = [];

  // Local working copy of settings
  workingSettings: VisibilitySettings = {
    hiddenMessageTypes: [],
    includedContextSections: [],
    hiddenAgents: []
  };

  ngAfterViewInit(){
    
  }

  ngOnInit() {
    // Extract available context sections from contextData first
    this.extractContextSections();
    
    // Initialize working settings from current settings
    this.workingSettings = {
      hiddenMessageTypes: [...this.currentSettings.hiddenMessageTypes],
      includedContextSections: this.currentSettings.includedContextSections.length > 0 
        ? [...this.currentSettings.includedContextSections]
        : [...this.availableContextSections.map(s => s.key)], // Default to all context sections selected
      hiddenAgents: [...this.currentSettings.hiddenAgents]
    };
    
    // Initialize visibilityOfContextDataSections from workingSettings
    this.visibilityOfContextDataSections = {};
    this.availableContextSections.forEach(section => {
      this.visibilityOfContextDataSections[section.key] = 
        this.workingSettings.includedContextSections.includes(section.key);
    });
    
    // Extract available agents
    //this.extractAvailableAgents();
  }

  private extractContextSections() {
    this.availableContextSections = [];

    if (!this.contextData) {
      return;
    }

    // Extract top-level properties from contextData
    Object.keys(this.contextData).forEach(key => {
      const value = this.contextData[key];
      let description = '';
      let preview = '';

      // Generate description and preview based on the data type and content
      if (Array.isArray(value)) {
        description = `Include ${key} array (${value.length} items)`;
        preview = value.length > 0 ? `[${JSON.stringify(value[0]).substring(0, 100)}...]` : '[]';
      } else if (typeof value === 'object' && value !== null) {
        const subKeys = Object.keys(value);
        description = `Include ${key} object (${subKeys.length} properties)`;
        preview = `{${subKeys.slice(0, 3).join(', ')}${subKeys.length > 3 ? '...' : ''}}`;
      } else {
        description = `Include ${key} data`;
        preview = String(value).substring(0, 50) + (String(value).length > 50 ? '...' : '');
      }

      this.availableContextSections.push({
        key,
        label: this.formatLabel(key),
        description,
        preview
      });
    });
  }

  private formatLabel(key: string): string {
    // Convert camelCase to Title Case
    return key
      .replace(/([A-Z])/g, ' $1')
      .replace(/^./, str => str.toUpperCase())
      .trim();
  }

  isMessageTypeHidden(messageType: string): boolean {
    return this.workingSettings.hiddenMessageTypes.includes(messageType);
  }

  visibilityOfContextDataSections:any = {};
  isContextSectionIncluded(sectionKey: string): boolean {
    if(!Object.keys(this.visibilityOfContextDataSections).includes(sectionKey))
      this.visibilityOfContextDataSections[sectionKey] = true;
    return this.visibilityOfContextDataSections[sectionKey];
  }

  toggleMessageType(messageType: string) {
    const index = this.workingSettings.hiddenMessageTypes.findIndex(t=>t==messageType||t.indexOf(messageType)>-1);
    if (index > -1) {
      this.workingSettings.hiddenMessageTypes.splice(index, 1);
    } else {
      this.workingSettings.hiddenMessageTypes.push(messageType);
    }
  }

  //default to true but toggle after.
  toggleContextSection(sectionKey: string) {
    if(!Object.keys(this.visibilityOfContextDataSections).includes(sectionKey))
      this.visibilityOfContextDataSections[sectionKey] = true;
    this.visibilityOfContextDataSections[sectionKey] = !this.visibilityOfContextDataSections[sectionKey];
    
    // Sync with workingSettings.includedContextSections
    const index = this.workingSettings.includedContextSections.indexOf(sectionKey);
    if (this.visibilityOfContextDataSections[sectionKey] && index === -1) {
      // Add to included sections
      this.workingSettings.includedContextSections.push(sectionKey);
    } else if (!this.visibilityOfContextDataSections[sectionKey] && index > -1) {
      // Remove from included sections
      this.workingSettings.includedContextSections.splice(index, 1);
    }
  }

  isAgentHidden(agentName: string): boolean {
    return this.workingSettings.hiddenAgents.includes(agentName);
  }

  toggleAgent(agentName: string) {
    const index = this.workingSettings.hiddenAgents.indexOf(agentName);
    if (index > -1) {
      this.workingSettings.hiddenAgents.splice(index, 1);
    } else {
      this.workingSettings.hiddenAgents.push(agentName);
    }
  }

  resetToDefaults() {
    this.workingSettings = {
      hiddenMessageTypes: ['tool-trace', 'error'],
      includedContextSections: [...this.availableContextSections.map(s => s.key)], // Default to all context sections
      hiddenAgents: []
    };
  }

  showAll() {
    this.workingSettings = {
      hiddenMessageTypes: [],
      includedContextSections: [...this.availableContextSections.map(s => s.key)],
      hiddenAgents: []
    };
  }

  hideAll() {
    this.workingSettings = {
      hiddenMessageTypes: [...this.availableMessageTypes.map(t => t.key)],
      includedContextSections: [],
      hiddenAgents: [...this.availableAgentOptions.map(a => a.key)]
    };
  }

  applySettings() {
    this.settingsChanged.emit({
      hiddenMessageTypes: [...this.workingSettings.hiddenMessageTypes],
      includedContextSections: [...this.workingSettings.includedContextSections],
      hiddenAgents: [...this.workingSettings.hiddenAgents]
    });
    this.closeModal();
  }

  closeModal() {
    this.modalClosed.emit();
  }

  // Get summary of current settings
  getSettingsSummary(): string {
    const hiddenTypes = this.workingSettings.hiddenMessageTypes.length;
    const includedSections = this.workingSettings.includedContextSections.length;
    const totalSections = this.availableContextSections.length;
    const hiddenAgents = this.workingSettings.hiddenAgents.length;

    const parts: string[] = [];
    
    if (hiddenTypes > 0) {
      parts.push(`${hiddenTypes} message type${hiddenTypes > 1 ? 's' : ''} hidden`);
    }
    
    if (hiddenAgents > 0) {
      parts.push(`${hiddenAgents} agent${hiddenAgents > 1 ? 's' : ''} hidden`);
    }
    
    if (totalSections > 0) {
      if (includedSections === 0) {
        parts.push('no context sections included');
      } else if (includedSections === totalSections) {
        parts.push('all context sections included');
      } else {
        parts.push(`${includedSections}/${totalSections} context sections included`);
      }
    }

    return parts.length > 0 ? parts.join(', ') : 'Default settings';
  }
}