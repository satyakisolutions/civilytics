import { useState } from 'react';
import { Loader2, AlertTriangle, Eye, EyeOff, CheckCircle2, ArrowLeft } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useAuth } from '@/context/AuthContext';
import { API_BASE } from '@/lib/api-base';

/**
 * Civilytics branded sign-in.
 *
 * Posts to POST /api/auth/login, which performs the Keycloak token exchange
 * server-side and sets httpOnly cookies. No token ever reaches this
 * component or localStorage.
 *
 * Forgot-password posts to POST /api/auth/forgot-password, which asks
 * Keycloak to email a reset link. That endpoint always succeeds (it never
 * reveals whether the account exists) and only delivers if realm SMTP is
 * configured.
 */
export default function Login() {
  const { login } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const [mode, setMode] = useState<'login' | 'forgot'>('login');
  const [resetUser, setResetUser] = useState('');
  const [resetSent, setResetSent] = useState(false);

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

  async function onForgot(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await fetch(`${API_BASE}/api/auth/forgot-password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: resetUser }),
      });
      // Always show the same confirmation — the endpoint never reveals
      // whether the account exists.
      setResetSent(true);
    } catch {
      // Network failure is the only thing worth surfacing here.
      setError('Could not reach the server. Try again.');
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

        {mode === 'login' && (
          <>
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
                <div className="relative">
                  <Input
                    id="password"
                    type={showPassword ? 'text' : 'password'}
                    autoComplete="current-password"
                    required
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    disabled={submitting}
                    className="pr-10"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((v) => !v)}
                    disabled={submitting}
                    aria-label={showPassword ? 'Hide password' : 'Show password'}
                    aria-pressed={showPassword}
                    className="absolute inset-y-0 right-0 flex items-center px-3 text-muted-foreground hover:text-foreground focus:outline-none focus-visible:ring-1 focus-visible:ring-ring rounded-r-md"
                  >
                    {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                </div>
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

            <button
              type="button"
              onClick={() => {
                setMode('forgot');
                setError(null);
                setResetUser(username);
                setResetSent(false);
              }}
              className="text-xs text-primary hover:underline mt-6"
            >
              Forgot your password?
            </button>
          </>
        )}

        {mode === 'forgot' && (
          <>
            <h1 className="text-xl font-semibold mb-1">Reset password</h1>
            {resetSent ? (
              <div className="mt-4 flex items-start gap-2 text-xs text-muted-foreground bg-muted/40 border border-border rounded-md p-3">
                <CheckCircle2 className="w-4 h-4 mt-px shrink-0 text-primary" />
                <span>
                  If an account with that username exists, a password-reset link has
                  been emailed to its address. Check your inbox and spam folder.
                </span>
              </div>
            ) : (
              <>
                <p className="text-xs text-muted-foreground mb-6">
                  Enter your username and we'll email a reset link to the address on file.
                </p>
                <form onSubmit={onForgot} className="space-y-4">
                  <div className="grid gap-2">
                    <Label htmlFor="reset-user">Username</Label>
                    <Input
                      id="reset-user"
                      autoComplete="username"
                      autoFocus
                      required
                      value={resetUser}
                      onChange={(e) => setResetUser(e.target.value)}
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

                  <Button type="submit" className="w-full" disabled={submitting || !resetUser}>
                    {submitting && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                    {submitting ? 'Sending…' : 'Send reset link'}
                  </Button>
                </form>
              </>
            )}

            <button
              type="button"
              onClick={() => {
                setMode('login');
                setError(null);
              }}
              className="flex items-center gap-1 text-xs text-primary hover:underline mt-6"
            >
              <ArrowLeft className="w-3 h-3" /> Back to sign in
            </button>
          </>
        )}

        <p className="text-[9px] text-muted-foreground mt-8">
          © 2026 Satyaki Solutions Pvt Ltd
        </p>
      </div>
    </div>
  );
}
