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
