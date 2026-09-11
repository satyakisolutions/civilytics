import { useEffect, useRef, useCallback, useState } from 'react';
import { Camera } from '@/types/monitoring';
import { useFrameAnalysis } from './useFrameAnalysis';
import { useMediaMTXConfig } from './useMediaMTXConfig';

interface AnalysisSession {
  cameraId: string;
  isActive: boolean;
  lastAnalysis: Date | null;
  analysisCount: number;
  detectionCount: number;
}

export function useAutomatedAnalysis(cameras: Camera[]) {
  const { analyzeFrame, isAnalyzing } = useFrameAnalysis();
  const { frameAnalysisInterval } = useMediaMTXConfig();
  const [sessions, setSessions] = useState<Map<string, AnalysisSession>>(new Map());
  const [isEnabled, setIsEnabled] = useState(false);
  const videoRefs = useRef<Map<string, HTMLVideoElement>>(new Map());
  const intervalRef = useRef<NodeJS.Timeout | null>(null);

  // Register a video element for a camera
  const registerVideoElement = useCallback((cameraId: string, element: HTMLVideoElement | null) => {
    if (element) {
      videoRefs.current.set(cameraId, element);
    } else {
      videoRefs.current.delete(cameraId);
    }
  }, []);

  // Capture frame from video element
  const captureFrame = useCallback((videoElement: HTMLVideoElement): string | null => {
    try {
      if (videoElement.videoWidth === 0 || videoElement.videoHeight === 0) {
        return null;
      }

      const canvas = document.createElement('canvas');
      canvas.width = videoElement.videoWidth;
      canvas.height = videoElement.videoHeight;
      
      const ctx = canvas.getContext('2d');
      if (!ctx) return null;

      ctx.drawImage(videoElement, 0, 0, canvas.width, canvas.height);
      return canvas.toDataURL('image/jpeg', 0.8);
    } catch (error) {
      console.error('Failed to capture frame:', error);
      return null;
    }
  }, []);

  // Analyze a single camera
  const analyzeCamera = useCallback(async (camera: Camera) => {
    const videoElement = videoRefs.current.get(camera.id);
    if (!videoElement || isAnalyzing) return;

    const frameData = captureFrame(videoElement);
    if (!frameData) return;

    // Remove the data URL prefix for the API
    const base64Data = frameData.replace(/^data:image\/\w+;base64,/, '');
    
    const result = await analyzeFrame(camera, base64Data);
    
    // Update session stats
    setSessions(prev => {
      const newSessions = new Map(prev);
      const session = newSessions.get(camera.id) || {
        cameraId: camera.id,
        isActive: true,
        lastAnalysis: null,
        analysisCount: 0,
        detectionCount: 0,
      };
      
      newSessions.set(camera.id, {
        ...session,
        lastAnalysis: new Date(),
        analysisCount: session.analysisCount + 1,
        detectionCount: result?.isDumpingDetected 
          ? session.detectionCount + 1 
          : session.detectionCount,
      });
      
      return newSessions;
    });

    return result;
  }, [analyzeFrame, captureFrame, isAnalyzing]);

  // Run analysis cycle on all online cameras
  const runAnalysisCycle = useCallback(async () => {
    if (!isEnabled || isAnalyzing) return;

    const onlineCameras = cameras.filter(c => 
      c.status === 'online' && videoRefs.current.has(c.id)
    );

    // Analyze cameras sequentially to avoid overwhelming the API
    for (const camera of onlineCameras) {
      await analyzeCamera(camera);
      // Small delay between cameras
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  }, [cameras, isEnabled, isAnalyzing, analyzeCamera]);

  // Start/stop automated analysis
  const startAnalysis = useCallback(() => {
    setIsEnabled(true);
    // Initialize sessions for all cameras
    setSessions(new Map(
      cameras.map(c => [c.id, {
        cameraId: c.id,
        isActive: true,
        lastAnalysis: null,
        analysisCount: 0,
        detectionCount: 0,
      }])
    ));
  }, [cameras]);

  const stopAnalysis = useCallback(() => {
    setIsEnabled(false);
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, []);

  // Set up interval when enabled
  useEffect(() => {
    if (!isEnabled) return;

    // Initial analysis
    runAnalysisCycle();

    // Set up interval (convert seconds to ms)
    intervalRef.current = setInterval(runAnalysisCycle, frameAnalysisInterval * 1000);

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
      }
    };
  }, [isEnabled, frameAnalysisInterval, runAnalysisCycle]);

  // Manually trigger analysis for a specific camera
  const triggerAnalysis = useCallback(async (camera: Camera) => {
    return analyzeCamera(camera);
  }, [analyzeCamera]);

  return {
    isEnabled,
    isAnalyzing,
    sessions,
    startAnalysis,
    stopAnalysis,
    triggerAnalysis,
    registerVideoElement,
    totalAnalyses: Array.from(sessions.values()).reduce((sum, s) => sum + s.analysisCount, 0),
    totalDetections: Array.from(sessions.values()).reduce((sum, s) => sum + s.detectionCount, 0),
  };
}
