import { Component, Input } from '@angular/core';

/**
 * Timeline Visualization Component
 * 
 * Displays implementation plans, campaign schedules, and phased rollouts in a horizontal
 * timeline format with phases, milestones, tasks, and dependencies.
 * 
 * @example
 * ```html
 * <app-timeline-visualization [timelineData]="campaignTimeline"></app-timeline-visualization>
 * ```
 * 
 * @example JSON Structure:
 * ```json
 * {
 *   "visualizationType": "timeline",
 *   "title": "Holiday Campaign Execution Timeline",
 *   "subtitle": "Q4 2024 Campaign Rollout Plan",
 *   "startDate": "2024-10-01",
 *   "endDate": "2024-12-31",
 *   "totalDuration": "3 months",
 *   "overallObjective": "Launch comprehensive holiday campaign across all channels",
 *   "phases": [
 *     {
 *       "primaryLabel": "Campaign Planning",
 *       "secondaryLabel": "Strategy & Creative Development",
 *       "description": "Develop campaign strategy, create assets, and finalize targeting",
 *       "startDate": "2024-10-01",
 *       "endDate": "2024-10-31",
 *       "duration": "4 weeks",
 *       "status": "completed",
 *       "priority": "high",
 *       "expectedOutcome": "Complete campaign strategy and creative assets",
 *       "budget": "$150,000",
 *       "resources": ["Creative Team", "Strategy Team", "Data Analysts"],
 *       "milestones": [
 *         {
 *           "date": "2024-10-15",
 *           "label": "Strategy Approval",
 *           "status": "achieved"
 *         },
 *         {
 *           "date": "2024-10-30",
 *           "label": "Creative Assets Complete",
 *           "status": "achieved"
 *         }
 *       ],
 *       "tasks": [
 *         {
 *           "taskLabel": "Market Research",
 *           "status": "completed",
 *           "dueDate": "2024-10-10"
 *         },
 *         {
 *           "taskLabel": "Creative Brief",
 *           "status": "completed",
 *           "dueDate": "2024-10-15"
 *         }
 *       ],
 *       "risks": ["Delayed creative approvals"]
 *     },
 *     {
 *       "primaryLabel": "Pre-Launch Setup",
 *       "secondaryLabel": "Technical Implementation",
 *       "description": "Set up tracking, configure campaigns, and conduct testing",
 *       "startDate": "2024-11-01",
 *       "endDate": "2024-11-15",
 *       "duration": "2 weeks",
 *       "status": "active",
 *       "priority": "high",
 *       "expectedOutcome": "All campaigns configured and tested",
 *       "budget": "$75,000",
 *       "dependencies": ["Campaign Planning"],
 *       "milestones": [
 *         {
 *           "date": "2024-11-08",
 *           "label": "Technical Setup Complete",
 *           "status": "pending"
 *         }
 *       ],
 *       "tasks": [
 *         {
 *           "taskLabel": "Campaign Configuration",
 *           "status": "in-progress",
 *           "dueDate": "2024-11-05"
 *         },
 *         {
 *           "taskLabel": "QA Testing",
 *           "status": "pending",
 *           "dueDate": "2024-11-12"
 *         }
 *       ]
 *     },
 *     {
 *       "primaryLabel": "Campaign Launch",
 *       "secondaryLabel": "Full Market Activation",
 *       "description": "Launch campaigns across all channels with monitoring",
 *       "startDate": "2024-11-16",
 *       "endDate": "2024-12-31",
 *       "duration": "6 weeks",
 *       "status": "upcoming",
 *       "priority": "high",
 *       "expectedOutcome": "Successful campaign execution with target KPIs",
 *       "budget": "$2,000,000",
 *       "dependencies": ["Pre-Launch Setup"],
 *       "milestones": [
 *         {
 *           "date": "2024-11-16",
 *           "label": "Campaign Go-Live",
 *           "status": "upcoming"
 *         },
 *         {
 *           "date": "2024-12-01",
 *           "label": "Black Friday Peak",
 *           "status": "upcoming"
 *         }
 *       ]
 *     }
 *   ],
 *   "milestones": [
 *     {
 *       "date": "2024-11-16",
 *       "label": "Campaign Launch",
 *       "description": "Full campaign activation across all channels"
 *     },
 *     {
 *       "date": "2024-12-01",
 *       "label": "Black Friday",
 *       "description": "Peak shopping period activation"
 *     }
 *   ],
 *   "successMetrics": [
 *     "25% increase in brand awareness",
 *     "15% lift in holiday sales",
 *     "3.5% average CTR across channels"
 *   ],
 *   "criticalPath": ["Campaign Planning", "Pre-Launch Setup", "Campaign Launch"]
 * }
 * ```
 * 
 * @features
 * - Horizontal timeline with phase visualization
 * - Status indicators (upcoming, active, completed, delayed)
 * - Priority levels with color coding
 * - Milestone markers and achievement tracking
 * - Task completion progress bars
 * - Dependency visualization
 * - Resource and budget information
 * - Risk factor display
 * - Critical path highlighting
 * 
 * @useCases
 * - Campaign execution schedules
 * - Creative rollout plans
 * - Testing and optimization phases
 * - Content release timing
 * - Strategic implementation roadmaps
 */

