# 免费数据补采后的准入差距报告

状态：**采集进行中；Data Admission 仍为 blocked；0/11 个 base-required 数据集已准入**

本报告回答两个不同问题：

1. 本地已有值和免费公开源还能补到什么；
2. 哪些证据已经足以让正式研究消费这些值。

“下载成功”“文件存在”“值的缺失率较低”和“Data Admission 通过”不是同一件事。
截至本报告快照，免费补采已经显著增加可重放的原始证据，但还没有形成可供自主搜索
消费的 Canonical Data Freeze。当前正确终态仍是 `blocked`，不是采集失败。

裁决合同见 [Governed A-share Data Admission Contract](../DATA_ADMISSION_CONTRACT.md)，
采集实现与恢复边界见 [免费数据源签名补采与恢复](../FREE_PROVIDER_BACKFILL.md)。

## 1. 范围与安全边界

本报告只覆盖首期研究合同：

| 项目 | 锁定范围 |
| --- | --- |
| access view | research |
| 研究日期 | `2012-01-01` 至 `2019-12-31` |
| As-of Market Date | `2019-12-31` |
| universe | PIT CSI300；事件负证明覆盖截止日证券主表中的全部 A 股 |
| 首期特征 | 价格、成交量、生命周期、可交易性、规模 |
| 数据准入身份 | `dap_d785714ef1b912a20c0f19ca` |
| staging scope | `research_20120101_20191231_asof_20191231` |

所有补采只写新的 staging generation。以下权限继续固定为 `false`：

- 自动激活或放宽 Data Admission Profile；
- Alpha 搜索和 `Validation Candidate` 发布；
- 新留出集、认证；
- shadow、paper 和 live。

数据准入通过也只解除“research view 可以被 Research Contract 引用”这一层阻断，
不会自动开启搜索，更不会授权留出集、模拟盘或实盘。

## 2. 如何阅读证据状态

本报告按下列成熟度区分证据，后一级不能由前一级自签替代：

| 状态 | 含义 |
| --- | --- |
| `local_value_only` | 有历史值，但缺少逐请求回执、可信 known-at、完整血缘或重放证明 |
| `capture_succeeded` | 锁定的网络请求和原始响应已归档；只证明该物理采集成功 |
| `normalized_replayable` | 当前 parser 能只从原始字节确定性重放规范化结果 |
| `coverage_blocked` | 已投影到逻辑义务，但 exact cover、证券轴或准入回执仍有缺口 |
| `semantic_blocked` | 字节和覆盖可能完整，但 PIT 状态机、事件因果链或字段语义未证明 |
| `quarantined` | 字节可验证，但来源血缘先天不完整；后续签名不能把弱证据洗成强证据 |
| `admitted` | 独立 Admission Verifier 对精确 Profile × view × span × as-of 签发通过 verdict |

当前总结果是 `0/11 admitted`。任何表中的 `succeeded` 都仅是 capture 状态。

## 3. 可更新的采集快照

以下是 **2026-08-20 的持续采集快照**。generation、活动进度和 `current.json`
指针会随新的不可变发布而变化；它们是审计定位信息，不是稳定的产品常量，也不构成
准入声明。为避免把中间 journal 数量误写成成功，本节不把尚未发布并通过独立验证的
动态 generation 或请求数列为完成结果。长期判断应以独立 verdict 引用的精确
content hash 为准。

### 3.1 现有本地冻结和开发回放

| 证据 | 快照身份/规模 | 当前裁决 |
| --- | --- | --- |
| 旧 Source Freeze | `ashare_freeze_125cb21b9efdd7c6f0ec559b`；content hash `125cb21b9efdd7c6f0ec559b80c2fb45be8e97a6251e7456919c0b1670143c0a`；41 个声明数据集、37 个物化数据集、105,208,161 行、1,176 个 Parquet 分区 | 结构可读，但 PIT universe、状态、公司行为、复权因果和 strict matrix 血缘不足；blocked |
| Local Development Bundle | `469 × 1,945` 日期轴、10 个特征通道、2,767,930 个有效特征槽、275,471 个可用 target | 只允许固定因子 `development_replay`；不能发布正式生命周期事件 |
| 旧 `daily_basic` 值 | 5,361,311 行；`volume_ratio` 有 64,378 个空值 | 没有受治理的 provider receipt；不能因值已存在而准入 |
| 旧 adjustment-factor 值 | 5,876,096 行 | 当前聚合值；没有 revision/vintage 和公司行为因果链 |

