import { useState } from 'react';
import { 
  Video, 
  Server, 
  CheckCircle2, 
  XCircle, 
  Loader2,
  RefreshCw,
  Activity,
  Brain,
} from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { Slider } from '@/components/ui/slider';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import { useMediaMTXConfig } from '@/hooks/useMediaMTXConfig';
import { useCameraHealth } from '@/hooks/useCameraHealth';
import { useToast } from '@/hooks/use-toast';

export function MediaMTXSettings() {
  const { 
    serverUrl, 
    autoReconnect, 
    healthCheckInterval,
    frameAnalysisInterval,
    updateConfig, 
    resetConfig,
    validateServer,
    isValidating,
    isServerReachable,
  } = useMediaMTXConfig();

  const { 
    streamCount, 
    healthyCount, 
    lastCheckTime, 
    forceCheck, 
    isChecking 
  } = useCameraHealth(healthCheckInterval * 1000);

  const { toast } = useToast();
  const [tempUrl, setTempUrl] = useState(serverUrl);

  const handleSaveUrl = async () => {
    const isValid = await validateServer(tempUrl);
    if (isValid) {
      updateConfig({ serverUrl: tempUrl });
      toast({
        title: 'Server URL Updated',
        description: 'MediaMTX server configuration saved successfully',
      });
    } else {
      toast({
        title: 'Connection Failed',
        description: 'Could not connect to the MediaMTX server. Please check the URL.',
        variant: 'destructive',
      });
    }
  };

  const handleTestConnection = async () => {
    const isValid = await validateServer(tempUrl);
    if (isValid) {
      toast({
        title: 'Connection Successful',
        description: `Connected to MediaMTX server at ${tempUrl}`,
      });
    } else {
      toast({
        title: 'Connection Failed',
        description: 'Could not reach the MediaMTX server',
        variant: 'destructive',
      });
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <Video className="w-5 h-5 text-primary" />
          <CardTitle className="text-base">Camera Streaming (MediaMTX)</CardTitle>
        </div>
        <CardDescription>Configure RTSP-to-WebRTC streaming server</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* Server URL Configuration */}
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Server className="w-4 h-4 text-muted-foreground" />
              <Label>MediaMTX Server URL</Label>
            </div>
            {isServerReachable !== null && (
              <Badge variant={isServerReachable ? 'default' : 'destructive'} className="gap-1">
                {isServerReachable ? (
                  <>
                    <CheckCircle2 className="w-3 h-3" />
                    Connected
                  </>
                ) : (
                  <>
                    <XCircle className="w-3 h-3" />
                    Unreachable
                  </>
                )}
              </Badge>
            )}
          </div>
          
          <div className="flex gap-2">
            <Input
              value={tempUrl}
              onChange={(e) => setTempUrl(e.target.value)}
              placeholder="http://localhost:8889"
              className="flex-1"
            />
            <Button 
              variant="outline" 
              size="sm"
              onClick={handleTestConnection}
              disabled={isValidating}
            >
              {isValidating ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                'Test'
              )}
            </Button>
            <Button 
              size="sm"
              onClick={handleSaveUrl}
              disabled={isValidating || tempUrl === serverUrl}
            >
              Save
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            WebRTC endpoint for camera streams. API port (9997) is derived automatically.
          </p>
        </div>

        <Separator />

        {/* Stream Health Status */}
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Activity className="w-4 h-4 text-muted-foreground" />
              <Label>Stream Health</Label>
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={forceCheck}
              disabled={isChecking}
              className="h-7 px-2"
            >
              {isChecking ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : (
                <RefreshCw className="w-3 h-3" />
              )}
            </Button>
          </div>
          
          <div className="grid grid-cols-3 gap-3">
            <div className="p-3 rounded-lg bg-muted/50 text-center">
              <div className="text-2xl font-bold text-foreground">{streamCount}</div>
              <div className="text-xs text-muted-foreground">Total Streams</div>
            </div>
            <div className="p-3 rounded-lg bg-success/10 text-center">
              <div className="text-2xl font-bold text-success">{healthyCount}</div>
              <div className="text-xs text-muted-foreground">Healthy</div>
            </div>
            <div className="p-3 rounded-lg bg-destructive/10 text-center">
              <div className="text-2xl font-bold text-destructive">{streamCount - healthyCount}</div>
              <div className="text-xs text-muted-foreground">Unhealthy</div>
            </div>
          </div>
          
          {lastCheckTime && (
            <p className="text-xs text-muted-foreground">
              Last checked: {lastCheckTime.toLocaleTimeString()}
            </p>
          )}
        </div>

        <Separator />

        {/* Connection Settings */}
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <Label>Auto-Reconnect</Label>
              <p className="text-xs text-muted-foreground">Automatically reconnect lost streams</p>
            </div>
            <Switch 
              checked={autoReconnect}
              onCheckedChange={(checked) => updateConfig({ autoReconnect: checked })}
            />
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label>Health Check Interval</Label>
              <span className="text-sm font-mono">{healthCheckInterval}s</span>
            </div>
            <Slider
              value={[healthCheckInterval]}
              onValueChange={([value]) => updateConfig({ healthCheckInterval: value })}
              min={10}
              max={120}
              step={5}
              className="w-full"
            />
            <p className="text-xs text-muted-foreground">How often to check stream health</p>
          </div>
        </div>

        <Separator />

        {/* AI Analysis Settings */}
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <Brain className="w-4 h-4 text-muted-foreground" />
            <Label className="text-sm font-medium">AI Frame Analysis</Label>
          </div>
          
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label>Analysis Interval</Label>
              <span className="text-sm font-mono">{frameAnalysisInterval}s</span>
            </div>
            <Slider
              value={[frameAnalysisInterval]}
              onValueChange={([value]) => updateConfig({ frameAnalysisInterval: value })}
              min={5}
              max={60}
              step={5}
              className="w-full"
            />
            <p className="text-xs text-muted-foreground">
              How often to capture and analyze frames for AI detection
            </p>
          </div>
        </div>

        <Separator />

        {/* Reset */}
        <div className="flex justify-end">
          <Button variant="outline" size="sm" onClick={resetConfig}>
            Reset to Defaults
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
