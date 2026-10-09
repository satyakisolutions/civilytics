import { ReactNode, useState } from 'react';
import { Menu, X } from 'lucide-react';
import { Sidebar } from './Sidebar';
import { AlertPanel } from '@/components/alerts/AlertPanel';
import { useMonitoring } from '@/context/MonitoringContext';
import { cn } from '@/lib/utils';

interface MainLayoutProps {
  children: ReactNode;
}

export function MainLayout({ children }: MainLayoutProps) {
  const { isAlertPanelOpen } = useMonitoring();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  return (
    <div className="flex h-screen bg-background overflow-hidden relative">
      {/* Mobile hamburger */}
      {!sidebarOpen && (
        <button
          onClick={() => setSidebarOpen(true)}
          className="fixed top-3 left-3 z-50 p-2 rounded-md bg-sidebar border border-sidebar-border md:hidden"
          aria-label="Open menu"
        >
          <Menu className="w-5 h-5 text-sidebar-foreground" />
        </button>
      )}

      {/* Sidebar — always visible on md+, drawer on mobile */}
      <div
        className={cn(
          'fixed inset-0 z-40 md:static md:z-auto',
          sidebarOpen ? 'block' : 'hidden md:block'
        )}
      >
        {/* Backdrop */}
        <div
          className="absolute inset-0 bg-black/50 md:hidden"
          onClick={() => setSidebarOpen(false)}
        />
        <div className="relative h-full w-56 flex-shrink-0">
          {/* Close button on mobile */}
          <button
            onClick={() => setSidebarOpen(false)}
            className="absolute top-3 right-3 z-50 p-1 rounded-md hover:bg-sidebar-accent md:hidden"
            aria-label="Close menu"
          >
            <X className="w-4 h-4 text-sidebar-foreground" />
          </button>
          <Sidebar className="w-56 flex-shrink-0" onNavigate={() => setSidebarOpen(false)} />
        </div>
      </div>

      {/* Main Content */}
      <main className="flex-1 flex flex-col overflow-hidden pt-14 md:pt-0">
        {children}
      </main>

      {isAlertPanelOpen && (
        <div className="fixed top-0 right-0 h-full w-80 bg-card border-l border-border z-50 shadow-xl animate-slide-in-right">
          <AlertPanel />
        </div>
      )}
    </div>
  );
}
