import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import type { ReconciliationDashboard, ReconciliationCase, ReconciliationRun, Decision } from '../api/types';
import { formatINR, formatDateTime } from '../lib/format';
import { SkeletonTable } from '../App';

const CLASS_STYLES: Record<string, string> = {
  MATCHED: 'border border-emerald-300/60 bg-emerald-500/10 text-emerald-700',
  REVIEW_REQUIRED: 'border border-amber-300/60 bg-amber-500/10 text-amber-700',
  EXCEPTION: 'border border-red-300/60 bg-red-500/10 text-red-700',
};

const AI_BADGE: Record<string, { label: string; className: string; dot: string }> = {
  available: { label: 'AI OK', className: 'bg-emerald-500/10 text-emerald-700', dot: 'bg-emerald-500' },
  unavailable: { label: 'AI UNAVAILABLE', className: 'bg-amber-500/10 text-amber-700', dot: 'bg-amber-500' },
  failed: { label: 'AI FAILED', className: 'bg-red-500/10 text-red-700', dot: 'bg-red-500' },
  not_needed: { label: 'DETERMINISTIC', className: 'bg-stone-100 text-stone-600', dot: 'bg-stone-400' },
  not_attempted: { label: 'AI NOT TRIED', className: 'bg-stone-100 text-stone-600', dot: 'bg-stone-400' },
};

const HIGH_RISK = new Set(['AMOUNT_MISMATCH', 'DUPLICATE_PAYMENT', 'DUPLICATE_SETTLEMENT', 'REFUND_MISMATCH', 'FEE_MISMATCH', 'TAX_MISMATCH', 'MISSING_PAYMENT', 'INVALID_RECORD', 'CONTRADICTORY_EVIDENCE']);

type Tone = 'default' | 'good' | 'warn' | 'bad';
const TONE_TEXT: Record<Tone, string> = { default: 'text-stone-900', good: 'text-emerald-600', warn: 'text-amber-600', bad: 'text-red-600' };
const TONE_DOT: Record<Tone, string> = { default: 'bg-stone-300', good: 'bg-emerald-500', warn: 'bg-amber-500', bad: 'bg-red-500' };

function MetricCard({ label, value, sub, tone = 'default' }: { label: string; value: string; sub?: string; tone?: Tone }) {
  return (
    <div className="surface relative overflow-hidden p-4 transition-shadow duration-300 hover:shadow-[0_12px_32px_-16px_rgba(16,16,20,0.25)]">
      <span aria-hidden="true" className={`absolute inset-x-0 top-0 h-px ${tone === 'default' ? 'bg-black/[0.06]' : `bg-gradient-to-r from-transparent via-current to-transparent opacity-60 ${TONE_TEXT[tone]}`}`} />
      <div className="flex items-center justify-between gap-2">
        <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-stone-500">{label}</p>
        <span aria-hidden="true" className={`h-1.5 w-1.5 shrink-0 rounded-full ${TONE_DOT[tone]}`} />
      </div>
      <p className={`amount mt-1.5 text-[22px] font-bold leading-none tracking-tight ${TONE_TEXT[tone]}`}>{value}</p>
      {sub && <p className="mt-1.5 text-[10px] leading-snug text-stone-500">{sub}</p>}
    </div>
  );
}

function StatusChip({ label, dot }: { label: string; dot: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-white/60 px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-stone-600 ring-1 ring-black/[0.06]">
      <span className={`h-1 w-1 rounded-full ${dot}`} />
      {label}
    </span>
  );
}

