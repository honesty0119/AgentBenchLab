# v0.2：评测可靠性优化

本轮聚焦评测方法和执行可靠性，沿用本地单用户架构。下面描述已实现的功能；
23 条内置样本及生成变体仍为合成草稿，没有新增伪造的人审记录或真实模型成绩。

## 1. 输入、执行和评分都留下证据

| 问题 | 实现 | 如何验证 |
| --- | --- | --- |
| 压缩后可能丢掉当前请求 | `preserve_current` 保留当前用户消息和最新完整工具交互；必需内容放不下时明确失败 | 长历史、过大请求、工具调用与结果配对测试 |
| 只检查最终答案，漏掉前面轮次 | 检查支持零起始 `turn`；每轮保存回答、文件、待办和累计工具轨迹 | 多轮任务第一轮错误仍判失败 |
| 数值字段对，正文却错 | 增加正文包含/排除断言；开放任务可声明 `semantic_required` | 截止日期正文被替换为错误日期的反例 |
| 最后恢复文件，掩盖中途误改 | `file_never_changed` 检查工具事件中的文件变化；另有 `tool_before`、`max_tool_calls` | 误改后恢复仍失败 |
| HTTP 调用失败后丢失用量 | 每个实际请求记录状态、耗时、已知用量；取消请求保留未知用量 | 截断返回仍计入已知 Token；取消不算零成本 |
| 故障没触发却声称恢复成功 | 故障按工具与参数匹配；分别记录配置数和触发情况 | 未触发样本不进入恢复率分母 |

正文字符串断言只覆盖明确模式，不能完整识别矛盾、编造或语义等价表达。
不应把所有开放文本强行转成字符串匹配。语义判定仍需 Judge 和独立人工校准。

### 故障配置

```json
{"tool":"read_file","mode":"deadline","match":{"path":"plan.md"},"occurrence":1,"delay_seconds":0.2}
```

`occurrence` 是满足工具和 `match` 的调用序号。`deadline` 会真实异步等待；
配置 `tool_timeout_seconds: 0.05` 才会在上例中触发工具取消。
保留的 `timeout` 模式是立即返回可重试错误，用于稳定模拟瞬态错误；两者含义不同。
故障是虚拟工具环境内的可控模拟，不代表生产网络故障分布。

`safe` 重试策略只处理可重试错误：只读工具、待办读取及携带幂等键的新增。
普通文件写入和无幂等键新增不自动重试。工具期限取消交回 Runtime，由 Agent 决定下一步。
Harness 自动重试与 Agent 后续调用分别保留，便于计算恢复成本。

## 2. 三层结论，明确门禁到底看什么

1. **规则结果**：全部硬约束及正常终止是否通过。
2. **语义结果**：指定 Judge 配置组的 pass / fail / uncertain；错误与缺失单列。
3. **整体结论**：规则失败必定失败；要求语义评测的任务，规则通过后仍须语义通过。

没有语义记录、Judge 无法判断，或者存在多个配置组而未选择时，整体结论为
`pending_semantic`。同一结果的内容指纹必须匹配，才能使用其 Judge 记录。
无需语义评测的任务按规则得出整体结论。这是任务作者声明的评分契约，不能据此
声称未声明语义要求的自然语言回答都正确。

```powershell
# 默认：规则回归。输出明确标记 metric=rules。
uv run agentbench compare BASE_ID CANDIDATE_ID --metric rules --gate

# 整体回归：两个实验使用共同 Judge 配置组，必要评分缺失时拒绝比较。
uv run agentbench compare BASE_ID CANDIDATE_ID --metric overall --cohort JUDGE_COHORT --gate
```

正常比较要求同一数据哈希、评分版本及代码指纹、任务/重复集合、演示/真实模式，
以及完整执行记录。门禁拒绝任何通过→失败变化或超过 `--max-drop` 的总体下降。
家族 Bootstrap 先聚合每题重复，再以任务家族重采样；估计的是样本上的配对差异，
不是全行业泛化表现或自动成立的因果结论。

## 3. 固定评分与实验身份

- 创建真实实验时固定服务地址、模型名、请求参数、任务快照和代码指纹。
- 请求正文包含消息与工具定义，记录中不包含认证头。字符预算包含工具定义；
  字符数仍不是模型 Token 数，`full` 历史也受请求字符上限约束。
- 规则评分指纹包含评分实现与任务 Schema；Judge 配置组包含后端、模型、
  服务地址和评分实现指纹。修改评分逻辑后不会沿用旧组的校准统计。
