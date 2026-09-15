# Code provenance

AgentBench Lab evolves the author's earlier project:

- Repository: https://github.com/honesty0119/Agent_design
- Imported source commit: `41785e64ba897b1cc28459db9d488d50018c3fd3`
- Imported directories: `app/`, and the original `tests/test_context.py`,
  `tests/test_runtime.py`, `tests/test_tools.py`, `tests/helpers.py` and package markers.
- Source obtained through GitHub's public raw endpoint, pinned to that commit.
- The new evaluator uses the original `AgentRuntime`, `SessionStore`, `ContextBuilder`,
  bounded calculator, tool interfaces and registry. Its virtual tools and instrumented
  model adapter live under `agentbench/`.
- The original chat UI/API remain under `app/` for reference. The supported Lab launch
  command is `uv run agentbench serve`, not `uvicorn app.main:app`.

Other projects are dependencies or design references, not vendored code:

- Inspect AI: https://github.com/UKGovernmentBEIS/inspect_ai
- HKUDS/nanobot: https://github.com/HKUDS/nanobot (future adapter reference)
- HKUDS/OpenHarness: https://github.com/HKUDS/OpenHarness (runtime design reference)
- Harbor: https://github.com/harbor-framework/harbor (future terminal-task reference)

Source files under `app/` retain their original content. This repository does not
assert a new blanket license over the imported project. Dependency licenses remain
with their respective authors.
