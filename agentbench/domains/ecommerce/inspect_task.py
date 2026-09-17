"""Standalone Inspect task; does not alter the document benchmark registry."""
from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.scorer import Score, accuracy, scorer
from inspect_ai.solver import solver

from agentbench.domains.ecommerce.dataset import load_cases
from agentbench.domains.ecommerce.grading import grade
from agentbench.domains.ecommerce.runner import execute_case


@task
def ecommerce(split: str = "all", variant: str = "recovery"):
    if split not in {"all", "dev", "test", "challenge"} or variant not in {"recovery", "baseline"}:
        raise ValueError("Unknown split or script variant")
    cases, dataset_hash = load_cases()
    cases = [c for c in cases if split == "all" or c.split == split]
    by_id = {c.id: c for c in cases}

    @solver
    def execute():
        async def solve(state, generate):
            case = by_id[state.sample_id]
            result = await execute_case(case, variant=variant)
            state.metadata["result"] = result
            state.metadata["grade"] = grade(case, result)
            state.completed = True
            return state
        return solve

    @scorer(metrics=[accuracy()])
    def outcome():
        async def score(state, target):
            result = state.metadata["grade"]
            return Score(value="C" if result["rules"] == "pass" else "I", metadata=result,
                         explanation="Hard rules only; scripted demo, overall semantic assessment pending.")
        return score

    return Task(name="agentbench_ecommerce", version=1,
                dataset=[Sample(id=c.id, input="\n".join(c.turns), metadata={"family": c.family}) for c in cases],
                solver=execute(), scorer=outcome(),
                metadata={"demo": True, "dataset_hash": dataset_hash, "review_status": "draft"})
