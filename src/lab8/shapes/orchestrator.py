"""Shape C — orchestrator plus read-only investigators. Student work.

1. Build a handoff with `lab8.handoff.make_handoff`. Allowed tools are read-only.
   Never put parent_secret, raw headers, or the full claim row into facts.
2. `lab8.runtime.run_readonly_investigators` runs those tools.
3. The orchestrator quotes from the investigator's order view and calls
   `durability.issue_refund`. Investigators have no write registry.
   Pass the full quote; the host subtracts earlier refunds on the same order.
   `lookup_refund_history` is a read-only tool you may hand to an investigator.
4. Trace the sanitized handoff, not the parent context.

An LLM summary of the parent context is not a sanitizer. Use the allowlist.

Trace contract v1 (docs/contracts.md): return a trace_path to a JSON file written in this run,
{"run_id": <same run_id>, "events": [...]}, on every return path including early
ones. `lab8.trace.Trace` does this; your own tracer is fine if it matches.
"""

from __future__ import annotations

import sqlite3

from lab8.db import connect
from lab8.durability import InjectedCrash, issue_refund
from lab8.handoff import make_handoff
from lab8.mock_llm import complete
from lab8.runtime import run_readonly_investigators
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish, safe_dump
from lab8.tools import tool_cost


def run_claim(
    claim_id: str,
    *,
    seed: int,
    store_path: str,
    crash_at: str | None = None,
) -> RunResult:
    conn = connect(store_path)
    try:
        return _run(conn, claim_id, seed=seed, crash_at=crash_at)
    finally:
        conn.close()


def _run(
    conn: sqlite3.Connection,
    claim_id: str,
    *,
    seed: int,
    crash_at: str | None,
) -> RunResult:
    budget = Budget()
    run_id = make_run_id("orchestrator", claim_id, seed)
    trace = _Trace(run_id)

    # ── Bước 1: Đọc claim qua get_claim_view (không SELECT *) ──────────────
    budget.add_tool(tool_cost())
    claim_row = conn.execute(
        "SELECT claim_id, principal_id, order_id, ticket FROM claims WHERE claim_id = ?",
        (claim_id,),
    ).fetchone()

    if claim_row is None:
        trace.add("error", {"reason": "unknown_claim"})
        return finish(
            conn,
            shape="orchestrator",
            claim_id=claim_id,
            seed=seed,
            status="failed",
            proposal_amount=0,
            budget=budget,
            trace=trace,
            error_reason="unknown_claim",
        )

    # ── Bước 2: Thiếu order_id → needs_info ────────────────────────────────
    if not claim_row["order_id"]:
        complete(budget, seed=seed, purpose="draft", prompt="needs_info")
        trace.add("decision", {"status": "needs_info", "reason": "missing_order_id"})
        return finish(
            conn,
            shape="orchestrator",
            claim_id=claim_id,
            seed=seed,
            status="needs_info",
            proposal_amount=0,
            budget=budget,
            trace=trace,
            error_reason="missing_order_id",
        )

    # ── Bước 3: Dựng handoff an toàn và chạy investigator ──────────────────
    # make_handoff tự lọc qua allowlist — không được đưa parent_secret vào facts
    handoff = make_handoff(
        request_id=f"REQ-{claim_id}",
        claim_id=claim_id,
        task="investigate_claim",
        facts={},
        allowed_tools=[
            "lookup_order",
            "lookup_policy",
            "compute_compensation",
            "lookup_refund_history",
        ],
    )
    # Ghi handoff đã sanitize vào trace (không ghi parent context)
    trace.add("handoff", safe_dump(handoff))

    investigated = run_readonly_investigators(
        conn, budget, handoff, principal_id=claim_row["principal_id"]
    )

    order_view = investigated["tools"].get("lookup_order", {})
    trace.add(
        "investigator",
        {
            "lookup_order": order_view,
            "policy_count": len(
                (investigated["tools"].get("lookup_policy") or {}).get("policies") or []
            ),
        },
    )

    # ── Bước 4: Đơn không visible → escalated ──────────────────────────────
    if not order_view.get("visible"):
        complete(budget, seed=seed, purpose="draft", prompt="escalated")
        return finish(
            conn,
            shape="orchestrator",
            claim_id=claim_id,
            seed=seed,
            status="escalated",
            proposal_amount=0,
            budget=budget,
            trace=trace,
            error_reason=order_view.get("reason"),
        )

    # ── Bước 5: Tính quote từ order row bằng compute_compensation ──────────
    # Không tin số trong ticket (business rule: chữ trong ticket là dữ liệu user)
    quote_payload = investigated["tools"].get("compute_compensation")
    if not quote_payload:
        # compute_compensation chưa được investigator gọi → fallback denied
        trace.add("error", {"reason": "no_quote"})
        return finish(
            conn,
            shape="orchestrator",
            claim_id=claim_id,
            seed=seed,
            status="denied",
            proposal_amount=0,
            budget=budget,
            trace=trace,
            error_reason="no_quote",
        )

    proposal = int(quote_payload["amount_vnd"])
    trace.add("quote", quote_payload)
    complete(budget, seed=seed, purpose="draft", prompt=f"proposal={proposal}")

    # ── Bước 6: Quote = 0 → denied (không gọi issue_refund) ────────────────
    if proposal <= 0:
        return finish(
            conn,
            shape="orchestrator",
            claim_id=claim_id,
            seed=seed,
            status="denied",
            proposal_amount=0,
            budget=budget,
            trace=trace,
            error_reason=quote_payload.get("reason"),
        )

    # ── Bước 7: Gọi issue_refund — truyền FULL quote, không tự trừ ─────────
    # Truyền crash_at và KHÔNG bắt InjectedCrash (CP3)
    try:
        outcome = issue_refund(conn, claim_id, proposal, crash_at=crash_at)
    except InjectedCrash:
        # Ghi trace rồi re-raise để crash lan ra đúng như lab yêu cầu
        trace.add("crash", {"at": crash_at})
        trace.write()
        raise

    if not outcome["ok"]:
        status = "failed" if outcome["conflict"] else "denied"
        trace.add("refund_outcome", {"ok": False, "reason": outcome["reason"]})
        return finish(
            conn,
            shape="orchestrator",
            claim_id=claim_id,
            seed=seed,
            status=status,
            proposal_amount=proposal,
            budget=budget,
            trace=trace,
            error_reason=outcome["reason"],
        )

    # ── Bước 8: Thành công → resolved ──────────────────────────────────────
    trace.add("refund_outcome", {"ok": True, "reason": outcome["reason"]})
    return finish(
        conn,
        shape="orchestrator",
        claim_id=claim_id,
        seed=seed,
        status="resolved",
        proposal_amount=proposal,
        budget=budget,
        trace=trace,
        error_reason=None,
    )


class _Trace:
    """Thin wrapper giống swarm_reference — flush được khi crash."""

    def __init__(self, run_id: str) -> None:
        from lab8.trace import Trace

        self._inner = Trace(run_id)

    def add(self, kind: str, payload: dict) -> None:
        self._inner.add(kind, payload)

    def write(self) -> str:
        return self._inner.write()

    @property
    def run_id(self) -> str:
        return self._inner.run_id

