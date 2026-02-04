import { Component, Input, OnInit, OnDestroy, ChangeDetectorRef } from '@angular/core';
import { Subject, takeUntil, timer } from 'rxjs';
import { AwsConfigService } from '../../services/aws-config.service';
import { ImageStatusService, ImageStatusResponse } from '../../services/image-status.service';
import { Amplify } from 'aws-amplify';
import { events } from '@aws-amplify/api';
import { channel } from 'diagnostics_channel';

interface ImageItem {
  id: string;
  description: string;
  status: 'pending' | 'generating' | 'completed' | 'error';
  progress: number;
  url?: string;
  thumbnailUrl?: string;
  fullImageUrl?: string;
  error?: string;
}

interface CreativeImageData {
  title?: string|undefined;
  imagery?: string[]|undefined;
  creatives:any[]|undefined;
  imageDescriptions?: string[]|undefined;
  colors?: string[]|undefined;
  colorPalette?: string[]|undefined;
  theme?: string|undefined;
  style?: string|undefined;
}

@Component({
  selector: 'app-image-gallery',
  templateUrl: './image-gallery.component.html',
  styleUrls: ['./image-gallery.component.scss']
})
export class ImageGalleryComponent implements OnInit, OnDestroy {
  @Input() creativeData!: CreativeImageData;
  @Input() title: string = 'Creative Assets';
  @Input() maxImages: number = 6;

  images: ImageItem[] = [];
  generationInProgress = false;
  
  // Nova-style carousel properties
  currentSlide = 0;
  slideWidth = 33.333; // Show 3 cards at a time like Nova
  imagesPerSlide = 3;
  maxSlides = 0;
  dots: number[] = [];
  
  // Modal and hover properties
  showImageModal = false;
  selectedImage?: ImageItem;
  selectedImageIndex = -1;
  hoveredCardIndex = -1;
  
  private destroy$ = new Subject<void>();
  private awsConfig: any;
  private eventsClient: any;
  private subscriptions: any[] = [];
  private appSyncEventSocket?: WebSocket;
  private appSyncEventsEndpoint?: string;

  constructor(
    private cdr: ChangeDetectorRef,
    private awsConfigService: AwsConfigService,
    private imageStatusService: ImageStatusService
  ) { 
    this.updateCarouselSettings();
    window.addEventListener('resize', () => this.updateCarouselSettings());
  }

      async   ngOnInit() {
        
        // Configure Amplify with AWS config
        await this.configureAmplify();

        // if (this.creativeData) {
        //     
        // }

                 if (this.creativeData && this.hasImageData()) {
            this.initializeImages();
            this.startPollingForUpdates();
        } else {
            
            // if (this.creativeData) {
            // }
        }
    }

  ngOnDestroy() {
    window.removeEventListener('resize', () => this.updateCarouselSettings());
    
    // Clean up any active subscriptions
    this.subscriptions.forEach(sub => {
      if (sub && typeof sub.unsubscribe === 'function') {
        sub.unsubscribe();
      }
    });
    this.subscriptions = [];

    // Clean up image status service polling
    this.imageStatusService.cleanup();

    this.destroy$.next();
    this.destroy$.complete();
  }

  private async configureAmplify() {
    try {
      this.awsConfig = await this.awsConfigService.getAwsConfig();

      if (this.awsConfig && this.awsConfig.appsyncEvents) {
        // Configure Amplify with AppSync Events API
        Amplify.configure({
          API: {
            Events: {
              endpoint: this.awsConfig.appsyncEvents.eventsEndpoint,
              region: this.awsConfig.region,
              defaultAuthMode: 'userPool'
            }
          },
          Auth: {
            Cognito: {
              userPoolId: this.awsConfig.userPoolId,
              userPoolClientId: this.awsConfig.userPoolClientId,
              identityPoolId: this.awsConfig.identityPoolId
            }
          }
        });

      } else {
        
      }
    } catch (error) {
      
    }
  }

  public hasImageData(): boolean {
    const imagery = this.creativeData?.imagery || [];
    const creatives = this.creativeData?.creatives || [];
    const imageDescriptions = this.creativeData?.imageDescriptions || [];
    
    // console.log('🔍 hasImageData() checking:', {
    //   imagery: imagery.length,
    //   creatives: creatives.length,
    //   imageDescriptions: imageDescriptions.length,
    //   total: imagery.length + creatives.length + imageDescriptions.length
    // });
    
    return imagery.length > 0 || creatives.length > 0 || imageDescriptions.length > 0;
  }

