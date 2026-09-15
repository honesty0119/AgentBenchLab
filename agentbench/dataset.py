from __future__ import annotations

import json
from pathlib import Path

from agentbench.schema import Case, DatasetUpload, RunConfig, digest

ROOT = Path(__file__).parent
DATASET = ROOT / "fixtures" / "cases.json"


def load_dataset(path: Path = DATASET) -> tuple[list[Case], str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return validate_dataset(raw)


def validate_dataset(raw: dict) -> tuple[list[Case], str]:
    cases = [Case.model_validate(x) for x in raw["cases"]]
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate case IDs")
    families: dict[str, str] = {}
    for case in cases:
        if families.setdefault(case.family, case.split) != case.split:
            raise ValueError(f"Task family leaks across splits: {case.family}")
        if not any(c.hard for c in case.checks):
            raise ValueError(f"No hard criterion: {case.id}")
    return cases, digest(raw)


def select_cases(config: RunConfig, data_root: Path | None = None) -> tuple[list[Case], str]:
    cases, version = load_dataset()
    if config.dataset_hash and config.dataset_hash != version:
        if data_root is None:
            raise ValueError("Custom dataset requires a local data root")
        path = data_root / "datasets" / f"{config.dataset_hash}.json"
        if not path.exists():
            raise ValueError("Unknown dataset version")
        cases, version = load_dataset(path)
        if version != config.dataset_hash:
            raise ValueError("Dataset snapshot hash mismatch")
        if config.agent.startswith("demo-"):
            raise ValueError("Demo scripts only support the bundled dataset; use a real model for custom tasks")
    known = {c.id for c in cases}
    if set(config.case_ids) - known:
        raise ValueError("Unknown case ID")
    selected = [c for c in cases if (config.split == "all" or c.split == config.split)
                and (not config.case_ids or c.id in config.case_ids)]
    if not selected:
        raise ValueError("Selection has no cases")
    return selected, version


def import_dataset(data_root: Path, upload: DatasetUpload):
    raw = upload.model_dump()
    cases, version = validate_dataset(raw)
    folder = data_root / "datasets"
    folder.mkdir(exist_ok=True)
    path = folder / f"{version}.json"
    if not path.exists():
        path.write_text(json.dumps(raw, ensure_ascii=False, sort_keys=True, indent=2), "utf-8")
    return {"dataset_hash": version, "version": upload.version, "count": len(cases)}


def list_datasets(data_root: Path):
    paths = [DATASET] + sorted((data_root / "datasets").glob("*.json"))
    result = []
    for path in paths:
        raw = json.loads(path.read_text("utf-8"))
        cases, version = validate_dataset(raw)
        result.append({"dataset_hash": version, "version": raw.get("version", "unknown"),
                       "count": len(cases), "builtin": path == DATASET})
    return result