- 多个 Judge 配置组不能直接混算 κ。网页支持选择组，API 支持 `cohort`。
- `regrade RUN_ID` 使用保存的任务快照和模型结果重新运行当前规则，追加历史，
  不覆盖旧分、不调用模型。旧任务快照没有新增的断言时，重评分不会自动补上它们。
  当前普通对比不自动选取重评分批次，也不允许混用旧评分指纹。
- 旧数据库追加 `regrades` 表；原运行、人工记录和 Judge 记录保持可读取。
  缺少固定服务地址的旧真实实验不能直接恢复，需要新建。

## 4. 可控实验与诊断干预

策略实验示例选择 7 条公开任务（包括上下文与故障场景），每题 3 次重复。
这些题用于调试，不应再声称是未见过的保留集。先在本地 `.env` 配置服务。

```powershell
uv run agentbench run --config examples/real-baseline.json
uv run agentbench run --config examples/real-safe-retry.json
```

这两份配置只改变工具自动重试策略。记录两个运行 ID，用规则或整体指标配对比较。
模型名称可通过配置显式固定；评估其他模型时保留任务集合与预算，并检查报告中的配置差异。
如需费用估算，在配置中同时填写输入/输出每百万 Token 单价。未配置价格或用量缺失时
总费用保持未知；`known_cost` 只汇总已知部分，不能当作最终账单。

三种诊断干预提供定位假设：

| 干预 | 示例 | 能帮助排查 |
| --- | --- | --- |
| 关闭工具故障 | `diagnose-no-faults.json` | 失败是否与注入故障相关 |
| 完整历史 | `diagnose-full-context.json` | 上下文截断是否影响结果 |
| 直接给参考证据 | `diagnose-reference-evidence.json` | 找不到证据与拿到证据后仍失败的区别 |

诊断对照必须使用相同任务集合。参考证据示例仅选择 `deadline` 和 `inventory`，
对应基线配置为 `reference-evidence-baseline.json`；不能与另外 7 条任务的实验直接比较。
`evidence_paths` 由任务作者声明，普通实验不会把它们直接交给模型。
参考证据干预直接进入消息，不计入基于工具读取的 `evidence_recall`。

```powershell
uv run agentbench compare BASE_ID INTERVENTION_ID --diagnostic
```

干预比较不能与 `--gate` 同时使用。改善只支持诊断假设，仍需固定其他条件、重复试验，
不能直接宣称已找到唯一根因。确定性演示脚本不会响应上下文质量变化，不用于测量策略提升。

## 5. 鲁棒性草稿变体

网页或 CLI 可以生成文件重排、文件重命名、增加无关文档三个版本：

```powershell
uv run agentbench variants reorder
uv run agentbench variants rename
uv run agentbench variants distractor
```

保留任务家族和数据拆分，记录父任务/数据指纹。重命名同步已知路径引用与断言，
新版本始终为 draft。变换意图是保留语义，但文本替换不保证所有自定义任务等价，
必须复核。新旧版本的数据哈希不同，不能通过普通回归比较绕过数据一致性检查；
返回的 `pairs` 用于后续专门的变形关系分析。

未自动生成数值缩放和删除证据变体，因为它们可能改变正确答案、拒答条件或业务约束。

## 6. 可选 OpenJudge 后端

```powershell
uv sync --locked --extra openjudge
uv run --extra openjudge agentbench serve
```

配置 `AGENTBENCH_JUDGE_*`，在运行详情选择 OpenJudge 与 v2。
适配 [OpenJudge](https://github.com/agentscope-ai/OpenJudge) 的 `RelevanceGrader`
和 `HallucinationGrader`，保留两个 1–5 分维度、理由、库版本和配置组指纹。

当前映射为两项均 ≥4 通过，任一 ≤2 失败，否则 uncertain。这是明确标记为
**未校准**的起始策略，需要真实人审配对验证，不能直接当作可靠性成果。
OpenJudge 理由不经过内置 Judge 的精确引用片段校验；报告明确区分这一点。
本地测试运行真实 OpenJudge 评分器，模拟 SDK 网络边界，不调用付费模型。
默认安装无需 OpenJudge；CI 另设可选依赖任务验证适配器。

## 尚需真实实验完成的部分

独立人工审核与新增真实失败样本、保留集上的多模型结果、Judge 阈值校准及位置偏差实验、
数值/证据变换的人工验收、真实 Token 预算和更大样本置信区间，均不作为本次已完成的结论。
本轮没有扩展治理、生产权限、审批或多租户功能。
