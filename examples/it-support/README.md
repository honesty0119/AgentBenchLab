# IT 工单评测复现

在本场景 worktree 根目录执行。全部命令默认离线，输出目录与文档场景分开。

```powershell
uv sync --locked
uv run python -m agentbench.domains.it_support demo
uv run python -m agentbench.domains.it_support serve
```

打开 <http://127.0.0.1:8768>。网页只读展示保存的 `data/it-support/recovery.json`，
重新运行演示后重启服务以加载新报告；不会由页面触发模型或任何真实 IT 操作。
用 `--report data/it-support/skip-probe.json` 查看故意遗漏恢复证据的失败对照。

建议按以下顺序讲解：

1. `it-dns-cache`：诊断网络和 DNS，模拟刷新、验证、关单。
2. `it-dns-ineffective`：相同修复受理但未恢复，必须转交并记录下一步。
3. `it-diagnostic-deadline`：真实异步取消留下 cancelled 事件，后续调用恢复。
4. `it-create-response-lost`：建单已提交但响应丢失，重复相同幂等键只返回原工单。
5. `it-new-auth-failure`：上一轮成功，下一轮权限失败，旧证据不能支持新结论。

## Inspect 与测试

```powershell
uv run python -m inspect_ai eval agentbench/domains/it_support/inspect_tasks.py --model mockllm/model --log-dir data/it-support/inspect-logs --max-samples 2
uv run --extra openjudge pytest -q
uv run ruff check .
uv run python scripts/ci_smoke.py
uv build
```

Windows 若 `uv run inspect` 入口程序被应用控制策略阻止，可以使用上面的 Python 模块入口。
Inspect 的 accuracy 是规则验收，不能替代报告中的整体结论。Windows 控制面可能提示 AF_UNIX 不可用，
该可选控制面不影响本次离线任务执行和日志生成。

## 显式预算的单题模型入口（尚未运行）

先自行配置 `AGENTBENCH_API_KEY`、`AGENTBENCH_BASE_URL`，并将 model-budget.json 中的模型占位符改为实际模型。
配置由 shell 环境读取；本入口不自动加载主工作区的 .env，也不会读取其他场景密钥。

```powershell
uv run python -m agentbench.domains.it_support model --case it-dns-cache --config examples/it-support/model-budget.json --request-budget 20
```

这条命令会产生模型费用；未获得用户的具体模型和预算时不要执行。
示例上限：20 次决策，单次输出最多 1024 tokens、请求最多 160000 字符，总期限 120 秒，HTTP 自动重试为 0。
请求字符数不是输入 token 数，也不是美元预算；需要货币上限时应配合供应商硬限额。
该入口一次只接受一题。仅复用 v0.2 的 Runtime/模型传输/上下文组件，不接入 v0.2 数据库、校准网页和回归门禁。

## 独立评分与 Judge 接口

报告保存逐轮状态、真实工具结果、前后状态、故障、上下文请求、配置/数据/代码指纹；可离线调用
`grade(ITCase.model_validate(record['case']), record['result'])` 重新评分，不调用模型。
`semantic.prepare_payload(case, result, judge_config)` 构建领域 rubric 和证据；外部配置的 Judge 可输出
`Assessment`，交给 `grade(..., semantic=assessment)`。结果指纹、配置组、引用片段均保留并校验。
该接口不自行联网；当前未运行 Judge、未校准阈值、未产生独立人工审核记录。
规则失败始终整体失败；合格语义记录缺失/过期/无法核对时保持 `pending_semantic`。
