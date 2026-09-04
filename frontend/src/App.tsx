import { BrowserRouter, Routes, Route, Link, useNavigate, useLocation, Navigate } from 'react-router-dom';
import { useEffect, useState, useCallback, useRef } from 'react';
import {
  GoBeaker, GoCreditCard, GoFile, GoGitBranch, GoGraph, GoHome,
  GoListUnordered, GoOrganization, GoPlus, GoSearch, GoShieldLock,
} from 'react-icons/go';
import type { IconType } from 'react-icons';
import { api } from './api/client';
import type { AIStatus, Decision } from './api/types';
import { AuthProvider, useAuth } from './auth/AuthContext';
import ErrorBoundary from './components/ErrorBoundary';
import FloatingLines from './components/react-bits/FloatingLinesLazy';
import Home from './pages/Home';
import DecisionDetail from './pages/DecisionDetail';
import AuditTrail from './pages/AuditTrail';
import SellerProfile from './pages/SellerProfile';
import EvidenceView from './pages/EvidenceView';
import DefensePacket from './pages/DefensePacket';
import RazorpayEvents from './pages/RazorpayEvents';
import Scenarios from './pages/Scenarios';
import AnalyzeDecision from './pages/AnalyzeDecision';
import Decisions from './pages/Decisions';
import Login from './pages/Login';
import FinanceControlRoom from './pages/FinanceControlRoom';

/* ─── Navigation items ─── */
interface NavItem {
  label: string;
  href: string;
  icon: IconType;
}

const NAV_SECTIONS: { title: string; items: NavItem[] }[] = [
  {
    title: 'OVERVIEW',
    items: [
      { label: 'Home', href: '/', icon: GoHome },
      { label: 'Decisions', href: '/decisions', icon: GoListUnordered },
      { label: 'New Decision', href: '/analyze', icon: GoPlus },
    ],
  },
  {
    title: 'AUDIT',
    items: [
      { label: 'Audit Trail', href: '/audit', icon: GoGitBranch },
      { label: 'Evidence', href: '/decisions/dec_001/evidence', icon: GoFile },
      { label: 'Defense Packets', href: '/decisions/dec_001/defense', icon: GoShieldLock },
    ],
  },
  {
    title: 'FINANCE',
    items: [
      { label: 'Finance Control Room', href: '/finance-control-room', icon: GoGraph },
    ],
  },
  {
    title: 'OPERATIONS',
    items: [
      { label: 'Sellers', href: '/sellers/seller_abc', icon: GoOrganization },
      { label: 'Scenario Lab', href: '/scenarios', icon: GoBeaker },
      { label: 'Razorpay Events', href: '/razorpay', icon: GoCreditCard },
    ],
  },
];

/* ─── Skeleton components ─── */
export function SkeletonLine({ className = '' }: { className?: string }) {
  return <div className={`skeleton h-4 ${className}`} />;
}

export function SkeletonCard({ rows = 3 }: { rows?: number }) {
  return (
    <div className="surface p-5 space-y-3">
      <SkeletonLine className="w-1/3" />
      {Array.from({ length: rows }).map((_, i) => (
        <SkeletonLine key={i} className={i === rows - 1 ? 'w-2/3' : 'w-full'} />
      ))}
    </div>
  );
}

export function SkeletonTable({ rows = 5 }: { rows?: number }) {
  return (
    <div className="surface overflow-hidden">
      <div className="space-y-0">
        {Array.from({ length: rows }).map((_, i) => (
          <div key={i} className="flex items-center gap-4 border-b border-[var(--border)] px-4 py-3">
            <SkeletonLine className="w-20" />
            <SkeletonLine className="flex-1" />
            <SkeletonLine className="w-16" />
          </div>
        ))}
      </div>
    </div>
  );
}

