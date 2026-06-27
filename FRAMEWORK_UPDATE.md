## 2026-06-27 - 任务 001

### 本次变更摘要
- 建立 `data_pipeline.ashare` A 股基础数据模型包，新增配置、schema 和显式校验能力。
- 清理 `times.py` 顶部硬编码 Tushare token，改为运行时读取 `TUSHARE_TOKEN`。
- 新增最小测试，覆盖环境配置、股票代码/日期校验、财务公告日可用性和密钥扫描。

### 新增文件
- `data_pipeline/__init__.py`
- `data_pipeline/ashare/__init__.py`
- `data_pipeline/ashare/config.py`
- `data_pipeline/ashare/schema.py`
- `data_pipeline/ashare/validators.py`
- `.env.example`
- `tests/conftest.py`
- `tests/test_ashare_config.py`
- `tests/test_ashare_validators.py`
- `tests/test_times_secret.py`

### 修改文件
- `times.py`
- `FRAMEWORK_UPDATE.md`

### 删除或清理的旧问题
- 移除 `times.py` 中真实 Tushare token 字符串，避免继续把密钥提交到仓库。
- `times.py` import 阶段不读取 token、不初始化 Tushare API；缺少 `TUSHARE_TOKEN` 时在 `DataEngine` 初始化阶段明确报错。

### 新增 A 股平台能力
- `AShareDataConfig.from_env()` 显式读取 A 股数据环境变量，并校验复权类型和真实日期。
- 定义 A 股证券、交易日历、日线行情、每日指标、财务特征、因子元数据和因子值 dataclass。
- 增加 `FinancialFeature.is_available_on()` 与 `ensure_no_financial_lookahead()`，以公告日约束财务数据可用性，降低未来函数风险。
- 增加 A 股 `ts_code`、`YYYYMMDD` 日期和日线行情基础合法性校验。

### 测试结果
- `uv run pytest tests/test_ashare_config.py tests/test_ashare_validators.py tests/test_times_secret.py`：通过，22 passed。

### 后续待办
- 轮换已经暴露过的 Tushare token，并确认历史提交、备份和文档中不再保留真实密钥。
- 后续任务可逐步把旧 crypto 数据表和加载逻辑迁移为 A 股 `securities`、`daily_bars`、`daily_basic`、`financial_features` 等结构。

## 2026-06-27 - 任务 002

### 本次变更摘要
- 新增 A 股数据管线 dry-run 计划模块，描述待同步数据集、目标路径和基础配置。
- 将 `data_pipeline.run_pipeline` 从旧实时同步入口改为默认输出 A 股 pipeline plan 的 JSON CLI。
- 更新项目描述，移除 Solana/meme token 定位表述。
- 新增测试，锁定新入口不再引用 Birdeye/Solana 旧逻辑。

### 新增文件
- `data_pipeline/ashare/pipeline.py`
- `tests/test_ashare_pipeline.py`
- `tests/test_run_pipeline_cli.py`

### 修改文件
- `data_pipeline/ashare/__init__.py`
- `data_pipeline/run_pipeline.py`
- `pyproject.toml`
- `FRAMEWORK_UPDATE.md`

### 删除或隔离的旧问题
- `data_pipeline.run_pipeline` 不再导入旧 `DataManager` 和旧 `Config`。
- `data_pipeline.run_pipeline` 不再检查 `BIRDEYE_API_KEY`，也不再调用旧 token 数据同步流程。
- `--sync` 仅保留未来入口，本次明确返回非 0，不接真实 Tushare API。

### 新增 A 股平台能力
- `build_pipeline_plan()` 可基于 `AShareDataConfig` 生成固定 A 股数据集计划。
- 计划覆盖 `securities`、`trade_calendar`、`daily_bars`、`daily_basic`、`financial_features`。
- `PipelinePlan.to_dict()` 支持 CLI JSON 输出和后续测试/调度复用。
- `python -m data_pipeline.run_pipeline --pretty` 可直接查看 A 股数据管线 dry-run 计划。

### 测试结果
- `uv run pytest tests/test_ashare_config.py tests/test_ashare_validators.py tests/test_times_secret.py tests/test_ashare_pipeline.py tests/test_run_pipeline_cli.py`：通过，29 passed。
- `uv run python -m data_pipeline.run_pipeline --pretty`：通过，输出 A 股 pipeline plan JSON。

### 后续待办
- 后续任务可在 `--sync` 下接入真实 A 股数据同步实现，优先保持公告日、交易日和复权口径一致。
- 旧 `data_pipeline/config.py`、`data_manager.py`、`db_manager.py` 和 provider 目录仍待分阶段迁移或隔离。

## 2026-06-27 - 任务 003

### 本次变更摘要
- 将 `data_pipeline` 核心入口替换为 A 股本地同步框架。
- 删除旧 Birdeye/DexScreener provider 文件，不保留旧 crypto 兼容层。
- 新增 A 股 provider 抽象、确定性 sample provider、本地 JSONL storage 和同步 manager。
- 升级 `data_pipeline.run_pipeline`，支持 dry-run plan 与 `--sync --provider sample` 本地写入。

### 新增文件
- `data_pipeline/ashare/providers/__init__.py`
- `data_pipeline/ashare/providers/base.py`
- `data_pipeline/ashare/providers/factory.py`
- `data_pipeline/ashare/providers/sample.py`
- `data_pipeline/ashare/storage.py`
- `data_pipeline/ashare/manager.py`
- `tests/test_ashare_provider_sample.py`
- `tests/test_ashare_storage.py`
- `tests/test_ashare_manager.py`
- `tests/test_data_pipeline_no_crypto_core.py`

### 修改文件
- `data_pipeline/config.py`
- `data_pipeline/data_manager.py`
- `data_pipeline/db_manager.py`
- `data_pipeline/fetcher.py`
- `data_pipeline/processor.py`
- `data_pipeline/ashare/__init__.py`
- `data_pipeline/ashare/pipeline.py`
- `data_pipeline/run_pipeline.py`
- `requirements-optional.txt`
- `tests/test_run_pipeline_cli.py`
- `FRAMEWORK_UPDATE.md`

