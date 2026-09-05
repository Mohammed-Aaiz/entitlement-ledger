import type {
  Decision,
  Evidence,
  Policy,
  VerificationResult,
  Scenario,
  DefensePacket,
  DashboardStats,
  SellerDecisions,
  AIStatus,
  RazorpayEvent,
  RazorpayConnectionInfo,
  RazorpayStatus,
  AnalyzeResult,
  RazorpaySyncResult,
  SyncedRazorpayRecord,
  SyncHistoryEntry,
  ReconciliationRun,
  ReconciliationCase,
  ReconciliationDashboard,
  ReconciliationRecordInput,
  SupportAskResponse,
  SupportStatus,
} from './types';

const BASE = '/api';

/** Structured API error carrying the backend error envelope when present. */
export class ApiError extends Error {
  status: number;
  code: string;
  requestId: string;
  retryable: boolean;
  detail: string;

  constructor(status: number, message: string, code = '', requestId = '', retryable = false) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code || `HTTP_${status}`;
    this.requestId = requestId;
    this.retryable = retryable;
    this.detail = message;
  }
}

function parseErrorBody(payload: unknown, status: number, fallback: string): ApiError {
  try {
    const body = payload as { detail?: string; error?: { code?: string; message?: string; request_id?: string; retryable?: boolean } };
    const message = body?.error?.message || body?.detail || fallback;
    return new ApiError(
      status,
      message,
      body?.error?.code || '',
      body?.error?.request_id || '',
      Boolean(body?.error?.retryable),
    );
  } catch {
    return new ApiError(status, fallback);
  }
}

function getAuthHeaders(): Record<string, string> {
  const token = localStorage.getItem('el_token');
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** New idempotency key for a logical financial action (run POSTs). */
function newIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `key_${Date.now()}_${Math.random().toString(36).slice(2)}`;
}

const MAX_RETRIES = 3;
const BACKOFF_MS = 400;

