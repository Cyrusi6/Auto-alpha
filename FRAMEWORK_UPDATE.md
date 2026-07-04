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

## 2026-06-27 - 任务 014

### 本次变更摘要
- 新增 `research_suite/`，提供一键运行 data sync、universe、formula search、backtest、orders、walk-forward、promotion 和 artifact catalog 的研究套件。
- 新增 walk-forward 稳健性评估，输出每个窗口 train/test metrics 和稳定性摘要。
- 新增 promotion gate，将合格 composite factor 晋级为 `production_candidate` 并写入 factor metadata。
- 新增 artifact catalog，统一索引 suite 产生的数据、报告、因子库、回测、订单和晋级决策。
- 增强 dashboard，读取 suite result、suite report、artifact catalog 和 promotion decision。

### 新增文件
- `research_suite/__init__.py`
- `research_suite/models.py`
- `research_suite/catalog.py`
- `research_suite/walk_forward.py`
- `research_suite/promotion.py`
- `research_suite/workflow.py`
- `research_suite/report.py`
- `research_suite/run_suite.py`
- `tests/test_research_suite_catalog.py`
- `tests/test_research_suite_walk_forward.py`
- `tests/test_research_suite_promotion.py`
- `tests/test_research_suite_workflow.py`
- `tests/test_research_suite_cli.py`
- `tests/test_research_suite_no_old_terms.py`

### 修改文件
- `factor_store/storage.py`
- `dashboard/data_service.py`
- `dashboard/app.py`
- `tests/test_factor_store_batch_compatibility.py`
- `tests/test_dashboard_artifacts.py`
- `README.md`
- `CATREADME.md`
- `FRAMEWORK_UPDATE.md`

### 删除或隔离的旧问题
- 完整研究流程不再需要手工串联多条命令。
- 因子晋级不再只依赖单次 batch/search 结果，新增 walk-forward 和回测约束检查。
- 一次研究运行产生的 artifact 不再分散无索引，新增统一 catalog。

### 新增 A 股平台能力
- `python -m research_suite.run_suite` 可一键生成 suite report、search report、backtest、orders、walk-forward、promotion decision 和 artifact catalog。
- `build_walk_forward_windows()` 与 `evaluate_factor_walk_forward()` 可评估因子跨时间窗口稳定性。
- `promote_factor_if_eligible()` 可将通过门槛的 composite factor 更新为 `production_candidate`。
- `LocalFactorStore` 支持 `list_factors()`、`load_latest_factor()`，并可在 `update_factor_status()` 时写入 promotion metadata。
- dashboard Reports tab 可展示 suite stage status、promotion decision 和 artifact catalog 摘要。

### 测试结果
- `uv run pytest tests/test_research_suite_catalog.py tests/test_research_suite_walk_forward.py tests/test_research_suite_promotion.py tests/test_factor_store_batch_compatibility.py tests/test_research_suite_workflow.py tests/test_research_suite_cli.py tests/test_dashboard_artifacts.py tests/test_research_suite_no_old_terms.py`：通过，19 passed。
- `uv run pytest`：通过，182 passed。
- `uv run python -m research_suite.run_suite --suite-name sample_suite --provider sample --data-dir /tmp/auto-alpha-research-suite/data --universe-name csi300_sample --index-code 000300.SH --factor-store-dir /tmp/auto-alpha-research-suite/store --report-dir /tmp/auto-alpha-research-suite/reports --output-dir /tmp/auto-alpha-research-suite/suite --backtest-dir /tmp/auto-alpha-research-suite/backtest --orders-dir /tmp/auto-alpha-research-suite/orders --as-of-date 20240104 --factor-transform winsorize_zscore --search-seed 42 --search-population-size 12 --search-generations 2 --search-max-candidates 8 --top-k 5 --composite-method rank_average --promote-latest-composite --walk-forward-train-size 1 --walk-forward-test-size 1 --walk-forward-step-size 1 --pretty`：通过，全部 stage success。
- suite 输出 `suite_result.json`、`suite_report.md`、`walk_forward_result.json`、`promotion_decision.json`、`artifact_catalog.json`、`artifact_catalog.md`。
- sample suite 选中 composite factor `factor_0c8dda802c9fd989`，promotion decision passed，并晋级为 `production_candidate`。

### 后续待办
- 为 production_candidate 增加人工审核、冻结版本和发布记录。
- 扩展 walk-forward 为更多窗口策略、样本外分组和稳健性惩罚。
- dashboard 增加 suite 历史对比、artifact 下载和 promotion 审核视图。

## 2026-06-27 - 任务 015

### 本次变更摘要
- 新增生产化 A 股同步计划层，支持按数据集、日期窗口和指数代码生成稳定 sync jobs。
- 增强 Tushare provider，支持按 `SyncJob` 分段拉取，并接入本地响应缓存和 API request audit。
- 增强本地 JSONL storage，支持 dataset compaction、snapshot、record index 和 dataset stats。
- 增强 `data_pipeline.run_pipeline`，支持 `--plan-only`、`--use-plan`、`--resume`、`--validate-only`、`--fail-on-quality-error`、`--compact`、`--snapshot`、`--stats`、`--audit`。
- 增强 dashboard，展示 sync plan、pipeline state、API audit、dataset stats 和 snapshot summary。

### 新增文件
- `data_pipeline/ashare/sync_plan.py`
- `data_pipeline/ashare/cache.py`
- `data_pipeline/ashare/audit.py`
- `data_pipeline/ashare/compaction.py`
- `data_pipeline/ashare/stats.py`
- `tests/test_ashare_sync_plan.py`
- `tests/test_tushare_chunked_sync.py`
- `tests/test_ashare_storage_governance.py`
- `tests/test_ashare_manager_production_sync.py`
- `tests/test_run_pipeline_production_sync.py`
- `tests/test_production_sync_no_old_terms.py`

### 修改文件
- `data_pipeline/ashare/__init__.py`
- `data_pipeline/ashare/manager.py`
- `data_pipeline/ashare/providers/tushare.py`
- `data_pipeline/ashare/state.py`
- `data_pipeline/ashare/storage.py`
- `data_pipeline/run_pipeline.py`
- `dashboard/data_service.py`
- `dashboard/app.py`
- `tests/test_dashboard_artifacts.py`
- `README.md`
- `CATREADME.md`
- `FRAMEWORK_UPDATE.md`

### 删除或隔离的旧问题
- Tushare 同步不再只能一次性按全数据集拉取，新增按 job/date-window/index-code 的计划执行路径。
- 重复 append 后的数据集可通过 compaction 按主键稳定去重。
- 本地数据湖不再只有 records 文件，新增 stats、snapshot 和 index 能力。
- 同步过程可通过 pipeline state 记录 job 成功/失败，为 resume 提供依据。
- 请求审计和缓存均不写入密钥。

### 新增 A 股平台能力
- `build_sync_plan()` 可生成稳定 `plan_id` 和 `job_id`，用于可复现同步计划。
- `TushareResponseCache` 基于 `api_name`、`params`、`fields` 缓存响应。
- `ApiRequestAuditor` 写入 `api_audit.jsonl`，记录 cache hit、records、status、error 和耗时。
- `LocalAshareStorage` 支持 `compact_dataset()`、`snapshot_dataset()`、`build_record_index()`、`read_dataset_index()`、`dataset_exists()`。
- `compute_all_dataset_stats()` 写出 `dataset_stats.json`，包含记录数、主键唯一数、重复数、日期范围、股票数量、空值计数和文件大小。
- `run_pipeline --validate-only` 可只对现有数据做质量检查；`--fail-on-quality-error` 可作为质量门禁返回非 0。

### 测试结果
- `uv run pytest tests/test_ashare_config.py tests/test_ashare_validators.py tests/test_ashare_pipeline.py tests/test_ashare_provider_sample.py tests/test_ashare_quality.py tests/test_ashare_schema_market_constraints.py tests/test_ashare_state.py tests/test_ashare_storage.py tests/test_ashare_sync_plan.py tests/test_tushare_client.py tests/test_tushare_provider.py tests/test_tushare_chunked_sync.py tests/test_ashare_storage_governance.py tests/test_ashare_manager.py tests/test_ashare_manager_production_sync.py tests/test_run_pipeline_cli.py tests/test_run_pipeline_production_sync.py tests/test_dashboard_artifacts.py tests/test_dashboard_docs_dependencies.py tests/test_data_governance_no_old_terms.py tests/test_production_sync_no_old_terms.py`：通过，92 passed。
- `uv run pytest`：通过，197 passed。
- `uv run python -m data_pipeline.run_pipeline --plan-only --provider sample --data-dir /tmp/auto-alpha-production-sync/data --start-date 20240102 --end-date 20240104 --index-codes 000300.SH --chunk-days 1 --pretty`：通过，生成 20 个 sync jobs。
- `uv run python -m data_pipeline.run_pipeline --sync --use-plan --provider sample --data-dir /tmp/auto-alpha-production-sync/data --start-date 20240102 --end-date 20240104 --index-codes 000300.SH --chunk-days 1 --validate --audit --stats --compact --snapshot --mode append --pretty`：通过，quality 无 error，生成 `sync_plan.json`、`api_audit.jsonl`、`dataset_stats.json` 和 snapshot。
- `uv run python -m data_pipeline.run_pipeline --validate-only --data-dir /tmp/auto-alpha-production-sync/data --pretty`：通过，读取现有数据并重写 quality report。
- `uv run python -m universe.run_universe --data-dir /tmp/auto-alpha-production-sync/data --as-of-date 20240104 --universe-name csi300_sample --use-index-members --index-code 000300.SH --min-listed-days 0 --min-amount 0 --pretty`：通过，构建 3 个 sample 成员。
- `uv run python -m research_suite.run_suite --suite-name production_sync_sample_suite --provider sample --skip-data-sync --data-dir /tmp/auto-alpha-production-sync/data --universe-name csi300_sample --index-code 000300.SH --factor-store-dir /tmp/auto-alpha-production-sync/store --report-dir /tmp/auto-alpha-production-sync/reports --output-dir /tmp/auto-alpha-production-sync/suite --backtest-dir /tmp/auto-alpha-production-sync/backtest --orders-dir /tmp/auto-alpha-production-sync/orders --as-of-date 20240104 --factor-transform winsorize_zscore --search-seed 42 --search-population-size 12 --search-generations 2 --search-max-candidates 8 --top-k 5 --composite-method rank_average --promote-latest-composite --walk-forward-train-size 1 --walk-forward-test-size 1 --walk-forward-step-size 1 --pretty`：通过，suite status success，selected factor `factor_0c8dda802c9fd989` 晋级为 `production_candidate`。

### 后续待办
- 使用真实 Tushare token、权限和积分在全市场范围验证 chunked sync。
- 增加跨数据源校验、异常值修复策略和更细质量门禁。
- 对大规模 JSONL 读取、index 构建和 compaction 做性能压测。
- dashboard 增加 sync job 明细、audit 错误过滤和 snapshot 差异对比。

## 2026-06-27 - 任务 016

### 本次变更摘要
- 新增 `risk_model/`，提供股票暴露、portfolio/benchmark/active exposure、协方差、tracking error、风险约束检查和风险报告。
- 新增 `portfolio_optimizer/`，提供确定性 long-only benchmark-aware 启发式优化器和 CLI。
- 增强 backtest，支持 `--portfolio-method equal_weight|risk_aware`，risk-aware 模式写出优化结果和风险报告。
- 增强 strategy runner，支持用优化器生成目标持仓，并在 target positions 中输出 optimized / benchmark / active weights。
- 增强 research suite，支持 risk-aware backtest/orders，并把 risk report 和 optimization result 纳入 artifact catalog 与 promotion checks。
- 增强 dashboard，新增 Risk tab，读取 risk report 和 optimization result。

### 新增文件
- `risk_model/__init__.py`
- `risk_model/models.py`
- `risk_model/exposures.py`
- `risk_model/covariance.py`
- `risk_model/constraints.py`
- `risk_model/report.py`
- `portfolio_optimizer/__init__.py`
- `portfolio_optimizer/models.py`
- `portfolio_optimizer/optimizer.py`
- `portfolio_optimizer/run_optimize.py`
- `tests/test_risk_model.py`
- `tests/test_portfolio_optimizer.py`
- `tests/test_backtest_risk_aware.py`
- `tests/test_strategy_runner_risk_aware.py`
- `tests/test_risk_suite_integration.py`
- `tests/test_risk_dashboard_artifacts.py`
- `tests/test_risk_optimizer_no_old_terms.py`

### 修改文件
- `backtest/models.py`
- `backtest/io.py`
- `backtest/simulator.py`
- `backtest/run_backtest.py`
- `strategy_manager/runner.py`
- `strategy_manager/risk.py`
- `research_suite/models.py`
- `research_suite/run_suite.py`
- `research_suite/workflow.py`
- `research_suite/promotion.py`
- `dashboard/data_service.py`
- `dashboard/app.py`
- `README.md`
- `CATREADME.md`
- `FRAMEWORK_UPDATE.md`

### 删除或隔离的旧问题
- 组合构建不再只能依赖 top-N 等权和单票 max weight。
- 回测和订单生成开始具备 benchmark-aware active exposure、tracking error、行业主动暴露和集中度约束。
- production_candidate 晋级可纳入 tracking error 和 risk constraint violations。

### 新增 A 股平台能力
- `benchmark_weights_from_index_members()` 可从 `index_members` 构建指数 benchmark 权重。
- `build_security_exposures()` 输出行业、市值、波动率和 beta 暴露。
- `estimate_return_covariance()`、`portfolio_volatility()`、`tracking_error()` 提供本地协方差和风险度量。
- `PortfolioOptimizer` 支持 alpha tilt、max weight/max names、turnover shrink、tracking-error shrink 和 long-only 输出。
- `python -m portfolio_optimizer.run_optimize` 可写出 `optimized_weights.jsonl`、`optimization_result.json`、`risk_report.json` 和 `risk_report.md`。
- `python -m backtest.run_backtest --portfolio-method risk_aware` 输出 tracking error、active share、HHI、top weight、industry active 和 risk constraint violations。
- `python -m strategy_manager.runner --portfolio-method risk_aware` 可输出 benchmark/active weights 和风险摘要。
- dashboard 可展示 risk metrics、violations、optimization result 和 risk report markdown。

