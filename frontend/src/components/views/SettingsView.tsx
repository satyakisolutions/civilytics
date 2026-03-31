import { 
  Bell,
  Volume2,
  Mail,
  MessageSquare,
  Phone,
  Shield,
  Cloud,
  Monitor,
  Users,
  CheckCircle2,
  Loader2,
} from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Switch } from '@/components/ui/switch';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Slider } from '@/components/ui/slider';
import { Separator } from '@/components/ui/separator';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Badge } from '@/components/ui/badge';
import { useState } from 'react';
import { useDropbox } from '@/hooks/useDropbox';
import { MediaMTXSettings } from '@/components/settings/MediaMTXSettings';

export function SettingsView() {
  const [alertVolume, setAlertVolume] = useState([75]);
  const { isConnected, isLoading: dropboxLoading, lastSync, connect, disconnect } = useDropbox();

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="p-4 border-b border-border">
        <h1 className="text-lg font-semibold">Settings</h1>
        <p className="text-sm text-muted-foreground">Configure your monitoring preferences</p>
      </div>

      {/* Settings Content */}
      <ScrollArea className="flex-1">
        <div className="p-4 max-w-3xl space-y-6">
          {/* Alerts & Notifications */}
          <Card>
            <CardHeader>
              <div className="flex items-center gap-2">
                <Bell className="w-5 h-5 text-primary" />
                <CardTitle className="text-base">Alerts & Notifications</CardTitle>
              </div>
              <CardDescription>Configure how you receive violation alerts</CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              {/* Sound Settings */}
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <Volume2 className="w-4 h-4 text-muted-foreground" />
                    <div>
                      <Label>Alert Sound</Label>
                      <p className="text-xs text-muted-foreground">Play audio when violations detected</p>
                    </div>
                  </div>
                  <Switch defaultChecked />
                </div>
                <div className="pl-7 space-y-2">
                  <Label className="text-xs text-muted-foreground">Volume</Label>
                  <Slider
                    value={alertVolume}
                    onValueChange={setAlertVolume}
                    max={100}
                    step={1}
                    className="w-48"
                  />
                </div>
              </div>

              <Separator />

              {/* Notification Channels */}
              <div className="space-y-4">
                <Label className="text-sm font-medium">Notification Channels</Label>
                
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <Mail className="w-4 h-4 text-muted-foreground" />
                    <div>
                      <Label>Email Notifications</Label>
                      <p className="text-xs text-muted-foreground">Receive alerts via email</p>
                    </div>
                  </div>
                  <Switch defaultChecked />
                </div>

                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <MessageSquare className="w-4 h-4 text-muted-foreground" />
                    <div>
                      <Label>SMS Notifications</Label>
                      <p className="text-xs text-muted-foreground">Critical alerts via SMS</p>
                    </div>
                  </div>
                  <Switch />
                </div>

                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <MessageSquare className="w-4 h-4 text-muted-foreground" />
                    <div>
                      <Label>WhatsApp Notifications</Label>
                      <p className="text-xs text-muted-foreground">Alerts via WhatsApp</p>
                    </div>
                  </div>
                  <Switch />
                </div>

                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <Phone className="w-4 h-4 text-muted-foreground" />
                    <div>
                      <Label>VoIP Calls</Label>
                      <p className="text-xs text-muted-foreground">Voice calls for critical violations</p>
                    </div>
                  </div>
                  <Switch />
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Detection Settings */}
          <Card>
            <CardHeader>
              <div className="flex items-center gap-2">
                <Monitor className="w-5 h-5 text-primary" />
                <CardTitle className="text-base">Detection Settings</CardTitle>
              </div>
              <CardDescription>Configure AI detection parameters</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <Label>Confidence Threshold</Label>
                  <p className="text-xs text-muted-foreground">Minimum confidence to trigger alert</p>
                </div>
                <div className="flex items-center gap-2">
                  <Slider defaultValue={[70]} max={100} className="w-32" />
                  <span className="text-sm font-mono w-12">70%</span>
                </div>
              </div>

              <div className="flex items-center justify-between">
                <div>
                  <Label>Repeat Offender Tracking</Label>
                  <p className="text-xs text-muted-foreground">Track and flag repeat offenders</p>
                </div>
                <Switch defaultChecked />
              </div>

              <div className="flex items-center justify-between">
                <div>
                  <Label>Auto-capture Evidence</Label>
                  <p className="text-xs text-muted-foreground">Save snapshots and clips automatically</p>
                </div>
                <Switch defaultChecked />
              </div>
            </CardContent>
          </Card>

          {/* MediaMTX Streaming Settings */}
          <MediaMTXSettings />

          {/* Storage & Backup */}
          <Card>
            <CardHeader>
              <div className="flex items-center gap-2">
                <Cloud className="w-5 h-5 text-primary" />
                <CardTitle className="text-base">Storage & Backup</CardTitle>
              </div>
              <CardDescription>Manage evidence storage and cloud sync</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="flex flex-col">
                    <Label>Dropbox Integration</Label>
                    <p className="text-xs text-muted-foreground">Sync evidence to Dropbox</p>
                    {isConnected && lastSync && (
                      <p className="text-[10px] text-muted-foreground mt-1">
                        Last sync: {lastSync.toLocaleString()}
                      </p>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {dropboxLoading ? (
                    <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
                  ) : isConnected ? (
                    <>
                      <Badge variant="secondary" className="gap-1">
                        <CheckCircle2 className="w-3 h-3 text-success" />
                        Connected
                      </Badge>
                      <Button variant="outline" size="sm" onClick={disconnect}>
                        Disconnect
                      </Button>
                    </>
                  ) : (
                    <Button variant="outline" size="sm" onClick={connect}>
                      Connect
                    </Button>
                  )}
                </div>
              </div>

              <div className="flex items-center justify-between">
                <div>
                  <Label>Local Storage Path</Label>
                  <p className="text-xs text-muted-foreground">Evidence files location</p>
                </div>
                <Input defaultValue="/var/wastewatch/evidence" className="w-64 h-8 text-xs" />
              </div>

              <div className="flex items-center justify-between">
                <div>
                  <Label>Retention Period</Label>
                  <p className="text-xs text-muted-foreground">How long to keep evidence</p>
                </div>
                <Input defaultValue="90" className="w-20 h-8 text-xs text-center" />
                <span className="text-sm text-muted-foreground">days</span>
              </div>
            </CardContent>
          </Card>

          {/* Access Control */}
          <Card>
            <CardHeader>
              <div className="flex items-center gap-2">
                <Shield className="w-5 h-5 text-primary" />
                <CardTitle className="text-base">Access Control</CardTitle>
              </div>
              <CardDescription>Manage user roles and permissions</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <Users className="w-4 h-4 text-muted-foreground" />
                  <div>
                    <Label>User Management</Label>
                    <p className="text-xs text-muted-foreground">Add or remove system users</p>
                  </div>
                </div>
                <Button variant="outline" size="sm">Manage Users</Button>
              </div>

              <div className="flex items-center justify-between">
                <div>
                  <Label>Two-Factor Authentication</Label>
                  <p className="text-xs text-muted-foreground">Require 2FA for all users</p>
                </div>
                <Switch defaultChecked />
              </div>

              <div className="flex items-center justify-between">
                <div>
                  <Label>Audit Logging</Label>
                  <p className="text-xs text-muted-foreground">Log all user actions</p>
                </div>
                <Switch defaultChecked />
              </div>
            </CardContent>
          </Card>
        </div>
      </ScrollArea>
    </div>
  );
}
