import { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { api } from '../api/client';
import type { DefensePacket as DefensePacketType } from '../api/types';
import { formatINR, formatDateTime, sourceTypeLabel } from '../lib/format';
import BorderGlow from '../components/react-bits/BorderGlow';
import { SkeletonCard } from '../App';

const SRC_COLORS: Record<string, string> = {
  order: 'bg-sky-500/10 text-sky-700 border border-sky-200',
  delivery: 'bg-blue-500/10 text-blue-700 border border-blue-200',
  complaint: 'bg-red-500/10 text-red-700 border border-red-200',
  policy_doc: 'bg-violet-500/10 text-violet-700 border border-violet-200',
  seller_agreement: 'bg-emerald-500/10 text-emerald-700 border border-emerald-200',
  refund_record: 'bg-purple-500/10 text-purple-700 border border-purple-200',
};
const chip = (t: string) => `inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${SRC_COLORS[t] || 'bg-white/60 text-stone-600 border border-[var(--border)]'}`;

export default function DefensePacket() {
  const { id } = useParams<{ id: string }>();
  const [packet, setPacket] = useState<DefensePacketType | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pdfLoading, setPdfLoading] = useState(false);

  useEffect(() => {
    if (!id) return;
    api.getDefensePacket(id).then(setPacket).catch((e) => setError(e.message)).finally(() => setLoading(false));
  }, [id]);

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="h-8 skeleton w-48" />
        <div className="h-24 skeleton rounded-xl" />
        <div className="grid gap-6 xl:grid-cols-2">{Array.from({ length: 4 }).map((_, i) => <SkeletonCard key={i} rows={4} />)}</div>
      </div>
    );
  }

  if (error || !packet) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-500/[0.04] p-5">
        <p className="text-sm font-semibold text-red-600">Defense packet not available</p>
        <p className="mt-1 text-xs text-stone-600">{error || 'No defense packet data for this decision.'}</p>
        <div className="mt-3"><Link to="/" className="text-xs font-medium text-purple-600 hover:underline">← Dashboard</Link></div>
      </div>
    );
  }

  const { decision, financial_breakdown, evidence, policies, integrity } = packet;
  const validationOk = financial_breakdown.validation && 'is_valid' in financial_breakdown.validation
    ? Boolean((financial_breakdown.validation as { is_valid?: boolean }).is_valid) : true;

  const handleExportJSON = () => {
    const blob = new Blob([JSON.stringify(packet, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `defense-packet-${decision.decision_id}.json`;
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
  };

  const handleDownloadPDF = async () => {
    setPdfLoading(true);
    try {
      const token = localStorage.getItem('el_token');
      const res = await fetch(`/api/decisions/${decision.decision_id}/defense-packet/pdf`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!res.ok) throw new Error(`PDF generation failed: ${res.status}`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = `defense_${decision.decision_id}.pdf`;
      document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
    } catch (e) {
      alert(e instanceof Error ? e.message : 'PDF download failed');
    } finally {
      setPdfLoading(false);
    }
  };

  const handlePrint = () => {
    window.print();
  };

  return (
    <div className="space-y-6">
      {/* ── Header ── */}
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between no-print">
        <div>
          <div className="flex items-center gap-3">
            <Link to={`/decisions/${decision.decision_id}`} className="text-xs text-stone-500 hover:text-purple-600 transition-colors">← Decision</Link>
            <h1 className="text-xl font-bold tracking-tight text-stone-800 sm:text-2xl">Defense Packet</h1>
          </div>
          <p className="mt-1 max-w-xl text-sm leading-relaxed text-stone-600">
            Complete audit package for <span className="font-mono text-purple-600">{decision.decision_id}</span> — evidence,
            policies, authority and integrity in one exportable record.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-semibold ${
            integrity.valid
              ? 'border-[#4ADE80]/30 bg-emerald-500/[0.06] text-emerald-600'
              : 'border-red-200 bg-red-500/[0.06] text-red-600'
          }`}>
            <span className={`h-1.5 w-1.5 rounded-full ${integrity.valid ? 'bg-emerald-500' : 'bg-red-500'}`} />
            {integrity.valid ? 'Integrity verified' : 'Integrity compromised'}
          </span>
          <button type="button" onClick={handlePrint} className="rounded-lg border border-[var(--border)] bg-white/60 px-4 py-2 text-sm font-medium text-stone-800 btn-smooth hover:border-purple-500/40 hover:text-purple-600">
            Print ↓
          </button>
          <button type="button" onClick={handleDownloadPDF} disabled={pdfLoading} className="rounded-lg border border-purple-500/30 bg-purple-50 px-4 py-2 text-sm font-medium text-purple-700 btn-smooth hover:bg-purple-100 disabled:opacity-50">
            {pdfLoading ? 'Generating…' : 'Download PDF ↓'}
          </button>
          <button type="button" onClick={handleExportJSON} className="rounded-lg bg-purple-600 px-4 py-2 text-sm font-semibold text-[#0B0A0F] btn-smooth hover:bg-purple-700">
            Export JSON ↓
          </button>
        </div>
      </div>

      {/* ── Summary card with BorderGlow ── */}
      <BorderGlow
        backgroundColor="#120F17"
        borderRadius={14}
        glowRadius={28}
        glowIntensity={0.65}
        glowColor={validationOk ? '42, 65, 55' : '355, 70, 62'}
        colors={validationOk ? ['#D9A441', '#4B4560', '#0B0A0F'] : ['#F87171', '#3a2020', '#0B0A0F']}
        animated={false}
      >
        <div className="grid gap-4 p-5 sm:grid-cols-4">
          <div>
            <p className="section-label">Seller</p>
            <Link to={`/sellers/${decision.entity_id}`} className="mt-0.5 block truncate text-sm font-semibold text-purple-300 font-mono hover:text-purple-200 hover:underline">{decision.entity_id}</Link>
          </div>
          <div>
            <p className="section-label">Approver</p>
            <p className="mt-0.5 text-sm font-semibold text-stone-100">{decision.approver_id}</p>
          </div>
          <div>
            <p className="section-label">Approved</p>
            <p className="amount mt-0.5 text-sm font-semibold text-stone-100">{formatDateTime(decision.approved_at)}</p>
          </div>
          <div>
            <p className="section-label">Calculation</p>
            <p className={`mt-0.5 text-sm font-semibold ${validationOk ? 'text-[#4ADE80]' : 'text-[#F87171]'}`}>
              {validationOk ? '✓ Gross − deductions = final' : '⚠ Mismatch'}
            </p>
          </div>
        </div>
      </BorderGlow>

      <div className="grid gap-6 xl:grid-cols-2">
        {/* ── Financial Breakdown ── */}
        <section className="surface p-5">
          <h2 className="section-label mb-4">Financial Breakdown</h2>
          <div className="space-y-3">
            <div className="flex items-center justify-between border-b border-[var(--border)] pb-3">
              <span className="text-xs text-stone-600">Gross entitlement</span>
              <span className="amount text-base text-stone-800">{formatINR(financial_breakdown.gross_amount)}</span>
            </div>
            {decision.line_items.map((item, idx) => (
              <div key={idx} className="flex items-center justify-between text-xs">
                <span className="text-stone-600">−{item.label} <span className="font-mono text-[10px] text-stone-500">{item.policy_clause_id || ''}</span></span>
                <span className="amount font-medium text-red-600">{formatINR(item.amount)}</span>
              </div>
            ))}
            <div className="flex items-center justify-between border-t border-[var(--border)] pt-3">
              <span className="text-xs font-semibold text-stone-800">Final settlement</span>
              <span className="amount text-lg text-emerald-600">{formatINR(financial_breakdown.final_amount)}</span>
            </div>
          </div>
        </section>

        {/* ── Integrity Verification ── */}
        <div className={`rounded-xl border p-5 ${integrity.valid ? 'border-emerald-200 bg-emerald-500/[0.03]' : 'border-red-200 bg-red-500/[0.03]'}`}>
          <h2 className={`section-label ${integrity.valid ? 'text-emerald-600' : 'text-red-600'}`}>Tamper-Evident Verification</h2>
          <div className="mt-3 space-y-2 text-xs">
            <p className={integrity.valid ? 'text-emerald-600' : 'text-red-600'}>
              {integrity.valid ? '✓ Hash chain verified — no modifications' : '⚠ Hash chain broken'}
            </p>
            <p className="text-stone-600">{integrity.checked_count} record(s) from genesis.</p>
            {integrity.break_at && <p className="font-mono text-red-600">Break at: {integrity.break_at}</p>}
            <div className="mt-2 space-y-1 rounded-lg border border-[var(--border)] bg-white/50 p-3 font-mono text-[10px] break-all text-stone-600">
              <p>prev: {decision.prev_decision_hash === 'genesis' ? 'genesis' : decision.prev_decision_hash.slice(0, 30) + '…'}</p>
              <p className="text-purple-600">hash: {decision.decision_hash.slice(0, 30)}…</p>
            </div>
          </div>
        </div>

        {/* ── Supporting Evidence ── */}
        <section className="surface p-5">
          <h2 className="section-label mb-4">Supporting Evidence ({evidence.length})</h2>
          <div className="space-y-3">
            {evidence.length === 0 && (
              <p className="text-xs text-stone-500">No evidence records linked.</p>
            )}
            {evidence.map((ev) => (
              <Link key={ev.evidence_id} to={`/decisions/${decision.decision_id}/evidence?highlight=${ev.evidence_id}`} className="block rounded-lg border border-[var(--border)] bg-white/50 p-3 transition-colors hover:border-purple-300">
                <div className="mb-1.5 flex items-center gap-2">
                  <span className={chip(ev.source_type)}>{sourceTypeLabel(ev.source_type)}</span>
                  <span className="font-mono text-[10px] text-stone-500">{ev.evidence_id}</span>
                </div>
                <div className="space-y-0.5 text-[11px] text-stone-600">
                  {ev.extracted_facts.slice(0, 3).map((f, fi) => <p key={fi}>• {f.fact}</p>)}
                </div>
              </Link>
            ))}
          </div>
        </section>

        {/* ── Applicable Policies ── */}
        <section className="surface p-5">
          <h2 className="section-label mb-4">Applicable Policies ({policies.length})</h2>
          <div className="space-y-3">
            {policies.map((p) => (
              <div key={p.policy_id} className="rounded-lg border border-[var(--border)] bg-white/50 p-3">
                <div className="mb-1 flex items-center justify-between">
                  <span className="font-mono text-[11px] font-medium text-purple-600">{p.policy_id}</span>
                  <span className="text-[10px] text-stone-500">v{p.version}</span>
                </div>
                <p className="text-[11px] leading-relaxed text-stone-600">{p.clause_text}</p>
              </div>
            ))}
          </div>
        </section>
      </div>

      <p className="text-center text-[10px] text-stone-500 no-print">
        Generated by EntitlementLedger · content matches the hash-chained record at time of generation.
      </p>
    </div>
  );
}