### 删除或隔离的旧问题
- 删除 `data_pipeline/providers/base.py`、`data_pipeline/providers/birdeye.py`、`data_pipeline/providers/dexscreener.py`。
- `data_pipeline/config.py` 不再定义旧 `Config`，不再 import 时读取旧环境变量。
- `data_pipeline/data_manager.py` 和 `db_manager.py` 不再暴露旧异步 token 同步和 Postgres/Timescale crypto schema。
- `data_pipeline/run_pipeline.py` 不再引用旧数据管理器、旧 provider 或旧业务配置。

### 新增 A 股平台能力
- `SampleAShareDataProvider` 可离线生成证券、交易日历、日线、每日指标和财务特征样例数据。
- `LocalAshareStorage` 可将五类 A 股数据集写入 `data_dir/<dataset>/records.jsonl`，并生成不含密钥的 manifest。
- `AShareDataManager.sync()` 可协调 provider 与 storage 完成本地同步。
- `python -m data_pipeline.run_pipeline --sync --provider sample --data-dir <path> --pretty` 已可写入本地样例数据。

### 测试结果
- `uv run pytest tests/test_ashare_config.py tests/test_ashare_validators.py tests/test_times_secret.py tests/test_ashare_pipeline.py tests/test_run_pipeline_cli.py tests/test_ashare_provider_sample.py tests/test_ashare_storage.py tests/test_ashare_manager.py tests/test_data_pipeline_no_crypto_core.py`：通过，42 passed。
- `uv run python -m data_pipeline.run_pipeline --sync --provider sample --data-dir /tmp/auto-alpha-ashare-sample --pretty`：通过，写出五类 JSONL 数据集和 manifest。

### 后续待办
- Tushare 真实 provider 仍未实现，后续应在 provider 层接入并保持无未来函数对齐。
- 旧研究、模型、执行和看板模块仍需分阶段迁移到 A 股语义。

## 2026-06-27 - 任务 004

### 本次变更摘要
- 将 `model_core` 从旧 meme/crypto 因子系统迁移为 A 股因子研发核心层。
- 新增 A 股特征工程、JSONL 数据加载、公式 DSL 算子、StackVM 执行和因子评价。
- 新增 `FactorMiningEngine`，支持 dry-run 评估和最小训练输出。
- 新增测试锁定 `model_core` 主干不再包含旧 crypto/Solana/meme 业务词。

### 新增文件
- `model_core/__init__.py`
- `tests/test_model_core_vocab_ops.py`
- `tests/test_model_core_vm.py`
- `tests/test_model_core_features.py`
- `tests/test_model_core_data_loader.py`
- `tests/test_model_core_evaluator.py`
- `tests/test_model_core_engine_cli.py`
- `tests/test_model_core_no_crypto_terms.py`

### 修改文件
- `model_core/config.py`
- `model_core/vocab.py`
- `model_core/ops.py`
- `model_core/vm.py`
- `model_core/factors.py`
- `model_core/data_loader.py`
- `model_core/backtest.py`
- `model_core/engine.py`
- `FRAMEWORK_UPDATE.md`

### 删除或隔离的旧问题
- 移除旧 `CryptoDataLoader`、`MemeBacktest`、`MemeIndicators` 和 `best_meme_strategy.json` 输出。
- 移除模型核心对数据库连接、旧交易规模、旧流动性阈值和旧滑点/费用逻辑的依赖。
- 移除旧特征词表中的 meme/crypto 特征，改为 A 股价量、估值和财务特征。

### 新增 A 股平台能力
- `AShareDataLoader` 可读取 data_pipeline 写出的 A 股 JSONL 数据，并按 `ts_code`、交易日和财务公告日对齐。
- `AShareFeatureEngineer` 输出与 `FEATURE_NAMES` 一致的 A 股特征张量。
- `StackVM` 支持 A 股 DSL 算子执行、公式描述和 arity 校验。
- `AShareFactorEvaluator` 输出 RankIC、RankIC IR、Top-Bottom spread、覆盖率、换手率和综合分数。
- `python -m model_core.engine --dry-run --data-dir <path>` 可对 sample 数据执行端到端因子评估。

### 测试结果
- `uv run pytest tests/test_ashare_config.py tests/test_ashare_validators.py tests/test_ashare_pipeline.py tests/test_run_pipeline_cli.py tests/test_ashare_provider_sample.py tests/test_ashare_storage.py tests/test_ashare_manager.py tests/test_data_pipeline_no_crypto_core.py tests/test_model_core_vocab_ops.py tests/test_model_core_vm.py tests/test_model_core_features.py tests/test_model_core_data_loader.py tests/test_model_core_evaluator.py tests/test_model_core_engine_cli.py tests/test_model_core_no_crypto_terms.py`：通过，59 passed。
- `uv run python -m data_pipeline.run_pipeline --sync --provider sample --data-dir /tmp/auto-alpha-model-core-sample/data --pretty`：通过，写出 sample A 股 JSONL 数据。
- `uv run python -m model_core.engine --dry-run --data-dir /tmp/auto-alpha-model-core-sample/data --output-dir /tmp/auto-alpha-model-core-sample/out`：通过，输出 dry-run JSON metrics。

### 后续待办
- 增加更完整的横截面中性化、行业/市值暴露控制和因子库管理。
- 增加样本内/样本外切分、滚动验证和组合级 A 股回测。
- 接入真实 Tushare provider 后扩展数据质量检查和公告日校验。

## 2026-06-27 - 任务 005

### 本次变更摘要
- 新增本地 JSONL 因子库 `factor_store`，支持因子、实验和因子值持久化。
- 新增 `evaluation` 包，提供时间序列样本切分、split metrics 和因子报告生成。
- 增强 `model_core.engine`，支持 `--register` / `--no-register`。
- 训练模式默认将 best factor 写入因子库、实验库、因子值和报告。

### 新增文件
- `factor_store/__init__.py`
- `factor_store/hash.py`
- `factor_store/models.py`
- `factor_store/storage.py`
- `evaluation/__init__.py`
- `evaluation/split.py`
- `evaluation/metrics.py`
- `evaluation/report.py`
- `tests/test_factor_store.py`
- `tests/test_evaluation_split_metrics_report.py`
- `tests/test_engine_register_factor.py`
- `tests/test_engine_training_register.py`
- `tests/test_engine_no_register.py`
- `tests/test_factor_platform_no_crypto_terms.py`

### 修改文件
- `model_core/backtest.py`
- `model_core/engine.py`
- `tests/test_model_core_engine_cli.py`
- `FRAMEWORK_UPDATE.md`

