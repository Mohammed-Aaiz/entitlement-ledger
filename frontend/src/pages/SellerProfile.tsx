import { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { api } from '../api/client';
import type { SellerDecisions } from '../api/types';
import { formatINR, formatDate } from '../lib/format';
import { SkeletonCard, SkeletonTable } from '../App';

export default function SellerProfile() {
  const { entityId } = useParams<{ entityId: string }>();
  const [data, setData] = useState<SellerDecisions | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!entityId) return;
    api.getSellerDecisions(entityId).then(setData).catch((e) => setError(e.message)).finally(() => setLoading(false));
  }, [entityId]);

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="h-8 skeleton w-48" />
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">{Array.from({ length: 5 }).map((_, i) => <SkeletonCard key={i} rows={1} />)}</div>
        <SkeletonTable rows={4} />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-500/[0.04] p-5">
        <p className="text-sm font-semibold text-red-600">Seller not found</p>
        <p className="mt-1 text-xs text-stone-600">{error || 'No seller data available for this entity.'}</p>
        <div className="mt-3"><Link to="/" className="text-xs font-medium text-purple-600 hover:underline">← Back to dashboard</Link></div>
      </div>
    );
  }

  const flagged = data.decisions.filter(({ verification }) => !verification.valid).length;
  const stats = [
    { label: 'Total Decisions', value: String(data.total_decisions), accent: 'text-stone-800' },
    { label: 'Gross Entitlement', value: formatINR(data.total_gross_entitlement), accent: 'text-stone-800' },
    { label: 'Total Deductions', value: `−${formatINR(data.total_adjustments)}`, accent: 'text-red-600' },
    { label: 'Net Payout', value: formatINR(data.total_final_amount), accent: 'text-emerald-600' },
    { label: 'Integrity', value: flagged > 0 ? `${flagged} flagged` : 'All verified', accent: flagged > 0 ? 'text-red-600' : 'text-emerald-600' },
  ];

  return (
    <div className="space-y-6">
      {/* ── Header ── */}
      <div>
        <div className="flex items-center gap-3">
          <Link to="/" className="text-xs text-stone-500 hover:text-purple-600 transition-colors">← Dashboard</Link>
          <h1 className="text-xl font-bold tracking-tight text-stone-800 sm:text-2xl">Seller Profile</h1>
        </div>
        <p className="mt-1 font-mono text-sm text-purple-600">{data.entity_id}</p>
        <p className="mt-0.5 text-xs text-stone-500">Settlement decision history · marketplace finance reconciliation</p>
      </div>

      {/* ── Stats row ── */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {stats.map((s) => (
          <div key={s.label} className="surface p-4">
            <p className="section-label">{s.label}</p>
            <p className={`amount mt-1 text-lg tracking-tight sm:text-xl ${s.accent}`}>{s.value}</p>
          </div>
        ))}
      </div>

      {/* ── Decision history timeline ── */}
      <div>
        <h2 className="section-label mb-4">Decision History</h2>
        <ol className="relative">
          {data.decisions.map(({ decision, verification }, idx) => {
            const adj = decision.gross_amount - decision.final_amount;
            const isOk = verification.valid;

            return (
              <li key={decision.decision_id} className="relative flex gap-4 pb-6 last:pb-0">
                {/* Timeline node */}
                <div className="flex w-20 shrink-0 flex-col items-center pt-1">
                  <span className="tabular mono text-[10px] text-stone-500">{formatDate(decision.approved_at)}</span>
                  <span className={`mt-1 flex h-6 w-6 items-center justify-center rounded-full border-2 font-mono text-[10px] font-bold ${
                    isOk ? 'border-[#4ADE80] bg-emerald-500/10 text-emerald-600' : 'border-[#F87171] bg-red-500/10 text-red-600'
                  }`}>
                    {isOk ? '✓' : '✗'}
                  </span>
                  {idx < data.decisions.length - 1 && (
                    <div className={`mt-1 w-0.5 flex-1 rounded-full ${isOk ? 'bg-emerald-500/15' : 'bg-red-500/15'}`} />
                  )}
                </div>

                {/* Decision card */}
                <div className="min-w-0 flex-1 rounded-xl border border-[var(--border)] bg-white/60 p-4 card-smooth hover:border-purple-200">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="min-w-0 space-y-1.5">
                      <div className="flex flex-wrap items-center gap-2">
                        <Link to={`/decisions/${decision.decision_id}`} className="font-mono text-xs font-semibold text-stone-800 transition-colors hover:text-purple-600">{decision.decision_id}</Link>
                        <span className={`inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide ${isOk ? 'bg-emerald-500/10 text-emerald-600' : 'bg-red-500/10 text-red-600'}`}>
                          <span className={`h-1 w-1 rounded-full ${isOk ? 'bg-emerald-500' : 'bg-red-500'}`} />
                          {isOk ? 'Verified' : 'Compromised'}
                        </span>
                      </div>
                      <div className="flex flex-wrap items-center gap-x-3 text-[11px] text-stone-600">
                        <span className="amount">{formatINR(decision.gross_amount)} → <span className="font-semibold text-stone-800">{formatINR(decision.final_amount)}</span></span>
                        <span className="amount text-red-600">−{formatINR(adj)}</span>
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-3">
                      <Link to={`/decisions/${decision.decision_id}/defense`} className="text-[11px] font-medium text-stone-600 hover:text-purple-600 transition-colors">Defense →</Link>
                      <Link to={`/decisions/${decision.decision_id}`} className="text-[11px] font-semibold text-purple-600 hover:underline">View →</Link>
                    </div>
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
      </div>
    </div>
  );
}
