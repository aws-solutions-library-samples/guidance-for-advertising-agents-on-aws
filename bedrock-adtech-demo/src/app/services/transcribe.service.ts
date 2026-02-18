import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { AwsConfigService } from './aws-config.service';

// AWS SDK v3 imports for Transcribe Streaming
import {
  TranscribeStreamingClient,
  StartStreamTranscriptionCommand,
  LanguageCode,
  MediaEncoding,
  AudioStream
} from '@aws-sdk/client-transcribe-streaming';

interface TranscriptionEvent {
  type: 'partial' | 'final' | 'error' | 'complete';
  text: string;
  timestamp: Date;
  confidence?: number;
  isPartial?: boolean;
}

@Injectable({
  providedIn: 'root'
})
export class TranscribeService {
  private isRecording = false;
  private audioStream: MediaStream | null = null;
  private recognition: any = null;
  private transcribeClient: TranscribeStreamingClient | null = null;
  private clientInitialized = false;
  private audioContext: AudioContext | null = null;
  private currentObserver: any = null;
  private accumulatedText = '';
  private currentPartialText = '';

  constructor(private awsConfig: AwsConfigService) {
    this.initializeWebSpeechAPI();
    this.initializeTranscribeClient();
  }

  private initializeWebSpeechAPI(): void {
    // Use Web Speech API as fallback
    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;

    if (SpeechRecognition) {
      this.recognition = new SpeechRecognition();
      this.recognition.continuous = true;
      this.recognition.interimResults = true;
      this.recognition.lang = 'en-US';
    }
  }

  private async initializeTranscribeClient(): Promise<void> {
    try {
      // Wait for AWS config to be loaded
      this.awsConfig.config$.subscribe(config => {
        if (config && this.awsConfig.isAuthenticated()) {
          this.setupTranscribeClient();
        }
      });

      this.awsConfig.user$.subscribe(user => {
        if (user) {
          this.setupTranscribeClient();
        }
      });
    } catch (error) {
      console.error('Error initializing Transcribe client:', error);
    }
  }

  private async setupTranscribeClient(): Promise<void> {
    try {
      if (this.clientInitialized) {
        return; // Already initialized, skip setup
      }

      const awsConfig = await this.awsConfig.getAwsConfig();
      if (!awsConfig || !awsConfig.credentials) {
        console.log('AWS credentials not available for Transcribe, using Web Speech API fallback');
        return;
      }

      this.transcribeClient = new TranscribeStreamingClient({
        region: awsConfig.region,
        credentials: awsConfig.credentials
      });

      this.clientInitialized = true;

    } catch (error) {
      console.error('Error setting up AWS Transcribe client:', error);
      this.clientInitialized = false;
    }
  }

  // Check if browser supports required APIs
  isSupported(): boolean {
    const hasSpeechRecognition = typeof (window as any).SpeechRecognition !== 'undefined' ||
      typeof (window as any).webkitSpeechRecognition !== 'undefined';
    const hasMediaDevices = navigator.mediaDevices && typeof navigator.mediaDevices.getUserMedia === 'function';
    return !!(hasMediaDevices && hasSpeechRecognition);
  }

