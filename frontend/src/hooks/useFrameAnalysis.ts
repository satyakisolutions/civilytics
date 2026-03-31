import { useState, useCallback } from 'react';
import { supabase } from '@/integrations/supabase/client';
import { Camera } from '@/types/monitoring';
import { useToast } from '@/hooks/use-toast';

interface AnalysisResult {
  isDumpingDetected: boolean;
  confidence: number;
  eventType: 'dumping_detected' | 'suspicious_activity' | 'repeat_offender' | null;
  severity: 'critical' | 'warning' | 'info';
  personDescription: string | null;
  description: string;
}

export function useFrameAnalysis() {
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [lastResult, setLastResult] = useState<AnalysisResult | null>(null);
  const { toast } = useToast();

  const analyzeFrame = useCallback(async (
    camera: Camera,
    imageBase64: string
  ): Promise<AnalysisResult | null> => {
    setIsAnalyzing(true);

    try {
      const { data, error } = await supabase.functions.invoke('analyze-frame', {
        body: {
          cameraId: camera.id,
          cameraName: camera.name,
          zone: camera.zone,
          location: camera.location,
          imageBase64
        }
      });

      if (error) throw error;

      const result = data.detection as AnalysisResult;
      setLastResult(result);

      if (result.isDumpingDetected) {
        toast({
          title: result.severity === 'critical' 
            ? '🚨 Illegal Dumping Detected!' 
            : '⚠️ Suspicious Activity',
          description: `${camera.name}: ${result.description}`,
          variant: result.severity === 'critical' ? 'destructive' : 'default'
        });
      }

      return result;
    } catch (error) {
      console.error('Frame analysis failed:', error);
      
      // Handle rate limits
      if (error instanceof Error && error.message.includes('429')) {
        toast({
          title: 'Rate Limited',
          description: 'Too many analysis requests. Please wait a moment.',
          variant: 'destructive'
        });
      } else if (error instanceof Error && error.message.includes('402')) {
        toast({
          title: 'Credits Exhausted',
          description: 'AI credits have been exhausted. Please add funds.',
          variant: 'destructive'
        });
      } else {
        toast({
          title: 'Analysis Failed',
          description: 'Failed to analyze camera frame',
          variant: 'destructive'
        });
      }

      return null;
    } finally {
      setIsAnalyzing(false);
    }
  }, [toast]);

  // Simulate frame capture from video element
  const captureFrame = useCallback((videoElement: HTMLVideoElement): string | null => {
    try {
      const canvas = document.createElement('canvas');
      canvas.width = videoElement.videoWidth || 640;
      canvas.height = videoElement.videoHeight || 480;
      
      const ctx = canvas.getContext('2d');
      if (!ctx) return null;

      ctx.drawImage(videoElement, 0, 0, canvas.width, canvas.height);
      return canvas.toDataURL('image/jpeg', 0.8);
    } catch (error) {
      console.error('Failed to capture frame:', error);
      return null;
    }
  }, []);

  return {
    isAnalyzing,
    lastResult,
    analyzeFrame,
    captureFrame
  };
}
