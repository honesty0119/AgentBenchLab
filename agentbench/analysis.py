from __future__ import annotations

import random
from collections import Counter, defaultdict
from statistics import mean
import math
from agentbench.schema import digest

from agentbench.storage import Store


def combined_verdict(trial, case, cohort=None):
    rule = (trial.get("grade") or {}).get("label")
    if rule != "pass":
        return {"overall": "fail" if rule == "fail" else "unscored", "rule": rule}
    if not case.get("semantic_required", False):
        return {"overall": "pass", "rule": rule, "semantic": "not_required"}
    latest = {}
    for j in trial.get("judgements", []):
        p = j["payload"]
        if p.get("result_hash") == digest(trial["result"]):
            latest[judge_cohort(p)] = p
    if cohort is None and len(latest) == 1:
        cohort = next(iter(latest))
    p = latest.get(cohort)
    label = p["output"]["label"] if p and p.get("status") == "completed" else None
    return {"rule": rule, "overall": label if label in {"pass", "fail"} else "pending_semantic",
            "semantic": label or "pending", "cohort": cohort, "available_cohorts": sorted(latest)}


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
    executed = [t for t in trials if t.get("result")]
    durations = sorted(t["result"]["duration_ms"] for t in executed if "duration_ms" in t["result"])
    failure_classes = Counter(t["result"].get("failure_class") or
                              ("task_failure" if (t.get("grade") or {}).get("label") == "fail" else "none")
                              for t in executed if t["status"] != "error")
    triggered = [t for t in executed if t["result"].get("faults", {}).get("triggered")]
    fault_tasks = [t for t in executed if t["result"].get("faults", {}).get("configured", 0)]
    known_costs = [t["result"]["known_cost"] for t in executed if t["result"].get("known_cost") is not None]
    by_case = defaultdict(list)
    for t in scored:
        by_case[t["case_id"]].append(t["grade"]["label"] == "pass")
    costs = [t["result"].get("cost_estimate") for t in scored]
    case_map = {c["id"]: c for c in run["manifest"]["cases"]}
    overall = Counter(combined_verdict(store.get_trial(t["id"]), case_map[t["case_id"]])["overall"] for t in scored)
    pending_semantic = overall["pending_semantic"]
    return {"run_id": run_id, "total": len(trials), "scored": len(scored), "passed": passed,
            "failed": len(scored) - passed, "errors": sum(t["status"] == "error" for t in trials),
            "success_rate": passed / len(trials) if trials else None,
            "scored_pass_rate": passed / len(scored) if scored else None,
            "coverage": len(scored) / len(trials) if trials else 0,
            "categories": dict(groups), "failure_hints": dict(hints),
            "p50_ms": durations[max(0, math.ceil(len(durations)*.5)-1)] if durations else None,
            "p95_ms": durations[max(0, math.ceil(len(durations)*.95)-1)] if durations else None,
            "cost_estimate": sum(costs) if costs and len(scored) == len(trials) and all(c is not None for c in costs) else None,
            "known_cost": sum(known_costs) if known_costs else None,
            "unknown_usage_attempts": sum(t["result"].get("unknown_usage_attempts", 0) for t in executed),
            "usage_coverage": sum(bool(t["result"].get("usage_complete")) for t in executed)/len(executed) if executed else None,
            "failure_classes": dict(failure_classes),
            "fault_trigger_rate": len(triggered)/len(fault_tasks) if fault_tasks else None,
            "fault_recovery_rate": sum((t.get("grade") or {}).get("label") == "pass" for t in triggered)/len(triggered) if triggered else None,
            "fault_triggered_tasks": len(triggered), "fault_configured_tasks": len(fault_tasks),
            "stability": {k: {"scored_repeats": len(v), "pass_rate": mean(v), "all_passed": all(v)} for k,v in by_case.items()},
            "semantic_pending": pending_semantic,
            "overall_counts": dict(overall),
            "overall_pass_rate": overall["pass"] / len(trials) if trials else None,
            "intervention": run["manifest"].get("intervention", "none"),
            "demo": run["manifest"]["demo"], "status": run["status"]}


