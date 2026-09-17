# 企业 IT 工单与排障评测（合成草稿）

## 方案与来源核验（2026-09-17）

基于 v0.2 `be24e829`，业务 Agent 是被测对象；环境、任务、判分和演示脚本分离。
所有服务、网络、账户、设备都是进程内仿真，不连接真实 IT 系统。

| 官方仓库及核验版本 | 核对的实现与许可 | 本场景取舍 |
| --- | --- | --- |
| [WorkArena](https://github.com/ServiceNow/WorkArena/tree/a772230a94cf1caf4166b8ead3983f3b3786455b) | `src/browsergym/workarena/tasks/form.py` 的 validate 查询实际表记录，检查字段和越界修改；`knowledge.py` 的问答检查接受答案子串。LICENSE 为 Apache-2.0 | 借鉴终态加不变量；不用答案子串代表完整排障质量。不移植代码/数据，不要求 ServiceNow 开发实例 |
| [BrowserGym](https://github.com/ServiceNow/BrowserGym/tree/9e779f087de9a65668b6974d11f9ce9816026e96) | README 的 AbstractBrowserTask 扩展点、Playwright 安装及独立 benchmark 设置；LICENSE Apache-2.0 | 本轮无浏览器操作评测；继续复用本仓库 Runtime，不引入浏览器运行成本 |
| [CRMArena](https://github.com/SalesforceAIResearch/CRMArena/tree/6d84f3d71305af0fd3d5ed3c1936b7887464455a) | `crm_sandbox/env/env.py` 区分查询/提交，支持按 reward_metric 判答案；`agents/tool_call_agent.py` 构造工具协议、记录交互；LICENSE.txt CC-BY-NC-4.0 | 借鉴任务/工具/评价分离和多轮信息采集；不复制代码、CRM 数据或金标，不引入非商业数据依赖 |
| [WorkBench](https://github.com/olly-styles/WorkBench/tree/49c7dfd00c03d384ec59ea57374f50b766aa5613) | `src/evals/evaluation.py` 执行动作并比较归一化终态，另检查副作用；LICENSE MIT | 接受不同正确路径；期望状态自行编写，不通过执行被测工具生成金标 |
| [Zammad](https://github.com/zammad/zammad/tree/eab302d2eeec715da156791ad7b12db1bdf6164a) | `app/models/ticket/state.rb` 将状态与状态类型关联，维护默认创建/跟进/关闭状态；LICENSE AGPL-3.0 | 仅借鉴工单生命周期概念，自行实现最小状态机；不复制 Rails 系统，不声称兼容其 API |

上述都是方法参考，没有 vendoring。上游成绩、oracle/cheat 演示和本项目脚本成绩不能互换。
本地查看的源码临时下载在 work/upstream，交付文档用固定提交链接追溯。

## 业务范围、用户与假设

用户是员工、服务台和评测开发者。典型任务：VPN/DNS/凭据/客户端故障排查，邮件配额、打印队列、Wi-Fi 和应用服务异常；用户补充设备和现象，维护工单、恢复验证、未解决转交。
假设每题只有一个员工请求和一台目标设备，可含无关工单用于检测越界修改。工单查询也属于任务。
没有 RBAC、审批、多租户、真实 ServiceNow/Jira、全量 CMDB 或真实修复命令。

## 数据与状态

每题包含独立初态、隐藏故障事实、可检索 KB、逐轮用户文字与环境变化、故障注入、人工可核对终态契约。
状态：`diagnosing` → `waiting_for_user` / `escalated` / `resolved`；新用户事实或服务复发可重新诊断。
`resolved` 必须引用同一设备/服务、当前版本的成功恢复探针；执行修复不等于恢复。
信息不足应列出具体待补字段；转交包含现象、诊断观察、已做动作、未解决原因和下一步。
每次变更递增服务版本，旧探针不能证明新版本恢复。工单有请求身份、设备、服务、状态、笔记和证据 ID。
创建提供幂等键，键与请求负载绑定；响应丢失后复用键或先查现有工单。不同键重复创建仍可发生，由评分检出。

## 领域工具与执行

工具：KB 搜索、服务状态、设备诊断、有限模拟修复、恢复探针、工单查/建/更新、澄清记录。
仅序列化当前可见诊断结果，隐藏故障和评分期望不进入模型提示。工具不接触宿主网络、文件或系统配置。
复用 AgentRuntime、EvaluationContext、ModelClient、ToolRegistry 与 Inspect；独立模块入口和端口 8768。
故障覆盖诊断超时（真实异步取消）、创建响应丢失、修复无效；记录注入是否触发和重试来源。

## 评分契约与失败分类

逐轮检查业务终态、目标工单数、受保护工单不变、澄清字段、转交记录与证据真实性；全过程检查不合法解决尝试、无证据解决和错误修复。
结构化最终答复中的状态必须与工单一致；开放自然语言诚实性、可执行性、解释质量留给 Judge 补充。规则失败不能被语义分覆盖。
未提供独立语义判定时整体为 pending_semantic；演示只报告规则验收。
失败归类：定位错误、信息遗漏、错误修复、虚假解决、重复工单、越界变更、丢失多轮变化、恢复失败、证据/协议错误及 harness/provider 错误。
评分器独立读取保存的状态和轨迹，不调用修复/探针函数生成期望。反例以手写状态和失败脚本验证。

## 样本与拆分

首批 30 条，自行编写的 draft 合成任务，按根因/交互机制家族固定 dev/test/challenge，不跨家族拆分。
覆盖正常、边界、信息不足、多轮改变、故障恢复；用根因、恢复条件和用户意图差异解释覆盖，不能用改名字冒充新题。
脚本读过全部样本，因此 test/challenge 只是预定拆分，不是本次演示的未见保留集。

## 对照与最小交付验收

离线 oracle 演示验证全部任务可达；故意跳过恢复确认、重复创建、旧证据关闭等失败脚本验证评分敏感性。
这些对照是 harness 测试，不能声称模型效果提升。未来真实对照固定模型、预算、任务和提示，仅改变恢复策略，按家族统计；人审与 Judge 校准尚待开展。
最小交付：30 题、KB、领域工具/状态机、独立评分、Runtime/Inspect 入口、离线演示及 8768 只读结果页。
验收：VPN 根据诊断分支处理；未恢复不能关单；超时重试可恢复；创建丢响应不重复；多轮复发不能复用旧证据；既有回归通过。
模型执行必须显式选择，配置 API 和预算，首轮仅离线验证。数据输出使用 `data/it-support/`，虚拟环境只在本 worktree。

## 合并与限制

新增内容局限 `agentbench/domains/it_support/`、`tests/domains/it_support/`、`examples/it-support/` 和本文件，不修改其他场景。
第一版使用独立领域运行与报告，不将领域契约冒充 v0.2 Markdown Case，也不保证现有网页可直接导入。
不自动合并 main。交付时记录具体测试、分支、PR、尚未运行的模型实验与诚实简历描述。

## 第一版落地

领域实现位于独立目录，公共接口改动为 **0**；唯一共享文件改动是在 `.gitignore` 排除 `work/`，避免临时调研材料进入源码包。`runner.py` 直接组合已有 Runtime、模型传输和上下文；
`inspect_tasks.py` 提供单独 Inspect task。`web.py` 只读展示保存报告，不触发 Agent。
工具返回当前/无关请求标记，不暴露带根因名称的 case ID；缺设备时工单设备字段留空，下一轮获得信息后再绑定。
成功探针必须实际送达 Agent 才能支持解决；后台成功但响应丢失仍不算可观察证据。

| 家族 | 数量 / 拆分 | 独立覆盖点 |
| --- | --- | --- |
| vpn_dns | 3 / dev | 缓存修复、修复无效、复发重新验证 |
| vpn_auth | 3 / test | 会话过期、用户 MFA、权限拒绝 |
| vpn_client | 3 / dev | 进程停止、版本过旧、当前已健康 |
| vpn_service | 3 / challenge | 共享故障、间歇异常、上游恢复 |
| clarification | 3 / dev | 缺设备、缺两个诊断字段、补充后继续 |
| ticket_identity | 3 / challenge | 创建丢响应、已有工单、同请求改症状 |
| transport | 3 / test | 诊断期限取消、探针超时、修复丢响应 |
| other_services | 3 / test | 邮件配额、打印队列、无线断连 |
| changed_condition | 3 / challenge | 新权限故障、撤回设备信息、新共享故障 |
| knowledge | 3 / dev | 未知根因、废弃规程、不可信评论 |

共有 8 条多轮题、38 个用户轮次。每题初态与各轮终态写在 cases.json；演示动作计划另存 demo_scripts.json。
数值只涉及工单数、版本和证据身份，无金额计算。人工可核对案例：DNS 初态 version=1，适用修复后
version=2 且 healthy，成功 probe 才能 resolved；无效修复则版本变化但 cause 仍 dns，必须 escalated。
复发会再递增版本，任何旧探针都失效。这些期望由案例作者显式书写，没有调用被测工具生成金标。

终态允许不同诊断顺序，以及响应丢失后“同键重试”或“查询对账”两条正确路径；测试分别验证。
错误修复、越界改票、无证据解决的尝试会保留为过程违规，即使工具拒绝或后续恢复正确也不会抹掉。

### 限制与简历表述

当前仿真按单一主要根因建模，探针为确定性结果；没有模拟真实网络时序、权限系统或复杂多设备依赖。
用户轮次由固定脚本给出，不是自由生成用户模拟器。知识检索为简单文本匹配。
无独立人审、无收费真实模型/Judge 实验、无生产部署。语义接口只导出领域 rubric、校验结果指纹/配置组及引用，
没有新增自动 Judge 服务；自然语言中的隐含虚假陈述仍需要语义评估，不能以结构化规则通过证明全部内容真实。
本场景报告尚未进入主工作台数据库、家族 Bootstrap、历史重评分 UI 和统一门禁；这些已有基础功能并不等于本轮已完成领域接入。

可用简历表述：**在 AgentBench Lab 中实现企业 IT 工单与排障评测场景，构建 30 条按 10 个家族拆分的合成草稿任务，
设计基于恢复证据版本的工单终态评分、逐轮不变量和响应丢失幂等恢复，并通过离线故障注入与反例测试验证评测链路。**
不能写“真实模型准确率 100%”“上线企业服务台”“人审数据集”或“提升模型效果”。

运行命令、模型预算边界、演示顺序见 [复现指南](../../examples/it-support/README.md)。

### 本地验收记录（2026-09-17，Windows / Python 3.12）

- 全量离线测试（含可选 OpenJudge）：103 passed；随后新增的两项正确路径测试单独执行 2 passed，当前共 105 项。
- 恢复脚本：30/30 规则通过，整体 30 项 pending_semantic，4/4 配置故障实际触发。
- 跳过探针反例：8/30 规则通过，22 项被拦截；4 个故障仅触发 3 个，未调用探针的超时不算已恢复故障。
- Inspect 原生领域任务：30/30 规则通过，日志保存在 `data/it-support/inspect-logs/`。
- v0.2 Inspect 回归烟测、ruff、网页 JS 语法检查、wheel/sdist 构建通过；wheel 含 fixtures 和页面。
- 8768 页面/API 已实际打开检查，并切换到“DNS 刷新无效应转交”确认显示未恢复状态。

以上没有调用收费模型或 Judge。依赖有弃用警告；Windows Inspect 可选控制面 AF_UNIX 警告不影响任务日志。