### 删除或隔离的旧问题
- 因子研究结果不再只停留在 engine 输出文件，已形成可追踪的因子库和实验记录。
- dry-run 默认仍不写入持久层，避免无意创建实验记录；显式 `--register` 才注册。
- `--no-register` 可让训练只生成本次训练产物，不写因子库和报告。

### 新增 A 股平台能力
- `LocalFactorStore` 写入 `factors.jsonl`、`experiments.jsonl` 和 `factor_values/<factor_id>.jsonl`。
- `split_trade_dates()` 支持 train/valid/test 时间序列切分，小样本可稳定工作。
- `evaluate_by_splits()` 输出 train、valid、test、all 四类 metrics。
- `write_factor_report()` 输出 `factor_report.json` 和 `factor_report.md`。
- `model_core.engine --dry-run --register` 可完成公式评估、因子入库、实验记录、因子值落盘和报告生成。

### 测试结果
- `uv run pytest tests/test_ashare_config.py tests/test_ashare_validators.py tests/test_ashare_pipeline.py tests/test_run_pipeline_cli.py tests/test_ashare_provider_sample.py tests/test_ashare_storage.py tests/test_ashare_manager.py tests/test_data_pipeline_no_crypto_core.py tests/test_model_core_vocab_ops.py tests/test_model_core_vm.py tests/test_model_core_features.py tests/test_model_core_data_loader.py tests/test_model_core_evaluator.py tests/test_model_core_engine_cli.py tests/test_model_core_no_crypto_terms.py tests/test_factor_store.py tests/test_evaluation_split_metrics_report.py tests/test_engine_register_factor.py tests/test_engine_training_register.py tests/test_engine_no_register.py tests/test_factor_platform_no_crypto_terms.py`：通过，69 passed。
- `uv run python -m data_pipeline.run_pipeline --sync --provider sample --data-dir /tmp/auto-alpha-factor-platform/data --pretty`：通过，写出 sample A 股 JSONL 数据。
- `uv run python -m model_core.engine --dry-run --register --data-dir /tmp/auto-alpha-factor-platform/data --output-dir /tmp/auto-alpha-factor-platform/out --factor-store-dir /tmp/auto-alpha-factor-platform/store --report-dir /tmp/auto-alpha-factor-platform/reports`：通过，写出因子库、实验记录、因子值和报告。
- `uv run python -m model_core.engine --steps 2 --batch-size 3 --data-dir /tmp/auto-alpha-factor-platform/data --output-dir /tmp/auto-alpha-factor-platform/train_out --factor-store-dir /tmp/auto-alpha-factor-platform/train_store --report-dir /tmp/auto-alpha-factor-platform/train_reports`：通过，训练模式默认注册 best factor。

### 后续待办
- 增加因子相关性去重、重复公式治理和因子版本生命周期管理。
- 增加行业/市值中性化、组合回测和更完整的样本外验证。
- 接入真实 Tushare provider 后扩展数据覆盖率、停复牌和财务公告质量检查。

## 2026-06-27 - 任务 006

### 本次变更摘要
- 新增 `backtest` A 股组合回测包，支持从因子值生成 long-only 目标权重和组合回测结果。
- 将 `execution` 从旧实盘执行替换为 A 股 paper broker 和交易指令导出层。
- 将 `strategy_manager` 从旧实盘循环替换为 A 股目标持仓和订单生成入口。
- 增强 `factor_store`，支持读取已保存的 factor values。

### 新增文件
- `backtest/__init__.py`
- `backtest/models.py`
- `backtest/cost.py`
- `backtest/rules.py`
- `backtest/portfolio.py`
- `backtest/io.py`
- `backtest/simulator.py`
- `backtest/run_backtest.py`
- `execution/__init__.py`
- `execution/models.py`
- `execution/exporter.py`
- `execution/paper_broker.py`
- `tests/test_factor_store_load_values.py`
- `tests/test_backtest_cost_rules.py`
- `tests/test_backtest_portfolio_simulator.py`
- `tests/test_backtest_cli.py`
- `tests/test_execution_paper_broker.py`
- `tests/test_strategy_runner_ashare.py`
- `tests/test_execution_strategy_no_crypto_terms.py`

### 修改文件
- `factor_store/storage.py`
- `execution/config.py`
- `strategy_manager/__init__.py`
- `strategy_manager/config.py`
- `strategy_manager/portfolio.py`
- `strategy_manager/risk.py`
- `strategy_manager/runner.py`
- `.env.example`
- `FRAMEWORK_UPDATE.md`

### 删除或隔离的旧问题
- 删除旧 `execution/jupiter.py`、`execution/rpc_handler.py`、`execution/trader.py`、`execution/utils.py`。
- `execution` 不再读取私钥、钱包、RPC 或链上交易配置。
- `strategy_manager` 不再读取旧策略文件、不再启动异步实盘循环、不再依赖旧模型加载器或实盘交易器。
- `backtest`、`execution`、`strategy_manager` 均不接网络和真实券商接口。

### 新增 A 股平台能力
- `AShareBacktestSimulator` 输出 equity snapshots、fills 和组合指标。
- `backtest.run_backtest` 可从 `factor_store/factor_values` 读取因子值并写出 `backtest_result.json`、`equity_curve.jsonl`、`trades.jsonl`。
- `PaperBroker` 可基于本地价格生成 paper fills。
- `AShareStrategyRunner` 可生成目标持仓、订单 CSV/JSONL 和 paper fills。