### 测试结果
- `uv run pytest tests/test_backtest_cli.py tests/test_backtest_portfolio_simulator.py tests/test_strategy_runner_ashare.py tests/test_research_suite_cli.py tests/test_research_suite_workflow.py tests/test_dashboard_artifacts.py tests/test_dashboard_docs_dependencies.py tests/test_risk_model.py tests/test_portfolio_optimizer.py tests/test_backtest_risk_aware.py tests/test_strategy_runner_risk_aware.py tests/test_risk_suite_integration.py tests/test_risk_dashboard_artifacts.py tests/test_risk_optimizer_no_old_terms.py tests/test_execution_strategy_no_crypto_terms.py`：通过，27 passed。
- `uv run pytest`：通过，206 passed。
- `uv run python -m research_suite.run_suite --suite-name risk_aware_sample_suite --provider sample --data-dir /tmp/auto-alpha-risk-aware/data --universe-name csi300_sample --index-code 000300.SH --factor-store-dir /tmp/auto-alpha-risk-aware/store --report-dir /tmp/auto-alpha-risk-aware/reports --output-dir /tmp/auto-alpha-risk-aware/suite --backtest-dir /tmp/auto-alpha-risk-aware/backtest --orders-dir /tmp/auto-alpha-risk-aware/orders --as-of-date 20240104 --factor-transform winsorize_zscore --search-seed 42 --search-population-size 12 --search-generations 2 --search-max-candidates 8 --top-k 5 --composite-method rank_average --portfolio-method risk_aware --risk-aversion 1.0 --turnover-penalty 0.1 --max-turnover 1.0 --max-industry-active-weight 0.50 --max-tracking-error 1.00 --promote-latest-composite --walk-forward-train-size 1 --walk-forward-test-size 1 --walk-forward-step-size 1 --pretty`：通过，suite status success，selected factor `factor_0c8dda802c9fd989` 晋级为 `production_candidate`。
- `uv run python -m portfolio_optimizer.run_optimize --data-dir /tmp/auto-alpha-risk-aware/data --factor-store-dir /tmp/auto-alpha-risk-aware/store --output-dir /tmp/auto-alpha-risk-aware/optimize --latest-approved --factor-type composite --index-code 000300.SH --as-of-date 20240104 --max-weight 0.10 --max-names 2 --risk-aversion 1.0 --turnover-penalty 0.1 --pretty`：通过，生成 optimized weights、optimization result 和 risk report。
- `uv run python -m backtest.run_backtest --data-dir /tmp/auto-alpha-risk-aware/data --factor-store-dir /tmp/auto-alpha-risk-aware/store --output-dir /tmp/auto-alpha-risk-aware/backtest_direct --latest-approved --factor-type composite --portfolio-method risk_aware --index-code 000300.SH --top-n 2 --max-weight 0.10 --risk-report-dir /tmp/auto-alpha-risk-aware/risk_reports --pretty`：通过，tracking error 为 `0.0022750863116514706`，active share 为 `0.4095430374145508`。
- `uv run python -m strategy_manager.runner --data-dir /tmp/auto-alpha-risk-aware/data --factor-store-dir /tmp/auto-alpha-risk-aware/store --output-dir /tmp/auto-alpha-risk-aware/orders_direct --latest-approved --factor-type composite --portfolio-method risk_aware --index-code 000300.SH --top-n 2 --max-weight 0.10 --portfolio-value 1000000 --pretty`：通过，生成 2 条订单，写出 risk report。

### 后续待办
- 将风险模型扩展为 Barra-like 多因子风险模型和更细行业分类。
- 增强协方差估计、风险预算、换手预算和组合优化器求解质量。
- 增加 benchmark 成分变更、权重漂移和交易约束的更真实处理。
- dashboard 增加风险暴露时间序列、优化前后组合对比和约束诊断明细。

## 2026-06-27 - 任务 017

### 本次变更摘要
- 整理并增强 `AlphaGPT`，新增 action-mask 采样入口、checkpoint 保存/加载和参数计数工具。
- 新增 `neural_search/`，支持 warm-start 监督训练、StackVM-aware action mask、policy search、checkpoint、resume 入口和训练报告。
- 增强 `formula_search.run_search`，支持 `--search-mode random|neural|hybrid`，hybrid 模式记录 neural metadata 和 checkpoint 路径。
- 增强 `research_suite.run_suite`，支持 neural/hybrid 搜索参数，并把 neural artifacts 纳入 artifact catalog。
- 增强 `model_core.engine`，新增 `--train-mode neural` 轻量神经训练入口。
- dashboard Reports tab 可读取 neural search result、training history、checkpoint 列表和 neural report。

### 新增文件
- `neural_search/__init__.py`
- `neural_search/models.py`
- `neural_search/action_mask.py`
- `neural_search/dataset.py`
- `neural_search/trainer.py`
- `neural_search/sampler.py`
- `neural_search/reward.py`
- `neural_search/report.py`
- `neural_search/run_neural_search.py`
- `tests/test_neural_search_core.py`
- `tests/test_neural_search_cli.py`
- `tests/test_formula_search_neural_modes.py`
- `tests/test_neural_search_no_old_terms.py`

### 修改文件
- `model_core/alphagpt.py`
- `model_core/engine.py`
- `formula_search/models.py`
- `formula_search/run_search.py`
- `research_suite/models.py`
- `research_suite/run_suite.py`
- `research_suite/workflow.py`
- `dashboard/data_service.py`
- `dashboard/app.py`
- `tests/test_model_core_engine_cli.py`
- `tests/test_research_suite_workflow.py`
- `tests/test_research_suite_cli.py`
- `tests/test_dashboard_artifacts.py`
- `README.md`
- `CATREADME.md`
- `FRAMEWORK_UPDATE.md`

### 删除或隔离的旧问题
- 因子搜索不再只依赖随机生成、mutation、crossover 和固定候选公式。
- AlphaGPT 训练入口不再只能作为松散模型组件存在，新增可测试的 checkpoint、采样和轻量 policy-search 路径。
- one-click suite 不再只能使用 random formula search，可选择 neural 或 hybrid search。
- neural artifacts 不再脱离 dashboard 和 artifact catalog。

### 新增 A 股平台能力
- `python -m neural_search.run_neural_search` 可执行本地神经引导公式搜索，写出 `neural_search_result.json`、`neural_training_history.jsonl`、`neural_search_report.md` 和 `checkpoints/`。
- `build_action_mask()` 根据 StackVM 栈深度约束特征、unary op、binary op 的可选动作，避免采样 stack underflow。
- `NeuralFormulaTrainer` 支持 supervised warmup、policy search step、reward baseline、entropy bonus、value loss、stable rank 监控和 checkpoint。
- `formula_search.run_search --search-mode hybrid` 可混合 neural branch 与随机/变异/交叉分支，共用 factor store 并生成 composite factor。
- `research_suite.run_suite --search-mode neural|hybrid` 可在完整 sample suite 中使用神经/混合搜索，并继续执行 risk-aware backtest、orders、walk-forward 和 promotion。
- `model_core.engine --train-mode neural` 提供 AlphaGPT 轻量训练入口，保留 fixed 模式兼容。

### 测试结果
- `uv run pytest tests/test_neural_search_core.py tests/test_neural_search_cli.py tests/test_formula_search_neural_modes.py tests/test_model_core_engine_cli.py tests/test_research_suite_workflow.py tests/test_research_suite_cli.py tests/test_dashboard_artifacts.py tests/test_neural_search_no_old_terms.py`：通过，26 passed。
- `uv run pytest`：通过，219 passed。
- `uv run python -m neural_search.run_neural_search --data-dir /tmp/auto-alpha-neural-search/data --universe-name csi300_sample --factor-store-dir /tmp/auto-alpha-neural-search/store --report-dir /tmp/auto-alpha-neural-search/reports --output-dir /tmp/auto-alpha-neural-search/neural --seed 42 --warmup-steps 2 --policy-steps 2 --batch-size 4 --samples-per-step 4 --max-formula-len 8 --max-complexity 24 --max-lookback 10 --factor-transform winsorize_zscore --enable-gate --top-k 5 --composite-method rank_average --pretty`：通过，评估 8 个 neural samples，生成 6 个 approved factors、composite factor `factor_bfac36fbb83ab735` 和 2 个 checkpoints。
- `uv run python -m formula_search.run_search --search-mode hybrid ...`：通过，评估 19 个候选，生成 hybrid `search_result.json`，包含 neural metadata 和 checkpoint path。
- `uv run python -m research_suite.run_suite --suite-name neural_suite --search-mode hybrid --portfolio-method risk_aware ...`：通过，全部 stage success，selected factor `factor_c8cb3814b84e9c10` 晋级为 `production_candidate`。
- `uv run python -m backtest.run_backtest --latest-approved --factor-type composite ...`：通过，选中 neural composite factor `factor_bfac36fbb83ab735`，生成 backtest artifacts。
- `uv run python -m strategy_manager.runner --latest-approved --factor-type composite ...`：通过，生成 target positions、orders 和 paper fills。

### 后续待办
- 扩展 AlphaGPT 离线预训练语料、奖励归因和更稳定的 policy gradient 训练。
- 增加更丰富的 action mask 约束，如复杂度预算、lookback 预算和运算符频率约束的逐步剪枝。
- 支持 GPU 大批量 neural search、checkpoint resume 的训练历史合并和搜索对比。
- 将 neural/hybrid 搜索结果接入更细粒度 dashboard 曲线和人工审核视图。

## 2026-06-27 - 任务 018

### 本次变更摘要
- 新增本地生产运营层，覆盖 proposed orders、人工审批、审批后 paper execution、纸面账户台账和运营监控。
- `strategy_manager.runner` 支持 `--propose-only` 与 `--require-approval`，可生成 pending approval batch 而不执行 paper fills。
- `operations.run_daily` 支持选择 `production_candidate`，生成审批批次，审批后执行本地 paper fills，并更新 paper account。
- `paper_account` 持久化现金、持仓、成交、快照和绩效。
- `monitoring` 生成数据新鲜度、quality、factor drift、fill quality 和 paper account 检查报告。
- dashboard 新增 Production tab，展示 production run、approvals、paper account 和 monitoring artifacts。

### 新增文件
- `approval/__init__.py`
- `approval/models.py`
- `approval/store.py`
- `approval/run_approval.py`
- `paper_account/__init__.py`
- `paper_account/models.py`
- `paper_account/ledger.py`
- `paper_account/performance.py`
- `paper_account/run_account.py`
- `operations/__init__.py`
- `operations/models.py`
- `operations/daily_runner.py`
- `operations/report.py`
- `operations/run_daily.py`
- `monitoring/__init__.py`
- `monitoring/models.py`
- `monitoring/checks.py`
- `monitoring/report.py`
- `monitoring/run_monitor.py`
- `tests/test_approval_store.py`
- `tests/test_paper_account.py`
- `tests/test_operations_daily_runner.py`
- `tests/test_strategy_approval_integration.py`
- `tests/test_monitoring_reports.py`
- `tests/test_operations_no_old_terms.py`

### 修改文件
- `strategy_manager/runner.py`
- `execution/paper_broker.py`
- `dashboard/config.py`
- `dashboard/data_service.py`
- `dashboard/app.py`
- `tests/test_dashboard_artifacts.py`
- `README.md`
- `CATREADME.md`
- `FRAMEWORK_UPDATE.md`

### 删除或隔离的旧问题
- daily production 不再只能直接生成并执行 paper orders，新增审批门禁。
- paper fills 不再只是一次性文件输出，新增持久化 paper account ledger。
- production_candidate 进入每日运行后有 production_run、approval、account 和 monitoring artifacts 可追踪。
- PaperBroker 保持本地模拟，不接真实券商接口，不读取任何密钥。

### 新增 A 股平台能力
- `python -m approval.run_approval` 支持 list/show/approve/reject/expire approval batches，并写 `approval_log.jsonl`。
- `python -m paper_account.run_account` 支持 reset/show/mark-to-market/performance，并写账户状态、持仓、现金流水、成交流水和快照。
- `python -m operations.run_daily --require-approval` 可生成 proposed orders 和 pending approval，不执行 fills。
- `python -m operations.run_daily --approval-id ... --execute-approved` 可执行已审批订单，写 paper fills，并更新 paper account。
- `python -m monitoring.run_monitor` 可写 `monitoring_report.json`、`monitoring_report.md` 和 `alerts.jsonl`。
- dashboard Production tab 可读取 production run、approval batch/log、paper account state、positions、snapshots、trade ledger、monitoring report 和 alerts。

### 测试结果
- `uv run pytest tests/test_approval_store.py tests/test_paper_account.py tests/test_operations_daily_runner.py tests/test_strategy_approval_integration.py tests/test_monitoring_reports.py tests/test_dashboard_artifacts.py tests/test_operations_no_old_terms.py tests/test_execution_paper_broker.py tests/test_strategy_runner_ashare.py`：通过，20 passed。
- `uv run pytest`：通过，228 passed。
- `uv run python -m research_suite.run_suite --suite-name production_ops_suite --provider sample --data-dir /tmp/auto-alpha-production-ops/data --universe-name csi300_sample --index-code 000300.SH --factor-store-dir /tmp/auto-alpha-production-ops/store --report-dir /tmp/auto-alpha-production-ops/reports --output-dir /tmp/auto-alpha-production-ops/suite --backtest-dir /tmp/auto-alpha-production-ops/backtest --orders-dir /tmp/auto-alpha-production-ops/orders --as-of-date 20240104 --factor-transform winsorize_zscore --search-mode hybrid --search-seed 42 --search-population-size 12 --search-generations 2 --search-max-candidates 8 --neural-warmup-steps 1 --neural-policy-steps 1 --top-k 5 --composite-method rank_average --portfolio-method risk_aware --promote-latest-composite --walk-forward-train-size 1 --walk-forward-test-size 1 --walk-forward-step-size 1 --pretty`：通过，suite status success，selected factor `factor_c8cb3814b84e9c10`。
- `uv run python -m paper_account.run_account --account-dir /tmp/auto-alpha-production-ops/account reset --initial-cash 1000000 --pretty`：通过，初始化现金 1,000,000。
- `uv run python -m operations.run_daily --require-approval ...`：通过，生成 pending approval `approval_20240104_3814b84e9c10_2026_06_27T12_55_30Z`，未执行 paper fills。
- `uv run python -m approval.run_approval --store-dir /tmp/auto-alpha-production-ops/approvals approve --approval-id ... --reviewer local_reviewer --comment approved_for_paper --pretty`：通过，approval status 更新为 approved。
- `uv run python -m operations.run_daily --approval-id ... --execute-approved ...`：通过，生成 2 条 fills，均因交易约束 rejected；paper account cash 保持 1,000,000，写出账户快照。
- `uv run python -m monitoring.run_monitor ...`：通过，data freshness 与 quality 均 OK，生成 1 条 fill_quality warning，写出 monitoring report 和 alerts。

### 后续待办
- 增加多审批人、审批有效期、审批差异比对和更完整的人工审核 UI。
- 增强 paper account 对分红、送转、交易日资产重估和持仓漂移的处理。
- 监控层增加历史趋势、SLO、通知通道和更严格的 production gate。
- 未来如接入真实券商接口，应保持审批、台账、监控和本地 paper execution 的边界清晰。

## 2026-06-27 - 任务 019