### 3.2 已发布的免费源 staging generation

| 能力 | generation / content hash 前缀 | 物理或规范化结果 | 治理结论 |
| --- | --- | --- | --- |
| Baostock 全市场日状态 | `free_provider_backfill_96eac0be2174cdb7b3d6e379` / `96eac0be...` | 3,799 个请求；5,760,634 个 provider security-day；162,469 个 ST 正值、355,900 个停牌值；0 normalizer 冲突 | capture succeeded；尚未按正确 PIT 代码轴准入 |
| Baostock 状态 coverage-use | `free_provider_state_coverage_use_0a36c9532a79c8788c4f707a` / `0a36c953...` | 期望 5,709,826，精确观察 5,709,823；3 缺、1 多；义务域内 ST 正值 159,146、停牌值 353,696 | `blocked_gaps`；差异数字本身受错误证券身份轴影响，不能直接补四行了事 |
| Baostock security basic | `free_provider_backfill_2fb2ef3e6f204cedb23978fb` / `2fb2ef3e...` | 3,798/3,798 请求为 positive | current signed capture；只给当前聚合身份/生命周期对账，历史简称和代码不能当 PIT 事实 |
| Baostock 全市场日行情快照 | `free_provider_backfill_8b4404745328259e4ad0ba31` / `8b440474...` | 1,946/1,946 日期为 positive；6,752,512 个规范化证券日 | signed capture 完成；仍须按 PIT identity/lifecycle 轴生成 provider-neutral `daily_bars` coverage receipt |
| CSI300 指数日线 | `free_provider_backfill_37352de761cd6dbc4ec5ad23` / `37352de7...` | 1,945 个研究期开市日 | current signed raw 可重放；仍须绑定 profile-scoped coverage 和最终 bundle |
| turnover canary | `free_provider_backfill_416ab1d145ba2cfa43fbd63e` / `416ab1d1...` | 仅 `600000.SH`，1,967 行 | 证明接口几何，不证明全市场 `daily_basic` 覆盖 |
| CSI 公告 discovery | `free_provider_backfill_9f820307c20cb31b7c9c6200` / `9f820307...` | 109 个请求 | 已签名物理证据；治理 seam 更新后须用同一新身份重跑完整链 |
| CSI 公告 inventory | `free_provider_backfill_3e28a56ff1fd0091d6d72c4d` / `3e28a56f...` | 109 个请求，月叶列表 | 同上；不能跨实现身份拼接成强血缘 |
| CSI 公告 details | `free_provider_backfill_211258bf2cdb73389c37a5ea` / `211258bf...` | 1,098/1,098 positive；82 个 CSI300 候选公告 | 物理详情完整不等于已解析 PIT 调样事件；完整链须按最新治理身份重跑 |
| CSI 旧附件片 | `free_provider_backfill_88c03e4dcce007aef3b092af` / `88c03e4d...` | 439 个附件下载；163 个 blocked reference | 上游为弱 acquisition ancestry；`pit_membership_authorized=false` |
| CSI current full-range attachments | `free_provider_backfill_11c07e34fabb5c599bb2dcd1` / `11c07e34...` | 608 parent refs；439/439 positive、0 error；36,989,662 bytes；439 exchanges；blocked 为 153 无路径日期 + 14 拒绝 + 2 研究期外 | immutable publication 与独立 replay 均通过；仍缺 historical known-at/vintage 与 semantic parser；`pit_membership_authorized=false` |
| CSI current legacy-cons exact slice | `free_provider_backfill_06fd455b09738b70a465a5b6` / `06fd455b...` | 同一 608 parent refs；2/2 positive、0 error；4,889,936 bytes；2 exchanges；606 non-slice blocked refs | immutable publication 与独立 replay 均通过；blocker 与 full slice 相同；`pit_membership_authorized=false` |
| CNINFO 旧 base inventory | `free_provider_backfill_06cad6faacb5cc6033f703b4` / `06cad6fa...` | 2,459 个计划请求、66,881 个唯一公告、432 个完整月叶、0 冲突 | v1 publication；value-only，不能作为新强文档链的来源 |
| CNINFO 旧 supplemental discovery | `free_provider_backfill_212e27653d183d18eee5eccc` / `212e2765...` | 旧 756-leaf 几何形成 757 个含 org-map 请求的 published capture | 后续实测官网 page 101 回卷到 page 1；不能证明两个超限月 exact cover，保留审计但不供 current inventory/closure 使用 |
| CNINFO current supplemental discovery | `free_provider_backfill_700af7435eb7092f87e8c1e5` / `700af743...` | 759 个请求；669 positive、90 empty、0 error；绑定 758-leaf 修正版 profile 和 current `35c27d...` implementation root | signed capture 完成；只作为同身份 supplemental inventory 的强上游 |
| CNINFO current supplemental inventory | `free_provider_backfill_03a82909b1691d06525d3e3a` / `03a82909...` | 9,662 个请求；9,572 positive、90 empty、0 error；9,667 个有界 attempts；379,073,639 响应字节 | publication 和离线 validator 通过；必须与 current-root base inventory 一起进入 document closure，不能单独授权公司行为 |
| CNINFO current base discovery | `free_provider_backfill_cca3f9cd1ce4a136633d0148` / `cca3f9cd...` | 433/433 个请求；432 positive、1 empty、0 error；13,113,875 响应字节 | publication 与离线 replay 通过；current-root base inventory 已严格绑定该 generation 开始新活动 |
| CNINFO current base inventory | `free_provider_backfill_a8d27d60f2351c47efd98ae3` / `a8d27d60...` | 2,459/2,459 个请求完成并通过 current validator | 与 current supplemental `03a82909...` 共同形成 343,262 条逻辑 demand、342,516 份唯一物理文档；不单独授权公司行为 |
| CNINFO 2011 legacy 文档 | `free_provider_backfill_04dc9d99bcb15c3c9c2afb4a` / `04dc9d99...` | 8,129/8,129 positive，1,669,823,067 响应字节；独立重放根 `b9135164...e96f` | 字节与 publication signature 完整，但 `quarantined=true`、`weak_source_ancestry=true`、`governed_evidence_eligible=false` |