  private getImageDescriptions(): any[] {
    // Support both legacy 'imagery' and new 'imageDescriptions' properties
    const creatives = this.creativeData?.creatives || [];
    const imagery = this.creativeData?.imagery || [];
    const imageDescriptions = this.creativeData?.imageDescriptions || [];
    
    // 
    // Return the first non-empty array, prioritizing creatives, then imagery, then imageDescriptions
    if (creatives.length > 0) {
      return creatives;
    } else if (imagery.length > 0) {
      return imagery;
    } else if (imageDescriptions.length > 0) {
      return imageDescriptions;
    } else {
      
      return [];
    }
  }

      private initializeImages() {
        
        if (!this.creativeData) {
          
            return;
        }
        
        if (!this.hasImageData()) {
        //    console.warn('⚠️ hasImageData() returned false');
            return;
        }
        
        // Get image descriptions from either property
        const descriptions = this.getImageDescriptions();
        
        const slicedDescriptions = descriptions.slice(0, this.maxImages);

        this.images = slicedDescriptions.map((image, index) => {
            const imageItem = {
                id: image.imageId || image.content_id || `img-${Date.now()}-${index}`,
                description: image.description || `Image ${index + 1}`,
                status: image.status || 'pending',
                progress: image.progress || 0
            };
            return imageItem;
        });

        // Initialize carousel properties
        this.maxSlides = Math.ceil(this.images.length / this.imagesPerSlide);
        this.dots = Array(this.maxSlides).fill(0).map((_, i) => i);
        this.currentSlide = 0;

        // 
    }

    private startPollingForUpdates() {
        // Start polling for image status updates
        this.pollImageStatus();
        
        // Set up periodic polling every 3 seconds
        const pollingInterval = setInterval(() => {
            this.pollImageStatus();
        }, 3000);

        // Clean up interval when component is destroyed
        this.destroy$.subscribe(() => {
            clearInterval(pollingInterval);
        });
    }

    private async pollImageStatus() {
        if (!this.images.length) return;

        for (const image of this.images) {
            if (image.status === 'pending' || image.status === 'generating') {
                try {
                    await this.checkImageStatus(image);
                } catch (error) {
            
                }
            }
        }
    }

    private async checkImageStatus(image: ImageItem) {
        try {
            // Check real DynamoDB status using the image status service
            const status: ImageStatusResponse | null = await this.imageStatusService.checkImageStatus(image.id);
            
            if (status) {
                // Find the actual image in the array to ensure we're updating the right reference
                const imageIndex = this.images.findIndex(img => img.id === image.id);
                if (imageIndex === -1) {
                    
                    return;
                }

                const imageToUpdate = this.images[imageIndex];
                
                // Map 'failed' to 'error' status
                const mappedStatus = status.status === 'failed' ? 'error' : status.status;
                imageToUpdate.status = mappedStatus;
                
                if (status.status === 'completed') {
                    imageToUpdate.progress = 100;
                    imageToUpdate.url = status.thumbnail_url || status.original_url;
                    imageToUpdate.fullImageUrl = status.original_url;
                    imageToUpdate.thumbnailUrl = status.thumbnail_url;
                    // 
                } else if (status.status === 'generating') {
                    // For generating status, estimate progress based on time elapsed
                    if (status.created_date) {
                        const createdTime = new Date(status.created_date).getTime();
                        const now = new Date().getTime();
                        const elapsed = now - createdTime;
                        // Estimate progress: assume 60 seconds for full generation
                        imageToUpdate.progress = Math.min(Math.floor((elapsed / 60000) % 100), 95);
                    } else {
                        imageToUpdate.progress = Math.max(imageToUpdate.progress, 20); // Show some progress
                    }
                } else if (status.status === 'failed') {
                    imageToUpdate.status = 'error';
                    imageToUpdate.error = status.error_message || 'Image generation failed';
                    //.error(`❌ Image ${image.id} failed:`, status.error_message);
                } else if (status.status === 'pending') {
                    imageToUpdate.progress = 0;
                }
                
                // Force change detection
                this.cdr.detectChanges();
            } else {
                // No status found in DynamoDB yet - keep current status
            }
        } catch (error) {
         
            // Don't change status on error, just log it
        }
    }

