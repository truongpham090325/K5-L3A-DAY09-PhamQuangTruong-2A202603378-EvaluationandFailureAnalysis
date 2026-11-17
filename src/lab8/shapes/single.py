"""Shape B — one ReAct agent. Student work.

Use `lab8.runtime.run_react` with max_steps=8. `decide` may pick a read tool.
It must not call `issue_refund`. After the loop, the host validates the quote
and is the only writer.

A decide() that trusts ticket text over the order row will fail hidden claims
where the customer overstates the delay or cites a superseded clause.

Per-order cap: an order may already have been refunded by an earlier claim.
The read tool `lookup_refund_history` shows how much. The host still enforces
the cap, so pass the full quote to `durability.issue_refund`, not a net amount.

Caps: stop at 8 steps. Count model calls in the Budget so cost stays comparable.

Trace contract v1 (docs/contracts.md): return a trace_path to a JSON file written in this run,
{"run_id": <same run_id>, "events": [...]}, on every return path including early
ones. `lab8.trace.Trace` does this; your own tracer is fine if it matches.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from lab8.db import connect
from lab8.durability import issue_refund
from lab8.mock_llm import complete
from lab8.runtime import run_react
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish, safe_dump
from lab8.trace import Trace


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
    trace = Trace(make_run_id("single", claim_id, seed))
    state: dict[str, Any] = {}

    def decide(seen: dict[str, Any]) -> dict[str, Any]:
        last_tool = seen.get("last_tool")
        last_result = seen.get("last_result") or {}

        if last_tool is None:
            complete(budget, seed=seed, purpose="react_step", prompt=f"claim_id={claim_id}")
            trace.add("react_step", {"tool": "get_claim_view"})
            return {"tool": "get_claim_view", "args": {"claim_id": claim_id}}

        if last_tool == "get_claim_view":
            state["claim_view"] = last_result
            trace.add(
                "tool_result",
                {
                    "tool": "get_claim_view",
                    "found": last_result.get("found"),
                    "claim_id": last_result.get("claim_id"),
                    "order_id": last_result.get("order_id"),
                },
            )
            if not last_result.get("found"):
                complete(budget, seed=seed, purpose="draft", prompt="unknown_claim")
                return {
                    "final": {
                        "status": "failed",
                        "proposal_amount": 0,
                        "error_reason": "unknown_claim",
                    }
                }
            complete(budget, seed=seed, purpose="parse", prompt=last_result["ticket"])
            if not last_result.get("order_id"):
                complete(budget, seed=seed, purpose="draft", prompt="needs_info")
                return {
                    "final": {
                        "status": "needs_info",
                        "proposal_amount": 0,
                        "error_reason": "missing_order_id",
                    }
                }
            trace.add("react_step", {"tool": "lookup_order"})
            return {
                "tool": "lookup_order",
                "args": {
                    "order_id": last_result["order_id"],
                    "principal_id": last_result["principal_id"],
                },
            }

        if last_tool == "lookup_order":
            state["order_view"] = last_result
            trace.add("tool_result", {"tool": "lookup_order", "result": safe_dump(last_result)})
            if not last_result.get("visible"):
                complete(budget, seed=seed, purpose="draft", prompt="escalated")
                return {
                    "final": {
                        "status": "escalated",
                        "proposal_amount": 0,
                        "error_reason": last_result.get("reason"),
                    }
                }
            claim_view = state["claim_view"]
            complete(budget, seed=seed, purpose="react_step", prompt=f"history:{claim_view['order_id']}")
            trace.add("react_step", {"tool": "lookup_refund_history"})
            return {
                "tool": "lookup_refund_history",
                "args": {
                    "order_id": claim_view["order_id"],
                    "principal_id": claim_view["principal_id"],
                },
            }

        if last_tool == "lookup_refund_history":
            state["refund_history"] = last_result
            trace.add("tool_result", {"tool": "lookup_refund_history", "result": safe_dump(last_result)})
            order = state["order_view"]["order"]
            complete(budget, seed=seed, purpose="react_step", prompt=f"quote:{order['order_id']}")
            trace.add("react_step", {"tool": "compute_compensation"})
            return {"tool": "compute_compensation", "args": {"order": order}}

        if last_tool == "compute_compensation":
            proposal = int(last_result["amount_vnd"])
            trace.add("quote", safe_dump(last_result))
            complete(budget, seed=seed, purpose="draft", prompt=f"proposal={proposal}")
            if proposal <= 0:
                return {
                    "final": {
                        "status": "denied",
                        "proposal_amount": 0,
                        "error_reason": last_result.get("reason"),
                    }
                }
            return {
                "final": {
                    "status": "ready_to_issue",
                    "proposal_amount": proposal,
                    "error_reason": None,
                }
            }

        return {"final": {"status": "failed", "proposal_amount": 0, "error_reason": "unexpected_tool"}}

    loop_out = run_react(
        conn,
        budget,
        observation={"claim": {"claim_id": claim_id}},
        decide=decide,
        max_steps=8,
        worker=True,
    )
    final = loop_out["final"]
    if loop_out["stopped"] != "final" or final["status"] != "ready_to_issue":
        trace.add("decision", {"status": final["status"], "reason": final.get("error_reason")})
        return finish(
            conn,
            shape="single",
            claim_id=claim_id,
            seed=seed,
            status=final["status"],
            proposal_amount=int(final.get("proposal_amount", 0)),
            budget=budget,
            trace=trace,
            error_reason=final.get("error_reason"),
        )

    proposal = int(final["proposal_amount"])
    outcome = issue_refund(conn, claim_id, proposal, crash_at=crash_at)
    trace.add(
        "issue_refund",
        {
            "ok": outcome["ok"],
            "reason": outcome["reason"],
            "conflict": outcome["conflict"],
        },
    )
    if not outcome["ok"]:
        status = "failed" if outcome["conflict"] else "denied"
        return finish(
            conn,
            shape="single",
            claim_id=claim_id,
            seed=seed,
            status=status,
            proposal_amount=proposal,
            budget=budget,
            trace=trace,
            error_reason=outcome["reason"],
        )

    return finish(
        conn,
        shape="single",
        claim_id=claim_id,
        seed=seed,
        status="resolved",
        proposal_amount=proposal,
        budget=budget,
        trace=trace,
        error_reason=None,
    )
