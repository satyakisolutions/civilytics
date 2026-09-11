import {
  Users,
  AlertTriangle,
  Clock,
  Camera,
  CheckCircle2,
  XCircle,
  Activity,
  MapPin,
} from 'lucide-react';
import { useAnalytics } from '@/hooks/useAnalytics';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  BarChart,
  Bar,
} from 'recharts';

const CHART_COLORS = {
  primary: 'hsl(186, 100%, 42%)',
  success: 'hsl(142, 71%, 45%)',
  warning: 'hsl(38, 92%, 50%)',
  destructive: 'hsl(0, 72%, 51%)',
  muted: 'hsl(217, 33%, 17%)',
};

export function AnalyticsView() {
  const { summary, hourly, daily, byZone, isLoading } = useAnalytics();
  const cameraAvailabilityPct = summary.cameras_total > 0
    ? Math.round((summary.cameras_online / summary.cameras_total) * 100)
    : 0;

  return (
    <div className="flex flex-col h-full overflow-auto custom-scrollbar">
      {/* Header */}
      <div className="p-4 border-b border-border">
        <h1 className="text-lg font-semibold">Analytics Dashboard</h1>
        <p className="text-sm text-muted-foreground">Overview of waste violation monitoring metrics</p>
      </div>

      {/* Content */}
      <div className="flex-1 p-4 space-y-4">
        {/* Key Metrics Row */}
        <div className="grid grid-cols-4 gap-4">
          <MetricCard
            title="Events Today"
            value={summary.events_today}
            subtitle="violations detected"
            icon={AlertTriangle}
            iconColor="text-destructive"
          />
          <MetricCard
            title="This Week"
            value={summary.events_this_week}
            subtitle="total violations"
            icon={Activity}
            iconColor="text-warning"
          />
          <MetricCard
            title="Repeat Offenders"
            value={summary.repeat_offenders}
            subtitle="tracked identifiers, 2+ events"
            icon={Users}
            iconColor="text-info"
          />
          <MetricCard
            title="Avg Response"
            value={summary.avg_response_time_minutes != null ? `${summary.avg_response_time_minutes}m` : '—'}
            subtitle={summary.avg_response_time_minutes != null ? 'time to acknowledgement' : 'no acknowledged events yet'}
            icon={Clock}
            iconColor="text-success"
          />
        </div>

        {/* Charts Row */}
        <div className="grid grid-cols-3 gap-4">
          {/* Trend Chart */}
          <Card className="col-span-2">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium">Daily Trend (7 days)</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="h-[200px]">
                {daily.length === 0 && !isLoading ? (
                  <EmptyChartState label="No events in the last 7 days" />
                ) : (
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={daily}>
                      <defs>
                        <linearGradient id="colorCount" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor={CHART_COLORS.primary} stopOpacity={0.3}/>
                          <stop offset="95%" stopColor={CHART_COLORS.primary} stopOpacity={0}/>
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="hsl(217, 33%, 17%)" />
                      <XAxis
                        dataKey="date"
                        tick={{ fontSize: 10, fill: 'hsl(215, 20%, 55%)' }}
                        tickFormatter={(value) => new Date(value).toLocaleDateString('en-US', { weekday: 'short' })}
                      />
                      <YAxis tick={{ fontSize: 10, fill: 'hsl(215, 20%, 55%)' }} allowDecimals={false} />
                      <Tooltip
                        contentStyle={{
                          backgroundColor: 'hsl(222, 47%, 8%)',
                          border: '1px solid hsl(217, 33%, 18%)',
                          borderRadius: '8px',
                          fontSize: '12px',
                        }}
                      />
                      <Area
                        type="monotone"
                        dataKey="count"
                        stroke={CHART_COLORS.primary}
                        fill="url(#colorCount)"
                        strokeWidth={2}
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                )}
              </div>
            </CardContent>
          </Card>

          {/* Resolution Status */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium">Resolution Status</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <CheckCircle2 className="w-4 h-4 text-success" />
                    <span className="text-sm">Resolved</span>
                  </div>
                  <span className="font-mono text-sm">{summary.resolved_events}</span>
                </div>
                <Progress
                  value={summary.total_events > 0 ? (summary.resolved_events / summary.total_events) * 100 : 0}
                  className="h-2"
                />

                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <XCircle className="w-4 h-4 text-warning" />
                    <span className="text-sm">Pending</span>
                  </div>
                  <span className="font-mono text-sm">{summary.pending_events}</span>
                </div>
                <Progress
                  value={summary.total_events > 0 ? (summary.pending_events / summary.total_events) * 100 : 0}
                  className="h-2 [&>div]:bg-warning"
                />

                <div className="pt-2 border-t border-border">
                  <div className="flex items-center justify-between text-xs text-muted-foreground">
                    <span>Total Events</span>
                    <span className="font-mono">{summary.total_events}</span>
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Bottom Row */}
        <div className="grid grid-cols-3 gap-4">
          {/* Hourly Distribution */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium">Last 24 Hours</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="h-[150px]">
                {hourly.length === 0 && !isLoading ? (
                  <EmptyChartState label="No events in the last 24 hours" />
                ) : (
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={hourly}>
                      <CartesianGrid strokeDasharray="3 3" stroke="hsl(217, 33%, 17%)" />
                      <XAxis
                        dataKey="hour"
                        tick={{ fontSize: 8, fill: 'hsl(215, 20%, 55%)' }}
                        tickFormatter={(value) => new Date(value).toLocaleTimeString('en-US', { hour: 'numeric', hour12: false })}
                      />
                      <YAxis hide allowDecimals={false} />
                      <Tooltip
                        contentStyle={{
                          backgroundColor: 'hsl(222, 47%, 8%)',
                          border: '1px solid hsl(217, 33%, 18%)',
                          borderRadius: '8px',
                          fontSize: '12px',
                        }}
                        formatter={(value: number) => [`${value} events`, 'Count']}
                        labelFormatter={(label) => new Date(label).toLocaleString()}
                      />
                      <Bar
                        dataKey="count"
                        fill={CHART_COLORS.primary}
                        radius={[2, 2, 0, 0]}
                      />
                    </BarChart>
                  </ResponsiveContainer>
                )}
              </div>
            </CardContent>
          </Card>

          {/* Top Zones */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium">Top Violation Zones</CardTitle>
            </CardHeader>
            <CardContent>
              {byZone.length === 0 && !isLoading ? (
                <EmptyChartState label="No zone data yet" />
              ) : (
                <div className="space-y-3">
                  {byZone.slice(0, 5).map((zone, index) => (
                    <div key={zone.zone} className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="w-5 h-5 rounded bg-muted flex items-center justify-center text-[10px] font-mono">
                          {index + 1}
                        </span>
                        <MapPin className="w-3.5 h-3.5 text-muted-foreground" />
                        <span className="text-sm">{zone.zone}</span>
                      </div>
                      <Badge variant="secondary" className="text-[10px] font-mono">
                        {zone.event_count}
                      </Badge>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          {/* System Health */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium">System Health</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Camera className="w-4 h-4 text-primary" />
                    <span className="text-sm">Cameras Online</span>
                  </div>
                  <span className="font-mono text-sm text-success">
                    {summary.cameras_online}/{summary.cameras_total}
                  </span>
                </div>
                <Progress value={cameraAvailabilityPct} className="h-2" />

                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <AlertTriangle className="w-4 h-4 text-destructive" />
                    <span className="text-sm">Critical Today</span>
                  </div>
                  <span className="font-mono text-sm">{summary.critical_today}</span>
                </div>

                <div className="pt-2 border-t border-border">
                  <div className="flex items-center gap-2">
                    <span className={`status-dot ${cameraAvailabilityPct === 100 ? 'status-dot-online' : cameraAvailabilityPct > 0 ? 'status-dot-warning' : 'status-dot-offline'}`} />
                    <span className="text-xs text-muted-foreground">
                      {cameraAvailabilityPct}% of cameras online
                    </span>
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

function EmptyChartState({ label }: { label: string }) {
  return (
    <div className="h-full flex items-center justify-center text-xs text-muted-foreground">
      {label}
    </div>
  );
}

interface MetricCardProps {
  title: string;
  value: string | number;
  subtitle: string;
  icon: React.ElementType;
  iconColor?: string;
}

function MetricCard({ title, value, subtitle, icon: Icon, iconColor = 'text-primary' }: MetricCardProps) {
  return (
    <Card className="metric-card">
      <CardContent className="p-4">
        <div className="flex items-start justify-between">
          <div>
            <p className="text-xs text-muted-foreground mb-1">{title}</p>
            <p className="text-2xl font-bold font-mono">{value}</p>
            <p className="text-[10px] text-muted-foreground mt-1">{subtitle}</p>
          </div>
          <div className={`p-2 rounded-lg bg-muted ${iconColor}`}>
            <Icon className="w-5 h-5" />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
