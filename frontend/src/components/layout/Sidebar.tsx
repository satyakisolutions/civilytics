import { useState, useEffect } from 'react';
import {
  Camera,
  Grid3X3,
  Grid2X2,
  LayoutGrid,
  BarChart3,
  History,
  Settings,
  Bell,
  Volume2,
  VolumeX,
  Shield,
  AlertTriangle,
  Map,
  PlayCircle,
  XCircle,
  LogOut,
  UserCircle,
  CreditCard,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { API_BASE } from '@/lib/api-base';
import { useMonitoring } from '@/context/MonitoringContext';
import { useAuth, topRole } from '@/context/AuthContext';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';

interface SidebarProps {
  className?: string;
  onNavigate?: () => void;
}

const navigationItems = [
  { id: 'monitoring', label: 'Live Monitoring', icon: Grid3X3 },
  { id: 'map', label: 'Camera Map', icon: Map },
  { id: 'analytics', label: 'Analytics', icon: BarChart3 },
  { id: 'history', label: 'Event History', icon: History },
  { id: 'timeline', label: 'Timeline Playback', icon: PlayCircle },
  { id: 'cameras', label: 'Camera Management', icon: Camera },
  { id: 'billing', label: 'Subscription & Billing', icon: CreditCard },
  { id: 'settings', label: 'Settings', icon: Settings },
] as const;

export function Sidebar({ className, onNavigate }: SidebarProps) {
  const { 
    currentView, 
    setCurrentView, 
    alerts,
    clearAlerts,
    isSoundEnabled,
    setIsSoundEnabled,
    isAlertPanelOpen,
    setIsAlertPanelOpen,
  } = useMonitoring();
  const { user, logout } = useAuth();
  const role = user ? topRole(user.roles) : null;

  const activeAlerts = alerts.filter(a => a.status === 'active').length;

  return (
    <aside className={cn(
      'flex flex-col h-full bg-sidebar border-r border-sidebar-border',
      className
    )}>
      {/* Satyaki Logo + Brand */}
      <div className="p-4 border-b border-sidebar-border">
        <div className="flex items-center gap-3">
          <div className="relative">
            <svg viewBox="0 0 64 64" className="w-10 h-10" aria-label="Satyaki Solutions">
              <path d="M32 2 L58 17 L58 47 L32 62 L6 47 L6 17 Z" fill="#FCA719" stroke="#1A1A2E" strokeWidth="3"/>
              <text x="32" y="44" fontFamily="Poppins, Helvetica, Arial, sans-serif" fontSize="32" fontWeight="900" textAnchor="middle" fill="#1A1A2E">C</text>
            </svg>
            <span className="absolute -top-0.5 -right-0.5 w-3 h-3 bg-success rounded-full border-2 border-sidebar status-dot-online" />
          </div>
          <div className="flex flex-col">
            <span className="font-semibold text-sidebar-foreground text-sm" style={{ fontFamily: "'Poppins', sans-serif" }}>Civilytics</span>
            <span className="text-[10px] text-muted-foreground" style={{ fontFamily: "'Poppins', sans-serif" }}>Satyaki Solutions Pvt Ltd</span>
          </div>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 p-2 space-y-1 overflow-y-auto custom-scrollbar">
        {navigationItems.map((item) => {
          const Icon = item.icon;
          const isActive = currentView === item.id;

          return (
            <button
              key={item.id}
              onClick={() => { setCurrentView(item.id); onNavigate?.(); }}
              className={cn(
                'w-full flex items-center gap-3 px-3 py-2.5 rounded-md text-[13px] transition-all duration-200 relative',
                isActive
                  ? 'text-cv-cyan bg-cv-nav-active'
                  : 'text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground'
              )}
            >
              {isActive && <div className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-5 rounded-r-sm bg-cv-cyan" />}
              <Icon className="w-[18px] h-[18px]" />
              <span className="font-medium">{item.label}</span>
            </button>
          );
        })}
      </nav>

      {/* Quick Actions */}
      <div className="p-3 border-t border-sidebar-border space-y-2">
        {/* Alert Toggle */}
        <div className="flex items-center gap-1">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setIsAlertPanelOpen(!isAlertPanelOpen)}
                className={cn(
                  'flex-1 justify-start gap-3 h-10',
                  isAlertPanelOpen && 'bg-sidebar-accent'
                )}
              >
                <div className="relative">
                  <Bell className="w-4 h-4" />
                  {activeAlerts > 0 && (
                    <span className="absolute -top-1 -right-1 w-2 h-2 bg-destructive rounded-full status-dot-critical" />
                  )}
                </div>
                <span className="text-sm">Notifications</span>
                {activeAlerts > 0 && (
                  <Badge variant="destructive" className="ml-auto h-5 px-1.5 text-[10px]">
                    {activeAlerts}
                  </Badge>
                )}
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">Toggle Notification Panel</TooltipContent>
          </Tooltip>
          {activeAlerts > 0 && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8 flex-shrink-0 text-muted-foreground hover:text-destructive"
                  onClick={(e) => {
                    e.stopPropagation();
                    clearAlerts();
                  }}
                >
                  <XCircle className="w-4 h-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="right">Clear all notifications</TooltipContent>
            </Tooltip>
          )}
        </div>

        {/* Sound Toggle */}
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setIsSoundEnabled(!isSoundEnabled)}
              className={cn(
                'w-full justify-start gap-3 h-10',
                !isSoundEnabled && 'text-muted-foreground'
              )}
            >
              {isSoundEnabled ? (
                <Volume2 className="w-4 h-4 text-success" />
              ) : (
                <VolumeX className="w-4 h-4" />
              )}
              <span className="text-sm">Alert Sound</span>
              <span className={cn(
                'ml-auto text-[10px] uppercase font-medium',
                isSoundEnabled ? 'text-success' : 'text-muted-foreground'
              )}>
                {isSoundEnabled ? 'ON' : 'OFF'}
              </span>
            </Button>
          </TooltipTrigger>
          <TooltipContent side="right">
            {isSoundEnabled ? 'Disable' : 'Enable'} Alert Sounds
          </TooltipContent>
        </Tooltip>
      </div>

      {/* Time Display */}
      <div className="p-4 border-t border-sidebar-border">
        <TimeDisplay />
      </div>

      {/* Signed-in identity + sign out (CI-51). Lives in the sidebar so it is
          on every page; a shared office PC must be able to end the session. */}
      {user && (
        <div className="p-3 border-t border-sidebar-border flex items-center gap-2">
          <UserCircle className="w-8 h-8 text-muted-foreground flex-shrink-0" />
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium truncate">{user.name || user.username}</div>
            {user.email && (
              <div className="text-[10px] text-muted-foreground truncate">{user.email}</div>
            )}
            {role && (
              <Badge variant="secondary" className="mt-0.5 h-4 px-1.5 text-[9px] uppercase">
                {role}
              </Badge>
            )}
          </div>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8 flex-shrink-0"
                onClick={() => logout()}
                aria-label="Sign out"
              >
                <LogOut className="w-4 h-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">Sign out</TooltipContent>
          </Tooltip>
        </div>
      )}

      {/* Powered By Footer */}
      <div className="px-4 pb-3 border-t border-sidebar-border pt-2">
        <a
          href="https://satyaki.co.in"
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center justify-center gap-1.5 opacity-60 hover:opacity-100 transition-opacity"
        >
          <span className="text-[9px] text-muted-foreground" style={{ fontFamily: "'Poppins', sans-serif" }}>Powered by</span>
          <img src="/satyaki-logo.svg" alt="Satyaki Solutions Pvt Ltd" className="h-4 w-auto" />
          <span className="text-[9px] font-medium whitespace-nowrap" style={{ fontFamily: "'Poppins', sans-serif", color: '#FCA719' }}>Satyaki Solutions Pvt Ltd</span>
        </a>
      </div>
    </aside>
  );
}

function TimeDisplay() {
  const [time, setTime] = useState(new Date());

  useEffect(() => {
    const interval = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="text-center">
      <div className="font-mono text-2xl text-sidebar-foreground tracking-wider">
        {time.toLocaleTimeString('en-US', { hour12: false })}
      </div>
      <div className="text-xs text-muted-foreground mt-1">
        {time.toLocaleDateString('en-US', { 
          weekday: 'short', 
          month: 'short', 
          day: 'numeric',
          year: 'numeric'
        })}
      </div>
    </div>
  );
}

