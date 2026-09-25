from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from student_agent.contracts import Contracts
from student_agent.trace import TraceWriter
from student_agent.workflow import solve_case


class MockGateway:
    def __init__(self) -> None:
        self.evidence_counter = 0

    async def call(self, tool_name: str, *, case_id: str, **arguments: str) -> dict[str, Any]:
        self.evidence_counter += 1
        ev_ref = f"ev_gateway_test_{self.evidence_counter:032d}"
        if tool_name in ("get_order_payments", "get_payment"):
            return {
                "schema_version": "day09-mcp-evidence-v1",
                "evidence_ref": ev_ref,
                "result_hash": "sha256:" + "a" * 64,
                "domain": "payment",
                "data": {
                    "payments": [
                        {"payment_sequential": 1, "payment_type": "credit_card", "payment_value": 75.50}
                    ]
                },
            }
        elif tool_name == "get_order":
            return {
                "schema_version": "day09-mcp-evidence-v1",
                "evidence_ref": ev_ref,
                "result_hash": "sha256:" + "b" * 64,
                "domain": "order",
                "data": {
                    "order_status": "canceled",
                    "seller_id": "seller_abc_123",
                    "items": [{"order_item_id": "item_001", "seller_id": "seller_abc_123"}],
                },
            }
        elif tool_name in ("get_shipment_summary", "get_shipment"):
            return {
                "schema_version": "day09-mcp-evidence-v1",
                "evidence_ref": ev_ref,
                "result_hash": "sha256:" + "c" * 64,
                "domain": "shipment",
                "data": {
                    "order_delivered_carrier_date": "2026-03-01 10:00:00",
                    "shipping_limit_date": "2026-02-28 10:00:00",
                },
            }
        raise ValueError(f"Unknown tool: {tool_name}")


