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
