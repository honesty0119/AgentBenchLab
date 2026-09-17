"""Native Inspect entry point; defaults to offline known-answer scripts."""
from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.scorer import Score, accuracy, scorer
from inspect_ai.solver import solver

from agentbench.schema import RunConfig

from .dataset import load_cases
from .grading import grade
from .runner import execute


@scorer(metrics=[accuracy()])
def procurement_constraints():
    async def score(state, target):
        rules = state.metadata["grade"]
        return Score(value="C" if rules["label"] == "pass" else "I",
                     explanation="; ".join(rules["diagnostic_hints"]) or "Rules pass; semantic pending",
                     metadata=rules)
    return score


@task
def procurement(agent="demo-recovery", split="all"):
    if agent not in {"demo-baseline", "demo-recovery"}:
        raise ValueError("This Inspect CLI is an offline demonstration; use the bounded model runner")
    cases = load_cases(split)
    by_id = {c.id: c for c in cases}
    config = RunConfig(agent=agent, tool_timeout_seconds=0.05)

    @solver
    def run_procurement():
        async def solve(state, generate):
            case = by_id[state.metadata["case_id"]]
            result = await execute(case, config)
            state.metadata["grade"] = grade(case, result)
            state.metadata["agent_result"] = result
            state.completed = True
            return state
        return solve

    return Task(name="agentbench_procurement", dataset=[Sample(id=c.id, input="\n".join(c.turns),
                 metadata={"case_id": c.id, "family": c.family, "review_status": "draft"}) for c in cases],
                solver=run_procurement(), scorer=procurement_constraints(), version=1,
                metadata={"demo": True, "semantic": "pending", "note": "Known-answer synthetic scripts"})