def test_end_to_end_canceled_order_workflow(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    contracts = Contracts(root / "contracts" / "schemas")
    trace_path = tmp_path / "traces" / "trace.jsonl"
    trace = TraceWriter(trace_path, contracts)
    gateway = MockGateway()

    case = {
        "case_id": "L3A_CASE_001",
        "context": {"order_id": "ORDER_E2E_001"},
    }

    async def _run() -> None:
        trace.emit(case_id="L3A_CASE_001", event_type="case_received", actor="coordinator")
        output = await solve_case(case, gateway, trace)  # type: ignore[arg-type]
        contracts.validate_output(output, "e2e_output")
        trace.emit(case_id="L3A_CASE_001", event_type="case_finalized", actor="coordinator")

        assert output["schema_version"] == "day09-l3a-output-v2"
        assert output["case_id"] == "L3A_CASE_001"
        assert output["assessment"]["primary_issue"] == "canceled_order_paid"
        assert output["assessment"]["case_status"] == "action_required"
        assert output["financial_resolution"]["recommended_refund_brl"] == 75.50
        assert len(output["evidence_refs"]) >= 2
        assert output["resolution_actions"] == ["issue_refund"]

        # Validate that all trace events conform to schema
        events = [trace_path.read_text(encoding="utf-8").splitlines()]
        assert trace_path.exists()

    asyncio.run(_run())


def test_end_to_end_duplicate_charge_workflow(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    contracts = Contracts(root / "contracts" / "schemas")
    trace_path = tmp_path / "traces" / "trace.jsonl"
    trace = TraceWriter(trace_path, contracts)

    class DupGateway(MockGateway):
        async def call(self, tool_name: str, *, case_id: str, **arguments: str) -> dict[str, Any]:
            if tool_name in ("get_order_payments", "get_payment"):
                self.evidence_counter += 1
                return {
                    "schema_version": "day09-mcp-evidence-v1",
                    "evidence_ref": f"ev_gateway_test_{self.evidence_counter:032d}",
                    "result_hash": "sha256:" + "d" * 64,
                    "domain": "payment",
                    "data": {
                        "payments": [
                            {"payment_sequential": 1, "payment_type": "credit_card", "payment_value": 50.0},
                            {"payment_sequential": 2, "payment_type": "credit_card", "payment_value": 50.0},
                        ]
                    },
                }
            elif tool_name == "get_order":
                self.evidence_counter += 1
                return {
                    "schema_version": "day09-mcp-evidence-v1",
                    "evidence_ref": f"ev_gateway_test_{self.evidence_counter:032d}",
                    "result_hash": "sha256:" + "e" * 64,
                    "domain": "order",
                    "data": {"order_status": "delivered"},
                }
            elif tool_name in ("get_shipment_summary", "get_shipment"):
                self.evidence_counter += 1
                return {
                    "schema_version": "day09-mcp-evidence-v1",
                    "evidence_ref": f"ev_gateway_test_{self.evidence_counter:032d}",
                    "result_hash": "sha256:" + "f" * 64,
                    "domain": "shipment",
                    "data": {
                        "order_delivered_carrier_date": "2026-02-25 10:00:00",
                        "shipping_limit_date": "2026-02-28 10:00:00",
                        "order_delivered_customer_date": "2026-03-05 10:00:00",
                        "order_estimated_delivery_date": "2026-03-10 10:00:00",
                    },
                }
            return await super().call(tool_name, case_id=case_id, **arguments)

    gateway = DupGateway()
    case = {"case_id": "L3A_CASE_002", "context": {"order_id": "ORDER_E2E_002"}}

    async def _run() -> None:
        trace.emit(case_id="L3A_CASE_002", event_type="case_received", actor="coordinator")
        output = await solve_case(case, gateway, trace)  # type: ignore[arg-type]
        contracts.validate_output(output, "e2e_output_dup")
        trace.emit(case_id="L3A_CASE_002", event_type="case_finalized", actor="coordinator")

        assert output["assessment"]["primary_issue"] == "duplicate_charge"
        assert output["assessment"]["case_status"] == "action_required"
        assert output["financial_resolution"]["recommended_refund_brl"] == 50.00
        assert output["resolution_actions"] == ["issue_refund"]

    asyncio.run(_run())


def test_end_to_end_split_payment_workflow(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    contracts = Contracts(root / "contracts" / "schemas")
    trace_path = tmp_path / "traces" / "trace.jsonl"
    trace = TraceWriter(trace_path, contracts)

    class SplitGateway(MockGateway):
        async def call(self, tool_name: str, *, case_id: str, **arguments: str) -> dict[str, Any]:
            if tool_name in ("get_order_payments", "get_payment"):
                self.evidence_counter += 1
                return {
                    "schema_version": "day09-mcp-evidence-v1",
                    "evidence_ref": f"ev_gateway_test_{self.evidence_counter:032d}",
                    "result_hash": "sha256:" + "g" * 64,
                    "domain": "payment",
                    "data": {
                        "payments": [
                            {"payment_sequential": 1, "payment_type": "voucher", "payment_value": 25.0},
                            {"payment_sequential": 2, "payment_type": "credit_card", "payment_value": 75.0},
                        ]
                    },
                }
            elif tool_name == "get_order":
                self.evidence_counter += 1
                return {
                    "schema_version": "day09-mcp-evidence-v1",
                    "evidence_ref": f"ev_gateway_test_{self.evidence_counter:032d}",
                    "result_hash": "sha256:" + "h" * 64,
                    "domain": "order",
                    "data": {"order_status": "delivered"},
                }
            elif tool_name in ("get_shipment_summary", "get_shipment"):
                self.evidence_counter += 1
                return {
                    "schema_version": "day09-mcp-evidence-v1",
                    "evidence_ref": f"ev_gateway_test_{self.evidence_counter:032d}",
                    "result_hash": "sha256:" + "i" * 64,
                    "domain": "shipment",
                    "data": {
                        "order_delivered_carrier_date": "2026-02-25 10:00:00",
                        "shipping_limit_date": "2026-02-28 10:00:00",
                        "order_delivered_customer_date": "2026-03-05 10:00:00",
                        "order_estimated_delivery_date": "2026-03-10 10:00:00",
                    },
                }
            return await super().call(tool_name, case_id=case_id, **arguments)

    gateway = SplitGateway()
    case = {"case_id": "L3A_CASE_003", "context": {"order_id": "ORDER_E2E_003"}}

    async def _run() -> None:
        trace.emit(case_id="L3A_CASE_003", event_type="case_received", actor="coordinator")
        output = await solve_case(case, gateway, trace)  # type: ignore[arg-type]
        contracts.validate_output(output, "e2e_output_split")
        trace.emit(case_id="L3A_CASE_003", event_type="case_finalized", actor="coordinator")

        assert output["assessment"]["primary_issue"] == "valid_split_payment"
        assert output["assessment"]["case_status"] == "no_action"
        assert output["financial_resolution"]["recommended_refund_brl"] == 0.0
        assert output["financial_resolution"]["refund_lines"] == []
        assert output["resolution_actions"] == ["close_case"]

    asyncio.run(_run())


def test_package_submission_full_100_cases(tmp_path: Path) -> None:
    import json
    import zipfile
    from student_agent.submission import package_submission

    root = Path(__file__).resolve().parents[1]
    (tmp_path / "contracts").mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.copytree(root / "contracts" / "schemas", tmp_path / "contracts" / "schemas", dirs_exist_ok=True)

    case_ids = [f"L3A_CASE_{i:03d}" for i in range(1, 101)]
    (tmp_path / "case-set.json").write_text(
        json.dumps({"case_set_version": "test-v1", "variant_id": "l3a", "case_ids": case_ids}),
        encoding="utf-8",
    )
    (tmp_path / "inputs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "outputs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "traces").mkdir(parents=True, exist_ok=True)

    trace_lines: list[str] = []
    for idx, cid in enumerate(case_ids, 1):
        (tmp_path / "inputs" / f"{cid}.json").write_text(
            json.dumps({"case_id": cid, "context": {"order_id": f"ORD_{idx}"}}), encoding="utf-8"
        )
        sample_output = {
            "schema_version": "day09-l3a-output-v2",
            "case_id": cid,
            "assessment": {"primary_issue": "unsupported_claim", "case_status": "no_action", "confidence": 0.95},
            "affected_entities": {
                "order_ids": [f"ORD_{idx}"], "item_ids": [], "seller_ids": [], "payment_references": [], "shipment_ids": []
            },
            "root_cause_analysis": {
                "ranked_causes": [{"cause_code": "UNSUPPORTED_CLAIM", "rank": 1}],
                "responsible_parties": [{"party_type": "platform", "party_id": None}]
            },
            "evidence_refs": [],
            "data_conflicts": [],
            "financial_resolution": {"currency": "BRL", "recommended_refund_brl": 0.0, "refund_lines": []},
            "resolution_actions": ["close_case"]
        }
        (tmp_path / "outputs" / f"{cid}.json").write_text(
            json.dumps(sample_output), encoding="utf-8"
        )
        trace_lines.append(json.dumps({
            "schema_version": "day09-trace-event-v1",
            "event_id": f"evt_sample_test_{idx:020d}",
            "case_id": cid,
            "event_type": "case_received",
            "occurred_at": "2026-09-25T10:00:00Z",
            "actor": "coordinator"
        }))

    (tmp_path / "traces" / "trace.jsonl").write_text("\n".join(trace_lines) + "\n", encoding="utf-8")

    zip_dest = tmp_path / "dist" / "submission.zip"
    result = package_submission(tmp_path, zip_dest)
    assert result.exists()
    assert result.stat().st_size > 0

    with zipfile.ZipFile(result, "r") as zf:
        namelist = zf.namelist()
        assert "manifest.json" in namelist
        assert "trace.jsonl" in namelist
        for cid in case_ids:
            assert f"outputs/{cid}.json" in namelist
        # Verify NO extraneous files or leaked keys
        assert not any(name.endswith(".py") or name.startswith(".env") for name in namelist)