export interface TimelinePhase {
  primaryLabel: string;
  secondaryLabel?: string;
  description?: string;
  startDate: string;
  endDate: string;
  duration?: string;
  status: 'upcoming' | 'active' | 'completed' | 'delayed';
  priority: 'high' | 'medium' | 'low';
  tasks?: TimelineTask[];
  dependencies?: string[];
  expectedOutcome?: string;
  budget?: string;
  owner?: string;
  milestones?: TimelineMilestone[];
  resources?: string[];
  risks?: string[];
}

export interface TimelineTask {
  taskLabel: string;
  description?: string;
  dueDate?: string;
  status: 'pending' | 'in-progress' | 'completed' | 'blocked';
  assignee?: string;
  estimatedHours?: number;
}

export interface TimelineMilestone {
  milestoneLabel?: string;
  label?: string;
  date?: string;
  targetDate?: string;
  description?: string;
  successCriteria?: string[];
  status: 'upcoming' | 'achieved' | 'missed' | 'at-risk' | 'pending';
}

export interface TimelineData {
  visualizationType: 'timeline';
  title: string;
  subtitle?: string;
  totalDuration?: string;
  startDate: string;
  endDate: string;
  phases: TimelinePhase[];
  overallObjective?: string;
  successMetrics?: string[];
  riskFactors?: string[];
  criticalPath?: string[];
  milestones?: TimelineMilestone[];
}

@Component({
  selector: 'app-timeline-visualization',
  templateUrl: './timeline-visualization.component.html',
  styleUrls: ['./timeline-visualization.component.scss']
})
export class TimelineVisualizationComponent {
  @Input() timelineData!: TimelineData;
  @Input() compactMode: boolean = false;

  // TrackBy functions
  trackByIndex = (index: number): number => {
    return index;
  }

  trackByPhaseLabel = (index: number, phase: TimelinePhase): string => {
    return phase.primaryLabel;
  }

  trackByTaskLabel = (index: number, task: TimelineTask): string => {
    return task.taskLabel;
  }

  trackByMilestoneLabel = (index: number, milestone: TimelineMilestone): string => {
    return milestone.milestoneLabel || milestone.label || `milestone-${index}`;
  }

  // Helper method to get milestone label
  getMilestoneLabel(milestone: TimelineMilestone): string {
    return milestone.milestoneLabel || milestone.label || 'Milestone';
  }

  // Helper method to get milestone date
  getMilestoneDate(milestone: TimelineMilestone): string {
    return milestone.date || milestone.targetDate || '';
  }

  // Helper method to get critical path items
  getCriticalPath(): string[] {
    return this.timelineData?.criticalPath || [];
  }

  // Helper method to check if phase is in critical path
  isInCriticalPath(phase: TimelinePhase): boolean {
    const criticalPath = this.getCriticalPath();
    return criticalPath.includes(phase.primaryLabel);
  }

