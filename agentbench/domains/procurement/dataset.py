import json
from pathlib import Path

from agentbench.schema import Case, Check

from .environment import DRAFT_PATH, STATE_PATH, encode
from .models import ProcurementCase


def load_cases(split="all"):
    payload = json.loads((Path(__file__).parent / "fixtures" / "cases.json").read_text("utf-8"))
    cases = [ProcurementCase.model_validate(x) for x in payload["cases"]]
    if len({c.id for c in cases}) != len(cases):
        raise ValueError("Duplicate case id")
    families = {}
    for case in cases:
        if case.family in families and families[case.family] != case.split:
            raise ValueError("Family leakage across splits")
        families[case.family] = case.split
    return [c for c in cases if split == "all" or c.split == split]


def runtime_case(case):
    return Case(id=case.id, family="procurement-" + case.family, category="procurement", split=case.split,
                title=case.title, turns=case.turns,
                files={STATE_PATH: encode(case.snapshots[0].model_dump(mode="json")), DRAFT_PATH: "null"},
                checks=[Check(kind="citations", expected=[STATE_PATH])], faults=case.faults,
                reference="Use the procurement domain scorer; generic citation checks are insufficient.",
                semantic_required=True, evidence_paths=[STATE_PATH])