raw 日状态数与 coverage-use 数不同，是因为前者统计 provider 返回的全部行，后者只
统计当前义务域。两者都不能绕过 PIT 证券轴修复。

### 3.3 尚未发布的活动快照

| 当前重跑链 | 状态 | 完成条件与约束 |
| --- | --- | --- |
| Baostock `hs300-snapshots` v2 | **已暂停、未发布**：runtime contract `d6e8a7a9...`、activity `a8e90319...`；1,621 positive 日期；`2018-08-30` 六次连接失败；1,627 attempts、1,628 exchanges、41,458,942 bytes；pause `f46b57ae...` | 5/15/30/60/120 秒冷却全部执行，第六次后没有第七次；旧 v1 `464eed...` 与 v2 均不可拼接或冒充完成。后续登录已恢复，但 v3/扩大预算须重新人工批准 |
| Baostock `hs300-snapshots` v3 | **已完成并独立验证**：generation `free_provider_backfill_dcd7ba6e1337f498683a6650`；1,946/1,946 positive、0 final error；1,947 attempts、1,949 exchanges、49,770,410 bytes；583,800 normalized rows、0 conflict；replay root `58ae0278...` | publication/current implementation/raw/plan/normalizer replay 全部通过；仍为 `quarantined_reconciliation_only`，缺 publication time、历史权重、provider-origin 和 runtime-isolation，不能单独准入 |
| Baostock adjustments | **已发布、语义阻断**：`free_provider_backfill_479b720e306747e94c69a5bb`；3,798/3,798 请求，3,648 positive、150 empty、0 error；20,134 条对账记录 | 物理与 parser replay 完成，但 provider 不给历史 revision known-at；保留 `historical_adjustment_revision_timestamp_unavailable`，不得直接准入 |
| Baostock turnover | **已完成并发布**：`free_provider_backfill_7cc80948db9d22ea9839f5a7`；3,798/3,798 positive；3,799 attempts；5,760,634 条规范化记录 | 仍须 PIT alias 投影、exact-cover receipt 与 Profile 字段裁决；不能由 capture success 自动准入 |
| Baostock dividends | **已发布并通过离线全量重验**：`free_provider_backfill_adcab60f1a085b2c45614a1a`；34,182 个请求、34,184 attempts、18,939 positive、15,243 empty、0 final error；19,537 条记录 | 唯一 ENOTCONN attempt 的旧 producer 提前记了 wire count，严格 publication 正确阻断。窄修复未重新下载并要求后续成功 terminal；publication signature/raw/parser replay 均通过，但事件版本历史 blocker 仍保留 |
| CNINFO strong base chain | **已完成 discovery → inventory**：`cca3f9cd... → a8d27d60...`；base inventory 2,459/2,459 请求完成 | closure 递归重放实际 discovery parent；旧 `cb84a4b1...` partial journal 与旧 root 不参与新闭包 |
| CNINFO strong supplemental chain | **已完成 discovery → inventory 物理采集**：`700af743... → 03a82909...`；inventory 9,662/9,662 terminal、0 final error | 官网 page 101 回卷已由 758-leaf profile 修复；旧 `212e...` generation 与 `7f9d...` 中间 activity 保留不用。此链仍须与 current base 合并并完成 document closure |
| CNINFO document closure | **v3 在第 598 个 2011 请求正确暂停；v4 已于 2026-08-22 17:50:21 启动完整重跑**：v3 已有 597 positive、1,294,584,578 response bytes；`58896367.PDF` 是可读的 16 页 PaperPort PDF，旧 EOF 正则误判。v4 当前处于签名父链 preflight；full plan 根仍为 `00483b73...`，343,262 条 demand、342,516 份物理文档、合计预算 509,623,150,592 bytes | v4 只新增严格受限的 post-EOF PDF comment 语义，并绑定 2026-08-22 新授权；不拼接 v3 partial。服务 `auto-alpha-cninfo-document-year-shards-v4-20260822.service` 串行执行；2011–2019 每片最多 60,000 份、单请求最多 2 次重试、全链硬上限 512 GiB，任一新 pause 仍停止。九年完成后必须离线重放所有分片并证明每份文档唯一 disposition |

