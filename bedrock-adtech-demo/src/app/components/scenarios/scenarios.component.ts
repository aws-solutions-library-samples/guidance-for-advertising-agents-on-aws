import { Component, Input, Output, EventEmitter } from '@angular/core';
import { AgentConfigService } from '../../services/agent-config.service';
import { TextUtils } from '../../utils/text-utils';
import { ScenarioExample } from 'src/app/models/application-models';

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

  selectScenario(scenario: ScenarioExample, index: number): void {
    this.selectedScenarioIndex = index;
    scenario.agentObject = this.agentConfig.getAgentByAgentNameAndTeam(scenario.agentType, scenario.category);
    this.scenarioSelected.emit({ scenario, index });
  }

  closeScenariosPanel(): void {
    this.panelClosed.emit();
  }

  getAgentDisplayName(agentType: string): string {
    return TextUtils.pascalOrCamelToDisplayName(agentType);
  }

  getAgentColor(agentName)
  {
    return this.agentConfig.getEnrichedAgents().find((agent) => agent.name === agentName||agent.key==agentName)?.color;
  }

  getAgentIcon(): string {
    return 'psychology';
  }
}
