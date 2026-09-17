# 采购比价与采购方案评测（draft v1）

## 研究结论与复用边界

2026-09-17 在 GitHub 搜索并通过 `gh api` 检查以下官方仓库的实际源码。以下链接固定到检索时提交；没有导入第三方 ERP 源码、业务数据或其测试用例。

| 参考 | 实际核对内容、相关性 | 许可与取舍 |
| --- | --- | --- |
| [ERPNext 供应商报价比较](https://github.com/frappe/erpnext/blob/60913b722a3beafe6035e8ad426dcb28e601704e/erpnext/buying/report/supplier_quotation_comparison/supplier_quotation_comparison.py) | 查询 Supplier Quotation / Item，区分报价数量与库存单位数量，比较单位价格、有效期及交期；是真实采购侧比较，不是销售报价 | GPL-3.0；仅借鉴数据概念，自行实现轻量工具，不整套移植 ERP 或复制代码 |
| [ERPNext 报价测试](https://github.com/frappe/erpnext/blob/60913b722a3beafe6035e8ad426dcb28e601704e/erpnext/buying/doctype/supplier_quotation/test_supplier_quotation.py) | 有效期、数量与换算、订单数量上限、RFQ 状态测试；它们是业务单元测试，不是 Agent 评分器 | 同上；借鉴可校验终态的思路，不复用测试数据；不实现订单提交 |
| [Odoo purchase_order_line](https://github.com/odoo/odoo/blob/3a6d2bd9d00034346f711ff2d1fac6f58bd8ee08/addons/purchase/models/purchase_order_line.py) | 采购行的单位、供应商价格、折扣、税额和到货日期，说明只比裸单价不足以比较总成本 | purchase [manifest](https://github.com/odoo/odoo/blob/3a6d2bd9d00034346f711ff2d1fac6f58bd8ee08/addons/purchase/__manifest__.py) 明确 LGPL-3；仓库 API 的 NOASSERTION 不能代替模块许可。只参考字段语义，不复用实现 |
| [Google OR-Tools assignment_sat](https://github.com/google/or-tools/blob/98c165af62df62b3056c2ee0fca66b24e79097cb/ortools/sat/samples/assignment_sat.py) | 整数决策、分配约束、成本目标及 OPTIMAL / FEASIBLE / INFEASIBLE 区分；不是采购系统，但与独立约束验证直接相关 | Apache-2.0；第一版状态空间小，使用自行编写 Fraction 穷举，不增加求解器依赖。扩大规模时可增加 CP-SAT 并保留解状态 |

现有 AgentBench Runtime、期限取消、工具事件、上下文快照、模型客户端、Inspect AI 可直接复用。采购状态、工具、独立枚举评分、数据和演示单独实现。最小公共适配仅为 `execute_case` 增加可选依赖注入与逐轮回调；默认文档任务行为不变。独立场景入口避免修改三个场景共享的 CLI、Schema、前端和 Store。

## 业务范围与假设

用户是采购专员或评测工程师；业务 Agent 是被测对象。给定采购需求及三家合成报价，读取规格、核算到岸成本、判断预算和交期，保存采购清单草稿，并在用户修改数量/预算/期限或收到新报价后重新判断。信息不足时列出缺失字段；无可行方案时明确说明。

第一版人民币、自然日、单次采购、确定库存、不拆分同一需求行给多个供应商（可跨需求行选择不同供应商）、不跨行共享库存。每条需求为一个独立 SKU；报价为该 SKU 的独立库存池。每家供应商收一次含税运费。无物流税额再计算，无汇率、折扣券或供应链优化。无审批、RBAC、多租户、外部 ERP 集成、付款和下单。

目标显式为 `min_cost`（含税货款+运费最小）或 `feasible`（任意满足约束的计划）。交期为硬约束。多目标偏好第一版不支持，必须先澄清，不擅自加权。只有穷举了声明空间才可声明最优；同成本多个解全部接受。

## 数据和状态模型

需求快照：revision、as_of、deadline、budget_cents、objective、items（id、quantity、unit、spec、max_overbuy）。报价快照：id/version、supplier、item、spec、unit、pack_size、price_cents/整包、tax_included、tax_bps、tiers（起始包数、整包价格）、min_packs、stock_packs、lead_days、valid_until、currency。供应商运费单列。未知字段用 null，不能以 0 代替。

需求单位与报价基础单位须相同，例如需求 2000 g、报价每包 500 g；不从自由文本猜测换算。包装、MOQ、库存和阶梯阈值均以整数包计。每行在税前货款上使用全量阶梯价（不是累进价）；未含税价格乘 `(10000+tax_bps)/10000` 后每行四舍五入到分；含税价格不重复计税。总额为各行含税额之和加使用供应商的运费一次。

每轮 harness 提供新的权威完整快照；模型仅见当前和历史轮材料，不见未来快照与金标。旧草稿在新需求/报价快照到达时失效，新的草稿必须声明当前 revision 并引用指定报价版本。报价文本是数据，不能覆盖系统契约。

## 领域工具

`procurement_read` 读取当前权威快照及草稿；`procurement_cost` 为指定报价和包数核算明细（不证明全局最优）；`procurement_save_draft` 用 revision 和幂等键保存结构化草稿。工具只写内存沙箱的固定路径，没有订单/付款工具，也不接受主机路径。

保存工具验证参数结构和 revision，但不阻止保存业务上错误的计划：由独立评分器发现问题，避免工具替 Agent 完成所有推理。超时和丢响应使用现有故障注入；幂等保存重放须返回同一结果，冲突键拒绝。只有实际触发的故障才计入恢复分母。

## 评分契约与失败分类

每轮核对已保存草稿和 JSON 回答中的同一计划。草稿状态为 feasible / infeasible / needs_info；包含需求 revision、报价版本、整数包数、total_cents、缺失字段和解释。逐轮检查，不允许最终修复掩盖前一轮错误。

独立评分使用 Fraction 与穷举整数包数，禁止调用被测成本工具生成期望值。对每个需求行枚举满足规格、单位、MOQ、库存、有效期、期限及允许超采的报价包数；合并计算供应商运费与预算。对完整数据可证明无解或最优值。数据缺失时采用保守契约：声明快照中必要决策字段缺失则 needs_info，不声称最优/无解（即使部分报价暂时可行）。明确已不兼容的报价也保留缺失提醒，属于本基准约定，不声称通用采购规则。

硬失败类别：output_format、missing_draft、stale_revision、quote_version、spec_unit、pack_moq_stock、deadline_expiry、coverage、cost、budget、objective、unsupported_status、missing_information、draft_answer_mismatch、termination。自由文本准确性和理由完整性由 Judge 补充，默认所有样本 semantic_required；离线规则通过显示 pending_semantic，不把脚本当模型实验。Judge 不能翻转硬约束失败。

## 样本、家族与拆分

30 条自行构造任务，10 个互斥家族，每家族 3 条不同业务决策；全部 `authored-synthetic` / `draft`，没有独立人审。公开的 test/challenge 是开发保留标签，公开后不能声称模型未见过。

| 家族 | split | 三类决策 |
| --- | --- | --- |
| landed_cost | dev | 裸价与运费逆转、未含税逆转、行级四舍五入 |
| pack_units | dev | 整包超采、禁止超采、克/包换算 |
| tier_moq | dev | 阶梯门槛、MOQ 排除、获准超采触发更便宜阶梯 |
| availability | test | 库存不足、自然日边界、全部过期 |
| specification | test | 规格不匹配、错误单位、报价内嵌指令 |
| basket | test | 跨行运费合并、分供应商、并列最优 |
| incomplete | challenge | 运费未知、税信息未知、包装未知 |
| changes | challenge | 数量增加、预算下降、交期收紧 |
| versions | challenge | 新版涨价、旧版失效、规格变更 |
| recovery | challenge | 读取瞬态超时、保存丢响应、真实期限取消 |

## 对照实验和验收

离线验收：已知答案脚本贯穿真实 Runtime 和 Inspect；故意错误脚本/结果突变验证评分能拒绝裸价、旧版、缺货、漏运费、无证据无解、丢失中途状态等。脚本成功率只表示执行/评分链条验收。手算案例和不同算术实现交叉验证金额，避免循环金标。

未来模型实验：同任务/家族、相同模型版本/温度/步骤/预算和重复次数，仅切换重试或上下文策略；报告规则、语义、整体及实际故障触发率、成功恢复率、工具成本。真实模型需用户配置 API 和明确预算，当前不运行。Judge 需独立人工标签校准；不虚构人审、模型成绩和提升。

最小交付：30 条 JSON 草稿任务、独立可重算 oracle、领域工具、Runtime/Inspect 入口、可在 8767 查看逐轮计划与成本的本地演示、回归与反例测试。独立 `.venv`、`data/procurement`；不占 8765。验收要求旧测试保留通过；30 个恢复脚本硬约束通过、语义保持待评；错误反例被拒绝；GitHub 仅推 `codex/procurement-eval`，不合并 main。

## 实现与运行

在本场景工作树执行（独立环境和数据，不读取共享 Lab 数据库）：

```powershell
uv sync --locked --extra openjudge
uv run python -m agentbench.domains.procurement demo
uv run python -m agentbench.domains.procurement serve --port 8767
```

打开 http://127.0.0.1:8767 。页面是已保存执行证据的只读回放，可以切换任务与脚本，查看逐轮快照、报价、计划、成本明细、独立评分和工具轨迹。它不是聊天产品或采购系统。报告默认 `data/procurement/demo.json`；再次运行需用 `--output` 指定新文件，避免覆盖旧证据。

```powershell
uv run python -m pytest -q
uv run ruff check .
uv run python examples/procurement/inspect_smoke.py
uv run python scripts/ci_smoke.py
uv build
```

`fixtures/cases.json` 为随包发布的数据；`examples/procurement/build_fixtures.py` 是人工编写的场景构造源，不包含 oracle 或生成期望金额。`tests/test_procurement.py` 包含可直接手算的金额注释。没有复制第三方源文件或引入额外依赖。

真实模型入口保留，但本交付没有调用付费服务。使用 `examples/procurement/real-model.json` 时，先填写明确模型与用户确认的输入/输出价格，设置 API 环境变量，再给定预算：

```powershell
uv run python -m agentbench.domains.procurement run --config examples/procurement/real-model.json --case-id proc-quantity-change --budget-usd <用户明确授权金额> --output data/procurement/model-001.json
```

该入口串行执行、固定配置、最多指定步骤/HTTP 重试，启动前保守预留 `轮数×步骤×HTTP尝试×(4×请求字符上限×输入单价+输出Token上限×输出单价)` 的费用包络；超过预算则拒绝运行。计费 Token 超过每字符4个或用户提供的价格不准时，此包络不是账单保证。用量未知或已知累计费用超过预算时停止后续任务并保留已有证据。没有自动读取 `.env`；需显式环境变量。不得把并发、重复、默认文档数据选择参数误认为采购配置，重复实验应使用独立输出文件。

语义契约见 `semantic.packet` / `semantic.combine`：提供逐轮材料、rubric、输出 schema、内容哈希；接入预算受控的 Judge 后，可用匹配哈希/配置组和真实来源引用的记录合并。没有自动调用 Judge 的 CLI；默认保留 pending。已用模拟 Judge 验证语义 pass 不能覆盖规则 fail，也不能复用旧结果记录。这些测试不构成人工校准。

## 验收证据与限制

本地已验证 30 条恢复脚本通过全部硬约束，30 条故意裸价脚本仅 6 条通过，另外 24 条被拒绝。通过的 6 条包括无解/缺失信息等本来没有被裸价突变破坏的样本，不代表覆盖了所有错误类型。三个故障样本全部真实触发，其中 deadline 记录实际取消；响应丢失后只有一次草稿状态变化。所有规则通过的整体结论仍为 pending_semantic。

示例 `proc-quantity-change`：第一轮 10 令，A 的 ¥200 货款+¥5 运费=¥205；第二轮改为20令，A仅15包库存，B满足20包阶梯价，20×¥18=¥360，预算¥500且交期满足。评分独立枚举得到相同值。`proc-missing-freight` 明确输出 needs_info；`proc-all-expired` 输出 infeasible。浏览器已检查数量变更与缺运费的展示。

68 项新增领域测试覆盖：30条真实Runtime脚本、11个手算案例、80组独立算术交叉核对、14类业务突变、并列解与任意可行目标、逐轮错误、幂等重放/版本失效、期限取消、枚举上限、预算预检、模拟HTTP模型工具调用、Judge不可覆盖硬失败。原49项测试（含离线OpenJudge集成）保留；采购Inspect原生30题运行成功；原有Inspect烟测/门禁、ruff和构建通过。CI另加独立采购Windows/Ubuntu任务。

合并注意：公共改动仅 `agentbench/agents.py` 的 `execute_case` 新增 keyword-only `environment/client/system/before_turn`；默认仍执行原文档任务。`system` 指纹随注入提示变化。领域文件独立，不修改共享 CLI、Case schema、Store、文档前端或其他场景目录。网页是独立8767端口的场景回放，不宣称已整合至8765工作台的所有历史重评分/校准/统计功能。

所有金额是合成采购约定，不是税务/会计系统。首次仅支持同SKU整行单供应商，不处理供应商容量耦合、共享库存、汇率、退款、订单或生产交易。枚举超过200000组合会显式报错，不伪称无解/最优。test/challenge公开、数据未独立人审，尚无真实模型泛化成绩、Judge校准结果或业务上线效果。

## 可核验的简历表述

“在 AgentBench Lab 中实现采购方案评测场景，构建30条按10个任务家族拆分的合成草稿样本，覆盖包装换算、税运费、MOQ/库存/交期及多轮报价变更；以独立有理数枚举校验业务终态，接入Agent Runtime与Inspect，验证幂等恢复、逐轮断言和错误反例。”

补充边界：“目前完成离线工具/评分链路验收，数据未独立人审，尚未开展付费真实模型对照实验。”不使用“模型准确率提升80%”“已部署采购系统”“真实业务节省成本”等没有证据的表述。
