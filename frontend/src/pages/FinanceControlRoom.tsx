import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import type { ReconciliationDashboard, ReconciliationCase, ReconciliationRun, Decision } from '../api/types';
import { formatINR, formatDateTime } from '../lib/format';
import { SkeletonTable } from '../App';

const CLASS_STYLES: Record<string, string> = {
  MATCHED: 'border border-emerald-300 bg-emerald-500/10 text-emerald-600',
  REVIEW_REQUIRED: 'border border-amber-300 bg-amber-500/10 text-amber-600',
  EXCEPTION: 'border border-red-300 bg-red-500/10 text-red-600',
};

const AI_BADGE: Record<string, { label: string; className: string }> = {
  available: { label: 'AI OK', className: 'bg-emerald-500/10 text-emerald-600' },
  unavailable: { label: 'AI UNAVAILABLE', className: 'bg-amber-500/10 text-amber-600' },
  failed: { label: 'AI FAILED', className: 'bg-red-500/10 text-red-600' },
  not_needed: { label: 'DETERMINISTIC', className: 'bg-stone-100 text-stone-500' },
  not_attempted: { label: 'AI NOT TRIED', className: 'bg-stone-100 text-stone-500' },
};

function StatCard({ label, value, sub, tone = 'default' }: { label: string; value: string; sub?: string; tone?: 'default' | 'good' | 'warn' | 'bad' }) {
  const tones: Record<string, string> = {
    default: 'text-stone-800',
    good: 'text-emerald-600',
    warn: 'text-amber-600',
    bad: 'text-red-600',
  };
  return (
    <div className="surface p-4">
      <p className="text-[10px] font-semibold uppercase tracking-wider text-stone-500">{label}</p>
      <p className={`amount mt-1.5 text-xl font-bold ${tones[tone]}`}>{value}</p>
      {sub && <p className="mt-0.5 text-[10px] text-stone-500">{sub}</p>}
    </div>
  );
}

function TraceRow({ label, sign, amount, running }: { label: string; sign: string; amount: number; running: number }) {
  return (
    <div className="flex items-center justify-between border-b border-[var(--border)] py-1.5 text-[11px] last:border-0">
      <span className="text-stone-600">{label}</span>
      <span className="flex items-center gap-4">
        <span className={`amount w-24 text-right font-medium ${sign === '-' ? 'text-red-600' : 'text-emerald-600'}`}>
          {sign === '-' ? '−' : sign === '+' ? '+' : ''}{formatINR(amount)}
        </span>
        <span className="amount w-28 text-right text-stone-800">= {formatINR(running)}</span>
      </span>
    </div>
  );
}

