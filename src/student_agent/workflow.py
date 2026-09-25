from __future__ import annotations

from typing import Any

from .mcp_gateway import EvidenceGateway
from .order_agent import check_order_and_delivery
from .payment_agent import check_payment_and_refund
from .trace import TraceWriter


async def solve_case(
    case: dict[str, Any], gateway: EvidenceGateway, trace: TraceWriter
) -> dict[str, Any]:
    """Execute the multi-agent investigation workflow for a single case.

    Lifecycle:
    1. Coordinator emits 'task_assigned' to order-agent.
    2. Order & Logistics Specialist investigates order status and delivery dates.
    3. Coordinator/Order Specialist emits 'handoff' to payment-agent.
    4. Payment & Policy Specialist checks payments, detects duplicate charges,
       and evaluates refund policies.
    5. Verifier validates consistency, calibrates confidence, and formats the output
       strictly according to 'day09-l3a-output-v2.schema.json'.
    """
    case_id: str = case["case_id"]
    context = case.get("context") or {}
    order_id: str | None = context.get("order_id") or case.get("order_id")

    # 1. Coordinator assigns investigation task
    trace.emit(
        case_id=case_id,
        event_type="task_assigned",
        actor="coordinator",
        target="order-agent",
    )

    # 2. Order & Logistics Specialist runs investigation
    order_res = await check_order_and_delivery(case_id, order_id, gateway, trace)

    # 3. A2A Handoff to Payment Specialist
    trace.emit(
        case_id=case_id,
        event_type="handoff",
        actor="order-agent",
        target="payment-agent",
    )

    # 4. Payment Specialist investigates transactions and refund policies
    pay_res = await check_payment_and_refund(case_id, order_id or "", gateway, trace)

    # 5. Semantic assessment and decision synthesis
    issue = order_res.get("issue", "no_issue")
    total_paid: float = float(pay_res.get("total_paid", 0.0))
    is_duplicate: bool = bool(pay_res.get("is_duplicate", False))

    if issue in [
        "canceled_order_paid",
        "unavailable_order_paid",
        "late_delivery_seller",
        "late_delivery_logistics",
    ]:
        primary_issue = issue
        case_status = "action_required"
        refund_brl = total_paid
        responsible_type = order_res.get("responsible", "platform")
        responsible_id = order_res.get("responsible_party_id")
    elif is_duplicate:
        primary_issue = "duplicate_charge"
        case_status = "action_required"
        refund_brl = round(total_paid / 2.0, 2)
        responsible_type = "payment_provider"
        responsible_id = None
    elif issue == "insufficient_evidence":
        primary_issue = "insufficient_evidence"
        case_status = "needs_investigation"
        refund_brl = 0.0
        responsible_type = "unknown"
        responsible_id = None
    else:
        primary_issue = "unsupported_claim"
        case_status = "no_action"
        refund_brl = 0.0
        responsible_type = "customer"
        responsible_id = None

    # Collect unique authoritative evidence references
    all_evidence: list[str] = []
    for ref in order_res.get("ev", []) + pay_res.get("ev", []):
        if ref and ref.startswith("ev_") and ref not in all_evidence:
            all_evidence.append(ref)

    # Ensure refund consistency
    if case_status == "no_action":
        refund_brl = 0.0
        refund_lines: list[dict[str, Any]] = []
        resolution_actions = ["close_case"]
    else:
        refund_lines = (
            [
                {
                    "reason_code": primary_issue,
                    "amount_brl": round(float(refund_brl), 2),
                    "entity_id": order_id,
                }
            ]
            if refund_brl > 0
            else []
        )
        resolution_actions = ["issue_refund"] if refund_brl > 0 else ["investigate_manually"]

    # Entity aggregation
    order_ids = [order_id] if order_id else []
    item_ids = list(dict.fromkeys(order_res.get("item_ids", [])))
    seller_ids = list(dict.fromkeys(order_res.get("seller_ids", [])))
    shipment_ids = list(dict.fromkeys(order_res.get("shipment_ids", [])))
    payment_references = list(dict.fromkeys(pay_res.get("payment_refs", [])))

    # Calibrate confidence based on evidence presence
    if primary_issue == "insufficient_evidence":
        confidence = 0.35
    elif len(all_evidence) >= 2:
        confidence = 0.95
    elif len(all_evidence) == 1:
        confidence = 0.85
    else:
        confidence = 0.50

    # 6. Verifier completes validation
    trace.emit(
        case_id=case_id,
        event_type="verification_completed",
        actor="verifier",
        attributes={
            "primary_issue": primary_issue,
            "case_status": case_status,
            "evidence_count": len(all_evidence),
        },
    )

    # 7. Construct final contract-compliant payload
    output: dict[str, Any] = {
        "schema_version": "day09-l3a-output-v2",
        "case_id": case_id,
        "assessment": {
            "primary_issue": primary_issue,
            "case_status": case_status,
            "confidence": round(confidence, 2),
        },
        "affected_entities": {
            "order_ids": order_ids,
            "item_ids": item_ids,
            "seller_ids": seller_ids,
            "payment_references": payment_references,
            "shipment_ids": shipment_ids,
        },
        "root_cause_analysis": {
            "ranked_causes": [{"cause_code": primary_issue.upper(), "rank": 1}],
            "responsible_parties": [
                {
                    "party_type": responsible_type,
                    "party_id": responsible_id,
                }
            ],
        },
        "evidence_refs": all_evidence,
        "data_conflicts": [],
        "financial_resolution": {
            "currency": "BRL",
            "recommended_refund_brl": round(float(refund_brl), 2),
            "refund_lines": refund_lines,
        },
        "resolution_actions": resolution_actions,
    }

    return output