### 测试结果
- `uv run pytest tests/test_ashare_config.py tests/test_ashare_validators.py tests/test_ashare_pipeline.py tests/test_run_pipeline_cli.py tests/test_ashare_provider_sample.py tests/test_ashare_storage.py tests/test_ashare_manager.py tests/test_data_pipeline_no_crypto_core.py tests/test_model_core_vocab_ops.py tests/test_model_core_vm.py tests/test_model_core_features.py tests/test_model_core_data_loader.py tests/test_model_core_evaluator.py tests/test_model_core_engine_cli.py tests/test_model_core_no_crypto_terms.py tests/test_factor_store.py tests/test_factor_store_load_values.py tests/test_evaluation_split_metrics_report.py tests/test_engine_register_factor.py tests/test_engine_training_register.py tests/test_engine_no_register.py tests/test_factor_platform_no_crypto_terms.py tests/test_backtest_cost_rules.py tests/test_backtest_portfolio_simulator.py tests/test_backtest_cli.py tests/test_execution_paper_broker.py tests/test_strategy_runner_ashare.py tests/test_execution_strategy_no_crypto_terms.py`：通过。
- `uv run python -m backtest.run_backtest --data-dir /tmp/auto-alpha-portfolio-platform/data --factor-store-dir /tmp/auto-alpha-portfolio-platform/store --output-dir /tmp/auto-alpha-portfolio-platform/backtest --top-n 2 --max-weight 0.10 --pretty`：通过。
- `uv run python -m strategy_manager.runner --data-dir /tmp/auto-alpha-portfolio-platform/data --factor-store-dir /tmp/auto-alpha-portfolio-platform/store --output-dir /tmp/auto-alpha-portfolio-platform/orders --top-n 2 --max-weight 0.10 --portfolio-value 1000000 --pretty`：通过。

### 后续待办
- 迁移 dashboard 为 A 股因子研究看板。
- 接入真实 Tushare provider 并扩展停复牌、涨跌停、成交约束。
- 增加行业/市值中性化和更完整的组合回测。
- 清理旧依赖和仍未迁移的文档描述。

## 2026-06-27 - 任务 007

### 本次变更摘要
- 将 `dashboard/` 重构为 A 股因子研究本地 artifact 看板。
- 重写 `README.md` 和 `CATREADME.md`，对齐当前 A 股平台架构和 sample quickstart。
- 清理 `pyproject.toml`、`requirements.txt` 和 `uv.lock` 中不再需要的旧依赖。
- 新增 dashboard artifact、visualizer、app import、文档和依赖扫描测试。

### 新增文件
- `dashboard/config.py`
- `tests/test_dashboard_artifacts.py`
- `tests/test_dashboard_docs_dependencies.py`

### 修改文件
- `dashboard/app.py`
- `dashboard/data_service.py`
- `dashboard/visualizer.py`
- `README.md`
- `CATREADME.md`
- `pyproject.toml`
- `requirements.txt`
- `uv.lock`
- `FRAMEWORK_UPDATE.md`

### 删除或隔离的旧问题
- dashboard 不再访问数据库、链上服务、私钥或外部网络。
- dashboard import 不触发训练、数据同步或外部连接。
- 项目主文档不再描述旧业务主流程。
- 主依赖中移除不再使用的旧网络、数据库和链上 SDK。

### 新增 A 股平台能力
- `DashboardConfig` 统一读取本地 artifact 路径。
- `AshareDashboardService` 可读取 data、factor store、report、backtest、orders 和 paper fills。
- `dashboard.visualizer` 提供 equity curve、backtest metrics、split metrics 和 order distribution 图。
- Streamlit 页面包含 Data、Factors、Reports、Backtest、Orders 五个 tab，缺少 artifact 时展示空状态。

### 测试结果
- `uv run pytest`：通过，83 passed。
- 文档和 dashboard 目标文件扫描不含旧业务词。
- `pyproject.toml`、`requirements.txt`、`uv.lock` 扫描不含已移除依赖。

### 后续待办
- 为 dashboard 增加因子对比、参数过滤和多实验选择。
- 接入真实 Tushare provider 后补充数据质量和覆盖率面板。
- 继续收敛可选研究脚本和依赖边界。

## 2026-06-27 - 任务 008

### 本次变更摘要
- 新增基于 Python 标准库 `urllib.request` 的 Tushare Pro HTTP client。
- 新增 `TushareAShareDataProvider`，实现 securities、trade_calendar、daily_bars、daily_basic、financial_features 五类数据拉取和字段映射。
- 将 `provider=tushare` 从未实现入口切换为真实 HTTP provider，缺少 `TUSHARE_TOKEN` 时返回明确错误。
- 保持 sample provider 和本地 JSONL 同步路径不变。

### 新增文件
- `data_pipeline/ashare/providers/tushare_client.py`
- `data_pipeline/ashare/providers/tushare.py`
- `tests/test_tushare_client.py`
- `tests/test_tushare_provider.py`

### 修改文件
- `data_pipeline/ashare/config.py`
- `data_pipeline/ashare/providers/factory.py`
- `data_pipeline/ashare/providers/__init__.py`
- `data_pipeline/ashare/__init__.py`
- `tests/test_ashare_config.py`
- `tests/test_run_pipeline_cli.py`
- `README.md`
- `CATREADME.md`
- `requirements-optional.txt`
- `FRAMEWORK_UPDATE.md`

### 删除或隔离的旧问题
- `provider=tushare` 不再抛固定未实现错误。
- Tushare 接入不依赖 SDK，不新增第三方依赖。
- 测试通过 fake transport 和 fake client 离线验证，不访问真实 Tushare。

### 新增 A 股平台能力
- `AShareDataConfig` 支持 `TUSHARE_API_URL`、`TUSHARE_TIMEOUT_SECONDS`、`TUSHARE_RETRY_COUNT`。
- HTTP client 按 Tushare Pro 的 `api_name`、`token`、`params`、`fields` 请求结构提交 JSON。
- Provider 完成 Tushare 字段到 A 股 dataclass 的映射，例如 `vol -> volume`、`ann_date -> announce_date`。
- `run_pipeline --sync --provider tushare` 在配置真实 `TUSHARE_TOKEN` 后可进入真实同步路径。

### 测试结果
- `uv run pytest tests/test_ashare_config.py tests/test_tushare_client.py tests/test_tushare_provider.py tests/test_run_pipeline_cli.py`：通过，19 passed。
- `uv run pytest`：通过，91 passed。
- `uv run python -m data_pipeline.run_pipeline --sync --provider sample --data-dir /tmp/auto-alpha-task008-sample --pretty`：通过，写出五类 sample A 股 JSONL 数据。
- `env -u TUSHARE_TOKEN uv run python -m data_pipeline.run_pipeline --sync --provider tushare --data-dir /tmp/auto-alpha-task008-tushare`：返回 code 2，并提示缺少 `TUSHARE_TOKEN`。

### 后续待办
- 针对真实 Tushare 数据增加分页、增量同步和配额退避策略。
- 增加数据质量报告，覆盖缺失值、重复记录、停复牌和财务公告延迟。
- 为生产同步增加更完整的字段覆盖和分市场交易日历处理。

## 2026-06-27 - 任务 009

