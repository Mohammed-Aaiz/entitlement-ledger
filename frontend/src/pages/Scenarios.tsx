import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import type { Scenario } from '../api/types';
import BorderGlow from '../components/react-bits/BorderGlow';
import { SkeletonCard } from '../App';

type RunState = 'idle' | 'running' | 'completed' | 'error';
interface ScenarioRun { status: RunState; message?: string; decision_id?: string; }

const SCENARIO_META: Record<string, { purpose: string; gross: string; deductions: number; evidence: number; demo: boolean }> = {
  scenario_1: { purpose: 'Demonstrates multi-source decision reconstruction — platform fee, SLA penalty and return reserve all backed by evidence.', gross: '₹1,00,000', deductions: 3, evidence: 4, demo: true },
  scenario_2: { purpose: 'Delivery delay triggers SLA penalty. No returns involved — shows single-penalty pipeline.', gross: '₹80,000', deductions: 2, evidence: 2, demo: false },
  scenario_3: { purpose: "Customer complaint filed but evidence doesn't justify a penalty — platform fee only.", gross: '₹45,000', deductions: 1, evidence: 2, demo: false },
  scenario_4: { purpose: 'Second decision for the same seller — shows decision history on seller profile.', gross: '₹35,000', deductions: 1, evidence: 1, demo: false },
  scenario_5: { purpose: 'Record modified after hashing — breaks the integrity chain. Key demo moment for tamper evidence.', gross: '₹1,00,000', deductions: 3, evidence: 4, demo: false },
};

const STATUS_COLORS: Record<string, string> = {
  completed: 'border-[#4ADE80]/35 bg-[#4ADE80]/10 text-[#4ADE80]',
  pending: 'border-white/10 bg-white/[0.06] text-stone-200',
  failed: 'border-[#F87171]/35 bg-[#F87171]/10 text-[#F87171]',
};

export default function Scenarios() {
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [runs, setRuns] = useState<Record<string, ScenarioRun>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getScenarios().then(setScenarios).catch((e) => setError(e.message)).finally(() => setLoading(false));
  }, []);

  const handleRun = async (id: string) => {
    setRuns((p) => ({ ...p, [id]: { status: 'running' } }));
    try {
      const r = await fetch(`/api/scenarios/${id}/run`, { method: 'POST' });
      const d = await r.json();
      // FastAPI HTTPException wraps detail in {detail: {...}}
      const body = d.detail && typeof d.detail === 'object' ? d.detail : d;
      if (body.status === 'completed') {
        setRuns((p) => ({ ...p, [id]: { status: 'completed', message: body.message, decision_id: body.decision_id } }));
        api.getScenarios().then(setScenarios).catch(() => {});
      } else {
        // Detect Ollama connection failure
        const msg = body.message || body.error || '';
        const isOllamaError = msg.toLowerCase().includes('ollama') || msg.toLowerCase().includes('llm') || msg.toLowerCase().includes('model') || msg.toLowerCase().includes('connection');
        setRuns((p) => ({
          ...p,
          [id]: {
            status: 'error',
            message: isOllamaError
              ? `Can't reach the local model — start Ollama and try again. (${msg})`
              : msg || 'Pipeline unavailable',
          },
        }));
      }
    } catch (e) {
      setRuns((p) => ({
        ...p,
        [id]: {
          status: 'error',
          message: e instanceof Error ? `Can't reach the local model — start Ollama and try again. (${e.message})` : 'Request failed',
        },
      }));
    }
  };

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="h-8 skeleton w-48" />
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{Array.from({ length: 6 }).map((_, i) => <SkeletonCard key={i} rows={4} />)}</div>
      </div>
    );
  }
  if (error) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-500/[0.04] p-5">
        <p className="text-sm font-semibold text-red-600">Scenarios unavailable</p>
        <p className="mt-1 text-xs text-stone-600">{error}</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold tracking-tight text-stone-800 sm:text-2xl">Demo Cases</h1>
        <p className="mt-1 max-w-xl text-sm leading-relaxed text-stone-600">
          Pre-built settlement scenarios for demonstration and testing. Each run executes the full
          evidence → policy → AI reasoning → calculation pipeline and produces a hashed, auditable decision record.
          For real analysis, use{' '}
          <Link to="/analyze" className="text-purple-600 hover:underline">New Decision</Link>.
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {scenarios.map((s) => {
          const run = runs[s.scenario_id];
          const meta = SCENARIO_META[s.scenario_id] || { purpose: s.description, gross: '—', deductions: 0, evidence: 0, demo: false };
          const st = s.status || 'pending';

          return (
            <BorderGlow
              key={s.scenario_id}
              backgroundColor="#120F17"
              borderRadius={12}
              glowRadius={20}
              glowColor={meta.demo ? '42, 65, 55' : '42, 50, 55'}
              glowIntensity={meta.demo ? 0.8 : 0.4}
              colors={meta.demo ? ['#D9A441', '#4B4560', '#0B0A0F'] : ['#7CA5D4', '#2a2533', '#0B0A0F']}
            >
              <div className="p-5 h-full flex flex-col">
                <div className="flex items-start justify-between gap-3">
                  <h2 className="text-sm font-semibold text-stone-100 leading-snug">{s.name}</h2>
                  <span className={`shrink-0 inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${STATUS_COLORS[st] || STATUS_COLORS.pending}`}>
                    {st}
                  </span>
                </div>
                <p className="text-xs text-stone-300/90 mt-2 leading-relaxed flex-1">{meta.purpose}</p>

                {/* Stats */}
                <div className="mt-3 flex flex-wrap gap-2 text-[10px] text-stone-300">
                  <span className="rounded border border-white/10 bg-white/[0.06] px-1.5 py-0.5 amount">Gross {meta.gross}</span>
                  <span className="rounded border border-white/10 bg-white/[0.06] px-1.5 py-0.5">{meta.deductions} deductions</span>
                  <span className="rounded border border-white/10 bg-white/[0.06] px-1.5 py-0.5">{meta.evidence} evidence sources</span>
                </div>

                {/* Run result */}
                {run?.status === 'completed' && (
                  <div className="mt-3 rounded-md border border-[#4ADE80]/30 bg-[#4ADE80]/[0.08] px-3 py-2">
                    <p className="text-[11px] text-[#4ADE80]">✓ Decision created</p>
                    {run.decision_id && <Link to={`/decisions/${run.decision_id}`} className="text-[11px] font-mono text-purple-300 hover:text-purple-200 hover:underline">{run.decision_id}</Link>}
                  </div>
                )}
                {run?.status === 'error' && (
                  <div className="mt-3 rounded-md border border-[#F87171]/30 bg-[#F87171]/[0.08] px-3 py-2">
                    <p className="text-[11px] text-[#F87171]">✗ {run.message}</p>
                  </div>
                )}

                <div className="mt-4">
                  <button
                    type="button"
                    onClick={() => handleRun(s.scenario_id)}
                    disabled={run?.status === 'running'}
                    className="rounded-md bg-purple-600 px-4 py-1.5 text-xs font-semibold text-[#0B0A0F] btn-smooth hover:bg-purple-700 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {run?.status === 'running' ? 'Running…' : st === 'completed' ? 'Re-run' : 'Run Scenario'}
                  </button>
                </div>
              </div>
            </BorderGlow>
          );
        })}
      </div>

      <p className="text-[11px] text-stone-500">
        Scenario execution requires a configured LLM provider. Without one, use the seeded demo decisions on the{' '}
        <Link to="/" className="text-purple-600 hover:underline">Dashboard</Link>.
      </p>
    </div>
  );
}
