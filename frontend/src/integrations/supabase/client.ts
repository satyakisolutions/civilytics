// This file was scaffolded by Lovable. The LSG-WasteWatch deployment
// runs against its own FastAPI backend (apps/api at port 8082) and
// doesn't use Supabase for persistence, realtime, or storage. Rather
// than rewrite every hook that still imports from this module, we
// expose a SAFE STUB that short-circuits reads to empty results and
// writes to no-ops. Every existing `supabase.from(...).select()` /
// `.insert()` / `.update()` / `.channel(...)` call-site keeps working
// without any console noise.
//
// If you decide to re-enable the managed Supabase project later, set
// VITE_SUPABASE_URL + VITE_SUPABASE_PUBLISHABLE_KEY in frontend/.env
// to a reachable project and the real @supabase/supabase-js client
// will be returned instead.

import { createClient, SupabaseClient } from "@supabase/supabase-js";
import type { Database } from "./types";

const SUPABASE_URL = (import.meta as unknown as { env?: Record<string, string> }).env
  ?.VITE_SUPABASE_URL;
const SUPABASE_PUBLISHABLE_KEY = (import.meta as unknown as { env?: Record<string, string> }).env
  ?.VITE_SUPABASE_PUBLISHABLE_KEY;

const isValidConfig = (url?: string, key?: string): boolean =>
  !!url &&
  !!key &&
  url.startsWith("https://") &&
  url.includes(".supabase.co") &&
  // The original Lovable-generated URL points at a hosted project that
  // no longer exists (DNS doesn't resolve). Explicitly block it so the
  // stub kicks in even if the env var is still set.
  !url.includes("fpqajyyjxnidupsaimmw");

function makeStubClient(): SupabaseClient<Database> {
  // Chainable query builder that resolves `await` to { data: [], error: null }.
  // Implements every builder method the hooks in this repo actually call.
  const emptyOk = { data: [] as unknown[], error: null, count: 0, status: 200, statusText: "OK" };

  const makeBuilder = (): Record<string, unknown> => {
    const builder: Record<string, unknown> = {};
    const passthrough: Record<string, unknown> = new Proxy(builder, {
      get(_target, prop) {
        if (prop === "then") {
          return (resolve: (v: typeof emptyOk) => unknown) =>
            Promise.resolve(emptyOk).then(resolve);
        }
        if (prop === Symbol.iterator) return undefined;
        if (prop === "maybeSingle" || prop === "single") {
          return () => Promise.resolve({ ...emptyOk, data: null });
        }
        // Every other chainable call (select/eq/gte/order/limit/insert/
        // update/upsert/delete/etc.) returns the same builder so chains
        // keep working.
        return (..._args: unknown[]) => passthrough;
      },
    }) as unknown as Record<string, unknown>;
    return passthrough;
  };

  const channelStub = {
    on: () => channelStub,
    subscribe: () => channelStub,
    send: () => Promise.resolve(),
    unsubscribe: () => Promise.resolve("ok" as const),
  };

  const stub = {
    from: (_table: string) => makeBuilder(),
    rpc: (_fn: string, _args?: unknown) => makeBuilder(),
    channel: (_name: string) => channelStub,
    removeChannel: (_ch: unknown) => Promise.resolve("ok" as const),
    removeAllChannels: () => Promise.resolve("ok" as const),
    getChannels: () => [],
    auth: {
      getSession: () => Promise.resolve({ data: { session: null }, error: null }),
      onAuthStateChange: (_cb: unknown) => ({
        data: { subscription: { unsubscribe: () => undefined } },
        error: null,
      }),
      signInWithPassword: () =>
        Promise.resolve({ data: { user: null, session: null }, error: null }),
      signOut: () => Promise.resolve({ error: null }),
    },
    storage: {
      from: (_bucket: string) => ({
        upload: () => Promise.resolve({ data: null, error: null }),
        download: () => Promise.resolve({ data: null, error: null }),
        getPublicUrl: (_path: string) => ({ data: { publicUrl: "" } }),
        list: () => Promise.resolve({ data: [], error: null }),
        remove: () => Promise.resolve({ data: [], error: null }),
      }),
    },
    functions: {
      invoke: (_name: string, _args?: unknown) =>
        Promise.resolve({ data: null, error: null }),
    },
  };

  // eslint-disable-next-line no-console
  console.info(
    "[supabase] using local stub client (no remote calls). Set " +
      "VITE_SUPABASE_URL + VITE_SUPABASE_PUBLISHABLE_KEY in frontend/.env " +
      "to enable the real hosted client."
  );

  // Cast through unknown — the stub satisfies the shape we actually use,
  // not the full 1000-line SupabaseClient interface.
  return stub as unknown as SupabaseClient<Database>;
}

export const supabase: SupabaseClient<Database> = isValidConfig(
  SUPABASE_URL,
  SUPABASE_PUBLISHABLE_KEY
)
  ? createClient<Database>(SUPABASE_URL as string, SUPABASE_PUBLISHABLE_KEY as string, {
      auth: {
        storage: localStorage,
        persistSession: true,
        autoRefreshToken: true,
      },
    })
  : makeStubClient();
