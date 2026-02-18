import { Component, Input, Output, EventEmitter } from '@angular/core';
import { AgentConfigService } from '../../services/agent-config.service';
import { TextUtils } from '../../utils/text-utils';

@Component({
  selector: 'app-scenarios',
  templateUrl: './scenarios.component.html',
  styleUrls: ['./scenarios.component.scss']
})
export class ScenariosComponent {
  @Input() scenarios: any[] = [];
  @Input() showScenariosPanel = false;
  @Output() scenarioSelected = new EventEmitter<any>();
  @Output() panelClosed = new EventEmitter<void>();

  selectedScenarioIndex: number | null = null;

  constructor(private agentConfig: AgentConfigService) {}

  selectScenario(scenario: any, index: number): void {
    this.selectedScenarioIndex = index;
    this.scenarioSelected.emit({ scenario, index });
  }

  closeScenariosPanel(): void {
    this.panelClosed.emit();
  }

  getAgentDisplayName(agentType: string): string {
    return TextUtils.pascalOrCamelToDisplayName(agentType);
  }

  async getAgentColor(agentType: string): Promise<string> {
    return await this.agentConfig.getAgentColor(agentType);
  }

  getAgentIcon(): string {
    return 'psychology';
  }
}
