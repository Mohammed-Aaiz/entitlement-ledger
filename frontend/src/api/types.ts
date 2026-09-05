export interface LineItem {
  label: string;
  amount: number;
  type: 'fee' | 'deduction' | 'credit';
  policy_clause_id: string | null;
  evidence_ids: string[];
}

export interface Decision {
  decision_id: string;
  entity_type: string;
  entity_id: string;
  gross_amount: number;
  line_items: LineItem[];
  final_amount: number;
  policy_version_id: string;
  approver_id: string;
  approved_at: string;
  model_output: Record<string, unknown>;
  prev_decision_hash: string;
  decision_hash: string;
  created_at: string;
  status: 'DRAFT' | 'REVIEW_REQUIRED' | 'APPROVED' | 'REJECTED';
}

export interface Evidence {
  evidence_id: string;
  source_type: string;
  raw_content: string;
  extracted_facts: Array<{ fact: string; confidence: number }>;
  linked_decision_ids: string[];
}

export interface Policy {
  policy_id: string;
  version: string;
  clause_text: string;
  effective_date: string;
}

export interface VerificationResult {
  valid: boolean;
  checked_count: number;
  break_at: string | null;
}

export interface Scenario {
  scenario_id: string;
  name: string;
  description: string;
  status: string;
}

export interface DefensePacket {
  decision: Decision;
  financial_breakdown: {
    gross_amount: number;
    total_deductions: number;
    final_amount: number;
    validation: Record<string, unknown>;
  };
  evidence: Evidence[];
  policies: Policy[];
  approver_id: string;
  approved_at: string;
  integrity: VerificationResult;
}

export interface DashboardStats {
  total_decisions: number;
  verified_decisions: number;
  flagged_decisions: number;
  total_gross_entitlement: number;
  total_final_amount: number;
  total_adjustments: number;
}

export interface AIStatus {
  available: boolean;
  provider: string;
  model: string | null;
  requires_api_key?: boolean;
  description?: string;
  error?: string;
}

export interface RazorpayEvent {
  event_id: string;
  event_type: string;
  source: string;
  verification_status: string;
  razorpay_entity_type: string;
  razorpay_entity_id: string;
  payment_id: string;
  order_id: string;
  amount: number | null;
  currency: string;
  status: string;
  event_timestamp: string | null;
  received_at: string;
  extracted_facts: Array<{
    fact_type: string;
    value: string;
    amount: number | null;
    date: string | null;
    evidence_quote: string;
  }>;
  linked_decision_id: string | null;
  event_family?: string;
  known_event?: boolean;
  financial_relevance?: boolean;
  affects_reconciliation?: boolean;
  context_risk_only?: boolean;
}

export interface RazorpayStatus {
  configured: boolean;
  mode: string;
  webhook_configured: boolean;
  key_id_preview: string | null;
}

export interface RazorpayConnectionInfo {
  configured: boolean;
  key_id_present: boolean;
  key_id_preview: string | null;
  webhook_secret_present: boolean;
  mode: string;
}

export interface RazorpaySyncResult {
  status: string;
  sync_type: string;
  records_synced: number;
  records_failed: number;
  duration_ms: number;
  errors: string[];
}

export interface SyncedRazorpayRecord {
  id?: string;
  order_id?: string;
  payment_id?: string;
  settlement_id?: string;
  amount: number;
  currency: string;
  status: string;
  last_synced_at: string;
  method?: string;
  captured?: boolean;
  amount_refunded?: number;
}

export interface SyncHistoryEntry {
  sync_id: number;
  tenant_id: string;
  sync_type: string;
  status: string;
  records_synced: number;
  records_failed: number;
  error_message: string | null;
  started_at: string;
  completed_at: string | null;
}

export interface AnalyzeResult {
  status: string;
  decision_id: string;
  decision_status: string;
  gross_amount: number;
  final_amount: number;
  line_items: LineItem[];
  evidence_count: number;
  evidence_ids: string[];
  claims: Array<{ type: string; evidence_ids: string[]; policy_clause_id: string }>;
  decision_hash: string;
  prev_decision_hash: string;
  message: string;
}