### 本次变更摘要
- 增强 A 股本地 JSONL storage，支持 dataset 读取、append 写入和按主键去重。
- 新增数据质量检查与 `quality_report.json` 输出。
- 新增 `pipeline_state.json` 同步状态文件。
- 增强 `data_pipeline.run_pipeline`，支持 `--mode overwrite|append`、`--validate`、`--quality-report`、`--state-file`。
- 新增 `universe/` 股票池构建包和 CLI。

### 新增文件
- `data_pipeline/ashare/quality.py`
- `data_pipeline/ashare/state.py`
- `universe/__init__.py`
- `universe/models.py`
- `universe/builder.py`
- `universe/run_universe.py`
- `tests/test_ashare_quality.py`
- `tests/test_ashare_state.py`
- `tests/test_universe_builder.py`
- `tests/test_data_governance_no_old_terms.py`

### 修改文件
- `data_pipeline/ashare/storage.py`
- `data_pipeline/ashare/manager.py`
- `data_pipeline/ashare/__init__.py`
- `data_pipeline/run_pipeline.py`
- `tests/test_ashare_storage.py`
- `tests/test_ashare_manager.py`
- `tests/test_run_pipeline_cli.py`
- `README.md`
- `CATREADME.md`
- `FRAMEWORK_UPDATE.md`

### 删除或隔离的旧问题
- append 同步不再简单追加重复记录，而是按数据集主键合并。
- 同步状态文件和质量报告不保存 token 或密钥。
- 新增治理和股票池代码不引入旧业务执行逻辑。

### 新增 A 股平台能力
- `LocalAshareStorage.read_dataset()` 可读取本地 JSONL 数据集。
- `validate_all_datasets()` 检查空数据集、非法股票代码、非法日期、重复主键、日线价格错误和财务公告日期字段。
- `AShareDataManager.sync(validate=True)` 同步后写出 manifest、pipeline state 和 quality report。
- `universe.run_universe` 可按 as-of-date、上市天数、成交额、交易所和板块构建本地股票池。

### 测试结果
- `uv run pytest tests/test_ashare_storage.py tests/test_ashare_quality.py tests/test_ashare_state.py tests/test_ashare_manager.py tests/test_run_pipeline_cli.py tests/test_universe_builder.py tests/test_data_governance_no_old_terms.py`：通过，30 passed。
- `uv run pytest`：通过，110 passed。
- `uv run python -m data_pipeline.run_pipeline --sync --provider sample --data-dir /tmp/auto-alpha-data-governance/data --validate --mode overwrite --pretty`：通过，写出五类数据、manifest、pipeline state 和 quality report。
- `uv run python -m data_pipeline.run_pipeline --sync --provider sample --data-dir /tmp/auto-alpha-data-governance/data --validate --mode append --pretty`：通过，重复同步后主键去重，记录数不膨胀。
- `uv run python -m universe.run_universe --data-dir /tmp/auto-alpha-data-governance/data --as-of-date 20240104 --universe-name all_a_sample --min-listed-days 0 --min-amount 0 --pretty`：通过，选出 3 个 sample 股票池成员。
- `uv run python -m model_core.engine --dry-run --register --data-dir /tmp/auto-alpha-data-governance/data --output-dir /tmp/auto-alpha-data-governance/out --factor-store-dir /tmp/auto-alpha-data-governance/store --report-dir /tmp/auto-alpha-data-governance/reports`：通过，生成因子库和报告。
- `uv run python -m backtest.run_backtest --data-dir /tmp/auto-alpha-data-governance/data --factor-store-dir /tmp/auto-alpha-data-governance/store --output-dir /tmp/auto-alpha-data-governance/backtest --top-n 2 --max-weight 0.10 --pretty`：通过，生成组合回测结果。

### 后续待办
- 将 dashboard Data tab 增强为质量报告、同步状态和股票池 summary 的可视化入口。
- 为 Tushare 生产同步补充分页、增量日期窗口、重试退避和数据覆盖率报告。
- 增加更完整的 A 股股票池规则，例如上市板块、风险警示变更、停复牌连续性和行业覆盖。

## 2026-06-27 - 任务 010

### 本次变更摘要
- 新增 `factor_engine/`，提供横截面 winsorize、zscore、市值中性化、行业中性化、相关性检查和准入门禁。
- 增强 `AShareDataLoader`，支持 `universe_name` / `universe_file` 过滤，并输出行业编码和 `log_mkt_cap`。
- 增强因子评价指标，增加 RankIC std/t-stat/正值比例、Top-Bottom 胜率和单调性。
- 增强 engine 注册路径，支持 transform、correlation check、gate decision、universe-aware 注册。
- 增强 factor store、report 和 dashboard 因子页，展示 transform/gate/correlation metadata。

### 新增文件
- `factor_engine/__init__.py`
- `factor_engine/transforms.py`
- `factor_engine/correlation.py`
- `factor_engine/gate.py`
- `factor_engine/pipeline.py`
- `tests/test_factor_engine_transforms.py`
- `tests/test_factor_engine_correlation.py`
- `tests/test_factor_engine_gate.py`
- `tests/test_engine_factor_research_integration.py`
- `tests/test_factor_research_no_old_terms.py`

### 修改文件
- `model_core/data_loader.py`
- `model_core/backtest.py`
- `model_core/engine.py`
- `evaluation/metrics.py`
- `evaluation/report.py`
- `factor_store/models.py`
- `factor_store/storage.py`
- `dashboard/data_service.py`
- `dashboard/app.py`
- `tests/test_model_core_data_loader.py`
- `tests/test_model_core_evaluator.py`
- `tests/test_evaluation_split_metrics_report.py`
- `tests/test_factor_store.py`
- `tests/test_dashboard_artifacts.py`
- `README.md`
- `CATREADME.md`
- `FRAMEWORK_UPDATE.md`

### 删除或隔离的旧问题
- engine 注册因子不再只保存原始因子值，可保存处理后的因子值。
- factor store 兼容旧记录缺少 transform/gate metadata 的情况。
- dashboard 因子页不再只显示基础 metrics，可查看 status、gate 和 transform metadata。

