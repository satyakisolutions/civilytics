import { useEffect, useState } from 'react';
import { supabase } from '@/integrations/supabase/client';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { AlertTriangle, Eye, ShieldAlert, TrendingUp } from 'lucide-react';

interface DetectionStats {
  totalToday: number;
  activeAlerts: number;
  repeatOffenders: number;
  avgConfidence: number;
  recentEvents: { id: string; event_type: string; severity: string; timestamp: string; camera_name: string }[];
}

export function DetectionStatsWidget() {
  const [stats, setStats] = useState<DetectionStats>({
    totalToday: 0,
    activeAlerts: 0,
    repeatOffenders: 0,
    avgConfidence: 0,
    recentEvents: [],
  });

  const fetchStats = async () => {
    const todayStart = new Date();
    todayStart.setHours(0, 0, 0, 0);

    const [eventsRes, activeRes, repeatRes] = await Promise.all([
      supabase
        .from('violation_events')
        .select('*, cameras!inner(name)')
        .gte('timestamp', todayStart.toISOString())
        .order('timestamp', { ascending: false })
        .limit(50),
      supabase
        .from('violation_events')
        .select('id', { count: 'exact', head: true })
        .eq('status', 'active'),
      supabase
        .from('violation_events')
        .select('id', { count: 'exact', head: true })
        .eq('event_type', 'repeat_offender'),
    ]);

    const events = eventsRes.data || [];
    const avgConf = events.length > 0
      ? Math.round(events.reduce((sum, e) => sum + (e.confidence || 0), 0) / events.length)
      : 0;

    setStats({
      totalToday: events.length,
      activeAlerts: activeRes.count || 0,
      repeatOffenders: repeatRes.count || 0,
      avgConfidence: avgConf,
      recentEvents: events.slice(0, 5).map(e => ({
        id: e.id,
        event_type: e.event_type,
        severity: e.severity,
        timestamp: e.timestamp,
        camera_name: (e.cameras as any)?.name || 'Unknown',
      })),
    });
  };

  useEffect(() => {
    fetchStats();

    const channel = supabase
      .channel('detection-stats')
      .on('postgres_changes', { event: '*', schema: 'public', table: 'violation_events' }, () => {
        fetchStats();
      })
      .subscribe();

    return () => { supabase.removeChannel(channel); };
  }, []);

  const statCards = [
    { label: 'Detections Today', value: stats.totalToday, icon: Eye, color: 'text-primary' },
    { label: 'Active Alerts', value: stats.activeAlerts, icon: AlertTriangle, color: 'text-destructive' },
    { label: 'Repeat Offenders', value: stats.repeatOffenders, icon: ShieldAlert, color: 'text-warning' },
    { label: 'Avg Confidence', value: `${stats.avgConfidence}%`, icon: TrendingUp, color: 'text-success' },
  ];

  const severityClass: Record<string, string> = {
    critical: 'bg-destructive/20 text-destructive',
    warning: 'bg-warning/20 text-warning',
    info: 'bg-info/20 text-info',
  };

  return (
    <Card className="border-border">
      <CardHeader className="pb-3">
        <CardTitle className="text-sm font-semibold flex items-center gap-2">
          <Eye className="w-4 h-4 text-primary" />
          AI Detection Stats
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Metric cards */}
        <div className="grid grid-cols-2 gap-2">
          {statCards.map((s) => (
            <div key={s.label} className="metric-card flex items-center gap-2 p-3">
              <s.icon className={cn('w-4 h-4 flex-shrink-0', s.color)} />
              <div className="min-w-0">
                <div className="text-lg font-bold font-mono leading-none">{s.value}</div>
                <div className="text-[10px] text-muted-foreground truncate">{s.label}</div>
              </div>
            </div>
          ))}
        </div>

        {/* Recent events */}
        {stats.recentEvents.length > 0 && (
          <div className="space-y-1.5">
            <span className="text-[10px] text-muted-foreground uppercase font-semibold">Recent Activity</span>
            {stats.recentEvents.map((event) => (
              <div key={event.id} className="flex items-center justify-between gap-2 px-2 py-1.5 rounded bg-muted/50 text-xs">
                <div className="flex items-center gap-2 min-w-0">
                  <Badge className={cn('h-4 px-1 text-[9px] shrink-0', severityClass[event.severity] || severityClass.info)}>
                    {event.severity}
                  </Badge>
                  <span className="truncate text-foreground">{event.camera_name}</span>
                </div>
                <span className="text-[10px] text-muted-foreground font-mono shrink-0">
                  {new Date(event.timestamp).toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit' })}
                </span>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
