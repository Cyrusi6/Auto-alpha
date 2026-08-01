# 当前仓库架构

Auto-alpha 当前只服务于 A 股因子研究、验证、组合研究和受治理的 shadow 证据链。

## 目录

- `src/`：全部正式 Python 包。
- `tests/`：测试与合成 fixture。
- `docs/`：架构和操作文档。
- `evidence/`：可提交的脱敏摘要，不含真实市场数据。
- `dev_tools/`：仓库维护与隔离演练工具。

历史任务编号不再作为顶层架构。对应实现已归入：

- `src/point_in_time/historical_audit/`
- `src/backfill_repair/governed_replay/`
- `src/feature_factory/engineering_replay/`
- `src/research_firewall/`
- `src/live_readiness/`

正式研究链为：数据冻结 → PIT matrix/tensor → Alpha Factory → 样本外验证 → 因子认证 → 组合研究 → shadow。任何阶段都不能直接跳到 paper 或 live。

常用命令及安全边界见 `README.md`，包边界见 `docs/ARCHITECTURE.md`。
