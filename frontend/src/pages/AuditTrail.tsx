import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import type { Decision, VerificationResult } from '../api/types';
import { formatINR, formatDateTime } from '../lib/format';
import BorderGlow from '../components/react-bits/BorderGlow';
import { SkeletonTable } from '../App';

function formatTime(iso: string) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  } catch { return ''; }
}

export default function AuditTrail() {
  const [decisions, setDecisions] = useState<Decision[]>([]);
  
  const [chainResult, setChainResult] = useState<VerificationResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [verifying, setVerifying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getDecisions()
      .then((result) => {
        setDecisions(result.items);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const handleVerify = async () => {
    setVerifying(true);
    try { const r = await api.verifyAll(); setChainResult(r); }
    catch (e) { setError(e instanceof Error ? e.message : 'Verification failed'); }
    finally { setVerifying(false); }
  };

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="h-8 skeleton w-48" />
        <div className="h-16 skeleton rounded-xl" />
        <SkeletonTable rows={5} />
      </div>
    );
  }
  if (error) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-500/[0.04] p-5">
        <p className="text-sm font-semibold text-red-600">Audit trail unavailable</p>
        <p className="mt-1 text-xs text-stone-600">{error}</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* ── Header ── */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-stone-800 sm:text-2xl">Audit Trail</h1>
          <p className="mt-1 max-w-xl text-sm leading-relaxed text-stone-600">
            Tamper-evident settlement records in hash-chain order. Each decision cryptographically references its
            predecessor — any post-hoc modification breaks verification.
          </p>
        </div>
        <button
          type="button" onClick={handleVerify} disabled={verifying}
          className="shrink-0 rounded-lg bg-purple-600 px-4 py-2 text-sm font-semibold text-[#0B0A0F] btn-smooth hover:bg-purple-700 disabled:opacity-50"
        >
          {verifying ? 'Verifying…' : 'Verify Chain'}
        </button>
      </div>

      {/* ── Chain verification result — the demo moment ── */}
      {chainResult && (
        <BorderGlow
          backgroundColor="#120F17"
          borderRadius={14}
          glowRadius={32}
          glowIntensity={0.85}
          glowColor={chainResult.valid ? '142, 73, 48' : '355, 68, 60'}
          colors={chainResult.valid ? ['#4ADE80', '#2a3a2e', '#0B0A0F'] : ['#F87171', '#3a2020', '#0B0A0F']}
          animated={false}
        >
          <div className="p-5">
            <div className="flex items-center gap-3">
              <span className={`inline-flex h-10 w-10 items-center justify-center rounded-full text-lg font-bold verify-transition ${
                chainResult.valid
                  ? 'bg-[#4ADE80]/15 text-[#4ADE80]'
                  : 'bg-[#F87171]/15 text-[#F87171]'
              }`}>
                {chainResult.valid ? '✓' : '✗'}
              </span>
              <div>
                <p className={`text-sm font-semibold verify-transition ${chainResult.valid ? 'text-[#4ADE80]' : 'text-[#F87171]'}`}>
                  {chainResult.valid ? 'Integrity Verified' : 'Integrity Compromised'}
                </p>
                <p className="mt-0.5 text-xs text-stone-300">
                  Checked {chainResult.checked_count} record(s) from genesis.
                  {chainResult.valid
                    ? ' Every record matches its computed hash.'
                    : ` Chain integrity breaks at ${chainResult.break_at}.`}
                </p>
              </div>
            </div>
          </div>
        </BorderGlow>
      )}

      {/* ── Hash chain timeline — literal linked nodes ── */}
      <div className="surface p-4 sm:p-6">
        <h2 className="section-label mb-5">Decision Chain</h2>
        <ol className="relative">
          {decisions.map((d, idx) => {
            const isTampered = d.decision_id === 'dec_005_tampered';
            const adj = d.gross_amount - d.final_amount;

            return (
              <li key={d.decision_id} className="relative flex gap-4 pb-6 last:pb-0">
                {/* Timeline rail */}
                <div className="flex w-28 shrink-0 flex-col items-center pt-1">
                  <span className="tabular mono text-[11px] text-stone-500">{formatTime(d.approved_at)}</span>
                  {/* Node */}
                  <span
                    className={`mt-1 flex h-7 w-7 items-center justify-center rounded-full border-2 font-mono text-[10px] font-bold transition-colors ${
                      isTampered
                        ? 'border-[#F87171] bg-red-500/10 text-red-600'
                        : 'border-[#4ADE80] bg-emerald-500/10 text-emerald-600'
                    }`}
                  >
                    {isTampered ? '✗' : idx + 1}
                  </span>
                  {/* Connector line */}
                  {idx < decisions.length - 1 && (
                    <div className={`mt-1 w-0.5 flex-1 rounded-full ${
                      isTampered ? 'bg-red-500/20' : 'bg-emerald-500/15'
                    }`} />
                  )}
                </div>

                {/* Decision card */}                  <Link
                    to={`/decisions/${d.decision_id}`}
                  className={`min-w-0 flex-1 rounded-xl border p-4 card-smooth hover:border-purple-300 ${
                    isTampered
                      ? 'border-[#F87171]/25 bg-red-500/[0.02]'
                      : 'border-[var(--border)] bg-white/60'
                  }`}
                >
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div className="min-w-0 space-y-1.5">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-xs font-semibold text-stone-800">{d.decision_id}</span>
                        <span className={`inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide ${
                          isTampered ? 'bg-red-500/10 text-red-600' : 'bg-emerald-500/10 text-emerald-600'
                        }`}>
                          <span className={`h-1 w-1 rounded-full ${isTampered ? 'bg-red-500' : 'bg-emerald-500'}`} />
                          {isTampered ? 'Compromised' : 'Verified'}
                        </span>
                        <span className="rounded-full bg-white/60 px-1.5 py-0.5 text-[9px] font-medium uppercase text-stone-600">
                          {d.status.replace('_', ' ')}
                        </span>
                      </div>
                      <div className="flex flex-wrap items-center gap-x-3 text-[11px] text-stone-600">
                        <span className="font-mono">{d.entity_id}</span>
                        <span className="amount">{formatINR(d.gross_amount)} → <span className="font-semibold text-stone-800">{formatINR(d.final_amount)}</span></span>
                        <span className="text-red-600">−{formatINR(adj)}</span>
                      </div>
                      <p className="text-[10px] text-stone-500">Approved by {d.approver_id} · {formatDateTime(d.approved_at)}</p>
                    </div>
                    <div className="shrink-0 text-left sm:text-right">
                      <p className="section-label">Hash</p>
                      <p className="mt-0.5 hash text-purple-600">{d.decision_hash.slice(0, 18)}…</p>
                      <p className="hash text-stone-500">prev: {d.prev_decision_hash === 'genesis' ? 'genesis' : d.prev_decision_hash.slice(0, 12) + '…'}</p>
                    </div>
                  </div>
                </Link>
              </li>
            );
          })}
        </ol>
      </div>

      {/* ── How verification works ── */}
      <details className="surface group px-5 py-4">
        <summary className="cursor-pointer list-none text-xs font-semibold uppercase tracking-[0.14em] text-stone-600 transition-colors group-hover:text-purple-600">
          How hash-chain verification works ▾
        </summary>
        <div className="mt-3 grid gap-4 text-xs leading-relaxed text-stone-600 sm:grid-cols-3">
          <div className="rounded-lg border border-[var(--border)] bg-white/50 p-3">
            <p className="mb-1 font-semibold text-stone-800">1 · Record hashing</p>
            Every decision's full content is hashed — amount, clauses, approver. The hash is a unique fingerprint.
          </div>
          <div className="rounded-lg border border-[var(--border)] bg-white/50 p-3">
            <p className="mb-1 font-semibold text-stone-800">2 · Chaining</p>
            Each new decision embeds the previous decision's hash — like a wax seal referencing the seal before it.
          </div>
          <div className="rounded-lg border border-[var(--border)] bg-white/50 p-3">
            <p className="mb-1 font-semibold text-stone-800">3 · Verification</p>
            Re-computing from genesis confirms nothing was altered. A single changed amount breaks every hash after it.
          </div>
        </div>
      </details>
    </div>
  );
}
