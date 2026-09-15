from __future__ import annotations

import random
from collections import Counter, defaultdict
from statistics import mean

from agentbench.storage import Store


def summary(store: Store, run_id: str):
    run = store.get_run(run_id)
    trials = store.trials(run_id)
    categories = {c["id"]: c["category"] for c in run["manifest"]["cases"]}
    scored = [t for t in trials if t["status"] == "completed" and t["grade"]]
    passed = sum(t["grade"]["label"] == "pass" for t in scored)
    groups = defaultdict(lambda: {"total": 0, "scored": 0, "passed": 0})
    hints = Counter()
    for t in trials:
        g = groups[categories[t["case_id"]]]
        g["total"] += 1
        if t in scored:
            g["scored"] += 1
            g["passed"] += t["grade"]["label"] == "pass"
            if t["grade"]["label"] == "fail":
                hints.update(t["grade"]["diagnostic_hints"])
    durations = sorted(t["result"]["duration_ms"] for t in scored)
    costs = [t["result"].get("cost_estimate") for t in scored]
    return {"run_id": run_id, "total": len(trials), "scored": len(scored), "passed": passed,
            "failed": len(scored) - passed, "errors": sum(t["status"] == "error" for t in trials),
            "success_rate": passed / len(trials) if trials else None,
            "scored_pass_rate": passed / len(scored) if scored else None,
            "coverage": len(scored) / len(trials) if trials else 0,
            "categories": dict(groups), "failure_hints": dict(hints),
            "p50_ms": durations[len(durations) // 2] if durations else None,
            "p95_ms": durations[min(len(durations) - 1, int(len(durations) * .95))] if durations else None,
            "cost_estimate": sum(costs) if costs and all(c is not None for c in costs) else None,
            "demo": run["manifest"]["demo"], "status": run["status"]}


def compare(store: Store, base_id: str, candidate_id: str):
    base, candidate = store.get_run(base_id), store.get_run(candidate_id)
    reasons = []
    for key in ("dataset_hash", "scorer_version", "demo"):
        if base["manifest"][key] != candidate["manifest"][key]:
            reasons.append(f"Different {key}")
    a, b = store.trials(base_id), store.trials(candidate_id)
    if base["status"] != "completed" or candidate["status"] != "completed":
        reasons.append("Both runs must be completed")
    if any(t["status"] != "completed" or not t["grade"] for t in a + b):
        reasons.append("Unscored trials or infrastructure errors; comparison is incomplete")
    amap = {(t["case_id"], t["repeat"]): t for t in a}
    bmap = {(t["case_id"], t["repeat"]): t for t in b}
    if amap.keys() != bmap.keys():
        reasons.append("Different case/repeat selection")
    if reasons:
        return {"comparable": False, "reasons": reasons}
    groups = defaultdict(list)
    changes = []
    for key in amap:
        x, y = amap[key]["grade"]["label"], bmap[key]["grade"]["label"]
        groups[key[0]].append(int(y == "pass") - int(x == "pass"))
        if x != y:
            changes.append({"case_id": key[0], "repeat": key[1], "before": x, "after": y,
                            "base_trial": amap[key]["id"], "candidate_trial": bmap[key]["id"]})
    deltas = [mean(values) for values in groups.values()]
    rng = random.Random(42)
    samples = sorted(mean(rng.choices(deltas, k=len(deltas))) for _ in range(2000))
    return {"comparable": True, "delta": mean(deltas), "ci95": [samples[49], samples[1949]],
            "method": "paired bootstrap over tasks (repeats clustered), 2000 draws, seed=42",
            "task_count": len(deltas), "changes": changes, "demo": base["manifest"]["demo"],
            "config_changes": {k: [base["config"][k], v] for k, v in candidate["config"].items()
                               if v != base["config"].get(k)},
            "warning": "Synthetic fixture comparison; no model capability inference" if base["manifest"]["demo"]
                       else "Sample-specific uncertainty; inspect category regressions and confounders"}


def calibration(store: Store, run_id: str, rubric="v2"):
    pairs, omitted, reviewers = [], Counter(), set()
    for t in store.trials(run_id):
        trial = store.get_trial(t["id"])
        latest = {}
        for r in trial["reviews"]:
            p = r["payload"]
            latest[p["reviewer"]] = p["label"]
        judgements = [j["payload"] for j in trial["judgements"] if j["payload"].get("rubric") == rubric]
        if not latest or not judgements:
            omitted["missing_human_or_judge"] += 1
            continue
        labels = set(latest.values())
        if len(labels) != 1 or "uncertain" in labels:
            omitted["human_disagreement_or_uncertain"] += 1
            continue
        j = judgements[-1]
        if j["status"] != "completed" or j["output"]["label"] == "uncertain":
            omitted["judge_error_or_uncertain"] += 1
            continue
        reviewers.update(latest)
        pairs.append((next(iter(labels)), j["output"]["label"]))
    n = len(pairs)
    matrix = {f"human_{h}_judge_{j}": sum(a == h and b == j for a, b in pairs)
              for h in ("pass", "fail") for j in ("pass", "fail")}
    agreement = sum(h == j for h, j in pairs) / n if n else None
    expected = sum(sum(h == label for h, _ in pairs) * sum(j == label for _, j in pairs)
                   for label in ("pass", "fail")) / n ** 2 if n else None
    kappa = (agreement - expected) / (1 - expected) if expected is not None and expected < 1 else None
    return {"n": n, "agreement": agreement, "kappa": kappa, "confusion": matrix,
            "omitted": dict(omitted), "reviewer_count": len(reviewers), "rubric": rubric,
            "note": "Only latest label per reviewer; conflicts excluded. This is human-Judge agreement, not inter-human reliability."}