  // Helper method to get global milestones
  getGlobalMilestones(): TimelineMilestone[] {
    return this.timelineData?.milestones || [];
  }

  // Get status color for phases
  getPhaseStatusColor(status: string): string {
    switch (status) {
      case 'completed': return '#10b981';
      case 'active': return '#3b82f6';
      case 'delayed': return '#ef4444';
      case 'upcoming': return '#6b7280';
      default: return '#6b7280';
    }
  }

  // Get priority color
  getPriorityColor(priority: string): string {
    switch (priority?.toLowerCase()) {
      case 'high': return '#ef4444';
      case 'medium': return '#f59e0b';
      case 'low': return '#10b981';
      default: return '#6b7280';
    }
  }

  // Format date for display
  formatDate(dateString: string): string {
    if (!dateString) return '';
    const date = new Date(dateString);
    return date.toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric'
    });
  }

  // Get status icon based on phase/task status
  getStatusIcon(status: string): string {
    switch (status) {
      case 'completed': return 'check_circle';
      case 'active': return 'play_circle';
      case 'delayed': return 'warning';
      case 'upcoming': return 'schedule';
      default: return 'radio_button_unchecked';
    }
  }

  // Check if phase has tasks
  hasTasks(phase: TimelinePhase): boolean {
    return !!(phase.tasks && phase.tasks.length > 0);
  }

  // Check if phase has milestones
  hasMilestones(phase: TimelinePhase): boolean {
    return !!(phase.milestones && phase.milestones.length > 0);
  }

  // Get total tasks count
  getTotalTasksCount(phase: TimelinePhase): number {
    return phase.tasks ? phase.tasks.length : 0;
  }

  // Get completed tasks count
  getCompletedTasksCount(phase: TimelinePhase): number {
    if (!phase.tasks) return 0;
    return phase.tasks.filter(task => task.status === 'completed').length;
  }

  // Calculate task completion percentage
  getTaskCompletionPercentage(phase: TimelinePhase): number {
    const total = this.getTotalTasksCount(phase);
    if (total === 0) return 0;
    const completed = this.getCompletedTasksCount(phase);
    return Math.round((completed / total) * 100);
  }

  // Get detailed information for hover tooltip
  getHoverDetails(phase: TimelinePhase): string {
    let details = `${phase.primaryLabel}\n`;
    details += `${this.formatDate(phase.startDate)} → ${this.formatDate(phase.endDate)}\n`;

    if (phase.description) {
      details += `\n${phase.description}\n`;
    }

    if (phase.duration) {
      details += `\nDuration: ${phase.duration}`;
    }

    if (phase.budget) {
      details += `\nBudget: ${phase.budget}`;
    }

    if (phase.owner) {
      details += `\nOwner: ${phase.owner}`;
    }

    if (phase.expectedOutcome) {
      details += `\nExpected Outcome: ${phase.expectedOutcome}`;
    }

    if (phase.dependencies && typeof(phase.dependencies) === 'string') {
      details += `\nDependencies: ${phase.dependencies}`;
    } else if (phase.dependencies && Array.isArray(phase.dependencies) && phase.dependencies.length > 0) {
      details += `\nDependencies: ${phase.dependencies.join(', ')}`;
    }

    if (phase.tasks && phase.tasks.length > 0) {
      details += `\n\nTasks (${this.getCompletedTasksCount(phase)}/${this.getTotalTasksCount(phase)} completed):`;
      phase.tasks.forEach(task => {
        details += `\n• ${task.taskLabel} (${task.status})`;
        if (task.dueDate) {
          details += ` - due ${this.formatDate(task.dueDate)}`;
        }
      });
    }

    if (phase.milestones && phase.milestones.length > 0) {
      details += `\n\nMilestones:`;
      phase.milestones.forEach(milestone => {
        details += `\n• ${milestone.milestoneLabel} - ${milestone?.targetDate ? this.formatDate(milestone.targetDate) : ''} (${milestone.status})`;
      });
    }

    return details;
  }
} 