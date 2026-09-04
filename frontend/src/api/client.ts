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
} from './types';

const BASE = '/api';

function getAuthHeaders(): Record<string, string> {
  const token = localStorage.getItem('el_token');
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function fetchJSON<T>(url: string): Promise<T> {
  const res = await fetch(`${BASE}${url}`, { headers: getAuthHeaders() });
  if (res.status === 401) {
    localStorage.removeItem('el_token');
    window.location.href = '/login';
    throw new Error('Unauthorized');
  }
  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

async function postJSON<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${url}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify(body),
  });
  if (res.status === 401) {
    localStorage.removeItem('el_token');
    window.location.href = '/login';
    throw new Error('Unauthorized');
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: `API error: ${res.status}` }));
    throw new Error(err.detail || `API error: ${res.status}`);
  }
  return res.json();
}

export const api = {
  // Dashboard
  getStats: () => fetchJSON<DashboardStats>('/stats'),

  // Decisions
  getDecisions: () => fetchJSON<{ items: Decision[]; total: number; page: number; page_size: number; has_more: boolean }>('/decisions'),
  getDecision: (id: string) => fetchJSON<Decision>(`/decisions/${id}`),
  getDecisionEvidence: (id: string) => fetchJSON<Evidence[]>(`/decisions/${id}/evidence`),
  verifyDecision: (id: string) => fetchJSON<VerificationResult>(`/decisions/${id}/verify`),
  verifyAll: () => fetchJSON<VerificationResult>('/decisions/verify-all'),
  getDefensePacket: (id: string) => fetchJSON<DefensePacket>(`/decisions/${id}/defense-packet`),

  // Approval
  approveDecision: (id: string, approverId: string) =>
    postJSON(`/decisions/${id}/approve`, { approver_id: approverId }),
  rejectDecision: (id: string, approverId: string, reason: string) =>
    postJSON(`/decisions/${id}/reject`, { approver_id: approverId, reason }),

  // Sellers
  getSellerDecisions: (entityId: string) =>
    fetchJSON<SellerDecisions>(`/sellers/${entityId}/decisions`),

  // Evidence
  getEvidence: (id: string) => fetchJSON<Evidence>(`/evidence/${id}`),

  // Policies
  getPolicies: () => fetchJSON<Policy[]>('/policies'),

  // Scenarios
  getScenarios: () => fetchJSON<Scenario[]>('/scenarios'),
  runScenario: (id: string) => postJSON(`/scenarios/${id}/run`, {}),

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
  }) => postJSON<AnalyzeResult>('/decisions/analyze', data),

  // AI Status
  getAIStatus: () => fetchJSON<AIStatus>('/ai/status'),

  // Razorpay
  getRazorpayEvents: () => fetchJSON<{ events: RazorpayEvent[]; total: number }>('/razorpay/events'),
  getRazorpayEvent: (id: string) => fetchJSON<RazorpayEvent>(`/razorpay/events/${id}`),
  getRazorpayConnection: () => fetchJSON<RazorpayConnectionInfo>('/razorpay/connection'),
  getRazorpayStatus: () => fetchJSON<RazorpayStatus>('/razorpay/status'),
  processRazorpayEvent: (eventId: string) =>
    postJSON<{ status: string; decision_id: string; gross_amount: number; final_amount: number; evidence_id: string }>(`/razorpay/events/${eventId}/process`, {}),
  simulateWebhook: (data: {
    event_type?: string;
    amount?: number;
    order_id?: string;
    payment_id?: string;
    status?: string;
  }) => postJSON<{ status: string; event_id: string; event_type: string; note: string }>('/webhooks/razorpay/simulate', data),

  // Sync
  syncRazorpay: (syncType: 'orders' | 'payments' | 'settlements') =>
    postJSON<RazorpaySyncResult>(`/razorpay/sync/${syncType}`, {}),
  getSyncHistory: () =>
    fetchJSON<{ syncs: SyncHistoryEntry[] }>('/razorpay/sync/history'),
  getSyncedData: (dataType: 'orders' | 'payments' | 'settlements') =>
    fetchJSON<{ count: number; items: SyncedRazorpayRecord[] }>(`/razorpay/synced/${dataType}`),

  // Reconciliation / Finance Controller
  runReconciliation: (records: ReconciliationRecordInput[], useAi = false, source = 'batch') =>
    postJSON<ReconciliationRun>('/reconciliation/run', { records, use_ai: useAi, source }),
  runDemoReconciliation: (count = 100) =>
    postJSON<ReconciliationRun>(`/reconciliation/run/demo?count=${count}`, {}),
  runRazorpayReconciliation: (useAi = false) =>
    postJSON<ReconciliationRun>(`/reconciliation/run/razorpay?use_ai=${useAi}`, {}),
  getReconciliationRuns: (limit = 20) =>
    fetchJSON<{ runs: ReconciliationRun[]; total: number }>(`/reconciliation/runs?limit=${limit}`),
  getReconciliationRun: (runId: string) =>
    fetchJSON<ReconciliationRun>(`/reconciliation/runs/${runId}`),
  getRunExceptions: (runId: string) =>
    fetchJSON<{ exceptions: ReconciliationCase[]; total: number }>(`/reconciliation/runs/${runId}/exceptions`),
  getReconciliationCase: (caseId: string) =>
    fetchJSON<ReconciliationCase>(`/reconciliation/cases/${caseId}`),
  getReconciliationDashboard: () =>
    fetchJSON<ReconciliationDashboard>('/reconciliation/dashboard'),
};
