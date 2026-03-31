import { 
  TrendingUp, 
  TrendingDown, 
  Users, 
  AlertTriangle, 
  Clock, 
  Camera,
  CheckCircle2,
  XCircle,
  Activity,
  MapPin,
} from 'lucide-react';
import { mockAnalytics, mockSystemStatus } from '@/data/mockData';
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
  PieChart,
  Pie,
  Cell,
} from 'recharts';

const CHART_COLORS = {
  primary: 'hsl(186, 100%, 42%)',
  success: 'hsl(142, 71%, 45%)',
  warning: 'hsl(38, 92%, 50%)',
  destructive: 'hsl(0, 72%, 51%)',
  muted: 'hsl(217, 33%, 17%)',
};

export function AnalyticsView() {
  const analytics = mockAnalytics;
  const systemStatus = mockSystemStatus;

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
            value={analytics.eventsToday}
            subtitle="violations detected"
            icon={AlertTriangle}
            trend={{ value: 15, isUp: true }}
            iconColor="text-destructive"
          />
          <MetricCard
            title="This Week"
            value={analytics.eventsThisWeek}
            subtitle="total violations"
            icon={Activity}
            trend={{ value: 8, isUp: false }}
            iconColor="text-warning"
          />
          <MetricCard
            title="Repeat Offenders"
            value={analytics.repeatOffenders}
            subtitle="identified"
            icon={Users}
            trend={{ value: 3, isUp: true }}
            iconColor="text-info"
          />
          <MetricCard
            title="Avg Response"
            value={`${analytics.averageResponseTime}m`}
            subtitle="response time"
            icon={Clock}
            trend={{ value: 12, isUp: false }}
            iconColor="text-success"
          />
        </div>

        {/* Charts Row */}
        <div className="grid grid-cols-3 gap-4">
          {/* Trend Chart */}
          <Card className="col-span-2">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium">Weekly Trend</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="h-[200px]">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={analytics.dailyTrend}>
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
                    <YAxis tick={{ fontSize: 10, fill: 'hsl(215, 20%, 55%)' }} />
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
                  <span className="font-mono text-sm">{analytics.resolvedEvents}</span>
                </div>
                <Progress 
                  value={(analytics.resolvedEvents / analytics.totalEvents) * 100} 
                  className="h-2"
                />
                
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <XCircle className="w-4 h-4 text-warning" />
                    <span className="text-sm">Pending</span>
                  </div>
                  <span className="font-mono text-sm">{analytics.pendingEvents}</span>
                </div>
                <Progress 
                  value={(analytics.pendingEvents / analytics.totalEvents) * 100} 
                  className="h-2 [&>div]:bg-warning"
                />

                <div className="pt-2 border-t border-border">
                  <div className="flex items-center justify-between text-xs text-muted-foreground">
                    <span>Total Events</span>
                    <span className="font-mono">{analytics.totalEvents}</span>
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
              <CardTitle className="text-sm font-medium">Hourly Distribution</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="h-[150px]">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={analytics.hourlyDistribution}>
                    <CartesianGrid strokeDasharray="3 3" stroke="hsl(217, 33%, 17%)" />
                    <XAxis 
                      dataKey="hour" 
                      tick={{ fontSize: 8, fill: 'hsl(215, 20%, 55%)' }}
                      tickFormatter={(value) => `${value}h`}
                    />
                    <YAxis hide />
                    <Tooltip 
                      contentStyle={{ 
                        backgroundColor: 'hsl(222, 47%, 8%)', 
                        border: '1px solid hsl(217, 33%, 18%)',
                        borderRadius: '8px',
                        fontSize: '12px',
                      }}
                      formatter={(value: number) => [`${value} events`, 'Count']}
                      labelFormatter={(label) => `${label}:00`}
                    />
                    <Bar 
                      dataKey="count" 
                      fill={CHART_COLORS.primary}
                      radius={[2, 2, 0, 0]}
                    />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </CardContent>
          </Card>

          {/* Top Zones */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium">Top Violation Zones</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                {analytics.topZones.slice(0, 5).map((zone, index) => (
                  <div key={zone.zone} className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="w-5 h-5 rounded bg-muted flex items-center justify-center text-[10px] font-mono">
                        {index + 1}
                      </span>
                      <MapPin className="w-3.5 h-3.5 text-muted-foreground" />
                      <span className="text-sm">{zone.zone}</span>
                    </div>
                    <Badge variant="secondary" className="text-[10px] font-mono">
                      {zone.count}
                    </Badge>
                  </div>
                ))}
              </div>
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
                    <span className="text-sm">Camera Uptime</span>
                  </div>
                  <span className="font-mono text-sm text-success">{analytics.cameraUptime}%</span>
                </div>
                <Progress value={analytics.cameraUptime} className="h-2" />

                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Activity className="w-4 h-4 text-primary" />
                    <span className="text-sm">Storage Used</span>
                  </div>
                  <span className="font-mono text-sm">
                    {systemStatus.storageUsed}GB / {systemStatus.storageTotal}GB
                  </span>
                </div>
                <Progress 
                  value={(systemStatus.storageUsed / systemStatus.storageTotal) * 100} 
                  className="h-2"
                />

                <div className="pt-2 border-t border-border">
                  <div className="flex items-center gap-2">
                    <span className="status-dot status-dot-online" />
                    <span className="text-xs text-muted-foreground">All systems operational</span>
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

interface MetricCardProps {
  title: string;
  value: string | number;
  subtitle: string;
  icon: React.ElementType;
  trend?: { value: number; isUp: boolean };
  iconColor?: string;
}

function MetricCard({ title, value, subtitle, icon: Icon, trend, iconColor = 'text-primary' }: MetricCardProps) {
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
        {trend && (
          <div className="flex items-center gap-1 mt-2">
            {trend.isUp ? (
              <TrendingUp className="w-3 h-3 text-destructive" />
            ) : (
              <TrendingDown className="w-3 h-3 text-success" />
            )}
            <span className={`text-[10px] ${trend.isUp ? 'text-destructive' : 'text-success'}`}>
              {trend.value}% vs last week
            </span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
