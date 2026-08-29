import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';

export default function Login() {
  const { login, register } = useAuth();
  const navigate = useNavigate();
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [email, setEmail] = useState('admin@demo.ledger');
  const [password, setPassword] = useState('demo1234');
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

  return (
    <div className="flex min-h-screen items-center justify-center bg-white/50 px-4">
      <div className="w-full max-w-sm">
        {/* Brand */}
        <div className="mb-8 text-center">
          <div className="mx-auto mb-3 inline-flex h-10 w-10 items-center justify-center rounded-lg bg-purple-600/15 text-purple-600 text-sm font-bold">
            EL
          </div>
          <h1 className="text-xl font-bold tracking-tight text-stone-800">EntitlementLedger</h1>
          <p className="mt-1 text-xs text-stone-600">Financial decision provenance</p>
        </div>

        {/* Toggle */}
        <div className="mb-6 flex rounded-lg border border-[var(--border)] bg-white/60 p-0.5">
          <button
            type="button"
            onClick={() => setMode('login')}
            className={`flex-1 rounded-md py-1.5 text-xs font-medium btn-smooth ${
              mode === 'login' ? 'bg-purple-600 text-[#0B0A0F]' : 'text-stone-600 hover:text-stone-800'
            }`}
          >
            Sign In
          </button>
          <button
            type="button"
            onClick={() => setMode('register')}
            className={`flex-1 rounded-md py-1.5 text-xs font-medium btn-smooth ${
              mode === 'register' ? 'bg-purple-600 text-[#0B0A0F]' : 'text-stone-600 hover:text-stone-800'
            }`}
          >
            Register
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-4">
          {error && (
            <div className="rounded-lg border border-red-200 bg-red-500/[0.04] px-3 py-2">
              <p className="text-xs text-red-600">{error}</p>
            </div>
          )}

          <div>
            <label className="block text-[11px] font-medium text-stone-600 mb-1.5">Email</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              className="w-full rounded-md border border-[var(--border)] bg-white/50 px-3 py-2 text-sm text-stone-800 placeholder:text-stone-500 focus:border-purple-500/40 focus:outline-none"
            />
          </div>

          <div>
            <label className="block text-[11px] font-medium text-stone-600 mb-1.5">Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
              className="w-full rounded-md border border-[var(--border)] bg-white/50 px-3 py-2 text-sm text-stone-800 placeholder:text-stone-500 focus:border-purple-500/40 focus:outline-none"
            />
          </div>

          {mode === 'register' && (
            <>
              <div>
                <label className="block text-[11px] font-medium text-stone-600 mb-1.5">Display Name</label>
                <input
                  type="text"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  placeholder="Your name"
                  className="w-full rounded-md border border-[var(--border)] bg-white/50 px-3 py-2 text-sm text-stone-800 placeholder:text-stone-500 focus:border-purple-500/40 focus:outline-none"
                />
              </div>
              <div>
                <label className="block text-[11px] font-medium text-stone-600 mb-1.5">Organization</label>
                <input
                  type="text"
                  value={tenantName}
                  onChange={(e) => setTenantName(e.target.value)}
                  placeholder="Company name"
                  className="w-full rounded-md border border-[var(--border)] bg-white/50 px-3 py-2 text-sm text-stone-800 placeholder:text-stone-500 focus:border-purple-500/40 focus:outline-none"
                />
              </div>
            </>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded-lg bg-purple-600 px-4 py-2.5 text-sm font-semibold text-[#0B0A0F] btn-smooth hover:bg-purple-700 disabled:opacity-50"
          >
            {submitting ? '...' : mode === 'login' ? 'Sign In' : 'Create Account'}
          </button>
        </form>


      </div>
    </div>
  );
}
