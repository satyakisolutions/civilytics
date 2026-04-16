import {
  X,
  Bell,
  CheckCircle2,
  AlertTriangle,
  Info,
  Clock,
  MapPin,
  Camera,
  ChevronRight,
  Volume2,
} from 'lucide-react';
import { useMonitoring } from '@/context/MonitoringContext';
import { Alert as AlertType } from '@/types/monitoring';
import { API_BASE } from '@/lib/api-base';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';

export function AlertPanel() {
  const { alerts, acknowledgeAlert, clearAlerts, setIsAlertPanelOpen, setCurrentView } = useMonitoring();

  const sortedAlerts = [...alerts].sort((a, b) => {
    // Sort by status (active first) then by timestamp
    if (a.status === 'active' && b.status !== 'active') return -1;
    if (a.status !== 'active' && b.status === 'active') return 1;
    return b.timestamp.getTime() - a.timestamp.getTime();
  });

  const handleClearAll = () => clearAlerts();

  return (
    <div className="flex flex-col h-full overflow-hidden" style={{ width: 320, maxWidth: '100%' }}>
      {/* Header */}
      <div className="flex items-center justify-between p-3 border-b border-border flex-shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <Bell className="w-4 h-4 text-primary flex-shrink-0" />
          <h2 className="font-semibold text-sm">Alerts</h2>
          <Badge variant="secondary" className="h-5 px-1.5 text-[10px]">
            {alerts.filter(a => a.status === 'active').length}
          </Badge>
        </div>
        <div className="flex items-center gap-1 flex-shrink-0">
          <Button variant="ghost" size="sm" className="h-7 px-2 text-[10px]" onClick={handleClearAll}>
            Clear All
          </Button>
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setIsAlertPanelOpen(false)}>
            <X className="w-4 h-4" />
          </Button>
        </div>
      </div>

      {/* Alert List */}
      <ScrollArea className="flex-1" style={{ overflow: 'hidden' }}>
        <div className="p-2 space-y-2" style={{ width: '100%', maxWidth: 320, display: 'block' }}>
          {sortedAlerts.map((alert) => (
            <AlertCard 
              key={alert.id} 
              alert={alert} 
              onAcknowledge={() => acknowledgeAlert(alert.id)}
            />
          ))}
        </div>
      </ScrollArea>

      {/* Footer */}
      <div className="p-3 border-t border-border">
        <Button
          variant="outline"
          size="sm"
          className="w-full text-xs"
          onClick={() => { setCurrentView('history'); setIsAlertPanelOpen(false); }}
        >
          View All History
          <ChevronRight className="w-3.5 h-3.5 ml-1" />
        </Button>
      </div>
    </div>
  );
}

interface AlertCardProps {
  alert: AlertType;
  onAcknowledge: () => void;
}

function AlertCard({ alert, onAcknowledge }: AlertCardProps) {
  const severityConfig = {
    critical: {
      icon: AlertTriangle,
      className: 'alert-card-critical',
      iconColor: 'text-destructive',
      badgeClass: 'badge-critical',
    },
    warning: {
      icon: AlertTriangle,
      className: 'alert-card-warning',
      iconColor: 'text-warning',
      badgeClass: 'badge-warning',
    },
    info: {
      icon: Info,
      className: 'alert-card-info',
      iconColor: 'text-info',
      badgeClass: 'badge-info',
    },
  };

  const config = severityConfig[alert.severity];
  const Icon = config.icon;

  const timeAgo = getTimeAgo(alert.timestamp);

  return (
    <div 
      className={cn(
        'rounded-lg p-3 transition-all duration-200 animate-fade-in-up',
        config.className,
        alert.status === 'acknowledged' && 'opacity-60'
      )}
    >
      {/* Header */}
      <div className="flex items-start gap-2 mb-2">
        <div className={cn('mt-0.5', config.iconColor)}>
          <Icon className="w-4 h-4" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-2">
            <h3 className="font-medium text-sm text-foreground truncate">
              {alert.title}
            </h3>
            <Badge className={cn('text-[10px] px-1.5 h-4 flex-shrink-0', config.badgeClass)}>
              {alert.severity.toUpperCase()}
            </Badge>
          </div>
        </div>
      </div>

      {/* Message */}
      <p className="text-xs text-muted-foreground mb-3 line-clamp-2">
        {alert.message}
      </p>

      {/* Meta */}
      <div className="flex items-center gap-3 text-[10px] text-muted-foreground mb-3">
        <span className="flex items-center gap-1">
          <Clock className="w-3 h-3" />
          {timeAgo}
        </span>
        <span className="flex items-center gap-1">
          <MapPin className="w-3 h-3" />
          {alert.zone}
        </span>
      </div>

      {/* Actions */}
      {alert.status === 'active' && (
        <div className="flex gap-1.5">
          <Button
            size="sm"
            className="flex-1 h-7 text-xs"
            onClick={onAcknowledge}
          >
            <CheckCircle2 className="w-3.5 h-3.5 mr-1" />
            Ack
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-xs"
            onClick={() => {
              if ('speechSynthesis' in window) {
                speechSynthesis.cancel();
                const u = new SpeechSynthesisUtterance(`${alert.title}. ${alert.message}`);
                u.rate = 1.0;
                u.volume = 0.8;
                speechSynthesis.speak(u);
              }
            }}
          >
            <Volume2 className="w-3.5 h-3.5 mr-1" />
            Play
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-xs"
          >
            <Camera className="w-3.5 h-3.5 mr-1" />
            View
          </Button>
        </div>
      )}

      {alert.status === 'acknowledged' && (
        <div className="flex items-center gap-1.5 text-[10px] text-success">
          <CheckCircle2 className="w-3 h-3" />
          Acknowledged
        </div>
      )}
    </div>
  );
}

function getTimeAgo(date: Date): string {
  const seconds = Math.floor((new Date().getTime() - date.getTime()) / 1000);

  if (seconds < 60) return 'Just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}
