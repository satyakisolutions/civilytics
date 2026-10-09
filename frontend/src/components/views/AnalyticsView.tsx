import { useMemo } from 'react';
import {
  AlertTriangle,
  Activity,
  Users,
  Clock,
  Calendar,
  Download,
} from 'lucide-react';
import { useAnalytics } from '@/hooks/useAnalytics';
import { CV_COLORS } from '@/lib/cv-colors';

const CATEGORY_COLORS: Record<string, string> = {
  'Illegal Dumping': CV_COLORS.orange,
  'Overflowing Bin': CV_COLORS.green,
  'Unauthorized Access': CV_COLORS.purple,
  'Littering': CV_COLORS.cyan,
  'Vehicle Violation': CV_COLORS.blue,
  dumping_detected: CV_COLORS.orange,
  suspicious_activity: CV_COLORS.purple,
  repeat_offender: CV_COLORS.blue,
};

const CATEGORY_LABELS: Record<string, string> = {
  dumping_detected: 'Dumping',
  suspicious_activity: 'Suspicious',
  repeat_offender: 'Repeat',
};

function fmt(n: number | null | undefined, fallback = '—'): string {
  return n != null && n > 0 ? String(n) : fallback;
}

function formatDate(dateStr: string): string {
  const [y, m, d] = dateStr.split('-').map(Number);
  const date = new Date(y, m - 1, d);
  return date.toLocaleDateString('en-US', { day: 'numeric', month: 'short' });
}