function TraceRow({ label, sign, amount, running }: { label: string; sign: string; amount: number; running: number }) {
  return (
    <div className="flex items-center justify-between border-b border-black/[0.05] py-1.5 text-[11px] last:border-0">
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
  const [runMsg, setRunMsg] = useState<{ ok: boolean; text: string } | null>(null);
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
      setRunMsg({ ok: true, text: `Run ${run.run_id}: ${run.total_cases} cases — ${run.matched} matched, ${run.review_required} review, ${run.exceptions} exceptions (${(run.throughput_per_sec ?? 0).toFixed(0)}/sec)` });
      setSelectedRunId(run.run_id);
      load();
    } catch (e) {
      setRunMsg({ ok: false, text: e instanceof Error ? e.message : 'Run failed' });
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
          {Array.from({ length: 8 }).map((_, i) => <div key={i} className="h-24 skeleton rounded-xl" />)}
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

  const queue = dash?.unresolved_exceptions ?? [];
  const dist = run && run.total_cases > 0
    ? [
        { label: 'Matched', count: run.matched, color: 'bg-emerald-500' },
        { label: 'Review required', count: run.review_required, color: 'bg-amber-400' },
        { label: 'Exceptions', count: run.exceptions, color: 'bg-red-500' },
      ]
    : null;

  return (
    <div className="space-y-6">
      {/* ── Header ── */}
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div className="flex items-center gap-2.5">
            <span className="inline-flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-br from-fuchsia-500/15 to-violet-500/15 ring-1 ring-black/[0.05]">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#7e22ce" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 3v18h18" /><path d="M7 14l4-4 3 3 5-6" /></svg>
            </span>
            <h1 className="text-xl font-bold tracking-tight text-stone-900 sm:text-2xl">Finance Control Room</h1>
          </div>
          <p className="mt-2 max-w-2xl text-sm leading-relaxed text-stone-600">
            What happened to your money — and why the controller decided what it decided.
            Deterministic engine computes every paisa; AI only interprets evidence.
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {dash && (
            <span className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[11px] font-semibold ring-1 ${
              dash.ledger_verified
                ? 'bg-emerald-500/[0.07] text-emerald-700 ring-emerald-500/20'
                : 'bg-red-500/[0.06] text-red-700 ring-red-500/20'
            }`}>
              <span className={`h-1.5 w-1.5 rounded-full ${dash.ledger_verified ? 'bg-emerald-500' : 'animate-pulse bg-red-500'}`} />
              Ledger {dash.ledger_verified ? 'verified' : 'attention required'}
            </span>
          )}
          <button
            type="button"
            onClick={handleRunDemo}
            disabled={running}
            className="inline-flex items-center gap-2 rounded-lg bg-stone-900 px-4 py-2 text-xs font-semibold text-white btn-smooth hover:bg-stone-800 disabled:opacity-50"
          >
            {running ? (
              <>
                <span className="h-3 w-3 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                Reconciling 100 records…
              </>
            ) : (
              <>
                <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor"><path d="M4 2.5v11l9-5.5-9-5.5Z" /></svg>
                Run 100-record reconciliation
              </>
            )}
          </button>
        </div>
      </div>

      {runMsg && (
        <div role="status" className={`rounded-lg border px-4 py-2.5 text-[11px] ${runMsg.ok ? 'border-emerald-500/20 bg-emerald-500/[0.05] text-emerald-700' : 'border-red-500/25 bg-red-500/[0.05] text-red-700'}`}>
          {runMsg.ok ? '✓ ' : '✗ '}{runMsg.text}
        </div>
      )}

      {/* ── Latest run banner ── */}
      {run && (
        <div className="surface flex flex-wrap items-center gap-x-5 gap-y-2 px-4 py-3">
          <span className="font-mono text-[11px] text-stone-600">{run.run_id}</span>
          <span className="text-[11px] text-stone-500">source <span className="font-mono text-stone-700">{run.source}</span></span>
          <span className="text-[11px] text-stone-500">{formatDateTime(run.started_at)}</span>
          {run.duplicates_detected > 0 && (
            <StatusChip label={`${run.duplicates_detected} duplicate(s) handled idempotently`} dot="bg-violet-500" />
          )}
          {run.audit_completeness > 0 && (
            <StatusChip label={`audit ${Math.round(run.audit_completeness * 100)}%`} dot="bg-emerald-500" />
          )}
          {run.false_auto_resolve > 0 && (
            <StatusChip label={`⚠ ${run.false_auto_resolve} false auto-resolve`} dot="bg-red-500" />
          )}
          <span className="ml-auto font-mono text-[10px] text-stone-400">
            {(run.throughput_per_sec ?? 0).toFixed(0)} cases/s · p50 {(run.p50_latency_ms ?? 0).toFixed(0)}ms · p95 {(run.p95_latency_ms ?? 0).toFixed(0)}ms
          </span>
        </div>
      )}

      {/* ── Key metrics ── */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
        <MetricCard label="Records processed" value={run ? String(run.total_records) : '—'} sub={run ? `${run.total_cases} cases evaluated` : 'no runs yet'} />
        <MetricCard label="Matched" value={run ? String(run.matched) : '—'} tone="good" sub={run ? `${(run.match_rate * 100).toFixed(1)}% match rate` : ''} />
        <MetricCard label="Review required" value={run ? String(run.review_required) : '—'} tone="warn" sub={dist ? 'needs human judgement' : ''} />
        <MetricCard label="Exceptions" value={run ? String(run.exceptions) : '—'} tone="bad" sub={queue.length ? `${queue.length} unresolved in queue` : ''} />
        <MetricCard label="Calculation accuracy" value={run?.calculation_accuracy != null ? `${(run.calculation_accuracy * 100).toFixed(1)}%` : '—'} sub="benchmark-evaluated" />
        <MetricCard label="False auto-resolve" value={run ? String(run.false_auto_resolve) : '—'} tone={run && run.false_auto_resolve > 0 ? 'bad' : 'good'} sub="must stay at 0" />
        <MetricCard label="Decision integrity" value={dash?.ledger_verified ? 'Verified' : 'Unknown'} tone={dash?.ledger_verified ? 'good' : 'default'} sub="hash chain from genesis" />
        <MetricCard label="Queue exposure" value={dash ? formatINR(dash.total_variance ?? 0) : '—'} tone={dash && (dash.total_variance ?? 0) !== 0 ? 'bad' : 'good'} sub="unresolved financial variance" />
      </div>

      {/* ── Outcome distribution ── */}
      {dist && run && (
        <div className="surface px-4 py-3">
          <div className="flex h-2 w-full overflow-hidden rounded-full bg-black/[0.05]">
            {dist.map((s) => (
              <div key={s.label} className={`${s.color} h-full`} style={{ width: `${(s.count / run.total_cases) * 100}%` }} title={`${s.label}: ${s.count}`} />
            ))}
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-1 text-[10px] text-stone-500">
            {dist.map((s) => (
              <span key={s.label} className="inline-flex items-center gap-1.5">
                <span className={`h-1.5 w-1.5 rounded-full ${s.color}`} />
                {s.label} <span className="amount font-semibold text-stone-700">{s.count}</span>
              </span>
            ))}
            <span className="ml-auto font-medium text-stone-400">out of {run.total_cases} cases in latest run</span>
          </div>
        </div>
      )}

      {/* ── Run history ── */}
      {runs.length > 0 && (
        <div className="surface overflow-hidden">
          <div className="border-b border-black/[0.05] px-4 py-3">
            <h2 className="section-label">Reconciliation Runs</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[700px] text-sm">
              <thead>
                <tr className="border-b border-black/[0.05] text-left text-[10px] uppercase tracking-wider text-stone-500">
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
              <tbody className="divide-y divide-black/[0.04]">
                {runs.map((r) => (
                  <tr key={r.run_id} className={`row-hover ${selectedRunId === r.run_id ? 'bg-violet-500/[0.04]' : ''}`}>
                    <td className="px-4 py-3 font-mono text-[11px] text-stone-600">{r.run_id}</td>
                    <td className="px-4 py-3 text-[11px] text-stone-600">{r.source}</td>
                    <td className="amount px-4 py-3 text-right text-xs text-stone-800">{r.total_cases}</td>
                    <td className="amount px-4 py-3 text-right text-xs text-emerald-600">{r.matched}</td>
                    <td className="amount px-4 py-3 text-right text-xs text-amber-600">{r.review_required}</td>
                    <td className="amount px-4 py-3 text-right text-xs text-red-600">{r.exceptions}</td>
                    <td className="amount px-4 py-3 text-right text-xs text-stone-800">{(r.match_rate * 100).toFixed(1)}%</td>
                    <td className="amount px-4 py-3 text-[11px] text-stone-600">{formatDateTime(r.started_at)}</td>
                    <td className="px-4 py-3 text-right">
                      <button type="button" onClick={() => { setSelectedRunId(r.run_id); api.getRunExceptions(r.run_id).then(({ exceptions: ex }) => { if (ex.length) setSelected(ex[0]); }); }} className="text-[11px] font-medium text-violet-600 hover:underline">
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
        <div className="flex items-center justify-between border-b border-black/[0.05] px-4 py-3">
          <h2 className="section-label">Exception Queue</h2>
          <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${queue.length > 0 ? 'bg-red-500/10 text-red-700' : 'bg-emerald-500/10 text-emerald-700'}`}>
            {queue.length} unresolved
          </span>
        </div>
        {!dash || queue.length === 0 ? (
          <div className="px-4 py-12 text-center">
            <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-emerald-500/10 text-emerald-600">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6 9 17l-5-5" /></svg>
            </div>
            <p className="text-sm font-medium text-emerald-700">No unresolved exceptions.</p>
            <p className="mt-1 text-xs text-stone-500">Run a reconciliation to populate the queue.</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-sm">
              <thead>
                <tr className="border-b border-black/[0.05] text-left text-[10px] uppercase tracking-wider text-stone-500">
                  <th className="px-4 py-2.5 font-medium">Case</th>
                  <th className="px-4 py-2.5 font-medium">Payment</th>
                  <th className="px-4 py-2.5 font-medium">Exception</th>
                  <th className="px-4 py-2.5 font-medium">AI</th>
                  <th className="px-4 py-2.5 text-right font-medium">Variance</th>
                  <th className="px-4 py-2.5 font-medium">Explanation</th>
                  <th className="px-4 py-2.5 font-medium"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-black/[0.04]">
                {queue.map((c) => {
                  const ai = AI_BADGE[c.ai_status] || AI_BADGE.not_attempted;
                  return (
                    <tr key={c.case_id} className={`row-hover ${selected?.case_id === c.case_id ? 'bg-violet-500/[0.04]' : ''}`}>
                      <td className="px-4 py-3 font-mono text-[11px] text-stone-600">{c.case_id}</td>
                      <td className="px-4 py-3 font-mono text-[11px] text-stone-700">{c.payment_id}</td>
                      <td className="px-4 py-3">
                        <div className="flex flex-wrap items-center gap-1">
                          {c.exception_codes.slice(0, 2).map((code) => (
                            <span key={code} className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${HIGH_RISK.has(code) ? 'bg-red-500/10 text-red-700' : 'bg-amber-500/10 text-amber-700'}`}>
                              {code}
                            </span>
                          ))}
                          {c.exception_codes.length > 2 && <span className="text-[10px] text-stone-500">+{c.exception_codes.length - 2}</span>}
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[9px] font-semibold ${ai.className}`}>
                          <span className={`h-1 w-1 rounded-full ${ai.dot}`} />
                          {ai.label}
                        </span>
                      </td>
                      <td className={`amount px-4 py-3 text-right text-xs font-medium ${c.variance !== 0 ? 'text-red-600' : 'text-stone-700'}`}>
                        {c.variance !== 0 ? `${c.variance > 0 ? '+' : ''}${formatINR(c.variance)}` : '—'}
                      </td>
                      <td className="px-4 py-3 text-[11px] text-stone-600">{c.explanation.slice(0, 90)}{c.explanation.length > 90 ? '…' : ''}</td>
                      <td className="px-4 py-3 text-right">
                        <button type="button" onClick={() => openCase(c.case_id)} className="text-[11px] font-medium text-violet-600 hover:underline">
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
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-black/[0.05] px-4 py-3">
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
                  <div className="mt-2 flex items-center justify-between rounded-lg bg-stone-100/70 px-3 py-2 text-[12px]">
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
                <div className="rounded-lg border border-black/[0.06] bg-white/60 p-3">
                  <p className="text-[9px] font-semibold uppercase tracking-wider text-stone-500">Captured</p>
                  <p className="amount mt-1 text-sm font-semibold text-stone-800">{formatINR(selected.calculation_trace?.captured_amount ?? 0)}</p>
                </div>
                <div className="rounded-lg border border-black/[0.06] bg-white/60 p-3">
                  <p className="text-[9px] font-semibold uppercase tracking-wider text-stone-500">Refunds</p>
                  <p className="amount mt-1 text-sm font-semibold text-red-600">−{formatINR(selected.calculation_trace?.refund_total ?? 0)}</p>
                </div>
                <div className="rounded-lg border border-black/[0.06] bg-white/60 p-3">
                  <p className="text-[9px] font-semibold uppercase tracking-wider text-stone-500">Fees / Taxes</p>
                  <p className="amount mt-1 text-sm font-semibold text-stone-800">
                    −{formatINR(selected.calculation_trace?.fee_total ?? 0)} / −{formatINR(selected.calculation_trace?.tax_total ?? 0)}
                  </p>
                </div>
                <div className="rounded-lg border border-black/[0.06] bg-white/60 p-3">
                  <p className="text-[9px] font-semibold uppercase tracking-wider text-stone-500">Adjustments</p>
                  <p className="amount mt-1 text-sm font-semibold text-stone-800">
                    {selected.calculation_trace?.adjustments ? `${selected.calculation_trace.adjustments > 0 ? '+' : ''}${formatINR(selected.calculation_trace.adjustments)}` : '0'}
                  </p>
                </div>
              </div>
            </div>

            {/* Right: interpretation + evidence + ledger */}
            <div className="border-t border-black/[0.05] p-5 md:border-l md:border-t-0">
              <h3 className="section-label mb-2">AI Interpretation</h3>
              <div className="mb-1 flex items-center gap-2">
                <span className={`inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[9px] font-semibold ${(AI_BADGE[selected.ai_status] || AI_BADGE.not_attempted).className}`}>
                  <span className={`h-1 w-1 rounded-full ${(AI_BADGE[selected.ai_status] || AI_BADGE.not_attempted).dot}`} />
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
                    <div key={i} className="rounded-lg border border-black/[0.06] bg-white/60 p-3 text-[11px]">
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
                      <button type="button" onClick={() => navigate(`/decisions/${selected.decision_id}`)} className="font-mono text-violet-600 hover:underline">{selected.decision_id}</button>
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