### 新增 A 股平台能力
- `--universe-name` / `--universe-file` 让 engine 只在指定股票池内研发和注册因子。
- `--factor-transform` 支持 raw、winsorize、zscore、winsorize_zscore、neutralize_market_cap、neutralize_industry、neutralize_industry_size。
- `--enable-gate` 可基于 coverage、test split 指标、turnover 和 max_abs_correlation 生成 approved/rejected。
- 注册结果写入 factor record、experiment、factor values 和 factor report，并在 stdout 输出 gate/correlation/status。

### 测试结果
- `uv run pytest tests/test_factor_engine_transforms.py tests/test_factor_engine_correlation.py tests/test_factor_engine_gate.py tests/test_model_core_data_loader.py tests/test_model_core_evaluator.py tests/test_evaluation_split_metrics_report.py tests/test_factor_store.py tests/test_engine_factor_research_integration.py tests/test_dashboard_artifacts.py tests/test_factor_research_no_old_terms.py`：通过，34 passed。
- `uv run pytest`：通过，131 passed。
- `uv run python -m data_pipeline.run_pipeline --sync --provider sample --data-dir /tmp/auto-alpha-factor-research/data --validate --mode overwrite --pretty`：通过，质量报告无错误。
- `uv run python -m universe.run_universe --data-dir /tmp/auto-alpha-factor-research/data --as-of-date 20240104 --universe-name all_a_sample --min-listed-days 0 --min-amount 0 --pretty`：通过，选出 3 个 sample 成员。
- `uv run python -m model_core.engine --dry-run --register --data-dir /tmp/auto-alpha-factor-research/data --universe-name all_a_sample --output-dir /tmp/auto-alpha-factor-research/out --factor-store-dir /tmp/auto-alpha-factor-research/store --report-dir /tmp/auto-alpha-factor-research/reports --factor-transform neutralize_industry_size --enable-gate --correlation-threshold 0.99 --min-coverage 0.5 --pretty`：通过，gate approved，写出 transform/gate/correlation metadata。
- `uv run python -m model_core.engine --steps 3 --batch-size 4 --data-dir /tmp/auto-alpha-factor-research/data --universe-name all_a_sample --output-dir /tmp/auto-alpha-factor-research/train_out --factor-store-dir /tmp/auto-alpha-factor-research/store --report-dir /tmp/auto-alpha-factor-research/train_reports --factor-transform winsorize_zscore --enable-gate --correlation-threshold 0.99 --min-coverage 0.5`：通过，训练模式默认注册，gate approved。
- `uv run python -m backtest.run_backtest --data-dir /tmp/auto-alpha-factor-research/data --factor-store-dir /tmp/auto-alpha-factor-research/store --output-dir /tmp/auto-alpha-factor-research/backtest --top-n 2 --max-weight 0.10 --pretty`：通过，生成组合回测结果。

### 后续待办
- 将中性化扩展为更完整的风险模型和更细行业分类。
- 增加因子库相似因子治理策略，例如自动降级、替换和分组展示。
- dashboard 增加多因子对比、gate 失败原因筛选和相关性网络视图。

## 2026-06-27 - 任务 011

### 本次变更摘要
- 扩展 A 股数据模型和本地数据管线，新增 `daily_limits`、`adjustment_factors`、`index_members` 三类市场约束数据。
- 增强 sample / Tushare provider，支持涨跌停、复权因子和指数成分字段映射。
- 增强 `AShareDataLoader`，输出复权价格、涨跌停标记、停牌标记、指数成分矩阵和成交量/成交额矩阵。
- 增强 universe 构建，支持基于 `index_members` 的指数股票池。
- 升级 A 股组合回测撮合，支持停牌、涨跌停、T+1、整手、成交量参与率、成本、拒单和部分成交。
- 增强 paper broker / strategy runner，使纸面成交与回测共用交易约束并输出拒单原因。
- 增强 dashboard 本地 artifact 展示，增加市场约束数据、质量报告和成交状态字段。

### 新增文件
- `tests/test_ashare_schema_market_constraints.py`

### 修改文件
- `data_pipeline/ashare/schema.py`
- `data_pipeline/ashare/config.py`
- `data_pipeline/ashare/pipeline.py`
- `data_pipeline/ashare/storage.py`
- `data_pipeline/ashare/providers/base.py`
- `data_pipeline/ashare/providers/sample.py`
- `data_pipeline/ashare/providers/tushare.py`
- `data_pipeline/ashare/quality.py`
- `data_pipeline/ashare/__init__.py`
- `data_pipeline/run_pipeline.py`
- `model_core/data_loader.py`
- `universe/models.py`
- `universe/builder.py`
- `universe/run_universe.py`
- `backtest/models.py`
- `backtest/rules.py`
- `backtest/simulator.py`
- `execution/models.py`
- `execution/paper_broker.py`
- `strategy_manager/runner.py`
- `dashboard/data_service.py`
- `dashboard/app.py`
- `README.md`
- `CATREADME.md`
- 相关测试文件
- `FRAMEWORK_UPDATE.md`

### 删除或隔离的旧问题
- 回测不再只做简化权重收益模拟，新增交易约束、拒单和部分成交结果。
- 纸面成交不再静默跳过无法成交订单，而是写出 `REJECTED` 状态和原因。
- 股票池构建不再只能从全市场证券列表出发，可使用本地指数成分数据。

### 新增 A 股平台能力
- `run_pipeline --index-codes` 可同步指数成分，并在 manifest / state / quality report 中覆盖 8 类数据集。
- `universe.run_universe --use-index-members --index-code` 可按指定指数最新成分构建股票池。
- `AShareDataLoader` 使用 `adjusted_close` 计算目标收益，并保留 `close` 作为成交价格。
- `AShareBacktestSimulator` 记录 `rejected_trades`、`partial_fills`、`fill_rate`、`constraint_reject_rate`、`avg_exposure` 和 `cash_drag`。
- `PaperBroker` 输出 `FILLED` / `PARTIAL` / `REJECTED`，并保存成本和拒单原因。

