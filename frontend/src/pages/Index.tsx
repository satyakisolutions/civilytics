import { MonitoringProvider, useMonitoring } from '@/context/MonitoringContext';
import { MainLayout } from '@/components/layout/MainLayout';
import { MonitoringView } from '@/components/views/MonitoringView';
import { AnalyticsView } from '@/components/views/AnalyticsView';
import { HistoryView } from '@/components/views/HistoryView';
import { CameraManagementView } from '@/components/views/CameraManagementView';
import { SettingsView } from '@/components/views/SettingsView';
import { MapView } from '@/components/views/MapView';
import { TimelineView } from '@/components/views/TimelineView';
import { BillingView } from '@/components/views/BillingView';
import { PersonTrackingView } from '@/components/views/PersonTrackingView';
import { VehicleTrackingView } from '@/components/views/VehicleTrackingView';
import { OperationsView } from '@/components/views/OperationsView';

function DashboardContent() {
  const { currentView } = useMonitoring();

  return (
    <MainLayout>
      {currentView === 'monitoring' && <MonitoringView />}
      {currentView === 'map' && <MapView />}
      {currentView === 'analytics' && <AnalyticsView />}
      {currentView === 'history' && <HistoryView />}
      {currentView === 'timeline' && <TimelineView />}
      {currentView === 'persons' && <PersonTrackingView />}
      {currentView === 'vehicles' && <VehicleTrackingView />}
      {currentView === 'cameras' && <CameraManagementView />}
      {currentView === 'billing' && <BillingView />}
      {currentView === 'operations' && <OperationsView />}
      {currentView === 'settings' && <SettingsView />}
    </MainLayout>
  );
}

const Index = () => {
  return (
    <MonitoringProvider>
      <DashboardContent />
    </MonitoringProvider>
  );
};

export default Index;
