import { useState, useRef, useEffect } from 'react';
import {
  Play, Pause, SkipBack, SkipForward, ChevronLeft, ChevronRight,
  Maximize2, Volume2, VolumeX, Camera, Eye,
  ArrowLeft, ChevronDown, Shield, XCircle, ArrowUpRight, Minus, Plus,
  CheckCircle2, Loader2,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { CV_COLORS } from '@/lib/cv-colors';
import { API_BASE } from '@/lib/api-base';

const TYPE_COLORS: Record<string, string> = {
  dumping_detected: CV_COLORS.purple,
  suspicious_activity: CV_COLORS.orange,
  repeat_offender: CV_COLORS.blue,
};
const TYPE_LABELS: Record<string, string> = {
  dumping_detected: 'Dumping Detected',
  suspicious_activity: 'Suspicious Activity',
  repeat_offender: 'Repeat Offender',
};
const SEVERITY_LABELS: Record<string, string> = { critical: 'High', warning: 'Medium', info: 'Low' };

interface TimelineEvent {
  id: string;
  camera_id: string;
  timestamp: string;
  event_type: string;
  severity: string;
  confidence: number;
  status: string;
  snapshot_url: string | null;
  video_clip_url: string | null;
  notes: string | null;
  timeMin: number;
}

function resolveMediaUrl(url: string | null): string | null {
  if (!url) return null;
  if (url.startsWith('http')) return url;
  return `${API_BASE}${url.startsWith('/') ? '' : '/'}${url}`;
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false, timeZone: 'Asia/Kolkata' });
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Asia/Kolkata' });
}

const ZOOM_LEVELS = [30, 15, 5, 1] as const;

