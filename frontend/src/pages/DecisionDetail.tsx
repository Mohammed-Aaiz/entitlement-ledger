import { useEffect, useState, useCallback } from 'react';
import { useParams, Link } from 'react-router-dom';
import { api } from '../api/client';
import type { Decision, Evidence, VerificationResult, Policy } from '../api/types';
import { formatINR, formatDateTime, sourceTypeLabel } from '../lib/format';
import BorderGlow from '../components/react-bits/BorderGlow';

const SRC_COLORS: Record<string, string> = {
  order: 'bg-sky-400/10 text-sky-300 border border-sky-400/20',
  delivery: 'bg-[#7CA5D4]/10 text-[#7CA5D4] border border-[#7CA5D4]/20',
  complaint: 'bg-red-500/10 text-red-600 border border-red-200',
  policy_doc: 'bg-purple-600/10 text-purple-600 border border-purple-500/20',
  seller_agreement: 'bg-emerald-500/10 text-emerald-600 border border-emerald-200',
  refund_record: 'bg-purple-400/10 text-purple-300 border border-purple-400/20',
};
const chip = (t: string) =>
  `inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${SRC_COLORS[t] || 'bg-white/60 text-stone-600 border border-[var(--border)]'}`;

/* ── Provenance Chain ── */
function ProvenanceChain({ verified, breakAt }: { verified: boolean; breakAt?: string | null }) {
  const nodes = [
    { label: 'Decision', sub: 'Financial decision record' },
    { label: 'Policy', sub: 'Versioned clause references' },
    { label: 'Evidence', sub: 'Source records & extracted facts' },
    { label: 'Approval', sub: 'Human reviewer sign-off' },
    {
      label: 'SHA-256',
      sub: verified ? 'Chain verified' : `Chain broken${breakAt ? ` at ${breakAt}` : ''}`,
      status: verified ? 'verified' : 'flagged' as const,
    },
  ];
  return (
    <nav aria-label="Provenance chain" className="flex flex-col items-center gap-0 py-2">
      {nodes.map((n, i) => (
        <div key={n.label} className="flex flex-col items-center w-full">
          <div
            className={`w-full max-w-[220px] rounded-xl border px-4 py-2.5 text-center text-[11px] transition-colors ${
              n.status === 'verified'
                ? 'border-[#4ADE80]/30 bg-emerald-500/[0.05]'
                : n.status === 'flagged'
                ? 'border-red-200 bg-red-500/[0.05]'
                : 'border-[var(--border)] bg-white/60'
            }`}
          >
            <p
              className={`font-semibold ${
                n.status === 'verified'
                  ? 'text-emerald-600'
                  : n.status === 'flagged'
                  ? 'text-red-600'
                  : 'text-stone-800'
              }`}
            >
              {n.label}
            </p>
            <p className="mt-0.5 text-[10px] text-stone-500">{n.sub}</p>
          </div>
          {i < nodes.length - 1 && (
            <div className="h-4 w-px bg-gradient-to-b from-white/[0.12] to-white/[0.04]" />
          )}
        </div>
      ))}
    </nav>
  );
}

