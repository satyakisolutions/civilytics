import { ReactNode } from 'react';
import { Sidebar } from './Sidebar';
import { AlertPanel } from '@/components/alerts/AlertPanel';
import { useMonitoring } from '@/context/MonitoringContext';
import { cn } from '@/lib/utils';

interface MainLayoutProps {
  children: ReactNode;
}

export function MainLayout({ children }: MainLayoutProps) {
  const { isAlertPanelOpen } = useMonitoring();

  return (
    <div className="flex h-screen bg-background overflow-hidden relative">
      {/* Sidebar */}
      <Sidebar className="w-56 flex-shrink-0" />

      {/* Main Content */}
      <main className="flex-1 flex flex-col overflow-hidden">
        {children}
      </main>

      {/* Alert Panel — a non-blocking right-side drawer. No full-screen
          backdrop (CI-53): new alerts must never block what the operator is
          doing. Content behind stays interactive; dismiss via the panel's own
          close button or the sidebar bell toggle. */}
      {isAlertPanelOpen && (
        <div className="fixed top-0 right-0 h-full w-80 bg-card border-l border-border z-50 shadow-xl animate-slide-in-right">
          <AlertPanel />
        </div>
      )}
    </div>
  );
}
