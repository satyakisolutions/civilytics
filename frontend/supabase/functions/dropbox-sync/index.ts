import { serve } from "https://deno.land/std@0.168.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type, x-supabase-client-platform, x-supabase-client-platform-version, x-supabase-client-runtime, x-supabase-client-runtime-version",
};

interface SyncRequest {
  eventId: string;
  fileType: "snapshot" | "video_clip";
  fileBase64: string;
  fileName: string;
}

serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { headers: corsHeaders });
  }

  try {
    const supabaseUrl = Deno.env.get("SUPABASE_URL")!;
    const supabaseKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
    const supabase = createClient(supabaseUrl, supabaseKey);

    // Get Dropbox tokens
    const { data: tokenData, error: tokenError } = await supabase
      .from("dropbox_tokens")
      .select("access_token, refresh_token, expires_at")
      .eq("id", "default")
      .maybeSingle();

    if (tokenError || !tokenData) {
      return new Response(
        JSON.stringify({ error: "Dropbox not connected", connected: false }),
        { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    let accessToken = tokenData.access_token;

    // Check if token needs refresh
    if (tokenData.expires_at && new Date(tokenData.expires_at) < new Date()) {
      const DROPBOX_APP_KEY = Deno.env.get("DROPBOX_APP_KEY");
      const DROPBOX_APP_SECRET = Deno.env.get("DROPBOX_APP_SECRET");

      if (!tokenData.refresh_token || !DROPBOX_APP_KEY || !DROPBOX_APP_SECRET) {
        return new Response(
          JSON.stringify({ error: "Token expired and cannot refresh", connected: false }),
          { status: 401, headers: { ...corsHeaders, "Content-Type": "application/json" } }
        );
      }

      // Refresh the token
      const refreshResponse = await fetch("https://api.dropboxapi.com/oauth2/token", {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
        },
        body: new URLSearchParams({
          grant_type: "refresh_token",
          refresh_token: tokenData.refresh_token,
          client_id: DROPBOX_APP_KEY,
          client_secret: DROPBOX_APP_SECRET,
        }),
      });

      if (!refreshResponse.ok) {
        return new Response(
          JSON.stringify({ error: "Failed to refresh token", connected: false }),
          { status: 401, headers: { ...corsHeaders, "Content-Type": "application/json" } }
        );
      }

      const newTokens = await refreshResponse.json();
      accessToken = newTokens.access_token;

      // Update stored tokens
      await supabase
        .from("dropbox_tokens")
        .update({
          access_token: newTokens.access_token,
          expires_at: newTokens.expires_in 
            ? new Date(Date.now() + newTokens.expires_in * 1000).toISOString()
            : null
        })
        .eq("id", "default");
    }

    const { eventId, fileType, fileBase64, fileName }: SyncRequest = await req.json();

    if (!eventId || !fileBase64 || !fileName) {
      return new Response(
        JSON.stringify({ error: "Missing required fields: eventId, fileBase64, fileName" }),
        { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    // Get event details for folder structure
    const { data: event } = await supabase
      .from("violation_events")
      .select(`
        id,
        timestamp,
        event_type,
        cameras!inner(zone, location)
      `)
      .eq("id", eventId)
      .single();

    const date = event?.timestamp 
      ? new Date(event.timestamp).toISOString().split("T")[0]
      : new Date().toISOString().split("T")[0];
    
    const zone = (event?.cameras as any)?.zone || "Unknown";
    const dropboxPath = `/WasteWatch/${date}/${zone}/${fileName}`;

    console.log(`Uploading to Dropbox: ${dropboxPath}`);

    // Convert base64 to binary
    const binaryStr = atob(fileBase64.replace(/^data:[^;]+;base64,/, ""));
    const bytes = new Uint8Array(binaryStr.length);
    for (let i = 0; i < binaryStr.length; i++) {
      bytes[i] = binaryStr.charCodeAt(i);
    }

    // Upload to Dropbox
    const uploadResponse = await fetch("https://content.dropboxapi.com/2/files/upload", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${accessToken}`,
        "Content-Type": "application/octet-stream",
        "Dropbox-API-Arg": JSON.stringify({
          path: dropboxPath,
          mode: "add",
          autorename: true,
          mute: false,
        }),
      },
      body: bytes,
    });

    if (!uploadResponse.ok) {
      const errorText = await uploadResponse.text();
      console.error("Dropbox upload failed:", errorText);
      
      // Record failed sync
      await supabase
        .from("evidence_files")
        .insert({
          event_id: eventId,
          file_type: fileType,
          dropbox_path: dropboxPath,
          sync_status: "failed"
        });

      return new Response(
        JSON.stringify({ error: "Dropbox upload failed", details: errorText }),
        { status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    const uploadResult = await uploadResponse.json();
    console.log("Dropbox upload success:", uploadResult.path_display);

    // Record successful sync
    await supabase
      .from("evidence_files")
      .insert({
        event_id: eventId,
        file_type: fileType,
        dropbox_path: uploadResult.path_display,
        sync_status: "synced",
        synced_at: new Date().toISOString()
      });

    return new Response(
      JSON.stringify({
        success: true,
        dropboxPath: uploadResult.path_display,
        eventId
      }),
      { headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );

  } catch (error) {
    console.error("Dropbox sync error:", error);
    return new Response(
      JSON.stringify({ error: error instanceof Error ? error.message : "Unknown error" }),
      { status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );
  }
});
