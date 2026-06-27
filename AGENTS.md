# Repository Guidelines
## 目标

当前是加密资产数据管线、因子公式生成、回测、实盘执行和看板模块。本项目正从Solana/meme token量化系统重构为中国A股量化因子自主研发平台, 是最高优先级，旧的逻辑不需要保留，不要为了兼容旧的功能而增加适配层。任何新增代码、重构代码和文档更新，都应服务于 A 股量化因子研发平台。

## 当前旧结构说明

来自旧的加密资产系统，仅作为迁移参考，不代表未来目标架构：
  - `data_pipeline/`：Birdeye/DexScreener 数据管线。
  - `model_core/`：因子工程、公式 VM、AlphaGPT 训练和回测。
  - `strategy_manager/`：加密资产实盘策略循环。
  - `execution/`：Solana RPC 和 Jupiter 交易执行。
  - `dashboard/`：Streamlit 看板。
  - `assets/`, `paper/`, `lord/`, and `times.py` are supporting images, research material, and experiments.
  - `tests/`：`test_*.py` naming

## Environment
conda:`auto-alpha`, Python `3.11`, dependencies managed by `uv`.

## 常用命令
来自旧的加密资产系统，仅作为迁移参考，
```bash
python -m data_pipeline.run_pipeline   # sync market data into the database
python -m model_core.engine            # train and save best_meme_strategy.json
python -m strategy_manager.runner      # start the live strategy loop
streamlit run dashboard/app.py         # launch the dashboard
python lord/experiment.py --mode mechanism
```

## Commit 
不使用Pull Request, 通过 Git commit 管理变更
每次改进更新到`FRAMEWORK_UPDATE.md`