进度改变时只更新本节和最终 verdict 引用，不应改写以前 generation 的内容。历史失败、
暂停和弱血缘活动继续原样留档；跨活动拼接、改名或重签都不能把它们升级成成功。

### 3.4 跨源治理阻断

当前免费采集使用本地持久 capture key，可以证明“这台机器归档的请求/响应没有被
静默改写”，但它不是供应商签名，也不自动证明采集运行时隔离。当前仍缺：

- 人工批准且被 Data Admission Profile 引用的 Provider Acquisition Contract；
- 可独立验证的 provider-origin attestation，而不是本地自签来源声明；
- 可独立验证的 capture runtime isolation receipt；
- 从物理采集到 11 个 base-required dataset 的 profile-scoped coverage、PIT 语义和
  consumer closure。

因此 `provider_origin_attested=false`、
`capture_runtime_isolation_verified=false`、`data_admission_eligible=false`，正式
结果仍是 `0/11 admitted`。这些是准入阻断，不否定已经保存的原始字节，也不能靠
继续下载数量自动消除。

### 3.5 恢复能力限制

普通进程崩溃、主机重启或 journal torn tail 可以按同一 activity 身份自动恢复：已有
raw 会重建 terminal，传输结果不明确的 attempt 只按原合同的有界重试规则处理。当前
尚未实现 generic trusted pause authority；WAF、HTTP 403/429、非重试错误或预算耗尽
形成的 pause，即使现实中有人批准，也没有可供现有 seam 验证和消费的版本化授权
artifact，因此必须继续保持 blocked。更换 output/合同、删除目录或传入 CLI 字符串
都不是恢复授权。后续解决方案是实现 trusted resume authority，绑定原
activity/contract、pause 原因、新预算或 breaker 处置、批准主体和签名审计，再允许
受控恢复；在此之前不得把 pause 写成失败后自动续跑。

### 3.6 本轮已证实的聚焦验证

| seam | 结果 | 证明边界 |
| --- | --- | --- |
| CNINFO official backfill / document closure | official-backfill 31 项通过；current-root closure 关键子集 5 项通过 | 递归父代、demand/physical identity、复用/下载 disposition、弱血缘传播和 pause fail-closed；closure 全量仍由最终全仓测试覆盖 |
| CSI signed range capture | 新 seam 24 项通过；既有 CSI 回归 47 项通过 | full/range、strong ETag/If-Range、durable exchange sidecar、恢复、篡改和预算负向路径 |
| Baostock wire terminal | 20 项通过；相关回归组 27 项和 2 项通过 | 精确成功空 terminal slot、non-terminal 拒绝、raw/package reconciliation 和新 activity 身份 |
| Baostock `hs300-snapshots` v2 | 专项 9 项通过；official-backfill 全文件 144 项通过 | 1,946-date full replay、6 attempts/request、11,676 request cap、父 pause 精确绑定、跨崩溃冷却恢复和 exact historical allowlist |
| Baostock `hs300-snapshots` v3 | v2/v3/allowlist 专项 11 项通过 | v2 pause 强绑定、独立 v3 namespace、7 attempts/request、13,622 request cap、六档冷却和无第八次请求 |

