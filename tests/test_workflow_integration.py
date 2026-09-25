from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from student_agent.contracts import Contracts
from student_agent.trace import TraceWriter
from student_agent.workflow import solve_case


class MockGateway:
    def __init__(self, responses: dict[str, dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[str]:
        return list(self.responses.keys())

    async def call(self, tool_name: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((tool_name, kwargs))
        if tool_name in self.responses:
            return self.responses[tool_name]
        return {"evidence_ref": f"ev_default_{tool_name}_1234567890abcdef", "data": {}}


def test_workflow_end_to_end_with_schema_validation(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    contracts = Contracts(root / "contracts" / "schemas")
    trace_path = tmp_path / "trace.jsonl"
    trace = TraceWriter(trace_path, contracts)

    case = {
        "case_id": "CASE_TEST_001",
        "context": {"order_id": "ORD_TEST_001"},
    }

    gateway = MockGateway(
        {
            "get_order": {
                "schema_version": "day09-mcp-evidence-v1",
                "evidence_ref": "ev_order_test_canceled_abcdef123456",
                "result_hash": "sha256:" + "a" * 64,
                "domain": "order",
                "data": {
                    "order_id": "ORD_TEST_001",
                    "order_status": "canceled",
                    "seller_id": "SELLER_TEST_01",
                },
            },
            "get_payment": {
                "schema_version": "day09-mcp-evidence-v1",
                "evidence_ref": "ev_payment_test_1234567890abcdef",
                "result_hash": "sha256:" + "b" * 64,
                "domain": "payment",
                "data": {
                    "payments": [
                        {"payment_sequential": 1, "payment_value": 150.50}
                    ]
                },
            },
        }
    )

    # 1. Run solve_case
    output = asyncio.run(solve_case(case, gateway, trace))

    # 2. Strict public contract validation
    contracts.validate_output(output, "test output")

    assert output["case_id"] == "CASE_TEST_001"
    assert output["assessment"]["primary_issue"] == "canceled_order_paid"
    assert output["assessment"]["case_status"] == "action_required"
    assert output["financial_resolution"]["recommended_refund_brl"] == 150.50
    assert "SELLER_TEST_01" in output["affected_entities"]["seller_ids"]

    # 3. Validate generated trace events
    events = [line for line in trace_path.read_text(encoding="utf-8").splitlines() if line]
    # Events: task_assigned, tool_result_consumed, handoff, policy_decided, verification_completed
    assert len(events) >= 4


def test_workflow_duplicate_charge_resolution(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    contracts = Contracts(root / "contracts" / "schemas")
    trace_path = tmp_path / "trace.jsonl"
    trace = TraceWriter(trace_path, contracts)

    case = {
        "case_id": "CASE_TEST_002",
        "context": {"order_id": "ORD_TEST_002"},
    }

    gateway = MockGateway(
        {
            "get_order": {
                "evidence_ref": "ev_order_test_delivered_1234567890ab",
                "data": {
                    "order_id": "ORD_TEST_002",
                    "order_status": "delivered",
                },
            },
            "get_shipment": {
                "evidence_ref": "ev_ship_test_delivered_1234567890ab",
                "data": {
                    "shipping_limit_date": "2018-05-10 12:00:00",
                    "order_delivered_carrier_date": "2018-05-08 10:00:00",
                    "order_delivered_customer_date": "2018-05-12 18:00:00",
                    "order_estimated_delivery_date": "2018-05-15 00:00:00",
                },
            },
            "get_payment": {
                "evidence_ref": "ev_payment_duplicate_1234567890abcd",
                "data": {
                    "payments": [
                        {"payment_sequential": 1, "payment_value": 85.00},
                        {"payment_sequential": 2, "payment_value": 85.00},
                    ]
                },
            },
        }
    )

    output = asyncio.run(solve_case(case, gateway, trace))
    contracts.validate_output(output, "duplicate charge output")

    assert output["assessment"]["primary_issue"] == "duplicate_charge"
    assert output["assessment"]["case_status"] == "action_required"
    assert output["financial_resolution"]["recommended_refund_brl"] == 85.00
