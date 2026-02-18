import { Component, EventEmitter, Input, Output, OnInit } from '@angular/core';
import { DemoTrackingService } from '../../services/demo-tracking.service';

@Component({
  selector: 'app-demo-modal',
  templateUrl: './demo-modal.component.html',
  styleUrls: ['./demo-modal.component.scss']
})
export class DemoModalComponent implements OnInit {
  @Input() currentUser: any = null;
  @Output() closeModal = new EventEmitter<void>();
  @Output() customerSaved = new EventEmitter<{customerName: string, loginId: string}>();

  customerName: string = '';
  isSubmitting: boolean = false;
  errorMessage: string = '';
  existingCustomers: string[] = [];

  constructor(private demoTrackingService: DemoTrackingService) {}

  ngOnInit(): void {
    this.existingCustomers = this.demoTrackingService.getCustomersList();
  }

  async onSubmit(): Promise<void> {
    if (!this.customerName.trim()) {
      this.errorMessage = 'Please enter a customer name';
      return;
    }

    if (!this.currentUser?.signInDetails?.loginId) {
      this.errorMessage = 'User information not available';
      return;
    }

    this.isSubmitting = true;
    this.errorMessage = '';

    try {
      await this.demoTrackingService.saveDemoTrackingData(
        this.customerName.trim(),
        this.currentUser.signInDetails.loginId
      );
      
      // Emit customer data for other components to react to
      this.customerSaved.emit({
        customerName: this.customerName.trim(),
        loginId: this.currentUser.signInDetails.loginId
      });
      
      this.closeModal.emit();
    } catch (error) {
      console.error('Error saving demo data:', error);
      this.errorMessage = 'Failed to save demo information. Please try again.';
    } finally {
      this.isSubmitting = false;
    }
  }

  onCancel(): void {
    this.closeModal.emit();
  }

  selectExistingCustomer(customerName: string): void {
    this.customerName = customerName;
  }
}