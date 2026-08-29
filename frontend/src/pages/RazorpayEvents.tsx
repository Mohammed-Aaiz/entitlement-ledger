import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import type { RazorpayEvent, RazorpayConnectionInfo, RazorpayStatus } from '../api/types';
import { formatINR, formatDateTime } from '../lib/format';
import { SkeletonTable } from '../App';

function formatAmount(paise: number | null): string { return paise == null ? '—' : formatINR(paise); }

function StepBox({ label, active }: { label: string; active: boolean }) {
  return (
    <span className={`rounded px-2 py-1 text-[9px] font-medium uppercase tracking-wide ${
      active ? 'border border-[#4ADE80]/30 bg-emerald-500/10 text-emerald-600' : 'border border-[var(--border)] bg-white/50 text-stone-500'
    }`}>
      {label}
    </span>
  );
}

const EVT_COLORS: Record<string, string> = {
  'payment.captured': 'bg-emerald-500/10 text-emerald-600',
  'payment.failed': 'bg-red-500/10 text-red-600',
  'payment.authorized': 'bg-purple-600/10 text-purple-600',
  'order.paid': 'bg-purple-600/10 text-purple-600',
  'refund.created': 'bg-purple-400/10 text-purple-300',
  'refund.processed': 'bg-sky-400/10 text-sky-300',
  'settlement.processed': 'bg-emerald-500/10 text-emerald-600',
};

const SOURCE_BADGES: Record<string, { label: string; className: string }> = {
  live_webhook: { label: 'LIVE WEBHOOK', className: 'border border-[#4ADE80]/30 bg-emerald-500/[0.06] text-emerald-600' },
  razorpay_api: { label: 'RAZORPAY API', className: 'border border-purple-300 bg-purple-600/[0.06] text-purple-600' },
  local_simulator: { label: 'LOCAL SIMULATOR', className: 'border border-purple-500/20 bg-purple-600/[0.04] text-purple-600' },
};