  // Request microphone permissions
  async requestMicrophonePermission(): Promise<boolean> {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.getTracks().forEach(track => track.stop()); // Stop the stream immediately
      return true;
    } catch (error) {
      console.error('Microphone permission denied:', error);
      return false;
    }
  }

  // Start real-time transcription
  startTranscription(languageCode: string = 'en-US'): Observable<TranscriptionEvent> {
    return new Observable<TranscriptionEvent>(observer => {

      // Reset accumulated text for new session
      this.accumulatedText = '';
      this.currentPartialText = '';
      this.currentObserver = observer;

      this.startTranscriptionInternal(observer, languageCode).catch(error => {
        console.error('Error starting transcription:', error);
        observer.error(error);
      });
    });
  }

  private async startTranscriptionInternal(observer: any, languageCode: string): Promise<void> {
    try {

      if (!this.isSupported()) {
        throw new Error('Browser does not support required audio APIs');
      }

      // Ensure AWS Transcribe client is set up
      if (!this.clientInitialized || !this.transcribeClient) {
        
        await this.setupTranscribeClient();
      }

      // Try AWS Transcribe Streaming first if available
      if (this.clientInitialized && this.transcribeClient) {
        
        await this.startAWSTranscribeStreaming(observer, languageCode);
        return;
      }

      // Use Web Speech API as fallback
      if (!this.recognition) {
        throw new Error('Speech recognition not available');
      }

      await this.startWebSpeechAPI(observer, languageCode);

    } catch (error) {
      console.error('Error in transcription setup:', error);
      observer.error(error);
    }
  }

  // AWS Transcribe Streaming implementation
  private async startAWSTranscribeStreaming(observer: any, languageCode: string): Promise<void> {
    try {
      // Request microphone access
      this.audioStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          sampleRate: 16000,
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true
        }
      });

      // Set up audio context for processing
      this.audioContext = new AudioContext({ sampleRate: 16000 });
      const source = this.audioContext.createMediaStreamSource(this.audioStream);

      // Create script processor for audio data
      const processor = this.audioContext.createScriptProcessor(4096, 1, 1);

      // Create async generator for audio stream
      const audioStream = this.createAudioStream(processor);

      // Map language code to AWS format
      const awsLanguageCode = this.mapToAWSLanguageCode(languageCode);

      // Start AWS Transcribe streaming
      const command = new StartStreamTranscriptionCommand({
        LanguageCode: awsLanguageCode,
        MediaSampleRateHertz: 16000,
        MediaEncoding: MediaEncoding.PCM,
        AudioStream: audioStream
      });

      this.isRecording = true;

      // Connect audio processing
      source.connect(processor);
      processor.connect(this.audioContext.destination);

      const response = await this.transcribeClient!.send(command);

      // Process transcription results
      if (response.TranscriptResultStream) {
        for await (const event of response.TranscriptResultStream) {
          // Check if we should stop
          if (!this.isRecording) {
            
            break;
          }

          if (event.TranscriptEvent?.Transcript?.Results) {
            for (const result of event.TranscriptEvent.Transcript.Results) {
              if (result.Alternatives && result.Alternatives.length > 0) {
                const alternative = result.Alternatives[0];
                const text = alternative.Transcript || '';
                const confidence = alternative.Items?.[0]?.Confidence || 0;

                if (result.IsPartial) {
                  // For partial results, show accumulated + current partial
                  this.currentPartialText = text;
                  const displayText = this.accumulatedText + (this.accumulatedText ? ' ' : '') + this.currentPartialText;

                  observer.next({
                    type: 'partial',
                    text: displayText,
                    timestamp: new Date(),
                    confidence,
                    isPartial: true
                  });
                } else {
                  // For final results, add to accumulated text
                  if (text.trim()) {
                    this.accumulatedText += (this.accumulatedText ? ' ' : '') + text.trim();
                    this.currentPartialText = '';

                    observer.next({
                      type: 'final',
                      text: this.accumulatedText,
                      timestamp: new Date(),
                      confidence,
                      isPartial: false
                    });
                  }
                }
              }
            }
          }
        }
      }

      // Complete the transcription if still recording
      if (this.isRecording) {
        observer.next({
          type: 'complete',
          text: this.accumulatedText || 'AWS Transcription complete',
          timestamp: new Date()
        });
        observer.complete();
      }

    } catch (error) {
      console.error('AWS Transcribe Streaming error:', error);
      // Fallback to Web Speech API
      this.startWebSpeechAPI(observer, languageCode);
    }
  }

  // Create audio stream for AWS Transcribe
  private async *createAudioStream(processor: ScriptProcessorNode): AsyncGenerator<AudioStream> {
    const audioChunks: AudioStream[] = [];
    let resolveNextChunk: ((value: AudioStream) => void) | null = null;

    processor.onaudioprocess = (event: AudioProcessingEvent) => {
      if (!this.isRecording) return;

      const inputBuffer = event.inputBuffer;
      const inputData = inputBuffer.getChannelData(0);

      // Convert float32 audio data to 16-bit PCM
      const pcmData = new Int16Array(inputData.length);
      for (let i = 0; i < inputData.length; i++) {
        const sample = Math.max(-1, Math.min(1, inputData[i]));
        pcmData[i] = sample < 0 ? sample * 0x8000 : sample * 0x7FFF;
      }

      // Convert to Uint8Array and wrap in AudioStream
      const uint8Data = new Uint8Array(pcmData.buffer);
      const audioStream: AudioStream = { AudioEvent: { AudioChunk: uint8Data } };

      if (resolveNextChunk) {
        resolveNextChunk(audioStream);
        resolveNextChunk = null;
      } else {
        audioChunks.push(audioStream);
      }
    };

    try {
      while (this.isRecording) {
        if (audioChunks.length > 0) {
          yield audioChunks.shift()!;
        } else {
          const nextChunk = await new Promise<AudioStream>((resolve) => {
            resolveNextChunk = resolve;
            // Add a simple timeout to prevent hanging
            setTimeout(() => {
              if (resolveNextChunk === resolve) {
                resolveNextChunk = null;
                resolve({ AudioEvent: { AudioChunk: new Uint8Array(0) } });
              }
            }, 200);
          });

          if (this.isRecording && nextChunk.AudioEvent?.AudioChunk && nextChunk.AudioEvent.AudioChunk.length > 0) {
            yield nextChunk;
          }
        }

        // Small delay to prevent overwhelming the stream
        await new Promise(resolve => setTimeout(resolve, 10));
      }
    } catch (error) {
      console.error('Error in audio stream:', error);
    } finally {
      
    }
  }

  // Map language codes to AWS format
  private mapToAWSLanguageCode(languageCode: string): LanguageCode {
    const mapping: { [key: string]: LanguageCode } = {
      'en-US': LanguageCode.EN_US,
      'en-GB': LanguageCode.EN_GB,
      'es-ES': LanguageCode.ES_ES,
      'es-US': LanguageCode.ES_US,
      'fr-FR': LanguageCode.FR_FR,
      'de-DE': LanguageCode.DE_DE,
      'it-IT': LanguageCode.IT_IT,
      'pt-BR': LanguageCode.PT_BR,
      'ja-JP': LanguageCode.JA_JP,
      'ko-KR': LanguageCode.KO_KR,
      'zh-CN': LanguageCode.ZH_CN
    };
    return mapping[languageCode] || LanguageCode.EN_US;
  }

  // Web Speech API implementation (fallback)
  private async startWebSpeechAPI(observer: any, languageCode: string): Promise<void> {
    if (!this.recognition) {
      throw new Error('Speech recognition not available');
    }

    this.recognition.lang = languageCode;
    this.isRecording = true;

    this.recognition.onresult = (event: any) => {
      try {
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const result = event.results[i];
          const transcript = result[0].transcript;
          const confidence = result[0].confidence || 0.9;

          if (result.isFinal) {
            // Final result - add to accumulated text
            if (transcript.trim()) {
              this.accumulatedText += (this.accumulatedText ? ' ' : '') + transcript.trim();
              this.currentPartialText = '';

              observer.next({
                type: 'final',
                text: this.accumulatedText,
                timestamp: new Date(),
                confidence: confidence,
                isPartial: false
              });
            }
          } else {
            // Partial result - update current partial text
            this.currentPartialText = transcript;
            const displayText = this.accumulatedText + (this.accumulatedText ? ' ' : '') + this.currentPartialText;

            observer.next({
              type: 'partial',
              text: displayText,
              timestamp: new Date(),
              confidence: confidence,
              isPartial: true
            });
          }
        }
      } catch (error) {
        console.error('Error processing speech recognition result:', error);
      }
    };

    this.recognition.onerror = (event: any) => {
      console.error('Speech recognition error:', event.error);
      observer.next({
        type: 'error',
        text: `Speech recognition error: ${event.error}`,
        timestamp: new Date()
      });
    };

    this.recognition.onend = () => {
      if (this.isRecording) {
        this.isRecording = false;
        observer.next({
          type: 'complete',
          text: this.accumulatedText || 'Transcription complete',
          timestamp: new Date()
        });
        observer.complete();
      }
    };

    this.recognition.start();
  }

  // Stop transcription
  stopTranscription(): void {
    
    this.isRecording = false;

    // Stop Web Speech API
    if (this.recognition) {
      try {
        this.recognition.stop();
      } catch (error) {
        console.warn('Error stopping speech recognition:', error);
      }
    }

    // Stop audio stream
    if (this.audioStream) {
      this.audioStream.getTracks().forEach(track => {
        track.stop();
        
      });
      this.audioStream = null;
    }

    // Close audio context
    if (this.audioContext) {
      this.audioContext.close().then(() => {
        
      }).catch(error => {
        console.warn('Error closing audio context:', error);
      });
      this.audioContext = null;
    }

    // Complete the observer if still active
    if (this.currentObserver && !this.currentObserver.closed) {
      try {
        this.currentObserver.next({
          type: 'complete',
          text: this.accumulatedText || 'Transcription stopped',
          timestamp: new Date()
        });
        this.currentObserver.complete();
      } catch (error) {
        console.warn('Error completing observer:', error);
      }
    }

    // Reset state
    this.currentObserver = null;
  }

  /* // Simulation mode for when speech recognition is not available
  private simulateTranscription(observer: any): void {
    
    observer.next({
      type: 'partial',
      text: 'Listening...',
      timestamp: new Date(),
      isPartial: true
    });

    // Simulate some transcription progress
    const simulatedPhrases = [
      'I want to',
      'I want to optimize',
      'I want to optimize my',
      'I want to optimize my campaign',
      'I want to optimize my campaign bidding strategy'
    ];

    let phraseIndex = 0;
    const interval = setInterval(() => {
      if (phraseIndex < simulatedPhrases.length - 1) {
        observer.next({
          type: 'partial',
          text: simulatedPhrases[phraseIndex],
          timestamp: new Date(),
          isPartial: true
        });
        phraseIndex++;
      } else {
        observer.next({
          type: 'final',
          text: simulatedPhrases[phraseIndex],
          timestamp: new Date(),
          isPartial: false
        });
        observer.next({
          type: 'complete',
          text: 'Transcription complete',
          timestamp: new Date()
        });
        observer.complete();
        clearInterval(interval);
      }
    }, 500);

    // Auto-stop after 10 seconds
    setTimeout(() => {
      clearInterval(interval);
      if (!observer.closed) {
        observer.next({
          type: 'complete',
          text: 'Transcription timeout',
          timestamp: new Date()
        });
        observer.complete();
      }
    }, 10000);
  } */

  // Check if currently recording
  getIsRecording(): boolean {
    return this.isRecording;
  }

  // Get current accumulated text
  getCurrentTranscript(): string {
    const fullText = this.accumulatedText + (this.accumulatedText && this.currentPartialText ? ' ' : '') + this.currentPartialText;
    return fullText.trim();
  }

  // Get only the accumulated final text
  getFinalTranscript(): string {
    return this.accumulatedText.trim();
  }

  // Get supported language codes
  getSupportedLanguages(): { code: string; name: string }[] {
    return [
      { code: 'en-US', name: 'English (US)' },
      { code: 'en-GB', name: 'English (UK)' },
      { code: 'es-ES', name: 'Spanish (Spain)' },
      { code: 'es-US', name: 'Spanish (US)' },
      { code: 'fr-FR', name: 'French' },
      { code: 'de-DE', name: 'German' },
      { code: 'it-IT', name: 'Italian' },
      { code: 'pt-BR', name: 'Portuguese (Brazil)' },
      { code: 'ja-JP', name: 'Japanese' },
      { code: 'ko-KR', name: 'Korean' },
      { code: 'zh-CN', name: 'Chinese (Simplified)' }
    ];
  }
} 