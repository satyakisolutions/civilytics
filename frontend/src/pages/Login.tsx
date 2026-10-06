import { useState } from 'react';
import { Loader2, AlertTriangle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useAuth } from '@/context/AuthContext';

/**
 * Civilytics branded sign-in.
 *
 * Posts to POST /api/auth/login, which performs the Keycloak token exchange
 * server-side and sets httpOnly cookies. No token ever reaches this
 * component or localStorage.
 *
 * Note on what this flow cannot do, by design of the Direct Access Grant:
 * no interactive MFA, no self-service password reset, no email verification.
 * Those live in Keycloak's own login flow. Account recovery is therefore an
 * administrator action via Keycloak.
 */
export default function Login() {
  const { login } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(username, password);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-in failed');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm">
        {/* Brand */}
        <div className="flex items-center gap-3 mb-8">
          <div
            className="w-10 h-11 grid place-items-center bg-[#ff9a2e] text-[#122032] text-2xl font-bold shrink-0"
            style={{ clipPath: 'polygon(50% 0, 93% 25%, 93% 75%, 50% 100%, 7% 75%, 7% 25%)' }}
            aria-hidden
          >
            C
          </div>
          <div>
            <div className="text-lg font-semibold tracking-tight">Civilytics</div>
            <div className="text-[10px] text-muted-foreground">Satyaki Solutions Pvt Ltd</div>
          </div>
        </div>

        <h1 className="text-xl font-semibold mb-1">Sign in</h1>
        <p className="text-xs text-muted-foreground mb-6">
          Use the credentials issued by your administrator.
        </p>

        <form onSubmit={onSubmit} className="space-y-4">
          <div className="grid gap-2">
            <Label htmlFor="username">Username</Label>
            <Input
              id="username"
              autoComplete="username"
              autoFocus
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              disabled={submitting}
            />
          </div>

          <div className="grid gap-2">
            <Label htmlFor="password">Password</Label>
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={submitting}
            />
          </div>

          {error && (
            <div
              role="alert"
              className="flex items-start gap-2 text-xs text-destructive bg-destructive/10 border border-destructive/30 rounded-md p-3"
            >
              <AlertTriangle className="w-4 h-4 mt-px shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <Button type="submit" className="w-full" disabled={submitting || !username || !password}>
            {submitting && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
            {submitting ? 'Signing in…' : 'Sign in'}
          </Button>
        </form>

        <p className="text-[10px] text-muted-foreground mt-8 leading-relaxed">
          Forgotten your password? Contact your administrator — password resets are
          managed centrally.
        </p>
        <p className="text-[9px] text-muted-foreground mt-5">
          © 2026 Satyaki Solutions Pvt Ltd
        </p>
      </div>
    </div>
  );
}
