# 当前架构

Auto-alpha 只服务于 A 股量化因子研发平台，正式代码统一位于 `src/auto_alpha/`。

## 六个领域

- `data/`：摄取、数据湖、PIT、股票池、矩阵和质量。
- `research/`：特征、公式、因子、搜索和 neural-guided research。
- `validation/`：Research Firewall、walk-forward 和认证。
- `portfolio/`：因子组合、风险、容量和事件账本模拟。
- `execution/`：券商、交易、结算、对账和运行操作。
- `platform/`：artifact、调度、治理、监控、发布和网络权威。

共 25 个可见子系统。测试目录按相同六领域镜像。任务编号不再是包名；Task 055 只保留最终、扁平的 `platform/network_authority`，旧代源码、`_internal` 历史树、公开入口和测试均已删除。

数据内部也已收敛：`ingestion` 只保留 `pipeline/repair`，原始 landing 与 index 合并为 `lake/catalog`，backfill observer 归入 `platform/observability/monitoring`。停牌真值属于 `data/pit`，费用、估值和事件账本属于 `portfolio/simulation`，不再堆进网络权威模块。

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