function SourceBadge({ source }: { source: string }) {
  const badge = SOURCE_BADGES[source] || { label: source.toUpperCase(), className: 'border border-[var(--border)] bg-white/50 text-stone-600' };
  return (
    <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider ${badge.className}`}>
      {badge.label}
    </span>
  );
}

export default function RazorpayEvents() {
  const [events, setEvents] = useState<RazorpayEvent[]>([]);
  const [connection, setConnection] = useState<RazorpayConnectionInfo | null>(null);
  const [status, setStatus] = useState<RazorpayStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [simAmount, setSimAmount] = useState('100000');
  const [simType, setSimType] = useState('payment.captured');
  const [simulating, setSimulating] = useState(false);
  const [simResult, setSimResult] = useState<string | null>(null);
  const [selectedEvent, setSelectedEvent] = useState<RazorpayEvent | null>(null);
  const [processing, setProcessing] = useState<string | null>(null);
  const [processResult, setProcessResult] = useState<string | null>(null);
  const [syncing, setSyncing] = useState<string | null>(null);
  const [syncResult, setSyncResult] = useState<string | null>(null);
  const navigate = useNavigate();

  const load = () => {
    Promise.all([
      api.getRazorpayEvents(),
      api.getRazorpayConnection(),
      api.getRazorpayStatus(),
    ])
      .then(([ev, conn, st]) => { setEvents(ev.events); setConnection(conn); setStatus(st); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };
  useEffect(() => { load(); }, []);

  const handleSim = async () => {
    setSimulating(true); setSimResult(null);
    try {
      const r = await api.simulateWebhook({ event_type: simType, amount: parseInt(simAmount) || 100000 });
      setSimResult(`✓ Event ${r.event_id} stored (${r.event_type}) — LOCAL SIMULATOR`);
      load();
    } catch (e) { setSimResult(`✗ ${e instanceof Error ? e.message : 'Failed'}`); }
    finally { setSimulating(false); }
  };

  const handleProcess = async (eventId: string) => {
    setProcessing(eventId); setProcessResult(null);
    try {
      const r = await api.processRazorpayEvent(eventId);
      if (r.status === 'processed') {
        setProcessResult(`✓ Processed into decision ${r.decision_id} (${formatINR(r.gross_amount)} → ${formatINR(r.final_amount)})`);
        load();
      } else {
        setProcessResult(`✗ Processing failed (status: ${r.status})`);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Processing failed';
      setProcessResult(`✗ ${msg}`);
    } finally { setProcessing(null); }
  };

  const handleSync = async (syncType: 'orders' | 'payments' | 'settlements') => {
    setSyncing(syncType); setSyncResult(null);
    try {
      const r = await api.syncRazorpay(syncType);
      setSyncResult(`Sync ${syncType}: ${r.records_synced} synced, ${r.records_failed} failed (${r.duration_ms}ms)`);
      load();
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Sync failed';
      setSyncResult(`Sync failed: ${msg}`);
    } finally { setSyncing(null); }
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
        <p className="text-sm font-semibold text-red-600">Events unavailable</p>
        <p className="mt-1 text-xs text-stone-600">{error}</p>
      </div>
    );
  }

  const inputCls = 'rounded-md border border-[var(--border)] bg-white/50 px-3 py-1.5 text-xs text-stone-800 focus:border-purple-500/40 focus:outline-none';

  return (
    <div className="space-y-6">
      {/* ── Header ── */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-stone-800 sm:text-2xl">Payment Events</h1>
          <p className="mt-1 max-w-xl text-sm leading-relaxed text-stone-600">
            Financial events from the payment gateway, ingested as provenance evidence — each event feeds the pipeline that backs settlement decisions.
          </p>
        </div>
        {/* Integration status badge */}
        {status && (
          <span className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-semibold ${
            status.mode === 'live'
              ? 'border-[#4ADE80]/30 bg-emerald-500/[0.06] text-emerald-600'
              : 'border-purple-300 bg-purple-600/[0.06] text-purple-600'
          }`}>
            <span className={`h-1.5 w-1.5 rounded-full ${status.mode === 'live' ? 'bg-emerald-500' : 'bg-purple-600'}`} />
            {status.mode === 'live' ? 'LIVE MODE' : 'DEMO MODE'}
            {status.key_id_preview && <span className="ml-1 font-mono text-[10px] opacity-70">{status.key_id_preview}</span>}
          </span>
        )}
      </div>

      {/* ── Connection status ── */}
      {connection && (
        <div className="surface flex items-center gap-3 p-4">
          <span className={`h-2 w-2 shrink-0 rounded-full ${connection.configured ? 'bg-emerald-500' : 'bg-purple-600'}`} />
          <p className="text-xs">
            {connection.configured ? (
              <>
                <span className="capitalize text-stone-800">{connection.mode} mode</span>
                <span className="ml-2 font-mono text-stone-500">{connection.key_id_preview}</span>
                {connection.webhook_secret_present && (
                  <span className="ml-2 text-emerald-600">Webhook signature verification enabled</span>
                )}
              </>
            ) : (
              <span className="text-stone-600">Gateway keys not configured — use the simulator to ingest test events.</span>
            )}
          </p>
        </div>
      )}

      {/* ── Data Sync ── */}
      {connection?.configured && (
        <section className="surface p-5">
          <div className="flex items-center gap-2 mb-3">
            <span className="inline-flex items-center rounded-full border border-emerald-300 bg-emerald-500/10 px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-emerald-600">
              Data Sync
            </span>
            <span className="text-[10px] text-stone-500">Fetch live data from Razorpay API</span>
          </div>
          <p className="text-xs text-stone-600 mb-4">Pull real orders, payments, and settlements from your Razorpay account into EntitlementLedger.</p>
          <div className="flex flex-wrap items-end gap-3">
            {(['orders', 'payments', 'settlements'] as const).map((type) => (
              <button
                key={type}
                type="button"
                onClick={() => handleSync(type)}
                disabled={syncing !== null}
                className="rounded-md bg-emerald-600 px-4 py-1.5 text-xs font-semibold text-white btn-smooth hover:bg-emerald-700 disabled:opacity-50"
              >
                {syncing === type ? `Syncing ${type}…` : `Sync ${type}`}
              </button>
            ))}
          </div>
          {syncResult && <p role="status" className="mt-3 text-[11px] text-stone-600">{syncResult}</p>}
        </section>
      )}

      {/* ── Local Webhook Simulator ── */}
      <section className="surface p-5">
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center rounded-full border border-purple-300 bg-purple-600/10 px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-purple-600">
            Local Simulator
          </span>
          <span className="text-[10px] text-stone-500">Not a live webhook</span>
        </div>
        <p className="mt-2 text-xs text-stone-600">Simulates a gateway webhook through the same ingestion pipeline. Creates a real evidence record labeled LOCAL SIMULATOR.</p>
        <div className="mt-4 flex flex-wrap items-end gap-3">
          <div>
            <label htmlFor="sim-type" className="mb-1 block text-[10px] font-medium uppercase tracking-wider text-stone-500">Event type</label>
            <select id="sim-type" value={simType} onChange={(e) => setSimType(e.target.value)} className={inputCls}>
              <option value="payment.captured">payment.captured</option>
              <option value="payment.authorized">payment.authorized</option>
              <option value="payment.failed">payment.failed</option>
              <option value="order.paid">order.paid</option>
              <option value="refund.created">refund.created</option>
              <option value="refund.processed">refund.processed</option>
              <option value="settlement.processed">settlement.processed</option>
            </select>
          </div>
          <div>
            <label htmlFor="sim-amt" className="mb-1 block text-[10px] font-medium uppercase tracking-wider text-stone-500">Amount (paise)</label>
            <input id="sim-amt" type="number" value={simAmount} onChange={(e) => setSimAmount(e.target.value)} className={`${inputCls} amount w-32`} />
          </div>
          <button type="button" onClick={handleSim} disabled={simulating} className="rounded-md bg-purple-600 px-4 py-1.5 text-xs font-semibold text-[#0B0A0F] btn-smooth hover:bg-purple-700 disabled:opacity-50">
            {simulating ? 'Sending…' : 'Simulate event'}
          </button>
        </div>
        {simResult && <p role="status" className="mt-3 text-[11px] text-stone-600">{simResult}</p>}
      </section>

      {/* ── Ingested Events Table ── */}
      <div className="surface overflow-hidden">
        <div className="border-b border-[var(--border)] px-4 py-3">
          <h2 className="section-label">Ingested Events <span className="ml-1 font-normal normal-case text-stone-500">({events.length})</span></h2>
        </div>
        {events.length === 0 ? (
          <div className="px-4 py-12 text-center">
            <p className="text-sm text-stone-600">No events yet.</p>
            <p className="mt-1 text-xs text-stone-500">Use the simulator above to create test payment events.</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[750px] text-sm">
              <thead>
                <tr className="border-b border-white/[0.04] text-left text-[10px] uppercase tracking-wider text-stone-500">
                  <th className="px-4 py-2.5 font-medium">Event</th>
                  <th className="px-4 py-2.5 font-medium">Type</th>
                  <th className="px-4 py-2.5 font-medium">Source</th>
                  <th className="px-4 py-2.5 text-right font-medium">Amount</th>
                  <th className="px-4 py-2.5 font-medium">Order / Payment</th>
                  <th className="px-4 py-2.5 font-medium">Received</th>
                  <th className="px-4 py-2.5 font-medium"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.04]">
                {events.map((ev) => (
                  <tr key={ev.event_id} className="row-hover">
                    <td className="px-4 py-3 font-mono text-[11px] text-stone-600">{ev.event_id.slice(0, 16)}…</td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${EVT_COLORS[ev.event_type] || 'bg-white/60 text-stone-600'}`}>
                        {ev.event_type}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <SourceBadge source={ev.source || 'unknown'} />
                    </td>
                    <td className="amount px-4 py-3 text-right text-xs font-medium text-stone-800">{formatAmount(ev.amount)}</td>
                    <td className="px-4 py-3 font-mono text-[11px] text-stone-500">{ev.order_id || ev.payment_id || '—'}</td>
                    <td className="amount px-4 py-3 text-[11px] text-stone-600">{formatDateTime(ev.received_at)}</td>
                    <td className="px-4 py-3 text-right">
                      <button type="button" onClick={() => setSelectedEvent(selectedEvent?.event_id === ev.event_id ? null : ev)} aria-expanded={selectedEvent?.event_id === ev.event_id} className="text-[11px] font-medium text-purple-600 hover:underline">
                        {selectedEvent?.event_id === ev.event_id ? 'Close' : 'View →'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── Selected event detail ── */}
      {selectedEvent && (
        <section className="surface p-5" aria-live="polite">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-3">
              <h3 className="section-label">Event Provenance</h3>
              <SourceBadge source={selectedEvent.source || 'unknown'} />
              {selectedEvent.verification_status === 'verified' && (
                <span className="inline-flex items-center rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-[9px] font-semibold text-emerald-600">SIG VERIFIED</span>
              )}
            </div>
            <span className="font-mono text-[11px] text-stone-500">{selectedEvent.event_id}</span>
          </div>
          <div className="flex flex-wrap items-center gap-2" aria-label="Provenance pipeline">
            <StepBox label="Raw event" active />
            <span aria-hidden className="text-stone-500">→</span>
            <StepBox label="Evidence" active={!!selectedEvent.linked_decision_id} />
            <span aria-hidden className="text-stone-500">→</span>
            <StepBox label="Decision" active={!!selectedEvent.linked_decision_id} />
            <span aria-hidden className="text-stone-500">→</span>
            <StepBox label="Approval" active={!!selectedEvent.linked_decision_id} />
            <span aria-hidden className="text-stone-500">→</span>
            <StepBox label="Provenance" active={!!selectedEvent.linked_decision_id} />
          </div>

          {/* Process into Ledger button */}
          <div className="mt-4 flex flex-wrap items-center gap-3">
            {!selectedEvent.linked_decision_id ? (
              <button
                type="button"
                onClick={() => handleProcess(selectedEvent.event_id)}
                disabled={processing === selectedEvent.event_id}
                className="rounded-lg bg-purple-600 px-4 py-2 text-sm font-semibold text-[#0B0A0F] btn-smooth hover:bg-purple-700 disabled:opacity-50"
              >
                {processing === selectedEvent.event_id ? 'Processing…' : 'Process into Ledger →'}
              </button>
            ) : (
              <div className="flex items-center gap-3">
                <span className="text-[11px] text-emerald-600">✓ Already processed</span>
                <button
                  type="button"
                  onClick={() => navigate(`/decisions/${selectedEvent.linked_decision_id}`)}
                  className="text-[11px] font-medium text-purple-600 hover:underline"
                >
                  Open decision →
                </button>
              </div>
            )}
          </div>

          {processResult && (
            <div role="status" className="mt-3 rounded-lg border border-[var(--border)] bg-white/50 px-4 py-2.5 text-[11px] text-stone-600">
              {processResult}
            </div>
          )}
          <div className="mt-4 space-y-2">
            <h4 className="section-label">Extracted Evidence Facts</h4>
            {selectedEvent.extracted_facts.length === 0 && (
              <p className="text-xs text-stone-500">No facts extracted yet.</p>
            )}
            {selectedEvent.extracted_facts.map((fact, i) => (
              <div key={i} className="rounded-lg border border-[var(--border)] bg-white/50 p-3 text-[11px]">
                <div className="mb-1 flex items-center gap-2">
                  <span className="font-medium text-stone-800">{fact.fact_type}</span>
                  {fact.amount != null && <span className="amount text-stone-600">· {formatAmount(fact.amount)}</span>}
                </div>
                <p className="text-stone-600">{fact.value}</p>
                <p className="mt-1 italic text-stone-500">"{fact.evidence_quote}"</p>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
