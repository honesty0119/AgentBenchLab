"""Optional OpenJudge backend. Thresholds are an explicit, uncalibrated starting policy."""
import asyncio
import hashlib
import json
import math
from importlib.metadata import version
from pathlib import Path

from agentbench.schema import digest


async def evaluate(store, trial_id, payload, material, key):
    model = None
    try:
        from openjudge.models import OpenAIChatModel
        from openjudge.graders.common.relevance import RelevanceGrader
        from openjudge.graders.common.hallucination import HallucinationGrader
        payload["package_version"] = version("py-openjudge")
        payload["rubric_hash"] = digest({"adapter": hashlib.sha256(Path(__file__).read_text("utf-8").encode()).hexdigest(),
                                        "version": payload["package_version"]})
        payload["cohort"] = digest({k: payload.get(k) for k in ("model", "endpoint", "rubric", "rubric_hash", "backend")})
        model = OpenAIChatModel(model=payload["model"], api_key=key, base_url=payload["endpoint"],
                               temperature=0, max_tokens=1500, max_retries=0, timeout=45)
        data = {"query": "\n".join(material["task"]), "response": material["answer"],
                "context": json.dumps({k: v for k, v in material.items() if k not in {"answer", "task"}}, ensure_ascii=False)}
        scores = {}
        async with asyncio.timeout(100):
            for name, cls in (("relevance", RelevanceGrader), ("grounding", HallucinationGrader)):
                result = await cls(model=model).aevaluate(**data)
                score = getattr(result, "score", None)
                if isinstance(score, bool) or not isinstance(score, (float, int)) or not math.isfinite(score) or not 1 <= score <= 5:
                    raise ValueError("OpenJudge did not return a valid 1-5 score")
                scores[name] = {"score": score, "reason": getattr(result, "reason", "")}
        values = [s["score"] for s in scores.values()]
        label = "pass" if min(values) >= 4 else ("fail" if min(values) <= 2 else "uncertain")
        payload.update(status="completed", output={"label": label, "dimensions": scores,
                       "policy": "both>=4 pass; any<=2 fail; otherwise uncertain",
                       "calibrated": False, "evidence_validation": "OpenJudge reasons; no native quote validation"})
    except Exception as exc:
        payload["error_type"] = type(exc).__name__
        if isinstance(exc, ImportError):
            payload["setup"] = "Install optional dependency: uv sync --extra openjudge"
    finally:
        if model is not None:
            client = getattr(model, "client", None)
            if client is not None and hasattr(client, "close"):
                await client.close()
    store.annotate("judgements", trial_id, payload)
    return payload
