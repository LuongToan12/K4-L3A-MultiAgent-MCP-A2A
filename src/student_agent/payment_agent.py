from __future__ import annotations

from typing import Any


async def check_payment_and_refund(
    case_id: str, order_id: str, gateway: Any, trace: Any
) -> dict[str, Any]:
    """Check payment information and assess refund eligibility.

    Interacts with the MCP Gateway 'get_payment' tool to inspect payment
    records, detects duplicate charges, and calculates total paid amount.
    """
    pay_res = await gateway.call("get_payment", case_id=case_id, order_id=order_id)
    ev_pay = pay_res["evidence_ref"]
    pay_data = pay_res.get("data") or {}

    # Hard Gate Requirement: tool_result_consumed event
    trace.emit(
        case_id=case_id,
        event_type="tool_result_consumed",
        actor="payment-agent",
        tool_name="get_payment",
        evidence_refs=[ev_pay],
    )

    payments = pay_data.get("payments") or []
    total_paid = sum(float(p.get("payment_value") or 0.0) for p in payments)

    # Check for duplicate charge (customer charged multiple times for the exact same amount)
    values = [round(float(p.get("payment_value") or 0.0), 2) for p in payments]
    positive_values = [v for v in values if v > 0]
    is_duplicate = len(positive_values) > 1 and len(positive_values) != len(set(positive_values))

    # Payment reference identification
    payment_refs = list(
        dict.fromkeys(
            str(p.get("payment_sequential") or p.get("payment_id") or idx + 1)
            for idx, p in enumerate(payments)
        )
    )

    # Hard Gate Requirement: policy_decided event
    trace.emit(
        case_id=case_id,
        event_type="policy_decided",
        actor="policy-agent",
        decision_code="REFUND_POLICY_EVALUATED",
    )

    return {
        "total_paid": round(total_paid, 2),
        "is_duplicate": is_duplicate,
        "payment_refs": payment_refs,
        "ev": [ev_pay],
    }
