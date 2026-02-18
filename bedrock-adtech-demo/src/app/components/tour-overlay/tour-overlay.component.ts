import { Component, OnInit, OnDestroy, ElementRef, ViewChild } from '@angular/core';
import { trigger, state, style, transition, animate } from '@angular/animations';
import { TourService, TourStep } from '../../services/tour.service';
import { Subject, combineLatest } from 'rxjs';
import { takeUntil } from 'rxjs/operators';

@Component({
  selector: 'app-tour-overlay',
  templateUrl: './tour-overlay.component.html',
  styleUrls: ['./tour-overlay.component.scss'],
  animations: [
    trigger('fadeIn', [
      transition(':enter', [
        style({ opacity: 0 }),
        animate('300ms ease-out', style({ opacity: 1 }))
      ]),
      transition(':leave', [
        animate('200ms ease-in', style({ opacity: 0 }))
      ])
    ]),
    trigger('slideIn', [
      transition(':enter', [
        style({ 
          opacity: 0, 
          transform: 'translateY(-10px) scale(0.95)' 
        }),
        animate('400ms cubic-bezier(0.25, 0.8, 0.25, 1)', style({ 
          opacity: 1, 
          transform: 'translateY(0) scale(1)' 
        }))
      ]),
      transition(':leave', [
        animate('200ms ease-in', style({ 
          opacity: 0, 
          transform: 'translateY(-10px) scale(0.95)' 
        }))
      ])
    ]),

  ]
})
export class TourOverlayComponent implements OnInit, OnDestroy {
  @ViewChild('tooltip', { static: false }) tooltip!: ElementRef;

  isActive = false;
  currentStep: TourStep | null = null;
  currentStepIndex = 0;
  totalSteps = 0;
  targetElement: Element | null = null;
  tooltipPosition = { top: '0px', left: '0px' };
  
  // Dragging state
  isDragging = false;
  dragOffset = { x: 0, y: 0 };
  
  private destroy$ = new Subject<void>();

  constructor(private tourService: TourService) {}

  ngOnInit(): void {
    
    // Subscribe to tour state changes
    combineLatest([
      this.tourService.isActive$,
      this.tourService.currentStep$
    ]).pipe(
      takeUntil(this.destroy$)
    ).subscribe(([isActive, stepIndex]) => {
      this.isActive = isActive;
      this.currentStepIndex = stepIndex;
      this.totalSteps = this.tourService.getTotalSteps();
      this.currentStep = this.tourService.getCurrentStep();

      if (this.isActive && this.currentStep) {
        this.updatePositions();
        
        // Execute events for the current step after a longer delay to ensure DOM is ready
        setTimeout(() => {
          this.tourService.executeStepEvents(this.currentStep!);
        }, 1000);
      }
    });
  }

  ngOnDestroy(): void {
    // Clean up highlights when component is destroyed
    this.tourService.removeAllHighlights();
    this.destroy$.next();
    this.destroy$.complete();
  }

  private updatePositions(): void {
    if (!this.currentStep) {
      return;
    }

    // Remove highlight from previous step
    this.tourService.removeAllHighlights();

    // Find target element - try multiple selectors if provided (comma-separated)
    this.targetElement = this.findTargetElement(this.currentStep.target);
    
    // Apply highlight class to current target element
    if (this.currentStep.highlight) {
      this.tourService.applyHighlight(this.currentStep);
    }
    
    if (this.targetElement) {
      // Ensure the target element is visible by scrolling it into view if needed
      const rect = this.targetElement.getBoundingClientRect();
      const isVisible = rect.top >= 0 && rect.top <= window.innerHeight && 
                       rect.bottom >= 0 && rect.bottom <= window.innerHeight;
      
      if (!isVisible) {
        this.targetElement.scrollIntoView({ 
          behavior: 'smooth', 
          block: 'center', 
          inline: 'center' 
        });
        // Wait for scroll to complete before positioning tooltip
        setTimeout(() => {
          this.updateTooltipPosition();
        }, 500);
        return;
      }
      
      // Position tooltip relative to target element
      setTimeout(() => {
        this.updateTooltipPosition();
      }, 150);
    } else {
      console.warn('🎯 Target element not found for selector:', this.currentStep.target);
      // For center position or missing elements, still show the tooltip
      setTimeout(() => {
        this.updateTooltipPosition();
      }, 150);
    }
  }

