import { serve } from "https://deno.land/std@0.168.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type, x-supabase-client-platform, x-supabase-client-platform-version, x-supabase-client-runtime, x-supabase-client-runtime-version",
};

serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { headers: corsHeaders });
  }

  try {
    const url = new URL(req.url);
    const action = url.searchParams.get("action") || "init";

    const DROPBOX_APP_KEY = Deno.env.get("DROPBOX_APP_KEY");
    const DROPBOX_APP_SECRET = Deno.env.get("DROPBOX_APP_SECRET");

    if (!DROPBOX_APP_KEY || !DROPBOX_APP_SECRET) {
      return new Response(
        JSON.stringify({ 
          error: "Dropbox app credentials not configured",
          setup_required: true,
          instructions: "Please add DROPBOX_APP_KEY and DROPBOX_APP_SECRET to your secrets"
        }),
        { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    const supabaseUrl = Deno.env.get("SUPABASE_URL")!;
    const supabaseKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
    const supabase = createClient(supabaseUrl, supabaseKey);

    if (action === "init") {
      // Generate OAuth authorization URL
      const redirectUri = `${supabaseUrl}/functions/v1/dropbox-auth?action=callback`;
      const state = crypto.randomUUID();
      
      const authUrl = new URL("https://www.dropbox.com/oauth2/authorize");
      authUrl.searchParams.set("client_id", DROPBOX_APP_KEY);
      authUrl.searchParams.set("redirect_uri", redirectUri);
      authUrl.searchParams.set("response_type", "code");
      authUrl.searchParams.set("token_access_type", "offline");
      authUrl.searchParams.set("state", state);

      return new Response(
        JSON.stringify({ authUrl: authUrl.toString(), state }),
        { headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    if (action === "callback") {
      // Handle OAuth callback
      const code = url.searchParams.get("code");
      const error = url.searchParams.get("error");

      if (error) {
        // Redirect to app with error
        return new Response(null, {
          status: 302,
          headers: {
            ...corsHeaders,
            "Location": `/?dropbox_error=${encodeURIComponent(error)}`
          }
        });
      }

      if (!code) {
        return new Response(
          JSON.stringify({ error: "Missing authorization code" }),
          { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
        );
      }

      // Exchange code for tokens
      const redirectUri = `${supabaseUrl}/functions/v1/dropbox-auth?action=callback`;
      const tokenResponse = await fetch("https://api.dropboxapi.com/oauth2/token", {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
        },
        body: new URLSearchParams({
          code,
          grant_type: "authorization_code",
          client_id: DROPBOX_APP_KEY,
          client_secret: DROPBOX_APP_SECRET,
          redirect_uri: redirectUri,
        }),
      });

      if (!tokenResponse.ok) {
        const errorText = await tokenResponse.text();
        console.error("Token exchange failed:", errorText);
        return new Response(null, {
          status: 302,
          headers: {
            ...corsHeaders,
            "Location": `/?dropbox_error=token_exchange_failed`
          }
        });
      }

      const tokens = await tokenResponse.json();
      
      // Store tokens in database
      const { error: dbError } = await supabase
        .from("dropbox_tokens")
        .upsert({
          id: "default",
          access_token: tokens.access_token,
          refresh_token: tokens.refresh_token,
          expires_at: tokens.expires_in 
            ? new Date(Date.now() + tokens.expires_in * 1000).toISOString()
            : null
        });

      if (dbError) {
        console.error("Failed to store tokens:", dbError);
      }

      // Redirect back to app with success
      return new Response(null, {
        status: 302,
        headers: {
          ...corsHeaders,
          "Location": `/?dropbox_connected=true`
        }
      });
    }

    if (action === "status") {
      // Check if Dropbox is connected
      const { data, error } = await supabase
        .from("dropbox_tokens")
        .select("id, expires_at, updated_at")
        .eq("id", "default")
        .maybeSingle();

      if (error || !data) {
        return new Response(
          JSON.stringify({ connected: false }),
          { headers: { ...corsHeaders, "Content-Type": "application/json" } }
        );
      }

      const isExpired = data.expires_at && new Date(data.expires_at) < new Date();

      return new Response(
        JSON.stringify({ 
          connected: !isExpired,
          lastUpdated: data.updated_at
        }),
        { headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    if (action === "disconnect") {
      await supabase
        .from("dropbox_tokens")
        .delete()
        .eq("id", "default");

      return new Response(
        JSON.stringify({ success: true }),
        { headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    return new Response(
      JSON.stringify({ error: "Invalid action" }),
      { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );

  } catch (error) {
    console.error("Dropbox auth error:", error);
    return new Response(
      JSON.stringify({ error: error instanceof Error ? error.message : "Unknown error" }),
      { status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );
  }
});
