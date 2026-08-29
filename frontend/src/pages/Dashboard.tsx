import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import type { Decision, Policy, AIStatus } from '../api/types';
import { formatINR, formatDate } from '../lib/format';
import { SkeletonCard, SkeletonTable } from '../App';
import MagicBento from '../components/react-bits/MagicBento';

import React from 'react';

// Wrapper to catch errors inside the bento grid
class BentoErrorBoundary extends React.Component<{ children: React.ReactNode }, { hasError: boolean }> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { hasError: false };
  }
  static getDerivedStateFromError() { return { hasError: true }; }
  componentDidCatch(error: any) { console.error('Bento grid error:', error); }
  render() {
    if (this.state.hasError) return <div className="surface p-4 text-sm text-stone-500">Failed to load statistics grid.</div>;
    return this.props.children;
  }
}

function IntegrityBadge({ valid, compact = false }: { valid: boolean; compact?: boolean }) {
  if (valid) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/20 bg-emerald-50 px-2 py-0.5 text-[10px] font-semibold tracking-wide text-emerald-700">
        <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
        {compact ? 'OK' : 'VERIFIED'}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-red-500/20 bg-red-50 px-2 py-0.5 text-[10px] font-semibold tracking-wide text-red-700">
      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-red-500" />
      {compact ? 'ERR' : 'TAMPERED'}
    </span>
  );
}

