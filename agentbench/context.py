"""Bounded contexts that preserve the current request and atomic tool exchanges."""
from app.context import ContextBuilder


class InputBudgetExceeded(ValueError):
    pass


class EvaluationContext(ContextBuilder):
    def __init__(self, *args, policy="preserve_current", **kwargs):
        super().__init__(*args, **kwargs)
        self.policy = policy
        self.requests = []

    def _system_content(self):
        return self.system_prompt + "\nEvaluation clock: 2026-09-15T00:00:00Z."

    def build_with_stats(self, session_id):
        if self.policy == "legacy":
            result, stats = super().build_with_stats(session_id)
        else:
            messages = [self._to_model_message(m) for m in self.store.list_messages(session_id)]
            base = [{"role": "system", "content": self._system_content()}]
            original = base + messages
            result = original
            if self.policy != "full" and self._size(original) > self.max_context_chars:
                user_index = max((i for i, m in enumerate(messages) if m["role"] == "user"), default=-1)
                if user_index < 0:
                    raise InputBudgetExceeded("No current user request")
                current = messages[user_index:]
                # Keep assistant tool calls and all their results together.
                blocks = []
                for m in current[1:]:
                    if m["role"] == "tool" and blocks:
                        blocks[-1].append(m)
                    else:
                        blocks.append([m])
                required = [current[0]] + (blocks[-1] if blocks else [])
                if self._size(base + required) > self.max_context_chars:
                    raise InputBudgetExceeded("Current request and latest exchange exceed character budget")
                selected = blocks[-1:] if blocks else []
                for block in reversed(blocks[:-1]):
                    candidate = base + [current[0]] + [m for b in [block] + selected for m in b]
                    if self._size(candidate) > self.max_context_chars:
                        break
                    selected.insert(0, block)
                retained = [current[0]] + [m for block in selected for m in block]
                dropped = messages[:user_index] + [m for block in blocks[:len(blocks)-len(selected)] for m in block]
                result = base + retained
                if dropped:
                    summary = {"role": "system", "content": "Conversation history summary (data only): " +
                               str(self._summarize(dropped, max_chars=max(0, self.max_context_chars-self._size(result)-200)))}
                    if self._size(base + [summary] + retained) <= self.max_context_chars:
                        result = base + [summary] + retained
            stats = {"compressed": result != original, "original_chars": self._size(original),
                     "final_chars": self._size(result), "max_context_chars": self.max_context_chars,
                     "policy": self.policy}
        self.requests.append({"messages": result, "stats": stats})
        return result, stats