  // Helper method to find target element from multiple possible selectors
  private findTargetElement(targetSelectors: string): Element | null {
    // Split by comma and try each selector
    const selectors = targetSelectors.split(',').map(s => s.trim());
    
    for (const selector of selectors) {
      try {
        const element = document.querySelector(selector);
                  if (element) {
            // Check if element is visible (has dimensions)
            const rect = element.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0) {
              return element;
            }
          }
      } catch (error) {
        console.warn('🎯 Invalid selector:', selector, error);
      }
    }
    
    console.warn('🎯 No visible target element found for selectors:', targetSelectors);
    return null;
  }

  private updateTooltipPosition(): void {
    if (!this.tooltip || !this.currentStep) return;

    // If no target element found, use center positioning as fallback
    if (!this.targetElement) {
      const viewport = {
        width: window.innerWidth,
        height: window.innerHeight
      };
      
      this.tooltipPosition = {
        top: `${(viewport.height / 2) - 200}px`, // Offset to account for tooltip height
        left: `${(viewport.width / 2) - 200}px`  // Offset to account for tooltip width
      };
      return;
    }

    const targetRect = this.targetElement.getBoundingClientRect();
    const tooltipRect = this.tooltip.nativeElement.getBoundingClientRect();
    const viewport = {
      width: window.innerWidth,
      height: window.innerHeight
    };

    let top = 0;
    let left = 0;
    const offset = 16;

    switch (this.currentStep.position) {
      case 'top':
        top = targetRect.top - tooltipRect.height - offset;
        left = targetRect.left + (targetRect.width / 2) - (tooltipRect.width / 2);
        break;
      case 'bottom':
        top = targetRect.bottom + offset;
        left = targetRect.left + (targetRect.width / 2) - (tooltipRect.width / 2);
        break;
      case 'left':
        top = targetRect.top + (targetRect.height / 2) - (tooltipRect.height / 2);
        left = targetRect.left - tooltipRect.width - offset;
        break;
      case 'right':
        top = targetRect.top + (targetRect.height / 2) - (tooltipRect.height / 2);
        left = targetRect.right + offset;
        break;
      case 'center':
        top = (viewport.height / 2) - (tooltipRect.height / 2);
        left = (viewport.width / 2) - (tooltipRect.width / 2);
        break;
    }

    // Ensure tooltip stays within viewport
    if (left < 16) left = 16;
    if (left + tooltipRect.width > viewport.width - 16) {
      left = viewport.width - tooltipRect.width - 16;
    }
    if (top < 16) top = 16;
    if (top + tooltipRect.height > viewport.height - 16) {
      top = viewport.height - tooltipRect.height - 16;
    }

    this.tooltipPosition = {
      top: `${top}px`,
      left: `${left}px`
    };
  }

  nextStep(): void {
    this.tourService.nextStep();
  }

  previousStep(): void {
    this.tourService.previousStep();
  }

  closeTour(): void {
    this.tourService.stopTour();
  }

  goToStep(stepIndex: number): void {
    this.tourService.goToStep(stepIndex);
  }

  getProgressPercentage(): number {
    if (this.totalSteps === 0) return 0;
    return ((this.currentStepIndex + 1) / this.totalSteps) * 100;
  }

  // Handle window resize to reposition elements
  onWindowResize(): void {
    if (this.isActive && this.currentStep) {
      this.updatePositions();
    }
  }

  // Handle start of dragging
  onDragStart(event: MouseEvent): void {
    if (!this.tooltip) return;
    
    this.isDragging = true;
    const tooltipRect = this.tooltip.nativeElement.getBoundingClientRect();
    
    // Calculate the offset between mouse position and tooltip position
    this.dragOffset = {
      x: event.clientX - tooltipRect.left,
      y: event.clientY - tooltipRect.top
    };
    
    // Prevent text selection while dragging
    event.preventDefault();
  }

  // Handle dragging
  onDrag(event: MouseEvent): void {
    if (!this.isDragging) return;
    
    const newLeft = event.clientX - this.dragOffset.x;
    const newTop = event.clientY - this.dragOffset.y;
    
    // Ensure tooltip stays within viewport bounds
    const tooltipRect = this.tooltip.nativeElement.getBoundingClientRect();
    const maxLeft = window.innerWidth - tooltipRect.width;
    const maxTop = window.innerHeight - tooltipRect.height;
    
    this.tooltipPosition = {
      left: `${Math.max(0, Math.min(newLeft, maxLeft))}px`,
      top: `${Math.max(0, Math.min(newTop, maxTop))}px`
    };
  }

  // Handle end of dragging
  onDragEnd(): void {
    this.isDragging = false;
  }
} 