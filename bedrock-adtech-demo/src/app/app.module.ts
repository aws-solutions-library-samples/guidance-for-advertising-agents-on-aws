import { NgModule, CUSTOM_ELEMENTS_SCHEMA } from '@angular/core';
import { BrowserModule } from '@angular/platform-browser';
import { FormsModule } from '@angular/forms';
import { HttpClientModule, HttpClient } from '@angular/common/http';
import { BrowserAnimationsModule } from '@angular/platform-browser/animations';
import { MarkdownModule } from 'ngx-markdown';
import { GenericTabComponent } from './components/generic-tab/generic-tab.component';
import { ChatInterfaceComponent } from './components/chat-interface/chat-interface.component';
import { AgentMentionTypeaheadComponent } from './components/agent-mention-typeahead/agent-mention-typeahead.component';
import { ImageGalleryComponent } from './components/image-gallery/image-gallery.component';
import { TourOverlayComponent } from './components/tour-overlay/tour-overlay.component';
import { LoginComponent } from './components/auth/login.component';
import { MetricsVisualizationComponent } from './components/visualizations/metrics-visualization/metrics-visualization.component';
import { AllocationsVisualizationComponent } from './components/visualizations/allocations-visualization/allocations-visualization.component';
import { SegmentsVisualizationComponent } from './components/visualizations/segments-visualization/segments-visualization.component';
import { ChannelsVisualizationComponent } from './components/visualizations/channels-visualization/channels-visualization.component';
import { CreativeVisualizationComponent } from './components/visualizations/creative-visualization/creative-visualization.component';
import { TimelineVisualizationComponent } from './components/visualizations/timeline-visualization/timeline-visualization.component';
import { DecisionTreeVisualizationComponent } from './components/visualizations/decision-tree-visualization/decision-tree-visualization.component';
import { VisualizationPopoverComponent } from './components/visualizations/visualization-popover/visualization-popover.component';
import { VisualizationCompanionPanelComponent } from './components/visualizations/visualization-companion-panel/visualization-companion-panel.component';
import { HistogramVisualizationComponent } from './components/visualizations/histogram-visualization/histogram-visualization.component';
import { DoubleHistogramVisualizationComponent } from './components/visualizations/double-histogram-visualization/double-histogram-visualization.component';
import { BarChartVisualizationComponent } from './components/visualizations/bar-chart-visualization/bar-chart-visualization.component';
import { DonutChartVisualizationComponent } from './components/visualizations/donut-chart-visualization/donut-chart-visualization.component';
import { AdcpInventoryVisualizationComponent } from './components/visualizations/adcp_get_products-visualization/adcp_get_products-visualization.component';
import { PresentationVisualizationComponent } from './components/visualizations/presentation-visualization/presentation-visualization.component';
import { AgentConfigComponent } from './components/agent-config/agent-config.component';
import { DemoModalComponent } from './components/demo-modal/demo-modal.component';
import { ContextPanelComponent } from './components/context-panel/context-panel.component';
import { AgentSummaryModalComponent } from './components/agent-summary-modal/agent-summary-modal.component';
import { VisibilitySettingsModalComponent } from './components/visibility-settings-modal/visibility-settings-modal.component';
import { AuthGuard } from './guards/auth.guard';
import { AppRoutingModule } from './app-routing.module';
import { AppComponent } from './app.component';

@NgModule({
  declarations: [
    AppComponent,
    GenericTabComponent,
    ChatInterfaceComponent,
    AgentMentionTypeaheadComponent,
    ImageGalleryComponent,
    TourOverlayComponent,
    LoginComponent,
    MetricsVisualizationComponent,
    AllocationsVisualizationComponent,
    SegmentsVisualizationComponent,
    ChannelsVisualizationComponent,
    CreativeVisualizationComponent,
    TimelineVisualizationComponent,
    DecisionTreeVisualizationComponent,
    VisualizationPopoverComponent,
    VisualizationCompanionPanelComponent,
    HistogramVisualizationComponent,
    DoubleHistogramVisualizationComponent,
    BarChartVisualizationComponent,
    DonutChartVisualizationComponent,
    AdcpInventoryVisualizationComponent,
    PresentationVisualizationComponent,
    AgentConfigComponent,
    DemoModalComponent,
    ContextPanelComponent,
    AgentSummaryModalComponent,
    VisibilitySettingsModalComponent
  ],
  imports: [
    BrowserModule,
    AppRoutingModule,
    FormsModule,
    HttpClientModule,
    BrowserAnimationsModule,
    MarkdownModule.forRoot({
      loader: HttpClient
    })
  ],
  providers: [AuthGuard],
  bootstrap: [AppComponent],
  schemas: [CUSTOM_ELEMENTS_SCHEMA]
})
export class AppModule { } 