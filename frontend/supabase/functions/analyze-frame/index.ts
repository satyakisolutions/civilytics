import { serve } from "https://deno.land/std@0.168.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type, x-supabase-client-platform, x-supabase-client-platform-version, x-supabase-client-runtime, x-supabase-client-runtime-version",
};

interface AnalysisRequest {
  cameraId: string;
  cameraName: string;
  zone: string;
  location: string;
  imageBase64: string;
}

interface DetectionResult {
  isDumpingDetected: boolean;
  confidence: number;
  eventType: "dumping_detected" | "suspicious_activity" | "repeat_offender" | null;
  severity: "critical" | "warning" | "info";
  personDescription: string | null;
  description: string;
}

serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { headers: corsHeaders });
  }

  try {
    const LOVABLE_API_KEY = Deno.env.get("LOVABLE_API_KEY");
    if (!LOVABLE_API_KEY) {
      throw new Error("LOVABLE_API_KEY is not configured");
    }

    const { cameraId, cameraName, zone, location, imageBase64 }: AnalysisRequest = await req.json();

    if (!cameraId || !imageBase64) {
      return new Response(
        JSON.stringify({ error: "Missing cameraId or imageBase64" }),
        { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    console.log(`Analyzing frame from camera: ${cameraId} (${cameraName})`);

    // Call Gemini Vision API for dumping detection
    const response = await fetch("https://ai.gateway.lovable.dev/v1/chat/completions", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${LOVABLE_API_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: "google/gemini-2.5-pro",
        messages: [
          {
            role: "system",
            content: `You are a computer vision system specialized in detecting illegal waste dumping activities. 
Analyze surveillance camera frames and determine if illegal dumping is occurring.

DETECTION CRITERIA:
- Person actively dumping or disposing of waste/garbage in unauthorized areas
- Person carrying bags, boxes, or materials toward dumping spots
- Suspicious behavior near waste accumulation areas
- Vehicles unloading waste material

RESPONSE FORMAT (JSON only):
{
  "isDumpingDetected": boolean,
  "confidence": number (0-100),
  "eventType": "dumping_detected" | "suspicious_activity" | null,
  "severity": "critical" | "warning" | "info",
  "personDescription": string or null (brief description if person visible),
  "description": string (brief description of what's happening)
}

Set severity to:
- "critical" if active dumping detected with high confidence (>80%)
- "warning" if suspicious activity or moderate confidence
- "info" for minor observations

Be accurate and avoid false positives. Only flag clear violations.`
          },
          {
            role: "user",
            content: [
              {
                type: "text",
                text: `Analyze this surveillance frame from camera "${cameraName}" located at ${location} (${zone}). Detect any illegal waste dumping activity.`
              },
              {
                type: "image_url",
                image_url: {
                  url: imageBase64.startsWith("data:") ? imageBase64 : `data:image/jpeg;base64,${imageBase64}`
                }
              }
            ]
          }
        ]
      }),
    });

    if (!response.ok) {
      if (response.status === 429) {
        return new Response(
          JSON.stringify({ error: "Rate limit exceeded. Please try again later." }),
          { status: 429, headers: { ...corsHeaders, "Content-Type": "application/json" } }
        );
      }
      if (response.status === 402) {
        return new Response(
          JSON.stringify({ error: "AI credits exhausted. Please add funds." }),
          { status: 402, headers: { ...corsHeaders, "Content-Type": "application/json" } }
        );
      }
      const errorText = await response.text();
      console.error("AI gateway error:", response.status, errorText);
      throw new Error(`AI gateway error: ${response.status}`);
    }

    const aiResponse = await response.json();
    const content = aiResponse.choices?.[0]?.message?.content || "";
    
    console.log("AI Response:", content);

    // Parse the JSON response from AI
    let detection: DetectionResult;
    try {
      // Extract JSON from response (may be wrapped in markdown code blocks)
      const jsonMatch = content.match(/\{[\s\S]*\}/);
      if (jsonMatch) {
        detection = JSON.parse(jsonMatch[0]);
      } else {
        throw new Error("No JSON found in response");
      }
    } catch {
      console.error("Failed to parse AI response:", content);
      detection = {
        isDumpingDetected: false,
        confidence: 0,
        eventType: null,
        severity: "info",
        personDescription: null,
        description: "Analysis complete - no violations detected"
      };
    }

    // If dumping detected, create an event and alert in the database
    if (detection.isDumpingDetected && detection.confidence >= 70) {
      const supabaseUrl = Deno.env.get("SUPABASE_URL")!;
      const supabaseKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
      const supabase = createClient(supabaseUrl, supabaseKey);

      // Create violation event
      const { data: event, error: eventError } = await supabase
        .from("violation_events")
        .insert({
          camera_id: cameraId,
          event_type: detection.eventType || "dumping_detected",
          severity: detection.severity,
          confidence: detection.confidence,
          person_id: detection.personDescription ? `person-${Date.now()}` : null,
          status: "active"
        })
        .select()
        .single();

      if (eventError) {
        console.error("Failed to create event:", eventError);
      } else {
        console.log("Created violation event:", event.id);

        // Create alert
        const { error: alertError } = await supabase
          .from("alerts")
          .insert({
            event_id: event.id,
            severity: detection.severity,
            title: detection.severity === "critical" 
              ? "Illegal Dumping Detected!" 
              : "Suspicious Activity Detected",
            message: detection.description,
            camera_id: cameraId,
            zone: zone,
            status: "active"
          });

        if (alertError) {
          console.error("Failed to create alert:", alertError);
        }

        // Update camera status
        await supabase
          .from("cameras")
          .update({ 
            status: detection.severity === "critical" ? "alert" : "warning",
            last_event_at: new Date().toISOString()
          })
          .eq("id", cameraId);
      }
    }

    return new Response(
      JSON.stringify({
        success: true,
        detection,
        cameraId,
        timestamp: new Date().toISOString()
      }),
      { headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );

  } catch (error) {
    console.error("Analysis error:", error);
    return new Response(
      JSON.stringify({ error: error instanceof Error ? error.message : "Unknown error" }),
      { status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );
  }
});
