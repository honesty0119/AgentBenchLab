"""Offline rescoring and deterministic task variants; never generate human labels."""
import copy
import uuid

from agentbench.dataset import import_dataset, select_cases
from agentbench.grading import grade, scorer_fingerprint
from agentbench.schema import Case, DatasetUpload, RunConfig, digest


def regrade_run(store, run_id):
    run = store.get_run(run_id)
    cases = {c["id"]: Case.model_validate(c) for c in run["manifest"]["cases"]}
    batch = uuid.uuid4().hex[:16]
    records = []
    for t in store.trials(run_id):
        if t["status"] != "completed" or not t["result"]:
            continue
        updated = grade(cases[t["case_id"]], t["result"])
        payload = {"batch_id": batch, "scorer_hash": scorer_fingerprint(),
                   "result_hash": digest(t["result"]), "grade": updated}
        store.annotate("regrades", t["id"], payload)
        records.append({"trial_id": t["id"], "case_id": t["case_id"],
                        "before": t["grade"]["label"] if t["grade"] else None,
                        "after": updated["label"], "overall": updated["overall"]})
    return {"batch_id": batch, "run_id": run_id, "scorer_hash": scorer_fingerprint(),
            "records": records, "skipped": len(store.trials(run_id))-len(records),
            "note": "Original scores and model outputs are unchanged; no model calls."}


def variants(store, config: RunConfig, mode: str):
    if mode not in {"reorder", "rename", "distractor"}:
        raise ValueError("Supported variants: reorder, rename, distractor")
    cases, source_hash = select_cases(config.model_copy(update={"agent": "openai-compatible"}), store.root)
    output = []
    for c in cases:
        data = copy.deepcopy(c.model_dump())
        if mode == "reorder":
            data["files"] = dict(reversed(list(data["files"].items())))
            data["file_order"] = list(reversed(c.file_order))
        elif mode == "rename":
            mapping = {p: f"variant_{i}_{p.rsplit('/', 1)[-1]}" for i, p in enumerate(c.files)}
            def rewrite(value):
                if isinstance(value, str):
                    # One pass avoids collisions and recursively replacing inserted names.
                    import re
                    if not mapping:
                        return value
                    return re.sub("|".join(re.escape(k) for k in sorted(mapping, key=len, reverse=True)),
                                  lambda m: mapping[m.group()], value)
                if isinstance(value, list):
                    return [rewrite(x) for x in value]
                if isinstance(value, dict):
                    return {mapping.get(k, k): rewrite(v) for k, v in value.items()}
                return value
            data = rewrite(data)
        else:
            path = "irrelevant_fixture.txt"
            while path in data["files"]:
                path = "extra_" + path
            data["files"][path] = "Unrelated botanical note: this file contains no task evidence."
            data["file_order"].append(path)
        data["id"] = c.id + "-" + mode
        data["family"] = c.family
        data["review_status"] = "draft"
        data["provenance"] = f"metamorphic:{mode}; parent={c.id}; dataset={source_hash}"
        output.append(Case.model_validate(data))
    imported = import_dataset(store.root, DatasetUpload(version=f"{mode}-{source_hash[:8]}", cases=output))
    return {**imported, "relation": "answer/state semantics preserved; paths follow rename mapping",
            "pairs": [{"source": c.id, "variant": v.id, "family": c.family} for c, v in zip(cases, output)],
            "review_required": True}