  private async startImageGeneration() {
    if (!this.images.length) return;

    this.generationInProgress = true;

    // Generate images sequentially to avoid overwhelming the service
    for (let i = 0; i < this.images.length; i++) {
      await this.generateImage(i);

      // Add a small delay between generations
      if (i < this.images.length - 1) {
        await this.delay(1000);
      }
    }

    this.generationInProgress = false;
  }

  private async generateImage(index: number): Promise<void> {
    const image = this.images[index];
    if (!image) return;

    try {
      // Update status to generating
      this.updateImageStatus(index, 'generating', 0);

      // Build the prompt with color scheme if available
      let prompt = image.description;

      // Add color scheme to prompt if available
      if (this.creativeData?.colors && this.creativeData.colors.length > 0) {
        const colorString = this.creativeData.colors.join(', ');
        prompt += ` Color scheme: ${colorString}.`;
      }

      // Add theme/style if available
      if (this.creativeData?.theme) {
        prompt += ` Theme: ${this.creativeData.theme}.`;
      }
      if (this.creativeData?.style) {
        prompt += ` Style: ${this.creativeData.style}.`;
      }

      // Add quality directives
      prompt += ' High quality, professional advertising creative, clean composition, commercial photography style.';

      // Simulate the image generation request (replace with actual AppSync/API call)
      const result = await this.callImageGenerationAPI(prompt, image.id!);
      if (result.success && result.imageId) {
        // Subscribe to real-time status updates
        //this.subscribeToImageEvents(result.imageId);
      } else {
        this.updateImageStatus(index, 'error', 0, result.error);
      }

    } catch (error) {
     
      this.updateImageStatus(index, 'error', 0, error instanceof Error ? error.message : 'Unknown error');
    }
  }

  private async callImageGenerationAPI(prompt: string, imageId: string): Promise<{ success: boolean, imageId?: string, error?: string }> {
    try {
      if (!this.awsConfig || !this.awsConfig.appsyncEvents) {
        await this.configureAmplify();
      }

      let awsConfigApiConfig = this.awsConfig.appsyncEvents;
      try {
        // Prepare the payload for the image generation event
        const payload = {
          imageId: imageId,
          prompt: prompt,
          colorScheme: this.getColorPalette(),
          width: 1024,
          height: 1024,
          timestamp: new Date().toISOString()
        };

        let url = `https://${awsConfigApiConfig.eventsEndpoint}/event`;
        /*await fetch(url, {
          method: "POST",
          headers: {
            'Content-Type': 'application/json',
            'Authorization': await this.getAuthorizationHeader(url, JSON.stringify(payload))
          },
          body: JSON.stringify({
            channel:"images",
            events: [
              JSON.stringify(payload)
            ]
          })
        });*/

        // Publish the event using Amplify Events API
        //await events.post(channel, payload);

        //og('✅ Image generation request published successfully via Amplify');
        return { success: true, imageId: imageId };

      } catch (amplifyError: any) {
       
        return { success: false, error: `Amplify Events Error: ${amplifyError.message || amplifyError}` };
      }

    } catch (error) {
    
      return {
        success: false,
        error: error instanceof Error ? error.message : 'API call failed'
      };
    }
  }

  // Fallback simulation method for development/testing
  private async simulateImageGeneration(imageId: string): Promise<{ success: boolean, imageId?: string, error?: string }> {

    // Simulate API delay
    await this.delay(500);

    // Simulate 90% success rate
    if (Math.random() > 0.1) {
      return {
        success: true,
        imageId: imageId
      };
    } else {
      return {
        success: false,
        error: 'Simulated generation failure'
      };
    }
  }

  private subscribeToImageEvents(imageId: string) {
    if (!this.awsConfig?.appsyncEvents?.realtimeEndpoint) {
    
      this.simulateImageProgress(imageId);
      return;
    }

    try {
      // Subscribe to real-time events for this specific image
      const wsUrl = `${this.awsConfig.appsyncEvents.realtimeEndpoint}/event/images/${imageId}`;
      this.appSyncEventSocket = new WebSocket(wsUrl);

      this.appSyncEventSocket.onopen = () => {
      };

      this.appSyncEventSocket.onmessage = (event) => {
        try {
          const eventData = JSON.parse(event.data);
          this.handleImageStatusUpdate(imageId, eventData);
        } catch (error) {
     
        }
      };

      this.appSyncEventSocket.onerror = (error) => {
     
        // Fall back to simulation on error
        this.simulateImageProgress(imageId);
      };

      this.appSyncEventSocket.onclose = () => {
      };

    } catch (error) {
  
      this.simulateImageProgress(imageId);
    }
  }

