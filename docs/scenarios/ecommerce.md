# 电商售后评测沙箱（方案与验收）

## 定位和业务假设

业务 Agent 是被测对象；本模块是本地评测环境，不执行真实退款，不连接商家系统。
顾客在当前会话身份已确认；不开发登录、RBAC、审批、多租户。仅处理订单检索、政策检索、
报价、部分退货申请及申请查询，不处理换货、物流、真实支付或凭空承诺到账时间。
用户明确说“确认申请”即构成该笔操作授权；咨询、缺少商品/数量和中途撤回均不能创建。
政策是自拟沙箱规则，不是任何公司的真实条款或法律解释。

## GitHub 核对（2026-09-17）

1. [sierra-research/tau2-bench](https://github.com/sierra-research/tau2-bench/tree/2174a603f6d014ef94473ffa95957f6ce27100db)，
   固定提交 `2174a603f6d014ef94473ffa95957f6ce27100db`，仓库现称 τ³。
   阅读了 [evaluation.md](https://github.com/sierra-research/tau2-bench/blob/2174a603f6d014ef94473ffa95957f6ce27100db/docs/evaluation.md)、
   [EnvironmentEvaluator](https://github.com/sierra-research/tau2-bench/blob/2174a603f6d014ef94473ffa95957f6ce27100db/src/tau2/evaluator/evaluator_env.py)、
   [retail tools](https://github.com/sierra-research/tau2-bench/blob/2174a603f6d014ef94473ffa95957f6ce27100db/src/tau2/domains/retail/tools.py)、
   [policy](https://github.com/sierra-research/tau2-bench/blob/2174a603f6d014ef94473ffa95957f6ce27100db/data/tau2/domains/retail/policy.md) 和实际 tasks.json。
   `return_delivered_order_items` 校验已送达、商品多重集和退款方式，写入 return requested，
   不计算本场景的优惠分摊，也没有幂等键。环境评分重放参考动作生成 gold DB，再比较状态哈希；
   并非强制相同工具路径。文档默认 DB+COMMUNICATE，但当前零售任务可使用 DB+NL_ASSERTION，
   因此必须看每题 reward_basis，不能照抄 README 的默认解释。
2. [olly-styles/WorkBench](https://github.com/olly-styles/WorkBench/tree/49c7dfd00c03d384ec59ea57374f50b766aa5613)，
   固定提交 `49c7dfd00c03d384ec59ea57374f50b766aa5613`。
   阅读了 [evaluation.py](https://github.com/olly-styles/WorkBench/blob/49c7dfd00c03d384ec59ea57374f50b766aa5613/src/evals/evaluation.py)、
   [actions.py](https://github.com/olly-styles/WorkBench/blob/49c7dfd00c03d384ec59ea57374f50b766aa5613/src/evals/actions.py)、
   CRM tools 和 tasks_and_outcomes/customer_relationship_manager_tasks_and_outcomes.csv。
   其 CRM 是客户检索/更新，不是电商退款现成实现。`is_correct` 比较执行后的所有状态表，
   `_states_match` 忽略行序但保留数量；`has_side_effects` 另查非预期变更。
   借鉴状态与副作用分开诊断，而不是把 CRM 工具改名当作售后工具。

两个固定版本的 LICENSE 均为 MIT（分别 Sierra Research 2025、Mindsdb 2024）。本模块不复制
上游代码或数据，仅引用设计；若未来复制实质内容，须随分发保留其版权与许可。任务全部自行构造。
明确不复用上游参考动作重放来生成金额金标，以免被测工具与评分器共享错误。

## 用户、典型任务与最小交付

使用者是评测工程师和售后 Agent 开发者。首批 30 条草稿：正常部分退货、数量与优惠、
政策日期边界、商品限制、政策版本/冲突、信息不足、跨轮变更、已有申请、故障恢复、隔离/状态沟通。
每一任务有业务目标、逐轮期望、初态、参考说明和独立演示轨迹；后者不是被测模型输入。
完整演示：订单键盘 300 元、鼠标 100 元、订单券 40 元，券分摊为 30/10 元；只退键盘应申请
270 元。首次创建成功后丢失响应，通过同键重试或查询恢复，只允许一个申请，鼠标与其他订单不变。

## 数据、状态与工具

- 状态：固定评测日期、订单及明细、政策版本、申请及其数量/单位位置、幂等映射。金额全部用整数分。
- 优惠：先扣逐件商品优惠，再按剩余商品金额比例分摊订单券；余分用最大余数法，按明细 ID 和单位序号打破平局。
  运费不退；申请金额是所选尚未占用单位的净额之和，已申请数量不能再次申请。
- 政策：以购买日期、渠道与类别匹配唯一政策；到货日差在含端点窗口内；缺日期或匹配冲突需要澄清。
  普通不喜欢 14 天、质量原因 30 天；卫生类拆封及数字商品不接受普通退货。
- 工具：`search_policies`、`get_policy`、`list_orders`、`get_order`、`quote_return`、
  `create_return`、`list_returns`。创建需非空幂等键及核对金额；同键不同请求拒绝。
- 复用现有 AgentRuntime、ToolRegistry、EvaluationContext、ModelClient 和故障注入；新增领域适配器、
  独立样本加载、状态评分、离线脚本、CLI/只读演示页及 Inspect 入口。尽量零修改公共接口。
  状态和日志独立存入本 worktree 的 `data/ecommerce/`；虚拟环境 `.venv`；端口 8766。

## 评分契约与失败分类

逐轮比较申请的业务投影（订单、商品、数量、金额、政策、原因、处理状态），忽略新 ID 和查询顺序。
订单和政策始终不可修改；检查每次工具后的状态，不允许“改错再改回”掩盖违规。
检查同键重复、数量占用、未授权轮次写入、错误商品、非目标订单副作用及金额守恒。
回复使用结构化事实字段：status、amount_cents、policy_ids、request_ids、clarify；只允许声明真实状态。
申请成功不能当作退款到账。自然语言解释和是否充分澄清交给补充 Judge；无有效 Judge 时整体 pending_semantic，
硬规则失败不能被 Judge 翻转。故障必须实际触发，才进入恢复样本分母。
分类：policy、arithmetic、selection、unauthorized_write、collateral_state、duplicate、
false_status、clarification、execution、fault_not_triggered、semantic。

## 家族拆分与实验

同一家族只能在一个 dev/test/challenge 拆分，变体继承家族；均为 authored-synthetic、draft。
公开测试集只证明工程覆盖，不宣称未见泛化。金额金标为作者明确常量，另用 Fraction 参考算法和
手算实例核对，不调用 quote_return/create_return 生成金标。
离线对照检验评分器：正确脚本 vs 错金额、错商品、重复申请、未创建声称成功等反例。
真实实验待预算和配置：相同模型/任务/预算，比较无自动重试与安全重试、全文政策与检索政策、
完整历史与受限历史；按任务家族报告结果，故障恢复同时报告触发率和工具成本。
第一版小型结构化政策库用确定性检索足够，不引入 LightRAG。只有政策规模和跨文档问题扩大，且
检索召回诊断显示瓶颈，才用同样例/预算做独立检索对照；不能因增加技术名词宣称性能提升。

## 验收标准

30 条样本经严格加载与家族检查；正确离线脚本硬规则通过、整体待语义；关键反例必须失败；
验证金额分摊、日期端点、多轮更改、幂等冲突、响应丢失、无关状态不变及错误回复。
原测试回归、静态检查、打包及本地 8766 演示通过后，只推送 codex/ecommerce-eval 并开可审阅 PR。
不合并主分支；不声称已人审、真实模型评测、生产集成或效果提升。

## 实现后的使用入口

在独立工作树运行（不会占用 8765）：

```powershell
uv sync --locked
uv run python -m agentbench.domains.ecommerce demo
uv run python -m agentbench.domains.ecommerce demo --variant baseline --output data/ecommerce/baseline.json
uv run python -m agentbench.domains.ecommerce serve --port 8766
uv run python -m inspect_ai eval agentbench/domains/ecommerce/inspect_task.py@ecommerce --model mockllm/model --limit 3 --log-dir data/ecommerce/inspect_logs
uv run pytest tests/ecommerce -q
```

打开 http://127.0.0.1:8766 。默认选择响应丢失完整案例；可选30题、看逐轮业务状态、展开每个工具事件。
只读页面不发送模型请求，也不开放创建/退款 HTTP 接口。`demo`、`serve`、当前 Inspect 入口均离线。
报告保存到 `data/ecommerce/report.json`，包含任务快照、数据哈希、源码/依赖/脚本指纹、实际上下文、
工具前后状态、故障触发、逐轮答案及分层结论。SQLite 会话使用每次试验独立临时库，JSON 报告长期保存。
现有文档工作台的数据导入、评分、对比与 UI 未扩展为跨领域通用平台，不能把领域任务导入旧 Case Schema。

| 家族 | 拆分 | 三项不同覆盖 |
| --- | --- | --- |
| partial | dev | 键鼠只退键盘、两件只退一件、两种商品均退 |
| money | dev | 余分归属、商品优惠优先、运费排除 |
| window | test | 普通第14天、超期一天、质量第30天 |
| product | test | 卫生拆封、卫生未拆、数字商品 |
| policy | test | 购买时旧版、冲突版本、渠道缺失 |
| clarification | dev | 订单歧义、数量缺失、到货日缺失 |
| changes | challenge | 改商品、撤回、减少数量 |
| existing | test | 查已有申请、真实已收货状态、数量已占用 |
| recovery | challenge | 提交后丢响应、读取瞬态错误、提交前真实期限取消 |
| integrity | challenge | 不存在目标订单、数据含伪指令、不得虚报到账 |

### 可选模型与 Judge

`examples/ecommerce/real-model.json` 提供配置模板；本次没有执行它。用户需替换 model、在环境设置
`AGENTBENCH_API_KEY` 和 `AGENTBENCH_BASE_URL`，确认预算后运行：

```powershell
uv run python -m agentbench.domains.ecommerce run --config examples/ecommerce/real-model.json --output data/ecommerce/model.json
```

该模板最多20次模型请求，每次最多1500输出Token，`http_retries=0`；总请求预算跨任务/重复共享。
这是请求次数预算，不是美元金额预算，实际费用取决于模型和输入长度。没有价格或有缺失用量时总费用未知，
已知用量/费用另列，不把超时调用算成零成本。模型请求只含系统提示、用户轮次和工具信息，不含金标或演示轨迹。
支持 safe/none 工具重试、full/受限上下文、关闭故障的诊断；不支持原文档任务的 reference_evidence 干预。
当前真实模型执行串行，RunConfig 中 concurrency 不用于领域 runner。

`semantic.assess(case, result, client, identity)` 是补充 Judge 的 Python 接口：调用方显式提供客户端、
重试/费用预算和模型身份；只发一次 completion 请求，验证维度、引用片段、任务/结果/评分提示指纹。
用 `grading.grade(case, result, semantic=record)` 合并结论。客户端本身重试必须计入外部预算。
本次只用模拟 Judge 验证协议，没有真实 Judge 分数、独立人审或阈值校准；原平台 Judge 校准表未接入领域报告。

### 合并边界和诚实表述

新增代码全部收敛于 `agentbench/domains/ecommerce/`；配套目录是 `scripts/ecommerce/`、
`tests/ecommerce/`、`examples/ecommerce/`、`docs/scenarios/ecommerce*.md`。公共接口零修改、依赖零新增。
未来合并三个场景可抽取领域协议；本次不抢先重构公共 runner 或改写另外两个场景。

简历可写：**在 AgentBench Lab 中实现电商售后评测沙箱，构造30条、10个家族的合成草稿任务；
以业务终态、逐轮不变量和独立金额校验评测部分退货，验证响应丢失下的幂等恢复，并接入既有运行时与 Inspect 日志。**
不能写模型成功率提升、已完成专家标注、真实退款生产落地或已验证 LightRAG 优势。