### 测试结果
- `uv run pytest`：通过，142 passed。
- `uv run python -m data_pipeline.run_pipeline --sync --provider sample --data-dir /tmp/auto-alpha-market-rules/data --validate --mode overwrite --index-codes 000300.SH --pretty`：通过，写出 8 类数据集，quality report 无 error / warning。
- `uv run python -m universe.run_universe --data-dir /tmp/auto-alpha-market-rules/data --as-of-date 20240104 --universe-name csi300_sample --use-index-members --index-code 000300.SH --min-listed-days 0 --min-amount 0 --pretty`：通过，基于 `000300.SH` 最新成分选出 3 个 sample 成员。
- `uv run python -m model_core.engine --dry-run --register --data-dir /tmp/auto-alpha-market-rules/data --universe-name csi300_sample --output-dir /tmp/auto-alpha-market-rules/out --factor-store-dir /tmp/auto-alpha-market-rules/store --report-dir /tmp/auto-alpha-market-rules/reports --factor-transform winsorize_zscore --enable-gate --correlation-threshold 0.99 --min-coverage 0.5 --pretty`：通过，gate approved 并写出因子库、实验、因子值和报告。
- `uv run python -m backtest.run_backtest --data-dir /tmp/auto-alpha-market-rules/data --factor-store-dir /tmp/auto-alpha-market-rules/store --output-dir /tmp/auto-alpha-market-rules/backtest --top-n 2 --max-weight 0.10 --pretty`：通过，生成约束撮合回测，包含拒单和 fill rate 指标。
- `uv run python -m strategy_manager.runner --data-dir /tmp/auto-alpha-market-rules/data --factor-store-dir /tmp/auto-alpha-market-rules/store --output-dir /tmp/auto-alpha-market-rules/orders --top-n 2 --max-weight 0.10 --portfolio-value 1000000 --pretty`：通过，生成目标持仓、订单和 paper fills，纸面成交包含拒单原因。

### 后续待办
- 将日频约束撮合扩展到更精细的盘口、分钟级成交量和真实滑点模型。
- 完善指数成分历史变更、复权校验和停复牌连续性检查。
- 增加更多指数股票池模板和真实券商接口前的人工复核流程。

## 2026-06-27 - 任务 012

### 本次变更摘要
- 新增 `research/` 批量因子研发编排层，支持默认候选公式、JSON 候选、批量 VM 执行、transform、gate、correlation check、注册和 batch report。
- 增强 `factor_store`，支持 composite factor 兼容字段、按 formula hash 查找、状态更新和 factor values 矩阵加载。
- 增强 `factor_engine.correlation`，新增相关性矩阵和 pairwise correlation table。
- 新增 composite factor 构建与注册，支持 `equal_weight`、`score_weighted`、`rank_average`。
- 增强 `backtest.run_backtest` 和 `strategy_manager.runner`，支持 `--latest-approved` 与 `--factor-type single|composite|any`。
- 增强 dashboard，展示 factor type、batch id、component factors，并可读取 batch research report。

### 新增文件
- `research/__init__.py`
- `research/models.py`
- `research/candidates.py`
- `research/batch_runner.py`
- `research/composite.py`
- `research/report.py`
- `research/run_batch.py`
- `tests/test_research_candidates.py`
- `tests/test_research_batch_runner.py`
- `tests/test_research_composite.py`
- `tests/test_research_composite_cli_integration.py`
- `tests/test_research_run_batch_cli.py`
- `tests/test_research_no_old_terms.py`
- `tests/test_factor_store_batch_compatibility.py`

### 修改文件
- `factor_store/models.py`
- `factor_store/storage.py`
- `factor_engine/__init__.py`
- `factor_engine/correlation.py`
- `model_core/engine.py`
- `backtest/__init__.py`
- `backtest/io.py`
- `backtest/run_backtest.py`
- `strategy_manager/runner.py`
- `dashboard/data_service.py`
- `dashboard/app.py`
- `tests/test_factor_engine_correlation.py`
- `tests/test_dashboard_artifacts.py`
- `README.md`
- `CATREADME.md`
- `FRAMEWORK_UPDATE.md`

### 删除或隔离的旧问题
- 批量实验不再重复注册相同公式 hash 的候选因子。
- 回测和订单生成不再只能默认选择最新因子，可显式选择最新 approved composite factor。
- dashboard 兼容旧 factor records 缺少 batch/composite metadata 的情况。

### 新增 A 股平台能力
- `research.default_candidates()` 提供 12 个基础 A 股候选公式。
- `python -m research.run_batch` 可生成 `batch_result.json`、`batch_results.jsonl`、`batch_report.json` 和 `batch_report.md`。
- composite factor 作为 `factor_type=composite` 写入 factor store，并保存 component factor ids。
- `backtest.run_backtest --latest-approved --factor-type composite` 可直接回测最新 approved composite factor。
- `strategy_manager.runner --latest-approved --factor-type composite` 可直接用 composite factor 生成目标持仓、订单和 paper fills。

### 测试结果
- `uv run pytest tests/test_research_candidates.py tests/test_research_batch_runner.py tests/test_research_composite.py tests/test_research_composite_cli_integration.py tests/test_research_run_batch_cli.py tests/test_factor_store_batch_compatibility.py tests/test_factor_engine_correlation.py tests/test_dashboard_artifacts.py tests/test_backtest_cli.py tests/test_strategy_runner_ashare.py`：通过，23 passed。
- `uv run pytest`：通过，157 passed。
- `uv run python -m data_pipeline.run_pipeline --sync --provider sample --data-dir /tmp/auto-alpha-batch-research/data --validate --mode overwrite --index-codes 000300.SH --pretty`：通过，写出 8 类数据集，quality report 无 error / warning。
- `uv run python -m universe.run_universe --data-dir /tmp/auto-alpha-batch-research/data --as-of-date 20240104 --universe-name csi300_sample --use-index-members --index-code 000300.SH --min-listed-days 0 --min-amount 0 --pretty`：通过，基于 `000300.SH` 最新成分选出 3 个 sample 成员。
- `uv run python -m research.run_batch --data-dir /tmp/auto-alpha-batch-research/data --universe-name csi300_sample --factor-store-dir /tmp/auto-alpha-batch-research/store --report-dir /tmp/auto-alpha-batch-research/reports --output-dir /tmp/auto-alpha-batch-research/batch --factor-transform winsorize_zscore --enable-gate --top-k 5 --max-candidates 8 --composite-method rank_average --correlation-threshold 0.99 --min-coverage 0.5 --pretty`：通过，8 个候选中 6 个 approved、2 个 rejected，并生成 composite factor。
- `uv run python -m backtest.run_backtest --data-dir /tmp/auto-alpha-batch-research/data --factor-store-dir /tmp/auto-alpha-batch-research/store --output-dir /tmp/auto-alpha-batch-research/backtest --latest-approved --factor-type composite --top-n 2 --max-weight 0.10 --pretty`：通过，选中最新 approved composite factor 并生成组合回测。
- `uv run python -m strategy_manager.runner --data-dir /tmp/auto-alpha-batch-research/data --factor-store-dir /tmp/auto-alpha-batch-research/store --output-dir /tmp/auto-alpha-batch-research/orders --latest-approved --factor-type composite --top-n 2 --max-weight 0.10 --portfolio-value 1000000 --pretty`：通过，选中最新 approved composite factor 并生成目标持仓、订单和 paper fills。

