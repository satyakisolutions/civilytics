import { serve } from "https://deno.land/std@0.168.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type, x-supabase-client-platform, x-supabase-client-platform-version, x-supabase-client-runtime, x-supabase-client-runtime-version",
};

/**
 * Camera Stream Proxy
 * 
 * This edge function provides endpoints for camera stream management.
 * For WebRTC streaming, it returns signaling information and manages
 * camera connection states.
 * 
 * NOTE: Full RTSP-to-WebRTC transcoding requires a dedicated media server
 * (like Janus, MediaMTX, or similar) running on infrastructure that can
 * handle the computational load. This function provides the signaling
 * and coordination layer.
 */

interface CameraConfig {
  id: string;
  name: string;
  streamUrl: string;
  protocol: "rtsp" | "onvif" | "http";
  credentials?: {
    username: string;
    password: string;
  };
}

serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { headers: corsHeaders });
  }

  try {
    const url = new URL(req.url);
    const action = url.searchParams.get("action") || "list";

    const supabaseUrl = Deno.env.get("SUPABASE_URL")!;
    const supabaseKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
    const supabase = createClient(supabaseUrl, supabaseKey);

    if (action === "list") {
      // List all cameras with their stream status
      const { data: cameras, error } = await supabase
        .from("cameras")
        .select("*")
        .order("is_pinned", { ascending: false })
        .order("name");

      if (error) {
        throw error;
      }

      return new Response(
        JSON.stringify({ cameras }),
        { headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    if (action === "add") {
      // Add a new camera
      const body: CameraConfig = await req.json();
      
      if (!body.name || !body.streamUrl) {
        return new Response(
          JSON.stringify({ error: "Missing required fields: name, streamUrl" }),
          { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
        );
      }

      const { data: camera, error } = await supabase
        .from("cameras")
        .insert({
          name: body.name,
          zone: "Default Zone",
          location: "Unknown",
          stream_url: body.streamUrl,
          status: "offline"
        })
        .select()
        .single();

      if (error) {
        throw error;
      }

      return new Response(
        JSON.stringify({ success: true, camera }),
        { headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    if (action === "connect") {
      // Initiate connection to a camera
      const cameraId = url.searchParams.get("cameraId");
      
      if (!cameraId) {
        return new Response(
          JSON.stringify({ error: "Missing cameraId" }),
          { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
        );
      }

      const { data: camera, error } = await supabase
        .from("cameras")
        .select("*")
        .eq("id", cameraId)
        .single();

      if (error || !camera) {
        return new Response(
          JSON.stringify({ error: "Camera not found" }),
          { status: 404, headers: { ...corsHeaders, "Content-Type": "application/json" } }
        );
      }

      // For now, return connection info for client-side handling
      // In production, this would coordinate with a media server
      const connectionInfo = {
        cameraId: camera.id,
        name: camera.name,
        streamUrl: camera.stream_url,
        protocol: camera.stream_url?.includes("rtsp://") ? "rtsp" 
                : camera.stream_url?.includes("onvif://") ? "onvif" 
                : "http",
        status: "connecting",
        // WebRTC signaling would go here in a full implementation
        webrtc: {
          iceServers: [
            { urls: "stun:stun.l.google.com:19302" }
          ],
          // In production: Include TURN servers for NAT traversal
        },
        instructions: camera.stream_url 
          ? "Use a WebRTC media server to proxy this RTSP stream"
          : "No stream URL configured for this camera"
      };

      // Update camera status
      await supabase
        .from("cameras")
        .update({ status: "online" })
        .eq("id", cameraId);

      return new Response(
        JSON.stringify(connectionInfo),
        { headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    if (action === "disconnect") {
      const cameraId = url.searchParams.get("cameraId");
      
      if (cameraId) {
        await supabase
          .from("cameras")
          .update({ status: "offline" })
          .eq("id", cameraId);
      }

      return new Response(
        JSON.stringify({ success: true }),
        { headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    if (action === "snapshot") {
      // Capture a snapshot from the camera
      const cameraId = url.searchParams.get("cameraId");
      
      if (!cameraId) {
        return new Response(
          JSON.stringify({ error: "Missing cameraId" }),
          { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
        );
      }

      // In production, this would capture a frame from the stream
      // For now, return placeholder info
      return new Response(
        JSON.stringify({
          cameraId,
          timestamp: new Date().toISOString(),
          message: "Snapshot capture requires connection to media server",
          // In production: snapshotUrl would be returned
        }),
        { headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    if (action === "update-status") {
      // Bulk update camera statuses (for health checks)
      const { statuses }: { statuses: { id: string; status: string }[] } = await req.json();
      
      for (const { id, status } of statuses) {
        await supabase
          .from("cameras")
          .update({ status })
          .eq("id", id);
      }

      return new Response(
        JSON.stringify({ success: true, updated: statuses.length }),
        { headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    return new Response(
      JSON.stringify({ error: "Invalid action" }),
      { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );

  } catch (error) {
    console.error("Camera stream error:", error);
    return new Response(
      JSON.stringify({ error: error instanceof Error ? error.message : "Unknown error" }),
      { status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );
  }
});
