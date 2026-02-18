import { Injectable } from '@angular/core';
import { Observable, BehaviorSubject, timer } from 'rxjs';
import { switchMap, catchError } from 'rxjs/operators';
import { AwsConfigService } from './aws-config.service';

// AWS SDK v3 imports for DynamoDB
import { DynamoDBClient, GetItemCommand, BatchGetItemCommand } from '@aws-sdk/client-dynamodb';

export interface ImageStatusResponse {
  content_id: string;
  content_type: string;
  status: 'pending' | 'generating' | 'completed' | 'failed';
  original_url?: string;
  thumbnail_url?: string;
  prompt?: string;
  key?: string;
  bucket?: string;
  created_date?: string;
  updated_date?: string;
  error_message?: string;
}

@Injectable({
  providedIn: 'root'
})
export class ImageStatusService {
  private dynamoClient: DynamoDBClient | null = null;
  private awsConfig: any;
  private statusCache = new Map<string, ImageStatusResponse>();
  private pollingSubjects = new Map<string, BehaviorSubject<ImageStatusResponse | null>>();
  private clientInitialized = false;

  constructor(private awsConfigService: AwsConfigService) {
    this.initializeClient();
  }

  private async initializeClient(): Promise<void> {
    try {
      this.awsConfig = await this.awsConfigService.getAwsConfig();
      await this.setupDynamoClient();
    } catch (error) {
      console.error('Failed to initialize AWS config and DynamoDB client:', error);
    }
  }

  private async setupDynamoClient(): Promise<void> {
    try {
      if (!this.awsConfig) {
        console.warn('AWS config not available for DynamoDB client setup');
        return;
      }

      if (!this.awsConfig.credentials?.accessKeyId || !this.awsConfig.credentials?.secretAccessKey) {
        console.warn('AWS credentials not available for DynamoDB client');
        return;
      }

      this.dynamoClient = new DynamoDBClient({
        region: this.awsConfig.region || 'us-east-1',
        credentials: {
          accessKeyId: this.awsConfig.credentials.accessKeyId,
          secretAccessKey: this.awsConfig.credentials.secretAccessKey,
          sessionToken: this.awsConfig.credentials.sessionToken || undefined
        }
      });

      this.clientInitialized = true;
    } catch (error) {
      
      this.clientInitialized = false;
    }
  }

  /**
   * Check status of a single image by content ID
   */
  async checkImageStatus(contentId: string): Promise<ImageStatusResponse | null> {
    try {
      if (!this.clientInitialized || !this.dynamoClient) {
        console.warn('DynamoDB client not initialized, attempting to initialize...');
        await this.initializeClient();
        
        if (!this.clientInitialized || !this.dynamoClient) {
          console.error('Failed to initialize DynamoDB client');
          return null;
        }
      }

      let configs = this.awsConfigService.getConfig();
      if (!configs?.bedrock?.creativesDynamoDBTable) {
        console.warn('DynamoDB table name not configured');
        return null;
      }

      const command = new GetItemCommand({
        TableName: configs.bedrock.creativesDynamoDBTable,
        Key: {
          content_id: {
            S: contentId
          }
        }
      });

      const response = await this.dynamoClient.send(command);

      if (response.Item) {
        
        // Convert DynamoDB item format to our interface
        const statusValue = response.Item['status']?.S || 'pending';
        const validStatus = ['pending', 'generating', 'completed', 'failed'].includes(statusValue) 
          ? statusValue as 'pending' | 'generating' | 'completed' | 'failed'
          : 'pending';

        const imageStatus: ImageStatusResponse = {
          content_id: (response.Item['content_id']?.S || response.Item['imageId']?.S) || contentId,
          content_type: response.Item['content_type']?.S || 'image',
          status: validStatus,
          original_url: response.Item['original_url']?.S,
          thumbnail_url: response.Item['thumbnail_url']?.S,
          prompt: response.Item['prompt']?.S,
          key: response.Item['key']?.S,
          bucket: response.Item['bucket']?.S,
          created_date: response.Item['created_date']?.S,
          updated_date: response.Item['updated_date']?.S,
          error_message: response.Item['error_message']?.S
        };
        
        this.statusCache.set(contentId, imageStatus);
        return imageStatus;
      }

      return null;
    } catch (error) {
      console.error(`Error checking status for image ${contentId}:`, error);
      return null;
    }
  }

