from __future__ import annotations

from app.context import ContextBuilder
from app.database import SessionStore


def test_context_compresses_old_messages(tmp_path):
    store = SessionStore(str(tmp_path / "context.db"))
    session = store.create_session()
    for index in range(12):
        store.add_message(session["id"], "user", f"user fact {index} " + "x" * 100)
        store.add_message(session["id"], "assistant", f"answer {index} " + "y" * 100)
    builder = ContextBuilder(store, "system", max_context_chars=1600, recent_messages=4)
    context, stats = builder.build_with_stats(session["id"])
    assert context[0]["role"] == "system"
    assert "history summary" in context[1]["content"]
    assert "user fact" in context[1]["content"]
    assert stats["compressed"] is True
    assert stats["summary_included"] is True
    assert stats["original_chars"] > 1600
    assert stats["final_chars"] <= 1600
    assert stats["recent_messages_configured"] == 4
