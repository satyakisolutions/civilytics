import { useState, useEffect, useCallback } from 'react';
import { supabase } from '@/integrations/supabase/client';
import { useToast } from '@/hooks/use-toast';

export function useDropbox() {
  const [isConnected, setIsConnected] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [lastSync, setLastSync] = useState<Date | null>(null);
  const { toast } = useToast();

  const checkStatus = useCallback(async () => {
    try {
      const { data, error } = await supabase.functions.invoke('dropbox-auth', {
        body: {},
        method: 'GET',
      });

      // Try with query param approach
      const response = await fetch(
        `${import.meta.env.VITE_SUPABASE_URL}/functions/v1/dropbox-auth?action=status`,
        {
          headers: {
            'Authorization': `Bearer ${import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY}`,
          },
        }
      );

      if (response.ok) {
        const status = await response.json();
        setIsConnected(status.connected || false);
        if (status.lastUpdated) {
          setLastSync(new Date(status.lastUpdated));
        }
      }
    } catch (error) {
      console.error('Failed to check Dropbox status:', error);
      setIsConnected(false);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    checkStatus();

    // Check URL for OAuth callback result
    const params = new URLSearchParams(window.location.search);
    if (params.get('dropbox_connected') === 'true') {
      toast({
        title: 'Dropbox Connected',
        description: 'Your Dropbox account has been successfully linked'
      });
      // Clean up URL
      window.history.replaceState({}, '', window.location.pathname);
      checkStatus();
    } else if (params.get('dropbox_error')) {
      toast({
        title: 'Dropbox Connection Failed',
        description: `Error: ${params.get('dropbox_error')}`,
        variant: 'destructive'
      });
      window.history.replaceState({}, '', window.location.pathname);
    }
  }, [checkStatus, toast]);

  const connect = async () => {
    try {
      const response = await fetch(
        `${import.meta.env.VITE_SUPABASE_URL}/functions/v1/dropbox-auth?action=init`,
        {
          headers: {
            'Authorization': `Bearer ${import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY}`,
          },
        }
      );

      const data = await response.json();

      if (data.setup_required) {
        toast({
          title: 'Setup Required',
          description: data.instructions,
          variant: 'destructive'
        });
        return;
      }

      if (data.authUrl) {
        // Redirect to Dropbox OAuth
        window.location.href = data.authUrl;
      }
    } catch (error) {
      console.error('Failed to start Dropbox auth:', error);
      toast({
        title: 'Error',
        description: 'Failed to start Dropbox authentication',
        variant: 'destructive'
      });
    }
  };

  const disconnect = async () => {
    try {
      const response = await fetch(
        `${import.meta.env.VITE_SUPABASE_URL}/functions/v1/dropbox-auth?action=disconnect`,
        {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY}`,
            'Content-Type': 'application/json'
          },
        }
      );

      if (response.ok) {
        setIsConnected(false);
        setLastSync(null);
        toast({
          title: 'Dropbox Disconnected',
          description: 'Your Dropbox account has been unlinked'
        });
      }
    } catch (error) {
      console.error('Failed to disconnect Dropbox:', error);
      toast({
        title: 'Error',
        description: 'Failed to disconnect Dropbox',
        variant: 'destructive'
      });
    }
  };

  const syncFile = async (eventId: string, fileType: 'snapshot' | 'video_clip', fileBase64: string, fileName: string) => {
    if (!isConnected) {
      toast({
        title: 'Dropbox Not Connected',
        description: 'Please connect your Dropbox account first',
        variant: 'destructive'
      });
      return null;
    }

    try {
      const response = await fetch(
        `${import.meta.env.VITE_SUPABASE_URL}/functions/v1/dropbox-sync`,
        {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY}`,
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({
            eventId,
            fileType,
            fileBase64,
            fileName
          })
        }
      );

      const result = await response.json();

      if (result.success) {
        toast({
          title: 'File Synced',
          description: `Uploaded to Dropbox: ${result.dropboxPath}`
        });
        setLastSync(new Date());
        return result.dropboxPath;
      } else {
        throw new Error(result.error || 'Sync failed');
      }
    } catch (error) {
      console.error('Failed to sync file:', error);
      toast({
        title: 'Sync Failed',
        description: error instanceof Error ? error.message : 'Failed to upload to Dropbox',
        variant: 'destructive'
      });
      return null;
    }
  };

  return {
    isConnected,
    isLoading,
    lastSync,
    connect,
    disconnect,
    syncFile,
    checkStatus
  };
}
