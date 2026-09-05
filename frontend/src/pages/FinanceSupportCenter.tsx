import { useEffect, useState } from 'react';
import { api } from '../api/client';
import ProductErrorState from '../components/ProductErrorState';
import type { SupportStatus, SupportAskResponse } from '../api/types';

const MODE_LABELS: Record<string, string> = {
  explain_exception: 'Explain an exception',
  summarize_run: 'Summarize a run',
  pattern_analysis: 'Find patterns',
  review_assistant: 'Review assistant',
  finance_qa: 'Finance Q&A',
};

export default function FinanceSupportCenter() {
  const [status, setStatus] = useState<SupportStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [mode, setMode] = useState('finance_qa');
  const [question, setQuestion] = useState('');
  const [runId, setRunId] = useState('');
  const [caseId, setCaseId] = useState('');
  const [asking, setAsking] = useState(false);
  const [answer, setAnswer] = useState<SupportAskResponse | null>(null);
  const [askError, setAskError] = useState<Error | null>(null);
  const [runs, setRuns] = useState<Array<{ run_id: string; source: string }>>([]);

  const load = () => {
    api.getSupportStatus()
      .then(setStatus)
      .catch((e) => setError(e))
      .finally(() => setLoading(false));
    api.getReconciliationRuns(10)
      .then((r) => setRuns(r.runs))
      .catch(() => {});
  };
  useEffect(() => { load(); }, []);

  const ask = async () => {
    setAsking(true);
    setAnswer(null);
    setAskError(null);
    try {
      const resp = await api.askSupportCenter({
        question,
        mode,
        run_id: runId || undefined,
        case_id: caseId || undefined,
      });
      setAnswer(resp);
    } catch (e) {
      setAskError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setAsking(false);
    }
  };

  if (loading) {
    return <div className="h-8 w-64 skeleton" />;
  }
  if (error) {
    return <ProductErrorState error={error} onRetry={() => { setError(null); setLoading(true); load(); }} />;
  }

  const unavailable = !status?.available;

  return (
    <div className="space-y-6">
      {/* ── Header ── */}
      <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div className="flex items-center gap-2.5">
            <span className="inline-flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-br from-sky-500/15 to-emerald-500/15 ring-1 ring-black/[0.05]">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#0e7490" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" /></svg>
            </span>
            <h1 className="text-xl font-bold tracking-tight text-stone-900 sm:text-2xl">Finance Support Center</h1>
          </div>
          <p className="mt-2 max-w-2xl text-sm leading-relaxed text-stone-600">
            Gemini explains what the deterministic controller found. Every number it
            references comes from the ledger-backed engine — it never recalculates or mutates money.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {status && (
            <span className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[11px] font-semibold ring-1 ${
              status.available
                ? 'bg-emerald-500/[0.07] text-emerald-700 ring-emerald-500/20'
                : 'bg-amber-500/[0.06] text-amber-700 ring-amber-500/20'
            }`}>
              <span className={`h-1.5 w-1.5 rounded-full ${status.available ? 'bg-emerald-500' : 'bg-amber-500'}`} />
              {status.available ? `Gemini · ${status.model || 'connected'}` : 'Gemini unavailable'}
            </span>
          )}
          {status && status.usage.invocations + status.usage.failures > 0 && (
            <span className="font-mono text-[10px] text-stone-400">
              {status.usage.invocations} ok · {status.usage.failures} failed
            </span>
          )}
        </div>
      </div>

      {/* ── Ask panel ── */}
      <div className="surface space-y-3 p-5">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
          {Object.entries(MODE_LABELS).map(([key, label]) => (
            <button
              key={key}
              type="button"
              onClick={() => setMode(key)}
              className={`rounded-lg border px-3 py-2 text-left text-[11px] font-semibold transition-colors ${
                mode === key
                  ? 'border-violet-500/30 bg-violet-500/[0.07] text-violet-800'
                  : 'border-black/[0.06] bg-white/40 text-stone-600 hover:border-black/[0.12]'
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {(mode === 'summarize_run' || mode === 'pattern_analysis' || mode === 'review_assistant') && (
          <div className="flex flex-wrap gap-2">
            <label className="flex items-center gap-2 text-[11px] text-stone-500">
              Run
              <select
                value={runId}
                onChange={(e) => setRunId(e.target.value)}
                className="rounded-lg border border-black/[0.08] bg-white px-2 py-1.5 text-[11px] text-stone-800 focus:outline-none"
              >
                <option value="">Latest run</option>
                {runs.map((r) => (
                  <option key={r.run_id} value={r.run_id}>{r.run_id} ({r.source})</option>
                ))}
              </select>
            </label>
          </div>
        )}
        {(mode === 'explain_exception' || mode === 'review_assistant') && (
          <div className="flex flex-wrap gap-2">
            <label className="flex items-center gap-2 text-[11px] text-stone-500">
              Case ID
              <input
                value={caseId}
                onChange={(e) => setCaseId(e.target.value)}
                placeholder="case_…"
                className="w-44 rounded-lg border border-black/[0.08] bg-white px-2 py-1.5 font-mono text-[11px] text-stone-800 focus:outline-none"
              />
            </label>
          </div>
        )}

        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && question.trim() && !asking && !unavailable) ask(); }}
            placeholder={
              unavailable
                ? 'Gemini is unavailable — the deterministic engine keeps running regardless.'
                : mode === 'finance_qa'
                  ? 'e.g. Why is the match rate low? How many refund-related exceptions exist?'
                  : 'e.g. What should I investigate next?'
            }
            disabled={unavailable}
            className="flex-1 rounded-lg border border-black/[0.08] bg-white px-3 py-2 text-[13px] text-stone-800 placeholder:text-stone-400 focus:border-violet-500/40 focus:outline-none disabled:opacity-60"
          />
          <button
            type="button"
            onClick={ask}
            disabled={!question.trim() || asking || unavailable}
            className="inline-flex items-center justify-center gap-2 rounded-lg bg-stone-900 px-4 py-2 text-xs font-semibold text-white btn-smooth hover:bg-stone-800 disabled:opacity-50"
          >
            {asking ? (
              <>
                <span className="h-3 w-3 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                Asking Gemini…
              </>
            ) : (
              'Ask'
            )}
          </button>
        </div>
      </div>

      {unavailable && (
        <div className="rounded-lg border border-amber-500/20 bg-amber-500/[0.05] px-4 py-3 text-[12px] leading-relaxed text-amber-800">
          <span className="font-semibold">Gemini provider unavailable.</span>{' '}
          {status?.error || 'The Support Center needs a configured Gemini API key.'} Deterministic
          reconciliation, the Control Room and the ledger are unaffected. You can retry once the
          provider is reachable.
          <button
            type="button"
            onClick={() => { setLoading(true); setError(null); load(); }}
            className="ml-2 text-[11px] font-semibold underline underline-offset-2"
          >
            Retry
          </button>
        </div>
      )}

      {askError && (
        <div className="rounded-lg border border-red-500/20 bg-red-500/[0.04] p-4">
          <ProductErrorState error={askError} onRetry={ask} compact />
        </div>
      )}

      {answer && answer.status === 'ok' && (
        <div className="surface space-y-4 p-5">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-[13px] font-semibold text-stone-900">Gemini answer</h2>
            <span className="font-mono text-[10px] text-stone-400">
              {answer.provider} · {answer.model || ''} · {answer.latency_ms}ms
            </span>
          </div>

          <p className="text-[13px] leading-relaxed text-stone-800">{answer.answer.answer}</p>

          {answer.answer.key_points.length > 0 && (
            <ul className="space-y-1.5">
              {answer.answer.key_points.map((pt, i) => (
                <li key={i} className="flex items-start gap-2 text-[12px] text-stone-600">
                  <span aria-hidden="true" className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-violet-500" />
                  {pt}
                </li>
              ))}
            </ul>
          )}

          <div className="flex flex-wrap items-center gap-2">
            {answer.answer.insufficient_evidence && (
              <span className="rounded-full bg-amber-500/10 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide text-amber-700 ring-1 ring-amber-500/20">
                Insufficient evidence — answered from what is actually known
              </span>
            )}
            {answer.answer.citations.map((c) => (
              <span key={c} className="rounded-full bg-stone-100 px-2.5 py-1 font-mono text-[10px] text-stone-600 ring-1 ring-black/[0.05]">
                {c}
              </span>
            ))}
            <span className="ml-auto inline-flex items-center gap-1 text-[10px] text-stone-400">
              <span className="h-1 w-1 rounded-full bg-emerald-500" />
              Advisory only — deterministic engine decides
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