export default function FinanceControlRoom() {
  const [dash, setDash] = useState<ReconciliationDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [runMsg, setRunMsg] = useState<string | null>(null);
  const [runs, setRuns] = useState<ReconciliationRun[]>([]);
  const [selected, setSelected] = useState<ReconciliationCase | null>(null);
  const [decision, setDecision] = useState<Decision | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string>('');
  const navigate = useNavigate();

  const load = () => {
    Promise.all([api.getReconciliationDashboard(), api.getReconciliationRuns(10)])
      .then(([d, r]) => { setDash(d); setRuns(r.runs); if (!selectedRunId && d.latest_run) setSelectedRunId(d.latest_run.run_id); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };
  useEffect(() => { load(); }, []);

  const handleRunDemo = async () => {
    setRunning(true); setRunMsg(null);
    try {
      const run = await api.runDemoReconciliation(100);
      setRunMsg(`✓ Run ${run.run_id}: ${run.total_cases} cases — ${run.matched} matched, ${run.review_required} review, ${run.exceptions} exceptions (${(run.throughput_per_sec ?? 0).toFixed(0)}/sec)`);
      setSelectedRunId(run.run_id);
      load();
    } catch (e) {
      setRunMsg(`✗ ${e instanceof Error ? e.message : 'Run failed'}`);
    } finally { setRunning(false); }
  };

  const openCase = async (caseId: string) => {
    setDecision(null);
    try {
      const c = await api.getReconciliationCase(caseId);
      setSelected(c);
      if (c.decision_id) {
        api.getDecision(c.decision_id).then(setDecision).catch(() => setDecision(null));
      }
    } catch { /* keep old selection */ }
  };

  const run = dash?.latest_run ?? null;

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="h-8 skeleton w-64" />
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => <div key={i} className="h-20 skeleton rounded-xl" />)}
        </div>
        <SkeletonTable rows={5} />
      </div>
    );
  }
  if (error && !dash) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-500/[0.04] p-5">
        <p className="text-sm font-semibold text-red-600">Finance Control Room unavailable</p>
        <p className="mt-1 text-xs text-stone-600">{error}</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* ── Header ── */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-stone-800 sm:text-2xl">Finance Control Room</h1>
          <p className="mt-1 max-w-2xl text-sm leading-relaxed text-stone-600">
            What happened to your money — and why the controller decided what it decided.
            Deterministic engine computes every paisa; AI only interprets evidence.
          </p>
        </div>
        <button
          type="button"
          onClick={handleRunDemo}
          disabled={running}
          className="shrink-0 rounded-lg bg-purple-600 px-4 py-2 text-xs font-semibold text-[#0B0A0F] btn-smooth hover:bg-purple-700 disabled:opacity-50"
        >
          {running ? 'Reconciling 100 records…' : '▶ Run 100-record reconciliation'}
        </button>
      </div>

      {runMsg && <div role="status" className="rounded-lg border border-[var(--border)] bg-white/50 px-4 py-2.5 text-[11px] text-stone-600">{runMsg}</div>}

      {/* ── Latest run banner ── */}
      {run && (
        <div className="surface flex flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <span className="font-mono text-[11px] text-stone-600">{run.run_id}</span>
          <span className="text-[11px] text-stone-500">source: <span className="text-stone-700">{run.source}</span></span>
          <span className="text-[11px] text-stone-500">{formatDateTime(run.started_at)}</span>
          {run.duplicates_detected > 0 && (
            <span className="rounded-full bg-purple-500/10 px-2 py-0.5 text-[9px] font-semibold text-purple-600">
              {run.duplicates_detected} duplicate event(s) handled idempotently
            </span>
          )}
          {run.audit_completeness > 0 && (
            <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[9px] font-semibold text-emerald-600">
              audit {Math.round(run.audit_completeness * 100)}%
            </span>
          )}
        </div>
      )}

      {/* ── Key metrics ── */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="Records processed" value={run ? String(run.total_records) : '—'} sub={run ? `${run.total_cases} cases` : 'no runs yet'} />
        <StatCard label="Matched" value={run ? String(run.matched) : '—'} tone="good" sub={run ? `${(run.match_rate * 100).toFixed(1)}% match rate` : ''} />
        <StatCard label="Review required" value={run ? String(run.review_required) : '—'} tone="warn" />
        <StatCard label="Exceptions" value={run ? String(run.exceptions) : '—'} tone="bad" />
        <StatCard label="Calculation accuracy" value={run?.calculation_accuracy != null ? `${(run.calculation_accuracy * 100).toFixed(1)}%` : '—'} sub="benchmark-evaluated" />
        <StatCard label="False auto-resolve" value={run ? String(run.false_auto_resolve) : '—'} tone={run && run.false_auto_resolve > 0 ? 'bad' : 'good'} sub="must stay at 0" />
        <StatCard label="Throughput" value={run ? `${run.throughput_per_sec.toFixed(0)}/s` : '—'} sub="cases per second" />
        <StatCard label="P50 / P95 latency" value={run ? `${run.p50_latency_ms.toFixed(0)} / ${run.p95_latency_ms.toFixed(0)} ms` : '—'} />
      </div>

      {/* ── Run history ── */}
      {runs.length > 0 && (
        <div className="surface overflow-hidden">
          <div className="border-b border-[var(--border)] px-4 py-3">
            <h2 className="section-label">Reconciliation Runs</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[700px] text-sm">
              <thead>
                <tr className="border-b border-white/[0.04] text-left text-[10px] uppercase tracking-wider text-stone-500">
                  <th className="px-4 py-2.5 font-medium">Run</th>
                  <th className="px-4 py-2.5 font-medium">Source</th>
                  <th className="px-4 py-2.5 text-right font-medium">Cases</th>
                  <th className="px-4 py-2.5 text-right font-medium">Matched</th>
                  <th className="px-4 py-2.5 text-right font-medium">Review</th>
                  <th className="px-4 py-2.5 text-right font-medium">Exceptions</th>
                  <th className="px-4 py-2.5 text-right font-medium">Match rate</th>
                  <th className="px-4 py-2.5 font-medium">Started</th>
                  <th className="px-4 py-2.5 font-medium"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.04]">
                {runs.map((r) => (
                  <tr key={r.run_id} className={`row-hover ${selectedRunId === r.run_id ? 'bg-purple-500/[0.04]' : ''}`}>
                    <td className="px-4 py-3 font-mono text-[11px] text-stone-600">{r.run_id}</td>
                    <td className="px-4 py-3 text-[11px] text-stone-600">{r.source}</td>
                    <td className="amount px-4 py-3 text-right text-xs text-stone-800">{r.total_cases}</td>
                    <td className="amount px-4 py-3 text-right text-xs text-emerald-600">{r.matched}</td>
                    <td className="amount px-4 py-3 text-right text-xs text-amber-600">{r.review_required}</td>
                    <td className="amount px-4 py-3 text-right text-xs text-red-600">{r.exceptions}</td>
                    <td className="amount px-4 py-3 text-right text-xs text-stone-800">{(r.match_rate * 100).toFixed(1)}%</td>
                    <td className="amount px-4 py-3 text-[11px] text-stone-600">{formatDateTime(r.started_at)}</td>
                    <td className="px-4 py-3 text-right">
                      <button type="button" onClick={() => { setSelectedRunId(r.run_id); api.getRunExceptions(r.run_id).then(({ exceptions: ex }) => { if (ex.length) setSelected(ex[0]); }); }} className="text-[11px] font-medium text-purple-600 hover:underline">
                        Exceptions →
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── Exception queue ── */}
      <div className="surface overflow-hidden">
        <div className="border-b border-[var(--border)] px-4 py-3">
          <h2 className="section-label">
            Exception Queue <span className="ml-1 font-normal normal-case text-stone-500">({dash?.unresolved_exceptions.length ?? 0})</span>
          </h2>
        </div>
        {!dash || dash.unresolved_exceptions.length === 0 ? (
          <div className="px-4 py-12 text-center">
            <p className="text-sm text-emerald-600 font-medium">No unresolved exceptions.</p>
            <p className="mt-1 text-xs text-stone-500">Run a reconciliation to populate the queue.</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-sm">
              <thead>
                <tr className="border-b border-white/[0.04] text-left text-[10px] uppercase tracking-wider text-stone-500">
                  <th className="px-4 py-2.5 font-medium">Case</th>
                  <th className="px-4 py-2.5 font-medium">Payment</th>
                  <th className="px-4 py-2.5 font-medium">Exception</th>
                  <th className="px-4 py-2.5 font-medium">AI</th>
                  <th className="px-4 py-2.5 text-right font-medium">Variance</th>
                  <th className="px-4 py-2.5 font-medium">Explanation</th>
                  <th className="px-4 py-2.5 font-medium"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.04]">
                {dash.unresolved_exceptions.map((c) => {
                  const ai = AI_BADGE[c.ai_status] || AI_BADGE.not_attempted;
                  return (
                    <tr key={c.case_id} className={`row-hover ${selected?.case_id === c.case_id ? 'bg-purple-500/[0.04]' : ''}`}>
                      <td className="px-4 py-3 font-mono text-[11px] text-stone-600">{c.case_id}</td>
                      <td className="px-4 py-3 font-mono text-[11px] text-stone-700">{c.payment_id}</td>
                      <td className="px-4 py-3">
                        <div className="flex flex-wrap items-center gap-1">
                          {c.exception_codes.slice(0, 2).map((code) => (
                            <span key={code} className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${code === 'AMOUNT_MISMATCH' || code === 'DUPLICATE_PAYMENT' || code === 'DUPLICATE_SETTLEMENT' || code === 'REFUND_MISMATCH' || code === 'FEE_MISMATCH' || code === 'TAX_MISMATCH' || code === 'MISSING_PAYMENT' || code === 'INVALID_RECORD' || code === 'CONTRADICTORY_EVIDENCE' ? 'bg-red-500/10 text-red-600' : 'bg-amber-500/10 text-amber-600'}`}>
                              {code}
                            </span>
                          ))}
                          {c.exception_codes.length > 2 && <span className="text-[10px] text-stone-500">+{c.exception_codes.length - 2}</span>}
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`inline-flex items-center rounded-full px-1.5 py-0.5 text-[9px] font-semibold ${ai.className}`}>{ai.label}</span>
                      </td>
                      <td className={`amount px-4 py-3 text-right text-xs font-medium ${c.variance !== 0 ? 'text-red-600' : 'text-stone-700'}`}>
                        {c.variance !== 0 ? `${c.variance > 0 ? '+' : ''}${formatINR(c.variance)}` : '—'}
                      </td>
                      <td className="px-4 py-3 text-[11px] text-stone-600">{c.explanation.slice(0, 90)}{c.explanation.length > 90 ? '…' : ''}</td>
                      <td className="px-4 py-3 text-right">
                        <button type="button" onClick={() => openCase(c.case_id)} className="text-[11px] font-medium text-purple-600 hover:underline">
                          Investigate →
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── Case detail ── */}
      {selected && (
        <section className="surface overflow-hidden" aria-live="polite">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--border)] px-4 py-3">
            <div className="flex items-center gap-3">
              <h2 className="section-label">Case {selected.case_id}</h2>
              <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold ${CLASS_STYLES[selected.classification] || 'bg-stone-100 text-stone-600'}`}>
                {selected.classification}
              </span>
            </div>
            <span className="font-mono text-[11px] text-stone-500">{selected.payment_id}</span>
          </div>

          <div className="grid gap-0 md:grid-cols-2">
            {/* Left: deterministic numbers */}
            <div className="p-5">
              <h3 className="section-label mb-3">Deterministic Calculation</h3>
              {selected.calculation_trace?.steps && selected.calculation_trace.steps.length > 0 ? (
                <div>
                  {selected.calculation_trace.steps.map((s) => (
                    <TraceRow key={s.component} label={s.label} sign={s.sign} amount={s.amount} running={s.running_total} />
                  ))}
                  <div className="mt-2 flex items-center justify-between rounded-lg bg-stone-50 px-3 py-2 text-[12px]">
                    <span className="font-semibold text-stone-700">Expected settlement</span>
                    <span className="amount font-bold text-stone-900">{formatINR(selected.expected_amount)}</span>
                  </div>
                  <div className="mt-1 flex items-center justify-between px-3 py-1 text-[11px] text-stone-600">
                    <span>Actual settlement</span>
                    <span className="amount">{formatINR(selected.actual_amount)}</span>
                  </div>
                  <div className={`flex items-center justify-between px-3 py-1 text-[11px] font-medium ${selected.variance !== 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                    <span>Variance</span>
                    <span className="amount">{selected.variance !== 0 ? `${selected.variance > 0 ? '+' : ''}${formatINR(selected.variance)}` : '0 (reconciled)'}</span>
                  </div>
                </div>
              ) : (
                <p className="text-xs text-stone-500">No calculation possible — {selected.explanation}</p>
              )}

              <div className="mt-4 grid grid-cols-2 gap-3">
                <div className="rounded-lg border border-[var(--border)] bg-white/50 p-3">
                  <p className="text-[9px] font-semibold uppercase tracking-wider text-stone-500">Captured</p>
                  <p className="amount mt-1 text-sm font-semibold text-stone-800">{formatINR(selected.calculation_trace?.captured_amount ?? 0)}</p>
                </div>
                <div className="rounded-lg border border-[var(--border)] bg-white/50 p-3">
                  <p className="text-[9px] font-semibold uppercase tracking-wider text-stone-500">Refunds</p>
                  <p className="amount mt-1 text-sm font-semibold text-red-600">−{formatINR(selected.calculation_trace?.refund_total ?? 0)}</p>
                </div>
                <div className="rounded-lg border border-[var(--border)] bg-white/50 p-3">
                  <p className="text-[9px] font-semibold uppercase tracking-wider text-stone-500">Fees / Taxes</p>
                  <p className="amount mt-1 text-sm font-semibold text-stone-800">
                    −{formatINR(selected.calculation_trace?.fee_total ?? 0)} / −{formatINR(selected.calculation_trace?.tax_total ?? 0)}
                  </p>
                </div>
                <div className="rounded-lg border border-[var(--border)] bg-white/50 p-3">
                  <p className="text-[9px] font-semibold uppercase tracking-wider text-stone-500">Adjustments</p>
                  <p className="amount mt-1 text-sm font-semibold text-stone-800">
                    {selected.calculation_trace?.adjustments ? `${selected.calculation_trace.adjustments > 0 ? '+' : ''}${formatINR(selected.calculation_trace.adjustments)}` : '0'}
                  </p>
                </div>
              </div>
            </div>

            {/* Right: interpretation + evidence + ledger */}
            <div className="border-t border-[var(--border)] p-5 md:border-l md:border-t-0">
              <h3 className="section-label mb-2">AI Interpretation</h3>
              <div className="mb-1 flex items-center gap-2">
                <span className={`inline-flex items-center rounded-full px-1.5 py-0.5 text-[9px] font-semibold ${(AI_BADGE[selected.ai_status] || AI_BADGE.not_attempted).className}`}>
                  {(AI_BADGE[selected.ai_status] || AI_BADGE.not_attempted).label}
                </span>
                {selected.ai_confidence != null && (
                  <span className="text-[10px] text-stone-500">confidence {selected.ai_confidence.toFixed(2)}</span>
                )}
              </div>
              {selected.ai_technical_reason && (
                <p className="mt-1 rounded border border-amber-200 bg-amber-500/[0.05] px-2.5 py-1.5 text-[10px] text-amber-700">{selected.ai_technical_reason}</p>
              )}
              {selected.ai_interpretation?.evidence_summary ? (
                <p className="mt-2 text-xs leading-relaxed text-stone-700">{selected.ai_interpretation.evidence_summary as string}</p>
              ) : (
                <p className="mt-2 text-xs text-stone-500">No AI interpretation — deterministic reconciliation was sufficient.</p>
              )}
              {selected.ai_interpretation?.discrepancy_explanation ? (
                <p className="mt-2 text-[11px] text-stone-600">
                  <span className="font-medium text-stone-800">Discrepancy:</span> {String(selected.ai_interpretation.discrepancy_explanation)}
                </p>
              ) : null}

              <h3 className="section-label mb-2 mt-4">Structured Exceptions</h3>
              {selected.exceptions.length === 0 ? (
                <p className="text-xs text-stone-500">None.</p>
              ) : (
                <div className="space-y-2">
                  {selected.exceptions.map((exc, i) => (
                    <div key={i} className="rounded-lg border border-[var(--border)] bg-white/50 p-3 text-[11px]">
                      <div className="mb-0.5 flex items-center justify-between">
                        <span className="font-semibold text-red-600">{exc.code}</span>
                        {exc.financial_impact !== 0 && <span className="amount text-stone-600">impact {formatINR(exc.financial_impact)}</span>}
                      </div>
                      <p className="text-stone-600">{exc.explanation}</p>
                      <p className="mt-1 text-[10px] text-stone-500">
                        human review: {exc.human_action_required ? 'required' : 'not required'}
                        {exc.evidence_refs.length > 0 && ` · evidence: ${exc.evidence_refs.join(', ')}`}
                      </p>
                    </div>
                  ))}
                </div>
              )}

              <h3 className="section-label mb-2 mt-4">Ledger & Evidence</h3>
              <div className="space-y-1.5 text-[10px]">
                {selected.decision_id ? (
                  <>
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-stone-500">Decision</span>
                      <button type="button" onClick={() => navigate(`/decisions/${selected.decision_id}`)} className="font-mono text-purple-600 hover:underline">{selected.decision_id}</button>
                    </div>
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-stone-500">Decision hash</span>
                      <span className="font-mono text-stone-600">{decision?.decision_hash ? `${decision.decision_hash.slice(0, 20)}…` : '—'}</span>
                    </div>
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-stone-500">Previous hash</span>
                      <span className="font-mono text-stone-600">{decision?.prev_decision_hash ? `${decision.prev_decision_hash.slice(0, 20)}…` : '—'}</span>
                    </div>
                  </>
                ) : (
                  <p className="text-stone-500">No ledger decision — no valid capture existed for this case (recorded as exception).</p>
                )}
                <div className="flex items-center justify-between gap-2">
                  <span className="text-stone-500">Related records</span>
                  <span className="font-mono text-stone-600">{selected.related_record_ids.length}</span>
                </div>
              </div>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}