### 本次变更摘要
- 新增 `matrix_store/`，可将 governed JSONL A 股数据转换为本地 numpy 矩阵缓存。
- `AShareDataLoader` 支持显式 `use_matrix_cache=True` 时优先读取 matrix cache，默认 JSONL 路径保持不变。
- 新增 `performance_benchmark/`，输出本地数据加载、StackVM、批量研究、公式搜索和组合回测的轻量性能报告。
- 新增 `cross_source_checks/`，支持比较两个 data_dir 或 snapshot 的 dataset 一致性。
- `research_suite` 支持 `--build-matrix-cache`、`--use-matrix-cache`、`--benchmark`，并将矩阵和 benchmark artifacts 写入 catalog。
- dashboard 新增 Performance tab，展示 matrix cache、benchmark 和 cross-source artifacts。

### 新增文件
- `matrix_store/__init__.py`
- `matrix_store/models.py`
- `matrix_store/builder.py`
- `matrix_store/reader.py`
- `matrix_store/validator.py`
- `matrix_store/run_build_matrix.py`
- `performance_benchmark/__init__.py`
- `performance_benchmark/models.py`
- `performance_benchmark/timer.py`
- `performance_benchmark/runner.py`
- `performance_benchmark/report.py`
- `performance_benchmark/run_benchmark.py`
- `cross_source_checks/__init__.py`
- `cross_source_checks/models.py`
- `cross_source_checks/comparator.py`
- `cross_source_checks/report.py`
- `cross_source_checks/run_compare.py`
- `tests/test_matrix_store.py`
- `tests/test_data_loader_matrix_cache.py`
- `tests/test_performance_benchmark.py`
- `tests/test_cross_source_checks.py`
- `tests/test_research_suite_matrix_benchmark.py`
- `tests/test_dashboard_matrix_perf_artifacts.py`
- `tests/test_matrix_perf_no_old_terms.py`

### 修改文件
- `model_core/data_loader.py`
- `research_suite/models.py`
- `research_suite/run_suite.py`
- `research_suite/workflow.py`
- `dashboard/config.py`
- `dashboard/data_service.py`
- `dashboard/app.py`
- `README.md`
- `CATREADME.md`
- `FRAMEWORK_UPDATE.md`

### 删除或隔离的旧问题
- 大规模研究不再只能逐次读取 JSONL，新增矩阵缓存读取骨架。
- 全市场性能不再只停留在文档待办，新增可重复本地 benchmark artifact。
- 多数据源一致性检查不再只停留在未来计划，新增 data_dir/snapshot 比较报告骨架。
- suite artifact catalog 不再缺少矩阵缓存和性能报告索引。

### 新增 A 股平台能力
- `python -m matrix_store.run_build_matrix` 可写 `metadata.json`、`ts_codes.json`、`trade_dates.json`、`fields.json`、`<field>.npy` 和 `matrix_validation_report.json`。
- Matrix cache 覆盖价格、成交、日频估值、财务、复权、涨跌停、停牌、行业编码和指数成分矩阵。
- `python -m performance_benchmark.run_benchmark` 可写 `benchmark_result.json` 与 `benchmark_report.md`。
- `python -m cross_source_checks.run_compare` 可写 `cross_source_report.json` 与 `cross_source_report.md`，报告 record count、missing keys、numeric diff、date range diff 和 ts_code count diff。
- `research_suite.run_suite --build-matrix-cache --use-matrix-cache --benchmark` 可在完整研究套件中生成矩阵缓存、校验报告和性能报告。
- dashboard Performance tab 可读取矩阵、benchmark 和 cross-source artifacts，缺失 artifact 时保持空状态。

### 测试结果
- `uv run pytest tests/test_matrix_store.py tests/test_data_loader_matrix_cache.py tests/test_performance_benchmark.py tests/test_cross_source_checks.py tests/test_research_suite_matrix_benchmark.py tests/test_dashboard_matrix_perf_artifacts.py tests/test_matrix_perf_no_old_terms.py`：通过，11 passed。

### 后续待办
- 增加真实全市场规模的 matrix cache 构建和加载压测。
- 增加矩阵缓存增量刷新、字段版本管理和 cache invalidation 策略。
- 扩展 benchmark 指标到内存峰值、磁盘读取量和更细粒度阶段耗时。
- 扩展 cross-source checks 到更多 provider pair、容忍阈值、字段级审计和异常样本导出。

## 2026-06-27 - 任务 020

### 本次变更摘要
- 将 `risk_model/` 扩展为 Barra-like 多因子风险模型 v1，新增 style factor、industry factor、factor returns、factor covariance、specific risk、风险分解和收益归因。
- `portfolio_optimizer` 支持 `--use-factor-risk-model`，可在优化诊断中输出 style exposure、active style exposure、factor/specific risk 和风险贡献。
- `backtest.run_backtest` 支持 factor risk model 和 attribution，写出逐日 `risk_exposures.jsonl`、`risk_decomposition.jsonl`、`return_attribution.jsonl` 和 `risk_model_report.json/md`。
- `strategy_manager.runner` 与 `operations.run_daily` 透传 factor risk model 参数，并在订单/生产摘要中记录风格暴露、主动风格暴露和风险分解。
- `monitoring` 增加 style exposure drift、active risk drift、factor risk concentration 和 attribution anomaly 检查。
- dashboard Risk tab 可读取 risk model report、逐日风格暴露、风险分解和收益归因 artifacts。

### 新增文件
- `risk_model/style.py`
- `risk_model/industry.py`
- `risk_model/factor_model.py`
- `risk_model/decomposition.py`
- `risk_model/attribution.py`
- `tests/test_risk_model_barra.py`

### 修改文件
- `risk_model/__init__.py`
- `risk_model/models.py`
- `risk_model/report.py`
- `portfolio_optimizer/models.py`
- `portfolio_optimizer/optimizer.py`
- `portfolio_optimizer/run_optimize.py`
- `backtest/simulator.py`
- `backtest/run_backtest.py`
- `strategy_manager/runner.py`
- `research_suite/models.py`
- `research_suite/run_suite.py`
- `research_suite/workflow.py`
- `research_suite/promotion.py`
- `operations/daily_runner.py`
- `operations/run_daily.py`
- `monitoring/checks.py`
- `monitoring/run_monitor.py`
- `dashboard/data_service.py`
- `dashboard/app.py`
- `README.md`
- `CATREADME.md`
- `tests/test_portfolio_optimizer.py`
- `tests/test_backtest_risk_aware.py`
- `tests/test_strategy_runner_risk_aware.py`
- `tests/test_risk_suite_integration.py`
- `tests/test_operations_daily_runner.py`
- `tests/test_monitoring_reports.py`
- `tests/test_risk_dashboard_artifacts.py`
- `tests/test_risk_optimizer_no_old_terms.py`

### 删除或隔离的旧问题
- 风险层不再只停留在简单 covariance/tracking error，新增 Barra-like factor exposure 和 factor/specific risk 拆解。
- 组合优化不再只能用行业 active/tracking error 近似约束，新增 style exposure 和 active style exposure 门槛。
- 回测不再只输出组合层指标，新增逐日风险暴露、风险贡献和收益归因 artifacts。
- 运营监控不再只检查基础 risk report 和成交质量，新增风格漂移、主动风险漂移、风险集中度和归因异常检查。

### 新增 A 股平台能力
- `build_style_exposures()` 输出 size、value、momentum、volatility、liquidity、quality、growth 七类风格因子暴露。
- `build_industry_exposures()` 输出稳定行业 one-hot 暴露。
- `build_barra_like_risk_model()` 估计横截面 factor returns、factor covariance 和 specific risk。
- `portfolio_risk_decomposition()` 与 `active_risk_decomposition()` 输出 factor risk、specific risk、style/industry contribution 和 active factor exposure。
- `attribute_active_return()` 输出 factor/specific active return 与简化 allocation/selection 归因。
- `portfolio_optimizer.run_optimize --use-factor-risk-model` 写出 `risk_model_report.json/md`。
- `backtest.run_backtest --use-factor-risk-model --attribution` 写出风险暴露、风险分解和收益归因逐日文件。
- `strategy_manager.runner --use-factor-risk-model` 在 summary 中输出 style exposure、active style exposure 和 risk decomposition。
- `research_suite.run_suite --use-factor-risk-model --attribution` 将 risk model artifacts 纳入 artifact catalog 和 promotion checks。

### 测试结果
- `uv run pytest tests/test_risk_model.py tests/test_risk_model_barra.py tests/test_portfolio_optimizer.py tests/test_backtest_risk_aware.py tests/test_strategy_runner_risk_aware.py tests/test_risk_suite_integration.py tests/test_operations_daily_runner.py tests/test_monitoring_reports.py tests/test_risk_dashboard_artifacts.py tests/test_risk_optimizer_no_old_terms.py`：通过，18 passed。
- `uv run pytest`：通过，244 passed。
- `uv run python -m research_suite.run_suite --suite-name barra_risk_suite --provider sample --data-dir /tmp/auto-alpha-barra-risk/data --universe-name csi300_sample --index-code 000300.SH --factor-store-dir /tmp/auto-alpha-barra-risk/store --report-dir /tmp/auto-alpha-barra-risk/reports --output-dir /tmp/auto-alpha-barra-risk/suite --backtest-dir /tmp/auto-alpha-barra-risk/backtest --orders-dir /tmp/auto-alpha-barra-risk/orders --as-of-date 20240104 --factor-transform winsorize_zscore --search-mode hybrid --search-seed 42 --search-population-size 12 --search-generations 2 --search-max-candidates 8 --neural-warmup-steps 1 --neural-policy-steps 1 --top-k 5 --composite-method rank_average --portfolio-method risk_aware --use-factor-risk-model --risk-model-lookback 3 --risk-model-shrinkage 0.1 --attribution --max-active-style-exposure 1.0 --promote-latest-composite --walk-forward-train-size 1 --walk-forward-test-size 1 --walk-forward-step-size 1 --pretty`：通过，suite status success，selected factor `factor_c8cb3814b84e9c10` 晋级为 `production_candidate`。
- `uv run python -m portfolio_optimizer.run_optimize --data-dir /tmp/auto-alpha-barra-risk/data --factor-store-dir /tmp/auto-alpha-barra-risk/store --output-dir /tmp/auto-alpha-barra-risk/optimize --latest-approved --factor-type composite --index-code 000300.SH --as-of-date 20240104 --max-weight 0.10 --max-names 2 --risk-aversion 1.0 --use-factor-risk-model --max-active-style-exposure 1.0 --pretty`：通过，生成 optimized weights、optimization result、risk report 和 risk model report。
- `uv run python -m backtest.run_backtest --data-dir /tmp/auto-alpha-barra-risk/data --factor-store-dir /tmp/auto-alpha-barra-risk/store --output-dir /tmp/auto-alpha-barra-risk/backtest_direct --latest-approved --factor-type composite --portfolio-method risk_aware --index-code 000300.SH --top-n 2 --max-weight 0.10 --use-factor-risk-model --attribution --risk-report-dir /tmp/auto-alpha-barra-risk/risk_reports --pretty`：通过，生成 risk exposures、risk decomposition、return attribution 和 risk model report。
- `uv run python -m strategy_manager.runner --data-dir /tmp/auto-alpha-barra-risk/data --factor-store-dir /tmp/auto-alpha-barra-risk/store --output-dir /tmp/auto-alpha-barra-risk/orders_direct --latest-approved --factor-type composite --portfolio-method risk_aware --index-code 000300.SH --top-n 2 --max-weight 0.10 --portfolio-value 1000000 --use-factor-risk-model --max-active-style-exposure 1.0 --pretty`：通过，summary 输出 style exposures、active style exposures 和 risk decomposition，写出 paper fills。

### 后续待办
- 用真实全市场数据校准 Barra-like style definitions、行业层级和协方差稳健估计。
- 增加 benchmark-aware optimizer 的严格约束求解器与更细 risk budget。
- 将收益归因扩展为多期 Brinson、行业/风格分层和交易成本归因。
- 增加 dashboard 风格暴露趋势图、风险贡献趋势图和 production drift 历史看板。

## 2026-06-27 - 任务 021

### 本次变更摘要
- 新增 `capacity_model/`，基于成交额、成交量、波动和参与率估算单票/组合容量、容量得分和冲击成本。
- 新增 `execution_plan/`，支持 parent orders、child orders、bucket schedule、child fills、execution quality 和调仓计划报告。
- `backtest.run_backtest` 支持 `--capacity-aware`，capacity-aware 模式输出容量报告、执行计划、child fills 和执行质量指标。
- `strategy_manager.runner` 支持 `--capacity-aware` 和 `--execution-plan-dir`，可额外导出 parent/child orders、capacity report 和 execution plan。
- `operations.run_daily` 在 approval 阶段保存 parent/child schedule，审批后优先执行 approved child orders，并将 execution quality 写入 production summary。
- `approval` 支持可选 parent_orders、child_orders 和 capacity_summary，旧 approval records 兼容。
- `paper_account` 支持 `apply_child_fills()`，trade ledger 记录 parent_order_id、child_order_id 和 bucket。
- `monitoring` 增加 capacity warnings、execution quality、unfilled orders 和 impact cost spike 检查。
- dashboard Orders tab 展示 capacity report、execution plan、parent orders、child orders、child fills 和 execution quality。

### 新增文件
- `capacity_model/__init__.py`
- `capacity_model/models.py`
- `capacity_model/estimator.py`
- `capacity_model/impact.py`
- `capacity_model/report.py`
- `capacity_model/run_capacity.py`
- `execution_plan/__init__.py`
- `execution_plan/models.py`
- `execution_plan/scheduler.py`
- `execution_plan/simulator.py`
- `execution_plan/report.py`
- `execution_plan/run_plan.py`
- `tests/test_capacity_model.py`
- `tests/test_execution_plan.py`
- `tests/test_capacity_execution_no_old_terms.py`

### 修改文件
- `backtest/models.py`
- `backtest/simulator.py`
- `backtest/run_backtest.py`
- `execution/models.py`
- `strategy_manager/runner.py`
- `operations/daily_runner.py`
- `operations/run_daily.py`
- `approval/models.py`
- `approval/store.py`
- `paper_account/models.py`
- `paper_account/ledger.py`
- `paper_account/performance.py`
- `monitoring/checks.py`
- `monitoring/run_monitor.py`
- `dashboard/data_service.py`
- `dashboard/app.py`
- `README.md`
- `CATREADME.md`
- `FRAMEWORK_UPDATE.md`
- `tests/test_backtest_risk_aware.py`
- `tests/test_strategy_runner_risk_aware.py`
- `tests/test_operations_daily_runner.py`
- `tests/test_approval_store.py`
- `tests/test_paper_account.py`
- `tests/test_monitoring_reports.py`
- `tests/test_risk_dashboard_artifacts.py`

