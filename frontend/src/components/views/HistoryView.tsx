import { useState } from 'react';
import { 
  Search, 
  Filter, 
  Calendar,
  Download,
  Eye,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Clock,
  MapPin,
  Camera,
  User,
  ChevronDown,
  MoreHorizontal,
} from 'lucide-react';
import { useViolationEvents } from '@/hooks/useViolationEvents';
import { ViolationEvent } from '@/types/monitoring';
import { API_BASE } from '@/lib/api-base';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { 
  Table, 
  TableBody, 
  TableCell, 
  TableHead, 
  TableHeader, 
  TableRow 
} from '@/components/ui/table';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { ScrollArea } from '@/components/ui/scroll-area';

export function HistoryView() {
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedFilter, setSelectedFilter] = useState<'all' | 'active' | 'resolved'>('all');
  const statusFilter = selectedFilter === 'all' ? undefined : selectedFilter;
  const { events: allEvents, isLoading } = useViolationEvents({ status: statusFilter });

  const filteredEvents = allEvents.filter(event => {
    const matchesSearch = !searchQuery ||
      (event.cameraName || '').toLowerCase().includes(searchQuery.toLowerCase()) ||
      (event.zone || '').toLowerCase().includes(searchQuery.toLowerCase()) ||
      (event.location || '').toLowerCase().includes(searchQuery.toLowerCase());
    return matchesSearch;
  });

  const handleExportCsv = () => {
    const params = new URLSearchParams();
    if (statusFilter) params.set('status', statusFilter);
    params.set('limit', '1000');
    window.open(`${API_BASE}/api/events/export/csv?${params.toString()}`, '_blank');
  };

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="p-4 border-b border-border">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h1 className="text-lg font-semibold">Event History</h1>
            <p className="text-sm text-muted-foreground">View and manage all detected violations</p>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" className="gap-2">
              <Calendar className="w-4 h-4" />
              Date Range
            </Button>
            <Button variant="outline" size="sm" className="gap-2" onClick={handleExportCsv}>
              <Download className="w-4 h-4" />
              Export CSV
            </Button>
          </div>
        </div>

        {/* Filters */}
        <div className="flex items-center gap-3">
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
            <Input
              placeholder="Search by camera, zone, location..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-9 h-9"
            />
          </div>

          <div className="flex items-center gap-1 bg-muted rounded-lg p-1">
            {(['all', 'active', 'resolved'] as const).map((filter) => (
              <Button
                key={filter}
                variant="ghost"
                size="sm"
                onClick={() => setSelectedFilter(filter)}
                className={cn(
                  'h-7 px-3 text-xs capitalize',
                  selectedFilter === filter && 'bg-primary text-primary-foreground hover:bg-primary'
                )}
              >
                {filter}
              </Button>
            ))}
          </div>

          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="sm" className="gap-2">
                <Filter className="w-4 h-4" />
                More Filters
                <ChevronDown className="w-3.5 h-3.5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-48">
              <DropdownMenuItem>By Severity</DropdownMenuItem>
              <DropdownMenuItem>By Zone</DropdownMenuItem>
              <DropdownMenuItem>By Camera</DropdownMenuItem>
              <DropdownMenuItem>Repeat Offenders Only</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      {/* Table */}
      <ScrollArea className="flex-1">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="w-[100px]">Time</TableHead>
              <TableHead>Event</TableHead>
              <TableHead>Camera / Location</TableHead>
              <TableHead className="w-[100px]">Confidence</TableHead>
              <TableHead className="w-[100px]">Status</TableHead>
              <TableHead className="w-[80px]">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filteredEvents.map((event) => (
              <EventRow key={event.id} event={event} />
            ))}
          </TableBody>
        </Table>
      </ScrollArea>

      {/* Footer */}
      <div className="p-4 border-t border-border flex items-center justify-between">
        <span className="text-sm text-muted-foreground">
          Showing {filteredEvents.length} of {allEvents.length} events
        </span>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" disabled>Previous</Button>
          <Button variant="outline" size="sm">Next</Button>
        </div>
      </div>
    </div>
  );
}