export function AnalyticsView() {
  const { summary, heatmap, dailyByCategory, byZone, isLoading, error } = useAnalytics();

  const categoryTotals = useMemo(() =>
    heatmap.categories.map(cat => {
      const total = heatmap.data.reduce((sum, row) => sum + (Number(row[cat]) || 0), 0);
      return {
        key: cat,
        label: CATEGORY_LABELS[cat] ?? cat,
        color: CATEGORY_COLORS[cat] ?? CV_COLORS.muted,
        total,
      };
    }),
    [heatmap],
  );
  const grandTotal = Math.max(1, categoryTotals.reduce((s, c) => s + c.total, 0));

  const maxBarVal = useMemo(() => Math.max(
    1,
    ...dailyByCategory.data.flatMap(row =>
      dailyByCategory.categories.map(c => Number(row[c]) || 0)
    ),
  ), [dailyByCategory]);

  const maxHeatVal = useMemo(() => Math.max(
    1,
    ...heatmap.data.flatMap(row =>
      heatmap.categories.map(c => Number(row[c]) || 0)
    ),
  ), [heatmap]);

  const maxZone = useMemo(
    () => Math.max(1, ...byZone.map(z => z.event_count)),
    [byZone],
  );

  return (
    <div className="flex flex-col h-full overflow-auto custom-scrollbar bg-cv-bg">
      {/* Page header */}
      <div className="shrink-0 px-4 pt-5 pb-0 pl-14 md:pl-5 flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
        <div>
          <h1 className="text-xl md:text-[26px] font-semibold tracking-tight text-cv-fg">
            Analytics Dashboard
          </h1>
          <p className="text-xs mt-1 text-cv-fg2">
            Dumping patterns and violation metrics
          </p>
        </div>
        <div className="flex gap-2.5 flex-wrap">
          <button disabled className="h-[37px] px-3.5 rounded-md text-xs flex items-center gap-1.5 border bg-cv-panel border-cv-border text-cv-fg2 opacity-60 cursor-not-allowed">
            <Calendar className="w-3.5 h-3.5" /> Last 7 days
          </button>
          <button disabled className="h-[37px] px-3.5 rounded-md text-xs flex items-center gap-1.5 border bg-cv-panel border-cv-border text-cv-fg2 opacity-60 cursor-not-allowed">
            <Download className="w-3.5 h-3.5" /> Export report
          </button>
        </div>
      </div>

      {error && (
        <div className="mx-4 mt-3 px-4 py-2.5 rounded-md border text-xs bg-cv-panel border-cv-border text-cv-orange">
          Failed to load analytics data. Showing last available state.
        </div>
      )}

      <div className="flex-1 p-4 space-y-4">
        {/* KPI Row */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <KpiCard icon={AlertTriangle} iconColor={CV_COLORS.orange} label="Events today" value={fmt(summary.events_today)} note="Across all cameras" />
          <KpiCard icon={Activity} iconColor={CV_COLORS.blue} label="Events this week" value={fmt(summary.events_this_week)} note="Verified events" />
          <KpiCard icon={Users} iconColor={CV_COLORS.purple} label="Repeat match groups" value={fmt(summary.repeat_offenders)} note="Person + vehicle groups" />
          <KpiCard icon={Clock} iconColor={CV_COLORS.green} label="Avg. review time" value={summary.avg_response_time_minutes != null ? `${summary.avg_response_time_minutes}m` : '—'} note="Detection to first review" />
        </div>

        {/* Chart + Categories */}
        <div className="grid grid-cols-1 lg:grid-cols-[68fr_32fr] gap-4">
          {/* Grouped bar chart */}
          <Panel title="Daily Peak Dumping Pattern" sub="Event counts by arrival mode within configured peak windows">
            {dailyByCategory.categories.length === 0 && !isLoading ? (
              <Empty label="No categorized events yet" />
            ) : (
              <>
                <div className="flex gap-4 mb-3.5 flex-wrap">
                  {dailyByCategory.categories.map(cat => (
                    <div key={cat} className="flex items-center gap-1.5 text-[10px] text-cv-fg2">
                      <div className="w-2 h-2 rounded-sm" style={{ backgroundColor: CATEGORY_COLORS[cat] ?? CV_COLORS.muted }} />
                      {CATEGORY_LABELS[cat] ?? cat}
                    </div>
                  ))}
                </div>
                <div className="flex items-end gap-1 h-[249px] border-b border-l border-cv-border overflow-x-auto overflow-y-hidden min-w-0">
                  {dailyByCategory.data.map((row, i) => (
                    <div key={i} className="flex-1 flex items-end justify-center gap-[3px] h-full group/day hover:bg-white/[0.03] rounded-t transition-colors">
                      {dailyByCategory.categories.map(cat => {
                        const val = Number(row[cat]) || 0;
                        return (
                          <div
                            key={cat}
                            className="w-[8px] sm:w-[14px] rounded-t-sm relative cursor-pointer transition-all duration-150 hover:brightness-125 hover:scale-x-[1.3] origin-bottom"
                            style={{
                              height: val > 0 ? `${(val / maxBarVal) * 100}%` : '0%',
                              backgroundColor: CATEGORY_COLORS[cat] ?? CV_COLORS.muted,
                              minHeight: val > 0 ? 1 : 0,
                            }}
                            title={`${CATEGORY_LABELS[cat] ?? cat}: ${val}`}
                          >
                            {val > 0 && (
                              <span className="absolute -top-3.5 left-1/2 -translate-x-1/2 text-[8px] tabular-nums text-cv-fg2 pointer-events-none">
                                {val}
                              </span>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  ))}
                </div>
                <div className="flex justify-between pt-1.5">
                  {dailyByCategory.data.map((row, i) => (
                    <span key={i} className="text-[10px] font-mono flex-1 text-center text-cv-muted">
                      {formatDate(String(row.date))}
                    </span>
                  ))}
                </div>
              </>
            )}
          </Panel>

          {/* Violation categories */}
          <Panel title="Violation categories" sub="Last 7 days">
            {categoryTotals.length === 0 && !isLoading ? (
              <Empty label="No categories yet" />
            ) : (
              <>
                <div className="space-y-5">
                  {categoryTotals.map(cat => {
                    const pct = grandTotal > 0 ? ((cat.total / grandTotal) * 100).toFixed(1) : '0.0';
                    return (
                      <div key={cat.key}>
                        <div className="flex items-center gap-2 mb-1">
                          <div className="w-2 h-2 rounded-sm flex-shrink-0" style={{ backgroundColor: cat.color }} />
                          <span className="text-xs flex-1 text-cv-fg2">{cat.label}</span>
                          <span className="text-sm font-semibold tabular-nums text-cv-fg">
                            {cat.total > 0 ? cat.total : '—'}
                          </span>
                          <span className="text-[11px] tabular-nums w-[40px] text-right text-cv-muted">
                            {cat.total > 0 ? `${pct}%` : ''}
                          </span>
                        </div>
                        <div className="h-1 rounded-full overflow-hidden bg-cv-track">
                          <div className="h-full rounded-full" style={{ width: cat.total > 0 ? `${pct}%` : '0%', backgroundColor: cat.color }} />
                        </div>
                      </div>
                    );
                  })}
                </div>
                {categoryTotals.some(c => c.total > 0) && (
                  <div className="flex justify-between items-center mt-3.5 pt-3.5 text-xs border-t border-cv-border text-cv-fg2">
                    <span>Total verified events</span>
                    <span className="font-semibold text-cv-fg">{grandTotal}</span>
                  </div>
                )}
              </>
            )}
          </Panel>
        </div>

        {/* Bottom Row */}
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-[1.2fr_1fr_1fr] gap-4">
          {/* Heatmap */}
          <Panel title="When dumping happens" sub="Hourly event count">
            {heatmap.categories.length === 0 && !isLoading ? (
              <Empty label="No heatmap data yet" />
            ) : (
              <>
                <div className="overflow-x-auto">
                  <div className="inline-grid gap-px w-full" style={{ gridTemplateColumns: '80px repeat(24, 1fr)' }}>
                    <div />
                    {Array.from({ length: 24 }, (_, h) => (
                      <div key={h} className="text-[9px] text-center font-mono text-cv-muted">{String(h).padStart(2, '0')}</div>
                    ))}
                    {heatmap.categories.map(cat => (
                      <div key={cat} className="contents">
                        <div className="text-[10px] flex items-center pr-1 truncate text-cv-fg2">
                          {CATEGORY_LABELS[cat] ?? cat}
                        </div>
                        {Array.from({ length: 24 }, (_, h) => {
                          const row = heatmap.data[h];
                          const val = row ? Number(row[cat]) || 0 : 0;
                          return (
                            <div
                              key={`${cat}-${h}`}
                              className={`rounded-sm flex items-center justify-center ${val === 0 ? 'bg-cv-track' : ''}`}
                              style={{
                                height: 14,
                                ...(val > 0 ? {
                                  backgroundColor: CATEGORY_COLORS[cat] ?? CV_COLORS.muted,
                                  opacity: Math.max(0.5, val / maxHeatVal),
                                } : {
                                  backgroundImage: 'repeating-linear-gradient(45deg, transparent, transparent 2px, rgba(255,255,255,0.03) 2px, rgba(255,255,255,0.03) 4px)',
                                }),
                              }}
                              title={val > 0 ? `${CATEGORY_LABELS[cat] ?? cat} at ${String(h).padStart(2, '0')}:00 — ${val}` : undefined}
                            >
                              {val > 0 && (
                                <span className="text-[7px] font-semibold text-cv-bg leading-none">{val}</span>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    ))}
                  </div>
                </div>
                <div className="flex justify-between items-center mt-2.5 text-[10px] text-cv-muted">
                  <div className="flex items-center gap-1.5">
                    <div className="w-3.5 h-3.5 rounded-sm bg-cv-track" style={{
                      backgroundImage: 'repeating-linear-gradient(45deg, transparent, transparent 2px, rgba(255,255,255,0.03) 2px, rgba(255,255,255,0.03) 4px)',
                    }} />
                    Shaded cells: outside configured windows
                  </div>
                </div>
              </>
            )}
          </Panel>

          {/* Events by zone */}
          <Panel title="Events by zone" sub="Last 7 days">
            {byZone.length === 0 && !isLoading ? (
              <Empty label="No zone data yet" />
            ) : (
              <div className="space-y-3.5">
                {byZone.slice(0, 5).map(zone => (
                  <div key={zone.zone} className="flex items-center gap-2.5">
                    <span className="text-xs w-[90px] shrink-0 truncate text-cv-fg2">{zone.zone}</span>
                    <div className="flex-1 h-1.5 rounded-full overflow-hidden bg-cv-track">
                      <div className="h-full rounded-full bg-cv-blue" style={{ width: `${(zone.event_count / maxZone) * 100}%` }} />
                    </div>
                    <span className="text-[13px] font-semibold tabular-nums w-7 text-right text-cv-fg">{zone.event_count}</span>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          {/* System health */}
          <Panel title="System health" sub="Current status">
            <div className="space-y-0">
              <HealthRow dotColor="bg-cv-green" label="Cameras online" value={summary.cameras_total > 0 ? `${summary.cameras_online} / ${summary.cameras_total}` : '—'} valueColor="text-cv-green" />
              <HealthRow dotColor="bg-cv-orange" label="Stream warnings" value={fmt(summary.critical_today)} valueColor="text-cv-orange" />
              <HealthRow dotColor="bg-cv-fg" label="Offline cameras" value={summary.cameras_total > 0 ? fmt(summary.cameras_total - summary.cameras_online) : '—'} valueColor="text-cv-fg" />
              <HealthRow dotColor="bg-cv-green" label="Evidence available" value={summary.total_events > 0 ? `${summary.resolved_events + summary.pending_events} / ${summary.total_events}` : '—'} valueColor="text-cv-green" />
            </div>
          </Panel>
        </div>

        {/* Time window strip */}
        <div className="flex items-center gap-2 text-[10px] flex-wrap text-cv-fg2">
          <Clock className="w-3.5 h-3.5 flex-shrink-0 text-cv-muted" />
          <span>Midnight: 10:00 PM–1:00 AM</span>
          <span className="text-cv-muted">·</span>
          <span>Daytime: 5:00 AM–9:00 AM</span>
          <span className="text-cv-muted">·</span>
          <span className="text-cv-muted">End times exclusive. Other hours tracked separately</span>
        </div>
      </div>

      {/* Footer */}
      <div className="shrink-0 flex flex-col sm:flex-row justify-between items-center px-4 py-3 text-[9px] border-t border-cv-border text-cv-muted gap-1">
        <span>Sample data, illustrative camera locations and generated demonstration imagery</span>
        <span>All times IST (UTC+05:30)</span>
      </div>
    </div>
  );
}

function Panel({ title, sub, children }: { title: string; sub: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg p-5 bg-cv-panel border border-cv-border">
      <div className="text-[15px] font-semibold mb-0.5 text-cv-fg">{title}</div>
      <div className="text-[11px] mb-3 text-cv-muted">{sub}</div>
      {children}
    </div>
  );
}

function Empty({ label }: { label: string }) {
  return <div className="h-[120px] flex items-center justify-center text-xs text-cv-muted">{label}</div>;
}

function KpiCard({ icon: Icon, iconColor, label, value, note }: { icon: React.ElementType; iconColor: string; label: string; value: string; note: string }) {
  return (
    <div className="rounded-lg p-[18px_20px] h-[100px] sm:h-[125px] flex flex-col justify-between relative bg-cv-panel border border-cv-border focus-visible:outline-2 focus-visible:outline-cv-cyan focus-visible:outline-offset-2">
      <div>
        <p className="text-xs mb-1 text-cv-fg2">{label}</p>
        <p className="text-[30px] font-bold tabular-nums text-cv-fg">{value}</p>
      </div>
      <p className="text-[11px] text-cv-muted">{note}</p>
      <Icon className="absolute top-[18px] right-5 w-5 h-5" style={{ color: iconColor }} />
    </div>
  );
}

function HealthRow({ dotColor, label, value, valueColor }: { dotColor: string; label: string; value: string; valueColor: string }) {
  return (
    <div className="flex items-center justify-between py-[9.5px]">
      <div className="flex items-center gap-2 text-[11px] text-cv-fg2">
        <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${dotColor}`} />
        {label}
      </div>
      <span className={`text-[13px] font-semibold tabular-nums ${valueColor}`}>{value}</span>
    </div>
  );
}