async function requestJSON<T>(
  url: string,
  opts: { method?: 'GET' | 'POST'; body?: unknown; idempotent?: boolean } = {},
): Promise<T> {
  const { method = 'GET', body, idempotent = false } = opts;
  // A stable key per logical call: internal retries reuse it, so a timed-out
  // reconciliation run POST can never create a duplicate run on the backend.
  const idempotencyKey = idempotent ? newIdempotencyKey() : '';

  let attempt = 0;
  // Intentionally `true` so we always execute at least once.
  while (attempt <= MAX_RETRIES) {
    const headers: Record<string, string> = { ...getAuthHeaders() };
    if (method === 'POST') headers['Content-Type'] = 'application/json';
    if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey;

    let res: Response;
    try {
      res = await fetch(`${BASE}${url}`, {
        method,
        headers,
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
    } catch (err) {
      // Network failure — retryable with backoff when we have attempts left.
      if (attempt < MAX_RETRIES) {
        attempt += 1;
        await new Promise((r) => setTimeout(r, BACKOFF_MS * 2 ** (attempt - 1)));
        continue;
      }
      throw new ApiError(0, 'Network error — could not reach the server. Please retry.', 'NETWORK_ERROR', '', true);
    }

    if (res.status === 401) {
      localStorage.removeItem('el_token');
      window.location.href = '/login';
      throw new ApiError(401, 'Session expired. Please sign in again.', 'UNAUTHORIZED');
    }

    if (!res.ok) {
      const payload = await res.json().catch(() => null);
      const err = parseErrorBody(payload, res.status, `API error: ${res.status}`);
      // Retry transient failures only (and only for safe/idempotent calls).
      if (
        (idempotent || method === 'GET') &&
        err.retryable &&
        attempt < MAX_RETRIES
      ) {
        attempt += 1;
        await new Promise((r) => setTimeout(r, BACKOFF_MS * 2 ** (attempt - 1)));
        continue;
      }
      throw err;
    }
    return res.json() as Promise<T>;
  }
  // Unreachable — loop always returns or throws.
  throw new ApiError(500, 'Request failed after retries', 'RETRY_EXHAUSTED', '', true);
}

export const api = {
  // Dashboard
  getStats: () => requestJSON<DashboardStats>('/stats'),

  // Decisions
  getDecisions: () => requestJSON<{ items: Decision[]; total: number; page: number; page_size: number; has_more: boolean }>('/decisions'),
  getDecision: (id: string) => requestJSON<Decision>(`/decisions/${id}`),
  getDecisionEvidence: (id: string) => requestJSON<Evidence[]>(`/decisions/${id}/evidence`),
  verifyDecision: (id: string) => requestJSON<VerificationResult>(`/decisions/${id}/verify`),
  verifyAll: () => requestJSON<VerificationResult>('/decisions/verify-all'),
  getDefensePacket: (id: string) => requestJSON<DefensePacket>(`/decisions/${id}/defense-packet`),

  // Approval (mutating — no automatic retry)
  approveDecision: (id: string, approverId: string) =>
    requestJSON(`/decisions/${id}/approve`, { method: 'POST', body: { approver_id: approverId } }),
  rejectDecision: (id: string, approverId: string, reason: string) =>
    requestJSON(`/decisions/${id}/reject`, { method: 'POST', body: { approver_id: approverId, reason } }),

  // Sellers
  getSellerDecisions: (entityId: string) =>
    requestJSON<SellerDecisions>(`/sellers/${entityId}/decisions`),

  // Evidence
  getEvidence: (id: string) => requestJSON<Evidence>(`/evidence/${id}`),

  // Policies
  getPolicies: () => requestJSON<Policy[]>('/policies'),

  // Scenarios
  getScenarios: () => requestJSON<Scenario[]>('/scenarios'),
  runScenario: (id: string) => requestJSON(`/scenarios/${id}/run`, { method: 'POST', body: {} }),

  // Analyze Decision
  analyzeDecision: (data: {
    entity_type?: string;
    entity_id: string;
    gross_amount: number;
    evidence_items: Array<{ source_type: string; raw_content: string }>;
    has_sla_breach?: boolean;
    sla_penalty_amount?: number;
    has_returns?: boolean;
    return_reserve_amount?: number;
    approver_id?: string;
  }) => requestJSON<AnalyzeResult>('/decisions/analyze', { method: 'POST', body: data }),

  // AI Status
  getAIStatus: () => requestJSON<AIStatus>('/ai/status'),

  // Razorpay
  getRazorpayEvents: () => requestJSON<{ events: RazorpayEvent[]; total: number }>('/razorpay/events'),
  getRazorpayEvent: (id: string) => requestJSON<RazorpayEvent>(`/razorpay/events/${id}`),
  getRazorpayConnection: () => requestJSON<RazorpayConnectionInfo>('/razorpay/connection'),
  getRazorpayStatus: () => requestJSON<RazorpayStatus>('/razorpay/status'),
  processRazorpayEvent: (eventId: string) =>
    requestJSON<{ status: string; decision_id: string; gross_amount: number; final_amount: number; evidence_id: string }>(`/razorpay/events/${eventId}/process`, { method: 'POST', body: {} }),
  simulateWebhook: (data: {
    event_type?: string;
    amount?: number;
    order_id?: string;
    payment_id?: string;
    status?: string;
  }) => requestJSON<{ status: string; event_id: string; event_type: string; note: string }>('/webhooks/razorpay/simulate', { method: 'POST', body: data }),

  // Sync
  syncRazorpay: (syncType: 'orders' | 'payments' | 'settlements') =>
    requestJSON<RazorpaySyncResult>(`/razorpay/sync/${syncType}`, { method: 'POST', body: {} }),
  getSyncHistory: () =>
    requestJSON<{ syncs: SyncHistoryEntry[] }>('/razorpay/sync/history'),
  getSyncedData: (dataType: 'orders' | 'payments' | 'settlements') =>
    requestJSON<{ count: number; items: SyncedRazorpayRecord[] }>(`/razorpay/synced/${dataType}`),

  // Reconciliation / Finance Controller
  // The run POSTs are idempotent: a client retry reuses one Idempotency-Key,
  // and the backend returns the ORIGINAL run instead of duplicating it.
  runReconciliation: (records: ReconciliationRecordInput[], useAi = false, source = 'batch') =>
    requestJSON<ReconciliationRun>('/reconciliation/run', { method: 'POST', body: { records, use_ai: useAi, source }, idempotent: true }),
  runDemoReconciliation: (count = 100) =>
    requestJSON<ReconciliationRun>(`/reconciliation/run/demo?count=${count}`, { method: 'POST', body: {}, idempotent: true }),
  runRazorpayReconciliation: (useAi = false) =>
    requestJSON<ReconciliationRun>(`/reconciliation/run/razorpay?use_ai=${useAi}`, { method: 'POST', body: {}, idempotent: true }),
  getReconciliationRuns: (limit = 20) =>
    requestJSON<{ runs: ReconciliationRun[]; total: number }>(`/reconciliation/runs?limit=${limit}`),
  getReconciliationRun: (runId: string) =>
    requestJSON<ReconciliationRun>(`/reconciliation/runs/${runId}`),
  getRunExceptions: (runId: string) =>
    requestJSON<{ exceptions: ReconciliationCase[]; total: number }>(`/reconciliation/runs/${runId}/exceptions`),
  getReconciliationCase: (caseId: string) =>
    requestJSON<ReconciliationCase>(`/reconciliation/cases/${caseId}`),
  getReconciliationDashboard: () =>
    requestJSON<ReconciliationDashboard>('/reconciliation/dashboard'),

  // Gemini Finance Support Center
  getSupportStatus: () =>
    requestJSON<SupportStatus>('/reconciliation/support/status'),
  getSupportModes: () =>
    requestJSON<{ modes: { [mode: string]: string } }>('/reconciliation/support/modes'),
  askSupportCenter: (data: { question: string; mode: string; run_id?: string; case_id?: string }) =>
    requestJSON<SupportAskResponse>('/reconciliation/support/ask', { method: 'POST', body: data }),
};

export { MAX_RETRIES };
