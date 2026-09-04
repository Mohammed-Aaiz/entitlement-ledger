import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import FloatingLines from '../components/react-bits/FloatingLinesLazy';

function BrandMark({ size = 'md' }: { size?: 'md' | 'lg' }) {
  const cls = size === 'lg' ? 'h-12 w-12 rounded-2xl text-lg' : 'h-10 w-10 rounded-xl text-sm';
  return (
    <span
      aria-hidden="true"
      className={`inline-flex items-center justify-center bg-gradient-to-br from-fuchsia-500 via-purple-600 to-violet-700 font-bold text-white shadow-[0_8px_20px_-8px_rgba(126,34,206,0.65)] ${cls}`}
    >
      EL
    </span>
  );
}

export default function Login() {
  const { login, register } = useAuth();
  const navigate = useNavigate();
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [email, setEmail] = useState('admin@demo.ledger');
  const [password, setPassword] = useState('demo1234');
  const [showPassword, setShowPassword] = useState(false);
  const [displayName, setDisplayName] = useState('');
  const [tenantName, setTenantName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      if (mode === 'login') {
        await login(email, password);
      } else {
        await register(email, password, displayName || email.split('@')[0], tenantName || 'default');
      }
      navigate('/');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Authentication failed');
    } finally {
      setSubmitting(false);
    }
  };

  const inputCls =
    'w-full rounded-lg border border-black/[0.09] bg-white/80 px-3.5 py-2.5 text-sm text-stone-900 shadow-sm placeholder:text-stone-400 focus:border-purple-500/50 focus:outline-none';

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden px-4 py-10">
      {/* ── Ambient backdrop ── */}
      <div aria-hidden="true" className="pointer-events-none fixed inset-0 -z-0">
        <div className="absolute -left-32 -top-40 h-96 w-96 rounded-full bg-fuchsia-500/[0.07] blur-3xl" />
        <div className="absolute -bottom-40 -right-24 h-[28rem] w-[28rem] rounded-full bg-violet-500/[0.09] blur-3xl" />
        <div className="absolute left-1/2 top-1/2 h-[36rem] w-[36rem] -translate-x-1/2 -translate-y-1/2 rounded-full bg-purple-400/[0.04] blur-3xl" />
      </div>
      <div aria-hidden="true" className="pointer-events-none fixed inset-x-0 bottom-0 h-56 opacity-70">
        <FloatingLines
          enabledWaves={['bottom']}
          lineCount={5}
          lineDistance={7}
          bendRadius={8}
          bendStrength={-1}
          interactive={false}
          parallax={false}
          animationSpeed={0.8}
          linesGradient={['#c084fc', '#a78bfa', '#e945f5']}
        />
      </div>

      <div className="relative z-10 w-full max-w-[400px]">
        {/* Card */}
        <div className="rounded-2xl border border-black/[0.06] bg-white/80 p-7 shadow-[0_24px_60px_-24px_rgba(16,16,20,0.25)] backdrop-blur-2xl sm:p-8">
          {/* Brand */}
          <div className="mb-7 text-center">
            <BrandMark size="lg" />
            <h1 className="mt-4 text-[22px] font-bold tracking-tight text-stone-900">EntitlementLedger</h1>
            <p className="mt-1 text-xs leading-relaxed text-stone-500">
              Financial decision provenance
              <br />
              <span className="text-[10px] uppercase tracking-[0.16em] text-stone-400">Evidence · Calculation · Ledger</span>
            </p>
          </div>

          {/* Toggle */}
          <div className="mb-6 flex rounded-xl border border-black/[0.06] bg-black/[0.03] p-1" role="tablist" aria-label="Authentication mode">
            <button
              type="button"
              role="tab"
              aria-selected={mode === 'login'}
              onClick={() => { setMode('login'); setError(null); }}
              className={`flex-1 rounded-lg py-2 text-xs font-semibold transition-all duration-200 ${
                mode === 'login' ? 'bg-white text-violet-700 shadow-sm' : 'text-stone-500 hover:text-stone-700'
              }`}
            >
              Sign In
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === 'register'}
              onClick={() => { setMode('register'); setError(null); }}
              className={`flex-1 rounded-lg py-2 text-xs font-semibold transition-all duration-200 ${
                mode === 'register' ? 'bg-white text-violet-700 shadow-sm' : 'text-stone-500 hover:text-stone-700'
              }`}
            >
              Register
            </button>
          </div>

          {/* Form */}
          <form onSubmit={handleSubmit} className="space-y-4">
            {error && (
              <div role="alert" className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-500/[0.05] px-3 py-2.5">
                <svg aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
                </svg>
                <p className="text-xs leading-relaxed text-red-700">{error}</p>
              </div>
            )}

            <div>
              <label htmlFor="email" className="mb-1.5 block text-[11px] font-semibold text-stone-600">Email</label>
              <input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="email" className={inputCls} />
            </div>

            <div>
              <label htmlFor="password" className="mb-1.5 block text-[11px] font-semibold text-stone-600">Password</label>
              <div className="relative">
                <input
                  id="password"
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  minLength={8}
                  autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                  className={`${inputCls} pr-10`}
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  aria-label={showPassword ? 'Hide password' : 'Show password'}
                  className="absolute right-2 top-1/2 -translate-y-1/2 rounded-md p-1 text-stone-400 transition-colors hover:text-stone-600"
                >
                  {showPassword ? (
                    <svg aria-hidden="true" className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" /><path d="M1 1l22 22" /></svg>
                  ) : (
                    <svg aria-hidden="true" className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8Z" /><circle cx="12" cy="12" r="3" /></svg>
                  )}
                </button>
              </div>
            </div>

            {mode === 'register' && (
              <>
                <div>
                  <label htmlFor="displayName" className="mb-1.5 block text-[11px] font-semibold text-stone-600">Display Name</label>
                  <input id="displayName" type="text" value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="Your name" className={inputCls} />
                </div>
                <div>
                  <label htmlFor="tenantName" className="mb-1.5 block text-[11px] font-semibold text-stone-600">Organization</label>
                  <input id="tenantName" type="text" value={tenantName} onChange={(e) => setTenantName(e.target.value)} placeholder="Company name" className={inputCls} />
                </div>
              </>
            )}

            <button
              type="submit"
              disabled={submitting}
              className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-gradient-to-r from-violet-600 to-purple-600 px-4 py-2.5 text-sm font-semibold text-white shadow-[0_10px_24px_-10px_rgba(126,34,206,0.7)] btn-smooth hover:from-violet-500 hover:to-purple-500 disabled:opacity-60"
            >
              {submitting ? (
                <>
                  <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                  {mode === 'login' ? 'Signing in…' : 'Creating account…'}
                </>
              ) : (
                mode === 'login' ? 'Sign In' : 'Create Account'
              )}
            </button>
          </form>
        </div>

        <p className="mt-5 text-center text-[10px] text-stone-400">
          Demo workspace — sign in with the seeded administrator account
          <br />
          or register a new tenant workspace.
        </p>
      </div>
    </div>
  );
}