/* ─── AI Status badge (polled every 30s) ─── */
function AIStatusBadge() {
  const [status, setStatus] = useState<AIStatus | null>(null);

  useEffect(() => {
    let alive = true;
    const poll = () => {
      api.getAIStatus()
        .then((s) => { if (alive) setStatus(s); })
        .catch(() => { if (alive) setStatus(null); });
    };
    poll();
    const interval = setInterval(poll, 30000);
    return () => { alive = false; clearInterval(interval); };
  }, []);

  if (!status) return null;

  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border)] bg-white/50 backdrop-blur-md px-2.5 py-1 text-[11px] shadow-sm">
      <span className={`h-1.5 w-1.5 rounded-full ${status.available ? 'bg-emerald-500' : 'bg-amber-500'}`} />
      {status.available ? (
        <span className="text-stone-600">
          <span className="font-medium text-stone-800">{status.provider}</span>
          {' · '}{status.model || 'connected'}
        </span>
      ) : (
        <span className="text-stone-500">Seeded demo data</span>
      )}
    </span>
  );
}

/* ─── Global search ─── */
function GlobalSearch() {
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<Decision[]>([]);
  const [open, setOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const search = useCallback((q: string) => {
    if (!q.trim()) { setResults([]); return; }
    api.getDecisions().then((result) => {
      const decisions = result.items;
      const lower = q.toLowerCase();
      setResults(
        decisions.filter(
          (d) =>
            d.decision_id.toLowerCase().includes(lower) ||
            d.entity_id.toLowerCase().includes(lower)
        ).slice(0, 5)
      );
    }).catch(() => {});
  }, []);

  useEffect(() => {
    const timeout = setTimeout(() => search(query), 200);
    return () => clearTimeout(timeout);
  }, [query, search]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        inputRef.current?.focus();
        setOpen(true);
      }
      if (e.key === 'Escape') {
        setOpen(false);
        inputRef.current?.blur();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  return (
    <div className="relative">
      <GoSearch aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[13px] text-stone-400" />
      <input
        ref={inputRef}
        type="text"
        value={query}
        onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        placeholder="Jump to decision…"
        aria-label="Search decisions"
        className="w-48 rounded-full border border-[var(--border)] bg-white/60 pl-8 pr-10 py-1.5 text-[12px] text-stone-800 placeholder:text-stone-400 shadow-sm focus:border-purple-400/50 focus:outline-none sm:w-64 btn-smooth"
      />
      <kbd className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 rounded border border-[var(--border)] bg-white/50 px-1.5 py-0.5 text-[9px] text-stone-500">⌘K</kbd>

      {open && results.length > 0 && (
        <div className="absolute left-0 top-full z-50 mt-2 w-full rounded-xl border border-[var(--border)] bg-white/90 backdrop-blur-xl py-1 shadow-xl crossfade-enter">
          {results.map((d) => (
            <button
              key={d.decision_id}
              type="button"
              onClick={() => {
                navigate(`/decisions/${d.decision_id}`);
                setOpen(false);
                setQuery('');
              }}
              className="flex w-full items-center justify-between px-4 py-2.5 text-left text-[12px] hover:bg-stone-100/50 btn-smooth transition-colors"
            >
              <span className="font-mono text-stone-800 font-medium">{d.decision_id}</span>
              <span className="text-stone-500">{d.entity_id}</span>
            </button>
          ))}
        </div>
      )}
      {open && query && results.length === 0 && (
        <div className="absolute left-0 top-full z-50 mt-2 w-full rounded-xl border border-[var(--border)] bg-white/90 backdrop-blur-xl px-4 py-3 text-center text-[12px] text-stone-500 shadow-xl crossfade-enter">
          No decisions matching &quot;{query}&quot;
        </div>
      )}
    </div>
  );
}

/* ─── Navigation Drawer (Central Dropdown style) ─── */
function NavDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const location = useLocation();
  const [animState, setAnimState] = useState<'closed' | 'opening' | 'open' | 'closing'>('closed');

  const isActive = (href: string) => {
    if (href === '/') return location.pathname === '/';
    return location.pathname.startsWith(href);
  };

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, onClose]);

  useEffect(() => {
    if (open) {
      setAnimState('opening');
      const t = setTimeout(() => setAnimState('open'), 450);
      return () => clearTimeout(t);
    } else if (animState === 'open' || animState === 'opening') {
      setAnimState('closing');
      const t = setTimeout(() => setAnimState('closed'), 350);
      return () => clearTimeout(t);
    }
  }, [open]);

  const visible = animState !== 'closed';

  return (
    <>
      {visible && (
        <div
          className={`fixed inset-0 z-40 bg-white/10 backdrop-blur-[2px] transition-opacity duration-300 ${open ? 'opacity-100' : 'opacity-0'}`}
          onClick={onClose}
          aria-hidden="true"
        />
      )}

      {visible && (
        <div
          className={`fixed left-1/2 top-20 z-50 w-[280px] -translate-x-1/2 transform transition-all duration-300 ${
            open ? 'opacity-100 translate-y-0 scale-100' : 'opacity-0 -translate-y-4 scale-95'
          }`}
          role="dialog"
          aria-label="Navigation"
        >
          <div className="overflow-hidden rounded-2xl border border-white/20 bg-white/80 p-2 shadow-2xl backdrop-blur-2xl">
            <nav className="space-y-1">
              {NAV_SECTIONS.map((section) => (
                <div key={section.title} className="mb-2">
                  <div className="mb-1 px-3 py-1 text-[9px] font-bold tracking-widest text-stone-400">
                    {section.title}
                  </div>
                  {section.items.map((item) => {
                    const active = isActive(item.href);
                    return (
                      <Link
                        key={item.href}
                        to={item.href}
                        onClick={onClose}
                        className={`group flex items-center gap-3 rounded-xl px-3 py-2 text-sm font-medium transition-all ${
                          active
                            ? 'bg-gradient-to-r from-fuchsia-500/[0.08] to-violet-500/[0.06] text-violet-800 ring-1 ring-violet-500/15'
                            : 'text-stone-600 hover:bg-black/[0.03] hover:text-stone-900'
                        }`}
                      >
                        <item.icon aria-hidden="true" className={`text-[15px] transition-colors ${active ? 'text-violet-600' : 'text-stone-400 group-hover:text-stone-600'}`} />
                        {item.label}
                        {active && <span aria-hidden="true" className="ml-auto h-1.5 w-1.5 rounded-full bg-gradient-to-r from-fuchsia-500 to-violet-500" />}
                      </Link>
                    );
                  })}
                </div>
              ))}
            </nav>
            <div className="mt-2 border-t border-black/[0.06] px-3 pb-2 pt-2.5">
              <div className="flex items-center justify-between">
                <span className="text-[10px] text-stone-400">EntitlementLedger v0.2.0</span>
                <span className="inline-flex items-center gap-1 text-[9px] font-medium text-stone-400">
                  <span className="h-1 w-1 rounded-full bg-emerald-500" />
                  Decision provenance
                </span>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

/* ─── Protected Route ─── */
function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#fdfdfc]">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-purple-400/30 border-t-purple-500" />
      </div>
    );
  }
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

/* ─── App Shell ─── */
function AppShell() {
  const location = useLocation();
  const { user, logout } = useAuth();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const isHome = location.pathname === '/';

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: 'instant' });
  }, [location.pathname]);

  useEffect(() => {
    setDrawerOpen(false);
  }, [location.pathname]);

  if (!user) return null;

  return (
    <div className="relative min-h-screen w-full font-sans text-stone-800 selection:bg-purple-200">
      
      {/* ── Global Background ── */}
      <div 
        className="fixed inset-0 z-0 transition-all duration-1000 ease-[cubic-bezier(0.16,1,0.3,1)] pointer-events-none"
        style={{
          filter: isHome ? 'blur(0px)' : 'blur(10px) brightness(1.05) saturate(0.8)',
          opacity: isHome ? 1 : 0.8,
          transform: isHome ? 'scale(1)' : 'scale(1.02)'
        }}
      >
        <div className="absolute inset-0 z-0">
          <FloatingLines
            enabledWaves={['top', 'middle', 'bottom']}
            lineCount={8}
            lineDistance={8}
            bendRadius={8}
            bendStrength={-2}
            interactive={true}
            parallax={true}
            animationSpeed={1}
            linesGradient={['#e945f5', '#6f6f6f', '#6a6a6a']}
          />
        </div>
      </div>

      <div className="relative z-10 flex min-h-screen flex-col">
        {/* ── Navigation Drawer ── */}
        <NavDrawer open={drawerOpen} onClose={() => setDrawerOpen(false)} />

        {/* ── Top Bar ── */}
        <header className="sticky top-4 z-30 px-6 lg:px-8 mt-2 mb-4 pointer-events-none">
          <div className="mx-auto flex h-14 max-w-7xl items-center justify-between">
            
            {/* Left: Search & AI Status */}
            <div className="flex items-center gap-3 pointer-events-auto">
              <GlobalSearch />
              <AIStatusBadge />
            </div>

            {/* Center: The new central title widget */}
            <div className="absolute left-1/2 -translate-x-1/2 pointer-events-auto">
              <button
                type="button"
                onClick={() => setDrawerOpen(!drawerOpen)}
                className="group flex items-center gap-3 rounded-full border border-white/20 bg-white/70 px-4 py-2 shadow-sm backdrop-blur-xl transition-all hover:bg-white/90 hover:shadow-md"
                aria-label="Toggle navigation"
                aria-expanded={drawerOpen}
              >
                <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.5" className={`text-stone-500 transition-transform duration-300 ${drawerOpen ? 'rotate-90' : ''}`}>
                  <path className="hamburger-line" d="M3 5h12" />
                  <path className="hamburger-line" d="M3 9h12" />
                  <path className="hamburger-line" d="M3 13h12" />
                </svg>
                <span className="text-[13px] font-bold tracking-wide text-stone-800">
                  ENTITLEMENTLEDGER
                </span>
              </button>
            </div>

            {/* Right: User Menu */}
            <div className="flex items-center gap-3 pointer-events-auto">
              <div className="flex items-center gap-2.5 rounded-full border border-white/20 bg-white/70 py-1 pl-1 pr-3 shadow-sm backdrop-blur-xl">
                <span
                  aria-hidden="true"
                  className="flex h-7 w-7 items-center justify-center rounded-full bg-gradient-to-br from-fuchsia-500 to-violet-600 text-[10px] font-bold text-white shadow-sm"
                >
                  {(user.display_name || user.email || '?').slice(0, 2).toUpperCase()}
                </span>
                <span className="hidden text-[11px] font-medium text-stone-700 sm:block">{user.display_name}</span>
                <span className="rounded-md bg-stone-100 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-stone-500">
                  {user.role}
                </span>
                <div className="mx-0.5 h-3 w-px bg-stone-300" />
                <button
                  type="button"
                  onClick={logout}
                  className="text-[11px] font-medium text-stone-500 transition-colors hover:text-red-600"
                >
                  Logout
                </button>
              </div>
            </div>
          </div>
        </header>

        {/* ── Main content ── */}
        <main className={`flex-1 ${isHome ? '' : 'px-4 pb-12 lg:px-8 pt-4'}`}>
          <div className={isHome ? 'h-full w-full' : 'mx-auto max-w-[1280px]'}>
            <Routes location={location}>
              <Route path="/" element={<Home />} />
              <Route path="/analyze" element={<AnalyzeDecision />} />
              <Route path="/decisions" element={<Decisions />} />
              <Route path="/decisions/:id" element={<DecisionDetail />} />
              <Route path="/decisions/:id/evidence" element={<EvidenceView />} />
              <Route path="/decisions/:id/defense" element={<DefensePacket />} />
              <Route path="/audit" element={<AuditTrail />} />
              <Route path="/sellers/:entityId" element={<SellerProfile />} />
              <Route path="/scenarios" element={<Scenarios />} />
              <Route path="/razorpay" element={<RazorpayEvents />} />
              <Route path="/finance-control-room" element={<FinanceControlRoom />} />
            </Routes>
          </div>
        </main>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/*" element={
              <ProtectedRoute>
                <AppShell />
              </ProtectedRoute>
            } />
          </Routes>
        </AuthProvider>
      </BrowserRouter>
    </ErrorBoundary>
  );
}
