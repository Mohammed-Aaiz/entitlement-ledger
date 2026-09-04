import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import type { AIStatus, VerificationResult } from '../api/types';

function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const onChange = () => setReduced(mq.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);
  return reduced;
}

const CAPABILITIES = [
  {
    label: 'Evidence',
    title: 'Analysis workspace',
    body: 'Bring order records, delivery data and complaints. EntitlementLedger extracts facts and reconstructs what happened.',
    href: '/analyze',
    cta: 'Start an analysis',
    glyph: 'M4 19.5A2.5 2.5 0 0 1 6.5 17H20M4 19.5A2.5 2.5 0 0 0 6.5 22H20V4a2 2 0 0 0-2-2H6.5A2.5 2.5 0 0 0 4 4.5v15Z',
  },
  {
    label: 'Settlement engine',
    title: 'Deterministic money math',
    body: 'Fees, penalties and reserves are computed by deterministic code. The model interprets — it never decides a rupee amount.',
    href: '/decisions',
    cta: 'Browse the ledger',
    glyph: 'M3 3v18h18M7 14l4-4 3 3 5-6',
  },
  {
    label: 'Integrity',
    title: 'Tamper-evident ledger',
    body: 'Every decision references its predecessor by hash. Any post-hoc edit breaks verification from genesis.',
    href: '/audit',
    cta: 'Verify the chain',
    glyph: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Zm-4-10 2.5 2.5L16 10',
  },
];

