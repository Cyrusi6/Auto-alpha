# Repository Guidelines
## 目标

本项目目标打造为中国A股量化因子自主研发平台，他能运用GPU资源，自己研发因子，并跑回撤，筛选出最好的因子运用到实盘，是最高优先级，旧的逻辑不需要保留，不要为了兼容旧的功能而增加适配层。任何新增代码、重构代码和文档更新，都应服务于 A 股量化因子研发平台。

## 结构说明
- src/：正式生产源码，包含data，research、validation、portfolio、execution、platform。
- tests/：按六个业务领域镜像组织测试。
- docs/：架构、数据合同、设计决策和运维文档。
- dev_tools/：仓库审计、迁移和开发辅助工具，不被生产代码依赖。
- evidence/：可提交的脱敏治理证据，不保存真实数据。

## Environment
conda:`auto-alpha`, Python `3.11`, dependencies managed by `uv`.

处理所有需求统一遵循这套规则： 
1. 先判断现有信息是否足够完全理解你的真实需求，达到95%置信度。
2. 信息充足：直接开始实现，不额外提问。 
3. 信息缺失、描述模糊：禁止直接实现，每轮只提出1个必要澄清问题，根据你的回复持续递进追问，直到信息完备后，再开始实现。
4. 实现完成后需要验收所有的需求是否实现

## Commit 

不使用Pull Request, 通过 Git commit 管理变更, 使用 git commit -m "提交信息"; git push origin main去提交 
每次改进后按照之前的结构更新到`FRAMEWORK_UPDATE.md`

完成后需要commit / push，并回复以下内容，除非明确要求：
1. 修改文件列表与摘要，按文件或目录列出变化，以及实现的功能
2. 测试命令和结果