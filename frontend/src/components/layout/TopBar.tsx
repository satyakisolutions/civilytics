import { 
  Grid2X2, 
  Grid3X3, 
  Maximize2,
  LayoutGrid,
  ChevronDown,
} from 'lucide-react';
import { useMonitoring } from '@/context/MonitoringContext';
import { GridLayout } from '@/types/monitoring';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';

const layoutOptions: { value: GridLayout; label: string; icon: typeof Grid2X2 }[] = [
  { value: '1x1', label: 'Single View', icon: Maximize2 },
  { value: '2x2', label: '2×2 Grid', icon: Grid2X2 },
  { value: '3x3', label: '3×3 Grid', icon: Grid3X3 },
  { value: '4x4', label: '4×4 Grid', icon: LayoutGrid },
];

export function TopBar() {
  const { gridLayout, setGridLayout, selectedCamera, setSelectedCamera } = useMonitoring();
  const currentLayout = layoutOptions.find(l => l.value === gridLayout);

  return (
    <div className="hidden md:flex h-12 bg-card border-b border-border items-center justify-between px-4">
      {/* Left: Page title */}
      <div className="flex items-center gap-4">
        <h1 className="text-sm font-semibold text-foreground">Live Monitoring</h1>
        <span className="live-indicator">LIVE</span>
      </div>

      {/* Center: Layout controls — hidden on mobile */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-muted-foreground mr-2">Layout:</span>
        <div className="flex gap-1 bg-muted rounded-lg p-1">
          {layoutOptions.map((option) => {
            const Icon = option.icon;
            return (
              <Button
                key={option.value}
                variant="ghost"
                size="sm"
                onClick={() => {
                  setGridLayout(option.value);
                  if (option.value !== '1x1') {
                    setSelectedCamera(null);
                  }
                }}
                className={cn(
                  'h-7 w-7 p-0',
                  gridLayout === option.value 
                    ? 'bg-primary text-primary-foreground hover:bg-primary hover:text-primary-foreground' 
                    : 'hover:bg-accent'
                )}
                title={option.label}
              >
                <Icon className="w-4 h-4" />
              </Button>
            );
          })}
        </div>
      </div>

      {/* Right: Quick filters + Satyaki */}
      <div className="flex items-center gap-3">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm" className="h-8 text-xs gap-1.5">
              All Zones
              <ChevronDown className="w-3.5 h-3.5" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem>All Zones</DropdownMenuItem>
            <DropdownMenuItem>Zone A - Industrial</DropdownMenuItem>
            <DropdownMenuItem>Zone B - Commercial</DropdownMenuItem>
            <DropdownMenuItem>Zone C - Residential</DropdownMenuItem>
            <DropdownMenuItem>Zone D - Highway</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm" className="h-8 text-xs gap-1.5">
              All Status
              <ChevronDown className="w-3.5 h-3.5" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem>All Status</DropdownMenuItem>
            <DropdownMenuItem className="text-success">Online</DropdownMenuItem>
            <DropdownMenuItem className="text-warning">Warning</DropdownMenuItem>
            <DropdownMenuItem className="text-destructive">Alert</DropdownMenuItem>
            <DropdownMenuItem className="text-muted-foreground">Offline</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>

        <a
          href="https://satyaki.co.in"
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-1.5 pl-3 ml-1 border-l border-border opacity-80 hover:opacity-100 transition-opacity"
          title="Satyaki Solutions Pvt Ltd"
        >
          <img src="/satyaki-logo.svg" alt="Satyaki Solutions Pvt Ltd" className="h-6 w-auto" />
          <span className="text-xs font-medium hidden sm:inline whitespace-nowrap" style={{ fontFamily: "'Poppins', sans-serif", color: '#FCA719' }}>Satyaki Solutions Pvt Ltd</span>
        </a>
      </div>
    </div>
  );
}
