"""Offline Inspect entry: uv run inspect eval agentbench/domains/it_support/inspect_tasks.py"""
from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.scorer import Score, accuracy, scorer
from inspect_ai.solver import solver

from agentbench.domains.it_support.dataset import load_cases
from agentbench.domains.it_support.grading import grade
from agentbench.domains.it_support.runner import execute


@scorer(metrics=[accuracy()])
def business_constraints():
    async def score(state, target):
        result = state.metadata["grade"]
        return Score(value="C" if result["rules"] == "pass" else "I", metadata=result,
                     explanation="Rule-only synthetic harness check; semantic quality pending")
    return score


@task
def it_support(variant: str = "recovery", split: str = "all"):
    cases, dataset_hash = load_cases()
    if variant not in {"recovery", "skip-probe", "duplicate"}:
        raise ValueError("Inspect entry is deliberately offline; use bounded model CLI for real runs")
    if split not in {"all", "dev", "test", "challenge"}:
        raise ValueError("Unknown split")
    selected = {c.id: c for c in cases if split == "all" or c.split == split}

    @solver
    def run_domain():
        async def solve(state, generate):
            case = selected[state.sample_id]
            result = await execute(case, variant=variant)
            state.metadata["result"] = result
            state.metadata["grade"] = grade(case, result)
            state.completed = True
            return state
        return solve

    return Task(name="it_support_draft", dataset=[Sample(id=c.id, input=c.turns[0].message)
                for c in selected.values()], solver=run_domain(), scorer=business_constraints(),
                metadata={"demo": True, "dataset_hash": dataset_hash, "review_status": "draft"})