### 后续待办
- 扩展候选公式来源，包括配置化公式库、搜索器输出和训练生成公式。
- 增强 composite factor 的权重优化、稳定性分析和样本外衰减监控。
- dashboard 增加 batch 间对比、相关性热力图和 composite component drill-down。

## 2026-06-27 - 任务 013

### 本次变更摘要
- 增强公式 DSL，新增 delay、delta、rolling mean/std/rank/min/max/corr 等 A 股因子算子。
- 为算子增加 arity、lookback、complexity 元数据和查询 helper。
- 增强 `StackVM`，支持 `validate_with_reason()`、公式 complexity/lookback、canonical formula 和 explain。
- 新增 `formula_search/`，支持随机生成、seed formulas、变异、交叉、去重、多代搜索和 search report。
- 增强 `research/` 候选公式 metadata，支持 formula search candidate 转 batch candidate。
- 增强 batch report、factor metadata 和 dashboard，展示 source、generation、complexity、lookback 和 search report。

### 新增文件
- `formula_search/__init__.py`
- `formula_search/models.py`
- `formula_search/generator.py`
- `formula_search/mutation.py`
- `formula_search/search.py`
- `formula_search/report.py`
- `formula_search/run_search.py`
- `tests/test_formula_search_generator.py`
- `tests/test_formula_search_mutation.py`
- `tests/test_formula_search_runner.py`
- `tests/test_formula_search_cli.py`
- `tests/test_formula_search_no_old_terms.py`

### 修改文件
- `model_core/ops.py`
- `model_core/vm.py`
- `research/models.py`
- `research/candidates.py`
- `research/batch_runner.py`
- `research/report.py`
- `research/__init__.py`
- `dashboard/data_service.py`
- `dashboard/app.py`
- `tests/test_model_core_vocab_ops.py`
- `tests/test_model_core_vm.py`
- `tests/test_research_candidates.py`
- `tests/test_research_batch_runner.py`
- `tests/test_dashboard_artifacts.py`
- `README.md`
- `CATREADME.md`
- `FRAMEWORK_UPDATE.md`

### 删除或隔离的旧问题
- 候选生成不再完全依赖手写公式列表。
- 非法公式不再只返回布尔失败，可给出 stack underflow、empty formula、multi output stack 等原因。
- 搜索候选通过 formula hash 去重，避免重复注册同一 canonical formula。

### 新增 A 股平台能力
- `research.default_candidates()` 扩展到 20 个基础候选，覆盖新增时间序列和横截面算子。
- `formula_search.generate_initial_population()` 可按 seed 可复现生成合法公式。
- `formula_search.mutate_formula()` 和 `crossover_formula()` 可生成带 parent hashes 的合法子公式。
- `python -m formula_search.run_search` 可输出 `search_result.json`、`search_candidates.jsonl`、`search_report.json` 和 `search_report.md`。
- search runner 复用 batch research / gate / composite 流程，可直接生成 approved factors 和 composite factor。

### 测试结果
- `uv run pytest tests/test_model_core_vocab_ops.py tests/test_model_core_vm.py tests/test_formula_search_generator.py tests/test_formula_search_mutation.py tests/test_formula_search_runner.py tests/test_formula_search_cli.py tests/test_formula_search_no_old_terms.py tests/test_research_candidates.py tests/test_research_batch_runner.py tests/test_dashboard_artifacts.py`：通过，32 passed。
- `uv run pytest`：通过，170 passed。
- `uv run python -m data_pipeline.run_pipeline --sync --provider sample --data-dir /tmp/auto-alpha-formula-search/data --validate --mode overwrite --index-codes 000300.SH --pretty`：通过，写出 8 类数据集，quality report 无 error / warning。
- `uv run python -m universe.run_universe --data-dir /tmp/auto-alpha-formula-search/data --as-of-date 20240104 --universe-name csi300_sample --use-index-members --index-code 000300.SH --min-listed-days 0 --min-amount 0 --pretty`：通过，基于 `000300.SH` 最新成分选出 3 个 sample 成员。
- `uv run python -m formula_search.run_search --data-dir /tmp/auto-alpha-formula-search/data --universe-name csi300_sample --factor-store-dir /tmp/auto-alpha-formula-search/store --report-dir /tmp/auto-alpha-formula-search/reports --output-dir /tmp/auto-alpha-formula-search/search --seed 42 --population-size 12 --generations 2 --max-formula-len 8 --max-complexity 24 --max-lookback 10 --factor-transform winsorize_zscore --enable-gate --top-k 5 --composite-method rank_average --correlation-threshold 0.99 --min-coverage 0.5 --pretty`：通过，两代共评估 19 个候选，生成 10 个 approved factor 和 composite factor。
- `uv run python -m backtest.run_backtest --data-dir /tmp/auto-alpha-formula-search/data --factor-store-dir /tmp/auto-alpha-formula-search/store --output-dir /tmp/auto-alpha-formula-search/backtest --latest-approved --factor-type composite --top-n 2 --max-weight 0.10 --pretty`：通过，选中 search 生成的最新 approved composite factor 并生成回测。
- `uv run python -m strategy_manager.runner --data-dir /tmp/auto-alpha-formula-search/data --factor-store-dir /tmp/auto-alpha-formula-search/store --output-dir /tmp/auto-alpha-formula-search/orders --latest-approved --factor-type composite --top-n 2 --max-weight 0.10 --portfolio-value 1000000 --pretty`：通过，选中 search 生成的最新 approved composite factor 并生成目标持仓、订单和 paper fills。

### 后续待办
- 将公式搜索扩展为 neural-guided search 和更大规模候选池。
- 增加更多 A 股特色算子、行业/风格风险暴露控制和复杂度惩罚策略。
- dashboard 增加 search generation 对比、公式树展示和候选演化路径。