### 删除或隔离的旧问题
- 本地 paper execution 不再只能按整单模拟成交，新增 parent/child order schedule。
- 回测不再只记录整体成交约束，新增容量占用、冲击成本、未成交金额和执行质量指标。
- 审批批次不再只能审批扁平订单，新增 parent/child order metadata 和 capacity summary。
- 纸面账户台账不再丢失切片来源，trade ledger 记录 parent/child/bucket。

### 新增 A 股平台能力
- `estimate_security_capacity()` 与 `estimate_portfolio_capacity()` 输出 avg daily amount/volume、amount/volume participation、max trade value/shares、impact cost、capacity score 和 warnings。
- `python -m capacity_model.run_capacity` 可独立生成 `capacity_report.json/md`。
- `build_execution_schedule()` 可将 target orders 切成默认 `open/morning/afternoon/close` bucket 的 child orders。
- `simulate_child_orders()` 按停牌、涨跌停、T+1、整手、成交量参与率和成本生成 child fills。
- `python -m execution_plan.run_plan` 可从 orders 文件生成 execution plan 和 child fills。
- `backtest.run_backtest --capacity-aware` 增加 `avg_amount_participation`、`avg_volume_participation`、`estimated_impact_cost`、`realized_execution_cost`、`unfilled_order_value`、`execution_fill_rate` 和 `capacity_warning_count`。
- `strategy_manager.runner --capacity-aware` 额外写出 `parent_orders.jsonl`、`child_orders.jsonl`、`child_fills.jsonl`、`execution_quality.json` 和 execution plan report。
- `operations.run_daily --capacity-aware --require-approval` 生成待审批 child schedule；审批后 `--execute-approved` 执行 approved child orders 并更新 paper account。

### 测试结果
- `uv run pytest tests/test_capacity_model.py tests/test_execution_plan.py tests/test_backtest_risk_aware.py tests/test_strategy_runner_risk_aware.py tests/test_operations_daily_runner.py tests/test_approval_store.py tests/test_paper_account.py tests/test_monitoring_reports.py tests/test_risk_dashboard_artifacts.py tests/test_capacity_execution_no_old_terms.py`：通过，18 passed。
- `uv run python -m research_suite.run_suite --suite-name capacity_execution_suite --provider sample --data-dir /tmp/auto-alpha-capacity-execution/data --universe-name csi300_sample --index-code 000300.SH --factor-store-dir /tmp/auto-alpha-capacity-execution/store --report-dir /tmp/auto-alpha-capacity-execution/reports --output-dir /tmp/auto-alpha-capacity-execution/suite --backtest-dir /tmp/auto-alpha-capacity-execution/backtest --orders-dir /tmp/auto-alpha-capacity-execution/orders --as-of-date 20240104 --factor-transform winsorize_zscore --search-mode hybrid --search-seed 42 --search-population-size 12 --search-generations 2 --search-max-candidates 8 --neural-warmup-steps 1 --neural-policy-steps 1 --top-k 5 --composite-method rank_average --portfolio-method risk_aware --use-factor-risk-model --attribution --promote-latest-composite --walk-forward-train-size 1 --walk-forward-test-size 1 --walk-forward-step-size 1 --pretty`：通过，suite status success，selected factor `factor_c8cb3814b84e9c10` 晋级为 `production_candidate`。
- `uv run python -m backtest.run_backtest --capacity-aware ...`：通过，生成 capacity report、execution plan 和 child fills，execution fill rate 为 `0.6002587991746816`。
- `uv run python -m strategy_manager.runner --capacity-aware ...`：通过，生成 8 个 child orders，capacity warnings 为 8。
- `uv run python -m operations.run_daily --capacity-aware --require-approval ...`：通过，生成 pending approval `approval_20240104_3814b84e9c10_2026_06_27T14_26_57Z` 和 8 个 child orders。
- `uv run python -m approval.run_approval ... approve ...`：通过，approval status 更新为 approved。
- `uv run python -m operations.run_daily --approval-id ... --execute-approved --capacity-aware ...`：通过，执行 8 个 child fills，production status executed。
- `uv run python -m monitoring.run_monitor ...`：通过生成 monitoring artifacts，包含 unfilled_orders、impact_cost_spike、fill_quality 和 paper_account checks。

### 后续待办
- 用真实全市场数据校准容量模型、成交额参与率阈值和冲击成本参数。
- 增加更真实的日内成交曲线、分钟级容量、订单簿约束和交易暂停处理。
- 将 execution plan 与审批 UI 做差异比对，支持审批后订单计划冻结和版本追踪。
- 增加多日调仓计划、跨日未完成订单滚动和更完整的执行归因。

## 2026-06-27 - 任务 022

### 本次变更摘要
- 新增 `broker_adapter/`，定义本地 BrokerAdapter 协议、broker order request/record/event/fill、batch summary 和 reconciliation models。
- 新增 LocalBrokerStore，使用 JSON/JSONL 持久化 `broker_orders.jsonl`、`broker_order_state.json`、`broker_events.jsonl`、`broker_fills.jsonl` 和 `broker_batches.json`。
- 新增 broker order 状态机，支持 submit、cancel、replace、status、list、fills 和 batch reconciliation，terminal 状态禁止撤单/改单/成交。
- 新增 `SimulatedBrokerAdapter`，基于 A 股价格、成交量、停牌、涨跌停、整手和成本模型模拟 broker order 生命周期。
- 新增 `FileInstructionBrokerAdapter`，导出通用 CSV/JSONL/manifest outbox，可从 inbox 导入 status/fills；`qmt_skeleton` 仅是字段映射骨架，不声明真实券商兼容。
- `operations.run_daily` 新增 `--broker-adapter paper|simulated|file`、broker store/outbox/inbox、auto-fill、reconcile 和 price type 参数。
- `paper_account` 增加 broker fill idempotency，重复 execute-approved 不重复扣现金或重复增加持仓。
- monitoring 和 dashboard 增加 broker orders、events、fills、reconciliation、outbox manifest 和 idempotent replay 展示/检查。

### 新增文件
- `broker_adapter/__init__.py`
- `broker_adapter/models.py`
- `broker_adapter/protocol.py`
- `broker_adapter/state_machine.py`
- `broker_adapter/store.py`
- `broker_adapter/converters.py`
- `broker_adapter/simulated.py`
- `broker_adapter/file_adapter.py`
- `broker_adapter/reconciliation.py`
- `broker_adapter/report.py`
- `broker_adapter/run_broker.py`
- `tests/test_broker_adapter_store.py`
- `tests/test_broker_adapter_simulated_file.py`
- `tests/test_broker_adapter_no_old_terms.py`

### 修改文件
- `execution/models.py`
- `operations/daily_runner.py`
- `operations/run_daily.py`
- `paper_account/models.py`
- `paper_account/ledger.py`
- `monitoring/checks.py`
- `monitoring/run_monitor.py`
- `dashboard/data_service.py`
- `dashboard/app.py`
- `README.md`
- `CATREADME.md`
- `FRAMEWORK_UPDATE.md`
- `tests/test_operations_daily_runner.py`
- `tests/test_paper_account.py`
- `tests/test_monitoring_reports.py`
- `tests/test_risk_dashboard_artifacts.py`

### 删除或隔离的旧问题
- approved child orders 不再只能直接走 execution plan simulator，可显式路由到 simulated broker 或 file instruction adapter。
- 重复执行同一 approved batch 时，broker submit 和 paper account fill apply 均具备幂等保护。
- broker order 状态、事件、成交和对账不再散落在 paper fill 文件中，而是独立写入 broker artifacts。
- 文件指令导出明确为 generic schema / configurable mapping skeleton，不误导为真实 QMT 或券商柜台兼容。

### 新增 A 股平台能力
- `SimulatedBrokerAdapter`：支持 local submit、auto-fill、cancel、replace、status/list/fills 和 reconcile。
- `FileInstructionBrokerAdapter`：支持 outbox `broker_orders.csv`、`broker_orders.jsonl`、`broker_instruction_manifest.json` 和 `broker_batch_summary.json`。
- `broker_adapter.run_broker`：支持 `submit-simulated`、`export-file`、`show-batch`、`list-orders`、`list-fills`、`cancel`、`replace` 和 `reconcile`。
- `operations.run_daily --broker-adapter simulated`：approved child orders 生成 broker orders/fills/events/reconciliation，并将 broker fills 转为 paper account fills。
- `operations.run_daily --broker-adapter file`：无 inbox fills 时只导出 outbox，不更新 paper account，production status 为 `broker_exported`。
- monitoring 新增 broker reconciliation、open orders、rejected orders、idempotency 和 file outbox checks。
- dashboard Orders / Production 区域展示 broker summary、status distribution、broker fills、broker events 和 reconciliation issues。

### 测试结果
- `uv run pytest tests/test_broker_adapter_store.py tests/test_broker_adapter_simulated_file.py tests/test_broker_adapter_no_old_terms.py tests/test_operations_daily_runner.py tests/test_paper_account.py tests/test_monitoring_reports.py tests/test_risk_dashboard_artifacts.py`：通过，14 passed。
- `uv run pytest tests/test_broker_adapter*.py tests/test_operations_daily_runner.py tests/test_paper_account.py tests/test_monitoring_reports.py`：通过，13 passed。
- `uv run pytest`：通过，259 passed。
- 端到端 sample smoke：`research_suite.run_suite` 成功生成 production candidate `factor_c8cb3814b84e9c10`；`operations.run_daily --broker-adapter simulated` 成功生成 8 条 broker orders/fills；重复 execute-approved 返回 `idempotent_replay_count=8`；`broker_adapter.run_broker show-batch/reconcile/export-file` 成功；`monitoring.run_monitor` 成功读取 broker checks；`import dashboard.app` 成功。

### 后续待办
- 引入更完整的 broker order replacement 版本链和撤改单审批流程。
- 扩展 file adapter 的 schema validation、人工字段映射模板和差异审阅报告。
- 增加多日 open broker orders 滚动、过期处理和 broker/account 双向对账。
- 在真实券商接入前完成合规、权限、风控、回滚和人工确认流程设计。

## 2026-06-27 - 任务 023

### 本次变更摘要
- 新增 `data_source_validation/`，提供 provider readiness、Tushare token/network gating、权限/限流/字段/空数据/异常诊断、字段覆盖、audit summary、baseline compare 和小样本 smoke report。
- 新增 offline `FakeTushareHttpClient`，覆盖 success、permission denied、rate limited、missing fields、empty response、malformed payload 和 network error 场景；默认测试不访问真实 Tushare。
- `TushareHttpClient` 增加 `post_with_metadata` 和 response envelope，保留 `post` 兼容；新增 permission/rate/schema/network 专用异常，异常和报告不包含 token。
- smoke runner 复用现有 AShareDataManager、sync plan、cache、audit、quality、stats、snapshot 和 compaction 能力，不复制同步逻辑。
- `monitoring.run_monitor` 增加 data source smoke、provider readiness、field coverage、audit summary 和 baseline compare checks。
- dashboard Data tab 增加 data source smoke、provider probe、field coverage、audit/cache、incremental recovery、baseline diff 和 dataset contracts 摘要读取。
- README、CATREADME、`.env.example` 更新 Tushare gated smoke、token redaction、offline fake smoke、incremental recovery smoke 和 baseline compare 说明。

### 新增文件
- `data_source_validation/__init__.py`
- `data_source_validation/models.py`
- `data_source_validation/contracts.py`
- `data_source_validation/fake_tushare.py`
- `data_source_validation/probe.py`
- `data_source_validation/field_coverage.py`
- `data_source_validation/audit_summary.py`
- `data_source_validation/incremental_recovery.py`
- `data_source_validation/baseline_compare.py`
- `data_source_validation/report.py`
- `data_source_validation/smoke_runner.py`
- `data_source_validation/run_smoke.py`
- `tests/test_data_source_validation_fake.py`
- `tests/test_data_source_validation_smoke.py`
- `tests/test_data_source_validation_no_old_terms.py`

### 修改文件
- `data_pipeline/ashare/providers/tushare_client.py`
- `data_pipeline/ashare/manager.py`
- `monitoring/checks.py`
- `monitoring/run_monitor.py`
- `dashboard/config.py`
- `dashboard/data_service.py`
- `dashboard/app.py`
- `.env.example`
- `README.md`
- `CATREADME.md`
- `FRAMEWORK_UPDATE.md`
- `tests/test_tushare_client.py`
- `tests/test_monitoring_reports.py`
- `tests/test_risk_dashboard_artifacts.py`

### 新增 A 股平台能力
- `python -m data_source_validation.run_smoke --provider sample`：本地 sample smoke、quality、stats、audit、snapshot、compact 和 incremental recovery 验证。
- `python -m data_source_validation.run_smoke --provider tushare --fake-tushare-scenario success`：离线验证 Tushare 字段映射、cache/audit 和小样本同步闭环。
- `python -m data_source_validation.run_smoke --provider tushare --allow-network --require-token`：仅在显式允许网络且提供 token 时执行真实 Tushare 极小请求 smoke。
- smoke report 输出 `data_source_smoke_report.json/md`、`provider_probe.json`、`field_coverage.json`、`audit_summary.json`、`incremental_recovery_report.json`、`baseline_compare_summary.json` 和 `dataset_contracts.json`。
- baseline compare 可结构化呈现两个本地 data_dir 的 record count、missing keys、numeric diff 和 date range diff，默认不阻断 smoke。

### 测试结果
- `uv run pytest tests/test_data_source_validation*.py tests/test_tushare*.py tests/test_cross_source*.py tests/test_monitoring_reports.py tests/test_risk_dashboard_artifacts.py`：通过，30 passed。
- `uv run pytest`：通过，276 passed。
- 端到端 data source smoke：sample provider 成功写出 8 类数据、quality、stats、snapshot、audit 和 incremental recovery；fake Tushare success 成功写出 8 类数据且 cache hit 统计为 14/28；fake permission denied 生成结构化 `permission_denied` 诊断且默认退出 0；sample baseline compare 差异为 0；monitoring 成功读取 data source smoke artifacts；`import dashboard.app` 成功。

### 后续待办
- 用真实 Tushare token 和实际积分权限运行人工 gated smoke，确认不同 API 权限和限流表现。
- 扩展数据源 contract 到后续新增接口，加入更严格的字段类型和业务范围校验。
- 增加跨 provider 的真实 baseline 策略和生产阈值配置。
- 将 online smoke 结果纳入人工上线审批清单和 dashboard 生产状态页。

## 2026-06-28 - 任务 024

