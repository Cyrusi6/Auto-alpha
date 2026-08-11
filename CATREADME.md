# 当前架构

Auto-alpha 只服务于 A 股量化因子研发平台，正式代码统一位于 `src/auto_alpha/`。

## 六个领域

- `data/`：摄取、数据湖、PIT、股票池、矩阵和质量。
- `research/`：特征、公式、因子、搜索和 neural-guided research。
- `validation/`：Research Firewall、walk-forward 和认证。
- `portfolio/`：因子组合、风险、容量和事件账本模拟。
- `execution/`：券商、交易、结算、对账和运行操作。
- `platform/`：artifact、调度、治理、监控、发布和网络权威。

共 23 个可见子系统、54 个 Python package 目录、436 个 Python 源文件。`research`、`validation`、`portfolio`、`execution` 在子系统下不再继续套包，runner/store/report 历史链已合并为职责明确的能力文件。结构审计将 package、源码和提交证据上限分别锁定为 55、450、4。测试目录按相同六领域镜像。Task 055 的唯一正式边界位于 `platform/governance/network`。

数据内部也已收敛：`ingestion` 只保留 `pipeline/repair`，原始 landing 与 index 合并为 `lake/catalog`，backfill observer 归入 `platform/observability/monitoring`。停牌真值属于 `data/pit`，费用、估值和事件账本属于 `portfolio/simulator`。

删除优先清理了不可达的 broker connectivity/mirror/UAT、go-live/incident/production replay/shadow-lab，以及重复的 research suite/benchmark/orchestrator/factor lifecycle；对应 CLI、CI 和测试也同步删除，不保留兼容入口。

本轮进一步把 `research/discovery + neural` 合并为 `research/search`，将 `validation/lab` 改为 `validation/walk_forward`，将 `portfolio/simulation` 改为 `portfolio/simulator`，并把 execution 的 broker/trading/settlement 前缀模块链压缩为 14 个能力文件。

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