def compare(store: Store, base_id: str, candidate_id: str, diagnostic=False, metric="rules", cohort=None):
    if metric not in {"rules", "overall"}:
        raise ValueError("metric must be rules or overall")
    base, candidate = store.get_run(base_id), store.get_run(candidate_id)
    reasons = []
    for key in ("dataset_hash", "scorer_version", "demo"):
        if base["manifest"][key] != candidate["manifest"][key]:
            reasons.append(f"Different {key}")
    if not base["manifest"].get("scorer_hash") or base["manifest"].get("scorer_hash") != candidate["manifest"].get("scorer_hash"):
        reasons.append("Missing or different scorer fingerprint; regrade stored outputs separately")
    if not diagnostic and (base["config"].get("intervention", "none") != "none" or candidate["config"].get("intervention", "none") != "none"):
        reasons.append("Intervention runs require a diagnostic comparison, not a regression gate")
    families = {c["id"]: c["family"] for c in base["manifest"]["cases"]}
    a, b = store.trials(base_id), store.trials(candidate_id)
    if base["status"] != "completed" or candidate["status"] != "completed":
        reasons.append("Both runs must be completed")
    if any(t["status"] != "completed" or not t["grade"] for t in a + b):
        reasons.append("Unscored trials or infrastructure errors; comparison is incomplete")
    amap = {(t["case_id"], t["repeat"]): t for t in a}
    bmap = {(t["case_id"], t["repeat"]): t for t in b}
    if amap.keys() != bmap.keys():
        reasons.append("Different case/repeat selection")
    labels = {}
    if metric == "overall" and not reasons:
        semantic_cases = {c["id"] for c in base["manifest"]["cases"] if c.get("semantic_required")}
        if semantic_cases and not cohort:
            reasons.append("Overall comparison requires an explicit common Judge cohort")
        for run, trials in ((base, a), (candidate, b)):
            cases = {c["id"]: c for c in run["manifest"]["cases"]}
            for t in trials:
                label = combined_verdict(store.get_trial(t["id"]), cases[t["case_id"]], cohort)["overall"]
                labels[t["id"]] = label
                if label not in {"pass", "fail"}:
                    reasons.append("Overall comparison has pending semantic judgements or unscored trials")
                    break
    if reasons:
        return {"comparable": False, "reasons": list(dict.fromkeys(reasons)), "metric": metric}
    groups = defaultdict(list)
    changes = []
    for key in amap:
        x, y = (labels[t["id"]] if metric == "overall" else t["grade"]["label"] for t in (amap[key], bmap[key]))
        groups[key[0]].append(int(y == "pass") - int(x == "pass"))
        if x != y:
            changes.append({"case_id": key[0], "repeat": key[1], "before": x, "after": y,
                            "base_trial": amap[key]["id"], "candidate_trial": bmap[key]["id"]})
    deltas = [mean(values) for values in groups.values()]
    clusters = defaultdict(list)
    for case_id, values in groups.items():
        clusters[families[case_id]].append(mean(values))
    cluster_values = list(clusters.values())
    rng = random.Random(42)
    samples = sorted(mean(v for cluster in rng.choices(cluster_values, k=len(cluster_values)) for v in cluster) for _ in range(2000))
    return {"comparable": True, "metric": metric, "cohort": cohort if metric == "overall" else None,
            "delta": mean(deltas), "ci95": [samples[49], samples[1949]],
            "method": "paired bootstrap over task families (task means, repeats clustered), 2000 draws, seed=42",
            "task_count": len(deltas), "family_count": len(clusters), "diagnostic": diagnostic, "changes": changes, "demo": base["manifest"]["demo"],
            "config_changes": {k: [base["config"][k], v] for k, v in candidate["config"].items()
                               if v != base["config"].get(k)},
            "warning": "Synthetic fixture comparison; no model capability inference" if base["manifest"]["demo"]
                       else "Sample-specific uncertainty; inspect category regressions and confounders"}


def judge_cohort(payload):
    return payload.get("cohort") or digest({k: payload.get(k) for k in ("model", "endpoint", "rubric", "rubric_hash", "backend")})


def calibration(store: Store, run_id: str, rubric="v2", cohort=None):
    available = {judge_cohort(j["payload"]) for t in store.trials(run_id)
                 for j in store.get_trial(t["id"])["judgements"] if j["payload"].get("rubric") == rubric}
    if cohort is None and len(available) > 1:
        return {"requires_cohort": True, "cohorts": sorted(available), "n": 0, "agreement": None,
                "kappa": None, "reviewer_count": 0, "note": "Select exactly one Judge cohort"}
    cohort = cohort or next(iter(available), None)
    if cohort is not None and cohort not in available:
        raise ValueError("Unknown Judge cohort")
    pairs, omitted, reviewers = [], Counter(), set()
    for t in store.trials(run_id):
        trial = store.get_trial(t["id"])
        latest = {}
        for r in trial["reviews"]:
            p = r["payload"]
            latest[p["reviewer"]] = p["label"]
        judgements = [j["payload"] for j in trial["judgements"] if j["payload"].get("rubric") == rubric and judge_cohort(j["payload"]) == cohort]
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
            "omitted": dict(omitted), "reviewer_count": len(reviewers), "rubric": rubric, "cohort": cohort, "requires_cohort": False,
            "note": "Only latest label per reviewer; conflicts excluded. This is human-Judge agreement, not inter-human reliability."}
