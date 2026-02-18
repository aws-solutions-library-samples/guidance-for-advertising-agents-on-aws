import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

export interface TourStep {
  id: string;
  title: string;
  content: string;
  target: string; // CSS selector for the target element
  position: 'top' | 'bottom' | 'left' | 'right' | 'center';
  highlight?: boolean; // Whether to highlight the target element
  backdrop?: boolean; // Whether to show backdrop
  order: number;
  events?: TourStepEvent[]; // Events to trigger when step is shown
  highlightClass?: string; // CSS class to add to target element for highlighting
}

export interface TourStepEvent {
  type: 'click' | 'open-panel' | 'close-panel' | 'custom' | 'hover' | string;
  target?: string; // CSS selector for event target
  action?: string; // Custom action identifier
  delay?: number; // Delay in ms before triggering event
  data?: any; // Additional data for the event
  targetRectanglePositionOverride?: {
    top?: string;
    left?: string;
    width?: string;
    height?: string;
  };
}

export interface TourConfig {
  [tabId: string]: {
    title: string;
    description: string;
    steps: TourStep[];
  };
}

@Injectable({
  providedIn: 'root'
})
export class TourService {
  private isActiveSubject = new BehaviorSubject<boolean>(false);
  private currentTabSubject = new BehaviorSubject<string>('');
  private currentStepSubject = new BehaviorSubject<number>(0);
  private executedEvents = new Set<string>(); // Track executed events to prevent duplicates

  public isActive$ = this.isActiveSubject.asObservable();
  public currentTab$ = this.currentTabSubject.asObservable();
  public currentStep$ = this.currentStepSubject.asObservable();

  private tourConfig: TourConfig = {
    // Universal interface tour that works across all tabs
    'interface-overview': {
      title: 'Agentic Campaign Planning Interface',
      description: 'Understand how AI agents collaborate to solve complex advertising challenges through structured data and intelligent conversations.',
      steps: [
        {
          id: 'welcome',
          title: 'Welcome to Agents for Advertising',
          content: 'This interface demonstrates how AI agents work together to solve complex advertising challenges. Unlike simple chatbots, these agents can analyze data, make decisions, and integrate with business systems to provide actionable insights and automation.',
          target: '.main-container',
          position: 'center',
          backdrop: true,
          highlight: false,
          order: 1
        },
        {
          id: 'context-info',
          title: 'Context Section - Current Data State',
          content: 'This section displays the current context and data that agents are working with. It shows contextual factors that inform agent decision-making. This data serves as the foundation for the agent recommendations, and is passed into the agent invocation.',
          target: '.context-trigger-container',
          position: 'right',
          highlight: true,
          highlightClass: 'tour-highlight-warning,with-margin-and-padding',
          order: 2,
          events: [
            {
              type: 'mouseover',
              target: '.context-trigger-btn',
              delay: 0,
              action: 'mouseover'
            }
          ]
        }
        ,
        {
          id: 'scenarios-button',
          title: 'Scenario Collection',
          content: 'When you see this scenarios panel open, you can explore pre-built questions and use cases that demonstrate agentic capabilities. These scenarios showcase how agents handle different challenges and makes is easy to do experiment or demo quickly.',
          target: '.scenarios-btn',
          position: 'left',
          highlight: true,
          highlightClass: 'tour-highlight-warning',
          order: 3,
          events: [
            {
              type: 'open-panel',
              target: '.scenarios-btn, .scenarios-toggle, .scenarios-button, [data-action="open-scenarios"]',
              delay: 0
            }
          ]
        },

        {
          id: 'tab-configuration',
          title: 'Tab Configuration',
          content: 'The scenario questions, as well as the context data for each tab, can be configured via the Tab Setup button in the header menu.',
          target: '.tab-setup-button',
          position: 'left',
          highlight: true,
          highlightClass: 'tour-highlight-warning,with-margin-and-padding',
          order: 4,
          events: [{
            type: 'click',
            target: '.scenario-card:first-child',
            delay: 300,
          },
          {
            type: 'click',
            target: '.user-button',
            delay: 0
          }
          ]
        },
        {
          id: 'chat-section',
          title: 'Agent Conversation Thread',
          content: 'This chat interface visualizes the conversation between AI agents and users as a structured message thread. It demonstrates agentic thought processes, collaborative reasoning, and how agents build upon each other\'s insights. This represents the "thinking out loud" approach rather than black-box AI responses. There are 2 ways to input chat messages. You can either select a scenario from the scenarios panel, or you can type your own message in the input field.',
          target: '.message',
          position: 'left',
          highlight: true,
          highlightClass: 'tour-highlight-warning,with-margin-and-padding',
          order: 5,
          events: [


          ]
        },
        {
          id: 'active-agents',
          title: 'Active Agents Collaboration',
          content: 'This section shows all AI agents that have contributed to the current conversation. Each agent has specialized expertise (Campaign Strategy, Creative Selection, Bid Optimization, etc.). Click on any agent to see their specific contributions and structured data.',
          target: '.participants-list',
          position: 'bottom',
          highlight: true,
          highlightClass: 'tour-highlight-active,with-margin-and-padding',
          order: 5,
          events: [

          ]
        },

        {
          id: 'visualizations',
          title: 'Structured Data Integration',
          content: 'These visualizations demonstrate that agents are not just conversational - they can return structured, machine-readable data that integrates directly with business systems. Charts, metrics, allocations, and generated content show how agents can trigger real business actions and system updates.',
          target: '.visual-components, .metrics-section, .allocations-section, .segment-cards-section, .visualization-container',
          position: 'top',
          highlight: true,
          highlightClass: 'tour-highlight-bright,with-margin-and-padding',
          order: 6,
          events: [
            {
              type: 'click',
              target: '.visual-components, .metrics-section, .allocations-section, .segment-cards-section, .visualization-container',
              delay: 100,
              action: 'highlight-visualizations'
            }
          ]
        }
      ]
    }

  };

