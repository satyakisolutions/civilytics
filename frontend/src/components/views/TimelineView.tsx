import { useState, useRef, useMemo, useEffect, useCallback } from 'react';
import {
  Play, Pause, SkipBack, SkipForward, ChevronLeft, ChevronRight,
  Maximize2, Volume2, VolumeX, Camera, MapPin, Clock, Eye,
  ArrowLeft, ChevronDown, Shield, XCircle, ArrowUpRight, Minus, Plus,
  CheckCircle2,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { CV_COLORS } from '@/lib/cv-colors';

// Event type colors
const TYPE_COLORS = {
  'Midnight Walk': CV_COLORS.purple,
  'Midnight Vehicle': CV_COLORS.blue,
  'Daytime Walk': CV_COLORS.orange,
  'Daytime Vehicle': CV_COLORS.green,
} as const;
type EventType = keyof typeof TYPE_COLORS;
const EVENT_TYPES: EventType[] = ['Midnight Walk', 'Midnight Vehicle', 'Daytime Walk', 'Daytime Vehicle'];

interface TimelineEvent {
  id: string;
  camera: string;
  zone: string;
  location: string;
  type: EventType;
  time: string; // HH:MM:SS
  timeMin: number; // minutes from timeline start
  confidence: number;
  priority: 'High' | 'Medium' | 'Low';
  status: 'Pending review' | 'Confirmed' | 'Dismissed';
}

// Demo data
const SELECTED_EVENT: TimelineEvent = {
  id: 'EVT-240930-028', camera: 'AD-04', zone: 'Adugodi', location: 'Adugodi 5th Cross, Bengaluru',
  type: 'Midnight Walk', time: '21:42:18', timeMin: 12.3, confidence: 94, priority: 'High', status: 'Pending review',
};

const NEARBY_EVENTS: TimelineEvent[] = [
  { id: 'EVT-240930-025', camera: 'AD-04', zone: 'Adugodi', location: 'Adugodi South Lane', type: 'Midnight Vehicle', time: '21:37:45', timeMin: 7.75, confidence: 92, priority: 'Medium', status: 'Pending review' },
  SELECTED_EVENT,
  { id: 'EVT-240930-030', camera: 'AD-03', zone: 'Adugodi', location: 'Adugodi 3rd Cross', type: 'Midnight Walk', time: '21:48:22', timeMin: 18.37, confidence: 88, priority: 'Medium', status: 'Pending review' },
  { id: 'EVT-240930-033', camera: 'AD-04', zone: 'Adugodi', location: 'Adugodi South Lane', type: 'Midnight Vehicle', time: '21:56:04', timeMin: 26.07, confidence: 90, priority: 'Medium', status: 'Pending review' },
];

const ZOOM_LEVELS = [30, 15, 5, 1] as const;

export function TimelineView() {
  const [selected, setSelected] = useState<TimelineEvent>(SELECTED_EVENT);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [muted, setMuted] = useState(false);
  const [cursorMin, setCursorMin] = useState(SELECTED_EVENT.timeMin);
  const [zoomLevel, setZoomLevel] = useState<number>(5);
  const [showConfirm, setShowConfirm] = useState(false);
  const [showDismiss, setShowDismiss] = useState(false);
  const [showEscalate, setShowEscalate] = useState(false);
  const [dismissReason, setDismissReason] = useState('');
  const timelineRef = useRef<HTMLDivElement>(null);

  // Playback simulation
  useEffect(() => {
    if (!playing) return;
    const iv = setInterval(() => setCursorMin(c => Math.min(30, c + 0.05 * speed)), 100);
    return () => clearInterval(iv);
  }, [playing, speed]);

  const formatCursorTime = (min: number) => {
    const totalSec = Math.floor((21 * 60 + 30) * 60 + min * 60);
    const h = Math.floor(totalSec / 3600);
    const m = Math.floor((totalSec % 3600) / 60);
    const s = totalSec % 60;
    return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  };

  const selectEvent = (ev: TimelineEvent) => {
    setSelected(ev);
    setCursorMin(ev.timeMin);
    setPlaying(false);
  };

  const navEvent = (dir: -1 | 1) => {
    const idx = NEARBY_EVENTS.findIndex(e => e.id === selected.id);
    const next = NEARBY_EVENTS[idx + dir];
    if (next) selectEvent(next);
  };

  const handleTimelineClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    setCursorMin(pct * 30);
  };

  return (
    <div className="flex flex-col h-full overflow-y-auto" style={{ background: CV_COLORS.bg }}>
      {/* Header */}
      <div className="px-4 sm:px-6 py-4 border-b" style={{ borderColor: CV_COLORS.border }}>
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div>
            <p className="text-[11px] mb-1" style={{ color: CV_COLORS.muted }}>Bengaluru South / Event History / {selected.id} / Timeline Playback</p>
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
        <span className="font-mono font-semibold" style={{ color: CV_COLORS.fg }}>{selected.id}</span>
        <span style={{ color: CV_COLORS.fg2 }}>{selected.camera}</span>
        <span style={{ color: CV_COLORS.fg2 }}>{selected.location}</span>
        <span className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-full" style={{ background: TYPE_COLORS[selected.type] }} />
          <span style={{ color: TYPE_COLORS[selected.type] }}>{selected.type}</span>
        </span>
        <span style={{ color: CV_COLORS.fg2 }}>30 Sep 2026, {selected.time} IST</span>
        <span style={{ color: CV_COLORS.cyan }}>{selected.confidence}% confidence</span>
        <span className="ml-auto px-2 py-0.5 rounded text-[10px] font-medium" style={{ background: `${CV_COLORS.orange}22`, color: CV_COLORS.orange }}>{selected.status}</span>
      </div>

      {/* Playback toolbar */}
      <div className="mx-4 sm:mx-6 mt-3 px-4 py-2.5 flex flex-wrap items-center gap-3 text-xs border-b" style={{ borderColor: CV_COLORS.border }}>
        <FilterSelect value={`${selected.camera}, Adugodi South Lane`} options={['AD-04, Adugodi South Lane', 'AD-03, Adugodi 3rd Cross', 'AD-01, Adugodi Main Road']} onChange={() => {}} />
        <FilterSelect value="30 Sep 2026" options={['30 Sep 2026', '29 Sep 2026', '28 Sep 2026']} onChange={() => {}} />
        <FilterSelect value="9:30 PM – 10:00 PM" options={['±15 min of event', '±30 min of event', 'Last 1 hour', 'Custom range']} onChange={() => {}} />
        <div className="ml-auto flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full" style={{ background: CV_COLORS.green }} />
          <span style={{ color: CV_COLORS.green }}>Available</span>
          <span style={{ color: CV_COLORS.muted }}>· 30-day retention</span>
        </div>
      </div>

      {/* Primary footage + inspector */}
      <div className="mx-4 sm:mx-6 mt-4 rounded-lg overflow-hidden" style={{ border: `1px solid ${CV_COLORS.border}` }}>
        <div className="flex flex-col lg:flex-row">
          {/* Video viewport */}
          <div className="lg:w-[72%] relative" style={{ background: '#050d18' }}>
            <div className="aspect-video relative flex items-center justify-center">
              {/* Dark CCTV placeholder */}
              <div className="absolute inset-0 flex items-center justify-center" style={{ background: 'linear-gradient(135deg, #0a1520 0%, #0d1a28 50%, #081218 100%)' }}>
                <div className="text-center">
                  <Camera className="w-12 h-12 mx-auto mb-2 opacity-20" style={{ color: CV_COLORS.muted }} />
                  <p className="text-xs opacity-40" style={{ color: CV_COLORS.muted }}>Recorded footage viewport</p>
                </div>
              </div>
              {/* Overlays */}
              <div className="absolute top-3 left-3 text-[10px] font-mono px-2 py-1 rounded" style={{ background: 'rgba(0,0,0,0.6)', color: CV_COLORS.fg2 }}>
                {selected.camera} | 30 Sep 2026 | {formatCursorTime(cursorMin)} IST
              </div>
              <div className="absolute top-3 right-3 text-[10px] px-2 py-1 rounded font-medium" style={{ background: 'rgba(0,0,0,0.6)', color: CV_COLORS.muted }}>
                Recorded
              </div>
              {/* Detection label near center */}
              {Math.abs(cursorMin - selected.timeMin) < 0.5 && (
                <div className="absolute bottom-16 left-1/2 -translate-x-1/2 px-2 py-1 rounded text-[10px] font-medium" style={{ border: `1px solid ${CV_COLORS.cyan}`, color: CV_COLORS.cyan, background: 'rgba(0,0,0,0.7)' }}>
                  {selected.type}, {selected.confidence}%
                </div>
              )}
            </div>

            {/* Playback controls */}
            <div className="px-4 py-3 flex items-center gap-2 flex-wrap" style={{ background: CV_COLORS.panel, borderTop: `1px solid ${CV_COLORS.border}` }}>
              <button onClick={() => setPlaying(!playing)} className="p-2 rounded hover:bg-white/5" style={{ color: CV_COLORS.cyan }}>
                {playing ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}
              </button>
              <button onClick={() => navEvent(-1)} className="p-1.5 rounded hover:bg-white/5" style={{ color: CV_COLORS.fg2 }} title="Previous event"><SkipBack className="w-3.5 h-3.5" /></button>
              <button onClick={() => setCursorMin(c => Math.max(0, c - 0.017))} className="p-1.5 rounded hover:bg-white/5" style={{ color: CV_COLORS.fg2 }} title="Previous frame"><ChevronLeft className="w-3.5 h-3.5" /></button>
              <span className="font-mono text-xs tabular-nums px-2" style={{ color: CV_COLORS.fg }}>{formatCursorTime(cursorMin)} / 30:00</span>
              <button onClick={() => setCursorMin(c => Math.min(30, c + 0.017))} className="p-1.5 rounded hover:bg-white/5" style={{ color: CV_COLORS.fg2 }} title="Next frame"><ChevronRight className="w-3.5 h-3.5" /></button>
              <button onClick={() => navEvent(1)} className="p-1.5 rounded hover:bg-white/5" style={{ color: CV_COLORS.fg2 }} title="Next event"><SkipForward className="w-3.5 h-3.5" /></button>
              <div className="flex items-center gap-0.5 ml-2">
                {[0.5, 1, 2, 4].map(s => (
                  <button key={s} onClick={() => setSpeed(s)} className={cn('px-1.5 py-0.5 rounded text-[10px] font-mono', speed === s ? 'font-bold' : '')} style={{ color: speed === s ? CV_COLORS.cyan : CV_COLORS.muted }}>{s}x</button>
                ))}
              </div>
              <button onClick={() => setMuted(!muted)} className="p-1.5 rounded hover:bg-white/5 ml-auto" style={{ color: CV_COLORS.fg2 }}>
                {muted ? <VolumeX className="w-3.5 h-3.5" /> : <Volume2 className="w-3.5 h-3.5" />}
              </button>
              <button className="p-1.5 rounded hover:bg-white/5" style={{ color: CV_COLORS.fg2 }} title="Fullscreen"><Maximize2 className="w-3.5 h-3.5" /></button>
            </div>
          </div>

          {/* Event inspector */}
          <div className="lg:w-[28%] p-4 overflow-y-auto" style={{ background: CV_COLORS.panel, borderLeft: `1px solid ${CV_COLORS.border}` }}>
            <p className="text-[10px] uppercase tracking-wider font-semibold mb-2" style={{ color: CV_COLORS.muted }}>Selected Event</p>
            <div className="flex items-center gap-2 mb-4">
              <span className="text-sm font-semibold font-mono" style={{ color: CV_COLORS.fg }}>{selected.id}</span>
              <span className="px-1.5 py-0.5 rounded text-[9px] font-medium" style={{ background: `${CV_COLORS.orange}22`, color: CV_COLORS.orange }}>{selected.status}</span>
            </div>
            <div className="space-y-2.5 text-xs mb-4">
              {([
                ['Detection', selected.type, TYPE_COLORS[selected.type]],
                ['Captured', `${selected.time} IST`, CV_COLORS.fg],
                ['Confidence', `${selected.confidence}%`, CV_COLORS.cyan],
                ['Priority', selected.priority, selected.priority === 'High' ? '#ef4444' : CV_COLORS.fg],
                ['Camera', selected.camera, CV_COLORS.fg],
                ['Zone', selected.zone, CV_COLORS.fg],
              ] as [string, string, string][]).map(([k, v, c]) => (
                <div key={k} className="flex justify-between">
                  <span style={{ color: CV_COLORS.muted }}>{k}</span>
                  <span className="font-medium" style={{ color: c }}>{v}</span>
                </div>
              ))}
            </div>

            {/* Evidence strip */}
            <p className="text-[10px] uppercase tracking-wider font-semibold mb-2" style={{ color: CV_COLORS.muted }}>Evidence frames</p>
            <div className="grid grid-cols-4 gap-1.5 mb-4">
              {[0, 1, 2, 3].map(i => (
                <div key={i} className="aspect-video rounded cursor-pointer" style={{ background: CV_COLORS.track, border: i === 0 ? `2px solid ${CV_COLORS.cyan}` : `1px solid ${CV_COLORS.border}` }}>
                  <div className="w-full h-full flex items-center justify-center">
                    <Camera className="w-3 h-3 opacity-30" style={{ color: CV_COLORS.muted }} />
                  </div>
                </div>
              ))}
            </div>

            {/* Actions */}
            <div className="space-y-1.5">
              <button className="w-full text-left px-3 py-2 rounded text-xs hover:bg-white/5 flex items-center gap-2" style={{ color: CV_COLORS.cyan }}>
                <Eye className="w-3.5 h-3.5" /> Open event detail
              </button>
              <button className="w-full text-left px-3 py-2 rounded text-xs hover:bg-white/5 flex items-center gap-2" style={{ color: CV_COLORS.fg2 }}>
                <Maximize2 className="w-3.5 h-3.5" /> View full evidence
              </button>
              <button className="w-full text-left px-3 py-2 rounded text-xs hover:bg-white/5 flex items-center gap-2" style={{ color: CV_COLORS.fg2 }}>
                <ArrowUpRight className="w-3.5 h-3.5" /> Track person
              </button>
            </div>
            <p className="text-[10px] mt-4 italic" style={{ color: CV_COLORS.muted }}>Review the full clip before confirming the detection.</p>
          </div>
        </div>
      </div>

      {/* Overview timeline */}
      <div className="mx-4 sm:mx-6 mt-4 p-4 rounded-lg" style={{ background: CV_COLORS.panel, border: `1px solid ${CV_COLORS.border}` }}>
        <div className="flex items-center justify-between mb-3">
          <div>
            <p className="text-xs font-semibold" style={{ color: CV_COLORS.fg }}>Recorded activity overview</p>
            <p className="text-[10px]" style={{ color: CV_COLORS.muted }}>{selected.camera}, 9:30 PM – 10:00 PM</p>
          </div>
        </div>
        {/* Time axis */}
        <div className="flex justify-between text-[9px] font-mono mb-1" style={{ color: CV_COLORS.muted }}>
          {['9:30', '9:35', '9:40', '9:45', '9:50', '9:55', '10:00'].map(t => <span key={t}>{t} PM</span>)}
        </div>
        {/* Availability bar + events */}
        <div ref={timelineRef} className="relative h-10 rounded cursor-pointer" style={{ background: CV_COLORS.track }} onClick={handleTimelineClick}>
          {/* Availability fill */}
          <div className="absolute inset-0 rounded opacity-30" style={{ background: CV_COLORS.blue }} />
          {/* Event blocks */}
          {NEARBY_EVENTS.map(ev => {
            const left = `${(ev.timeMin / 30) * 100}%`;
            const isSelected = ev.id === selected.id;
            return (
              <div key={ev.id} className="absolute top-1 cursor-pointer" style={{ left, transform: 'translateX(-50%)' }}
                onClick={e => { e.stopPropagation(); selectEvent(ev); }}>
                <div className={cn('w-[12px] h-6 rounded-sm', isSelected && 'ring-2 ring-offset-1')}
                  style={{ background: TYPE_COLORS[ev.type], ...(isSelected ? { ringColor: CV_COLORS.cyan } : {}) }}
                  title={`${ev.id} ${ev.time}`} />
              </div>
            );
          })}
          {/* Cursor */}
          <div className="absolute top-0 h-full w-0.5" style={{ left: `${(cursorMin / 30) * 100}%`, background: CV_COLORS.cyan, transition: 'left 0.1s' }}>
            <div className="absolute -top-1 left-1/2 -translate-x-1/2 w-2 h-2 rounded-full" style={{ background: CV_COLORS.cyan }} />
          </div>
        </div>
      </div>

      {/* Detailed timeline */}
      <div className="mx-4 sm:mx-6 mt-4 p-4 rounded-lg" style={{ background: CV_COLORS.panel, border: `1px solid ${CV_COLORS.border}` }}>
        <div className="flex items-center justify-between mb-3">
          <p className="text-xs font-semibold" style={{ color: CV_COLORS.fg }}>Detailed event timeline</p>
          <div className="flex items-center gap-1">
            <button onClick={() => { const i = ZOOM_LEVELS.indexOf(zoomLevel as any); if (i > 0) setZoomLevel(ZOOM_LEVELS[i - 1]); }}
              className="p-1 rounded hover:bg-white/5" style={{ color: CV_COLORS.fg2 }}><Minus className="w-3 h-3" /></button>
            <span className="text-[10px] font-mono px-2 tabular-nums" style={{ color: CV_COLORS.cyan }}>{zoomLevel} min</span>
            <button onClick={() => { const i = ZOOM_LEVELS.indexOf(zoomLevel as any); if (i < ZOOM_LEVELS.length - 1) setZoomLevel(ZOOM_LEVELS[i + 1]); }}
              className="p-1 rounded hover:bg-white/5" style={{ color: CV_COLORS.fg2 }}><Plus className="w-3 h-3" /></button>
          </div>
        </div>
        {/* Lanes */}
        <div className="space-y-1 overflow-x-auto">
          {EVENT_TYPES.map(type => {
            const events = NEARBY_EVENTS.filter(e => e.type === type);
            return (
              <div key={type} className="flex items-center gap-2 min-w-[500px]">
                <span className="w-28 text-[10px] truncate flex-shrink-0 flex items-center gap-1.5" style={{ color: TYPE_COLORS[type] }}>
                  <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: TYPE_COLORS[type] }} />
                  {type}
                </span>
                <div className="flex-1 relative h-6 rounded" style={{ background: CV_COLORS.track }} onClick={handleTimelineClick}>
                  {events.map(ev => {
                    const left = `${(ev.timeMin / 30) * 100}%`;
                    const isSelected = ev.id === selected.id;
                    return (
                      <div key={ev.id} className="absolute top-0.5 cursor-pointer group" style={{ left, transform: 'translateX(-50%)' }}
                        onClick={e => { e.stopPropagation(); selectEvent(ev); }}>
                        <div className="w-2.5 h-5 rounded-sm" style={{ background: TYPE_COLORS[ev.type], outline: isSelected ? `2px solid ${CV_COLORS.cyan}` : 'none', outlineOffset: '2px' }} />
                        {/* Hover card */}
                        <div className="hidden group-hover:block absolute bottom-full left-1/2 -translate-x-1/2 mb-1 px-2 py-1.5 rounded text-[10px] whitespace-nowrap z-10"
                          style={{ background: CV_COLORS.bg, border: `1px solid ${CV_COLORS.border}`, color: CV_COLORS.fg2 }}>
                          <div className="font-mono font-semibold" style={{ color: CV_COLORS.fg }}>{ev.id}</div>
                          <div>{ev.time} · {ev.camera} · {ev.confidence}%</div>
                        </div>
                      </div>
                    );
                  })}
                  {/* Cursor on lane */}
                  <div className="absolute top-0 h-full w-px" style={{ left: `${(cursorMin / 30) * 100}%`, background: CV_COLORS.cyan, opacity: 0.5 }} />
                </div>
              </div>
            );
          })}
        </div>
        {/* Selected label */}
        <p className="text-[10px] mt-2 font-mono" style={{ color: CV_COLORS.cyan }}>{selected.id}, {selected.time}</p>
      </div>

      {/* Bottom row: Nearby events + Review actions */}
      <div className="mx-4 sm:mx-6 mt-4 mb-6 flex flex-col lg:flex-row gap-4">
        {/* Nearby events */}
        <div className="lg:w-[58%] rounded-lg p-4" style={{ background: CV_COLORS.panel, border: `1px solid ${CV_COLORS.border}` }}>
          <p className="text-xs font-semibold mb-1" style={{ color: CV_COLORS.fg }}>Nearby events</p>
          <p className="text-[10px] mb-3" style={{ color: CV_COLORS.muted }}>Same camera and adjacent cameras, within 30 minutes.</p>
          {/* Desktop table */}
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
                {NEARBY_EVENTS.map(ev => (
                  <tr key={ev.id} className={cn('border-t', ev.id === selected.id && 'bg-white/[0.03]')} style={{ borderColor: CV_COLORS.border }}>
                    <td className="py-2.5 font-mono" style={{ color: CV_COLORS.fg }}>{ev.time}</td>
                    <td className="py-2.5" style={{ color: CV_COLORS.fg2 }}>{ev.camera}</td>
                    <td className="py-2.5">
                      <span className="flex items-center gap-1.5">
                        <span className="w-2 h-2 rounded-full" style={{ background: TYPE_COLORS[ev.type] }} />
                        <span style={{ color: TYPE_COLORS[ev.type] }}>{ev.type}</span>
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
          {/* Mobile cards */}
          <div className="sm:hidden space-y-2">
            {NEARBY_EVENTS.map(ev => (
              <div key={ev.id} className="p-3 rounded-md cursor-pointer" style={{ background: ev.id === selected.id ? `${CV_COLORS.cyan}08` : CV_COLORS.track, border: `1px solid ${ev.id === selected.id ? CV_COLORS.cyan + '33' : CV_COLORS.border}` }}
                onClick={() => selectEvent(ev)}>
                <div className="flex items-center justify-between mb-1">
                  <span className="font-mono text-xs" style={{ color: CV_COLORS.fg }}>{ev.time}</span>
                  <span className="flex items-center gap-1 text-[10px]"><span className="w-1.5 h-1.5 rounded-full" style={{ background: TYPE_COLORS[ev.type] }} /><span style={{ color: TYPE_COLORS[ev.type] }}>{ev.type}</span></span>
                </div>
                <div className="flex items-center justify-between text-[10px]" style={{ color: CV_COLORS.muted }}>
                  <span>{ev.camera}</span><span>{ev.confidence}%</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Review actions */}
        <div className="lg:w-[42%] rounded-lg p-4" style={{ background: CV_COLORS.panel, border: `1px solid ${CV_COLORS.border}` }}>
          <p className="text-xs font-semibold mb-3" style={{ color: CV_COLORS.fg }}>Review actions</p>
          <div className="flex items-center gap-2 mb-2 text-xs">
            <CheckCircle2 className="w-3.5 h-3.5" style={{ color: CV_COLORS.green }} />
            <span style={{ color: CV_COLORS.green }}>Evidence integrity verified</span>
          </div>
          <p className="text-[10px] mb-4" style={{ color: CV_COLORS.muted }}>Four of four evidence frames available.</p>

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
          <p className="text-xs mb-4" style={{ color: CV_COLORS.fg2 }}>Mark <b style={{ color: CV_COLORS.fg }}>{selected.id}</b> as confirmed. This action will finalize the detection record.</p>
          <div className="flex gap-2 justify-end">
            <button onClick={() => setShowConfirm(false)} className="px-3 py-1.5 rounded text-xs border" style={{ borderColor: CV_COLORS.border, color: CV_COLORS.fg2 }}>Cancel</button>
            <button onClick={() => setShowConfirm(false)} className="px-3 py-1.5 rounded text-xs font-medium" style={{ background: CV_COLORS.green, color: CV_COLORS.bg }}>Confirm</button>
          </div>
        </Modal>
      )}

      {/* Dismiss modal */}
      {showDismiss && (
        <Modal title="Dismiss event" onClose={() => setShowDismiss(false)}>
          <p className="text-xs mb-3" style={{ color: CV_COLORS.fg2 }}>Provide a reason for dismissing <b style={{ color: CV_COLORS.fg }}>{selected.id}</b>.</p>
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
              <label className="block mb-1" style={{ color: CV_COLORS.muted }}>Priority</label>
              <FilterSelect value="High" options={['Critical', 'High', 'Medium', 'Low']} onChange={() => {}} />
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

// --- Helpers ---

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center" onClick={onClose}>
      <div className="absolute inset-0 bg-black/60" />
      <div className="relative w-full max-w-md mx-4 rounded-lg p-5" style={{ background: CV_COLORS.panel, border: `1px solid ${CV_COLORS.border}` }} onClick={e => e.stopPropagation()}>
        <p className="text-sm font-semibold mb-3" style={{ color: CV_COLORS.fg }}>{title}</p>
        {children}
      </div>
    </div>
  );
}

function FilterSelect({ value, options, onChange }: { value: string; options: string[]; onChange: (v: string) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button onClick={() => setOpen(!open)} className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium"
        style={{ background: CV_COLORS.track, border: `1px solid ${CV_COLORS.border}`, color: CV_COLORS.fg2 }}>
        {value} <ChevronDown className="w-3 h-3" />
      </button>
      {open && (
        <div className="absolute top-full left-0 mt-1 z-[9999] rounded-md py-1 min-w-[160px] shadow-lg" style={{ background: CV_COLORS.panel, border: `1px solid ${CV_COLORS.border}` }}>
          {options.map(o => (
            <button key={o} onClick={() => { onChange(o); setOpen(false); }} className="w-full text-left px-3 py-1.5 text-xs hover:bg-white/5"
              style={{ color: o === value ? CV_COLORS.cyan : CV_COLORS.fg2 }}>{o}</button>
          ))}
        </div>
      )}
    </div>
  );
}
