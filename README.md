# AgentBench Lab

面向文档与知识库 Agent 的本地评测、诊断与回归平台。

基于 [honesty0119/Agent_design](https://github.com/honesty0119/Agent_design) 的 Agent Runtime 演进。
评测数据、评分器与被测 Agent 分离，保留规则断言、工具轨迹、人工复核和 Judge 校准证据。

> 当前内置任务为自行构造的合成样本，审核状态为 draft。演示代理是确定性脚本，
> 知道演示答案，仅用于验证执行与判分流程。演示通过率不是任何真实模型的能力指标。

## 快速开始

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。

```powershell
uv sync --locked
uv run agentbench demo
uv run agentbench serve
```

浏览器打开 http://127.0.0.1:8765 。`serve` 会启动独立评测 Worker；默认仅绑定本机。

## 能做什么

| 面试与工程问题 | 已实现的证据 |
| --- | --- |
| 评测集怎么构建、避免泄漏？ | 23 条合成任务，8 类场景；任务家族隔离；来源、审核状态、JSON 导入、不可变内容哈希 |
| 怎么判断 Agent 真正完成任务？ | 数值、引用路径、文件变化、待办终态与终止原因逐项断言 |
| Agent 失败在哪里？ | 原始输入、最终产物、逐步工具轨迹；超时、空返回、写成功但响应丢失的故障注入 |
| 模型评分可信吗？ | Judge v1/v2、证据片段校验、人工盲审入口、分歧统计和 Cohen’s κ |
| 改进是否稳定？ | 独立重复试验、配对任务 Bootstrap 区间、逐题回归与 CLI 门禁 |
| 怎么复现？ | Inspect 原生执行日志；配置、数据、提示词、源代码与依赖版本清单；断点恢复 |

内置场景：事实问答、多文档综合、表格计算、定向编辑、缺失/冲突、多轮上下文、故障恢复、状态操作。
核心执行沿用原项目 Runtime；评测调度接入 [Inspect AI](https://inspect.aisi.org.uk/)。

## 接入真实模型

复制 `.env.example` 为 `.env`，填写以下变量后重启服务。兼容接口必须支持串行 Tool Calling。

```dotenv
AGENTBENCH_API_KEY=your-key
AGENTBENCH_BASE_URL=https://your-provider.example/v1
AGENTBENCH_MODEL=your-model
# 可选：独立的语义评分模型
AGENTBENCH_JUDGE_API_KEY=your-judge-key
AGENTBENCH_JUDGE_BASE_URL=https://your-provider.example/v1
AGENTBENCH_JUDGE_MODEL=your-judge-model
```

在网页新建实验时选择真实模型，或运行：

```powershell
uv run agentbench run --config examples/real-model.json
uv run agentbench run --config examples/context-small.json --queue
```

`run` 默认等待指定实验完成；已有 Worker 时由其执行，否则本命令按队列顺序执行。
`--queue` 只入队，需保持网页服务或独立 `uv run agentbench worker` 运行。
真实接口会发送任务材料并产生费用；密钥只从环境读取，不保存到报告或网页。

## 演示与回归

`uv run agentbench demo` 运行两组预设脚本：基线有 7 个预设失败任务，恢复脚本通过全部 23 条。
这是评分器与执行链路的验收数据，**不是真实模型提升实验**。

```powershell
uv run agentbench report RUN_ID --output artifacts/local/report.json
uv run agentbench compare BASE_RUN_ID CANDIDATE_RUN_ID
uv run agentbench compare BASE_RUN_ID CANDIDATE_RUN_ID --gate
```

门禁拒绝不完整/不可比实验、任何通过→失败的任务，以及超过 `--max-drop` 的总体退化。
运行结果在 `data/`，Inspect 日志在 `data/inspect_logs/`；均不提交 Git。

## 导入自己的任务集

网页「评测集 → 导入 JSON 任务集」支持与 [cases.json](agentbench/fixtures/cases.json) 相同的
`version` + `cases` 结构。导入时校验任务 ID、家族拆分和证据路径，生成独立哈希版本。
自定义任务使用真实模型执行；演示脚本只支持内置任务。
完整字段定义见 [schema.py](agentbench/schema.py)。不要把未审核样本标记成已审核。

## 工程结构

```text
app/                      原项目 Runtime、工具与会话实现
agentbench/               任务模型、隔离环境、执行、判分、统计、API
  fixtures/               合成任务和独立演示脚本
  static/                 无构建依赖的中文网页工作台
tests/                    原项目测试与评测集成测试
examples/                 真实模型与上下文实验配置
docs/                     技术设计、面经映射与简历表述
```

## 验证

```powershell
uv run pytest
uv run ruff check .
node --check agentbench/static/app.js
uv run python scripts/ci_smoke.py
uv build
```

GitHub Actions 在 Windows / Ubuntu 执行测试、静态检查和 Inspect 回归演示。
HTTP 模型与 Judge 接口通过模拟响应测试；首次发布未运行收费真实模型评测。

## 设计资料与边界

- [技术设计与评测契约](docs/design.md)：隔离、统计口径、失败处理、恢复和评分边界。
- [面经问题映射、演示脚本与简历改写](docs/interview-map.md)：可核验的牛客来源、开源参考与项目讲述。
- [原项目来源](UPSTREAM.md)：导入的源码版本及归属。

v0.1 聚焦 Markdown/CSV 虚拟环境。引用路径覆盖不等于语义正确，搜索不是向量检索，
上下文预算按字符计量。人工与 Judge 评分独立保存，不覆盖硬约束结果。
暂未包含 DOCX/PDF 版式、多模态、nanobot 适配器或生产级多租户部署。
