# Interview evidence → implemented project modules

Research date: 2026-09-15. Candidate posts are self-reports, not company-wide question banks.
Xiaohongshu public search did not yield verifiable note bodies and browser extraction timed out.
No Xiaohongshu popularity statistics or interview-note claims are made here. Reposts and marketing
answer compilations were not counted as independent evidence.

| Source | Topic (paraphrased) | Evidence in this repository |
|---|---|---|
| [百度 AI 测开](https://api-cdn.nowcoder.com/feed/main/detail/cd8e446a6ec14edfa53cf4c7b6864c4d?sourceSSR=post) | Golden Set、Judge、文档任务、分层测试 | dataset.py, grading.py, judge.py, human review |
| [字节 AI 测开](https://www.nowcoder.com/feed/main/detail/06f03441a8e0402a8e15a326262b7803) | 表格、样本覆盖、Judge Prompt、重试、延迟 | table/editing tasks, rubric v1/v2, fault injection, latency report |
| [字节 Agent 测评](https://www.nowcoder.com/feed/main/detail/c591854483f64b269d471ef7da1a7130?sourceSSR=dynamic) | Agent 测试集与 Skill 路由测试 | task categories, tool behavior assertions; no full Skill adapter claim |
| [快手 Agent 二面](https://www.nowcoder.com/feed/main/detail/f78abe9951f74a8381a17d3876576213?sourceSSR=dynamic) | RAG 指标、数据来源、上下文 | source provenance, evidence diagnostics, context budget experiment |
| [北京 B 端 AI 面经](https://www.nowcoder.com/feed/main/detail/43d1bfd538dc43edb6739e00dabf054c) | 事件适配、分场景评测、数字来源 | normalized results, category reports, manifests |
| [无界智索评测实习](https://www.nowcoder.com/feed/main/detail/cbe27c1f5e6d42b9953806bd850405ea) | ROUGE-L、LCS、框架工程 | metrics.py with bounded-space LCS, CI, Inspect Worker |

## Demo walk-through (10 minutes)

1. `uv run agentbench demo` creates the baseline/recovery fixture runs. Open the experiments page.
2. View the baseline's `growth`: the script returns the wrong percentage scale; inspect numeric assertion.
3. View `edit-status`: fluent completion text hides an unintended change; inspect file assertion and artifacts.
4. View `lost-write`: first write succeeds but the injected response is lost; naive retry duplicates the todo.
   Recovery uses a stable idempotency key. Inspect final state, not the completion text alone.
5. Compare the two fixture runs. Show the seven changed tasks, category breakdown and statistical method.
   State clearly: this is a verification of the evaluation pipeline using scripted examples.
6. Review one output manually. With a configured Judge, run v1/v2 and inspect the human-Judge comparison.
   Do not manufacture multi-rater agreement or present developer-set fixture labels as human research.

## Resume wording after implementing this version

**AgentBench Lab｜文档与知识库 Agent 评测平台（独立开发）**

- 基于自研 Agent Runtime 与 Inspect AI 构建本地评测流程，设计 23 条合成任务覆盖
  文档问答、表格计算、定向编辑、多轮约束及故障恢复；通过数值、文件和状态断言保存可核验判分依据。
- 实现任务集版本快照、隔离执行、故障注入、轨迹诊断、人工复核和 Judge 校准接口，
  支持配对实验、覆盖率与回归报告，并以自动化测试验证重复写入、评分失败和中断恢复等边界。

Only add actual model names, evaluation scores and human calibration results after running them.
Demo success rates are not model-performance results. Use actual development dates.
