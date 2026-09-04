import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import type { AnalyzeResult } from '../api/types';
import { formatINR } from '../lib/format';
import BorderGlow from '../components/react-bits/BorderGlow';

interface EvidenceItem {
  source_type: string;
  label: string;
  raw_content: string;
}

const SOURCE_TYPES = [
  { value: 'order', label: 'Order' },
  { value: 'delivery', label: 'Delivery Record' },
  { value: 'complaint', label: 'Customer Complaint' },
  { value: 'refund_record', label: 'Refund Record' },
  { value: 'seller_agreement', label: 'Seller Agreement' },
  { value: 'policy', label: 'Policy Document' },
];

const PRESET_EVIDENCE: Record<string, { label: string; content: string }> = {
  order: {
    label: 'Order Record',
    content: JSON.stringify(
      { order_id: '', seller_id: '', product: '', amount: 0, order_date: '', status: '' },
      null,
      2
    ),
  },
  delivery: {
    label: 'Delivery Record',
    content: JSON.stringify(
      { order_id: '', promised_date: '', actual_date: '', delay_days: 0, carrier: '' },
      null,
      2
    ),
  },
  complaint: {
    label: 'Customer Complaint',
    content: JSON.stringify(
      { complaint_id: '', order_id: '', customer_id: '', issue: '', severity: 'medium', resolution: '', filed_date: '' },
      null,
      2
    ),
  },
  refund_record: {
    label: 'Refund Record',
    content: JSON.stringify(
      { refund_id: '', order_id: '', amount: 0, reason: '', status: 'processed', return_date: '' },
      null,
      2
    ),
  },
};