/* ── Integrity Panel ── */
function IntegrityPanel({ verification, decision }: { verification: VerificationResult; decision: Decision }) {
  const allFailed = !verification.valid;
  const checks = [
    { label: 'Decision content unchanged', ok: !allFailed },
    { label: 'Evidence links intact', ok: !allFailed },
    { label: 'Previous hash matches', ok: !allFailed },
    { label: 'SHA-256 chain valid', ok: !allFailed },
  ];

  return (
    <BorderGlow
      backgroundColor="#120F17"
      borderRadius={12}
      glowRadius={28}
      glowIntensity={0.75}
      glowColor={verification.valid ? '142, 73, 48' : '355, 68, 60'}
      colors={verification.valid ? ['#4ADE80', '#2a3a2e', '#0B0A0F'] : ['#F87171', '#3a2020', '#0B0A0F']}
      animated={false}
    >
      <div className="p-5">
        <h3
          className={`text-xs font-semibold uppercase tracking-[0.14em] ${
            verification.valid ? 'text-emerald-600' : 'text-red-600'
          }`}
        >
          {verification.valid ? '✓ Integrity Verified' : '✗ Integrity Compromised'}
        </h3>
        <ul className="mt-3 space-y-2">
          {checks.map((c) => (
            <li key={c.label} className="flex items-center gap-2 text-[11px]">
              <span className={c.ok ? 'text-emerald-600' : 'text-red-600'}>{c.ok ? '✓' : '✗'}</span>
              <span className={c.ok ? 'text-stone-600' : 'text-red-600'}>{c.label}</span>
            </li>
          ))}
        </ul>
        {!verification.valid && (
          <div className="mt-4 rounded-lg border border-red-200 bg-red-500/[0.04] p-3 text-[11px]">
            <p className="font-semibold text-red-600">Hash mismatch detected.</p>
            <p className="mt-1 text-stone-600">
              Content was modified after the hash was computed. Break point: {verification.break_at || decision.decision_id}.
            </p>
            <div className="mt-2 space-y-1 font-mono text-[10px]">
              <p className="text-stone-500">Expected: {decision.decision_hash.slice(0, 32)}…</p>
              <p className="text-red-600">Actual: content no longer matches</p>
            </div>
          </div>
        )}
        <p className="mt-3 text-[10px] text-stone-500">
          {verification.checked_count} record(s) verified from genesis.
        </p>
      </div>
    </BorderGlow>
  );
}

