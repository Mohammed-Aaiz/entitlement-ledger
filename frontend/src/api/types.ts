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