### 本次变更摘要
- 新增 `artifact_schema/`，提供 artifact type registry、schema versioning、JSON/JSONL validator、checksum manifest、legacy-compatible validation 和 `artifact_schema.run_validate` CLI。
- 核心近期 artifact writer 接入 schema metadata：data source smoke、capacity report、execution plan、broker report、monitoring report、research suite catalog/report、production run、approval batch 和 paper account state。
- JSON report 顶层写入 `artifact_type`、`schema_version`、`producer`、`created_at` 和 `artifact_metadata`；JSONL 默认保持业务行不变，通过 sidecar/manifest 记录 schema。
- 新增 `release_manager/`，生成 dependency/module/CLI inventory、release manifest、release gate report 和 release notes draft；支持本地 import smoke、dashboard import、schema validation、package build 和可选 pytest。
- 新增 `ci/` 本地离线 CI runner，quick 模式跑 import smoke、offline data-source smoke、schema validation 和 release dry-run。
- 新增 GitHub Actions：默认离线 `ci.yml`、手动离线 `release-smoke.yml`、手动 gated `tushare-online-smoke.yml`。
- `pyproject.toml` 切换为 hatchling 可构建包配置，wheel/sdist 仅包含 A 股平台模块，排除 tests、assets、paper、lord 和 `times.py`。
- monitoring 和 dashboard 增加 artifact schema validation、release gate、release manifest、dependency/module/CLI inventory 和 local CI report 读取展示。

### 新增文件
- `artifact_schema/`
- `release_manager/`
- `ci/`
- `.github/workflows/ci.yml`
- `.github/workflows/release-smoke.yml`
- `.github/workflows/tushare-online-smoke.yml`
- `tests/test_artifact_schema.py`
- `tests/test_release_manager.py`
- `tests/test_ci_local.py`

### 新增 A 股平台能力
- `python -m artifact_schema.run_validate`：扫描 artifact dirs / suite artifact catalog，输出 schema validation report、issues JSONL 和 checksum manifest。
- `python -m release_manager.run_release`：生成 release manifest、dependency inventory、module inventory、CLI inventory、release gate report 和 release notes draft；默认不联网。
- `python -m ci.run_local_ci --quick`：本地离线 CI smoke，与默认 GitHub CI 共享验证边界。
- `uv build`：本地生成 A 股平台 wheel/sdist。

### 后续待办
- 扩展 schema registry 到更多历史 artifacts，并逐步提高 strict validation 覆盖率。
- 为 release gate 增加更细的 artifact lineage、schema migration 和 wheel 安装 smoke。
- 在真实发布流程中补充签名、版本号策略、变更日志生成和人工审批。

## 2026-06-28 - 任务 025

### 本次变更摘要
- 新增 `formula_corpus/`，可从默认候选、seed formulas、factor store、search/batch/neural artifacts 和 suite catalog 构建可复用公式语料。
- 新增 `formula_batch_eval/`，支持共享 `AShareDataLoader`、matrix cache、eval cache、chunked StackVM 执行、transform、split metrics、gate/correlation 评估和 approved factor 注册。
- 新增 `neural_search.run_pretrain`，支持从 `formula_sequences.jsonl` 离线监督预训练 AlphaGPT，并输出训练历史、checkpoint manifest 和 latest checkpoint。
- `research.BatchFactorResearchRunner`、`formula_search.run_search` 和 `research_suite.run_suite` 支持 matrix cache、batch eval、eval cache、formula corpus 和 pretrain checkpoint。
- `performance_benchmark/` 增加公式批量评估和 AlphaGPT 预训练小样本基准。
- `artifact_schema/`、`release_manager/`、`ci/`、monitoring 和 dashboard 接入新增 corpus、batch eval、pretrain artifact。
- `pyproject.toml` 打包列表加入 `formula_corpus` 和 `formula_batch_eval`。

### 新增文件
- `formula_corpus/`
- `formula_batch_eval/`
- `neural_search/pretrain.py`
- `neural_search/run_pretrain.py`
- `tests/test_formula_corpus.py`
- `tests/test_formula_batch_eval.py`
- `tests/test_alphagpt_pretrain.py`
- `tests/test_formula_batch_eval_integration.py`
- `tests/test_research_suite_formula_pretrain_batch_eval.py`
- `tests/test_formula_pretrain_no_old_terms.py`

### 新增 A 股平台能力
- `python -m formula_corpus.run_corpus`：构建公式语料、next-token sequence、preference pairs 和 corpus stats。
- `python -m formula_batch_eval.run_batch_eval`：对公式语料或候选公式做矩阵化批量评估，并可注册通过 gate 的因子。
- `python -m neural_search.run_pretrain`：基于本地语料离线预训练 AlphaGPT，生成 checkpoint 供 neural/hybrid search 复用。
- `python -m research_suite.run_suite --build-formula-corpus --pretrain-alphagpt --use-batch-eval`：在一键套件中串联语料构建、预训练、批量评估和搜索。

### 后续待办
- 扩展真实历史公式语料来源、负样本构造和偏好学习策略。
- 将 batch eval 推进到更大规模矩阵缓存与 GPU 性能压测。
- 增强 AlphaGPT 离线预训练配置、checkpoint selection 和 warm-start policy search 稳定性。

## 2026-06-28 - 任务 026

### 本次变更摘要
- 新增 `model_registry/`，提供本地模型版本、部署、生命周期事件、状态机、lineage graph、registry report 和 `model_registry.run_registry` CLI。
- 新增 `factor_lifecycle/`，提供因子健康检查、生命周期决策、review package、model lifecycle approval、approved activation、pause/quarantine/rollback 等治理入口。
- `approval/` 增加 `approval_type=model_lifecycle` 及 model lifecycle 字段，并保持旧 order approval record 兼容。
- `research_suite.run_suite` 支持 `--register-model-version`、`--create-model-review-package` 和 `--require-model-approval`，可将 promoted composite factor 写入 model registry、生成 review package，并创建待审批 model activation batch。
- `operations.run_daily` 支持 `--use-model-registry` 和 `--require-active-model`，可从 active model deployment 选择 factor，并阻断 paused/quarantined/retired 或缺失 active model 的生产运行。
- monitoring 新增 model registry、active model status、lifecycle health、pending review、lineage completeness、rollback availability 和 paused/quarantined status checks。
- dashboard 增加 model registry report、model versions/deployments/events、factor lifecycle report、health checks、review package 和 lineage graph 本地读取展示。
- `artifact_schema/`、`release_manager/`、`ci/` 和 `pyproject.toml` 接入 `model_registry` / `factor_lifecycle` artifacts 与 package/module inventory。

### 新增文件
- `model_registry/`
- `factor_lifecycle/`
- `tests/test_model_registry.py`
- `tests/test_factor_lifecycle.py`
- `tests/test_model_lifecycle_no_old_terms.py`

### 新增 A 股平台能力
- `python -m model_registry.run_registry`：注册 factor model、查看 active model、activate/pause/quarantine/retire/rollback，并写 registry report 和 lineage graph。
- `python -m factor_lifecycle.run_lifecycle propose-activation`：评估 factor health，生成 review package，并可创建 pending `model_lifecycle` approval。
- `python -m factor_lifecycle.run_lifecycle apply-approved`：审批通过后激活 model deployment，并同步 factor store lifecycle status。
- `python -m operations.run_daily --use-model-registry --require-active-model`：生产运行只使用已激活模型，暂停/隔离/退役状态会阻断订单生成。

### 后续待办
- 扩展 lifecycle policy 到更多生产指标，例如长期漂移、真实成交质量、回撤恢复和人工复审 SLA。
- 增加更细的 model deployment environment 管理、跨环境 promotion，以及外部审批系统对接。
- 为 model registry 增加 schema migration、版本 diff 和更完整的 lineage 可视化。

## 2026-06-28 - 任务 027

### 本次变更摘要
- 新增 `point_in_time/`，提供 A 股 dataset availability contracts、security lifecycle、active security mask、PIT validation report 和 survivorship bias report。
- 新增 `leakage_audit/`，提供公式静态扫描、factor values 审计、truncation consistency、backtest leakage 和 survivorship audit。
- `data_pipeline` securities schema/provider/config 支持 `list_status`、`delist_date`、`area`、`raw_name` 以及 `--security-list-statuses L,D,P`。
- `AShareDataLoader`、`matrix_store`、`universe`、`research`、`formula_search`、`backtest`、`strategy_manager`、`operations` 和 `research_suite` 增加 opt-in `--point-in-time` / `--feature-cutoff-mode` / leakage audit 参数。
- `factor_lifecycle` health/review、monitoring、dashboard、artifact schema、release inventory、local CI 和 packaging 接入 PIT/leakage artifacts。

### 新增文件
- `point_in_time/`
- `leakage_audit/`
- `tests/test_point_in_time.py`
- `tests/test_leakage_audit.py`
- `tests/test_pit_leakage_integration.py`

### 新增 A 股平台能力
- `python -m point_in_time.run_pit validate`：生成 PIT 合同、manifest、security lifecycle、active mask 和 survivorship report。
- `python -m leakage_audit.run_audit`：执行公式、因子值、截断一致性和回测 artifact 的未来函数审计。
- `python -m research_suite.run_suite --point-in-time --run-pit-validation --run-leakage-audit`：在一键研究套件中串联 PIT 验证、泄漏审计、模型注册和生命周期 review。
- `python -m backtest.run_backtest --point-in-time --run-leakage-audit`：在回测输出中记录 active universe coverage、inactive order、signal lag 和 leakage gate。

### 后续待办
- 引入历史 ST 状态、真实暂停上市历史、指数成分公告日/生效日字段和更严格复权因子 as-of 策略。
- 将 truncation consistency 从持久化 artifact 检查升级为公式重算对比，并覆盖更多 batch/neural search 场景。
- 为生产模型审批增加更细的 PIT policy、人工复核 SLA 和跨数据源 survivorship 对照。
## Task 028 - Corporate Actions, Total Return, And Paper Account Equity

- Added `corporate_actions/` for normalized dividend/stock-distribution events, PIT-aware schedules, account applications, total-return series, adjustment-factor reconciliation, reports, and CLI actions.
- Added `corporate_actions` to A-share schema/storage/provider contracts, sample data, Tushare dividend mapping, data quality, stats, source validation, PIT contracts, and leakage audit.
- Extended `AShareDataLoader`, `matrix_store`, research/search/suite, backtest, strategy, operations, lifecycle review, monitoring, dashboard, artifact schema, release inventory, and local CI with opt-in corporate-action-aware / total-return mode.
- Added paper-account corporate action and settlement ledgers with idempotent cash-dividend and stock-distribution application.
- Default research remains `adjusted_close`; explicit total return uses `--corporate-action-aware --target-return-mode corporate_action_total_return`.

## Task 029 - Trade Settlement, Lot Cost, PnL, And NAV Reconciliation

- Added `settlement_engine/` for local paper settlement profiles, deterministic settlement events, cash/share availability, position lots, fee/tax breakdown, realized PnL, account NAV, reconciliation reports, and `settlement_engine.run_settlement`.
- Extended execution, execution plan, broker adapter, and paper account fills/ledgers with commission, stamp duty, transfer fee, slippage, market impact, other fee, and cost-breakdown fields while keeping old fill constructors compatible.
- Added settlement-aware paper account application, settlement advancement, order precheck, report export, and broker-fill idempotency so repeated approved executions do not duplicate cash, lot, or trade-ledger updates.
- Integrated settlement-aware mode into backtest, strategy runner, operations, corporate action account application, factor lifecycle review, monitoring, dashboard, artifact schema, release inventory, local CI, and packaging.
- Settlement-aware artifacts include `settlement_report.json/md`, `settlement_events.jsonl`, `cash_buckets.jsonl`, `position_lots.jsonl`, `position_availability.jsonl`, `realized_pnl.jsonl`, `account_nav.jsonl`, `account_performance_report.json`, `account_reconciliation_report.json`, and `fee_tax_report.json`.
- This remains local paper accounting only; no real broker clearing interface, tax reporting interface, default network access, or live trading path was added.

## Task 030 - Broker Statement Import, External Account Mirror, And EOD Reconciliation

- Added `broker_statement/` for local generic broker statement import, QMT-style skeleton field mapping, normalized external orders/trades/fills/positions/cash/settlements/corporate actions, source hashes, parse issues, validation reports, import reports, and synthetic statement generation for smoke tests.
- Added `reconciliation_center/` for EOD reconciliation across external statement mirrors, broker adapter orders/fills/events, paper account ledgers, settlement artifacts, and corporate-action ledgers.
- Added structured reconciliation breaks for cash, available cash, positions, available shares, fills, fees/taxes, settlements, corporate actions, NAV, stale statements, duplicate external ids, and schema parse issues.
- Added adjustment proposals plus `account_reconciliation_adjustment` approval batches. Approved adjustments apply idempotently to paper account cash/positions, write manual adjustment ledger rows, and record manual settlement events for audit.
- Extended `paper_account` with `adjustment_ledger`, idempotent manual adjustment application, `apply-adjustments`, `show-adjustments`, and `reconcile-external` CLI actions.
- Extended `operations.run_daily` with `--run-eod-reconciliation`, `--reconcile-only`, statement import, EOD reconciliation, adjustment proposal/approval creation, and approved adjustment application.
- Extended monitoring and dashboard to display statement import status, external account mirror, EOD break counts, materiality, adjustment proposal/application status, and adjustment ledger rows.
- Extended artifact schema, release/module inventory, local CI, package metadata, README, and CATREADME with broker statement and EOD reconciliation artifacts.
- This remains a local generic statement and reconciliation skeleton only. No real broker API, real broker SDK, credential handling, verified QMT file compatibility, network submission, or live trading path was added.

### New Artifacts
- `broker_statement_manifest.json`
- `broker_statement_import_report.json/md`
- `broker_statement_validation_report.json`
- `broker_statement_parse_issues.jsonl`
- `normalized_external_orders.jsonl`
- `normalized_external_trades.jsonl`
- `normalized_external_fills.jsonl`
- `normalized_external_positions.jsonl`
- `normalized_external_cash.jsonl`
- `normalized_external_settlements.jsonl`
- `normalized_external_corporate_actions.jsonl`
- `eod_reconciliation_report.json/md`
- `reconciliation_breaks.jsonl`
- `external_account_mirror.json`
- `external_cash_mirror.jsonl`
- `external_position_mirror.jsonl`
- `external_fill_mirror.jsonl`
- `external_settlement_mirror.jsonl`
- `adjustment_proposals.jsonl`
- `adjustment_proposal_batch.json`
- `adjustment_application_result.json/md`
- `adjustment_ledger.jsonl`

### Follow-Ups
- Add richer configurable broker-statement field mapping templates and manual mapping review reports.
- Extend reconciliation matching across multi-day partial fills, cancelled/replaced orders, and settlement calendars.
- Add break lifecycle ownership, aging, SLA, and persistent resolution status before any real broker onboarding.

## Task 031 - Pre-Trade Risk Limits, Kill Switch, And Execution Gate

