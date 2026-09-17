import json
from pathlib import Path

from agentbench.schema import digest

from .schema import ITCase

FIXTURES = Path(__file__).parent / "fixtures"


def load_cases():
    raw = json.loads((FIXTURES / "cases.json").read_text("utf-8"))
    cases = [ITCase.model_validate(c) for c in raw["cases"]]
    families = {}
    ids = set()
    for case in cases:
        if case.id in ids:
            raise ValueError("Duplicate case ID")
        ids.add(case.id)
        if families.setdefault(case.family, case.split) != case.split:
            raise ValueError("A task family cannot span splits")
    return cases, digest([c.model_dump() for c in cases])