export interface SellerDecisions {
  entity_id: string;
  total_decisions: number;
  total_gross_entitlement: number;
  total_final_amount: number;
  total_adjustments: number;
  decisions: Array<{
    decision: Decision;
    verification: VerificationResult;
  }>;
}

export interface ReconciliationException {
  code: string;
  explanation: string;
  involved_record_ids: string[];
  financial_impact: number;
  evidence_refs: string[];
  human_action_required: boolean;
}

export interface ReconciliationRecordInput {
  record_type: 'payment' | 'refund' | 'settlement' | 'fee_tax' | 'adjustment';
  external_id: string;
  amount: number;
  currency?: string;
  status?: string;
  payment_id?: string;
  order_id?: string;
  fee_amount?: number;
  tax_amount?: number;
  adjustment_sign?: 'positive' | 'negative' | '';
  recorded_at?: string | null;
  source?: string;
  raw_evidence_ref?: string;
  payload_hash?: string;
  extra?: Record<string, unknown>;
}

export interface ReconciliationRun {
  run_id: string;
  status: string;
  source: string;
  total_records: number;
  total_cases: number;
  matched: number;
  review_required: number;
  exceptions: number;
  match_rate: number;
  classification_accuracy: number | null;
  calculation_accuracy: number | null;
  false_auto_resolve: number;
  throughput_per_sec: number;
  p50_latency_ms: number;
  p95_latency_ms: number;
  duplicates_detected: number;
  audit_completeness: number;
  errors: string[];
  started_at: string;
  completed_at: string;
}

export interface ReconciliationCase {
  case_id: string;
  payment_id: string;
  run_id: string;
  classification: 'MATCHED' | 'REVIEW_REQUIRED' | 'EXCEPTION';
  expected_amount: number;
  actual_amount: number;
  variance: number;
  exception_codes: string[];
  exceptions: ReconciliationException[];
  ai_status: string;
  ai_invoked: boolean;
  ai_confidence: number | null;
  ai_interpretation: Record<string, unknown>;
  ai_technical_reason: string;
  ai_trigger_reason: string;
  ai_tool_calls: number;
  calculation_trace: {
    captured_amount?: number;
    refund_total?: number;
    fee_total?: number;
    tax_total?: number;
    adjustments?: number;
    expected_settlement?: number;
    actual_settlement?: number | null;
    variance?: number | null;
    currency?: string;
    steps?: Array<{ component: string; sign: string; amount: number; running_total: number; label: string }>;
    formula?: string;
  };
  match_info: Record<string, unknown>;
  decision_id: string;
  explanation: string;
  related_record_ids: string[];
  // Tier 1-7 analysis + typed relationship graph (deterministic, real).
  tiers_applied?: number[];
  tier_findings?: Array<{
    tier: number;
    tier_label: string;
    code: string;
    severity: string;
    explanation: string;
    evidence_refs: string[];
    detail: Record<string, unknown>;
  }>;
  relationships?: Array<{
    source: string;
    relation: string;
    target: string;
    evidence_refs: string[];
  }>;
  created_at: string;
}

export interface ReconciliationDashboard {
  total_runs: number;
  latest_run: ReconciliationRun | null;
  total_cases: number;
  matched: number;
  review_required: number;
  exceptions: number;
  match_rate: number;
  total_variance: number;
  // Real tier distribution across all cases (tier -> case count).
  tier_counts: { [tier: string]: number };
  ai_invoked_cases: number;
  ai_invocation_rate: number;
  deterministic_only_rate: number;
  unresolved_exceptions: ReconciliationCase[];
  false_auto_resolve_risk_cases: ReconciliationCase[];
  ledger_verified: boolean;
}

export interface SupportAnswer {
  answer: string;
  key_points: string[];
  citations: string[];
  insufficient_evidence: boolean;
}

export interface SupportAskResponse {
  status: string;
  mode: string;
  answer: SupportAnswer;
  technical_reason: string;
  latency_ms: number;
  provider: string;
  model: string;
  usage: {
    invocations: number;
    failures: number;
    last_error: string;
    last_latency_ms: number | null;
  };
}

export interface SupportStatus {
  provider: string;
  available: boolean;
  model: string;
  error: string;
  modes: { [mode: string]: string };
  usage: {
    invocations: number;
    failures: number;
    last_error: string;
    last_latency_ms: number | null;
  };
}