- Added `risk_controls/` for local A-share pre-trade policy profiles, order/child/broker request evaluation, accepted/rejected/clipped order artifacts, limit usage snapshots, audit events, kill switch state, and approval-gated override records.
- Extended approval batches with `risk_control_override` approval type and optional risk control report, breach, override, kill switch, and override-expiry metadata while keeping older approval records compatible.
- Integrated opt-in `--risk-controls` into `strategy_manager.runner`, `operations.run_daily`, `backtest.run_backtest`, simulated/file broker adapters, monitoring, dashboard artifact service, artifact schema registry, release inventory, local CI, and packaging metadata.
- `operations.run_daily --block-on-kill-switch` now blocks proposal/execution locally when the risk kill switch is active unless an approved risk override or explicit local override is provided.
- Broker adapter risk controls remain local only: the simulated adapter rejects locally and the file adapter withholds outbox instructions when the kill switch is active.

### New Artifacts
- `risk_control_policy.json`
- `risk_control_policy_manifest.json`
- `risk_control_report.json/md`
- `risk_control_breaches.jsonl`
- `risk_control_decisions.jsonl`
- `risk_limit_usage.jsonl`
- `accepted_orders.jsonl`
- `rejected_orders.jsonl`
- `clipped_orders.jsonl`
- `kill_switch_state.json`
- `risk_override_request.json`
- `risk_override_records.jsonl`

### Follow-Ups
- Expand policy templates with sector, account, ADV, issuer, concentration, and intraday usage dimensions.
- Add richer override expiry/usage enforcement and ownership workflows.
- Add production-grade limit calibration, intraday state refresh, and verified broker-side pre-trade controls before any real broker onboarding.

## Task 032 - Production Data Lake, Full-Market Backfill, Data Versioning, And Research Freezes

- Added `data_backfill/` for governed A-share backfill plans, chunked jobs, staging records, resumable state, quota/readiness checks, coverage matrices, coverage gaps, and run reports.
- Added `data_lake/` for dataset fingerprints, deterministic dataset versions, local data lake registry, copy/hardlink/manifest-only research freezes, freeze hash validation, lineage graphs, and retention reports.
- Extended data-source smoke validation with optional dataset-version and research-freeze creation while keeping fake/offline smoke as the default path and redacting all token material.
- Extended matrix build, formula search, batch research, research suite, backtest, strategy runner, and operations with optional `--data-freeze-dir` / `--require-data-freeze` inputs and freeze metadata in summaries.
- Extended monitoring and dashboard data service to read backfill, dataset version, freeze validation, and data lineage artifacts.
- Extended artifact schema registry, release inventory, packaging metadata, README, CATREADME, and `.env.example` for the new production data lake and backfill modules.
- This remains a local governed-data and research-freeze layer. Real full-market backfill still requires explicit Tushare token/network access, quota checks, operational review, and full-scale performance validation.

## Task 033 - 4-GPU Research Compute Plane, Scheduling, And Large-Scale Sharding

- Added `compute_cluster/` for local CPU/CUDA probing, file-based GPU leases, JSON/JSONL job state, subprocess job runs, heartbeats, retry/resume, scheduler reports, and `compute_cluster.run_compute`.
- Added `experiment_orchestrator/` for formula-corpus sharding, experiment graph/plan/resource artifacts, compute job submission, shard merge reports, and `experiment_orchestrator.run_experiment`.
- Extended `formula_batch_eval` with deterministic shard selection, shard manifests, shard merge, GPU/device metadata hooks, and per-run `resource_usage.json`.
- Extended `neural_search.run_pretrain` with distributed/DDP metadata options, rank0 checkpoint metadata, CPU fallback reporting, and resource report output.
- Extended `research`, `formula_search`, `research_suite`, `performance_benchmark`, monitoring, dashboard, artifact schema, release inventory, local CI, package metadata, README, and CATREADME with compute scheduler and experiment orchestration artifacts.
- Default CI/test paths remain CPU/offline and skip or fallback when CUDA is unavailable. The 4-card RTX 4090 path is an optional operator smoke for per-GPU shard parallelism and AlphaGPT DDP metadata, not a default requirement.

## Task 034 - Alpha Factory, Feature Space V2, And Multi-Stage Candidate Funnel

- Added `feature_factory/` with versioned A-share feature catalogs, opt-in `ashare_features_v2`, feature manifests, coverage reports, feature value summaries, tensor artifacts, and `feature_factory.run_features`.
- Extended `model_core` so `AShareDataLoader` keeps v1 feature defaults while optionally loading a feature-set manifest/name for v2 tensors; formula hashing and batch eval metadata now carry feature-set lineage.
- Added `alpha_factory/` for campaign manifests, source-budgeted candidate generation, templates, static DSL checks, cheap proxy evaluation, optional `formula_batch_eval` full evaluation, novelty/diversity scoring, family caps, shortlist artifacts, and campaign reports.
- Extended `formula_batch_eval`, `research`, `formula_search`, and `research_suite` with feature-set and alpha-campaign metadata, plus `research_suite --run-alpha-factory` and `--use-alpha-shortlist-for-search` handoff.
- Extended monitoring, dashboard artifact service, artifact schema registry, release inventory, local CI smoke, performance benchmark, package metadata, README, CATREADME, and `.env.example` for feature/alpha factory artifacts.
- Defaults remain sample/offline/CPU. Large Alpha Factory campaigns and GPU-backed sharded evaluation are opt-in and are not required by default pytest or CI.

### New Artifacts
- `feature_set_manifest.json`
- `feature_tensor.npy`
- `feature_coverage_report.json/md`
- `feature_values_summary.json`
- `feature_tensor_build_result.json`
- `alpha_campaign_manifest.json`
- `alpha_candidates.jsonl`
- `alpha_generation_stats.json`
- `alpha_static_checks.jsonl`
- `alpha_proxy_eval.jsonl`
- `alpha_proxy_eval_report.json`
- `alpha_full_eval_summary.json`
- `alpha_scored_candidates.jsonl`
- `alpha_shortlist.jsonl`
- `alpha_rejected.jsonl`
- `alpha_diversity_report.json/md`
- `alpha_factory_report.json/md`
- `alpha_campaign_artifact_catalog.json`

### Follow-Ups
- Calibrate proxy scoring and diversity thresholds on longer real-data freezes.
- Expand feature-space v2 with validated full-market PIT and corporate-action fields.
- Run optional multi-GPU campaign stress tests outside default CI before promoting large-scale Alpha Factory workloads.

## Task 035 - Out-Of-Sample Validation, Anti-Overfit Governance, And Factor Certification

- Added `validation_lab/` for deterministic validation splits, split-level factor metrics, multiple-testing summaries, PBO/deflated IC-like overfit diagnostics, placebo/null tests, regime robustness, sensitivity checks, and stress backtest validation artifacts.
- Added `factor_certification/` for certification policies, factor scorecards, certification decisions, review packages, explicit factor-store status application, and sample/research/production policy profiles.
- Integrated validation and certification into `research_suite` so `--run-validation-lab --run-factor-certification --require-certification` executes before promotion and records validation/certification summaries in suite output, artifact catalog, model registry metadata, and lifecycle review artifacts.
- Extended `backtest.run_backtest` with validation-bundle stress report output, and extended `formula_search`, `alpha_factory`, and `formula_batch_eval` summaries with trial and selection-bias metadata.
- Extended monitoring, dashboard artifact service, artifact schema registry, release inventory, local CI, performance benchmarks, package metadata, README, and CATREADME for validation and certification artifacts.
- Defaults remain sample/offline/CPU. PBO, deflated IC-like, and multiple-testing diagnostics are local approximations for governance review and do not guarantee future returns.

### New Artifacts
- `validation_lab_report.json/md`
- `validation_splits.jsonl`
- `factor_validation_results.jsonl`
- `factor_validation_summary.json`
- `multiple_testing_report.json`
- `overfit_risk_report.json`
- `placebo_test_report.json`
- `placebo_trials.jsonl`
- `regime_validation_report.json`
- `regime_results.jsonl`
- `sensitivity_report.json`
- `sensitivity_results.jsonl`
- `robustness_surface.json`
- `stress_backtest_report.json`
- `stress_backtest_results.jsonl`
- `validation_issues.jsonl`
- `factor_certification_policy.json`
- `factor_certification_scorecard.json`
- `factor_certification_decision.json`
- `factor_certification_package.json`
- `factor_certification_report.md`
- `factor_certification_checks.jsonl`

### Follow-Ups
- Calibrate validation thresholds on longer real-data freezes and production-sized Alpha Factory campaigns.
- Replace approximate deflated IC/PBO diagnostics with richer statistical estimators once enough live research history exists.
- Add reviewer ownership, expiry, and remediation workflow for conditional certification before any automated activation policy.

## Task 036 - Portfolio Production Certification, Optimizer Policy Registry, And Portfolio Gate

- Added `portfolio_optimizer.policy` with deterministic, serializable portfolio policies and conversion back to existing `OptimizationConfig` while keeping default optimizer behavior unchanged.
- Added `portfolio_lab/` for small portfolio policy grids, scenario trials, trial metrics, robustness ranking, selected policy artifacts, and portfolio lab reports.
- Added `portfolio_certification/` for portfolio certification policies, scorecards, decisions, certified portfolio policy packages, activation requests, registry registration, and approval-gated optimizer policy activation.
- Integrated portfolio lab/certification into `research_suite`, including suite summaries, artifact catalog entries, promotion blocking when portfolio certification is required, optimizer policy registration, and activation approval creation.
- Extended `backtest`, `strategy_manager`, and `operations` with explicit portfolio policy paths, active optimizer policy lookup, certified-policy gates, and fail-closed production order generation when required.
- Extended `model_registry`, `factor_lifecycle`, `approval`, `monitoring`, `dashboard` data service, `artifact_schema`, `release_manager`, `performance_benchmark`, local CI, packaging metadata, README, and CATREADME for portfolio lab/certification artifacts and optimizer policy lifecycle.

### New Artifacts
- `portfolio_lab_report.json/md`
- `portfolio_policy_grid.json`
- `portfolio_scenarios.json`
- `portfolio_policy_trials.jsonl`
- `portfolio_trial_metrics.jsonl`
- `portfolio_robustness_report.json/md`
- `selected_portfolio_policy.json`
- `portfolio_lab_issues.jsonl`
- `portfolio_lab_artifact_catalog.json`
- `portfolio_certification_policy.json`
- `portfolio_certification_scorecard.json`
- `portfolio_certification_decision.json`
- `portfolio_certification_package.json`
- `portfolio_certification_report.md`
- `portfolio_certification_checks.jsonl`
- `certified_portfolio_policy.json`
- `portfolio_policy_activation_request.json`

### Follow-Ups
- Calibrate portfolio certification thresholds on long real-data freezes and production-scale policy grids.
- Add richer scenario matrices for slippage, liquidity droughts, settlement delays, and risk-control override workflows.
- Keep optimizer policy activation approval-gated until production review and external operational controls are mature.

## Task 037 - Production Calendar, Shadow Trading, Orchestration, And Incident Recovery

- Added `production_orchestrator/` for local production calendars, run plans, readiness gates, phase state, resume-aware reports, day packages, and `production_orchestrator.run_production`.
- Added `shadow_trading/` for a separate local shadow book, shadow orders/fills/positions/snapshots, drift and performance reports, and `shadow_trading.run_shadow`.
- Added `incident_response/` for local incident records, runbook steps, detection from production artifacts, acknowledge/resolve/suppress lifecycle, and `incident_response.run_incident`.
- Extended `operations` and `strategy_manager` with `production_run_id`, orchestrator metadata, and `shadow_only` safety mode so shadow proposals do not execute broker/file/account mutations.
- Extended monitoring, dashboard, artifact schema, release inventory, local CI, package metadata, README, CATREADME, and `.env.example` for production orchestrator, shadow trading, and incident artifacts.
- Default paths remain offline/local and do not connect to a real broker or Tushare. `shadow_only` is a fail-safe observation mode; `paper_simulated` reuses the existing approval, simulated broker, paper account, settlement, and reconciliation stack.

### New Artifacts
- `production_run_plan.json/md`
- `production_orchestrator_report.json/md`
- `production_readiness_report.json`
- `production_phase_runs.jsonl`
- `production_gate_results.jsonl`
- `production_run_events.jsonl`
- `production_runbook.json`
- `production_day_package.json`
- `shadow_run_report.json/md`
- `shadow_orders.jsonl`
- `shadow_fills.jsonl`
- `shadow_positions.jsonl`
- `shadow_account_snapshots.jsonl`
- `shadow_drift_report.json`
- `shadow_performance_report.json`
- `shadow_vs_production_comparison.json`
- `incident_report.json/md`
- `incident_records.jsonl`
- `incident_events.jsonl`
- `incident_runbook.json`

### Follow-Ups
- Add richer phase-level resume hash checks and operator assignment metadata.
- Wire production close-day to larger real-data freeze governance once production calendars and data freezes are reviewed with real Tushare data.
- Keep real broker connectivity out of scope until broker adapter, risk controls, approval, and incident procedures are reviewed in a controlled environment.

## Task 038 - Real-Data Shadow/Paper Multi-Day Production Validation, Drift Evaluation, And Live Readiness Gate

- Added `production_replay/` for multi-day local production replay over a trade-date window, including replay plans, day results, events, resume state, replay packages, and reports.
- Added `shadow_lab/` for aggregating shadow runs across replay days into performance summaries, drift summaries, day series, issues, and calibration suggestions.
- Added `live_readiness/` for policy-driven readiness scorecards and decisions using replay, shadow lab, certification, freeze, incident, monitoring, settlement, and reconciliation artifacts.
- Extended `artifact_schema`, `release_manager`, package metadata, local CI, monitoring, and dashboard artifact readers for replay, shadow lab, and live readiness artifacts.
- Replay execution remains local-only: `shadow_only` does not mutate broker/file/account state, `paper_simulated` requires local approval before paper execution, and no real broker or default network path is introduced.

### New Artifacts
- `production_replay_plan.json`
- `production_replay_report.json/md`
- `production_replay_days.jsonl`
- `production_replay_events.jsonl`
- `production_replay_state.json`
- `production_replay_package.json`
- `production_replay_artifact_catalog.json`
- `shadow_lab_report.json/md`
- `shadow_day_summaries.jsonl`
- `shadow_performance_series.jsonl`
- `shadow_drift_series.jsonl`
- `shadow_drift_summary.json`
- `shadow_calibration_suggestions.json`
- `shadow_lab_issues.jsonl`
- `live_readiness_policy.json`
- `live_readiness_scorecard.json`
- `live_readiness_decision.json`
- `live_readiness_checks.jsonl`
- `live_readiness_package.json`

### Follow-Ups
- Calibrate live readiness policies on longer frozen real-data shadow windows.
- Add richer replay drift diagnostics once multiple production days are available.
- Keep any real broker route behind the existing broker adapter, approval, risk-control, and incident gates.

## Task 039 - Broker File Dry-Run Gateway, Mapping Certification, And Operator Handoff

