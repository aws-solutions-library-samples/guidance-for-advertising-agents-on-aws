import { Injectable } from '@angular/core';
import jsPDF from 'jspdf';
import html2canvas from 'html2canvas';

export interface ChatExportOptions {
  includeTimestamps?: boolean;
  includeVisualizations?: boolean;
  includeThinkingProcess?: boolean;
  paperSize?: 'a4' | 'letter'|'no-size';
  orientation?: 'portrait' | 'landscape';
  title?: string;
  subtitle?: string;
}

@Injectable({
  providedIn: 'root'
})
export class PdfExportService {

  constructor() { }

  /**
   * Export chat messages to PDF
   * @param element - The HTML element containing the chat messages
   * @param options - Export configuration options
   * @param filename - Optional filename for the PDF
   */
  async exportChatToPdf(
    element: HTMLElement, 
    options: ChatExportOptions = {}, 
    filename?: string
  ): Promise<void> {
    try {
      // Set default options
      let chatContainer = document.querySelector('.chat-messages');

      let paperSize:any = options.paperSize||'a4';
      if(chatContainer)
        paperSize = [chatContainer.getBoundingClientRect().width,chatContainer.getBoundingClientRect().height]

      const exportOptions: Required<ChatExportOptions> = {
        includeTimestamps: true,
        includeVisualizations: true,
        includeThinkingProcess: false,
        paperSize: paperSize,
        orientation: 'portrait',
        title: 'Chat Export',
        subtitle: `Generated on ${new Date().toLocaleDateString()}`,
        ...options
      };

      // Show loading indicator
      this.showExportProgress('Preparing chat content...');

      // Clone and prepare the element for export
      const clonedElement = await this.prepareElementForExport(element, exportOptions);

      // Update progress
      this.showExportProgress('Capturing content...');

      // Convert to canvas with high quality settings
      const canvas = await html2canvas(clonedElement, {
        scale: 2, // Higher resolution
        useCORS: true,
        allowTaint: true,
        backgroundColor: '#ffffff',
        logging: false,
        width: clonedElement.scrollWidth,
        height: clonedElement.scrollHeight,
        scrollX: 0,
        scrollY: 0
      });

      // Update progress
      this.showExportProgress('Generating PDF...');

      // Create PDF
      const pdf = this.createPdfFromCanvas(canvas, exportOptions);

      // Generate filename if not provided
      const pdfFilename = filename || this.generateFilename(exportOptions.title);

      // Update progress
      this.showExportProgress('Saving PDF...');

      // Save the PDF
      pdf.save(pdfFilename);

      // Clean up
      document.body.removeChild(clonedElement);
      this.hideExportProgress();

      console.log('✅ PDF export completed successfully');

    } catch (error) {
      console.error('❌ PDF export failed:', error);
      this.hideExportProgress();
      throw new Error(`PDF export failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  }

  /**
   * Export chat messages using a more optimized approach for large conversations
   */
  async exportLargeChatToPdf(
    element: HTMLElement,
    options: ChatExportOptions = {},
    filename?: string
  ): Promise<void> {
    try {
      const exportOptions: Required<ChatExportOptions> = {
        includeTimestamps: true,
        includeVisualizations: true,
        includeThinkingProcess: false,
        paperSize: 'a4',
        orientation: 'portrait',
        title: 'AgentCore Agents Chat Export',
        subtitle: `Generated on ${new Date().toLocaleDateString()}`,
        ...options
      };

      this.showExportProgress('Preparing large chat export...');

      // For large chats, we'll split into pages and render each separately
      const messageElements = this.getMessageElements(element);
      const pdf = this.createEmptyPdf(exportOptions);

      // Add title page
      this.addTitlePage(pdf, exportOptions);

      let currentPage = 1;
      const messagesPerPage = 10; // Adjust based on content density

      for (let i = 0; i < messageElements.length; i += messagesPerPage) {
        this.showExportProgress(`Processing page ${Math.ceil((i + 1) / messagesPerPage)}...`);

        const pageMessages = messageElements.slice(i, i + messagesPerPage);
        const pageElement = this.createPageElement(pageMessages, exportOptions);

        // Render this page
        const canvas = await html2canvas(pageElement, {
          scale: 1.5,
          useCORS: true,
          allowTaint: true,
          backgroundColor: '#ffffff',
          logging: false
        });

        // Add to PDF
        if (currentPage > 1) {
          pdf.addPage();
        }

        this.addCanvasToPdf(pdf, canvas, exportOptions);
        currentPage++;

        // Clean up page element
        document.body.removeChild(pageElement);
      }

      const pdfFilename = filename || this.generateFilename(exportOptions.title);
      pdf.save(pdfFilename);

      this.hideExportProgress();
      console.log('✅ Large PDF export completed successfully');

    } catch (error) {
      console.error('❌ Large PDF export failed:', error);
      this.hideExportProgress();
      throw error;
    }
  }

  private async prepareElementForExport(
    element: HTMLElement, 
    options: Required<ChatExportOptions>
  ): Promise<HTMLElement> {
    // Clone the element to avoid modifying the original
    const cloned = element.cloneNode(true) as HTMLElement;
    
    // Apply export-specific styles
    cloned.style.position = 'absolute';
    cloned.style.left = '-9999px';
    cloned.style.top = '0';
    cloned.style.width = '800px'; // Fixed width for consistent PDF layout
    cloned.style.backgroundColor = '#ffffff';
    cloned.style.padding = '20px';
    cloned.style.fontFamily = 'Arial, sans-serif';
    cloned.style.fontSize = '14px';
    cloned.style.lineHeight = '1.4';

    // Remove or modify elements based on options
    if (!options.includeTimestamps) {
      const timestamps = cloned.querySelectorAll('.message-time, .agent-timestamp');
      timestamps.forEach(ts => ts.remove());
    }

    if (!options.includeThinkingProcess) {
      const thinkingSections = cloned.querySelectorAll('.thinking-section, .thinking-content');
      thinkingSections.forEach(section => section.remove());
    }

    if (!options.includeVisualizations) {
      const visualizations = cloned.querySelectorAll('.visual-components, [class*="visualization"]');
      visualizations.forEach(viz => viz.remove());
    }

    // Improve text readability
    const messages = cloned.querySelectorAll('.message');
    messages.forEach(message => {
      (message as HTMLElement).style.marginBottom = '16px';
      (message as HTMLElement).style.padding = '12px';
      (message as HTMLElement).style.border = '1px solid #e0e0e0';
      (message as HTMLElement).style.borderRadius = '8px';
    });

    // Style agent names
    const agentNames = cloned.querySelectorAll('.agent-name');
    agentNames.forEach(name => {
      (name as HTMLElement).style.fontWeight = 'bold';
      (name as HTMLElement).style.marginBottom = '8px';
    });

    // Add to DOM temporarily for rendering
    document.body.appendChild(cloned);

    // Wait for any dynamic content to load
    await new Promise(resolve => setTimeout(resolve, 500));

    return cloned;
  }

  private createPdfFromCanvas(
    canvas: HTMLCanvasElement, 
    options: Required<ChatExportOptions>
  ): jsPDF {
    const pdf = this.createEmptyPdf(options);
    
    // Add title page
    this.addTitlePage(pdf, options);
    pdf.addPage();

    // Add the canvas content
    this.addCanvasToPdf(pdf, canvas, options);

    return pdf;
  }

  private createEmptyPdf(options: Required<ChatExportOptions>): jsPDF {
    const format = options.paperSize === 'letter' ? 'letter' : 'a4';
    return new jsPDF({
      orientation: options.orientation,
      unit: 'mm',
      format: format
    });
  }

  private addTitlePage(pdf: jsPDF, options: Required<ChatExportOptions>): void {
    const pageWidth = pdf.internal.pageSize.getWidth();
    const pageHeight = pdf.internal.pageSize.getHeight();

    // Title
    pdf.setFontSize(24);
    pdf.setFont('helvetica', 'bold');
    const titleWidth = pdf.getTextWidth(options.title);
    pdf.text(options.title, (pageWidth - titleWidth) / 2, 40);

    // Subtitle
    pdf.setFontSize(14);
    pdf.setFont('helvetica', 'normal');
    const subtitleWidth = pdf.getTextWidth(options.subtitle);
    pdf.text(options.subtitle, (pageWidth - subtitleWidth) / 2, 55);

    // Add a decorative line
    pdf.setLineWidth(0.5);
    pdf.line(20, 70, pageWidth - 20, 70);

    // Add export info
    pdf.setFontSize(10);
    pdf.text('This document contains a conversation with Amazon Bedrock AgentCore Agents', 20, 90);
    pdf.text('for advertising and marketing optimization.', 20, 100);

    // Add page number
    pdf.text(`Page 1`, pageWidth - 30, pageHeight - 10);
  }

  private addCanvasToPdf(
    pdf: jsPDF, 
    canvas: HTMLCanvasElement, 
    options: Required<ChatExportOptions>
  ): void {
    const imgData = canvas.toDataURL('image/png');
    const pageWidth = pdf.internal.pageSize.getWidth();
    const pageHeight = pdf.internal.pageSize.getHeight();
    
    // Calculate dimensions to fit the page with margins
    const margin = 10;
    const maxWidth = pageWidth - (2 * margin);
    const maxHeight = pageHeight - (2 * margin);
    
    const canvasAspectRatio = canvas.height / canvas.width;
    let imgWidth = maxWidth;
    let imgHeight = imgWidth * canvasAspectRatio;
    
    // If image is too tall, scale down
    if (imgHeight > maxHeight) {
      imgHeight = maxHeight;
      imgWidth = imgHeight / canvasAspectRatio;
    }
    
    // Center the image
    const x = (pageWidth - imgWidth) / 2;
    const y = margin;
    
    pdf.addImage(imgData, 'PNG', x, y, imgWidth, imgHeight);
  }

  private getMessageElements(container: HTMLElement): HTMLElement[] {
    return Array.from(container.querySelectorAll('.message')) as HTMLElement[];
  }

  private createPageElement(
    messages: HTMLElement[], 
    options: Required<ChatExportOptions>
  ): HTMLElement {
    const pageElement = document.createElement('div');
    pageElement.style.position = 'absolute';
    pageElement.style.left = '-9999px';
    pageElement.style.top = '0';
    pageElement.style.width = '800px';
    pageElement.style.backgroundColor = '#ffffff';
    pageElement.style.padding = '20px';
    pageElement.style.fontFamily = 'Arial, sans-serif';

    messages.forEach(message => {
      const clonedMessage = message.cloneNode(true) as HTMLElement;
      pageElement.appendChild(clonedMessage);
    });

    document.body.appendChild(pageElement);
    return pageElement;
  }

  private generateFilename(title: string): string {
    const timestamp = new Date().toISOString().slice(0, 19).replace(/[:.]/g, '-');
    const sanitizedTitle = title.replace(/[^a-zA-Z0-9]/g, '-').toLowerCase();
    return `${sanitizedTitle}-${timestamp}.pdf`;
  }

  private showExportProgress(message: string): void {
    // Remove existing progress indicator
    this.hideExportProgress();

    // Create progress overlay
    const overlay = document.createElement('div');
    overlay.id = 'pdf-export-progress';
    overlay.style.cssText = `
      position: fixed;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
      background: rgba(0, 0, 0, 0.7);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 10000;
      color: white;
      font-family: Arial, sans-serif;
    `;

    const content = document.createElement('div');
    content.style.cssText = `
      background: #333;
      padding: 20px 40px;
      border-radius: 8px;
      text-align: center;
    `;

    const spinner = document.createElement('div');
    spinner.style.cssText = `
      width: 40px;
      height: 40px;
      border: 4px solid #555;
      border-top: 4px solid #fff;
      border-radius: 50%;
      animation: spin 1s linear infinite;
      margin: 0 auto 16px;
    `;

    const text = document.createElement('div');
    text.textContent = message;
    text.style.fontSize = '16px';

    // Add CSS animation
    const style = document.createElement('style');
    style.textContent = `
      @keyframes spin {
        0% { transform: rotate(0deg); }
        100% { transform: rotate(360deg); }
      }
    `;
    document.head.appendChild(style);

    content.appendChild(spinner);
    content.appendChild(text);
    overlay.appendChild(content);
    document.body.appendChild(overlay);
  }

  private hideExportProgress(): void {
    const existing = document.getElementById('pdf-export-progress');
    if (existing) {
      existing.remove();
    }
  }

  /**
   * Get estimated PDF size for large conversations
   */
  estimatePdfSize(messageCount: number): { pages: number; sizeMB: number } {
    const messagesPerPage = 8; // Conservative estimate
    const pages = Math.ceil(messageCount / messagesPerPage) + 1; // +1 for title page
    const sizeMB = pages * 0.5; // Rough estimate: 0.5MB per page

    return { pages, sizeMB };
  }

  /**
   * Check if the conversation is too large for standard export
   */
  isLargeConversation(messageCount: number): boolean {
    return messageCount > 50; // Threshold for using optimized export
  }
}