  startTour(tabId: string): void {
    const config = this.tourConfig[tabId];
    if (config) {
      // Clear any previously executed events when starting a new tour
      this.executedEvents.clear();
      this.currentTabSubject.next(tabId);
      this.currentStepSubject.next(0);
      this.isActiveSubject.next(true);
    } else {
      console.warn('🎯 No tour config found for tab:', tabId);
    }
  }

  // Start the main interface overview tour
  startInterfaceTour(): void {
    this.startTour('interface-overview');
  }

  // Start tab-specific deep dive tour
  startTabTour(tabId: string): void {
    if (this.tourConfig[tabId] && tabId !== 'interface-overview') {
      this.startTour(tabId);
    } else {
      console.warn('🎯 No tab-specific tour available for:', tabId);
      // Fall back to interface overview
      this.startInterfaceTour();
    }
  }

  stopTour(): void {
    // Remove all highlights before stopping tour
    this.removeAllHighlights();
    // Clear executed events when stopping tour
    this.executedEvents.clear();
    this.isActiveSubject.next(false);
    this.currentStepSubject.next(0);
  }

  nextStep(): void {
    const currentStep = this.currentStepSubject.value;
    const totalSteps = this.getTotalSteps();

    if (currentStep < totalSteps - 1) {
      // Clear executed events when moving to next step so events can fire again
      this.executedEvents.clear();
      this.currentStepSubject.next(currentStep + 1);
    } else {
      this.stopTour();
    }
  }

  previousStep(): void {
    const currentStep = this.currentStepSubject.value;
    if (currentStep > 0) {
      // Clear executed events when moving to previous step so events can fire again
      this.executedEvents.clear();
      this.currentStepSubject.next(currentStep - 1);
    }
  }

  goToStep(stepIndex: number): void {
    const totalSteps = this.getTotalSteps();

    if (stepIndex >= 0 && stepIndex < totalSteps) {
      // Clear executed events when jumping to a specific step so events can fire again
      this.executedEvents.clear();
      this.currentStepSubject.next(stepIndex);
    }
  }

  getCurrentConfig() {
    const currentTab = this.currentTabSubject.value;
    return this.tourConfig[currentTab] || null;
  }

  getCurrentStep(): TourStep | null {
    const config = this.getCurrentConfig();
    const stepIndex = this.currentStepSubject.value;

    if (config) {
      // Get base steps plus any dynamic steps
      const allSteps = this.getAllStepsForCurrentTab();
      if (allSteps[stepIndex]) {
        return allSteps[stepIndex];
      }
    }

    return null;
  }