- Added `broker_file_gateway/` for dry-run/manual broker file outbox generation, profile-driven field mapping, checksum manifests, operator readme files, optional package zip, inbox normalization, roundtrip checks, gateway state, and gateway reports.
- Added `operator_handoff/` for local operator handoff packages, required review checklist, evidence records, local handoff approval support, and handoff reports.
- Added `broker_mapping_certification/` for dry-run mapping profile certification, deterministic sample fixtures, roundtrip-based policy decisions, scorecards, decision artifacts, packages, and reports.
- Extended `approval`, `operations`, `production_orchestrator`, `production_replay`, `live_readiness`, `monitoring`, `dashboard`, `artifact_schema`, `release_manager`, local CI, package metadata, README, CATREADME, and `.env.example` for broker file gateway and handoff artifacts.
- File outbox remains local dry-run/manual handoff only. It never submits orders, never reads broker credentials, and the QMT profile is an explicit skeleton with no real compatibility guarantee. `live_readiness` can produce `ready_for_file_outbox_dry_run`; no live-trading readiness status was added.

### New Artifacts
- `broker_file_gateway_report.json/md`
- `broker_file_batch.json`
- `broker_file_manifest.json`
- `broker_order_manifest.json`
- `broker_file_checksum_manifest.json`
- `broker_file_operator_readme.md`
- `broker_file_roundtrip_report.json/md`
- `broker_file_roundtrip_issues.jsonl`
- `broker_file_events.jsonl`
- `normalized_broker_file_ack.jsonl`
- `normalized_broker_file_status.jsonl`
- `normalized_broker_file_fills.jsonl`
- `normalized_broker_file_rejects.jsonl`
- `operator_handoff_report.json/md`
- `operator_handoff_state.json`
- `operator_handoff_events.jsonl`
- `operator_handoff_evidence.jsonl`
- `broker_mapping_certification_policy.json`
- `broker_mapping_certification_scorecard.json`
- `broker_mapping_certification_decision.json`
- `broker_mapping_certification_package.json`
- `broker_mapping_certification_report.md`
- `broker_mapping_certification_checks.jsonl`

### Follow-Ups
- Validate real broker file mappings only through human-reviewed external fixtures; current profiles are dry-run skeletons.
- Add richer operator workflow metadata such as shift owner, dual-control review evidence, and time-windowed handoff expiry.
- Keep any real broker connectivity outside default CI and behind separate credentials, risk, approval, incident, and readiness gates.

## Task 040 - Program Trading Compliance Pack, BrokerAdapter UAT, And Go/No-Go Gate

- Added `program_trading_compliance/` for local program-trading evidence packs, software/strategy/risk-control inventories, evidence records, compliance checklists, secret scans, gap reports, review packages, and CLI smoke commands.
- Added `broker_uat_lab/` for deterministic offline BrokerAdapter contract UAT with a mock adapter, sample/strict scenarios, idempotency, status-transition, cancel/replace, rejection, partial-fill, duplicate-fill, replay, kill-switch, file-outbox, settlement, and EOD checks.
- Added `go_live_gate/` for local pre-live policy profiles, scorecards, decisions, review packages, and approval creation. Decisions are limited to local review stages: `not_ready`, `insufficient_data`, `ready_for_broker_uat`, `ready_for_file_outbox_dry_run`, and `ready_for_manual_pilot_review`.
- Extended `approval` with `compliance_review`, `broker_uat_review`, and `go_live_review` batch types, including compliance, UAT, and Go/No-Go summary fields while preserving old approval records.
- Integrated compliance/UAT/gate artifacts into `production_orchestrator`, `production_replay`, `live_readiness`, `monitoring`, `dashboard`, `artifact_schema`, `release_manager`, local CI, package metadata, README, CATREADME, and `.env.example`.
- The new layer is local evidence/UAT/review infrastructure only. It does not provide legal advice, regulatory filing automation, broker authorization, real broker credentials, external network submission, or real trading capability.

### New Artifacts
- `program_trading_system_inventory.json`
- `program_trading_strategy_inventory.json`
- `program_trading_risk_control_inventory.json`
- `program_trading_compliance_pack.json/md`
- `program_trading_evidence_records.jsonl`
- `program_trading_compliance_checklist.jsonl`
- `compliance_gap_report.json/md`
- `secret_scan_report.json/md`
- `secret_scan_findings.jsonl`
- `compliance_review_package.json/md`
- `broker_uat_plan.json`
- `broker_uat_report.json/md`
- `broker_uat_scenarios.jsonl`
- `broker_uat_results.jsonl`
- `broker_adapter_capability_manifest.json`
- `broker_adapter_contract_report.json`
- `broker_uat_replay_report.json`
- `broker_uat_issues.jsonl`
- `go_live_gate_policy.json`
- `go_live_gate_scorecard.json`
- `go_live_gate_decision.json`
- `go_live_gate_report.md`
- `go_live_gate_checks.jsonl`
- `go_live_review_package.json/md`

### Follow-Ups
- Calibrate Go/No-Go policies with longer shadow/paper/file dry-run windows and real operator review feedback.
- Add richer UAT fixtures for broker file acknowledgements and external statement imports once human-reviewed samples are available.
- Keep any real broker connectivity outside this local review layer and behind separate credentials, legal, compliance, risk, approval, incident, and operations controls.

## Task 041 - Broker UAT Safe Connectivity, Read-Only Mirror, And External Access Gates

- Added `broker_connectivity/` for broker UAT connection profiles, credential references, redaction metadata, network guard checks, read-only probe sessions, mock/generic/qmt-skeleton clients, and connectivity reports. Default execution is offline `mock_readonly`; network probes require `--allow-network`, `BROKER_UAT_ALLOW_NETWORK=1`, and an approved `broker_connectivity_review` when requested.
- Added `broker_readonly_mirror/` for normalizing read-only account snapshots, cash, positions, orders, fills, and statements into local mirror artifacts plus statement-compatible normalized external files and mirror reconciliation reports.
- Extended `approval` with `broker_connectivity_review`, and added broker connectivity/read-only mirror evidence to `program_trading_compliance`, `go_live_gate`, `live_readiness`, `broker_uat_lab`, `monitoring`, `dashboard`, `artifact_schema`, `release_manager`, local CI, package metadata, README, CATREADME, and `.env.example`.
- `broker_uat_lab` can now opt into read-only connectivity, credential redaction, network guard, and mirror reconciliation scenarios without changing the default sample scenario set.
- `production_orchestrator` and `production_replay` can opt into read-only broker health phases for `mock_readonly` connectivity probes, read-only mirror snapshots, and replay-level mirror break summaries without invoking submit, cancel, or replace paths.
- Go/No-Go statuses remain limited to local review milestones: `not_ready`, `insufficient_data`, `ready_for_broker_uat`, `ready_for_file_outbox_dry_run`, and `ready_for_manual_pilot_review`.

### New Artifacts
- `broker_connectivity_profile.json`
- `broker_credential_ref_manifest.json`
- `broker_network_guard_report.json`
- `broker_connectivity_probe_report.json`
- `broker_connectivity_report.json/md`
- `broker_connectivity_sessions.jsonl`
- `broker_connectivity_events.jsonl`
- `broker_connectivity_issues.jsonl`
- `broker_readonly_snapshot.json`
- `broker_readonly_mirror_report.json/md`
- `readonly_broker_cash.jsonl`
- `readonly_broker_positions.jsonl`
- `readonly_broker_orders.jsonl`
- `readonly_broker_fills.jsonl`
- `readonly_broker_statements.jsonl`
- `readonly_mirror_reconciliation_report.json`
- `readonly_mirror_reconciliation_issues.jsonl`

### Follow-Ups
- Validate any real broker UAT profile only through human-reviewed local approval, explicit network gate, redacted credential references, and read-only endpoints.
- Add richer external fixture mapping once reviewed account/order/fill/statement samples are available.
- Keep real order submission, cancellation, replacement, regulatory filing, automatic pilot trading, and broker compatibility claims out of this layer.

## Maintenance - Formula Batch Eval Requests And Compute Scheduler Closure

- Added explicit `--requests-json` and `--requests-jsonl` inputs for `formula_batch_eval.run_batch_eval`, with strict request validation and clear CLI errors for malformed request files or mutually exclusive sources.
- Replaced Alpha Factory's synthetic compute report path with real `ComputeJobSpec` shard jobs when `use_compute_scheduler` and multi-shard batch eval are enabled. Each shard now writes to its own factor store/report/output directories, then merges via `formula_batch_eval.merge`.
- Updated `LocalComputeScheduler` to run bounded parallel CPU/GPU batches with `ThreadPoolExecutor`, respect `max_parallel_cpu_jobs` / `max_parallel_gpu_jobs`, retain pending CUDA jobs when leases are unavailable, and protect JSON/JSONL job state updates with `threading.RLock`.
- Added focused tests for explicit request JSON/JSONL input, bounded CPU scheduler execution, and Alpha Factory's real compute-scheduler batch-eval path.

### Follow-Ups
- Keep unified main factor-store registration out of parallel shard jobs until a dedicated consolidation/approval path is added.
- Extend scheduler observability later with queue wait timing and richer pending-job status, without introducing an external queue service.

## Task 042 - Real Tushare Backfill, Rate Limits, Production Data Lake, And Matrix Refresh

- Added `real_data_ops/` for gated production data operations: local env loading with token redaction, built-in sample/fake/online Tushare profiles, readiness reports, backfill orchestration, data lake version registration, research freezes, SLA checks, storage-size reports, runbooks, and optional matrix refresh.
- Added `matrix_refresh/` for incremental matrix cache governance. It compares dataset-version content hashes with matrix metadata, supports `skip_if_fresh`, `validate_only`, and `full_rebuild`, and writes refresh plans, source diffs, freshness reports, result reports, and issue JSONL artifacts.
- Added a request pacing helper under `data_pipeline/ashare/rate_limit.py` and wired Tushare backfill execution through the default 150 requests/minute limiter, cache/audit metadata, resumable state, profile metadata, dataset-specific chunk strategies, and request-budget summaries.
- Extended `data_backfill`, `data_lake`, `data_source_validation`, `monitoring`, `dashboard`, `artifact_schema`, `release_manager`, local CI, and top-level CLIs to understand real-data profiles, SLA/status artifacts, size reports, matrix refresh status, and latest validated real-data versions.
- Updated docs, package metadata, `.env.example`, `.gitignore`, and focused tests for real-data ops, production chunk plans, rate limiting, fake Tushare offline runs, and matrix refresh freshness.

### Follow-Ups
- Run the full online Tushare profile only with an explicit local token, `RUN_TUSHARE_ONLINE_BACKFILL=1`, and operator-supervised request budgeting.
- Calibrate dataset-specific chunk windows and SLA thresholds after the first real full-market backfill.
- Expand matrix refresh to field-level partial updates once production cache sizes justify more granular rebuilds.

### Online Backfill Hardening
- Tushare HTTP responses may be gzip-compressed even when using the standard-library client; the client now advertises gzip support and transparently decodes gzip payloads before JSON parsing.
- Backfill execution now uses the configured cache directory for Tushare response cache files, preserving data/cache separation for long production runs.
- Production append writes now keep per-dataset primary-key sets and append only unseen records, avoiding per-job full dataset rewrites while preserving append/resume deduplication semantics.
- Error-path audit records now retain the latest rate-limit event when provider calls fail, which keeps long-run request pacing diagnostics intact.

### Raw Backfill Fast Path
- Added `data_backfill.run_backfill --direct-append` so raw-first online backfills can append provider records directly to governed JSONL storage without writing and re-reading per-job staging payloads.
- Added `--trade-days-only` planning for high-volume daily datasets, using local `trade_calendar` records to skip weekends and market holidays while preserving deterministic job ids for completed trade-day jobs.
- Added `--financial-by-ts-code` / `--financial-ts-codes` planning and a `ts_code` config override for Tushare `fina_indicator`, allowing financial feature backfills to satisfy providers that require single-security requests.
- These options are opt-in and leave existing sample/offline/default backfill behavior unchanged.

## Task 042-C - Expanded Real Raw Data Collection Coverage

- Added `data_pipeline/ashare/dataset_registry.py` as the governed dataset registry for the expanded real-data universe. It defines local dataset names, Tushare API names, request fields, primary keys, date/availability fields, chunk strategies, recommended ts-code split behavior, expanded index codes, and `weak_pit` flags.
- Expanded raw dataset support beyond the core 9 datasets to include index basics/daily bars/daily valuation, industry classification and members, suspensions, name changes, new shares, full income/balance/cashflow statements, forecasts, express reports, disclosure calendars, audit opinions, main business segments, money flow, margin summary/detail, top lists, institution seats, block trades, holder counts/trades/top holders, pledge detail/statistics, repurchases, share unlocks, and northbound holdings.
- Tushare provider now keeps the existing typed core mappings and adds a generic dataset fetch path for registry-backed datasets. Sample and fake Tushare providers can emit deterministic offline rows for every expanded dataset.
- Storage primary keys, sync planning, production chunk strategies, quality checks, dataset statistics, data-source field coverage contracts, and point-in-time contracts now recognize all expanded datasets. Weak publication-timing datasets are marked as `weak_pit` instead of being treated as automatically PIT-safe.
- `data_backfill` and `real_data_ops` now expose `--ts-code-split-datasets`, and `real_data_ops` exposes `--direct-append`, `--trade-days-only`, `--financial-by-ts-code`, and `--financial-ts-codes` for high-throughput raw-first acquisition.
- Added real-data profiles: `core_daily`, `index_industry_status`, `financial_statements`, `flow_margin_trading`, `holder_event_risk`, and `full_research_data`, with the expanded 12-code index list required for production research data.
- Added focused offline tests and ran the fake Tushare full-research-data smoke path over all 40 datasets without network access.

### Follow-Ups
- Continue the current core online backfill to completion before launching expanded real-data groups, so request budget and API throttling stay controlled.
- Run financial statement and holder/event groups with ts-code splitting when the provider requires single-security requests.
- After raw capture, compact, validate, snapshot/freeze, and refresh matrix caches from the governed data lake; never commit real data or `.env.local`.

## Task 042-D - Running Backfill Observation, Landing QA, And Freeze Preparation

- Added `backfill_observer/`, a read-only sidecar for active governed backfill directories. It reads existing state/job/log/data artifacts and writes progress, ETA, repair-plan, postprocess-plan, and issue artifacts without interrupting or restarting the downloader.
- Added `raw_data_landing/`, a streaming raw JSONL landing QA layer for record counts, date/security coverage, duplicate primary-key estimates, parse errors, coverage matrix, and freeze-readiness decisions.
- Extended `artifact_schema`, `monitoring`, `dashboard` data service, `release_manager` inventory, local CI, and package metadata for the new observer and landing artifacts.
- Added focused offline tests for observer progress/repair/postprocess generation, missing-state resilience, landing freeze blockers, monitoring integration, dashboard data service reads, and artifact schema validation.