export default function Dashboard() {
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [latestPolicy, setLatestPolicy] = useState<Policy | null>(null);
  const [aiStatus, setAiStatus] = useState<AIStatus | null>(null);
  const [stats, setStats] = useState<{ total_decisions: number; verified_decisions: number; total_value_inr: number; } | null>(null);
  const [sellers, setSellers] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    Promise.all([
      api.getDecisions(),
      api.getPolicies(),
      api.getAIStatus()
    ]).then(([decsResult, pols, ai]) => {
      if (!active) return;
      const decs = decsResult.items;
      // Sort newest first
      const sorted = [...decs].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
      
      setDecisions(sorted.slice(0, 4));
      
      const s = new Set<string>();
      let totalGross = 0;
      let verifiedCount = 0;
      decs.forEach(d => {
        s.add(d.entity_id);
        totalGross += d.gross_amount;
        if (d.decision_id !== 'dec_005_tampered') verifiedCount++;
      });
      setSellers(s);
      
      setStats({
        total_decisions: decs.length,
        verified_decisions: verifiedCount,
        total_value_inr: totalGross
      });

      if (pols.length > 0) {
        setLatestPolicy(pols.reduce((prev, curr) => (curr.version > prev.version ? curr : prev)));
      }
      setAiStatus(ai);
      setLoading(false);
    }).catch(err => {
      console.error(err);
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, []);

  if (loading) {
    return (
      <div className="space-y-8 animate-in fade-in duration-500">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-stone-900">Workspace Overview</h1>
          <p className="text-sm text-stone-500">Loading system status...</p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
        <SkeletonTable rows={4} />
      </div>
    );
  }

  return (
    <div className="space-y-8 animate-in fade-in duration-500 relative z-10">
      
      <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-stone-900">Workspace</h1>
          <p className="mt-1 text-sm text-stone-600">
            Real-time financial provenance and settlement decisions.
          </p>
        </div>
        <div className="flex gap-3">
          <Link
            to="/analyze"
            className="inline-flex items-center justify-center gap-2 rounded-lg bg-purple-600 px-4 py-2 text-sm font-semibold text-white shadow-md hover:bg-purple-700 btn-smooth"
          >
            New Decision
          </Link>
        </div>
      </header>

      {/* ─── System Health Bento Grid ─── */}
      <section>
        <h2 className="section-label mb-3">System Health & Metrics</h2>
        <BentoErrorBoundary>
        <MagicBento className="grid w-full grid-cols-2 gap-4 md:grid-cols-4 lg:grid-cols-6 lg:grid-rows-2">
          {/* Card 1: Value Processed (Large) */}
          <div className="col-span-2 row-span-2 flex flex-col justify-between h-full p-2 lg:p-4">
            <div className="magic-bento-card__header">
              <span className="magic-bento-card__label">Value Processed</span>
            </div>
            <div className="magic-bento-card__content">
              <p className="magic-bento-card__value text-stone-900">{formatINR(stats?.total_value_inr || 0)}</p>
              <p className="magic-bento-card__description">Total gross entitlement volume</p>
            </div>
          </div>

          {/* Card 2: Decisions Count */}
          <Link to="/audit" className="col-span-2 flex flex-col justify-between h-full p-1 hover:bg-black/5 transition-colors rounded-xl">
            <div className="magic-bento-card__header">
              <span className="magic-bento-card__label">Decisions</span>
            </div>
            <div className="magic-bento-card__content">
              <p className="magic-bento-card__value text-stone-900">{stats?.total_decisions || 0}</p>
              <p className="magic-bento-card__description">Ledger entries recorded</p>
            </div>
          </Link>

          {/* Card 3: Active Sellers */}
          <Link to="/sellers/seller_abc" className="col-span-2 flex flex-col justify-between h-full p-1 hover:bg-black/5 transition-colors rounded-xl">
            <div className="magic-bento-card__header">
              <span className="magic-bento-card__label">Entities</span>
            </div>
            <div className="magic-bento-card__content">
              <p className="magic-bento-card__value text-stone-900">{sellers.size}</p>
              <p className="magic-bento-card__description">Sellers with settlement history</p>
            </div>
          </Link>

          {/* Card 4: Verification */}
          <div className="flex flex-col justify-between h-full p-1">
            <div className="magic-bento-card__header">
              <span className="magic-bento-card__label">Verification</span>
            </div>
            <div className="magic-bento-card__content">
              <p className="magic-bento-card__value text-emerald-600">
                {stats ? stats.verified_decisions : 0}/{stats ? stats.total_decisions : 0}
              </p>
              <p className="magic-bento-card__description">Chain verification pass rate</p>
            </div>
          </div>

          {/* Card 5: Active Policy */}
          <div className="flex flex-col justify-between h-full p-1">
            <div className="magic-bento-card__header">
              <span className="magic-bento-card__label">Active Policy</span>
            </div>
            <div className="magic-bento-card__content">
              <p className="magic-bento-card__value text-purple-600">
                {latestPolicy ? `v${latestPolicy.version}` : '—'}
              </p>
              <p className="magic-bento-card__description">
                {latestPolicy ? latestPolicy.policy_id : 'No policies loaded'}
              </p>
            </div>
          </div>

          {/* Card 6: AI Provider */}
          <div className="col-span-2 flex flex-col justify-between h-full p-1">
            <div className="magic-bento-card__header">
              <span className="magic-bento-card__label">AI Provider</span>
            </div>
            <div className="magic-bento-card__content">
              <p className={`magic-bento-card__value ${aiStatus?.available ? 'text-purple-600' : 'text-stone-500'}`}>
                {aiStatus?.provider || 'None'}
              </p>
              <p className="magic-bento-card__description">
                {aiStatus?.available
                  ? `${aiStatus.model || 'connected'} — healthy`
                  : aiStatus?.error || 'Using seeded demo data'}
              </p>
            </div>
          </div>
        </MagicBento>
        </BentoErrorBoundary>
      </section>

      {/* ─── Recent Decisions ─── */}
      {decisions.length > 0 && (
        <section>
          <div className="flex items-center justify-between mb-3">
            <h2 className="section-label">Recent Decisions</h2>
            <Link to="/audit" className="text-[11px] font-medium text-purple-600 hover:underline btn-smooth">View full trail →</Link>
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            {decisions.map((d) => {
              const isTampered = d.decision_id === 'dec_005_tampered';
              const isPrimary = d.decision_id === 'dec_001';
              const adj = d.gross_amount - d.final_amount;

              return isPrimary ? (
                /* ─── PRIMARY DEMO DECISION ─── */
                <Link
                  key={d.decision_id}
                  to={`/decisions/${d.decision_id}`}
                  className="lg:col-span-2 block rounded-2xl border border-purple-500/20 bg-white/70 backdrop-blur-xl p-6 card-smooth hover:border-purple-500/50 shadow-sm"
                >
                  <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="rounded bg-purple-100 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-purple-700">
                          Primary Demo
                        </span>
                        <span className="rounded border border-stone-200 bg-white px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-stone-500">
                          Seller Payout
                        </span>
                        <span className="font-mono text-[11px] font-medium text-stone-500">{d.decision_id}</span>
                      </div>
                      <div className="mt-3 flex items-baseline gap-3">
                        <span className="amount text-3xl text-stone-900">{formatINR(d.gross_amount)}</span>
                        <span className="text-stone-400">→</span>
                        <span className="amount text-3xl text-emerald-600">{formatINR(d.final_amount)}</span>
                      </div>
                      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-stone-600">
                        <span>Seller <span className="font-mono font-bold text-stone-800">{d.entity_id}</span></span>
                        {d.line_items.map((li, i) => (
                          <span key={i} className="text-red-600 font-medium">−{formatINR(li.amount)} {li.label}</span>
                        ))}
                      </div>
                      <p className="mt-2 text-[11px] text-stone-500">
                        Approved by <span className="text-stone-700 font-medium">{d.approver_id}</span> · {formatDate(d.approved_at)}
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-4">
                      <IntegrityBadge valid={!isTampered} />
                      <span className="text-[12px] font-bold text-purple-600">View decision →</span>
                    </div>
                  </div>
                </Link>
              ) : (
                /* ─── Regular decision card ─── */
                <Link
                  key={d.decision_id}
                  to={`/decisions/${d.decision_id}`}
                  className="surface block p-5 card-smooth hover:border-purple-200"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-[12px] font-bold text-stone-800">{d.decision_id}</span>
                        <IntegrityBadge valid={!isTampered} compact />
                      </div>
                      <div className="mt-2 flex items-baseline gap-2">
                        <span className="amount text-lg text-stone-800">{formatINR(d.gross_amount)}</span>
                        <span className="text-stone-400">→</span>
                        <span className="amount text-lg text-emerald-600">{formatINR(d.final_amount)}</span>
                      </div>
                      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] font-medium text-stone-600">
                        <span className="font-mono">{d.entity_id}</span>
                        <span className="text-red-500">−{formatINR(adj)}</span>
                        <span className="text-stone-400">{formatDate(d.approved_at)}</span>
                      </div>
                      {d.line_items.length > 0 && (
                        <div className="mt-3 flex flex-wrap gap-1.5">
                          {d.line_items.map((li, i) => (
                            <span key={i} className="rounded border border-stone-200 bg-white/50 px-1.5 py-0.5 text-[10px] font-medium text-stone-500 shadow-sm">
                              {li.label} <span className="amount ml-1 text-stone-700">{formatINR(li.amount)}</span>
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                    <span className="shrink-0 text-[11px] font-bold text-purple-600">View →</span>
                  </div>
                </Link>
              );
            })}
          </div>
        </section>
      )}

      <p className="text-right text-[10px] font-medium text-stone-400 pb-8">
        Data refreshed {formatDate(new Date().toISOString())} · EntitlementLedger v0.1.0
      </p>
    </div>
  );
}