export function TimelineView() {
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [selected, setSelected] = useState<TimelineEvent | null>(null);
  const [loading, setLoading] = useState(true);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [muted, setMuted] = useState(false);
  const [cursorMin, setCursorMin] = useState(0);
  const [zoomLevel, setZoomLevel] = useState<number>(5);
  const [showConfirm, setShowConfirm] = useState(false);
  const [showDismiss, setShowDismiss] = useState(false);
  const [showEscalate, setShowEscalate] = useState(false);
  const [dismissReason, setDismissReason] = useState('');
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    fetch(`${API_BASE}/api/events?limit=50`)
      .then(r => r.ok ? r.json() : [])
      .then((data: any[]) => {
        if (!data.length) { setLoading(false); return; }
        const sorted = [...data].sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());
        const baseTime = new Date(sorted[0].timestamp).getTime();
        const mapped: TimelineEvent[] = sorted.map(e => ({
          id: e.id,
          camera_id: e.camera_id,
          timestamp: e.timestamp,
          event_type: e.event_type,
          severity: e.severity,
          confidence: typeof e.confidence === 'number' ? (e.confidence > 1 ? e.confidence : Math.round(e.confidence * 100)) : 0,
          status: e.status,
          snapshot_url: e.snapshot_url,
          video_clip_url: e.video_clip_url,
          notes: e.notes,
          timeMin: (new Date(e.timestamp).getTime() - baseTime) / 60000,
        }));
        setEvents(mapped);
        setSelected(mapped[0]);
        setCursorMin(mapped[0].timeMin);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  const timeSpanMin = events.length > 1
    ? Math.max(5, events[events.length - 1].timeMin - events[0].timeMin + 2)
    : 30;

  useEffect(() => {
    if (!playing || !videoRef.current) return;
    videoRef.current.playbackRate = speed;
    videoRef.current.play().catch(() => {});
    return () => { videoRef.current?.pause(); };
  }, [playing, speed, selected]);

  const selectEvent = (ev: TimelineEvent) => {
    setSelected(ev);
    setCursorMin(ev.timeMin);
    setPlaying(false);
  };

  const navEvent = (dir: -1 | 1) => {
    if (!selected) return;
    const idx = events.findIndex(e => e.id === selected.id);
    const next = events[idx + dir];
    if (next) selectEvent(next);
  };

  const handleTimelineClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    setCursorMin(pct * timeSpanMin);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full" style={{ background: CV_COLORS.bg }}>
        <Loader2 className="w-6 h-6 animate-spin" style={{ color: CV_COLORS.cyan }} />
        <span className="ml-2 text-xs" style={{ color: CV_COLORS.fg2 }}>Loading events…</span>
      </div>
    );
  }

  if (!selected || !events.length) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-2" style={{ background: CV_COLORS.bg }}>
        <Camera className="w-10 h-10 opacity-30" style={{ color: CV_COLORS.muted }} />
        <p className="text-sm" style={{ color: CV_COLORS.fg2 }}>No events to display</p>
        <p className="text-xs" style={{ color: CV_COLORS.muted }}>Events will appear here once detections are recorded.</p>
      </div>
    );
  }

  const videoUrl = resolveMediaUrl(selected.video_clip_url);
  const snapshotUrl = resolveMediaUrl(selected.snapshot_url);
  const eventTypes = [...new Set(events.map(e => e.event_type))];

  return (
    <div className="flex flex-col h-full overflow-y-auto" style={{ background: CV_COLORS.bg }}>
      {/* Header */}
      <div className="px-4 sm:px-6 py-4 border-b" style={{ borderColor: CV_COLORS.border }}>
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div>
            <p className="text-[11px] mb-1" style={{ color: CV_COLORS.muted }}>{selected.id} / Timeline Playback</p>
            <h1 className="text-base font-semibold" style={{ color: CV_COLORS.fg }}>Timeline Playback</h1>
            <p className="text-xs mt-0.5" style={{ color: CV_COLORS.fg2 }}>Review footage and nearby events around the selected detection.</p>
          </div>
          <button className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium border" style={{ borderColor: CV_COLORS.border, color: CV_COLORS.fg2 }}>
            <ArrowLeft className="w-3.5 h-3.5" /> Back to events
          </button>
        </div>
      </div>

      {/* Event context bar */}
      <div className="mx-4 sm:mx-6 mt-4 px-4 py-3 rounded-lg flex flex-wrap items-center gap-3 text-xs" style={{ background: CV_COLORS.panel, border: `1px solid ${CV_COLORS.border}` }}>
        <span className="font-mono font-semibold" style={{ color: CV_COLORS.fg }}>{selected.id.slice(0, 8)}</span>
        <span style={{ color: CV_COLORS.fg2 }}>{selected.camera_id.slice(0, 8)}</span>
        <span className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-full" style={{ background: TYPE_COLORS[selected.event_type] || CV_COLORS.muted }} />
          <span style={{ color: TYPE_COLORS[selected.event_type] || CV_COLORS.fg2 }}>{TYPE_LABELS[selected.event_type] || selected.event_type}</span>
        </span>
        <span style={{ color: CV_COLORS.fg2 }}>{formatDate(selected.timestamp)}, {formatTime(selected.timestamp)} IST</span>
        <span style={{ color: CV_COLORS.cyan }}>{selected.confidence}% confidence</span>
        <span className="ml-auto px-2 py-0.5 rounded text-[10px] font-medium" style={{
          background: `${selected.status === 'resolved' ? CV_COLORS.green : CV_COLORS.orange}22`,
          color: selected.status === 'resolved' ? CV_COLORS.green : CV_COLORS.orange,
        }}>{selected.status}</span>
      </div>

      {/* Primary footage + inspector */}
      <div className="mx-4 sm:mx-6 mt-4 rounded-lg overflow-hidden" style={{ border: `1px solid ${CV_COLORS.border}` }}>
        <div className="flex flex-col lg:flex-row">
          {/* Video viewport */}
          <div className="lg:w-[72%] relative" style={{ background: '#050d18' }}>
            <div className="aspect-video relative flex items-center justify-center">
              {videoUrl ? (
                <video
                  ref={videoRef}
                  key={videoUrl}
                  src={videoUrl}
                  muted={muted}
                  playsInline
                  className="absolute inset-0 w-full h-full object-contain bg-black"
                  onEnded={() => setPlaying(false)}
                />
              ) : (
                <div className="absolute inset-0 flex items-center justify-center" style={{ background: 'linear-gradient(135deg, #0a1520 0%, #0d1a28 50%, #081218 100%)' }}>
                  <div className="text-center">
                    <Camera className="w-12 h-12 mx-auto mb-2 opacity-20" style={{ color: CV_COLORS.muted }} />
                    <p className="text-xs opacity-40" style={{ color: CV_COLORS.muted }}>No video clip available</p>
                  </div>
                </div>
              )}
              {/* Overlays */}
              <div className="absolute top-3 left-3 text-[10px] font-mono px-2 py-1 rounded z-10" style={{ background: 'rgba(0,0,0,0.6)', color: CV_COLORS.fg2 }}>
                {selected.camera_id.slice(0, 8)} | {formatDate(selected.timestamp)} | {formatTime(selected.timestamp)} IST
              </div>
              <div className="absolute top-3 right-3 text-[10px] px-2 py-1 rounded font-medium z-10" style={{ background: 'rgba(0,0,0,0.6)', color: CV_COLORS.muted }}>
                Recorded
              </div>
            </div>

            {/* Playback controls */}
            <div className="px-4 py-3 flex items-center gap-2 flex-wrap" style={{ background: CV_COLORS.panel, borderTop: `1px solid ${CV_COLORS.border}` }}>
              <button onClick={() => {
                if (!videoRef.current) return;
                if (playing) { videoRef.current.pause(); setPlaying(false); }
                else { videoRef.current.play().catch(() => {}); setPlaying(true); }
              }} className="p-2 rounded hover:bg-white/5" style={{ color: CV_COLORS.cyan }}>
                {playing ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}
              </button>
              <button onClick={() => navEvent(-1)} className="p-1.5 rounded hover:bg-white/5" style={{ color: CV_COLORS.fg2 }} title="Previous event"><SkipBack className="w-3.5 h-3.5" /></button>
              <button onClick={() => { if (videoRef.current) videoRef.current.currentTime = Math.max(0, videoRef.current.currentTime - 1/30); }}
                className="p-1.5 rounded hover:bg-white/5" style={{ color: CV_COLORS.fg2 }} title="Previous frame"><ChevronLeft className="w-3.5 h-3.5" /></button>
              <span className="font-mono text-xs tabular-nums px-2" style={{ color: CV_COLORS.fg }}>{formatTime(selected.timestamp)}</span>
              <button onClick={() => { if (videoRef.current) videoRef.current.currentTime += 1/30; }}
                className="p-1.5 rounded hover:bg-white/5" style={{ color: CV_COLORS.fg2 }} title="Next frame"><ChevronRight className="w-3.5 h-3.5" /></button>
              <button onClick={() => navEvent(1)} className="p-1.5 rounded hover:bg-white/5" style={{ color: CV_COLORS.fg2 }} title="Next event"><SkipForward className="w-3.5 h-3.5" /></button>
              <div className="flex items-center gap-0.5 ml-2">
                {[0.5, 1, 2, 4].map(s => (
                  <button key={s} onClick={() => { setSpeed(s); if (videoRef.current) videoRef.current.playbackRate = s; }}
                    className={cn('px-1.5 py-0.5 rounded text-[10px] font-mono', speed === s ? 'font-bold' : '')}
                    style={{ color: speed === s ? CV_COLORS.cyan : CV_COLORS.muted }}>{s}x</button>
                ))}
              </div>
              <button onClick={() => { setMuted(!muted); if (videoRef.current) videoRef.current.muted = !muted; }}
                className="p-1.5 rounded hover:bg-white/5 ml-auto" style={{ color: CV_COLORS.fg2 }}>
                {muted ? <VolumeX className="w-3.5 h-3.5" /> : <Volume2 className="w-3.5 h-3.5" />}
              </button>
              <button onClick={() => videoRef.current?.requestFullscreen?.()}
                className="p-1.5 rounded hover:bg-white/5" style={{ color: CV_COLORS.fg2 }} title="Fullscreen"><Maximize2 className="w-3.5 h-3.5" /></button>
            </div>
          </div>

          {/* Event inspector */}
          <div className="lg:w-[28%] p-4 overflow-y-auto" style={{ background: CV_COLORS.panel, borderLeft: `1px solid ${CV_COLORS.border}` }}>
            <p className="text-[10px] uppercase tracking-wider font-semibold mb-2" style={{ color: CV_COLORS.muted }}>Selected Event</p>
            <div className="flex items-center gap-2 mb-4">
              <span className="text-sm font-semibold font-mono" style={{ color: CV_COLORS.fg }}>{selected.id.slice(0, 8)}</span>
              <span className="px-1.5 py-0.5 rounded text-[9px] font-medium" style={{
                background: `${selected.status === 'resolved' ? CV_COLORS.green : CV_COLORS.orange}22`,
                color: selected.status === 'resolved' ? CV_COLORS.green : CV_COLORS.orange,
              }}>{selected.status}</span>
            </div>
            <div className="space-y-2.5 text-xs mb-4">
              {([
                ['Detection', TYPE_LABELS[selected.event_type] || selected.event_type, TYPE_COLORS[selected.event_type] || CV_COLORS.fg],
                ['Captured', `${formatTime(selected.timestamp)} IST`, CV_COLORS.fg],
                ['Confidence', `${selected.confidence}%`, CV_COLORS.cyan],
                ['Priority', SEVERITY_LABELS[selected.severity] || selected.severity, selected.severity === 'critical' ? '#ef4444' : CV_COLORS.fg],
                ['Camera', selected.camera_id.slice(0, 8), CV_COLORS.fg],
              ] as [string, string, string][]).map(([k, v, c]) => (
                <div key={k} className="flex justify-between">
                  <span style={{ color: CV_COLORS.muted }}>{k}</span>
                  <span className="font-medium" style={{ color: c }}>{v}</span>
                </div>
              ))}
            </div>

            {/* Evidence strip */}
            <p className="text-[10px] uppercase tracking-wider font-semibold mb-2" style={{ color: CV_COLORS.muted }}>Evidence</p>
            <div className="grid grid-cols-2 gap-1.5 mb-4">
              {snapshotUrl ? (
                <div className="aspect-video rounded overflow-hidden col-span-2" style={{ border: `2px solid ${CV_COLORS.cyan}` }}>
                  <img src={snapshotUrl} alt="Evidence snapshot" className="w-full h-full object-cover" />
                </div>
              ) : (
                <div className="aspect-video rounded col-span-2 flex items-center justify-center" style={{ background: CV_COLORS.track, border: `1px solid ${CV_COLORS.border}` }}>
                  <Camera className="w-4 h-4 opacity-30" style={{ color: CV_COLORS.muted }} />
                </div>
              )}
            </div>

            {/* Actions */}
            <div className="space-y-1.5">
              <button className="w-full text-left px-3 py-2 rounded text-xs hover:bg-white/5 flex items-center gap-2" style={{ color: CV_COLORS.cyan }}>
                <Eye className="w-3.5 h-3.5" /> Open event detail
              </button>
              <button className="w-full text-left px-3 py-2 rounded text-xs hover:bg-white/5 flex items-center gap-2" style={{ color: CV_COLORS.fg2 }}>
                <Maximize2 className="w-3.5 h-3.5" /> View full evidence
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Unified timeline */}
      <div className="mx-4 sm:mx-6 mt-4 p-4 rounded-lg" style={{ background: CV_COLORS.panel, border: `1px solid ${CV_COLORS.border}` }}>
        <div className="flex items-center justify-between mb-3">
          <div>
            <p className="text-xs font-semibold" style={{ color: CV_COLORS.fg }}>Event timeline</p>
            <p className="text-[10px]" style={{ color: CV_COLORS.muted }}>{events.length} events · {formatDate(events[0].timestamp)}</p>
          </div>
          <div className="flex items-center gap-1">
            <button onClick={() => { const i = ZOOM_LEVELS.indexOf(zoomLevel as any); if (i > 0) setZoomLevel(ZOOM_LEVELS[i - 1]); }}
              className="p-1 rounded hover:bg-white/5" style={{ color: CV_COLORS.fg2 }}><Minus className="w-3 h-3" /></button>
            <span className="text-[10px] font-mono px-2 tabular-nums" style={{ color: CV_COLORS.cyan }}>{zoomLevel} min</span>
            <button onClick={() => { const i = ZOOM_LEVELS.indexOf(zoomLevel as any); if (i < ZOOM_LEVELS.length - 1) setZoomLevel(ZOOM_LEVELS[i + 1]); }}
              className="p-1 rounded hover:bg-white/5" style={{ color: CV_COLORS.fg2 }}><Plus className="w-3 h-3" /></button>
          </div>
        </div>

        {/* Overview bar */}
        <div className="relative h-8 rounded cursor-pointer mb-3" style={{ background: CV_COLORS.track }} onClick={handleTimelineClick}>
          <div className="absolute inset-0 rounded opacity-20" style={{ background: CV_COLORS.blue }} />
          {events.map(ev => {
            const left = `${(ev.timeMin / timeSpanMin) * 100}%`;
            const isSel = ev.id === selected.id;
            return (
              <div key={ev.id} className="absolute top-1 cursor-pointer" style={{ left, transform: 'translateX(-50%)' }}
                onClick={e => { e.stopPropagation(); selectEvent(ev); }}>
                <div className={cn('w-[10px] h-5 rounded-sm', isSel && 'ring-2 ring-offset-1')}
                  style={{ background: TYPE_COLORS[ev.event_type] || CV_COLORS.muted, ...(isSel ? { ringColor: CV_COLORS.cyan } : {}) }}
                  title={`${ev.id.slice(0, 8)} ${formatTime(ev.timestamp)}`} />
              </div>
            );
          })}
          <div className="absolute top-0 h-full w-0.5" style={{ left: `${(cursorMin / timeSpanMin) * 100}%`, background: CV_COLORS.cyan, transition: 'left 0.1s' }}>
            <div className="absolute -top-1 left-1/2 -translate-x-1/2 w-2 h-2 rounded-full" style={{ background: CV_COLORS.cyan }} />
          </div>
        </div>

        {/* Type lanes */}
        <div className="space-y-1 overflow-x-auto">
          {eventTypes.map(type => {
            const typeEvents = events.filter(e => e.event_type === type);
            return (
              <div key={type} className="flex items-center gap-2 min-w-[500px]">
                <span className="w-32 text-[10px] truncate flex-shrink-0 flex items-center gap-1.5" style={{ color: TYPE_COLORS[type] || CV_COLORS.fg2 }}>
                  <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: TYPE_COLORS[type] || CV_COLORS.muted }} />
                  {TYPE_LABELS[type] || type}
                </span>
                <div className="flex-1 relative h-6 rounded" style={{ background: CV_COLORS.track }} onClick={handleTimelineClick}>
                  {typeEvents.map(ev => {
                    const left = `${(ev.timeMin / timeSpanMin) * 100}%`;
                    const isSel = ev.id === selected.id;
                    return (
                      <div key={ev.id} className="absolute top-0.5 cursor-pointer group" style={{ left, transform: 'translateX(-50%)' }}
                        onClick={e => { e.stopPropagation(); selectEvent(ev); }}>
                        <div className="w-2.5 h-5 rounded-sm" style={{ background: TYPE_COLORS[ev.event_type] || CV_COLORS.muted, outline: isSel ? `2px solid ${CV_COLORS.cyan}` : 'none', outlineOffset: '2px' }} />
                        <div className="hidden group-hover:block absolute bottom-full left-1/2 -translate-x-1/2 mb-1 px-2 py-1.5 rounded text-[10px] whitespace-nowrap z-10"
                          style={{ background: CV_COLORS.bg, border: `1px solid ${CV_COLORS.border}`, color: CV_COLORS.fg2 }}>
                          <div className="font-mono font-semibold" style={{ color: CV_COLORS.fg }}>{ev.id.slice(0, 8)}</div>
                          <div>{formatTime(ev.timestamp)} · {ev.confidence}%</div>
                        </div>
                      </div>
                    );
                  })}
                  <div className="absolute top-0 h-full w-px" style={{ left: `${(cursorMin / timeSpanMin) * 100}%`, background: CV_COLORS.cyan, opacity: 0.5 }} />
                </div>
              </div>
            );
          })}
        </div>
        <p className="text-[10px] mt-2 font-mono" style={{ color: CV_COLORS.cyan }}>{selected.id.slice(0, 8)}, {formatTime(selected.timestamp)}</p>
      </div>

      {/* Bottom row: Nearby events + Review actions */}
      <div className="mx-4 sm:mx-6 mt-4 mb-6 flex flex-col lg:flex-row gap-4">
        {/* Nearby events */}
        <div className="lg:w-[58%] rounded-lg p-4" style={{ background: CV_COLORS.panel, border: `1px solid ${CV_COLORS.border}` }}>
          <p className="text-xs font-semibold mb-1" style={{ color: CV_COLORS.fg }}>Events</p>
          <p className="text-[10px] mb-3" style={{ color: CV_COLORS.muted }}>{events.length} detections in this window.</p>
          <div className="hidden sm:block">
            <table className="w-full text-xs">
              <thead>
                <tr style={{ color: CV_COLORS.muted }}>
                  <th className="text-left font-medium pb-2">Time</th>
                  <th className="text-left font-medium pb-2">Camera</th>
                  <th className="text-left font-medium pb-2">Event type</th>
                  <th className="text-left font-medium pb-2">Confidence</th>
                  <th className="text-right font-medium pb-2"></th>
                </tr>
              </thead>
              <tbody>
                {events.map(ev => (
                  <tr key={ev.id} className={cn('border-t', ev.id === selected.id && 'bg-white/[0.03]')} style={{ borderColor: CV_COLORS.border }}>
                    <td className="py-2.5 font-mono" style={{ color: CV_COLORS.fg }}>{formatTime(ev.timestamp)}</td>
                    <td className="py-2.5" style={{ color: CV_COLORS.fg2 }}>{ev.camera_id.slice(0, 8)}</td>
                    <td className="py-2.5">
                      <span className="flex items-center gap-1.5">
                        <span className="w-2 h-2 rounded-full" style={{ background: TYPE_COLORS[ev.event_type] || CV_COLORS.muted }} />
                        <span style={{ color: TYPE_COLORS[ev.event_type] || CV_COLORS.fg2 }}>{TYPE_LABELS[ev.event_type] || ev.event_type}</span>
                      </span>
                    </td>
                    <td className="py-2.5 font-mono" style={{ color: CV_COLORS.fg2 }}>{ev.confidence}%</td>
                    <td className="py-2.5 text-right">
                      {ev.id === selected.id ? (
                        <span className="text-[10px] font-medium" style={{ color: CV_COLORS.cyan }}>Selected</span>
                      ) : (
                        <button onClick={() => selectEvent(ev)} className="text-[10px] font-medium hover:underline" style={{ color: CV_COLORS.cyan }}>Open</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="sm:hidden space-y-2">
            {events.map(ev => (
              <div key={ev.id} className="p-3 rounded-md cursor-pointer" style={{ background: ev.id === selected.id ? `${CV_COLORS.cyan}08` : CV_COLORS.track, border: `1px solid ${ev.id === selected.id ? CV_COLORS.cyan + '33' : CV_COLORS.border}` }}
                onClick={() => selectEvent(ev)}>
                <div className="flex items-center justify-between mb-1">
                  <span className="font-mono text-xs" style={{ color: CV_COLORS.fg }}>{formatTime(ev.timestamp)}</span>
                  <span className="flex items-center gap-1 text-[10px]"><span className="w-1.5 h-1.5 rounded-full" style={{ background: TYPE_COLORS[ev.event_type] || CV_COLORS.muted }} /><span style={{ color: TYPE_COLORS[ev.event_type] || CV_COLORS.fg2 }}>{TYPE_LABELS[ev.event_type] || ev.event_type}</span></span>
                </div>
                <div className="flex items-center justify-between text-[10px]" style={{ color: CV_COLORS.muted }}>
                  <span>{ev.camera_id.slice(0, 8)}</span><span>{ev.confidence}%</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Review actions */}
        <div className="lg:w-[42%] rounded-lg p-4" style={{ background: CV_COLORS.panel, border: `1px solid ${CV_COLORS.border}` }}>
          <p className="text-xs font-semibold mb-3" style={{ color: CV_COLORS.fg }}>Review actions</p>
          {snapshotUrl && (
            <div className="flex items-center gap-2 mb-2 text-xs">
              <CheckCircle2 className="w-3.5 h-3.5" style={{ color: CV_COLORS.green }} />
              <span style={{ color: CV_COLORS.green }}>Evidence available</span>
            </div>
          )}
          <p className="text-[10px] mb-4" style={{ color: CV_COLORS.muted }}>
            {videoUrl ? 'Video clip and snapshot available.' : 'Review detection data before confirming.'}
          </p>
          <div className="space-y-2">
            <button onClick={() => setShowConfirm(true)} className="w-full py-2.5 rounded-md text-xs font-medium flex items-center justify-center gap-1.5"
              style={{ background: CV_COLORS.green, color: CV_COLORS.bg }}>
              <Shield className="w-3.5 h-3.5" /> Confirm event
            </button>
            <button onClick={() => setShowDismiss(true)} className="w-full py-2.5 rounded-md text-xs font-medium border flex items-center justify-center gap-1.5"
              style={{ borderColor: CV_COLORS.border, color: CV_COLORS.fg2 }}>
              <XCircle className="w-3.5 h-3.5" /> Dismiss event
            </button>
            <button onClick={() => setShowEscalate(true)} className="w-full py-2.5 rounded-md text-xs font-medium border flex items-center justify-center gap-1.5"
              style={{ borderColor: `${CV_COLORS.orange}66`, color: CV_COLORS.orange }}>
              <ArrowUpRight className="w-3.5 h-3.5" /> Escalate
            </button>
          </div>
        </div>
      </div>

      {/* Confirm modal */}
      {showConfirm && (
        <Modal title="Confirm event" onClose={() => setShowConfirm(false)}>
          <p className="text-xs mb-4" style={{ color: CV_COLORS.fg2 }}>Mark <b style={{ color: CV_COLORS.fg }}>{selected.id.slice(0, 8)}</b> as confirmed.</p>
          <div className="flex gap-2 justify-end">
            <button onClick={() => setShowConfirm(false)} className="px-3 py-1.5 rounded text-xs border" style={{ borderColor: CV_COLORS.border, color: CV_COLORS.fg2 }}>Cancel</button>
            <button onClick={() => setShowConfirm(false)} className="px-3 py-1.5 rounded text-xs font-medium" style={{ background: CV_COLORS.green, color: CV_COLORS.bg }}>Confirm</button>
          </div>
        </Modal>
      )}

      {/* Dismiss modal */}
      {showDismiss && (
        <Modal title="Dismiss event" onClose={() => setShowDismiss(false)}>
          <p className="text-xs mb-3" style={{ color: CV_COLORS.fg2 }}>Provide a reason for dismissing <b style={{ color: CV_COLORS.fg }}>{selected.id.slice(0, 8)}</b>.</p>
          <textarea value={dismissReason} onChange={e => setDismissReason(e.target.value)} placeholder="Reason for dismissal…"
            className="w-full px-3 py-2 rounded text-xs mb-3 resize-none h-20" style={{ background: CV_COLORS.track, border: `1px solid ${CV_COLORS.border}`, color: CV_COLORS.fg }} />
          <div className="flex gap-2 justify-end">
            <button onClick={() => setShowDismiss(false)} className="px-3 py-1.5 rounded text-xs border" style={{ borderColor: CV_COLORS.border, color: CV_COLORS.fg2 }}>Cancel</button>
            <button onClick={() => { setShowDismiss(false); setDismissReason(''); }} disabled={!dismissReason.trim()} className="px-3 py-1.5 rounded text-xs font-medium disabled:opacity-40" style={{ background: CV_COLORS.border, color: CV_COLORS.fg }}>Dismiss</button>
          </div>
        </Modal>
      )}

      {/* Escalate modal */}
      {showEscalate && (
        <Modal title="Escalate event" onClose={() => setShowEscalate(false)}>
          <div className="space-y-3 text-xs">
            <div>
              <label className="block mb-1" style={{ color: CV_COLORS.muted }}>Assignee</label>
              <input className="w-full px-3 py-1.5 rounded" style={{ background: CV_COLORS.track, border: `1px solid ${CV_COLORS.border}`, color: CV_COLORS.fg }} placeholder="Select assignee" />
            </div>
            <div>
              <label className="block mb-1" style={{ color: CV_COLORS.muted }}>Notes</label>
              <textarea className="w-full px-3 py-2 rounded resize-none h-16" style={{ background: CV_COLORS.track, border: `1px solid ${CV_COLORS.border}`, color: CV_COLORS.fg }} placeholder="Additional notes…" />
            </div>
          </div>
          <div className="flex gap-2 justify-end mt-4">
            <button onClick={() => setShowEscalate(false)} className="px-3 py-1.5 rounded text-xs border" style={{ borderColor: CV_COLORS.border, color: CV_COLORS.fg2 }}>Cancel</button>
            <button onClick={() => setShowEscalate(false)} className="px-3 py-1.5 rounded text-xs font-medium" style={{ background: CV_COLORS.orange, color: CV_COLORS.bg }}>Escalate</button>
          </div>
        </Modal>
      )}
    </div>
  );
}

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center" role="dialog" aria-modal="true" aria-label={title} onClick={onClose}>
      <div className="absolute inset-0 bg-black/60" />
      <div className="relative w-full max-w-md mx-4 rounded-lg p-5" style={{ background: CV_COLORS.panel, border: `1px solid ${CV_COLORS.border}` }} onClick={e => e.stopPropagation()}>
        <p className="text-sm font-semibold mb-3" style={{ color: CV_COLORS.fg }}>{title}</p>
        {children}
      </div>
    </div>
  );
}