  // Get all steps including dynamic ones based on what's currently visible
  getAllStepsForCurrentTab(): TourStep[] {
    const config = this.getCurrentConfig();
    if (!config) return [];

    const baseSteps = [...config.steps];
    const dynamicSteps = this.getDynamicSteps();

    // Insert dynamic steps at appropriate positions
    const allSteps = [...baseSteps];

    // Add metrics visualization step after chat interface step if metrics are visible
    const metricsStep = dynamicSteps.find(step => step.id === 'metrics-visualization');
    if (metricsStep) {
      const chatInterfaceIndex = allSteps.findIndex(step => step.id === 'chat-interface');
      if (chatInterfaceIndex !== -1) {
        allSteps.splice(chatInterfaceIndex + 1, 0, metricsStep);
      } else {
        allSteps.push(metricsStep);
      }
    }

    // Reorder the steps and update order numbers
    return allSteps.map((step, index) => ({
      ...step,
      order: index + 1
    }));
  }

  // Get dynamic steps based on current page state
  private getDynamicSteps(): TourStep[] {
    const dynamicSteps: TourStep[] = [];

    // Check if metrics section is visible (indicating visual data from agent)
    const metricsSection = document.querySelector('.metrics-section');
    if (metricsSection) {
      dynamicSteps.push({
        id: 'metrics-visualization',
        title: 'Agent Response Visualization',
        content: 'AI agents can return structured data that integrates seamlessly with existing systems. This visual output contains formatted JSON that can be consumed by other applications, dashboards, or business intelligence tools for automated processing and analysis.',
        target: '.metrics-section',
        position: 'top',
        highlight: true,
        highlightClass: 'tour-highlight-bright',
        order: 0 // Will be reordered based on insertion position
      });
    }

    // Check for channel allocations
    const allocationsSection = document.querySelector('.allocations-section');
    if (allocationsSection) {
      dynamicSteps.push({
        id: 'allocations-visualization',
        title: 'Structured Budget Allocation',
        content: 'These allocation charts represent machine-readable budget distributions that can be automatically imported into media planning platforms, DSPs, or financial systems for immediate campaign execution.',
        target: '.allocations-section',
        position: 'top',
        highlight: true,
        highlightClass: 'tour-highlight-bright',
        order: 0
      });
    }

    // Check for segment cards
    const segmentSection = document.querySelector('.segment-cards-section');
    if (segmentSection) {
      dynamicSteps.push({
        id: 'segment-visualization',
        title: 'Audience Segment Intelligence',
        content: 'AI-generated audience segments with precise targeting parameters that can be exported to DMPs, CDPs, or programmatic platforms for automated audience targeting and bid adjustments.',
        target: '.segment-cards-section',
        position: 'top',
        highlight: true,
        highlightClass: 'tour-highlight-bright',
        order: 0
      });
    }

    return dynamicSteps;
  }

  getTotalSteps(): number {
    const allSteps = this.getAllStepsForCurrentTab();
    return allSteps.length;
  }

  getCurrentStepIndex(): number {
    return this.currentStepSubject.value;
  }

  // Refresh the tour steps (useful when DOM changes and new elements become available)
  refreshSteps(): void {
    if (this.isActiveSubject.value) {
      // Trigger a refresh by emitting the current step index
      const currentStepIndex = this.currentStepSubject.value;
      this.currentStepSubject.next(currentStepIndex);
    }
  }

  // Execute events for a tour step
  executeStepEvents(step: TourStep): void {
    if (!step.events || step.events.length === 0) {
      return;
    }

    step.events.forEach((event, index) => {
      // Create unique key for this step/event combination
      const eventKey = `${step.id}-${index}`;

      // Skip if this event has already been executed for this step
      if (this.executedEvents.has(eventKey)) {
        return;
      }

      // Mark this event as executed
      this.executedEvents.add(eventKey);

      setTimeout(() => {
        this.executeEvent(event);
      }, event.delay || 0);
    });
  }

  // Apply highlight class to target element
  applyHighlight(step: TourStep): void {
    if (!step.highlight || !step.target) return;

    const targetElements = this.findTargetElements(step.target);
    targetElements?.forEach((targetElement: Element) => {
      let highlightClass = step.highlightClass || 'tour-highlight-active';
      if (highlightClass.indexOf(',') > -1) {
        highlightClass.split(',').forEach(classnm => { targetElement.classList.add(classnm) });
      }
      else targetElement.classList.add(highlightClass);

    });
  }

