"""Reconciliation domain for EntitlementLedger.

The finance-controller loop:
    Razorpay payment/refund/settlement data
    → normalize
    → match related records
    → calculate expected settlement
    → compare with actual settlement
    → interpret ambiguous evidence using AI (optional, failure-safe)
    → deterministic decision gate
    → matched / review / exception
    → record complete decision in ledger
    → expose results through a finance control room

The deterministic finance engine is the SOLE authority on monetary
amounts.  The AI controller may interpret evidence and explain
discrepancies, but never calculates or overrides money.
"""