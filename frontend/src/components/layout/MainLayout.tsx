import { ReactNode } from 'react';
import { Sidebar } from './Sidebar';
import { AlertPanel } from '@/components/alerts/AlertPanel';
import { useMonitoring } from '@/context/MonitoringContext';
import { cn } from '@/lib/utils';

interface MainLayoutProps {
  children: ReactNode;
}

export function MainLayout({ children }: MainLayoutProps) {
  const { isAlertPanelOpen, setIsAlertPanelOpen } = useMonitoring();

  return (
    <div className="flex h-screen bg-background overflow-hidden relative">
      {/* Sidebar */}
      <Sidebar className="w-56 flex-shrink-0" />

      {/* Main Content */}
      <main className="flex-1 flex flex-col overflow-hidden">
        {children}
      </main>

      {/* Alert Panel — overlays on top of content, doesn't push layout */}
      {isAlertPanelOpen && (
        <>
          {/* Backdrop */}
          <div
            className="fixed inset-0 bg-black/20 z-40"
            onClick={() => setIsAlertPanelOpen(false)}
          />
          {/* Panel */}
          <div className="fixed top-0 right-0 h-full w-80 bg-card border-l border-border z-50 shadow-xl animate-slide-in-right">
            <AlertPanel />
          </div>
        </>
      )}
    </div>
  );
}
