# 当前架构

Auto-alpha 只服务于 A 股量化因子研发平台，正式代码统一位于 `src/auto_alpha/`。

## 六个领域

- `data/`：摄取、数据湖、PIT、股票池、矩阵和质量。
- `research/`：特征、公式、因子、搜索和 neural-guided research。
- `validation/`：Research Firewall、walk-forward 和认证。
- `portfolio/`：因子组合、风险、容量和事件账本模拟。
- `execution/`：券商、交易、结算、对账和运行操作。
- `platform/`：artifact、调度、治理、监控、发布和网络权威。

共 25 个可见子系统、56 个 Python package 目录、664 个 Python 源文件。`research`、`validation`、`portfolio`、`execution` 在子系统下不再继续套包，runner/store/report 通过同级能力前缀区分。结构审计将 package、源码和提交证据上限分别锁定为 65、665、4。测试目录按相同六领域镜像。任务编号不再是包名；Task 055 只保留最终、扁平的 `platform/network_authority` 和当前只读锚点，旧代源码、`_internal` 历史树、公开入口和测试均已删除。

数据内部也已收敛：`ingestion` 只保留 `pipeline/repair`，原始 landing 与 index 合并为 `lake/catalog`，backfill observer 归入 `platform/observability/monitoring`。停牌真值属于 `data/pit`，费用、估值和事件账本属于 `portfolio/simulation`，不再堆进网络权威模块。

删除优先清理了不可达的 broker connectivity/mirror/UAT、go-live/incident/production replay/shadow-lab，以及重复的 research suite/benchmark/orchestrator/factor lifecycle；对应 CLI、CI 和测试也同步删除，不保留兼容入口。

第二轮删除继续收敛唯一实现：公式批量评价只保留 `FormulaBatchEvaluator`，候选统一为 `FormulaEvalRequest`，组合归入 `research/factors`；PIT 真值只保留 `data.pit.truth`；费用只保留 `portfolio.simulation.fees`；不可变存储统一为 `platform.artifacts.storage`；Task054-A/C 旧生产器被删除，只保留当前生产 Sentinel 和必要的只读历史验证器。

## 唯一命令入口

```bash
auto-alpha list
auto-alpha data freeze --help
auto-alpha research alpha --help
auto-alpha validation run --help
auto-alpha portfolio research --help
auto-alpha platform ci --help
```

正式文档和自动化不得再新增 `python -m <微型包>.run_xxx`。完整边界见 `docs/ARCHITECTURE.md`。