export default function Home() {
  const reducedMotion = useReducedMotion();
  const [ai, setAi] = useState<AIStatus | null>(null);
  const [verify, setVerify] = useState<VerificationResult | null>(null);
  const [statusError, setStatusError] = useState(false);

  useEffect(() => {
    let alive = true;
    Promise.allSettled([api.getAIStatus(), api.verifyAll()])
      .then(([a, v]) => {
        if (!alive) return;
        if (a.status === 'fulfilled') setAi(a.value);
        if (v.status === 'fulfilled') setVerify(v.value);
        if (a.status === 'rejected' && v.status === 'rejected') setStatusError(true);
      });
    return () => { alive = false; };
  }, []);

  return (
    <div className="relative flex min-h-[82vh] flex-col items-center justify-center px-6 pb-24 pt-10">
      <div className="pointer-events-none mx-auto w-full max-w-3xl text-center">
        {/* Brand eyebrow */}
        <div
          className="crossfade-enter mb-6 inline-flex items-center gap-2 rounded-full border border-black/[0.07] bg-white/70 px-3 py-1 shadow-[0_1px_0_rgba(255,255,255,0.6)_inset,0_4px_16px_-8px_rgba(0,0,0,0.15)] backdrop-blur-xl"
          style={{ animationDelay: reducedMotion ? '0ms' : '60ms' }}
        >
          <span className="h-1.5 w-1.5 rounded-full bg-gradient-to-r from-fuchsia-500 to-violet-500" />
          <span className="text-[10px] font-semibold uppercase tracking-[0.22em] text-stone-500">
            Financial Decision Provenance
          </span>
        </div>

        <h1
          className="crossfade-enter text-4xl font-bold leading-[1.04] tracking-[-0.035em] text-stone-900 sm:text-6xl lg:text-[5.25rem]"
          style={{ animationDelay: reducedMotion ? '0ms' : '140ms' }}
        >
          ENTITLEMENT
          <span className="relative whitespace-nowrap">
            LEDGER
            <span
              aria-hidden="true"
              className="absolute -bottom-1 left-0 h-[3px] w-full rounded-full bg-gradient-to-r from-fuchsia-500 via-purple-500 to-violet-600/40 sm:-bottom-1.5"
            />
          </span>
        </h1>

        <p
          className="crossfade-enter mx-auto mt-7 max-w-2xl text-base font-medium leading-relaxed text-stone-600 sm:text-lg"
          style={{ animationDelay: reducedMotion ? '0ms' : '300ms' }}
        >
          AI interprets evidence.
          <br />
          <span className="font-semibold text-stone-800">Deterministic code calculates money.</span>
          <br />
          The ledger records and verifies the decision.
        </p>

        {/* Actions */}
        <div
          className="crossfade-enter pointer-events-auto mt-9 flex flex-wrap items-center justify-center gap-3"
          style={{ animationDelay: reducedMotion ? '0ms' : '440ms' }}
        >
          <Link
            to="/analyze"
            className="group inline-flex items-center gap-2 rounded-xl bg-stone-900 px-5 py-2.5 text-sm font-semibold text-white shadow-[0_10px_30px_-10px_rgba(28,25,23,0.6)] transition-all duration-200 hover:bg-stone-800 hover:shadow-[0_14px_34px_-10px_rgba(28,25,23,0.55)] btn-smooth"
          >
            Analyze a decision
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" className="transition-transform duration-200 group-hover:translate-x-0.5">
              <path d="M3 8h10M9 4l4 4-4 4" />
            </svg>
          </Link>
          <Link
            to="/finance-control-room"
            className="inline-flex items-center gap-2 rounded-xl border border-black/[0.09] bg-white/75 px-5 py-2.5 text-sm font-semibold text-stone-700 backdrop-blur-xl transition-all duration-200 hover:border-fuchsia-500/40 hover:bg-white hover:text-stone-900 btn-smooth"
          >
            Finance Control Room
          </Link>
        </div>

        {/* Live status strip — real API data, hidden on failure */}
        {!statusError && (ai || verify) && (
          <div
            className="crossfade-enter pointer-events-auto mx-auto mt-8 flex w-fit flex-wrap items-center justify-center gap-2 text-[10px]"
            style={{ animationDelay: reducedMotion ? '0ms' : '580ms' }}
          >
            {ai && (
              <span className="inline-flex items-center gap-1.5 rounded-full border border-black/[0.07] bg-white/65 px-2.5 py-1 font-medium text-stone-500 backdrop-blur-md">
                <span className={`h-1.5 w-1.5 rounded-full ${ai.available ? 'bg-emerald-500' : 'bg-amber-500'}`} />
                {ai.available ? `${ai.provider} · ${ai.model || 'connected'}` : 'AI offline — deterministic path active'}
              </span>
            )}
            {verify && (
              <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-medium backdrop-blur-md ${
                verify.valid
                  ? 'border-emerald-500/20 bg-emerald-500/[0.06] text-emerald-700'
                  : 'border-red-500/25 bg-red-500/[0.06] text-red-700'
              }`}>
                <span className={`h-1.5 w-1.5 rounded-full ${verify.valid ? 'bg-emerald-500' : 'bg-red-500'}`} />
                Ledger {verify.valid ? 'verified' : 'compromised'} · {verify.checked_count} record{verify.checked_count === 1 ? '' : 's'}
              </span>
            )}
          </div>
        )}
      </div>

      {/* Capability cards */}
      <div
        className="crossfade-enter pointer-events-auto mx-auto mt-16 grid w-full max-w-4xl gap-3 sm:grid-cols-3"
        style={{ animationDelay: reducedMotion ? '0ms' : '700ms' }}
      >
        {CAPABILITIES.map((c) => (
          <Link
            key={c.title}
            to={c.href}
            className="group surface relative overflow-hidden p-5 text-left card-smooth hover:-translate-y-0.5 hover:border-fuchsia-500/25"
          >
            <span
              aria-hidden="true"
              className="pointer-events-none absolute -right-8 -top-10 h-28 w-28 rounded-full bg-gradient-to-br from-fuchsia-500/10 to-violet-500/0 opacity-0 blur-2xl transition-opacity duration-500 group-hover:opacity-100"
            />
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-fuchsia-500/12 to-violet-500/12 text-stone-600">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <path d={c.glyph} />
              </svg>
            </div>
            <p className="mt-3 text-[9px] font-bold uppercase tracking-[0.18em] text-stone-400">{c.label}</p>
            <h3 className="mt-1 text-[15px] font-semibold tracking-tight text-stone-900">{c.title}</h3>
            <p className="mt-1.5 text-xs leading-relaxed text-stone-500">{c.body}</p>
            <span className="mt-3 inline-flex items-center gap-1 text-[11px] font-semibold text-violet-600 transition-colors group-hover:text-fuchsia-600">
              {c.cta}
              <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" className="transition-transform duration-200 group-hover:translate-x-0.5">
                <path d="M3 8h10M9 4l4 4-4 4" />
              </svg>
            </span>
          </Link>
        ))}
      </div>
    </div>
  );
}
