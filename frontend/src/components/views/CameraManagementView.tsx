import {
  Camera as CameraIcon,
  Plus,
  Search,
  MoreVertical,
  MapPin,
  Wifi,
  WifiOff,
  Settings,
  Trash2,
  Edit,
  Eye,
} from 'lucide-react';
import { Camera, CameraStatus } from '@/types/monitoring';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { ScrollArea } from '@/components/ui/scroll-area';
import { useState } from 'react';
import { useCameras } from '@/hooks/useCameras';
import { AddCameraDialog } from '@/components/camera/AddCameraDialog';

export function CameraManagementView() {
  const [searchQuery, setSearchQuery] = useState('');
  const [addOpen, setAddOpen] = useState(false);
  const { cameras, isLoading, addCamera, deleteCamera } = useCameras();

  const filteredCameras = cameras.filter(camera =>
    camera.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
    camera.zone.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const statusCounts = {
    online: cameras.filter(c => c.status === 'online').length,
    offline: cameras.filter(c => c.status === 'offline').length,
    warning: cameras.filter(c => c.status === 'warning').length,
    alert: cameras.filter(c => c.status === 'alert').length,
  };

  return (
    <div className="flex flex-col h-full">
      <AddCameraDialog open={addOpen} onOpenChange={setAddOpen} onAdd={addCamera} />
      {/* Header */}
      <div className="p-4 border-b border-border">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h1 className="text-lg font-semibold">Camera Management</h1>
            <p className="text-sm text-muted-foreground">Manage and configure your monitoring cameras</p>
          </div>
          <Button size="sm" className="gap-2" onClick={() => setAddOpen(true)}>
            <Plus className="w-4 h-4" />
            Add Camera
          </Button>
        </div>

        {/* Status Overview */}
        <div className="grid grid-cols-4 gap-3 mb-4">
          <StatusCard label="Online" count={statusCounts.online} status="online" />
          <StatusCard label="Offline" count={statusCounts.offline} status="offline" />
          <StatusCard label="Warning" count={statusCounts.warning} status="warning" />
          <StatusCard label="Alert" count={statusCounts.alert} status="alert" />
        </div>

        {/* Search */}
        <div className="relative max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input
            placeholder="Search cameras..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-9 h-9"
          />
        </div>
      </div>

      {/* Camera Grid */}
      <ScrollArea className="flex-1">
        {isLoading ? (
          <div className="flex items-center justify-center h-40 text-sm text-muted-foreground">
            Loading cameras…
          </div>
        ) : filteredCameras.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-64 gap-3 text-muted-foreground">
            <CameraIcon className="w-12 h-12 opacity-30" />
            <div className="text-center">
              <p className="text-lg">
                {cameras.length === 0 ? 'No cameras configured' : 'No cameras match your search'}
              </p>
              {cameras.length === 0 && (
                <p className="text-sm mt-1">Click "Add Camera" to register your first camera</p>
              )}
            </div>
            {cameras.length === 0 && (
              <Button size="sm" className="gap-2 mt-2" onClick={() => setAddOpen(true)}>
                <Plus className="w-4 h-4" />
                Add Camera
              </Button>
            )}
          </div>
        ) : (
          <div className="p-4 grid grid-cols-3 gap-4">
            {filteredCameras.map((camera) => (
              <CameraCard key={camera.id} camera={camera} onDelete={deleteCamera} />
            ))}
          </div>
        )}
      </ScrollArea>
    </div>
  );
}

function StatusCard({ label, count, status }: { label: string; count: number; status: CameraStatus }) {
  const statusColors = {
    online: 'text-success border-success/30 bg-success/10',
    offline: 'text-muted-foreground border-muted bg-muted/50',
    warning: 'text-warning border-warning/30 bg-warning/10',
    alert: 'text-destructive border-destructive/30 bg-destructive/10',
  };

  return (
    <div className={cn(
      'flex items-center justify-between p-3 rounded-lg border',
      statusColors[status]
    )}>
      <span className="text-sm font-medium">{label}</span>
      <span className="text-2xl font-bold font-mono">{count}</span>
    </div>
  );
}

function CameraCard({ camera, onDelete }: { camera: Camera; onDelete: (id: string) => Promise<void> }) {
  const statusConfig: Record<CameraStatus, { color: string; icon: typeof Wifi; label: string }> = {
    online: { color: 'text-success', icon: Wifi, label: 'Online' },
    offline: { color: 'text-muted-foreground', icon: WifiOff, label: 'Offline' },
    warning: { color: 'text-warning', icon: Wifi, label: 'Warning' },
    alert: { color: 'text-destructive', icon: Wifi, label: 'Alert' },
  };

  const status = statusConfig[camera.status];
  const StatusIcon = status.icon;

  return (
    <Card className="group hover:border-primary/50 transition-colors">
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-2">
            <div className={cn(
              'w-8 h-8 rounded-lg flex items-center justify-center',
              camera.status === 'online' && 'bg-success/20',
              camera.status === 'offline' && 'bg-muted',
              camera.status === 'warning' && 'bg-warning/20',
              camera.status === 'alert' && 'bg-destructive/20'
            )}>
              <CameraIcon className={cn('w-4 h-4', status.color)} />
            </div>
            <div>
              <CardTitle className="text-sm">{camera.name}</CardTitle>
              <Badge variant="secondary" className="text-[10px] mt-1">
                {camera.id.toUpperCase()}
              </Badge>
            </div>
          </div>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button 
                variant="ghost" 
                size="icon" 
                className="h-7 w-7 opacity-0 group-hover:opacity-100 transition-opacity"
              >
                <MoreVertical className="w-4 h-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem>
                <Eye className="w-4 h-4 mr-2" />
                View Feed
              </DropdownMenuItem>
              <DropdownMenuItem>
                <Edit className="w-4 h-4 mr-2" />
                Edit Camera
              </DropdownMenuItem>
              <DropdownMenuItem>
                <Settings className="w-4 h-4 mr-2" />
                Settings
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                className="text-destructive"
                onClick={() => onDelete(camera.id)}
              >
                <Trash2 className="w-4 h-4 mr-2" />
                Remove
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </CardHeader>
      <CardContent>
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <MapPin className="w-3.5 h-3.5" />
            <span>{camera.zone} • {camera.location}</span>
          </div>
          <div className="flex items-center justify-between">
            <div className={cn('flex items-center gap-1.5 text-xs', status.color)}>
              <StatusIcon className="w-3.5 h-3.5" />
              <span>{status.label}</span>
            </div>
            {camera.isPinned && (
              <Badge variant="outline" className="text-[10px]">Pinned</Badge>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