  private handleImageStatusUpdate(imageId: string, eventData: any) {
    const image = this.images.find(img => img.id === imageId);
    if (!image) return;

    image.status = eventData.status || image.status;
    image.progress = eventData.progress || image.progress;

    if (eventData.status === 'completed') {
      image.fullImageUrl = eventData.fullImageUrl;
      image.url = eventData.fullImageUrl;
      image.thumbnailUrl = eventData.thumbnailUrl;
    } else if (eventData.status === 'error') {
      image.error = eventData.error || 'Unknown error';
      
    }

    this.cdr.detectChanges();
  }

  
  private simulateImageProgress(imageId: string) {
    // Simulate progress updates for development/fallback
    const image = this.images.find(img => img.id === imageId);
    if (!image) return;

    let progress = 0;
    const progressInterval = setInterval(() => {
      progress += Math.random() * 20;
      if (progress >= 100) {
        progress = 100;
        image.status = 'completed';
        image.url = 'https://via.placeholder.com/400x300?text=Generated+Image';
        image.fullImageUrl = image.url;
        image.thumbnailUrl = image.url;
        clearInterval(progressInterval);
      } else {
        image.status = 'generating';
      }
      image.progress = Math.min(progress, 100);
      this.cdr.detectChanges();
    }, 1000);

    // Clean up interval when component is destroyed
    this.destroy$.subscribe(() => clearInterval(progressInterval));
  }

  private updateImageStatus(index: number, status: ImageItem['status'], progress: number = 0, error?: string) {
    if (this.images[index]) {
      this.images[index].status = status;
      this.images[index].progress = progress;
      if (error) {
        this.images[index].error = error;
      }
      this.cdr.detectChanges();
    }
  }

  private updateImageUrls(index: number, thumbnailUrl?: string, fullImageUrl?: string) {
    if (this.images[index]) {
      if (thumbnailUrl) this.images[index].thumbnailUrl = thumbnailUrl;
      if (fullImageUrl) this.images[index].fullImageUrl = fullImageUrl;
      this.cdr.detectChanges();
    }
  }