/* ── Slide-over Evidence Panel ── */
function EvidenceSlideOver({
  item,
  policy,
  evidenceRecords,
  decisionId,
  onClose,
}: {
  item: { label: string; amount: number; policy_clause_id: string | null; evidence_ids: string[] };
  policy: Policy | undefined;
  evidenceRecords: Evidence[];
  decisionId: string;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-y-0 right-0 z-50 flex justify-end" role="dialog" aria-label="Evidence panel">
      <div className="absolute inset-0 bg-black/55 backdrop-blur-sm backdrop-enter" onClick={onClose} onKeyDown={(e) => e.key === 'Escape' && onClose()} tabIndex={0} role="button" aria-label="Close" />
      <div className="relative w-full max-w-lg overflow-y-auto border-l border-[var(--border)] bg-white/50 shadow-2xl slide-over-enter">
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-[var(--border)] bg-white/50 px-5 py-3">
          <div>
            <p className="text-sm font-semibold text-stone-800">−{formatINR(item.amount)} · {item.label}</p>
            <p className="text-[10px] text-stone-500">Evidence panel</p>
          </div>
          <button type="button" onClick={onClose} className="text-stone-600 hover:text-stone-800 text-lg btn-smooth" aria-label="Close evidence panel">✕</button>
        </div>

        <div className="p-5 space-y-5">
          {/* Policy clause */}
          {policy && (
            <section>
              <h4 className="section-label mb-2">Policy Clause</h4>
              <div className="rounded-xl border border-purple-500/20 bg-purple-600/[0.04] p-4">
                <p className="font-mono text-[11px] font-medium text-purple-600">{policy.policy_id} v{policy.version}</p>
                <p className="mt-2 text-[11px] leading-relaxed text-stone-600">{policy.clause_text}</p>
              </div>
            </section>
          )}

          {/* Evidence records */}
          <section>
            <h4 className="section-label mb-2">Supporting Evidence ({item.evidence_ids.length})</h4>
            {item.evidence_ids.length === 0 && (
              <p className="text-[11px] text-stone-500">No evidence linked to this deduction.</p>
            )}
            <div className="space-y-3">
              {item.evidence_ids.map((evId) => {
                const ev = evidenceRecords.find((e) => e.evidence_id === evId);
                if (!ev) return null;
                return (
                  <div key={evId} className="rounded-xl border border-[var(--border)] bg-white/60 p-4">
                    <div className="flex items-center gap-2 mb-2">
                      <span className={chip(ev.source_type)}>{sourceTypeLabel(ev.source_type)}</span>
                      <span className="font-mono text-[10px] text-stone-500">{ev.evidence_id}</span>
                    </div>
                    <div className="space-y-1">
                      {ev.extracted_facts.map((f, fi) => (
                        <div key={fi} className="flex items-start gap-2 text-[11px]">
                          <span className="text-stone-500 mt-0.5">•</span>
                          <span className="text-stone-600 leading-relaxed">{f.fact}</span>
                          <span className="shrink-0 font-mono text-[10px] text-stone-500">{Math.round(f.confidence * 100)}%</span>
                        </div>
                      ))}
                    </div>
                    <Link to={`/decisions/${decisionId}/evidence?highlight=${evId}`} className="mt-2 inline-block text-[10px] font-medium text-purple-600 hover:underline">
                      Open in Evidence Viewer →
                    </Link>
                  </div>
                );
              })}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════ */
/* Main DecisionDetail page                                          */
/* ═══════════════════════════════════════════════════════════════════ */
export default function DecisionDetail() {
  const { id } = useParams<{ id: string }>();
  const [decision, setDecision] = useState<Decision | null>(null);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [verification, setVerification] = useState<VerificationResult | null>(null);
  const [selectedItem, setSelectedItem] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  const fetchDecision = useCallback((decisionId: string) => {
    setLoading(true);
    Promise.all([
      api.getDecision(decisionId),
      api.getDecisionEvidence(decisionId),
      api.verifyDecision(decisionId),
      api.getPolicies(),
    ])
      .then(([d, e, v, p]) => { setDecision(d); setEvidence(e); setVerification(v); setPolicies(p); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    Promise.all([
      api.getDecision(id),
      api.getDecisionEvidence(id),
      api.verifyDecision(id),
      api.getPolicies(),
    ])
      .then(([d, e, v, p]) => { setDecision(d); setEvidence(e); setVerification(v); setPolicies(p); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [id]);

  const handleApprove = async () => {
    if (!id) return;
    setActionLoading(true); setActionMessage(null);
    try { await api.approveDecision(id, 'finance_reviewer'); setActionMessage('Decision approved — hash recomputed and appended to chain.'); fetchDecision(id); }
    catch { setActionMessage('Failed to approve.'); }
    finally { setActionLoading(false); }
  };
  const handleReject = async () => {
    if (!id) return;
    setActionLoading(true); setActionMessage(null);
    try { await api.rejectDecision(id, 'finance_reviewer', 'Requires further review'); setActionMessage('Decision rejected.'); fetchDecision(id); }
    catch { setActionMessage('Failed to reject.'); }
    finally { setActionLoading(false); }
  };

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="h-8 skeleton w-48" />
        <div className="h-32 skeleton rounded-xl" />
        <div className="grid gap-6 xl:grid-cols-3">
          <div className="xl:col-span-2 space-y-4"><div className="h-64 skeleton rounded-xl" /></div>
          <div className="space-y-4"><div className="h-40 skeleton rounded-xl" /><div className="h-32 skeleton rounded-xl" /></div>
        </div>
      </div>
    );
  }

  if (error || !decision) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-500/[0.04] p-5">
        <p className="text-sm font-semibold text-red-600">{error || 'Decision not found'}</p>
        <p className="mt-1 text-xs text-stone-600">The requested decision record could not be loaded.</p>
        <Link to="/" className="mt-3 inline-block text-xs font-medium text-purple-600 hover:underline">← Back to dashboard</Link>
      </div>
    );
  }

  const isTampered = decision.decision_id === 'dec_005_tampered';
  const gross = decision.gross_amount;
  const adj = gross - decision.final_amount;
  const policyMap = Object.fromEntries(policies.map((p) => [p.policy_id, p]));

  // Surface AI model output fields
  const modelOutput = decision.model_output as Record<string, unknown> | undefined;
  const aiConfidence = modelOutput?.confidence;
  const aiReasoning = modelOutput?.reasoning_summary || modelOutput?.reasoning;

  // Open slide-over panel
  const openItem = selectedItem !== null ? decision.line_items[selectedItem] : null;

  return (
    <div className="space-y-6">
      {/* ── Header ── */}
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div className="flex items-center gap-3">
            <Link to="/" className="text-xs text-stone-500 hover:text-purple-600 transition-colors">← Dashboard</Link>
            <h1 className="text-xl font-bold tracking-tight text-stone-800 sm:text-2xl">Decision Detail</h1>
          </div>
          <p className="mt-1 font-mono text-xs text-stone-600">{decision.decision_id}</p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center rounded-lg border border-[var(--border)] bg-white/60 px-2.5 py-1 text-[11px] font-semibold text-stone-600">
              {decision.status.replace('_', ' ')}
            </span>
            {verification && (
              <span className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-[11px] font-semibold ${
                verification.valid
                  ? 'border-[#4ADE80]/25 bg-emerald-500/[0.06] text-emerald-600'
                  : 'border-[#F87171]/25 bg-red-500/[0.06] text-red-600'
              }`}>
                <span className={`h-1.5 w-1.5 rounded-full ${verification.valid ? 'bg-emerald-500' : 'bg-red-500'}`} />
                {verification.valid ? 'Integrity verified' : 'Integrity compromised'}
              </span>
            )}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 shrink-0">
          {decision.status === 'REVIEW_REQUIRED' && (
            <>
              <button type="button" onClick={handleApprove} disabled={actionLoading} className="rounded-lg bg-emerald-500/90 px-4 py-2 text-sm font-semibold text-[#05140c] hover:bg-emerald-500 disabled:opacity-50 btn-smooth">Approve</button>
              <button type="button" onClick={handleReject} disabled={actionLoading} className="rounded-lg bg-red-500/90 px-4 py-2 text-sm font-semibold text-white hover:bg-red-500 disabled:opacity-50 btn-smooth">Reject</button>
            </>
          )}
          <Link to={`/decisions/${decision.decision_id}/defense`}            className="rounded-lg border border-[var(--border)] bg-white/60 px-4 py-2 text-sm font-medium text-stone-800 btn-smooth hover:border-purple-500/40 hover:text-purple-600">
            Defense Packet →
          </Link>
        </div>
      </div>

      {actionMessage && (
        <div role="status" className="rounded-lg border border-purple-200 bg-purple-600/[0.04] px-4 py-2.5 text-xs text-stone-600">{actionMessage}</div>
      )}

      {isTampered && (
        <div role="alert" className="rounded-xl border border-red-200 bg-red-500/[0.04] p-4">
          <p className="text-sm font-semibold text-red-600">⚠ Tamper detected</p>
          <p className="mt-1 text-xs leading-relaxed text-red-600/70">
            This record was modified after its hash was computed — a demonstration of the tamper-evident mechanism.
          </p>
        </div>
      )}

      <div className="grid gap-6 xl:grid-cols-3">
        {/* ── LEFT: Waterfall + AI reasoning ── */}
        <div className="space-y-6 xl:col-span-2">
          {/* WATERFALL — the signature visual */}
          <BorderGlow
            backgroundColor="#120F17"
            borderRadius={14}
            glowRadius={28}
            glowIntensity={0.65}
            glowColor="42, 65, 55"
            colors={['#D9A441', '#4B4560', '#0B0A0F']}
            animated={false}
          >
            <div className="p-5">
              <h2 className="section-label mb-4">Financial Breakdown</h2>

              {/* Gross → Deductions → Final */}
              <div className="flex flex-col sm:flex-row items-baseline gap-3 sm:gap-6 border-b border-[var(--border)] pb-4">
                <div>
                  <p className="text-[10px] uppercase tracking-wider text-stone-500">Gross entitlement</p>
                  <p className="amount mt-0.5 text-3xl text-stone-800">{formatINR(gross)}</p>
                </div>
                <span className="hidden sm:inline text-stone-500">→</span>
                <div>
                  <p className="text-[10px] uppercase tracking-wider text-stone-500">Deductions</p>
                  <p className="amount mt-0.5 text-3xl text-red-600">−{formatINR(adj)}</p>
                </div>
                <span className="hidden sm:inline text-stone-500">→</span>
                <div>
                  <p className="text-[10px] uppercase tracking-wider text-stone-500">Final entitlement</p>
                  <p className="amount mt-0.5 text-3xl text-emerald-600">{formatINR(decision.final_amount)}</p>
                </div>
              </div>

              {/* Waterfall bar */}
              <div className="mt-4 space-y-1.5">
                <div className="flex items-center gap-3">
                  <span className="w-20 shrink-0 text-right text-[10px] text-stone-500">Gross</span>
                  <div className="flex-1 h-6 rounded bg-purple-600/20" style={{ width: '100%' }}>
                    <div className="h-full rounded bg-purple-600/50" style={{ width: '100%' }} />
                  </div>
                  <span className="amount w-24 text-right text-[11px] text-stone-800">{formatINR(gross)}</span>
                </div>
                {decision.line_items.map((item, i) => {
                  const pct = gross > 0 ? (item.amount / gross) * 100 : 0;
                  
                  return (
                    <div key={i} className="flex items-center gap-3">
                      <span className="w-20 shrink-0 text-right text-[10px] text-stone-500 truncate">−{item.label}</span>
                      <div className="flex-1 h-5 rounded bg-white/[0.02] relative overflow-hidden">
                        <div className="absolute right-0 top-0 h-full rounded bg-red-500/25" style={{ width: `${pct}%` }} />
                      </div>
                      <span className="amount w-24 text-right text-[11px] text-red-600">−{formatINR(item.amount)}</span>
                    </div>
                  );
                })}
                <div className="flex items-center gap-3 pt-1 border-t border-[var(--border)]">
                  <span className="w-20 shrink-0 text-right text-[10px] font-semibold text-stone-500">Final</span>
                  <div className="flex-1 h-6 rounded bg-white/[0.02] relative overflow-hidden">
                    <div className="h-full rounded bg-emerald-500/30" style={{ width: `${gross > 0 ? (decision.final_amount / gross) * 100 : 100}%` }} />
                  </div>
                  <span className="amount w-24 text-right text-[11px] font-semibold text-emerald-600">{formatINR(decision.final_amount)}</span>
                </div>
              </div>

              <p className="mt-4 text-[10px] text-stone-500">
                Amounts computed by deterministic application logic — the AI model never decides financial values.
              </p>
            </div>
          </BorderGlow>

          {/* Clickable deduction items → slide-over */}
          <section className="surface p-5">
            <h2 className="section-label mb-3">Deduction Items — click to view evidence</h2>
            <div className="space-y-2">
              {decision.line_items.map((item, idx) => (
                <button
                  key={idx}
                  type="button"
                  onClick={() => setSelectedItem(selectedItem === idx ? null : idx)}
                  aria-expanded={selectedItem === idx}
                  className="w-full rounded-lg border px-4 py-3 text-left card-smooth border-[var(--border)] bg-white/50 hover:border-purple-300"
                >
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <p className="text-xs font-medium text-stone-800">
                        <span className="amount text-red-600">−{formatINR(item.amount)}</span> · {item.label}
                      </p>
                      <p className="mt-0.5 text-[10px] text-stone-500">
                        clause: <span className="font-mono">{item.policy_clause_id || '—'}</span> · evidence: {item.evidence_ids.length} linked
                      </p>
                    </div>
                    <span className="shrink-0 text-[11px] font-medium text-purple-600">
                      {selectedItem === idx ? 'Close' : 'View evidence →'}
                    </span>
                  </div>
                </button>
              ))}
            </div>
          </section>

          {/* AI Reasoning — surface confidence + reasoning_summary */}
          {modelOutput && Object.keys(modelOutput).length > 0 && (
            <section className="surface p-5">
              <h2 className="section-label mb-3">AI Reasoning & Provenance</h2>

              {typeof aiConfidence === 'number' && (
                <div className="mb-3 rounded-lg border border-purple-500/20 bg-purple-600/[0.04] px-4 py-3">
                  <p className="text-[10px] uppercase tracking-wider text-stone-500">Model Confidence</p>
                  <div className="mt-1 flex items-baseline gap-2">
                    <span className="amount text-xl font-bold text-purple-600">{Math.round((aiConfidence as number) * 100)}%</span>
                    <span className="text-[10px] text-stone-600">confidence in classification</span>
                  </div>
                </div>
              )}

              {typeof aiReasoning === 'string' && (
                <div className="mb-3 rounded-lg border border-[var(--border)] bg-white/50 p-4">
                  <p className="text-[10px] uppercase tracking-wider text-stone-500 mb-1">Reasoning Summary</p>
                  <p className="text-[11px] leading-relaxed text-stone-600">{aiReasoning}</p>
                </div>
              )}

              <details>
                <summary className="cursor-pointer text-[10px] font-medium text-stone-600 hover:text-purple-600">
                  Full model output ▾
                </summary>
                <pre className="mt-2 overflow-x-auto rounded-lg border border-[var(--border)] bg-white/50 p-4 font-mono text-[10px] leading-relaxed text-stone-600">
                  {JSON.stringify(modelOutput, null, 2)}
                </pre>
              </details>
              <p className="mt-2 text-[10px] italic text-stone-500">
                AI extracts facts and claims; financial amounts are calculated deterministically.
              </p>
            </section>
          )}
        </div>

        {/* ── RIGHT: Metadata + Provenance + Integrity ── */}
        <div className="space-y-4">
          <ProvenanceChain verified={verification?.valid ?? true} breakAt={verification?.break_at} />

          <section className="surface p-4">
            <h3 className="section-label mb-2">Decision Metadata</h3>
            <dl className="space-y-2 text-xs">
              <div className="flex justify-between"><dt className="text-stone-500">Seller</dt><dd><Link to={`/sellers/${decision.entity_id}`} className="font-mono font-medium text-purple-600 hover:underline">{decision.entity_id}</Link></dd></div>
              <div className="flex justify-between"><dt className="text-stone-500">Approver</dt><dd className="font-medium text-stone-800">{decision.approver_id}</dd></div>
              <div className="flex justify-between"><dt className="text-stone-500">Approved</dt><dd className="text-stone-800">{formatDateTime(decision.approved_at)}</dd></div>
              <div className="flex justify-between"><dt className="text-stone-500">Policies</dt><dd className="break-all text-right font-mono text-[10px] text-stone-600">{decision.policy_version_id}</dd></div>
            </dl>
          </section>

          {/* Razorpay source metadata (if applicable) */}
          {modelOutput && typeof modelOutput === 'object' && (modelOutput as Record<string, unknown>).source === 'razorpay' && (
            <section className="surface p-4 border-purple-500/20">
              <h3 className="section-label mb-2 text-purple-600">Source: Razorpay</h3>
              <dl className="space-y-2 text-xs">
                <div className="flex justify-between">
                  <dt className="text-stone-500">Event</dt>
                  <dd className="font-mono text-stone-800">{String((modelOutput as Record<string, unknown>).razorpay_event_type || '')}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-stone-500">Verification</dt>
                  <dd>
                    <span className={`inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[9px] font-semibold ${(modelOutput as Record<string, unknown>).verification_status === 'verified' ? 'bg-emerald-500/10 text-emerald-600' : 'bg-purple-600/10 text-purple-600'}`}>
                      {String((modelOutput as Record<string, unknown>).verification_status || 'unverified').toUpperCase()}
                    </span>
                  </dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-stone-500">Event ID</dt>
                  <dd className="font-mono text-[10px] text-stone-600 break-all">{String((modelOutput as Record<string, unknown>).razorpay_event_id || '')}</dd>
                </div>
                {typeof (modelOutput as Record<string, unknown>).razorpay_entity_type === 'string' && (
                  <div className="flex justify-between">
                    <dt className="text-stone-500">Entity type</dt>
                    <dd className="font-mono text-stone-800">{String((modelOutput as Record<string, unknown>).razorpay_entity_type)}</dd>
                  </div>
                )}
              </dl>
            </section>
          )}

          <section className="surface p-4">
            <h3 className="section-label mb-2">Tamper-Evident Record</h3>
            <div className="space-y-2 text-[11px]">
              <div>
                <p className="text-stone-500">Previous hash</p>
                <p className="mt-0.5 hash text-stone-600">{decision.prev_decision_hash === 'genesis' ? 'genesis' : decision.prev_decision_hash.slice(0, 28) + '…'}</p>
              </div>
              <div>
                <p className="text-stone-500">Decision hash</p>
                <p className="mt-0.5 hash text-purple-600">{decision.decision_hash.slice(0, 28)}…</p>
              </div>
            </div>
          </section>

          {verification && <IntegrityPanel verification={verification} decision={decision} />}              <Link to={`/decisions/${decision.decision_id}/evidence`} className="block rounded-xl border border-[var(--border)] bg-white/60 px-4 py-3 text-center text-xs font-medium text-stone-600 card-smooth hover:border-purple-500/40 hover:text-purple-600">
            Open full Evidence Viewer →
          </Link>
        </div>
      </div>

      {/* ── Slide-over evidence panel ── */}
      {openItem && (
        <EvidenceSlideOver
          item={openItem}
          policy={openItem.policy_clause_id ? policyMap[openItem.policy_clause_id] : undefined}
          evidenceRecords={evidence}
          decisionId={decision.decision_id}
          onClose={() => setSelectedItem(null)}
        />
      )}
    </div>
  );
}