function EventRow({ event }: { event: ViolationEvent }) {
  const severityConfig = {
    critical: { icon: AlertTriangle, color: 'text-destructive', badge: 'badge-critical' },
    warning: { icon: AlertTriangle, color: 'text-warning', badge: 'badge-warning' },
    info: { icon: Clock, color: 'text-info', badge: 'badge-info' },
  };

  const config = severityConfig[event.severity];
  const Icon = config.icon;

  const formatTime = (date: Date) => {
    return new Intl.DateTimeFormat('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    }).format(date);
  };

  return (
    <TableRow className="group">
      <TableCell className="font-mono text-xs text-muted-foreground">
        {formatTime(event.timestamp)}
      </TableCell>
      <TableCell>
        <div className="flex items-center gap-2">
          <Icon className={cn('w-4 h-4', config.color)} />
          <div>
            <div className="flex items-center gap-2">
              <span className="font-medium text-sm">
                {event.eventType === 'dumping_detected' && 'Illegal Dumping'}
                {event.eventType === 'suspicious_activity' && 'Suspicious Activity'}
                {event.eventType === 'repeat_offender' && 'Repeat Offender'}
              </span>
              {event.repeatCount && event.repeatCount > 1 && (
                <Badge variant="destructive" className="h-4 px-1.5 text-[10px]">
                  ×{event.repeatCount}
                </Badge>
              )}
            </div>
            <span className="text-xs text-muted-foreground">{event.id}</span>
          </div>
        </div>
      </TableCell>
      <TableCell>
        <div className="flex items-start gap-2">
          <Camera className="w-3.5 h-3.5 text-muted-foreground mt-0.5" />
          <div>
            <div className="text-sm">{event.cameraName}</div>
            <div className="text-xs text-muted-foreground flex items-center gap-1">
              <MapPin className="w-3 h-3" />
              {event.zone} • {event.location}
            </div>
          </div>
        </div>
      </TableCell>
      <TableCell>
        <div className="flex items-center gap-2">
          <div className={cn(
            'h-1.5 w-12 rounded-full bg-muted overflow-hidden'
          )}>
            <div 
              className={cn(
                'h-full rounded-full',
                event.confidence >= 90 ? 'bg-success' :
                event.confidence >= 70 ? 'bg-warning' : 'bg-destructive'
              )}
              style={{ width: `${event.confidence}%` }}
            />
          </div>
          <span className="font-mono text-xs">{event.confidence}%</span>
        </div>
      </TableCell>
      <TableCell>
        <Badge 
          variant="secondary"
          className={cn(
            'text-[10px]',
            event.status === 'active' && 'badge-critical',
            event.status === 'acknowledged' && 'badge-warning',
            event.status === 'resolved' && 'badge-success'
          )}
        >
          {event.status === 'active' && <AlertTriangle className="w-3 h-3 mr-1" />}
          {event.status === 'acknowledged' && <Clock className="w-3 h-3 mr-1" />}
          {event.status === 'resolved' && <CheckCircle2 className="w-3 h-3 mr-1" />}
          {event.status.toUpperCase()}
        </Badge>
      </TableCell>
      <TableCell>
        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
          <Button variant="ghost" size="icon" className="h-7 w-7">
            <Eye className="w-4 h-4" />
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" className="h-7 w-7">
                <MoreHorizontal className="w-4 h-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem>View Details</DropdownMenuItem>
              <DropdownMenuItem>View Evidence</DropdownMenuItem>
              <DropdownMenuItem>Mark as Valid</DropdownMenuItem>
              <DropdownMenuItem>Mark as False Positive</DropdownMenuItem>
              <DropdownMenuItem>Export Report</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </TableCell>
    </TableRow>
  );
}