### New Artifacts
- `backfill_observer_report.json/md`
- `backfill_dataset_progress.jsonl`
- `backfill_eta_report.json`
- `backfill_repair_plan.json/md`
- `backfill_repair_commands.sh`
- `backfill_postprocess_plan.json/md`
- `backfill_postprocess_commands.sh`
- `backfill_observer_issues.jsonl`
- `raw_data_landing_report.json/md`
- `raw_dataset_landing_checks.jsonl`
- `raw_dataset_coverage_matrix.json`
- `raw_freeze_readiness_decision.json`
- `raw_freeze_readiness_checks.jsonl`

### Follow-Ups
- Use `backfill_observer.run_observer observe` against the real backfill root only as a read-only dashboard/reporting action while the live download continues.
- Run `raw_data_landing.run_landing report` after each major raw capture group to decide whether repair/resume is needed before compact, freeze, matrix refresh, or Alpha Factory.

## Task 042-E - Research Readiness Gates And Post-Download Planning

- Added `research_data_readiness/`, a read-only gate that combines raw landing QA, running backfill observer artifacts, repair/postprocess plans, PIT safety contracts, matrix freshness hints, and feature-family readiness into freeze/matrix/Alpha Factory/validation decisions.
- Expanded point-in-time contracts for the current extended raw dataset universe, including index, industry/status, financial statements, moneyflow/margin/trading, northbound holdings, shareholder, pledge, repurchase, and unlock datasets. Weak-PIT and unsafe missing-availability datasets are explicit in reports.
- Added feature-readiness cataloging for price/volume, liquidity, volatility, valuation, quality/growth, industry neutralization, index membership, moneyflow, margin, event-driven, shareholder, and corporate-action families without prematurely turning all expanded raw datasets into features.
- Added `post_download_orchestrator/`, a plan-only post-download sequence generator for observer refresh, landing QA, repair review, compact/validate/stats, data lake version/freeze, PIT/leakage/corporate checks, matrix refresh, schema validation, and real-data dry smoke. Execution is refused when readiness is blocked unless an operator explicitly allows incomplete diagnostics.
- Extended artifact schema, release inventory, local CI, monitoring checks, dashboard data service, README, and CATREADME for research readiness and post-download artifacts.

### New Artifacts
- `research_data_readiness_report.json/md`
- `research_dataset_readiness.jsonl`
- `feature_readiness_catalog.json/md`
- `research_readiness_decision.json`
- `research_readiness_remediations.jsonl`
- `post_download_plan.json/md`
- `post_download_steps.jsonl`
- `post_download_run_report.json/md`
- `post_download_commands.sh`

### Follow-Ups
- Keep using readiness assessment against the live full backfill only as a read-only report action until the downloader finishes.
- Run post-download `--execute` only after the real backfill has completed and repair/readiness blockers are cleared.
- Promote weak-PIT expanded datasets into Feature Factory v3 only after manual availability review and leakage tests.

## Task 042-F - Backfill Repair, Post-Download Execution State, And Freeze Candidate Gate

- Added `backfill_repair/`, an explicit repair batch layer that reads observer/backfill artifacts, builds `repair_batch_plan`, writes repair state/events/job results, supports dry-run/execute/resume, and blocks real data paths or network-style repair commands unless explicitly allowed.
- Upgraded `post_download_orchestrator/` from plan-only output to a local execute/resume state machine with step runs, events, state, final package, artifact catalog, and freeze candidate package artifacts. Mutation steps remain blocked when readiness is not green, and incomplete mode remains diagnostic-only.
- Enhanced `research_data_readiness/` decision output to distinguish raw download in progress, download complete but needing repair, raw ready for freeze, freeze/matrix/Alpha Factory readiness, and validation readiness. Decisions now expose explicit `can_create_freeze`, `can_build_matrix`, `can_run_core_alpha_factory`, `can_run_expanded_alpha_factory`, next action, and Codex task recommendations.
- Extended monitoring, dashboard artifact readers, artifact schema registry, release inventory, local CI, pyproject packaging, README, and CATREADME for repair, post-download step runs, freeze candidate packages, and final readiness decisions.

### New Artifacts
- `repair_batch_plan.json/md`
- `repair_run_report.json/md`
- `repair_job_results.jsonl`
- `repair_events.jsonl`
- `repair_run_state.json`
- `post_download_step_runs.jsonl`
- `post_download_state.json`
- `post_download_events.jsonl`
- `post_download_final_package.json`
- `post_download_artifact_catalog.json`
- `freeze_candidate_package.json/md`

### Follow-Ups
- Do not run repair `--execute` or post-download `--execute` against `/home/lijunsi/data/auto-alpha/ashare_lake` until the active downloader has finished and the repair/readiness reports are reviewed.
- After the real run is complete, run repair dry-run first, then execute only the reviewed batch, then rerun research readiness before creating the freeze candidate.

## Task 043-A - Alpha Factory Experiment Store And Shard Consolidation

- Added `alpha_experiment_store/`, a local Alpha campaign warehouse for experiment/shard registration, shard factor-store consolidation, formula-hash dedupe, leaderboard construction, validation candidate pool export, campaign comparison, and store reports.
- Enhanced `alpha_factory.run_factory` with readiness gating and optional experiment-store registration, shard consolidation, leaderboard writing, validation-pool export, cross-campaign dedupe hooks, and consolidated factor-store paths while keeping default behavior unchanged.
- Added `validation_lab` candidate-pool mode so `alpha_validation_candidate_pool.jsonl` can be validated in bounded batches and summarized into aggregate candidate-pool reports.
- Added `experiment_orchestrator` large Alpha Factory planning workflows that generate 4GPU runbooks/resource plans and block compute-job creation when research readiness is not alpha-ready.
- Extended artifact schema, release inventory, pyproject packaging, local CI smoke, monitoring checks, dashboard readers, README, and CATREADME for Alpha experiment store artifacts.

### New Artifacts
- `alpha_experiment_registry.json`
- `alpha_experiments.jsonl`
- `alpha_shards.jsonl`
- `alpha_consolidated_factors.jsonl`
- `alpha_leaderboard.jsonl`
- `alpha_experiment_store_report.json/md`
- `alpha_factor_dedup_report.json`
- `alpha_campaign_comparison_report.json`
- `alpha_validation_candidate_pool.jsonl`
- `validation_candidate_pool_report.json`
- `validation_candidate_pool_results.jsonl`
- `alpha_large_campaign_plan.json/md`
- `alpha_large_campaign_runbook.md`
- `alpha_large_campaign_commands.sh`
- `alpha_large_campaign_resource_plan.json`

### Follow-Ups
- Do not run real Alpha Factory campaigns until the active Tushare backfill, repair, freeze, and matrix refresh are complete and research readiness explicitly allows Alpha Factory.
- Use the warehouse consolidation path for large campaigns so shard-local factor stores remain isolated until merge and validation candidate selection.

## Task 044-A - Validation Campaign Store And Certification Queue

- Added `validation_campaign_store/`, a local campaign warehouse for Alpha validation candidate pools. It registers campaigns, ingests and deduplicates candidates, writes shard plans, runs `validation_lab` per shard, consolidates validation outputs, builds validation leaderboards, and exports `factor_certification_queue.jsonl`.
- Enhanced `factor_certification.run_certify` with queue-based dry-run/execution support while keeping the existing single-factor certification path unchanged.
- Added `experiment_orchestrator` validation campaign planning workflows, including `real_data_validation_campaign_large_plan`; readiness-blocked plans write runbooks/resource plans with empty `compute_jobs` and do not start validation work.
- Extended `research_suite`, `monitoring`, `dashboard`, `artifact_schema`, `release_manager`, local CI, packaging metadata, README, and CATREADME for validation campaign artifacts.
- Added offline sample/fake tests for campaign ingest, shard validation, consolidation, leaderboard, certification queue dry-run, readiness-blocked planning, monitoring, dashboard reads, and artifact schema validation.

### New Artifacts
- `validation_campaign_registry.json`
- `validation_campaigns.jsonl`
- `validation_candidates.jsonl`
- `validation_shards.jsonl`
- `validation_candidate_results.jsonl`
- `validation_leaderboard.jsonl`
- `factor_certification_queue.jsonl`
- `validation_campaign_store_report.json/md`
- `validation_candidate_dedup_report.json`
- `validation_campaign_consolidation_report.json`
- `validation_campaign_artifact_catalog.json`
- `validation_large_campaign_plan.json/md`
- `validation_large_campaign_runbook.md`
- `validation_large_campaign_commands.sh`
- `validation_large_campaign_resource_plan.json`

### Follow-Ups
- Do not run real validation campaigns until the active real Tushare backfill, repair, freeze, matrix refresh, and Alpha Factory campaign have finished and research readiness explicitly allows validation.
- Use validation campaign store as the standard bridge from large Alpha candidate pools to factor certification and portfolio lab review.

## Task 045-A - Certification And Portfolio Campaign Stores

- Added `certification_campaign_store/`, a local campaign warehouse for `factor_certification_queue.jsonl`. It registers factor certification campaigns, ingests queue items, supports dry-run/execute/resume, calls `factor_certification` per item, consolidates decisions, and writes `certified_factor_pool.jsonl` plus `certified_factor_leaderboard.jsonl`.
- Added `portfolio_campaign_store/`, a local campaign warehouse for certified factors. It ingests `certified_factor_pool.jsonl`, supports dry-run/execute/resume, runs portfolio lab and portfolio certification for bounded candidate batches, and writes `production_candidate_bundle.jsonl` plus `optimizer_policy_activation_queue.jsonl`.
- Added experiment-orchestrator planning workflows for factor certification campaigns, portfolio campaigns, production candidate bundle plans, and readiness-blocked real-data portfolio campaign plans. Blocked plans write runbooks/resource plans with empty `compute_jobs`.
- Extended `research_suite`, `monitoring`, `dashboard`, `artifact_schema`, `release_manager`, local CI, packaging metadata, README, and CATREADME for factor certification campaign and portfolio campaign artifacts.
- Added offline sample/fake tests for queue ingest, certification campaign execution, certified pool consolidation, portfolio campaign execution, production bundle creation, activation queue output, readiness-blocked planning, monitoring checks, dashboard reads, and artifact schema validation.

### New Artifacts
- `factor_certification_campaign_registry.json`
- `factor_certification_campaigns.jsonl`
- `factor_certification_items.jsonl`
- `factor_certification_campaign_report.json/md`
- `certified_factor_pool.jsonl`
- `certified_factor_leaderboard.jsonl`
- `factor_certification_campaign_artifact_catalog.json`
- `portfolio_certification_campaign_registry.json`
- `portfolio_certification_campaigns.jsonl`
- `portfolio_candidate_items.jsonl`
- `portfolio_certification_campaign_report.json/md`
- `production_candidate_bundle.jsonl`
- `production_candidate_bundle_report.json/md`
- `optimizer_policy_activation_queue.jsonl`
- `portfolio_campaign_artifact_catalog.json`
- `certification_campaign_plan.json/md`
- `portfolio_campaign_plan.json/md`
- `production_candidate_bundle_plan.json/md`
- `resource_plan.json`
- `commands.sh`
- `runbook.md`

### Follow-Ups
- Do not run real factor certification or portfolio campaigns until the active Tushare backfill, repair, freeze, matrix refresh, Alpha Factory, and validation campaigns have finished and readiness explicitly allows the next stage.
- Treat `production_candidate_bundle.jsonl` and `optimizer_policy_activation_queue.jsonl` as review inputs only; they do not activate models or trading policies without approval, model registry, factor lifecycle, and production gates.

## Task 046-A - Feature Factory V3 And Expanded PIT-Safe Feature Matrix

- Added opt-in `ashare_features_v3` catalog coverage for expanded A-share raw datasets: index/industry/status, complete financial statements, earnings events, moneyflow, margin, abnormal trading, holder structure, pledge/repurchase/unlock, and northbound holdings. v1/v2 defaults remain unchanged.
- Added `feature_factory.extended_builder` to attach v3 feature matrices to `AShareDataLoader` using PIT-style availability dates where present. Missing expanded datasets produce warnings and zero coverage instead of breaking sample/fake runs.
- Added v3 artifacts: `feature_family_readiness.json/md`, `feature_pit_alignment_report.json`, and `feature_build_warnings.jsonl`, plus feature-set metadata fields in matrix cache metadata.
- Enhanced dynamic formula vocab support across `StackVM`, Alpha Factory, Formula Batch Eval, and research batch runner so v2/v3 feature manifests can supply feature tokens without breaking legacy formulas.
- Extended Alpha Factory templates and CLI options for v3 feature families, family readiness filters, family budgets, and default weak-PIT exclusion.
- Enhanced `matrix_refresh` to detect feature-set hash drift and recommend full rebuilds when v3 feature definitions change.
- Enhanced research-data readiness, validation metadata, monitoring checks, dashboard artifact reads, artifact schema, performance benchmark, and local CI quick smoke for v3 feature readiness and PIT alignment.
- Added offline tests for v3 catalog contracts, missing/available expanded datasets, v3 Alpha Factory templates with batch eval, and matrix refresh feature hash drift.

### Follow-Ups
- Do not build production v3 feature matrices until the active real Tushare backfill, repair, freeze, and matrix refresh gates are complete.
- Treat weak-PIT expanded datasets as report/readiness inputs until manual availability review and leakage tests promote them into alpha sampling.

## Task 047-A - Feature Promotion Gate And Weak-PIT Review

- Added `feature_promotion/`, a local review layer for expanded v3 features. It builds stable promotion policies, per-feature evidence, review packages, approval-compatible decisions, allowlists, denylists, and application reports without reading real data or secrets.
- Added feature promotion metadata to Feature Factory manifests and matrix metadata. `matrix_refresh` now detects feature-promotion policy hash drift and recommends a rebuild when eligibility policy changes.
- Added Alpha Factory, formula search, research batch, formula batch eval metadata, validation metadata, and factor certification checks for feature promotion. With `--require-feature-promotion`, only allowlisted alpha-eligible features are sampled; blocked and unreviewed weak-PIT features are rejected.
- Extended approval types, monitoring checks, dashboard artifact readers, artifact schema registry, release inventory, packaging metadata, and local CI quick smoke for feature promotion artifacts.
- Added offline tests for policy/evidence generation, allowlist/denylist construction, Alpha Factory promotion-gated sampling, static blocked-feature rejection, factor certification blockers, and matrix refresh promotion drift.

### New Artifacts
- `feature_promotion_policy.json/md`
- `feature_promotion_evidence.jsonl`
- `feature_promotion_evidence_report.json/md`
- `feature_promotion_review_package.json/md`
- `feature_promotion_decisions.jsonl`
- `feature_promotion_allowlist.json`
- `feature_promotion_denylist.json`
- `feature_promotion_application_report.json/md`

### Follow-Ups
- Do not promote real weak-PIT expanded features until the active Tushare backfill, repair, freeze, and v3 feature build artifacts exist and reviewers have inspected evidence.
- Treat feature promotion as an availability/leakage control only; factor quality still requires validation, certification, portfolio certification, and production approval gates.
