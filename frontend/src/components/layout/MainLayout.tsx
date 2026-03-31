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
    <div className="flex h-screen bg-background overflow-hidden">
      {/* Sidebar */}
      <Sidebar className="w-56 flex-shrink-0" />

      {/* Main Content */}
      <main className="flex-1 flex flex-col overflow-hidden">
        {children}
      </main>

      {/* Alert Panel (Slide-in) */}
      <div 
        className={cn(
          'flex-shrink-0 border-l border-border bg-card transition-all duration-300 overflow-hidden',
          isAlertPanelOpen ? 'w-80' : 'w-0'
        )}
      >
        {isAlertPanelOpen && <AlertPanel />}
      </div>
    </div>
  );
}
