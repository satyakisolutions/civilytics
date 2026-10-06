import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { API_BASE } from '@/lib/api-base';
import { AUTH_FAILED_EVENT } from '@/lib/api-client';

export type Role = 'owner' | 'admin' | 'viewer';

export interface AuthUser {
  username: string;
  name?: string | null;
  email?: string | null;
  roles: string[];
}

interface AuthContextType {
  user: AuthUser | null;
  /** True until the initial session check finishes, so we don't flash the login page. */
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  /** owner implies admin implies viewer — see roleAtLeast(). */
  can: (role: Role) => boolean;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

/**
 * Keycloak realm roles are flat — a user granted `owner` does not
 * automatically hold `admin`. The backend's require_role() checks for the
 * exact role, so realm setup must grant composite roles (owner -> admin ->
 * viewer). We mirror that assumption here for UI gating only; the backend
 * remains the authority and will still 403 if the realm is set up otherwise.
 */
const RANK: Record<Role, number> = { viewer: 1, admin: 2, owner: 3 };

function roleAtLeast(roles: string[], required: Role): boolean {
  const held = roles
    .map((r) => RANK[r as Role])
    .filter((n): n is number => typeof n === 'number');
  if (!held.length) return false;
  return Math.max(...held) >= RANK[required];
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  // Restore an existing cookie session on load.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await fetch(`${API_BASE}/api/auth/me`);
        if (!cancelled && resp.ok) setUser(await resp.json());
      } catch {
        // Network failure on boot is indistinguishable from "not logged in"
        // from the page's point of view; show the login form either way.
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // The interceptor raises this once a request has failed auth and a silent
  // refresh did not recover it.
  useEffect(() => {
    const onAuthFailed = () => setUser(null);
    window.addEventListener(AUTH_FAILED_EVENT, onAuthFailed);
    return () => window.removeEventListener(AUTH_FAILED_EVENT, onAuthFailed);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const resp = await fetch(`${API_BASE}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    if (!resp.ok) {
      // The backend deliberately returns the same message for unknown user
      // and wrong password, so the form cannot be used to enumerate accounts.
      const detail = await resp.json().catch(() => null);
      throw new Error(detail?.detail ?? 'Sign-in failed');
    }
    setUser(await resp.json());
  }, []);

  const logout = useCallback(async () => {
    try {
      await fetch(`${API_BASE}/api/auth/logout`, { method: 'POST' });
    } finally {
      // Clear locally regardless — the cookies are gone either way.
      setUser(null);
    }
  }, []);

  const can = useCallback(
    (role: Role) => (user ? roleAtLeast(user.roles, role) : false),
    [user],
  );

  const value = useMemo(
    () => ({ user, loading, login, logout, can }),
    [user, loading, login, logout, can],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextType {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