  private delay(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  // Public methods for template
  getStatusIcon(status: ImageItem['status']): string {
    switch (status) {
      case 'pending': return 'schedule';
      case 'generating': return 'autorenew';
      case 'completed': return 'check_circle';
      case 'error': return 'error';
      default: return 'help';
    }
  }

  getStatusText(status: ImageItem['status']): string {
    switch (status) {
      case 'pending': return 'Queued';
      case 'generating': return 'Generating...';
      case 'completed': return 'Complete';
      case 'error': return 'Failed';
      default: return 'Unknown';
    }
  }

  getStatusColor(status: ImageItem['status']): string {
    switch (status) {
      case 'pending': return '#6b7280';
      case 'generating': return '#3b82f6';
      case 'completed': return '#10b981';
      case 'error': return '#ef4444';
      default: return '#6b7280';
    }
  }

  openFullImage(image: ImageItem) {
    const index = this.images.findIndex(img => img.id === image.id);
    this.openImageModal(image, index);
  }

  retryImage(index: number) {
    if (this.images[index]) {
      this.images[index].status = 'pending';
      this.images[index].progress = 0;
      this.images[index].error = undefined;
      this.generateImage(index);
    }
  }

  // Color palette display
  getColorPalette(): string[] {
    return this.creativeData?.colorPalette || this.creativeData?.colors || [];
  }

  copyColorToClipboard(color: string) {
    navigator.clipboard.writeText(color).then(() => {
    }).catch(err => {
  
    });
  }

  // Summary methods for footer
  getCompletedCount(): number {
    return this.images.filter(img => img.status === 'completed').length;
  }

  getGeneratingCount(): number {
    return this.images.filter(img => img.status === 'generating').length;
  }

  getErrorCount(): number {
    return this.images.filter(img => img.status === 'error').length;
  }

  retryAllErrors() {
    this.images.forEach((image, index) => {
      if (image.status === 'error') {
        this.retryImage(index);
      }
    });
  }

  // Nova-style carousel navigation methods
  nextSlide() {
    if (this.currentSlide < this.maxSlides - 1) {
      this.currentSlide++;
    }
  }

  previousSlide() {
    if (this.currentSlide > 0) {
      this.currentSlide--;
    }
  }

  goToSlide(slideIndex: number) {
    if (slideIndex >= 0 && slideIndex < this.maxSlides) {
      this.currentSlide = slideIndex;
    }
  }

  // Nova-style "Try the prompt" functionality
  tryPrompt(image: ImageItem) {
    if (image.status === 'completed') {
      // Copy the prompt to clipboard and show feedback
      const prompt = this.buildPromptForImage(image);
      navigator.clipboard.writeText(prompt).then(() => {
        // You could add a toast notification here
      }).catch(err => {
     
      });
    }
  }

  private buildPromptForImage(image: ImageItem): string {
    let prompt = image.description;

    // Add color scheme to prompt if available
    if (this.creativeData?.colors && this.creativeData.colors.length > 0) {
      const colorString = this.creativeData.colors.join(', ');
      prompt += ` Color scheme: ${colorString}.`;
    }

    // Add theme/style if available
    if (this.creativeData?.theme) {
      prompt += ` Theme: ${this.creativeData.theme}.`;
    }
    if (this.creativeData?.style) {
      prompt += ` Style: ${this.creativeData.style}.`;
    }

    // Add quality directives
    prompt += ' High quality, professional advertising creative, clean composition, commercial photography style.';

    return prompt;
  }

  private updateCarouselSettings() {
    const width = window.innerWidth;
    
    if (width <= 480) {
      this.imagesPerSlide = 1;
      this.slideWidth = 100;
    } else if (width <= 768) {
      this.imagesPerSlide = 1;
      this.slideWidth = 100;
    } else if (width <= 1024) {
      this.imagesPerSlide = 2;
      this.slideWidth = 50;
    } else {
      this.imagesPerSlide = 3;
      this.slideWidth = 33.333;
    }
    
    // Recalculate carousel properties if images are already initialized
    if (this.images.length > 0) {
      this.maxSlides = Math.ceil(this.images.length / this.imagesPerSlide);
      this.dots = Array(this.maxSlides).fill(0).map((_, i) => i);
      
      // Ensure current slide is within bounds
      if (this.currentSlide >= this.maxSlides) {
        this.currentSlide = Math.max(0, this.maxSlides - 1);
      }
      
      this.cdr.detectChanges();
    }
  }

  // Modal functionality
  openImageModal(image: ImageItem, index: number) {
    if (image.status === 'completed') {
      this.selectedImage = image;
      this.selectedImageIndex = index;
      this.showImageModal = true;
      document.body.classList.add('modal-open');
    }
  }

  closeImageModal() {
    this.showImageModal = false;
    this.selectedImage = undefined;
    this.selectedImageIndex = -1;
    document.body.classList.remove('modal-open');
  }

  downloadImage(image: ImageItem) {
    if (image.status === 'completed' && (image.fullImageUrl || image.url)) {
      const link = document.createElement('a');
      link.href = image.fullImageUrl || image.url!;
      link.download = `generated-image-${image.id}.jpg`;
      link.target = '_blank';
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    }
  }

  handleImageError(image: ImageItem) {
    console.error('❌ Image failed to load:', {
      id: image.id,
      thumbnailUrl: image.thumbnailUrl,
      url: image.url,
      fullImageUrl: image.fullImageUrl,
      status: image.status
    });
    
    // Optionally, you could set an error status or retry the image
    // const imageIndex = this.images.findIndex(img => img.id === image.id);
    // if (imageIndex !== -1) {
    //   this.images[imageIndex].status = 'error';
    //   this.images[imageIndex].error = 'Failed to load image';
    //   this.cdr.detectChanges();
    // }
  }

  // Debug method to help troubleshoot image loading issues
  debugImages() {
    /*console.log('🔍 Current images state:', this.images.map(img => ({
      id: img.id,
      status: img.status,
      progress: img.progress,
      hasUrl: !!img.url,
      hasThumbnailUrl: !!img.thumbnailUrl,
      hasFullImageUrl: !!img.fullImageUrl,
      thumbnailUrl: img.thumbnailUrl,
      url: img.url,
      fullImageUrl: img.fullImageUrl,
      error: img.error
    })));*/
  }

  // Manual refresh method for specific image
  async refreshImage(imageId: string) {
    const image = this.images.find(img => img.id === imageId);
    if (image) {
      await this.checkImageStatus(image);
    }
  }
}