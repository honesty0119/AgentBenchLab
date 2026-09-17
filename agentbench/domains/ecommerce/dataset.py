import json
from pathlib import Path

from agentbench.schema import digest

from .schema import EcommerceCase

FIXTURES = Path(__file__).parent / "fixtures"


def load_cases(path=None):
    data = json.loads(Path(path or FIXTURES / "cases.json").read_text("utf-8"))
    cases = [EcommerceCase.model_validate(c) for c in data["cases"]]
    if not cases or len({c.id for c in cases}) != len(cases):
        raise ValueError("empty dataset or duplicate case ID")
    families = {}
    for case in cases:
        if families.setdefault(case.family, case.split) != case.split:
            raise ValueError("task family leaks across splits")
    return cases, digest(data)
