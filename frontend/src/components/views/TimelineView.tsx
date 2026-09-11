import { useState, useMemo, useEffect, useRef } from 'react';
import { useViolationEvents } from '@/hooks/useViolationEvents';
import { ViolationEvent } from '@/types/monitoring';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Input } from '@/components/ui/input';
import {
  AlertTriangle,
  Clock,
  CheckCircle2,
  Play,
  SkipBack,
  SkipForward,
  Camera,
  MapPin,
  Image,
  Video,
  Search,
  Calendar,
  X,
  Loader2,
  Rewind,
  FastForward,
} from 'lucide-react';
import { format, isToday, isYesterday, startOfDay } from 'date-fns';

export function TimelineView() {
  const { events, isLoading } = useViolationEvents();
  const [selectedEvent, setSelectedEvent] = useState<ViolationEvent | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [playbackRate, setPlaybackRate] = useState(1);
  const videoRef = useRef<HTMLVideoElement | null>(null);

  // Apply playback rate whenever rate changes OR a new video mounts.
  useEffect(() => {
    const el = videoRef.current;
    if (el) el.playbackRate = playbackRate;
  }, [playbackRate, selectedEvent]);

  const filteredEvents = useMemo(() => {
    if (!searchQuery) return events;
    const q = searchQuery.toLowerCase();
    return events.filter(
      e =>
        e.cameraName.toLowerCase().includes(q) ||
        e.zone.toLowerCase().includes(q) ||
        e.eventType.toLowerCase().includes(q)
    );
  }, [events, searchQuery]);

  // Group events by day
  const groupedEvents = useMemo(() => {
    const groups = new Map<string, ViolationEvent[]>();
    for (const event of filteredEvents) {
      const dayKey = startOfDay(event.timestamp).toISOString();
      if (!groups.has(dayKey)) groups.set(dayKey, []);
      groups.get(dayKey)!.push(event);
    }
    return Array.from(groups.entries()).sort(
      ([a], [b]) => new Date(b).getTime() - new Date(a).getTime()
    );
  }, [filteredEvents]);

  const formatDayLabel = (iso: string) => {
    const d = new Date(iso);
    if (isToday(d)) return 'Today';
    if (isYesterday(d)) return 'Yesterday';
    return format(d, 'EEEE, MMMM d, yyyy');
  };

  const severityIcon = (severity: string) => {
    switch (severity) {
      case 'critical':
        return <AlertTriangle className="w-4 h-4 text-destructive" />;
      case 'warning':
        return <AlertTriangle className="w-4 h-4 text-warning" />;
      default:
        return <Clock className="w-4 h-4 text-info" />;
    }
  };

  const eventTypeLabel = (type: string) => {
    switch (type) {
      case 'dumping_detected':
        return 'Illegal Dumping';
      case 'suspicious_activity':
        return 'Suspicious Activity';
      case 'repeat_offender':
        return 'Repeat Offender';
      default:
        return type;
    }
  };

  // Navigate between events
  const currentIndex = selectedEvent
    ? filteredEvents.findIndex(e => e.id === selectedEvent.id)
    : -1;

  const goToEvent = (dir: 'prev' | 'next') => {
    const idx = dir === 'prev' ? currentIndex - 1 : currentIndex + 1;
    if (idx >= 0 && idx < filteredEvents.length) {
      setSelectedEvent(filteredEvents[idx]);
    }
  };

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="p-4 border-b border-border">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h1 className="text-lg font-semibold">Timeline Playback</h1>
            <p className="text-sm text-muted-foreground">
              Review past violation events chronologically
            </p>
          </div>
          <Badge variant="secondary" className="font-mono text-xs">
            {filteredEvents.length} events
          </Badge>
        </div>
        <div className="relative max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input
            placeholder="Search events..."
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            className="pl-9 h-9"
          />
        </div>
      </div>

      <div className="flex-1 flex overflow-hidden">
        {/* Timeline sidebar */}
        <ScrollArea className="w-80 border-r border-border">
          <div className="p-3 space-y-4">
            {groupedEvents.map(([dayKey, dayEvents]) => (
              <div key={dayKey}>
                <div className="flex items-center gap-2 mb-2 px-2">
                  <Calendar className="w-3.5 h-3.5 text-muted-foreground" />
                  <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                    {formatDayLabel(dayKey)}
                  </span>
                  <Badge variant="secondary" className="h-4 px-1.5 text-[10px] ml-auto">
                    {dayEvents.length}
                  </Badge>
                </div>
                <div className="space-y-1">
                  {dayEvents.map(event => (
                    <button
                      key={event.id}
                      onClick={() => setSelectedEvent(event)}
                      className={cn(
                        'w-full text-left px-3 py-2.5 rounded-lg transition-colors',
                        'hover:bg-accent/50 group',
                        selectedEvent?.id === event.id && 'bg-primary/10 border border-primary/30'
                      )}
                    >
                      <div className="flex items-start gap-2.5">
                        <div className="mt-0.5 relative">
                          {severityIcon(event.severity)}
                          {/* Timeline dot connector */}
                          <div className="absolute top-5 left-1/2 -translate-x-1/2 w-px h-4 bg-border" />
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-1.5 mb-0.5">
                            <span className="text-xs font-medium truncate">
                              {eventTypeLabel(event.eventType)}
                            </span>
                            {event.repeatCount && event.repeatCount > 1 && (
                              <Badge variant="destructive" className="h-4 px-1 text-[9px]">
                                ×{event.repeatCount}
                              </Badge>
                            )}
                          </div>
                          <div className="flex items-center gap-1 text-[11px] text-muted-foreground">
                            <Camera className="w-3 h-3" />
                            <span className="truncate">{event.cameraName}</span>
                          </div>
                          <div className="text-[10px] text-muted-foreground font-mono mt-0.5">
                            {format(event.timestamp, 'HH:mm:ss')}
                          </div>
                        </div>
                        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100">
                          {event.snapshotUrl && <Image className="w-3 h-3 text-muted-foreground" />}
                          {event.videoClipUrl && <Video className="w-3 h-3 text-muted-foreground" />}
                        </div>
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            ))}

            {groupedEvents.length === 0 && (
              <div className="text-center py-8 text-muted-foreground text-sm">
                No events found
              </div>
            )}
          </div>
        </ScrollArea>

        {/* Detail / Playback panel */}
        <div className="flex-1 flex flex-col min-w-0">
          {selectedEvent ? (
            <>
              {/* Media viewer — main video. Keyframe snapshot shown as
                  corner thumbnail. `min-h-0`/`min-w-0` lets flex shrink,
                  `object-contain` letterboxes tall/wide recordings. */}
              <div className="flex-1 min-h-0 min-w-0 bg-black/90 flex items-center justify-center relative overflow-hidden">
                {selectedEvent.videoClipUrl ? (
                  <video
                    ref={videoRef}
                    key={selectedEvent.id}
                    src={selectedEvent.videoClipUrl}
                    poster={selectedEvent.snapshotUrl}
                    controls
                    autoPlay
                    loop
                    className="max-w-full max-h-full w-auto h-auto object-contain"
                  />
                ) : selectedEvent.snapshotUrl ? (
                  <img
                    src={selectedEvent.snapshotUrl}
                    alt="Event snapshot"
                    className="max-w-full max-h-full w-auto h-auto object-contain"
                  />
                ) : (
                  <div className="text-muted-foreground text-sm flex flex-col items-center gap-2">
                    <Camera className="w-12 h-12 opacity-30" />
                    <span>No media available for this event</span>
                  </div>
                )}

                {/* Keyframe thumbnail (bottom-right) */}
                {selectedEvent.videoClipUrl && selectedEvent.snapshotUrl && (
                  <a
                    href={selectedEvent.snapshotUrl}
                    target="_blank"
                    rel="noreferrer"
                    title="Open keyframe"
                    className="absolute bottom-3 right-3 border border-border rounded overflow-hidden shadow-lg hover:ring-2 hover:ring-primary transition"
                  >
                    <img
                      src={selectedEvent.snapshotUrl}
                      alt="Keyframe"
                      className="w-32 h-20 object-cover"
                    />
                  </a>
                )}

                {/* Severity + event id overlay */}
                <div className="absolute top-4 left-4 flex items-center gap-2">
                  <Badge
                    variant={selectedEvent.severity === 'critical' ? 'destructive' : 'secondary'}
                    className="text-xs"
                  >
                    {selectedEvent.severity.toUpperCase()}
                  </Badge>
                  <Badge variant="secondary" className="font-mono text-[10px]">
                    {selectedEvent.id.slice(0, 12)}
                  </Badge>
                </div>

                {/* Confidence overlay */}
                <div className="absolute top-4 right-4">
                  <Badge variant="secondary" className="font-mono text-xs">
                    {selectedEvent.confidence}% confidence
                  </Badge>
                </div>
              </div>

              {/* Playback controls — prev/next event + speed */}
              <div className="border-t border-border p-3">
                <div className="flex items-center justify-between mb-3 gap-3 flex-wrap">
                  <div className="flex items-center gap-2">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-8 w-8"
                      disabled={currentIndex <= 0}
                      onClick={() => goToEvent('prev')}
                      title="Previous event"
                    >
                      <SkipBack className="w-4 h-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-8 w-8"
                      disabled={currentIndex >= filteredEvents.length - 1}
                      onClick={() => goToEvent('next')}
                      title="Next event"
                    >
                      <SkipForward className="w-4 h-4" />
                    </Button>
                  </div>

                  <div className="flex items-center gap-1">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-8 w-8"
                      onClick={() => setPlaybackRate(r => Math.max(0.25, +(r / 2).toFixed(2)))}
                      title="Slow down"
                    >
                      <Rewind className="w-4 h-4" />
                    </Button>
                    {[0.5, 1, 2, 4].map(r => (
                      <Button
                        key={r}
                        variant={playbackRate === r ? 'default' : 'ghost'}
                        size="sm"
                        className="h-8 px-2 text-[11px] font-mono"
                        onClick={() => setPlaybackRate(r)}
                      >
                        {r}×
                      </Button>
                    ))}
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-8 w-8"
                      onClick={() => setPlaybackRate(r => Math.min(8, +(r * 2).toFixed(2)))}
                      title="Speed up"
                    >
                      <FastForward className="w-4 h-4" />
                    </Button>
                    <Badge variant="secondary" className="ml-1 font-mono text-[10px]">
                      {playbackRate}×
                    </Badge>
                  </div>

                  <div className="text-xs text-muted-foreground font-mono">
                    {currentIndex + 1} / {filteredEvents.length}
                  </div>
                </div>

                {/* Event details bar */}
                <div className="bg-muted/50 rounded-lg p-3 grid grid-cols-4 gap-4 text-xs">
                  <div>
                    <span className="text-muted-foreground block mb-1">Event Type</span>
                    <span className="font-medium">{eventTypeLabel(selectedEvent.eventType)}</span>
                  </div>
                  <div>
                    <span className="text-muted-foreground block mb-1">Camera</span>
                    <div className="flex items-center gap-1">
                      <Camera className="w-3 h-3" />
                      <span className="font-medium">{selectedEvent.cameraName}</span>
                    </div>
                  </div>
                  <div>
                    <span className="text-muted-foreground block mb-1">Location</span>
                    <div className="flex items-center gap-1">
                      <MapPin className="w-3 h-3" />
                      <span className="font-medium">{selectedEvent.zone}</span>
                    </div>
                  </div>
                  <div>
                    <span className="text-muted-foreground block mb-1">Status</span>
                    <Badge
                      variant="secondary"
                      className={cn(
                        'text-[10px]',
                        selectedEvent.status === 'active' && 'bg-destructive/20 text-destructive',
                        selectedEvent.status === 'resolved' && 'bg-success/20 text-success'
                      )}
                    >
                      {selectedEvent.status === 'active' && <AlertTriangle className="w-3 h-3 mr-1" />}
                      {selectedEvent.status === 'resolved' && <CheckCircle2 className="w-3 h-3 mr-1" />}
                      {selectedEvent.status.toUpperCase()}
                    </Badge>
                  </div>
                </div>

                {selectedEvent.notes && (
                  <div className="mt-2 p-2 bg-muted/30 rounded text-xs text-muted-foreground">
                    <span className="font-medium text-foreground">Notes: </span>
                    {selectedEvent.notes}
                  </div>
                )}
              </div>
            </>
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center text-muted-foreground gap-3">
              <Play className="w-16 h-16 opacity-20" />
              <p className="text-sm">Select an event from the timeline to view details</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
