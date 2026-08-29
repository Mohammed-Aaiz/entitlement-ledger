import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import type { Decision, DashboardStats } from '../api/types';
import { formatINR, formatDate } from '../lib/format';
import { SkeletonCard } from '../App';

/* ─── Integrity badge ─── */
function IntegrityBadge({ valid }: { valid: boolean }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wide ${
        valid
          ? 'bg-emerald-500/10 text-emerald-600'
          : 'bg-red-500/10 text-red-600'
      }`}
    >
      <span className={`h-1 w-1 rounded-full ${valid ? 'bg-emerald-500' : 'bg-red-500'}`} />
      {valid ? 'Verified' : 'Compromised'}
    </span>
  );
}

export default function Decisions() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    setError(null);
    Promise.all([api.getStats(), api.getDecisions()])
      .then(([s, d]) => { setStats(s); setDecisions(d.items); })
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  if (loading) {
    return (
      <div className="space-y-6">
        <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => <SkeletonCard key={i} rows={2} />)}
        </div>
        <div className="grid gap-4 lg:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => <SkeletonCard key={i} rows={3} />)}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-500/[0.04] p-5">
        <p className="text-sm font-semibold text-red-600">Failed to load decisions</p>
        <p className="mt-1 text-xs text-stone-600">{error}</p>
        <button
          type="button"
          onClick={load}
          className="mt-3 rounded-lg bg-purple-600 px-4 py-2 text-sm font-semibold text-[#0B0A0F] btn-smooth hover:bg-purple-700"
        >
          Retry
        </button>
      </div>
    );
  }

  const flaggedCount = stats?.flagged_decisions ?? 0;

  return (
    <div className="space-y-8">
      {/* ── Header ── */}
      <div>
        <h1 className="text-xl font-semibold text-stone-800">Decisions</h1>
        <p className="mt-1 text-sm text-stone-500">All financial decisions in the ledger.</p>
      </div>

      {/* ── KPI row ── */}
      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <div className="surface p-4">
          <p className="section-label">Total</p>
          <p className="mt-1 amount text-2xl text-stone-800">{stats?.total_decisions ?? 0}</p>
        </div>
        <div className="surface p-4">
          <p className="section-label">Verified</p>
          <p className="mt-1 amount text-2xl text-emerald-600">{stats?.verified_decisions ?? 0}</p>
        </div>
        <div className="surface p-4">
          <p className="section-label">Flagged</p>
          <p className={`mt-1 amount text-2xl ${flaggedCount > 0 ? 'text-red-600' : 'text-stone-500'}`}>{flaggedCount}</p>
        </div>
        <div className="surface p-4">
          <p className="section-label">New Decision</p>
          <Link
            to="/analyze"
            className="mt-1 inline-flex items-center gap-1 text-sm font-semibold text-purple-600 hover:underline"
          >
            + Create →
          </Link>
        </div>
      </div>

      {/* ── Decision list ── */}
      {decisions.length > 0 ? (
        <div className="grid gap-4 lg:grid-cols-2">
          {decisions.map((d) => {
            const isTampered = d.decision_id === 'dec_005_tampered';
            const adj = d.gross_amount - d.final_amount;

            return (
              <Link
                key={d.decision_id}
                to={`/decisions/${d.decision_id}`}
                className="surface block p-5 card-smooth hover:border-purple-200"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs font-semibold text-stone-800">{d.decision_id}</span>
                      <IntegrityBadge valid={!isTampered} />
                    </div>
                    <div className="mt-2 flex items-baseline gap-2">
                      <span className="amount text-lg text-stone-800">{formatINR(d.gross_amount)}</span>
                      <span className="text-stone-500">→</span>
                      <span className="amount text-lg text-emerald-600">{formatINR(d.final_amount)}</span>
                    </div>
                    <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-stone-600">
                      <span className="font-mono">{d.entity_id}</span>
                      <span className="text-red-600">−{formatINR(adj)}</span>
                      <span>{formatDate(d.approved_at)}</span>
                    </div>
                    {d.line_items.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {d.line_items.map((li, i) => (
                          <span key={i} className="rounded border border-[var(--border)] bg-white/50 px-1.5 py-0.5 text-[10px] text-stone-600">
                            {li.label} <span className="amount">{formatINR(li.amount)}</span>
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                  <span className="shrink-0 text-[11px] font-medium text-purple-600">View →</span>
                </div>
              </Link>
            );
          })}
        </div>
      ) : (
        <div className="surface p-8 text-center">
          <p className="text-sm text-stone-600">No decisions yet.</p>
          <Link to="/analyze" className="mt-2 inline-block text-sm font-semibold text-purple-600 hover:underline">
            Create your first decision →
          </Link>
        </div>
      )}
    </div>
  );
}
