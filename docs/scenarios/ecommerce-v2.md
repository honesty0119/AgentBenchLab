# 电商售后第二轮：可复现的实验比较

起点：PR #3 / a915ab4。保持30条draft任务和现有业务范围，不新增生产退款、治理或公共平台重构。

## 已确认的问题与取舍

1. 故障未触发被硬判业务失败，误伤不同正确路径。改为三个独立输出：业务规则、故障覆盖、恢复资格。
   完整触发且完成执行的配置故障任务进入严格恢复分母；部分触发单列，无故障诊断不进入分母。
   异常终止且已完整触发也计为恢复失败；未执行任务不进入分母。报告同时给出配置/触发/完整触发数，避免挑分母。
2. `serve` 当前重跑脚本并覆盖默认报告。改为只加载显式指定的已存运行；运行有唯一ID、固定清单和只写一次的原始报告。
   Judge与重评分保存独立追加记录。旧v1报告可只读查看，但缺少完整清单时拒绝参与严格比较。
3. 比较在独立电商模块中适配基底的配对/家族Bootstrap方法，要求相同任务、重复、数据、执行契约和评分指纹。
   明示模型/策略配置差异；规则与整体指标分开，整体必须指定同一Judge配置组；缺失评分、不完整执行拒绝比较。
   诊断运行必须显式diagnostic比较且不可当门禁。门禁拒绝任一通过→失败以及超过阈值的平均下降。
4. Judge只有Python入口，当前引用可只引用Agent答案。增加显式请求预算CLI、源政策/状态证据与Agent声明分组，
   通过判定至少引用一处独立来源；提示与评分实现一起形成指纹，模型/服务地址/参数参与配置组。
   引用匹配只验证来源，不宣称语义正确。每条错误/超时/无效输出独立保存；未用预算和未执行记录明确显示。
5. Runtime终止原因折叠。保留实际事件与错误原因，区分服务、协议、Harness限制和业务规则；
   总模型预算耗尽后的任务标为not_executed，而不是Agent失败。记录耗时、工具次数、传输请求次数与已知用量/费用。

## 验收

- 正确业务路径未触发故障仍规则通过，但不宣称恢复；部分/全触发、无故障诊断及触发后失败均有分母测试。
- 补查中间状态、非目标状态、数量/金额金标：有证据的评分漏洞修复，不以参考工具路径替代终态契约。
- 查看已有模型报告不调用模型、不重跑脚本、不覆盖报告；规则/整体比较、家族聚类、门禁和不可比条件有离线测试。
- Judge错误、源证据不足、旧指纹、多组冲突、硬失败以及离线重评分均有反例；原运行内容不改变。
- 模拟HTTP证明预算截断/未执行覆盖率/未知成本处理；仅配置单变量实验模板，不生成真实效果数据。
- 实际浏览器检查8766；相关测试、原平台回归、打包和Windows/Ubuntu/OpenJudge CI通过；更新现有PR，暂不合并。

完整人工校准UI、跨领域统一数据库、生产集成和真实模型实验仍是后续工作。

## 使用当前版本

### 离线运行、只读查看与门禁

```powershell
uv run python -m agentbench.domains.ecommerce demo --variant baseline
uv run python -m agentbench.domains.ecommerce demo --variant recovery
# 分别记下两个命令返回的 BASE_ID 与 CANDIDATE_ID
uv run python -m agentbench.domains.ecommerce serve --run CANDIDATE_ID --port 8766
uv run python -m agentbench.domains.ecommerce compare BASE_ID CANDIDATE_ID --gate
```

每次运行返回 `ec-<UUID>` 和文件路径，原始报告位于 `data/ecommerce/runs/RUN_ID/run.json`。
`--root` 必须放在子命令前，可指定另一个电商结果目录。显式 `--output` 只导出到新文件，若文件存在，
在运行Agent前拒绝，绝不覆盖。`serve --run` 必须指定已存运行ID或文件，启动只读服务器，不执行任务。
页面支持切换运行、Judge组与重评分批次，展开逐轮工具证据和配对比较；页面无写入或收费入口。
旧版 `data/ecommerce/report.json` 可以 `serve --run data/ecommerce/report.json` 只读查看，但不参与严格比较。

清单固定任务快照哈希、任务/重复集合、数据集、提示、执行契约、源码/依赖、规则评分版本/指纹及配置。
每份报告和历史文件都有内容哈希以检测意外修改；这是本地审计措施，不是对拥有文件写权限者的安全边界。
原始结果与分数不改写；派生视图保留 `source_run_hash`，不冒充原始文件。

### 单变量真实模型配置（未执行）

先由用户配置模型、服务地址、API key并确认预算。下列模板均选择3个任务家族、每题3次重复，
最多120次请求、每次1500输出Token、无HTTP自动重试，只有标明的一个策略字段不同：