export default function AnalyzeDecision() {

  const [entityId, setEntityId] = useState('');
  const [grossAmount, setGrossAmount] = useState('');
  const [hasSlaBreach, setHasSlaBreach] = useState(false);
  const [slaPenalty, setSlaPenalty] = useState('12000');
  const [hasReturns, setHasReturns] = useState(false);
  const [returnReserve, setReturnReserve] = useState('5000');
  const [evidenceItems, setEvidenceItems] = useState<EvidenceItem[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<AnalyzeResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const addEvidence = (sourceType: string) => {
    const preset = PRESET_EVIDENCE[sourceType];
    setEvidenceItems((prev) => [
      ...prev,
      {
        source_type: sourceType,
        label: preset?.label || sourceType,
        raw_content: preset?.content || '{}',
      },
    ]);
  };

  const updateEvidence = (index: number, field: keyof EvidenceItem, value: string) => {
    setEvidenceItems((prev) => prev.map((item, i) => (i === index ? { ...item, [field]: value } : item)));
  };

  const removeEvidence = (index: number) => {
    setEvidenceItems((prev) => prev.filter((_, i) => i !== index));
  };

  const handleSubmit = async () => {
    if (!entityId.trim()) { setError('Seller/entity ID is required'); return; }
    const amount = parseInt(grossAmount, 10);
    if (!amount || amount <= 0) { setError('Gross amount must be a positive number'); return; }
    if (evidenceItems.length === 0) { setError('Add at least one evidence record'); return; }

    // Validate evidence JSON
    for (let i = 0; i < evidenceItems.length; i++) {
      try {
        JSON.parse(evidenceItems[i].raw_content);
      } catch {
        setError(`Evidence item ${i + 1} has invalid JSON`);
        return;
      }
    }

    setSubmitting(true);
    setError(null);
    try {
      const res = await api.analyzeDecision({
        entity_id: entityId.trim(),
        gross_amount: amount,
        evidence_items: evidenceItems.map((e) => ({
          source_type: e.source_type,
          raw_content: e.raw_content,
        })),
        has_sla_breach: hasSlaBreach,
        sla_penalty_amount: hasSlaBreach ? parseInt(slaPenalty, 10) || 0 : 0,
        has_returns: hasReturns,
        return_reserve_amount: hasReturns ? parseInt(returnReserve, 10) || 0 : 0,
      });
      setResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Analysis failed');
    } finally {
      setSubmitting(false);
    }
  };

  // Result view
  if (result) {
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-3">
          <Link to="/" className="text-[11px] font-medium text-stone-600 hover:text-stone-800">Dashboard</Link>
          <span className="text-stone-500">→</span>
          <h1 className="text-xl font-bold tracking-tight text-stone-800">Decision Analyzed</h1>
        </div>

        {/* Result hero */}
        <BorderGlow
          backgroundColor="#120F17"
          borderRadius={16}
          glowRadius={32}
          glowColor="42, 65, 55"
          glowIntensity={0.8}
          colors={['#D9A441', '#4B4560', '#0B0A0F']}
        >
          <div className="p-6">
            <div className="flex items-center gap-2 mb-4">
              <span className="rounded bg-purple-400/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-purple-300">
                Analyzed
              </span>
              <span className={`rounded px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${
                result.decision_status === 'REVIEW_REQUIRED'
                  ? 'bg-amber-400/15 text-amber-300'
                  : 'bg-[#4ADE80]/15 text-[#4ADE80]'
              }`}>
                {result.decision_status}
              </span>
            </div>

            <div className="flex items-baseline gap-3 mb-4">
              <span className="amount text-3xl text-stone-100">{formatINR(result.gross_amount)}</span>
              <span className="text-stone-400">→</span>
              <span className="amount text-3xl text-[#4ADE80]">{formatINR(result.final_amount)}</span>
            </div>

            {/* Line items */}
            <div className="space-y-2 mb-4">
              {result.line_items.map((li, i) => (
                <div key={i} className="flex items-center justify-between rounded-md border border-white/10 bg-white/[0.04] px-3 py-2">
                  <span className="text-xs text-stone-300">{li.label}</span>
                  <span className="amount text-xs text-[#F87171]">−{formatINR(li.amount)}</span>
                </div>
              ))}
            </div>

            {/* Evidence + hash */}
            <div className="flex flex-wrap gap-3 text-[11px] text-stone-300">
              <span>{result.evidence_count} evidence record{result.evidence_count !== 1 ? 's' : ''}</span>
              <span>·</span>
              <span>{result.claims.length} claim{result.claims.length !== 1 ? 's' : ''}</span>
              <span>·</span>
              <span className="font-mono">Hash: {result.decision_hash.slice(0, 16)}…</span>
            </div>

            {/* Actions */}
            <div className="mt-5 flex flex-wrap gap-3">
              <Link
                to={`/decisions/${result.decision_id}`}
                className="inline-flex items-center gap-1.5 rounded-lg bg-purple-600 px-4 py-2 text-sm font-semibold text-white btn-smooth hover:bg-purple-500"
              >
                View Decision Detail →
              </Link>
              <button
                type="button"
                onClick={() => { setResult(null); setEvidenceItems([]); setGrossAmount(''); setEntityId(''); }}
                className="inline-flex items-center gap-1.5 rounded-lg border border-white/15 px-4 py-2 text-sm font-medium text-stone-200 btn-smooth hover:border-purple-300/50 hover:text-white"
              >
                New Analysis
              </button>
            </div>
          </div>
        </BorderGlow>

        {/* Pipeline stages */}
        <div className="space-y-3">
          <h2 className="section-label">Provenance Chain</h2>
          <div className="flex flex-col gap-0">
            {[
              { label: 'Evidence', detail: `${result.evidence_count} records ingested`, cls: 'text-stone-500', dot: 'border-stone-400', solid: false },
              { label: 'Extracted Facts', detail: `${result.line_items.length} line items derived`, cls: 'text-stone-500', dot: 'border-stone-400', solid: false },
              { label: 'Policy Match', detail: `${result.claims.length} policy clause(s) applied`, cls: 'text-violet-700', dot: 'border-violet-500', solid: false },
              { label: 'Deterministic Calculation', detail: `${formatINR(result.gross_amount)} → ${formatINR(result.final_amount)}`, cls: 'text-stone-800', dot: 'border-stone-700', solid: false },
              { label: 'Decision Created', detail: result.decision_id, cls: 'text-stone-800', dot: 'border-stone-700', solid: false },
              { label: 'Hash Computed', detail: `${result.decision_hash.slice(0, 24)}…`, cls: 'text-emerald-700', dot: 'border-emerald-500', solid: true },
            ].map((stage, i) => (
              <div key={i} className="flex items-start gap-3">
                <div className="flex flex-col items-center">
                  <div className={`h-2.5 w-2.5 rounded-full border-2 ${stage.dot} ${stage.solid ? 'bg-emerald-500 border-emerald-500' : ''}`} />
                  {i < 5 && <div className="w-px h-6 bg-black/[0.08]" />}
                </div>
                <div className="pb-2">
                  <p className={`text-xs font-semibold ${stage.cls}`}>{stage.label}</p>
                  <p className="text-[11px] font-mono text-stone-500">{stage.detail}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  }

  // Form view
  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <h1 className="text-xl font-bold tracking-tight text-stone-800 sm:text-2xl">Analyze Decision</h1>
        <p className="mt-1 max-w-xl text-sm leading-relaxed text-stone-600">
          Bring the evidence. EntitlementLedger reconstructs the decision.
          Provide a seller/entity, transaction amount, and evidence records to produce an auditable financial decision.
        </p>
      </div>

      {error && (
        <div className="rounded-xl border border-red-200 bg-red-500/[0.04] p-4">
          <p className="text-sm font-semibold text-red-600">{error}</p>
        </div>
      )}

      {/* Entity + Amount */}
      <div className="space-y-4">
        <h2 className="section-label">Transaction Details</h2>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="block text-[11px] font-medium text-stone-600 mb-1.5">Seller / Entity ID</label>
            <input
              type="text"
              value={entityId}
              onChange={(e) => setEntityId(e.target.value)}
              placeholder="e.g. seller_abc"
              className="w-full rounded-md border border-[var(--border)] bg-white/50 px-3 py-2 text-sm text-stone-800 placeholder:text-stone-500 focus:border-purple-500/40 focus:outline-none"
            />
          </div>
          <div>
            <label className="block text-[11px] font-medium text-stone-600 mb-1.5">Gross Amount (paise)</label>
            <input
              type="number"
              value={grossAmount}
              onChange={(e) => setGrossAmount(e.target.value)}
              placeholder="e.g. 100000"
              min={1}
              className="w-full rounded-md border border-[var(--border)] bg-white/50 px-3 py-2 text-sm text-stone-800 font-mono placeholder:text-stone-500 focus:border-purple-500/40 focus:outline-none"
            />
            {grossAmount && (
              <p className="mt-1 text-[10px] text-stone-500">{formatINR(parseInt(grossAmount, 10) || 0)}</p>
            )}
          </div>
        </div>
      </div>

      {/* Deductions */}
      <div className="space-y-4">
        <h2 className="section-label">Deductions</h2>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="rounded-lg border border-[var(--border)] bg-white/60 p-4">
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={hasSlaBreach} onChange={(e) => setHasSlaBreach(e.target.checked)} className="accent-[#D9A441]" />
              <span className="text-xs font-medium text-stone-800">SLA Breach Detected</span>
            </label>
            {hasSlaBreach && (
              <div className="mt-3">
                <label className="block text-[10px] text-stone-600 mb-1">Penalty Amount (paise)</label>
                <input
                  type="number"
                  value={slaPenalty}
                  onChange={(e) => setSlaPenalty(e.target.value)}
                  className="w-full rounded border border-[var(--border)] bg-white/50 px-2 py-1.5 text-xs text-stone-800 font-mono focus:border-purple-500/40 focus:outline-none"
                />
              </div>
            )}
          </div>
          <div className="rounded-lg border border-[var(--border)] bg-white/60 p-4">
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={hasReturns} onChange={(e) => setHasReturns(e.target.checked)} className="accent-[#D9A441]" />
              <span className="text-xs font-medium text-stone-800">Return Processed</span>
            </label>
            {hasReturns && (
              <div className="mt-3">
                <label className="block text-[10px] text-stone-600 mb-1">Reserve Amount (paise)</label>
                <input
                  type="number"
                  value={returnReserve}
                  onChange={(e) => setReturnReserve(e.target.value)}
                  className="w-full rounded border border-[var(--border)] bg-white/50 px-2 py-1.5 text-xs text-stone-800 font-mono focus:border-purple-500/40 focus:outline-none"
                />
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Evidence */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="section-label">Evidence Records</h2>
        </div>

        {evidenceItems.length === 0 && (
          <div className="rounded-lg border border-dashed border-black/[0.14] bg-white/40 p-6 text-center">
            <p className="text-xs text-stone-500">No evidence added yet. Add order records, delivery data, complaints, or refund records.</p>
          </div>
        )}

        {evidenceItems.map((item, i) => (
          <div key={i} className="rounded-lg border border-[var(--border)] bg-white/60 p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="rounded bg-purple-600/10 px-2 py-0.5 text-[10px] font-semibold uppercase text-purple-600">
                  {item.label}
                </span>
                <span className="text-[10px] text-stone-500">{item.source_type}</span>
              </div>
              <button
                type="button"
                onClick={() => removeEvidence(i)}
                className="text-[11px] text-red-600 hover:underline"
              >
                Remove
              </button>
            </div>
            <textarea
              value={item.raw_content}
              onChange={(e) => updateEvidence(i, 'raw_content', e.target.value)}
              rows={6}
              className="w-full rounded border border-[var(--border)] bg-white/50 px-3 py-2 text-xs text-stone-800 font-mono placeholder:text-stone-500 focus:border-purple-500/40 focus:outline-none resize-y"
              placeholder='{"order_id": "...", "amount": 100000}'
            />
          </div>
        ))}

        {/* Add evidence buttons */}
        <div className="flex flex-wrap gap-2">
          {SOURCE_TYPES.map((st) => (
            <button
              key={st.value}
              type="button"
              onClick={() => addEvidence(st.value)}
              className="rounded-md border border-[var(--border)] bg-white/60 px-3 py-1.5 text-[11px] font-medium text-stone-600 btn-smooth hover:border-purple-300 hover:text-purple-600"
            >
              + {st.label}
            </button>
          ))}
        </div>
      </div>

      {/* Submit */}
      <div className="flex items-center gap-3 pt-2">
        <button
          type="button"
          onClick={handleSubmit}
          disabled={submitting}
          className="inline-flex items-center gap-1.5 rounded-lg bg-purple-600 px-6 py-2.5 text-sm font-semibold text-[#0B0A0F] btn-smooth hover:bg-purple-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {submitting ? (
            <>
              <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-[#0B0A0F]/30 border-t-[#0B0A0F]" />
              Analyzing…
            </>
          ) : (
            'Analyze Decision'
          )}
        </button>
        <Link to="/" className="text-xs text-stone-500 hover:text-stone-600">Cancel</Link>
      </div>
    </div>
  );
}
