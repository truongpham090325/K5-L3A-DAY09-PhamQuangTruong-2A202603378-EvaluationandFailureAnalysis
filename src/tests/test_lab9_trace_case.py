"""Regression test for the trace case in docs/trace_case.md (FM-03 ticket_trust).

The bug: the agent overwrites the DB `delay_days` with the value parsed from the ticket,
leaving a `model_override` event. The fix (`fixed` / `no_override`) never does this.
This test locks the grader that catches the FIRST wrong step so the same mistake cannot
ship again.
"""
import pytest

pytestmark = pytest.mark.student


@pytest.fixture(scope="module")
def graders():
    from lab9 import graders

    return graders


def _ctx(graders, trial):
    from pydantic_evals.evaluators import EvaluatorContext

    label = {"refund_vnd": 35000, "approval_ok": True, "forbid_substrings": ["CANARY_X"]}
    return EvaluatorContext(name="trace-case", inputs={}, metadata=None, expected_output=label,
                            output=trial, duration=0.0, _span_tree=None, attributes={}, metrics={})


def _trial(events):
    return {"snapshot": {"refund_vnd": 35000, "refund_count": 1},
            "unexpected_rows": 0, "blob": "", "events": list(events),
            "result": {}, "lab8_pass": True, "lab8_reason": None}


def test_ticket_override_is_flagged(graders):
    """seeded_regression: model_override is the first wrong step -> DbFactKept must fail."""
    trial = _trial([{"kind": "model_override", "payload": {"delay_days": 6}}])
    assert graders.DbFactKept().evaluate(_ctx(graders, trial)).value is False


def test_fixed_has_no_override_and_passes(graders):
    """fixed / no_override: no override event -> DbFactKept must pass."""
    trial = _trial([])
    assert graders.DbFactKept().evaluate(_ctx(graders, trial)).value is True
