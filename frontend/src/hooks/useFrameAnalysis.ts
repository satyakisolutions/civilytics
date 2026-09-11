import { useState, useCallback } from 'react';
import { Camera } from '@/types/monitoring';
import { useToast } from '@/hooks/use-toast';
import { VLM_BASE } from '@/lib/api-base';

const VLM_MODEL = "Qwen/Qwen2.5-VL-7B-Instruct-AWQ";
const VLM_PROMPT = `You are a waste-dumping surveillance analyst for Lake Shore Gardens, Bangalore. Look at this single CCTV frame and decide whether the person(s) in it are committing or about to commit illegal dumping (carrying garbage, dropping objects, leaving debris). Reply with a single JSON object and NOTHING else, matching this exact shape:
{
  "isDumpingDetected": boolean,
  "confidence": number 0-1,
  "eventType": "dumping_detected" | "suspicious_activity" | "repeat_offender" | null,
  "severity": "critical" | "warning" | "info",
  "personDescription": "<short phrase or null>",
  "description": "<one short sentence explaining what you see>"
}`;

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
      // Direct OpenAI-compatible call into vLLM (Qwen2.5-VL-7B). The
      // legacy supabase.functions.invoke('analyze-frame') call is dead
      // — Supabase has been replaced with a stub for LSG. Going to vLLM
      // directly keeps the round-trip on-LAN and avoids needing yet
      // another backend route just for this one button.
      const resp = await fetch(`${VLM_BASE}/v1/chat/completions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: VLM_MODEL,
          temperature: 0.1,
          max_tokens: 256,
          messages: [{
            role: "user",
            content: [
              { type: "text", text: VLM_PROMPT },
              { type: "image_url", image_url: { url: `data:image/jpeg;base64,${imageBase64}` } },
            ],
          }],
          response_format: { type: "json_object" },
        }),
      });

      if (!resp.ok) {
        throw new Error(`vLLM ${resp.status} ${resp.statusText}`);
      }
      const payload = await resp.json();
      const raw = payload?.choices?.[0]?.message?.content ?? "{}";
      // Qwen sometimes wraps JSON in ```json … ``` despite response_format.
      const jsonText = raw.replace(/^\s*```(?:json)?\s*|\s*```\s*$/g, "").trim();
      const result = JSON.parse(jsonText) as AnalysisResult;
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