这些是代码合同验证，不是对正在采集活动的完成声明。

## 4. 11 个 base-required 数据集的准入状态

| Base dataset | 已有值或采集证据 | 阻断原因 | 当前状态 |
| --- | --- | --- | --- |
| `securities` | 旧证券主表；3,798 个 Baostock security-basic 响应；部分官方代码变更/退市公告已定位 | 主表把当前代码和名称倒灌到历史；缺不可变 entity 与 PIT code/lifecycle interval；退市、最后交易日和 provider 状态日混为一个字段 | blocked |
| `trade_calendar` | 研究轴有 1,945 个开市日；Baostock 可免费重采 | 尚未以正确证券/日期义务和准入 receipt 绑定到新 Source Freeze；跨源 session 差异未裁决 | blocked |
| `daily_bars` | 本地 OHLCV 值丰富；Baostock raw 状态请求也包含可对账行情字段 | 旧值缺逐请求回执；新 raw 尚未作为独立 daily-bar artifact 按 PIT 轴、有效性和 consumer closure 重放 | blocked |
| `daily_basic` | 本地 5,361,311 行；turnover 有单证券 v2 canary | turnover 未完成全市场闭包；`volume_ratio`/`total_mv` 没有受治理的权威回执；字段有效性和 known mask 未冻结 | blocked |
| `daily_limits` | 本地有价格限制值 | 缺 provider receipt；未把板块、ST、IPO、规则生效日和前收盘价绑定到版本化计算或权威原值 | blocked |
| `adjustment_factors` | 本地 5,876,096 行；Baostock 20,134 条当前因子对账记录；已实现 causal vintage 派生 seam | 免费源没有历史 revision/vintage；派生链尚未取得完整 PIT 公司行为事件版本和独立 Admission Verdict | blocked |
| `index_members` | CSI 1,098 条详情、current full-range 439 个附件和 exact legacy-cons 2 个附件均有签名物理证据；本地有 2015 年后部分 delta | current 附件仍未证明 historical known-at/vintage，semantic parser 未运行；没有 2011 年末权威 300 只 seed、完整调样事件状态、每日恰好 300 只和每日权重 | blocked |
| `corporate_actions` | CNINFO 66,881 条旧 inventory；2011 的 8,129 份文档已补采但因无上游 ancestry 被 quarantine；Baostock dividend 可作对账 | base/supplemental 强血缘 inventory→document 尚未完成；PDF 未解析 proposal→approval→implementation→correction；没有经济效果与复权跳变因果链 | blocked |
| `index_daily_bars` | v2 generation 覆盖 1,945 个研究日 | capture 成功，但缺独立 coverage receipt、validity、consumer closure 和最终 Source Freeze 绑定 | blocked |
| `suspensions` | Baostock `tradestatus` 有正/负日状态；CNINFO 有停复牌公告类别 | 证券代码轴错误；没有盘中 timing 时须保守化；公告状态机、前置 seed、冲突裁决和逐义务负证明未完成 | blocked |
| `st_status_daily` | Baostock `isST` 日状态；CNINFO 特别处理/退市公告 | 证券代码轴错误；Baostock 不能区分 ST/*ST/退市风险；公告状态机与全生命周期 exact cover 未完成 | blocked |

这里没有“基本通过”一档。base-required 的 blocker 不能降级成 warning，也不能用另一个
数据集的高覆盖率抵消。

## 5. 详细能力矩阵与解决方案

| 能力 | 免费爬取能否补物理数据 | 爬完后仍缺什么 | 实际解决方案 |
| --- | --- | --- | --- |
| securities / lifecycle | 部分可以。Baostock 给当前聚合主表，巨潮/交易所给代码变更、上市、终止上市公告 | 稳定 entity ID、PIT code interval、最后交易日/退市决定日/终止生效日分离、全市场 exact cover | 建官方生命周期事件 slice；按公告 `known_at`/`effective_at` 生成不可变代码区间，再重建全部证券日义务，禁止当前代码回填历史 |
| trade calendar | 可以。Baostock/交易所日历可重采 | 签名请求、零值/异常回执、交易所 session 裁决和下游轴绑定 | 以 v2 capture 重采或从可信 raw 独立重放，构造 date-axis root，并由 coverage verifier 对 1,945 个研究日裁决 |
| daily OHLCV | 可以。Baostock 可免费形成 2012–2019 基础行情补源 | 全市场 raw closure、错误/空结果、价格/成交量 validity、公司行为和代码轴一致性 | 在 PIT identity 修复后，从签名 Baostock raw 独立生成 `daily_bars`；与旧湖做字段级冲突报告，冲突不静默覆盖 |
| turnover | 可以。当前只跑了 `600000.SH` canary | 3,798 只证券的请求闭包和 lifecycle-day validity | 按锁定证券计划完成全市场 v2 capture；按 PIT alias 投影并建立 exact-cover receipt |
| volume ratio | 没有找到能直接满足当前权威回执语义的长期免费源 | 旧字段定义、逐日原值回执、64,378 个空值裁决 | 选择其一：购买/恢复有历史回执的数据源；或人工批准新 Profile，把字段改为由过去 N 日成交量确定性计算的自研指标。后者是新字段身份，不能冒充旧 vendor `volume_ratio` |
| total market value | 免费聚合接口可给值，但没有当前合同所需的 PIT 权威链 | PIT 总股本、公司行为版本、价格时点、逐日有效性 | 优先从 PIT share-capital 事件 × 未复权收盘价确定性派生；若事件链无法 exact cover，则购买带 PIT 股本/市值的数据。派生公式变化必须产生新 Profile |
| daily limits | 可从免费行情和公开交易规则重建，也可用免费值交叉核对 | 板块/ST/IPO 特例、规则历史版本和生效日；一字板/停牌成交资格 | 实现版本化 A-share price-limit rule engine，输入只来自 admitted lifecycle/ST/pre-close；或购买可审计历史涨跌停价。两条路线都要逐日重放和冲突门禁 |
| adjustment factors | 免费源只能补当前计算结果；causal vintage 生成器已实现 | CNINFO 文档 exact cover、公告经济条款解析、稳定 event/version 身份、known-at/effective-at 和独立准入裁决 | 以巨潮/交易所公告事件版本为权威；同一事件只使用生效前已知的最后版本，事后修订不得回写历史。Baostock/旧 Tushare 仅交叉校验；派生结果在 verdict 前固定 `data_admission_eligible=false` |
| PIT CSI300 membership | 中证官网 current full-range 与 legacy-cons 物理附件已完整签名采集并独立验证 | 2011 年末 seed、全部定期/临时调整语义、发布/生效双时间、每日恰好 300 只、每日权重；当前下载不证明 historical known-at/vintage | 以已完成的 `11c07e34...` 与 `06fd455b...` 运行锁定 semantic parser，构建事件并逐开市日展开；对 known-at/vintage 做官方发布证据裁决。权重若免费档案不能补齐，则购买日权重，或人工批准只要求 PIT membership 的新 Profile |
| corporate actions | 可以抓公告和原文 | 文档 exact cover、结构化经济条款、阶段版本、补充更正、跨公告实体匹配 | 完成 CNINFO base + supplemental 强血缘链，定点补代码变更/终止上市公告；解析并构建 proposal→approval→implementation→correction 状态机，交易所公告做独立抽查/冲突裁决 |
| index daily bars | 已免费抓到 1,945 日 | admission receipt、validity 和 bundle closure | 独立重放 `51ab9f...` raw，绑定 date-axis root 与 benchmark consumer；若实现身份变化则新 generation，不改旧证据 |
| ST daily | Baostock 能补每日布尔状态；公告可补类型/变更 | ST 子类型、公告可见时点、全生命周期负证明 | 以公告状态机给出类型和 known-at，以 Baostock 日布尔值做逐日对账；冲突日保守 invalid，不用证券简称推断 |
| suspension daily | Baostock 能补整日交易状态；公告可补原因 | 盘中 timing、开始/恢复状态机、前置 seed | 公告优先；timing 不明则事件日整日不可交易，恢复次日生效；Baostock `tradestatus` 只作逐日状态/负覆盖对账 |
| name changes | 公告可以抓 | 当前简称倒灌、闭区间/开区间转换、ann_date 缺失 | 构建独立 PIT name/code event；`name_changes` 只用于身份对账，永不替代 `st_status_daily` |
| target / strict matrix | 不能靠继续下载自动得到 | 自包含 axes、raw feature arrays、target values、target mask、field validity、公式/执行时序和全血缘 | 11 个 base dataset admitted 后，构建一个自包含 `canonical_matrix/`；锁定 `t close → t+1 open → t+2 open` target values 与 mask，loader 只接受 bundle 内内容寻址文件 |

## 6. 必须先修正的 PIT 证券代码轴

当前 coverage 报告的“3 缺、1 多”不能按四个证券日直接补值。根因是旧证券主表把
当前代码投影到了 2012 年。首期轴必须至少按下列官方事件裁决：

| 实体/代码 | 正确研究期处理 | 已定位官方锚点 | 禁止做法 |
| --- | --- | --- | --- |
| `000022` → `001872` | 2018-12-26 前使用 `000022`，自该日代码变更后使用 `001872`；同一实体连续 | 巨潮公告 `1205690369`，发布/生效 2018-12-26 | 在 2012 年要求 `001872`，或把两段当两家公司 |
| `000043` → `001914` | 2019-12-16 前使用 `000043`，自该日后使用 `001914`；同一实体连续 | 巨潮公告 `1207164397`，发布/生效 2019-12-16 | 在 2012 年要求 `001914` |
| `300114` / `302132` | 2012–2019 全程使用 `300114`；`302132` 是 2025 年代码变更，超出本 research view | 2025 事件只用于证明当前主表发生了倒灌，不得进入 2012–2019 数据 | 在研究轴中用 `302132`，或为修轴打开 2025 留出数据 |
| `002190` | 独立证券，与 `300114/302132` 无关 | 历史快照同时存在两者 | 把 `002190` 错连为 `302132` 前身 |
| `600680` | 分离最后实际交易日、停牌起点、终止上市决定日、摘牌/终止生效日和 provider 状态日；2019-05-24 的 status=0 是终止生效哨兵，不是额外交易 bar | 巨潮 `1204831387`、`1204983113`、`1206282885` | 因 provider 多返回一天就扩大可交易生命周期 |

`300114→302132` 的正式事件发生在研究期外。本次修复只能使用已知事实来阻止当前代码
倒灌，不能把 2025 数据开放给候选生成器。轴修正后必须重新计算 population root、
每个 lifecycle-day obligation、state coverage、行情 join、CSI membership join 和
matrix axis hash；旧 `0a36...` 的差异数只保留为历史诊断。

## 7. 免费爬取可以和不可以关闭的边界

### 7.1 通过工程和官方公开档案可以关闭

- 交易日历、基础 OHLCV、CSI300 指数日线和 turnover 的签名物理采集；
- 巨潮/交易所的代码变更、ST、停复牌、公司行为、终止上市公告原文；
- 中证官网的公告发布日期、生效日、调入调出附件；
- 每次请求的 positive、empty、error、分页、重试和原始响应回执；
- 从上述原始字节到规范化事件、覆盖用途和下游矩阵的确定性重放。

这里“可以关闭”意味着有一条可实施路线，不表示现有活动已经完成或已准入。

### 7.2 免费接口不能直接给出，必须派生、改合同或购买

- `volume_ratio` 和 `total_mv` 的现有 vendor 字段历史权威回执；
- CSI300 每个交易日的官方历史权重；
- 2012–2019 每个时点的 adjustment-factor revision/vintage；
- ST 精细类型和停复牌盘中 timing 的完整结构化日表；
- 自包含 canonical matrix、target/validity 和消费血缘。

前三项不能用“网页今天返回了历史值”冒充当时可见的历史版本。可接受路线只有：

1. 从当时发布的官方事件按锁定算法派生，并由人工批准对应的新 Profile；或
2. 采购能给出所需 PIT/vintage、许可和逐请求证据的商业数据；或
3. 若字段不是首期研究不可缺少的能力，由人工建立新的内容寻址 Profile 将它移出
   base closure。系统本身不得因为难以取得而临时放宽。

## 8. 推荐执行顺序

1. **先修 identity/lifecycle。** 完成上述代码变更与 `600680` 官方事件 slice，生成
   stable entity + PIT code interval；所有后续 coverage 都依赖这条轴。
2. **重新生成覆盖义务。** 以新 population root 重算 3,798 只证券在半开生命周期内
   的交易日义务，不继承旧“3 缺、1 多”结论。
3. **完成 Baostock current reconciliation。** 依次完成并独立验证 `index-daily`、
   `security-basic`、`hs300-snapshots`、`adjustments`、`turnover`、`dividends`；再从签名
   raw 生成 trade calendar、daily bars、index bars 及字段有效性。旧结果只作对账。
4. **重跑官方公告强血缘链。** CNINFO base/supplemental 分别执行
   discovery→inventory；两条链都绑定 current `35c27d...` identity，supplemental 使用
   758 leaves。在 closure 层递归重放实际 discovery parents、合并 demand、去重物理文档
   并只采 residual。2011 必须沿 strong inventory 重抓；legacy derived disposition 保留
   quarantine。
5. **解析已完成的 CSI signed range 证据。** 保留旧 paused full-GET activity；current
   608/439 full profile 与 608/2 legacy-cons profile 已发布并独立验证。下一步运行锁定
   semantic parser，裁决 historical known-at/vintage，重建 2011 seed 与全部调样事件；
   在每日恰好 300 只和权重证据闭合前仍不得准入。
6. **实现三个事件状态机。** identity/lifecycle、ST/suspension、corporate actions；每个
   事件同时携带 `known_at`、`effective_at`、source document hash 和冲突裁决。
7. **解决三个非直接免费字段。** 对 `volume_ratio`、`total_mv`、daily CSI weights
   选择“批准派生定义”或“购买 PIT 数据”，并锁定合同身份。
8. **重建复权 vintage。** 只从 admitted corporate-action versions 计算，免费聚合
   factor 只作 reconciliation。
9. **构建 canonical bundle。** 冻结 axes、values、validity、target values/mask、
   execution timing、artifact hashes 和完整 consumer closure。
10. **独立验收与干净重放。** Admission Verifier 不信任 producer 的 `complete`，从
    原始回执重建所有 root；任何缺证据项都保持 blocked。

## 9. 数据层验收标准

只有同时满足以下条件，才可以说“首期数据层跑通”：

1. 人工激活的不可变 Data Admission Profile、Provider Acquisition Contract 和
   capture public-key root 均由 verdict 引用；provider origin 与采集运行时隔离均有
   独立证明；运行中没有改变字段或门槛。
2. 精确 scope 上 11/11 base-required 均为 `admitted`，没有把 blocker 降为 warning。
3. PIT security axis 通过：entity/code interval 无重叠、无当前代码倒灌，生命周期和
   退市语义分离，所有 join 使用同一 axis root。
4. 每个物理请求都有 signed start/terminal receipt 和 raw envelope；positive、empty、
   error、分页终止、重试均进入 exact request closure。
5. coverage verifier 对每个 active dataset 重算 population、义务叶和 coverage root；
   缺一只证券、一天、一页或一个合法空结果都会稳定阻断。
6. PIT verifier 证明所有事件和数值的 `known_at ≤ consumer time`；同日发布时间不明时
   采用保守可见时点，不能使用后见修订。
7. 字段 validity 独立于值；unknown/invalid 不能填成 0 并参与相关性或横截面排序。
8. CSI300 每个开市日都有发布可证明的 membership 状态且恰好 300 只；当前 Profile
   要求的 weight 也必须有证据，除非人工批准一个新 Profile。
9. 每个复权跳变和 EventLedger 公司行为都能追溯到当时可见的不可变公告版本；当前
   聚合 factor 与 dividend 表仅作交叉校验。
10. `canonical_matrix/` 自包含 strict manifest、axes、feature values/validity、target
    values/mask、公式和全部 hash；loader 不从目录外临时计算 target 或寻找原湖文件。
11. 在隔离环境中只凭被引用的 raw evidence 能生成字节一致的 normalized artifacts、
    coverage roots、matrix bundle 和 Data Scope Root；中断恢复结果与一次完成一致。
12. 篡改 raw、删除回执、混用 generation、弱 ancestry 洗白、错误代码轴和未来日期
    注入的负向测试都会 fail closed。

验收成功允许的结果仍可能是“数据层成功、后续研究零晋级”。本报告本身不满足上述
条件；它只把可爬取结果、证据成熟度和剩余解决路径明确分开。在 `11/11 admitted`
以及后续独立人工授权出现前，Alpha search、任何新 holdout、shadow、paper 和 live
都继续禁止。
