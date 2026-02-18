import { Injectable } from '@angular/core';
import { CloudWatchLogsClient, PutLogEventsCommand, CreateLogStreamCommand } from '@aws-sdk/client-cloudwatch-logs';
import { AwsConfigService } from './aws-config.service';

export interface DemoSessionData {
  demoUser: string;
  customerName: string;
  sessionDate: string;
  url: string;
}

export interface DemoScenarioData {
  demoUser: string;
  customerName: string;
  prompt: string;
  agentName: string;
  agentId: string;
  timestamp: string;
}

@Injectable({
  providedIn: 'root'
})
export class CloudWatchService {
  private cloudWatchClient: CloudWatchLogsClient | null = null;
  private logGroupName: string = '';
  private isInitialized = false;

  constructor(private awsConfig: AwsConfigService) { }

  private async initializeClient(): Promise<void> {
    if (this.isInitialized) return;

    try {
      const awsConfiguration = await this.awsConfig.getAwsConfig();
      const config = this.awsConfig.getConfig();

      if (!awsConfiguration?.credentials || !config?.aws?.region) {
        throw new Error('AWS credentials or region not available');
      }

      this.cloudWatchClient = new CloudWatchLogsClient({
        region: config.aws.region,
        credentials: awsConfiguration.credentials
      });

      // Get log group name from config or construct it
      this.logGroupName = config.demoLogGroupName || `${config.stackPrefix || 'sim'}-demos-${config.stackSuffix || 'default'}`;

      this.isInitialized = true;
    } catch (error) {
      console.error('❌ Failed to initialize CloudWatch service:', error);
      throw error;
    }
  }

  private async ensureLogStream(streamName: string): Promise<void> {
    if (!this.cloudWatchClient) {
      throw new Error('CloudWatch client not initialized');
    }

    try {
      const createStreamCommand = new CreateLogStreamCommand({
        logGroupName: this.logGroupName,
        logStreamName: streamName
      });

      await this.cloudWatchClient.send(createStreamCommand);
    } catch (error: any) {
      // Stream might already exist, which is fine
      if (error.name !== 'ResourceAlreadyExistsException') {
        console.error('❌ Failed to create log stream:', error);
        throw error;
      }
    }
  }

  async logDemoSession(sessionData: DemoSessionData): Promise<void> {
    try {
      await this.initializeClient();

      const streamName = `demo-sessions-${new Date().toISOString().split('T')[0]}`;
      await this.ensureLogStream(streamName);

      const logEvent = {
        timestamp: Date.now(),
        message: JSON.stringify({
          eventType: 'demo-session-start',
          demoUser: sessionData.demoUser,
          customerName: sessionData.customerName,
          sessionDate: sessionData.sessionDate,
          url: sessionData.url,
          timestamp: new Date().toISOString()
        })
      };

      const putLogEventsCommand = new PutLogEventsCommand({
        logGroupName: this.logGroupName,
        logStreamName: streamName,
        logEvents: [logEvent]
      });

      await this.cloudWatchClient!.send(putLogEventsCommand);
    } catch (error) {
      console.error('❌ Failed to log demo session:', error);
      // Don't throw error to avoid disrupting user experience
    }
  }

  async logDemoScenario(scenarioData: DemoScenarioData): Promise<void> {
    try {
      await this.initializeClient();

      const streamName = `demo-scenarios-${new Date().toISOString().split('T')[0]}`;
      await this.ensureLogStream(streamName);

      const logEvent = {
        timestamp: Date.now(),
        message: JSON.stringify({
          eventType: 'demo-scenario',
          demoUser: scenarioData.demoUser,
          customerName: scenarioData.customerName,
          prompt: scenarioData.prompt,
          agentName: scenarioData.agentName,
          agentId: scenarioData.agentId,
          timestamp: scenarioData.timestamp
        })
      };

      const putLogEventsCommand = new PutLogEventsCommand({
        logGroupName: this.logGroupName,
        logStreamName: streamName,
        logEvents: [logEvent]
      });

      await this.cloudWatchClient!.send(putLogEventsCommand);
    } catch (error) {
      console.error('❌ Failed to log demo scenario:', error);
      // Don't throw error to avoid disrupting user experience
    }
  }
}