"""Immutable local reports and append-only score history; domain comparison adapter."""
from __future__ import annotations

import copy
import json
import math
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from agentbench.analysis import compare as platform_compare
from agentbench.schema import digest

from .grading import grade, scorer_fingerprint, SCORER_VERSION
from .schema import EcommerceCase
from .semantic import label


def now():
    return datetime.now(timezone.utc).isoformat()


def seal(payload):
    result = copy.deepcopy(payload)
    result.pop("integrity_hash", None)
    return {**result, "integrity_hash": digest(result)}


def read_json(path):
    value = json.loads(Path(path).read_text("utf-8"))
    if "integrity_hash" in value and value["integrity_hash"] != seal(value)["integrity_hash"]:
        raise ValueError("Report/history content hash mismatch")
    return value


def write_new(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(seal(payload), stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def summarize(report, grades=None):
    trials = report["trials"]
    grades = grades or [t["grade"] for t in trials]
    executed = [(t, g) for t, g in zip(trials, grades) if t["result"].get("termination") != "not_executed"]
    full = [(t, g) for t, g in executed if g.get("recovery_eligible")]
    costs = [t["result"].get("cost_estimate") for t, _ in executed]
    known_costs = [t["result"]["known_cost"] for t, _ in executed if t["result"].get("known_cost") is not None]
    classes = Counter(t["result"].get("failure_class") or
                      ("business_rule" if g["rules"] == "fail" else "none") for t, g in executed)
    return {"total": len(trials), "executed": len(executed), "not_executed": len(trials)-len(executed),
            "execution_coverage": len(executed)/len(trials) if trials else 0,
            "completed": sum(t["result"]["termination"] == "completed" for t, _ in executed),
            "rules_pass": sum(g["rules"] == "pass" for g in grades),
            "overall_pass": sum(g["overall"] == "pass" for g in grades),
            "semantic_pending": sum(g["overall"] == "pending_semantic" for g in grades),
            "fault_configured_trials": sum(g.get("faults_configured", 0) > 0 for _, g in executed),
            "fault_any_triggered_trials": sum(g.get("faults_triggered", 0) > 0 for _, g in executed),
            "fault_partial_trials": sum(g.get("fault_coverage") == "partial" for _, g in executed),
            "recovery_denominator": len(full), "recovery_successes": sum(g["rules"] == "pass" for _, g in full),
            "recovery_rate": sum(g["rules"] == "pass" for _, g in full)/len(full) if full else None,
            "failure_classes": dict(classes),
            "duration_ms": sum(t["result"].get("duration_ms", 0) for t, _ in executed),
            "tool_calls": sum(t["result"].get("tool_calls", len(t["result"].get("tools", []))) for t, _ in executed),
            "actual_requests": sum(len(t["result"].get("transport_attempts", [])) for t, _ in executed),
            "known_cost": sum(known_costs) if known_costs else None,
            "cost_estimate": sum(costs) if costs and all(c is not None for c in costs) and len(executed) == len(trials) else None,
            "unknown_usage_attempts": sum(t["result"].get("unknown_usage_attempts", 0) for t, _ in executed)}


class RunStore:
    def __init__(self, root="data/ecommerce/runs"):
        self.root = Path(root)

    def directory(self, run_id):
        if not re.fullmatch(r"ec-[a-f0-9]{32}", run_id):
            raise ValueError("Invalid ecommerce run ID")
        return self.root / run_id

    def save(self, report):
        path = self.directory(report["run_id"]) / "run.json"
        write_new(path, report)
        return path

    def load(self, identifier):
        value = str(identifier)
        path = self.directory(value) / "run.json" if re.fullmatch(r"ec-[a-f0-9]{32}", value) else Path(value)
        report = read_json(path)
        if report.get("domain") != "ecommerce":
            raise ValueError("Not an ecommerce report")
        if report.get("format_version") == 2 and "integrity_hash" not in report:
            raise ValueError("Stored v2 report is missing its content hash")
        return report

    def runs(self):
        values = []
        for path in sorted(self.root.glob("ec-*/run.json")):
            report = self.load(path)
            values.append({k: report[k] for k in ("run_id", "created_at", "demo", "variant", "summary")})
        return sorted(values, key=lambda v: v["created_at"], reverse=True)

    def append(self, report, kind, payload):
        if kind not in {"judgements", "regrades"}:
            raise ValueError("Unknown history kind")
        stored = self.load(report["run_id"])
        if seal(stored)["integrity_hash"] != seal(report)["integrity_hash"]:
            raise ValueError("History must bind the exact stored report")
        record = {**payload, "id": uuid.uuid4().hex, "created_at": now(),
                  "run_id": report["run_id"], "run_hash": seal(report)["integrity_hash"], "kind": kind}
        write_new(self.directory(report["run_id"]) / kind / (record["id"] + ".json"), record)
        return record

    def history(self, report, kind):
        if kind not in {"judgements", "regrades"}:
            raise ValueError("Unknown history kind")
        if report.get("format_version") != 2:
            return []
        entries = [read_json(p) for p in (self.directory(report["run_id"]) / kind).glob("*.json")]
        if any(e["run_hash"] != seal(report)["integrity_hash"] for e in entries):
            raise ValueError("History is bound to a different report")
        return sorted(entries, key=lambda e: (e["created_at"], e["id"]))

    def regrade(self, identifier):
        report = self.load(identifier)
        records = [{"trial_id": t["trial_id"], "case_hash": digest(t["case"]),
                    "result_hash": digest(t["result"]),
                    "grade": grade(EcommerceCase.model_validate(t["case"]), t["result"])} for t in report["trials"]]
        return self.append(report, "regrades", {"scorer_hash": scorer_fingerprint(),
                           "scorer_version": SCORER_VERSION, "records": records})

    def view(self, report, cohort=None, regrade=None):
        output = copy.deepcopy(report)
        output["source_run_hash"] = output.pop("integrity_hash", None)
        output["derived_view"] = True
        if report.get("format_version") != 2:
            if cohort or regrade:
                raise ValueError("Legacy reports have no v2 history")
            return {**output, "legacy": True, "available_cohorts": [], "available_regrades": []}
        judgements = self.history(report, "judgements")
        cohorts = sorted({j["record"]["cohort"] for j in judgements})
        output["available_cohorts"] = cohorts
        output["available_regrades"] = [{k: r[k] for k in ("id", "created_at", "scorer_hash")}
                                        for r in self.history(report, "regrades")]
        selected = cohort or (cohorts[0] if len(cohorts) == 1 else None)
        if cohort and cohort not in cohorts:
            raise ValueError("Unknown Judge cohort for this run")
        rules = {t.get("trial_id"): t["grade"] for t in report["trials"]}
        if regrade:
            batch = next((r for r in self.history(report, "regrades") if r["id"] == regrade), None)
            if not batch:
                raise ValueError("Unknown regrade batch")
            records = {r["trial_id"]: r for r in batch["records"]}
            if set(records) != set(rules):
                raise ValueError("Incomplete regrade batch")
            for t in report["trials"]:
                r = records[t["trial_id"]]
                if r["result_hash"] != digest(t["result"]) or r["case_hash"] != digest(t["case"]):
                    raise ValueError("Stale regrade record")
            rules = {k: r["grade"] for k, r in records.items()}
            output.update(scorer_hash=batch["scorer_hash"], scorer_version=batch["scorer_version"])
        for trial in output["trials"]:
            records = [j["record"] for j in judgements if j["trial_id"] == trial.get("trial_id")
                       and j["record"]["cohort"] == selected and j["record"]["status"] != "not_executed"]
            semantic = label(EcommerceCase.model_validate(trial["case"]), trial["result"], records[-1] if records else None)
            g = copy.deepcopy(rules[trial.get("trial_id")])
            g.update(semantic=semantic, overall="unscored" if g["rules"] == "unscored" else
                     "fail" if g["rules"] == "fail" or semantic == "fail" else
                     "pass" if semantic == "pass" else "pending_semantic")
            trial["grade"] = g
            trial["judgement_status"] = records[-1]["status"] if records else "missing"
            trial["judgement"] = records[-1] if records else None
        output.update(selected_cohort=selected, selected_regrade=regrade)
        output["summary"] = summarize(output)
        return output


def compare(store, base_id, candidate_id, metric="rules", cohort=None, diagnostic=False,
            base_regrade=None, candidate_regrade=None, gate=False, max_drop=0):
    if metric not in {"rules", "overall"} or not math.isfinite(max_drop) or not 0 <= max_drop <= 1:
        raise ValueError("Invalid comparison metric or max_drop")
    if diagnostic and gate:
        raise ValueError("Diagnostic comparisons cannot be regression gates")
    try:
        raw = [store.load(base_id), store.load(candidate_id)]
    except (ValueError, FileNotFoundError) as exc:
        return {"comparable": False, "reasons": [str(exc)], "metric": metric, "gate_passed": False}
    reasons = []
    for report in raw:
        if report.get("format_version") != 2:
            reasons.append("Legacy report lacks a complete immutable manifest")
            continue
        if report.get("source_changed_during_run"):
            reasons.append("Implementation changed during execution")
        ids = [t["trial_id"] for t in report["trials"]]
        if not ids or len(ids) != len(set(ids)) or ids != report["manifest"]["selection"]:
            reasons.append("Incomplete or duplicate task/repeat selection")
        for t in report["trials"]:
            if (report["manifest"]["case_hashes"].get(t["case"]["id"]) != digest(t["case"])
                    or t["trial_id"] != f"{t['case']['id']}:{t['repeat']}"):
                reasons.append("Trial differs from fixed manifest")
            if t["result"].get("termination") != "completed" or (t.get("grade") or {}).get("rules") not in {"pass", "fail"}:
                reasons.append("Incomplete execution or unscored trials")
    for key in ("execution_hash", "prompt_hash"):
        if not raw[0].get(key) or raw[0].get(key) != raw[1].get(key):
            reasons.append("Missing or different " + key)
    if raw[0].get("manifest", {}).get("case_hashes") != raw[1].get("manifest", {}).get("case_hashes"):
        reasons.append("Different task snapshots")
    if bool(base_regrade) != bool(candidate_regrade):
        reasons.append("Choose regrade batches for both runs or neither")
    if metric == "overall" and not cohort:
        reasons.append("Overall comparison requires an explicit common Judge cohort")
    if reasons:
        return {"comparable": False, "reasons": sorted(set(reasons)), "metric": metric, "gate_passed": False}
    try:
        views = [store.view(r, cohort=cohort, regrade=batch) for r, batch in zip(raw, (base_regrade, candidate_regrade))]
    except ValueError as exc:
        return {"comparable": False, "reasons": [str(exc)], "metric": metric, "gate_passed": False}
    if metric == "overall" and any(t["grade"]["semantic"] not in {"pass", "fail"} for r in views for t in r["trials"]):
        return {"comparable": False, "reasons": ["Missing, stale, invalid or uncertain semantic scores"],
                "metric": metric, "gate_passed": False}

    class Adapter:
        """Reuse the platform's family bootstrap and pairing implementation unchanged."""
        def __init__(self):
            self.reports = {"base": views[0], "candidate": views[1]}
            self.items = {}
            for run, report in self.reports.items():
                for t in report["trials"]:
                    ident = run + ":" + t["trial_id"]
                    self.items[ident] = {"id": ident, "case_id": t["case"]["id"], "repeat": t["repeat"],
                        "status": "completed", "result": t["result"], "grade": {"label": t["grade"]["rules"]},
                        "judgements": [{"payload": {"result_hash": digest(t["result"]), "cohort": cohort,
                            "status": "completed", "output": {"label": t["grade"]["semantic"]}}}]}

        def get_run(self, key):
            r = self.reports[key]
            return {"status": "completed", "config": r["config"], "manifest": {
                **{k: r[k] for k in ("dataset_hash", "scorer_hash", "scorer_version", "demo")},
                "cases": [t["case"] for t in r["trials"]]}}

        def trials(self, key):
            return [v for k, v in self.items.items() if k.startswith(key + ":")]

        def get_trial(self, key):
            return self.items[key]

    result = platform_compare(Adapter(), "base", "candidate", diagnostic=diagnostic, metric=metric, cohort=cohort)
    result.update(base_run=raw[0]["run_id"], candidate_run=raw[1]["run_id"],
                  base_regrade=base_regrade, candidate_regrade=candidate_regrade)
    result["gate_passed"] = bool(result["comparable"] and not diagnostic and result["delta"] >= -max_drop
                                 and not any(c["before"] == "pass" and c["after"] == "fail" for c in result["changes"]))
    return result
