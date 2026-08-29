import { useEffect, useState, useMemo } from 'react';
import { useParams, useSearchParams, Link } from 'react-router-dom';
import { api } from '../api/client';
import type { Evidence, Decision } from '../api/types';
import { sourceTypeLabel } from '../lib/format';
import { SkeletonTable } from '../App';

const SRC_COLORS: Record<string, string> = {
  order: 'bg-sky-400/10 text-sky-300 border border-sky-400/20',
  delivery: 'bg-[#7CA5D4]/10 text-[#7CA5D4] border border-[#7CA5D4]/20',
  complaint: 'bg-red-500/10 text-red-600 border border-red-200',
  policy_doc: 'bg-purple-600/10 text-purple-600 border border-purple-500/20',
  seller_agreement: 'bg-emerald-500/10 text-emerald-600 border border-emerald-200',
  refund_record: 'bg-purple-400/10 text-purple-300 border border-purple-400/20',
};
const chip = (t: string) => `inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${SRC_COLORS[t] || 'bg-white/60 text-stone-600 border border-[var(--border)]'}`;

export default function EvidenceView() {
  const { id } = useParams<{ id: string }>();
  const [searchParams] = useSearchParams();
  const highlight = searchParams.get('highlight');
  const [decision, setDecision] = useState<Decision | null>(null);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    Promise.all([api.getDecision(id), api.getDecisionEvidence(id)])
      .then(([d, e]) => { setDecision(d); setEvidence(e); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [id]);

  const active = useMemo(() => {
    if (selectedId) {
      const found = evidence.find((x) => x.evidence_id === selectedId);
      if (found) return found;
    }
    if (highlight && evidence.length > 0) {
      const match = evidence.find((x) => x.evidence_id === highlight);
      if (match) return match;
    }
    return evidence[0] || null;
  }, [selectedId, highlight, evidence]);

  useEffect(() => {
    if (active) {
      const el = document.getElementById(`evidence-item-${active.evidence_id}`);
      el?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }, [active]);

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="h-8 skeleton w-48" />
        <SkeletonTable rows={4} />
      </div>
    );
  }
  if (error) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-500/[0.04] p-5">
        <p className="text-sm font-semibold text-red-600">Evidence unavailable</p>
        <p className="mt-1 text-xs text-stone-600">{error}</p>
      </div>
    );
  }

  let parsed: Record<string, unknown> = {};
  if (active) try { parsed = JSON.parse(active.raw_content); } catch { /* noop */ }

  return (
    <div className="space-y-4">
      {/* ── Header ── */}
      <div>
        <div className="flex items-center gap-3">
          <Link to={id ? `/decisions/${id}` : '/'} className="text-xs text-stone-500 hover:text-purple-600 transition-colors">← Decision</Link>
          <h1 className="text-xl font-bold tracking-tight text-stone-800 sm:text-2xl">Evidence Viewer</h1>
        </div>
        {decision && (
          <p className="mt-1 text-sm text-stone-600">
            Source records for <span className="font-mono text-purple-600">{decision.decision_id}</span> · {evidence.length} record(s)
          </p>
        )}
      </div>

      {evidence.length === 0 ? (
        <div className="surface p-12 text-center">
          <p className="text-sm text-stone-600">No evidence linked to this decision.</p>
          <p className="mt-1 text-xs text-stone-500">Evidence records are created when decisions are generated through scenarios.</p>
        </div>
      ) : (
        /* 3-column forensic layout */
        <div className="grid gap-4 xl:grid-cols-[260px_1fr_320px]">
          {/* Left: source metadata list */}
          <div className="surface overflow-hidden xl:max-h-[calc(100vh-200px)] xl:overflow-y-auto">
            <div className="border-b border-[var(--border)] px-3 py-2.5">
              <p className="section-label">Source Metadata</p>
            </div>
            <div className="divide-y divide-white/[0.04]">
              {evidence.map((ev) => {
                const isActive = active?.evidence_id === ev.evidence_id;
                return (
                  <button
                    key={ev.evidence_id}
                    type="button"
                    id={`evidence-item-${ev.evidence_id}`}
                    onClick={() => setSelectedId(ev.evidence_id)}
                    className={`w-full px-3 py-3 text-left transition-colors ${isActive ? 'bg-purple-600/[0.06]' : 'hover:bg-white/[0.02]'}`}
                  >
                    <div className="flex items-center gap-2">
                      <span className={chip(ev.source_type)}>{sourceTypeLabel(ev.source_type)}</span>
                      {isActive && <span className="h-1.5 w-1.5 rounded-full bg-purple-600" />}
                    </div>
                    <p className="mt-1 font-mono text-[10px] text-stone-500">{ev.evidence_id}</p>
                    <p className="mt-0.5 text-[10px] text-stone-500">
                      {ev.extracted_facts.length} facts · linked to {ev.linked_decision_ids.length} decision(s)
                    </p>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Center: raw document */}
          <div className="surface overflow-hidden">
            <div className="border-b border-[var(--border)] px-4 py-2.5 flex items-center justify-between">
              <p className="section-label">Raw Source Document</p>
              {active && <span className={chip(active.source_type)}>{sourceTypeLabel(active.source_type)}</span>}
            </div>
            <div className="p-4 font-mono text-[11px] leading-relaxed">
              {Object.keys(parsed).length > 0 ? (
                <div className="space-y-1.5">
                  {Object.entries(parsed).map(([key, val]) => (
                    <div key={key} className="flex gap-3">
                      <span className="shrink-0 w-28 text-stone-500">{key}</span>
                      <span className="break-all text-stone-800">{String(val)}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <pre className="whitespace-pre-wrap text-stone-600">{active?.raw_content || ''}</pre>
              )}
            </div>
          </div>

          {/* Right: AI extracted facts */}
          <div className="surface overflow-hidden xl:max-h-[calc(100vh-200px)] xl:overflow-y-auto">
            <div className="border-b border-[var(--border)] px-4 py-2.5">
              <p className="section-label">AI Extracted Facts</p>
            </div>
            <div className="p-4 space-y-2">
              {active && active.extracted_facts.length > 0 ? (
                active.extracted_facts.map((fact, fi) => (
                  <div key={fi} className="rounded-lg border border-purple-500/15 bg-purple-600/[0.03] px-3 py-2.5">
                    <div className="flex items-start justify-between gap-2">
                      <p className="text-[11px] text-stone-800 leading-relaxed">{fact.fact}</p>
                      <span className={`shrink-0 amount text-[10px] font-medium ${fact.confidence >= 0.8 ? 'text-emerald-600' : 'text-stone-600'}`}>
                        {Math.round(fact.confidence * 100)}%
                      </span>
                    </div>
                  </div>
                ))
              ) : (
                <div className="py-6 text-center">
                  <p className="text-xs text-stone-500">No facts extracted yet.</p>
                </div>
              )}
            </div>

            {/* Linked decisions */}
            {active && active.linked_decision_ids.length > 0 && (
              <div className="border-t border-[var(--border)] px-4 py-3">
                <p className="section-label mb-1.5">Linked Decisions</p>
                <div className="flex flex-wrap gap-1.5">
                  {active.linked_decision_ids.map((did) => (
                    <Link key={did} to={`/decisions/${did}`} className="rounded border border-[var(--border)] bg-white/50 px-2 py-0.5 font-mono text-[11px] text-purple-600 hover:bg-purple-600/10 transition-colors">{did}</Link>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
