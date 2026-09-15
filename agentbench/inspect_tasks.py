"""Native Inspect tasks used by the Worker and by `inspect eval`."""
from __future__ import annotations

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.scorer import Score, accuracy, scorer
from inspect_ai.solver import solver

from agentbench.agents import execute_case
from agentbench.dataset import select_cases
from agentbench.grading import grade
from agentbench.schema import RunConfig


@scorer(metrics=[accuracy()])
def hard_constraints():
    async def score(state, target):
        result = state.metadata.get("rule_grade")
        if result is None:
            return Score(value="I", explanation="Unscored: " + state.metadata.get("error", "cancelled"))
        return Score(value="C" if result["label"] == "pass" else "I",
                     explanation="; ".join(result["diagnostic_hints"]) or "All hard constraints passed",
                     metadata=result)
    return score


def make_task(cases, config, trials, store=None, run_id=None):
    by_id = {c.id: c for c in cases}

    @solver
    def run_agent():
        async def solve(state, generate):
            trial_id = state.metadata["trial_id"]
            if store and store.get_run(run_id)["cancel"]:
                state.metadata["error"] = "cancelled"
                state.completed = True
                return state
            if store:
                store.set_trial(trial_id, "running")
            try:
                case = by_id[state.metadata["case_id"]]
                result = await execute_case(case, config)
                scores = grade(case, result)
                state.metadata["rule_grade"] = scores
                state.metadata["agent_result"] = result
                if store:
                    store.set_trial(trial_id, "completed", result, scores)
            except Exception as exc:
                state.metadata["error"] = type(exc).__name__
                if store:
                    store.set_trial(trial_id, "error", {"error_type": type(exc).__name__})
            state.completed = True
            return state
        return solve

    samples = [Sample(id=t["id"], input="\n".join(by_id[t["case_id"]].turns),
                      metadata={"trial_id": t["id"], "case_id": t["case_id"], "repeat": t["repeat"]})
               for t in trials]
    return Task(dataset=samples, solver=run_agent(), scorer=hard_constraints(), name="agentbench_documents",
                version=1, metadata={"agent": config.agent, "demo": config.agent.startswith("demo-"),
                                     "note": "Inspect accuracy counts unscored as incorrect; use Lab report for error/coverage breakdown."})


@task
def documents(agent: str = "demo-recovery", split: str = "all"):
    config = RunConfig(agent=agent, split=split)
    cases, _ = select_cases(config)
    trials = [{"id": c.id, "case_id": c.id, "repeat": 0} for c in cases]
    if agent == "openai-compatible":
        import os
        config.model = os.environ.get("AGENTBENCH_MODEL", "")
    return make_task(cases, config, trials)