  /**
   * Start polling for status updates on a specific image
   */
  pollImageStatus(contentId: string, intervalMs: number = 3000): Observable<ImageStatusResponse | null> {
    // Return existing polling subject if already exists
    if (this.pollingSubjects.has(contentId)) {
      return this.pollingSubjects.get(contentId)!.asObservable();
    }

    // Create new polling subject
    const subject = new BehaviorSubject<ImageStatusResponse | null>(null);
    this.pollingSubjects.set(contentId, subject);

    // Start polling
    const polling$ = timer(0, intervalMs).pipe(
      switchMap(async () => {
        const status = await this.checkImageStatus(contentId);
        subject.next(status);
        
        // Stop polling if image is completed or failed
        if (status && (status.status === 'completed' || status.status === 'failed')) {
          this.stopPolling(contentId);
        }
        
        return status;
      }),
      catchError(error => {
        console.error(`Polling error for image ${contentId}:`, error);
        subject.error(error);
        this.stopPolling(contentId);
        return [];
      })
    );

    polling$.subscribe();
    return subject.asObservable();
  }

  /**
   * Stop polling for a specific image
   */
  stopPolling(contentId: string) {
    const subject = this.pollingSubjects.get(contentId);
    if (subject) {
      subject.complete();
      this.pollingSubjects.delete(contentId);
    }
  }

  /**
   * Batch check status for multiple images
   */
  async checkMultipleImageStatus(contentIds: string[]): Promise<Map<string, ImageStatusResponse>> {
    const results = new Map<string, ImageStatusResponse>();
    
    try {
      if (!this.clientInitialized || !this.dynamoClient) {
        console.warn('DynamoDB client not initialized, attempting to initialize...');
        await this.initializeClient();
        
        if (!this.clientInitialized || !this.dynamoClient) {
          console.error('Failed to initialize DynamoDB client');
          return results;
        }
      }

      if (!this.awsConfig?.bedrock?.creativesDynamoDBTable) {
        console.warn('DynamoDB table name not configured');
        return results;
      }

      // Use BatchGetItem for multiple keys
      const command = new BatchGetItemCommand({
        RequestItems: {
          [this.awsConfig.bedrock.creativesDynamoDBTable]: {
            Keys: contentIds.map(id => ({
              content_id: { S: id }
            }))
          }
        }
      });

      const response = await this.dynamoClient.send(command);

      if (response.Responses?.[this.awsConfig.creativesDynamoDBTable]) {
        const items = response.Responses[this.awsConfig.creativesDynamoDBTable];
        
        items.forEach((item: any) => {
          const statusValue = item['status']?.S || 'pending';
          const validStatus = ['pending', 'generating', 'completed', 'failed'].includes(statusValue) 
            ? statusValue as 'pending' | 'generating' | 'completed' | 'failed'
            : 'pending';

          const imageStatus: ImageStatusResponse = {
            content_id: item['content_id']?.S || '',
            content_type: item['content_type']?.S || 'image',
            status: validStatus,
            original_url: item['original_url']?.S,
            thumbnail_url: item['thumbnail_url']?.S,
            prompt: item['prompt']?.S,
            key: item['key']?.S,
            bucket: item['bucket']?.S,
            created_date: item['created_date']?.S,
            updated_date: item['updated_date']?.S,
            error_message: item['error_message']?.S
          };
          
          results.set(imageStatus.content_id, imageStatus);
          this.statusCache.set(imageStatus.content_id, imageStatus);
        });
      }

      return results;
    } catch (error) {
      console.error('Error checking multiple image status:', error);
      return results;
    }
  }

  /**
   * Get cached status if available
   */
  getCachedStatus(contentId: string): ImageStatusResponse | null {
    return this.statusCache.get(contentId) || null;
  }

  /**
   * Clear cache for specific image
   */
  clearCache(contentId?: string) {
    if (contentId) {
      this.statusCache.delete(contentId);
    } else {
      this.statusCache.clear();
    }
  }

  /**
   * Clean up all polling subscriptions
   */
  cleanup() {
    this.pollingSubjects.forEach((subject, contentId) => {
      subject.complete();
    });
    this.pollingSubjects.clear();
    this.statusCache.clear();
  }
} 