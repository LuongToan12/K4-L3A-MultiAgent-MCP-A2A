from __future__ import annotations

from typing import Any
import pytest

from student_agent.payment_agent import check_payment_and_refund


class DummyTrace:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, **kwargs: Any) -> dict[str, Any]:
        self.events.append(kwargs)
        return kwargs


class DummyGateway:
    def __init__(self, data: dict[str, Any], evidence_ref: str = "ev_test_12345678901234567890") -> None:
        self.data = data
        self.evidence_ref = evidence_ref
        self.calls: list[dict[str, Any]] = []

    async def call(self, tool_name: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"tool_name": tool_name, "kwargs": kwargs})
        return {
            "evidence_ref": self.evidence_ref,
            "data": self.data,
        }


def test_single_payment() -> None:
    async def _test() -> None:
        gateway = DummyGateway(
            data={
                "payments": [
                    {"payment_sequential": 1, "payment_type": "credit_card", "payment_value": 150.50}
                ]
            },
            evidence_ref="ev_pay_single_123456789012345678",
        )
        trace = DummyTrace()

        result = await check_payment_and_refund("CASE_001", "ORDER_001", gateway, trace)

        assert result["total_paid"] == 150.50
        assert result["is_duplicate"] is False
        assert result["payment_refs"] == ["1"]
        assert result["ev"] == ["ev_pay_single_123456789012345678"]

        assert len(trace.events) == 2
        assert trace.events[0]["event_type"] == "tool_result_consumed"
        assert trace.events[0]["actor"] == "payment-agent"
        assert trace.events[0]["tool_name"] in ("get_payment", "get_order_payments")
        assert trace.events[0]["evidence_refs"] == ["ev_pay_single_123456789012345678"]

        assert trace.events[1]["event_type"] == "policy_decided"
        assert trace.events[1]["actor"] == "policy-agent"
        assert trace.events[1]["decision_code"] == "REFUND_POLICY_EVALUATED"

    import asyncio
    asyncio.run(_test())


def test_duplicate_charge() -> None:
    async def _test() -> None:
        gateway = DummyGateway(
            data={
                "payments": [
                    {"payment_sequential": 1, "payment_type": "credit_card", "payment_value": 99.99},
                    {"payment_sequential": 2, "payment_type": "credit_card", "payment_value": 99.99},
                ]
            },
            evidence_ref="ev_pay_dup_12345678901234567890",
        )
        trace = DummyTrace()

        result = await check_payment_and_refund("CASE_002", "ORDER_002", gateway, trace)

        assert result["total_paid"] == 199.98
        assert result["is_duplicate"] is True
        assert result["payment_refs"] == ["1", "2"]
        assert result["ev"] == ["ev_pay_dup_12345678901234567890"]

    import asyncio
    asyncio.run(_test())


def test_split_payment_different_amounts() -> None:
    async def _test() -> None:
        gateway = DummyGateway(
            data={
                "payments": [
                    {"payment_sequential": 1, "payment_type": "voucher", "payment_value": 20.00},
                    {"payment_sequential": 2, "payment_type": "credit_card", "payment_value": 80.00},
                ]
            },
            evidence_ref="ev_pay_split_123456789012345678",
        )
        trace = DummyTrace()

        result = await check_payment_and_refund("CASE_003", "ORDER_003", gateway, trace)

        assert result["total_paid"] == 100.00
        assert result["is_duplicate"] is False
        assert result["payment_refs"] == ["1", "2"]

    import asyncio
    asyncio.run(_test())


def test_payment_agent_with_real_contracts_and_tracewriter(tmp_path: Any) -> None:
    from pathlib import Path
    import asyncio
    from student_agent.contracts import Contracts
    from student_agent.trace import TraceWriter

    root = Path(__file__).resolve().parents[1]
    contracts = Contracts(root / "contracts" / "schemas")
    trace_path = tmp_path / "traces" / "trace.jsonl"
    trace = TraceWriter(trace_path, contracts)

    async def _test() -> None:
        gateway = DummyGateway(
            data={
                "payments": [
                    {"payment_sequential": 1, "payment_type": "credit_card", "payment_value": 45.67}
                ]
            },
            evidence_ref="ev_pay_realtrace_12345678901234",
        )
        result = await check_payment_and_refund("CASE_REAL", "ORDER_REAL", gateway, trace)
        assert result["total_paid"] == 45.67
        assert result["is_duplicate"] is False
        assert trace_path.exists()
        lines = trace_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2

    asyncio.run(_test())

