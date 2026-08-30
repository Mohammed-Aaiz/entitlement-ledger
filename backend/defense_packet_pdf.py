"""PDF generation for Decision Defense Packets.

Generates a real, auditable PDF document from the decision defense packet data.
The PDF contains: decision details, financial breakdown, evidence, policy clauses,
approval information, hash chain verification, and audit trail.

Uses ReportLab for production PDF generation.
"""
from __future__ import annotations

import io
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def generate_defense_packet_pdf(defense_packet: dict, audit_trail: list[dict] = None) -> bytes:
    """Generate a PDF defense packet from the defense packet dict.

    Args:
        defense_packet: The defense packet dict from the API endpoint.
        audit_trail: Optional list of audit log entries for this decision.

    Returns:
        PDF file as bytes.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch, mm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        PageBreak, HRFlowable,
    )

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=20 * mm, leftMargin=20 * mm,
        topMargin=25 * mm, bottomMargin=25 * mm,
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name='TitleMain',
        parent=styles['Title'],
        fontSize=18,
        spaceAfter=12,
        textColor=colors.HexColor('#1a1a2e'),
    ))
    styles.add(ParagraphStyle(
        name='SectionHeader',
        parent=styles['Heading2'],
        fontSize=13,
        spaceBefore=16,
        spaceAfter=8,
        textColor=colors.HexColor('#2d3436'),
        borderWidth=0,
    ))
    styles.add(ParagraphStyle(
        name='BodySmall',
        parent=styles['BodyText'],
        fontSize=9,
        leading=13,
        spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        name='Monospace',
        parent=styles['Code'],
        fontSize=8,
        leading=11,
        backColor=colors.HexColor('#f5f6fa'),
    ))

    elements = []

    # ── Header ──
    decision = defense_packet.get("decision", {})
    elements.append(Paragraph("DECISION DEFENSE PACKET", styles['TitleMain']))
    elements.append(Paragraph(
        f"EntitlementLedger · Decision ID: <b>{decision.get('decision_id', 'N/A')}</b>",
        styles['BodySmall'],
    ))
    elements.append(Paragraph(
        f"Generated: {__import__('datetime').datetime.utcnow().isoformat()}Z · CONFIDENTIAL",
        styles['BodySmall'],
    ))
    elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#dfe6e9')))
    elements.append(Spacer(1, 8))

    # ── 1. Decision Summary ──
    elements.append(Paragraph("1. DECISION SUMMARY", styles['SectionHeader']))
    summary_data = [
        ["Field", "Value"],
        ["Decision ID", decision.get("decision_id", "N/A")],
        ["Entity", f"{decision.get('entity_type', '')} / {decision.get('entity_id', '')}"],
        ["Status", decision.get("status", "N/A")],
        ["Created", decision.get("created_at", "N/A")],
        ["Approver", decision.get("approver_id", "N/A")],
        ["Approved At", decision.get("approved_at") or "N/A"],
    ]
    summary_table = Table(summary_data, colWidths=[120, 340])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2d3436')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#dfe6e9')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 12))

    # ── 2. Financial Breakdown ──
    elements.append(Paragraph("2. FINANCIAL BREAKDOWN", styles['SectionHeader']))
    breakdown = defense_packet.get("financial_breakdown", {})
    line_items = decision.get("line_items", [])

    fin_data = [["Item", "Type", "Amount (INR)", "Policy Clause"]]
    for item in line_items:
        if isinstance(item, dict):
            fin_data.append([
                item.get("label", ""),
                item.get("type", ""),
                f"₹{item.get('amount', 0):,}",
                item.get("policy_clause_id", ""),
            ])
    fin_data.append(["", "", "", ""])
    fin_data.append([
        "Gross Amount", "", f"₹{breakdown.get('gross_amount', 0):,}", ""
    ])
    fin_data.append([
        "Total Deductions", "", f"₹{breakdown.get('total_deductions', 0):,}", ""
    ])
    fin_data.append([
        "Final Amount", "", f"₹{breakdown.get('final_amount', 0):,}", ""
    ])

    fin_table = Table(fin_data, colWidths=[160, 70, 110, 120])
    fin_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2d3436')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#dfe6e9')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('FONTNAME', (0, -3), (-1, -1), 'Helvetica-Bold'),
    ]))
    elements.append(fin_table)
    elements.append(Spacer(1, 12))

    # Validation
    validation = breakdown.get("validation", {})
    if validation:
        valid_status = "✓ VALID" if validation.get("valid") else "✗ INVALID"
        elements.append(Paragraph(
            f"Calculation Validation: <b>{valid_status}</b> "
            f"(expected: ₹{validation.get('expected_final', 0):,}, "
            f"calculated: ₹{validation.get('calculated_final', 0):,})",
            styles['BodySmall'],
        ))
        elements.append(Spacer(1, 8))

    # ── 3. Evidence ──
    elements.append(Paragraph("3. EVIDENCE", styles['SectionHeader']))
    evidence_list = defense_packet.get("evidence", [])
    if evidence_list:
        for ev in evidence_list:
            ev_id = ev.get("evidence_id", "N/A")
            source = ev.get("source_type", "N/A")
            facts = ev.get("extracted_facts", [])

            elements.append(Paragraph(
                f"<b>{ev_id}</b> (source: {source})",
                styles['BodySmall'],
            ))

            if facts:
                for fact in facts[:10]:  # Limit facts in PDF
                    if isinstance(fact, dict):
                        fact_text = fact.get("fact", fact.get("value", str(fact)))
                        confidence = fact.get("confidence", "")
                        conf_str = f" [{confidence:.0%}]" if isinstance(confidence, (int, float)) else ""
                        elements.append(Paragraph(f"  • {fact_text}{conf_str}", styles['BodySmall']))
            elements.append(Spacer(1, 4))
    else:
        elements.append(Paragraph("No evidence linked to this decision.", styles['BodySmall']))

    elements.append(Spacer(1, 8))

    # ── 4. Applicable Policies ──
    elements.append(Paragraph("4. APPLICABLE POLICIES", styles['SectionHeader']))
    policies = defense_packet.get("policies", [])
    if policies:
        for pol in policies:
            elements.append(Paragraph(
                f"<b>{pol.get('policy_id', '')}</b> (v{pol.get('version', '')}, "
                f"effective: {pol.get('effective_date', '')})",
                styles['BodySmall'],
            ))
            clause = pol.get("clause_text", "")
            if clause:
                # Wrap long clause text
                elements.append(Paragraph(
                    f"<i>{clause[:500]}{'...' if len(clause) > 500 else ''}</i>",
                    styles['BodySmall'],
                ))
            elements.append(Spacer(1, 4))
    else:
        elements.append(Paragraph("No policies referenced.", styles['BodySmall']))

    elements.append(Spacer(1, 8))

    # ── 5. AI Analysis (if present) ──
    model_output = decision.get("model_output", {})
    if model_output and isinstance(model_output, dict):
        elements.append(Paragraph("5. AI ANALYSIS", styles['SectionHeader']))
        classification = model_output.get("classification", "N/A")
        confidence = model_output.get("confidence", "N/A")
        reasoning = model_output.get("reasoning_summary", "N/A")

        elements.append(Paragraph(f"Classification: <b>{classification}</b>", styles['BodySmall']))
        if isinstance(confidence, (int, float)):
            elements.append(Paragraph(f"Confidence: <b>{confidence:.0%}</b>", styles['BodySmall']))
        elements.append(Paragraph(f"Summary: {reasoning}", styles['BodySmall']))

        claims = model_output.get("claims", [])
        if claims:
            elements.append(Paragraph("Claims:", styles['BodySmall']))
            for claim in claims:
                if isinstance(claim, dict):
                    elements.append(Paragraph(
                        f"  • {claim.get('claim_type', 'N/A')} → "
                        f"Policy: {claim.get('policy_clause_id', 'N/A')} "
                        f"(evidence: {', '.join(claim.get('evidence_ids', []))})",
                        styles['BodySmall'],
                    ))
        elements.append(Spacer(1, 8))

    # ── 6. Integrity Verification ──
    integrity = defense_packet.get("integrity", {})
    if integrity:
        elements.append(Paragraph("6. HASH CHAIN INTEGRITY", styles['SectionHeader']))
        valid = integrity.get("valid", False)
        checked = integrity.get("checked_count", 0)
        break_at = integrity.get("break_at")

        status_text = "✓ INTEGRITY VERIFIED" if valid else "✗ INTEGRITY COMPROMISED"
        elements.append(Paragraph(
            f"<b>Status: {status_text}</b>",
            styles['BodySmall'],
        ))
        elements.append(Paragraph(f"Decisions checked: {checked}", styles['BodySmall']))
        if break_at:
            elements.append(Paragraph(
                f"<font color='red'>Chain broken at: {break_at}</font>",
                styles['BodySmall'],
            ))

        elements.append(Paragraph(
            f"Decision Hash: <font size=7>{decision.get('decision_hash', 'N/A')}</font>",
            styles['BodySmall'],
        ))
        elements.append(Paragraph(
            f"Previous Hash: <font size=7>{decision.get('prev_decision_hash', 'N/A')}</font>",
            styles['BodySmall'],
        ))
        elements.append(Spacer(1, 8))

    # ── 7. Audit Trail ──
    if audit_trail:
        elements.append(Paragraph("7. AUDIT TRAIL", styles['SectionHeader']))
        audit_data = [["Timestamp", "Action", "Actor", "Details"]]
        for entry in audit_trail[:20]:  # Limit to 20 entries
            details = entry.get("details", "{}")
            if isinstance(details, str):
                try:
                    details_dict = json.loads(details)
                    details_str = ", ".join(f"{k}: {v}" for k, v in list(details_dict.items())[:3])
                except (json.JSONDecodeError, TypeError):
                    details_str = details[:80]
            else:
                details_str = str(details)[:80]

            audit_data.append([
                entry.get("created_at", "")[:19],
                entry.get("action", ""),
                entry.get("user_id", "")[:20],
                details_str[:60],
            ])

        if len(audit_data) > 1:
            audit_table = Table(audit_data, colWidths=[120, 110, 80, 150])
            audit_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2d3436')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTSIZE', (0, 0), (-1, -1), 7),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#dfe6e9')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
                ('LEFTPADDING', (0, 0), (-1, -1), 4),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ]))
            elements.append(audit_table)

    # ── Footer ──
    elements.append(Spacer(1, 24))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#dfe6e9')))
    elements.append(Paragraph(
        "This document was generated by EntitlementLedger — Financial Decision Provenance System. "
        "All hashes and verification results are computed from the production database. "
        "This document is confidential and intended for authorized recipients only.",
        styles['BodySmall'],
    ))

    # Build PDF
    doc.build(elements)
    pdf_bytes = buffer.getvalue()
    buffer.close()

    logger.info(
        "Defense packet PDF generated: %d bytes for decision %s",
        len(pdf_bytes), decision.get("decision_id", "unknown"),
    )
    return pdf_bytes
