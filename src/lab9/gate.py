"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from lab8.console import utf8_stdio
from lab9 import stats
from lab9.eval import SPEC, load_spec, per_item
from lab9.graders import GRADERS


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Must catch at least:
      - any metric value in pr["metrics"] that is NaN or inf
      - len(pr["rows"]) != manifest items x len(manifest seeds)   (dropped / duplicated rows)
      - the (item_id, seed) set of PR differs from the baseline's (cherry-picked seeds/items)
      - pr["evaluator_failures"] non-empty, or a row whose value for a graded spec metric is
        None (a crashed grader silently scores as "missing" and drops out of the mean)
      - base_spec given and a threshold in spec is LOOSER than in base_spec for the same
        eval-set version (ratchet: max may not rise, min may not fall, a blocking metric may
        not become non-blocking, a metric may not disappear; the same for spec["slices"],
        including dropping no_significant_drop)
      - an item's slice tags in PR rows differ from the baseline's (relabelling an item out of a
        slice hides its regression from the slice gate)"""
    problems: list[str] = []

    # 1. NaN / inf metric values: a gate that compares numbers must not average away a crash.
    for mid, val in (pr.get("metrics") or {}).items():
        if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
            problems.append(f"metric {mid} is NaN/inf")

    # 2. Row count must be exactly items x seeds (dropped / duplicated rows).
    manifest = pr.get("manifest") or {}
    items = manifest.get("items")
    seeds = list(manifest.get("seeds") or [])
    if items is not None and len(pr.get("rows", [])) != items * len(seeds):
        problems.append(f"row count {len(pr.get('rows', []))} != items {items} x seeds {len(seeds)}")

    # 3. The (item_id, seed) set must match the baseline's (cherry-picked seeds/items).
    pr_set = {(r.get("item_id"), r.get("seed")) for r in pr.get("rows", [])}
    base_set = {(r.get("item_id"), r.get("seed")) for r in base.get("rows", [])}
    if pr_set != base_set:
        problems.append("(item, seed) set differs from the baseline's")

    # 4. Crashed graders: evaluator failures, or a row whose value for a graded metric is None
    #    (a missing value silently drops out of the mean).
    if pr.get("evaluator_failures"):
        problems.append(f"{len(pr['evaluator_failures'])} evaluator failure(s)")
    graded = [m["id"] for m in spec.get("metrics", []) if m["id"] in GRADERS]
    for r in pr.get("rows", []):
        for mid in graded:
            if mid in r and r[mid] is None:
                problems.append(f"row {r.get('item_id')}@{r.get('seed')} metric {mid} is None")
                break

    # 5. Ratchet: on the same eval-set version a threshold may only get stricter.
    if base_spec is not None:
        problems += _ratchet_problems(spec, base_spec)

    # 6. Slice tags must not be relabelled between the two reports.
    pr_tags = _slice_tags(pr)
    base_tags = _slice_tags(base)
    for item_id, tags in pr_tags.items():
        if item_id in base_tags and tags != base_tags[item_id]:
            problems.append(f"item {item_id} slice tags changed from {sorted(base_tags[item_id])} to {sorted(tags)}")

    return problems


def _slice_tags(report: dict) -> dict[str, frozenset[str]]:
    return {r.get("item_id"): frozenset(r.get("slices") or []) for r in report.get("rows", [])}


def _ratchet_problems(spec: dict, base_spec: dict) -> list[str]:
    """On the same eval-set version, thresholds may only get stricter (never looser)."""
    problems: list[str] = []
    base_metrics = {m["id"]: m for m in base_spec.get("metrics", [])}
    for m in spec.get("metrics", []):
        bm = base_metrics.get(m["id"])
        if bm is None:
            continue  # a new metric is an addition, not a loosening
        th, bth = m.get("threshold", {}) or {}, bm.get("threshold", {}) or {}
        if "max" in th and "max" in bth and th["max"] > bth["max"]:
            problems.append(f"metric {m['id']} max rose {bth['max']} -> {th['max']}")
        if "min" in th and "min" in bth and th["min"] < bth["min"]:
            problems.append(f"metric {m['id']} min fell {bth['min']} -> {th['min']}")
        if bm.get("blocking") and not m.get("blocking"):
            problems.append(f"metric {m['id']} became non-blocking")
        if bth.get("no_significant_drop") and not th.get("no_significant_drop"):
            problems.append(f"metric {m['id']} dropped no_significant_drop")
    spec_ids = {m["id"] for m in spec.get("metrics", [])}
    for mid in base_metrics:
        if mid not in spec_ids:
            problems.append(f"metric {mid} disappeared")

    base_slices = {s["name"]: s for s in base_spec.get("slices", [])}
    for s in spec.get("slices", []):
        bs = base_slices.get(s["name"])
        if bs is None:
            continue
        if "min" in s and "min" in bs and s["min"] < bs["min"]:
            problems.append(f"slice {s['name']} min fell {bs['min']} -> {s['min']}")
        if bs.get("blocking") and not s.get("blocking"):
            problems.append(f"slice {s['name']} became non-blocking")
        if bs.get("no_significant_drop") and not s.get("no_significant_drop"):
            problems.append(f"slice {s['name']} dropped no_significant_drop")
    spec_slice_ids = {s["name"] for s in spec.get("slices", [])}
    for sname in base_slices:
        if sname not in spec_slice_ids:
            problems.append(f"slice {sname} disappeared")
    return problems


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocked: list[str] = []
    lines: list[str] = []
    bs = spec.get("bootstrap", {})
    for s in spec.get("slices", []):
        name = s["name"]
        metric = s.get("metric", "pass_rate")
        a = per_item(pr, metric, slice_name=name)
        b = per_item(base, metric, slice_name=name)
        val = sum(a.values()) / len(a) if a else None
        fail = False
        note = ""
        if val is None:
            fail = True
            note = "no items in slice"
        else:
            if "min" in s and val < s["min"]:
                fail = True
            if s.get("no_significant_drop"):
                keys = sorted(set(a) & set(b))
                d, lo, hi = stats.paired_bootstrap(
                    [a[k] for k in keys], [b[k] for k in keys],
                    n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
                note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
                if hi < 0:
                    fail = True
        label = "BLOCK" if (fail and s.get("blocking")) else ("warn" if fail else "ok")
        if fail and s.get("blocking"):
            blocked.append(f"slice:{name}/{metric}")
        lines.append(f"  slice:{name}/{metric:<16} {str(val):<8} {label} {note}")
    return blocked, lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    problems = integrity_check(pr, base, spec, base_spec)
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if verdict != "ok" and m.get("blocking"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr['run_id']} vs main {base['run_id']} · eval set {pr['manifest']['eval_set_sha']}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