  // Remove highlight class from target element
  removeHighlight(step: TourStep): void {
    if (!step.highlight || !step.target) return;

    const targetElements = this.findTargetElements(step.target);
    targetElements?.forEach((targetElement: Element) => {
      const highlightClass = step.highlightClass || 'tour-highlight-active';
      if (highlightClass.indexOf(',') > -1) {
        highlightClass.split(',').forEach(classnm => { targetElement.classList.remove(classnm); });
      }
      else targetElement.classList.remove(highlightClass);

    });
  }

  // Remove all highlight classes from all elements
  removeAllHighlights(): void {
    const config = this.getCurrentConfig();
    if (!config) return;

    const allSteps = this.getAllStepsForCurrentTab();
    allSteps.forEach(step => {
      if (step.highlight) {
        this.removeHighlight(step);
      }
    });
  }

  // Helper method to find target element from multiple possible selectors
  private findTargetElements(targetSelectors: string): Element[] | null {
    const selectors = targetSelectors.split(',').map(s => s.trim());
    const elements = new Array<Element>();

    for (const selector of selectors) {
      try {
        const elementList = document.querySelectorAll(selector);
        elementList.forEach((element) => {
          if (element) {

            const rect = element.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0) {
              elements.push(element);
            }
          }
        });
      } catch (error) {
        console.warn('🎯 Invalid selector:', selector, error);
      }

    }
    return elements

  }

  private executeEvent(event: TourStepEvent): void {

    switch (event.type) {
      case 'click':
        this.executeClickEvent(event);
        break;

      case 'open-panel':
        this.executeOpenPanelEvent(event);
        break;
      case 'close-panel':
        this.executeClosePanelEvent(event);
        break;
      case 'custom':
        this.executeCustomEvent(event);
        break;
      default:
        this.executeNamedEvent(event);
        break;

    }
  }

  private executeClickEvent(event: TourStepEvent): void {
    if (!event.target) return;

    // Try to find the element with retry logic
    this.retryFindElement(event.target, 3, 500).then((element) => {
      if (element) {
        element.click();
      } else {
        console.warn('🎯 Click target not found after retries:', event.target);
      }
    });
  }



  private executeNamedEvent(event: TourStepEvent): void {
    if (!event.target) return;

    let evt = document.createEvent("Event");
    // Try to find the element with retry logic
    this.retryFindElement(event.target, 3, 500).then((element) => {
      if (element && event.type) {
        const mouseoverEvent = new Event('mouseover');

        // Create a click event that bubbles up and
        // cannot be canceled
        element.dispatchEvent(mouseoverEvent);

        // Listen for the event.
        element.addEventListener(
          event.type,
          (e) => {
            // e.target matches elem
          },
          false,
        );

      } else {
        console.warn('target not found after retries:', event.target);
      }
    });
  }

  private executeOpenPanelEvent(event: TourStepEvent): void {
    if (!event.target) return;

    // Try to find the button with retry logic
    this.retryFindElement(event.target, 3, 500).then((button) => {
      if (button) {
        button.click();

        // Wait a bit for panel to open, then refresh tour positions
        setTimeout(() => {
          this.refreshSteps();
        }, 500);
      } else {
        console.warn('🎯 Panel button not found after retries:', event.target);
      }
    });
  }

  // Helper method to retry finding an element
  private retryFindElement(selector: string, maxRetries: number = 3, delay: number = 500): Promise<HTMLElement | null> {
    return new Promise((resolve) => {
      let attempts = 0;

      const tryFind = () => {
        const element = document.querySelector(selector) as HTMLElement;

        if (element) {
          resolve(element);
        } else if (attempts < maxRetries) {
          attempts++;
          setTimeout(tryFind, delay);
        } else {
          console.warn(`🎯 Element not found after ${maxRetries} retries:`, selector);
          resolve(null);
        }
      };

      tryFind();
    });
  }

  private executeClosePanelEvent(event: TourStepEvent): void {
    if (!event.target) return;

    // Try to find the close button with retry logic
    this.retryFindElement(event.target, 3, 500).then((closeButton) => {
      if (closeButton) {
        closeButton.click();

        // Wait a bit for panel to close, then refresh tour positions
        setTimeout(() => {
          this.refreshSteps();
        }, 300);
      } else {
        console.warn('🎯 Close button not found after retries:', event.target);
      }
    });
  }

  private executeCustomEvent(event: TourStepEvent): void {

    // Emit custom event that components can listen to
    const customEvent = new CustomEvent('tour-custom-event', {
      detail: {
        action: event.action,
        data: event.data,
        target: event.target
      }
    });

    document.dispatchEvent(customEvent);
  }
} 