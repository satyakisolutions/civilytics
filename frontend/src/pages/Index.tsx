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
import { useAuth, topRole } from '@/context/AuthContext';
import { canSeeView } from '@/components/layout/Sidebar';

function DashboardContent() {
  const { currentView } = useMonitoring();
  const { user } = useAuth();
  const role = user ? topRole(user.roles) : null;
  // A role-gated view (billing/cameras/settings) is also guarded here, not just
  // hidden in the sidebar, so a persisted or hand-set currentView can't reach
  // it. Falls back to monitoring, which everyone may see.
  const view = canSeeView(currentView, role) ? currentView : 'monitoring';

  return (
    <MainLayout>
      {view === 'monitoring' && <MonitoringView />}
      {view === 'map' && <MapView />}
      {view === 'analytics' && <AnalyticsView />}
      {view === 'history' && <HistoryView />}
      {view === 'timeline' && <TimelineView />}
      {view === 'cameras' && <CameraManagementView />}
      {view === 'billing' && <BillingView />}
      {view === 'settings' && <SettingsView />}
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