| 模板 | 与 strategy-baseline 的区别 | 比较方式 |
| --- | --- | --- |
| strategy-baseline.json | 无 | 普通对照 |
| strategy-safe-retry.json | tool_retry_policy=safe | 普通对照/规则门禁 |
| strategy-full-context.json | context_policy=full | 普通对照，仍受request_chars限制 |
| diagnose-no-faults.json | intervention=no_faults | 必须 `--diagnostic`，禁止门禁 |

```powershell
uv run python -m agentbench.domains.ecommerce run --config examples/ecommerce/strategy-baseline.json
uv run python -m agentbench.domains.ecommerce run --config examples/ecommerce/strategy-safe-retry.json
uv run python -m agentbench.domains.ecommerce compare BASE_ID SAFE_ID --gate
uv run python -m agentbench.domains.ecommerce compare BASE_ID NO_FAULTS_ID --diagnostic
```

请求次数预算是上限，不是币种金额预算。预算在任务/重复之间共享；开始前已耗尽的试验记为
`not_executed/global_model_budget_exhausted`、规则 `unscored`；中途耗尽记录真实终止及Harness限制。
二者都不能被当作完整可比实验。报告同时给计划数、执行数、执行覆盖率、完成数、实际传输次数、
任务/模型/工具耗时、工具次数、失败类别、已知用量/费用和未知用量请求数。用量缺失时总费用为null。
模型名/API配置和费用预算本次没有设置到真实服务；测试用模拟HTTP，不提供模型提升结论。

### 故障覆盖与恢复率

硬规则只回答“实际业务状态及回复事实是否正确”。`fault_coverage` 为 none_configured / untriggered /
partial / full / disabled / not_executed。只有**已执行且所有配置故障均触发**的任务进入严格恢复分母：

`recovery_rate = 完整触发且硬规则通过的试验数 / 完整触发的已执行试验数`。

完整触发后终止失败也在分母且记失败；未触发的正确路径仍规则通过，但恢复成功为null。
多个故障只部分触发单列，不进入严格恢复分母；no_faults诊断与未执行任务不进入。分母为0则恢复率null。
同时报告配置故障任务数、任意触发数、部分触发数，不能只展示恢复率而隐藏覆盖情况。

### 保存后的Judge与离线重评分

Judge使用独立的 `AGENTBENCH_JUDGE_API_KEY` / `AGENTBENCH_JUDGE_BASE_URL`。替换
`examples/ecommerce/judge.json` 的模型，并明确最多20次请求预算后才执行：

```powershell
uv run python -m agentbench.domains.ecommerce judge RUN_ID --config examples/ecommerce/judge.json
uv run python -m agentbench.domains.ecommerce report RUN_ID --cohort COHORT_ID
uv run python -m agentbench.domains.ecommerce compare BASE_ID CANDIDATE_ID --metric overall --cohort COHORT_ID --gate
```

每题评分立即追加到 `RUN_ID/judgements/UUID.json`，包括原运行/任务/结果哈希、模型与服务地址、
参数、提示快照、评分实现指纹、配置组、输出与引用、已知用量/费用、超时/网络/协议错误。
预算不足和未完成试验的跳过原因也保存。错误不会丢掉整份原运行。重新评分产生新记录，
同组使用最新实际评分尝试；预算跳过不抹除之前的评分，新的错误尝试会使该组待评分。

Judge的source_evidence使用指向独立政策/状态的JSON pointer和原文片段；仅引用Agent答案不能通过。
字符串来源校验并不能证明Judge正确理解或推理，仍需独立人审和校准。
多组同时存在时必须选择一个组；整体比较要求两份运行所有试验都有同组的有效pass/fail记录，
连硬规则失败的任务也不借此跳过缺失语义覆盖检查。硬规则失败始终使整体失败。

```powershell
# 不调用任何模型，基于保存的快照和输出追加当前规则评分
uv run python -m agentbench.domains.ecommerce regrade BASE_ID
uv run python -m agentbench.domains.ecommerce regrade CANDIDATE_ID
uv run python -m agentbench.domains.ecommerce compare BASE_ID CANDIDATE_ID --base-regrade BASE_BATCH --candidate-regrade CANDIDATE_BATCH --gate
```

默认比较原评分，使用重评分时两边必须显式指定批次；批次绑定原任务和结果，评分指纹必须一致。
同任务/重复配对、家族Bootstrap直接适配基底 `agentbench.analysis.compare`，先平均题内重复，
再按家族聚类重采样（2000次，seed=42）。门禁拒绝任一通过→失败和超过max-drop的平均下降。
数据、执行契约、提示、演示/真实模式、评分身份不同，缺失试验或异常终止，均明确不可比。
显示配置差异，但“可比”不自动等于因果识别；多字段变化必须自行控制，公开草稿不能称为未见测试集。

## 剩余边界

仍是单机电商独立结果目录，尚未实现断点恢复、人工校准UI或跨领域统一数据导入。
历史评分文件追加保存，不改原结果；不会自动替换旧Judge记录或批量把旧版v1报告升级为可比实验。
业务申请状态依旧是沙箱模拟，不执行真实退款。任务数量没有增加，所有30题仍为draft。
