# 免费数据源签名补采与恢复

## 边界

首期补采锁定为 `2012-01-01` 至 `2019-12-31`，状态类请求包含
`2011-12-01` 起的前置 seed。生命周期相交的沪深 A 股共 3,798 只，研究日历
包含 1,945 个开市日。补采只写入新的 staging generation，不覆盖已有
`data/`、Source Freeze、local development bundle 或 capability-probe evidence。

当前授权只包括免费公开数据的受限补采。以下授权始终为 `false`：

- Data Admission/Profile 自动激活；
- Alpha 搜索；
- 新留出集；
- 模拟盘、影子、实盘。

## 为什么不能直接放大 capability probe

旧 probe 明确不可用于批量补采，也没有在传输前绑定长期 capture key。现有
Data Admission verifier 又错误地把一次 provider attempt 限制为一个原子
obligation；逐日合同会因此退化为数千万次网络调用。

新路径把三层事实分开：

1. **physical capture**：精确请求、原始 HTTP/socket bytes、分页诊断和签名日志；
2. **coverage-use projection**：从一条完整 provider 响应投影到多个逐日逻辑义务；
3. **normalized generation**：只从已归档字节确定性重放，不能使用未落盘的内存结果。

本次先完成第 1 层和可重放的规范化 staging。正式 Admission 仍须实现并独立
验证第 2 层，且由人工批准 Profile/capture key/acquisition contract。

## 持久 capture key

`PersistentReceiptSigner` 使用一个仅当前用户可读写的 RSA 私钥 PEM：

- 首次创建使用 exclusive create 和原子 rename；
- 权限固定为 `0600`；
- 拒绝 symlink、非普通文件、非当前 owner、多 hard-link、宽权限；
- 加载时验证 RSA、至少 2,048 bit、私钥完整性和签名/验签自检；
- public key 与其哈希写入不可变 acquisition contract，私钥不写入 evidence。

默认私钥路径：

```text
/home/lijunsi/data/auto-alpha/ashare_lake/governance/capture_keys/
  free_domestic_backfill_20260816.pem
```

采集 CLI 只加载该既有批准密钥，不提供任意 `--capture-key` 或自动创建旁路；密钥
初始化属于单独的人工治理动作。

## 可恢复写入顺序

每个物理请求严格按以下顺序执行：

```text
签名 capture_attempt_started → fsync journal
    → 单连接 provider transport
    → fsync raw envelope
    → 签名 capture_attempt_terminal → fsync journal
```

如果在 start 与 raw 之间中断，恢复时产生 `ambiguous_transport` 终态，并仅按锁定
的只读重试合同重试。如果 raw 已落盘但 terminal 未写入，则从 durable raw wrapper
重建终态，不重新请求。journal、raw、catalog、normalized artifact 和 manifest
任何一处被改写，独立 validator 都会阻断。

v2 publication manifest 还由 capture key 签名，绑定 raw closure、journal、请求
计划、normalizer 输出树和安全常量。历史 v1 capture 可保留原始回执，但其
normalized 输出明确标为不可信；下游只能从签名 raw 重新运行当前锁定 parser。

## 供应商请求几何

### Baostock

- 单连接，默认最小间隔 1 秒；
- 客户端锁定 `baostock==0.9.3` / protocol `00.9.30`；
- 交易日历请求一次；
- 每只证券请求 `2011-12-01` 至 `2019-12-31` 的未复权日线，跨度低于 2,000
  行 page cap；
- 另按 `2011-12-30` seed 和 1,945 个研究期开市日调用
  `query_all_stock(day)`，形成 1,946 个全市场证券代码/交易状态快照；历史简称只作
  provider reconciliation，不能作为 PIT 身份，代码别名在独立裁决前不得改写行情；
- 原始 socket response 完整保存；
- 从归档字节重放 `isST`、`tradestatus`，生成 ST 正状态、保守停牌日、逐日
  provider state 和覆盖 staging。

Baostock 的 `isST` 不能区分 ST/*ST/退市风险，`tradestatus` 不提供盘中 timing；
`query_all_stock` 返回的 `code_name` 也没有历史发布时点。normalized manifest 因此
明确保留这些 blocker。匿名 session 返回 `10001001` 时，只能由 Baostock 恢复
adapter 转换为带供应商上下文的 bounded retry；采集引擎仍会再次核对
`provider=baostock`，不会把原始或转换后的同名错误码当成其他供应商可重试错误。
`ConnectionResetError` 等已审核的 socket 子类只在 Baostock reconciliation adapter
内映射到现有 `ConnectionError` 家族，并先重建 session 再消耗锁定重试次数；
具体异常码仍保留在签名证据里。该映射不扩展到 CNINFO/CSI HTTP 路径，
也不允许将协议、schema 或 parser 错误自动重试。

`security-snapshots` 同时锁定 1,945 个 open date 的内容根
`2b277e1c...55abbc6` 和 seed + open dates 的 population root
`f171e595...3b47f2`。计划、合同和 normalizer 三层都重建 1,946 请求闭包；只传一个
请求或用相同数量的错误日期都不能发布。v2 Baostock 重放直接解析真实 21-byte
protocol header、operation/参数、压缩 frame 和 `record`；SDK `parsed` 只用于对账，
不能提供数据。未压缩响应的 CRC 会重算；type `96` 的官方客户端不验证且实测 trailer
不等于标准 raw-frame CRC，因此这里只验证 declared compressed bytes、zlib 和 trailer
结构，不伪造一个不存在的 CRC 语义。

### 巨潮资讯

清单按 `category × calendar month` 分叶，每页 30 条：

- 特别处理/退市；
- 权益分派；
- 上交所监管停复牌；
- 深交所监管停复牌。

首期补充 profile 另锁定补充更正、配股、首次发行、退市整理、增发、股权变动和
风险提示。`base` 与 `supplemental` 是两个不可变 leaf profile；inventory 输入必须
精确覆盖所选 profile 的全部月份叶和唯一 org-map 请求，混入另一 profile、缺叶或
多叶都会阻断。

先抓每个叶的第一页并冻结 `totalAnnouncement`，再生成完整连续 page plan。任何月
超过 100 页必须进一步拆分，不把前端 cap 视为完整。完整 inventory 重算稳定 total、
连续页、末页 `hasMore=false`、叶内公告 ID 唯一和 ID 总数。

PDF 计划只能从通过分页验证的唯一公告 ID/URL inventory 生成。
Discovery、inventory 和 documents 之间携带递归、内容寻址的 source ancestry；
每一级都重新验证 provider、phase、scope、profile、HTTP method/URL/status、禁止
redirect 及 raw/body hash。上游 v1 或不可信 normalized 证据会永久传播
`weak_source_ancestry=true`，不能由下游 v2 签名洗白。旧的 ancestry-free inventory
不能创建任何新文档计划；仅当前已在运行的 2011 固定 request-plan hash 可按显式
legacy 规则完成和重放。
该 legacy 代次即使 publication bytes 和签名完整，CNINFO 专用治理裁决仍固定返回
`source_lineage_complete=false`、`quarantined=true`、
`governed_evidence_eligible=false`；字节完整性不能替代来源血缘。

针对已定位的两个代码变更与 `600680` 生命周期缺口，
`free_provider_cninfo_security_lifecycle` 另外锁定 5 份官方 PDF 的精确 URL、
公告 ID、主体代码和请求计划。该路径会验证 HTTP envelope、跳转、原始响应、
本地 capture key 和独立重放，但不把本地签名写成 provider 签名。在隔离采集运行时或
供应商来源证明建立前，`provider_origin_attested=false`、
`capture_runtime_isolation_verified=false` 和 `data_admission_eligible=false` 保持不变。

### 中证指数官网

列表按 `index_rebalance × calendar month` 获取 2011–2019 全部调样公告，不只用
“沪深300”标题或不完整的早年 indexCode 过滤。先冻结每月 total，再抓完整 page
chain。validator 同时核对请求页与 provider `currentPage`，防止越界页被钳回最后一页。

详情计划从完整唯一公告 inventory 生成。详情出现 HTTP 403/WAF 时必须停止该 host，
不能把 HTML 阻断页或未抓页面当作完整公告链。
每一段下游治理验证都会递归打开并重放实际的上游 immutable generation，
同时核对人工授权、批准 key、scope、profile、source binding 和原始 HTTP 封闭。
两条旧命名 `201511302cons.xls` / `201605302cons.xls` 只能通过精确 repair profile
采集；修复附件的存在性不能自动证明公布时间或 PIT 可见性。

## CLI

只看计划，不联网：

```bash
auto-alpha data free-backfill --plan-only --pretty
auto-alpha data free-cninfo-backfill --phase cninfo-discovery --plan-only --pretty
auto-alpha data free-cninfo-backfill --phase cninfo-discovery \
  --leaf-profile supplemental --plan-only --pretty
auto-alpha data free-csindex-backfill --phase csindex-discovery --plan-only --pretty
```

执行 Baostock 日状态补采：

```bash
auto-alpha data free-backfill \
  --allow-network \
  --permission-context-id human_authorization_20260816_free_domestic_missing_data_backfill_v1

uv run --with baostock==0.9.3 python -m \
  auto_alpha.data.ingestion.pipeline.ashare.free_provider_baostock_reconciliation \
  --phase security-snapshots --allow-network --pretty
```

巨潮和中证采用发现 → 完整清单 → 文档/详情三段式计划。后一段必须通过
`--input-capture` 绑定上一段的不可变 content hash。

## 仍然 fail closed 的部分

即使上述网络任务全部完成，以下内容也不能凭下载数量自动宣布完成：

- 一次物理 capture 到多个逐日 obligation 的独立 coverage-use verifier；
- 可信人工 Profile/acquisition/capture-key 激活根；
- ST 子类型与公告状态机；
- 停复牌盘中 timing 与冲突裁决；
- CSI300 2011 年末权威种子、所有临时调整的事件解析和每日历史权重；
- 巨潮 PDF 的公司行为字段解析、proposal/approval/implementation/correction 链；
- 从 PIT 公司行为重建 adjustment-factor vintage；
- 免费三源无法直接补齐的 `daily_basic.volume_ratio/total_mv` 权威回执；
- canonical matrix、target/validity、完整 lineage 和确定性 Source Freeze replay。

任一项证据缺失时，正式研究搜索继续保持 blocked；development replay 不会因此被
改写为正式生命周期证据。

## 2026-08-16 实际补采证据

所有路径均位于：

```text
/home/lijunsi/data/auto-alpha/ashare_lake/staging/data_admission/
  dap_d785714ef1b912a20c0f19ca/
    research_20120101_20191231_asof_20191231/
```

已完成且经当前独立 validator 重验 raw closure 的 generation；下列早期 generation
为 v1，normalized 只能经当前 parser 独立重放后消费：

| 证据 | generation | 结果 |
| --- | --- | --- |
| CNINFO 2011 首屏种子 | `free_provider_backfill_c6feb8fac1876ee6c1436e15` | 49/49 请求、1,231 条首屏公告、0 冲突 |
| CNINFO 2012–2019 首屏发现 | `free_provider_backfill_e5579a460bdb983a17de505c` | 385/385 请求；一项结构化空月；首屏 8,151 条 |
| CSI 2011–2019 完整公告清单 | `free_provider_backfill_04baffbbc92ca2769d9674e5` | 109/109 请求、1,098 个唯一公告、108 个月叶、0 冲突 |
| Baostock 单证券状态 canary | `free_provider_backfill_203794bf314341ca8a49ee25` | `600000.SH` 的 1,945 个研究证券日及前置 seed exact cover |
| CNINFO 严格文档 canary | `free_provider_backfill_cf8798aa0058cedbda61e13e` | PDF、HTML、JavaScript 各一份，3/3 通过严格格式、长度、结构和身份检查 |

CSI 首次完整分页活动正确地因跨页重复 ID 阻断。签名探测
`free_provider_backfill_01ca0f72c9d9d112b8a55e7f` 证明官网可在单页回显并返回
`rows=1000`；改用月度单页后，1,098 个公告才获得无重复 exact cover。空月份会
回显 `pageSize=0`，只有同时满足 `success=true`、`code=200`、`total=0` 和空数组
时才作为合法负证据。

CNINFO `adjunctSize` 是近似 KB：实测 PDF 26 KB 对应 26,218 bytes，旧 HTML 标记
1 KB 但实际为 3,466 bytes。因此硬门禁采用响应 `Content-Length`、内容哈希、
Content-Type、magic/结构、公告日期或 ID 标记；`adjunctSize` 只采用 16 倍宽容的
异常范围，不能用严格相等误杀旧档案。文档按年份形成独立有界活动。

403、429、WAF 或非重试错误的同合同恢复在首版完全禁用。触发后会先在与 output
无关的 provider×host 治理根持久打开签名熔断器，再停止活动；更换 output、合同或
删除活动目录都不能继续联网。未来只有接入独立可信的人类授权根后才能设计清除流程，
普通 CLI 字符串不能充当批准。timeout、连接中断和 5xx 仍只能在原合同预算内重试。

生产 CLI 不再接受任意 output 或 capture-key 路径。capture/coverage 只能写批准的
lake `staging/`，`data/`、canonical freeze、local bundle 和 lake 根均有写保护。
HTTP transport 显式禁用环境代理；Baostock v2 保存匿名登录和查询的实际 protocol
request/response bytes、socket peer、SDK RECORD 根，并从 raw 重算 exchange 数及
code/date/fields/year 绑定。

### CSI 公告附件准入前补采

附件计划只能从已验证 details capture 的签名 raw 独立重放生成。早期 details
generation 的 terminal journal 与 raw payload 有签名，但其 v1 publication 没有
最终签名，因此允许作为 value-only 来源，必须在下游固定记录：

```text
source_capture_schema=free_provider_backfill_capture_v1
source_publication_signature_verified=false
source_normalized_artifacts_trusted=false
weak_source_ancestry=true
```

这类来源不能因为下游 publication 是 v2 就被升级为可信 PIT 血缘。系统另行执行
v2 discovery → inventory → details 链，后续以 coverage-use 投影对账，而不是静默
改写既有 generation。

真实 1,098 条 details 的 OSS 范围离线重放得到 602 个审计对象：439 个 URL 同时
满足安全 URL 和路径日期位于 `20110101–20191231`，可以进入有界网络计划；147 个
缺少可验证路径日期、14 个为外站或拼接错误等拒绝引用、2 个明确指向 2020/2025，
全部进入 signed blocked-reference audit，不发网络请求。重复 URL 只下载一次，但
保留每条 announcement→attachment edge 及其独立 disposition；当前下载永远不证明
历史 `known_at`。

附件 transport 只允许 `https://oss-ch.csindex.com.cn`，并重算 envelope schema、
GET、原 URL、禁止 redirect、Content-Length、MIME、文件 magic、body hash 和
HTML/WAF。128 MiB body cap 是合同身份的一部分；超限响应保存安全响应头、读取前缀
大小/哈希、64 KiB 样本哈希和真实 exchange count 后停止，不会丢成零交换异常。

第一片真实附件已发布为
`free_provider_backfill_88c03e4dcce007aef3b092af`：439/439 positive、0 error、
439 个 wire exchange、33,876,978 bytes 签名响应 evidence。独立无网络重放得到 439
个附件索引、163 个 blocked reference 及 root
`76c9cc511a0776731f460c716a461b8ed95df1f927bbc8f37d56933f0b2afd98`。
manifest 仍明确携带 `weak_source_acquisition_ancestry`、
`csi300_attachment_semantic_parser_not_run`，`pit_membership_authorized=false`。

附件命名审计还发现一类官方旧格式 `YYYYMMDD<index-codes>2cons.xls[x]`。其中 146
个 URL 的前八位是合法研究期日期，但旧通用 token 规则因日期后紧接数字而保守阻断，
包括关键 `201511302cons.xls` 与 `201605302cons.xls`。它们将以单独内容寻址的
legacy-cons slice 补抓，不能通过放宽通用数字解析规则混入第一片 generation。

### CNINFO supplemental 类别

base profile 继续只表示既有 ST/退市、权益分派/限制措施、沪市停复牌、深市停复牌
四族。新增 `supplemental` profile 独立锁定 7×108=756 个 category×month leaves：

- `category_bcgz_szsh;` 补充更正；
- `category_pg_szsh;` 配股；
- `category_sf_szsh;` 首发；
- `category_tszlq_szsh;` 退市整理期；
- `category_zf_szsh;` 增发；
- `category_gqbd_szsh;` 股权变动；
- `category_fxts_szsh;` 风险提示。

discovery 与 inventory 合同显式绑定 `leaf_profile`，不能用 base capture 满足
supplemental 缺叶。document 请求记录上游 provider、adapter、scope、schema、contract、
generation 和 publication-signature 状态；v1 上游只能 value-only。文档 transport 与
normalizer同时重算 envelope schema、GET、原 URL、no redirect、body SHA-256、长度、
MIME、文件结构和 WAF，shared HTTP module hash 也进入实现身份。

### 长连接过期

全量 security-basic 首次运行在 `600035.SH` 收到 Baostock `10001001 用户未登录`。
该失败回执及 pause 保持不可变。恢复器只把这一精确会话错误列为有界重登录条件，
关闭旧 socket 后创建新 transport；其他 Baostock 业务错误仍 fail closed。修复改变
实现身份，因此没有往旧 journal 偷接事件，而是启动新 contract/activity 重跑。

### 已完成的全市场结果

| 证据 | generation | 结果 |
| --- | --- | --- |
| Baostock 全市场状态 | `free_provider_backfill_96eac0be2174cdb7b3d6e379` | 3,799 个请求；5,760,634 个 provider security-day；0 normalizer 冲突 |
| Baostock raw coverage-use v2 | `free_provider_state_coverage_use_0a36c9532a79c8788c4f707a` | 5,709,826 个期望日；3 缺失、1 生命周期外多余，正确为 `blocked_gaps` |
| CNINFO 完整公告清单 | `free_provider_backfill_06cad6faacb5cc6033f703b4` | 2,459 个计划请求、2 次有界重试、66,881 个唯一公告、432 个完整月叶、0 冲突 |
| Baostock CSI300 指数日线 v2 | `free_provider_backfill_51ab9f884b4e7361d0f7dd1d` | 1,945 个研究日，publication signature 与 wire closure 均通过 |
| Baostock security-basic v2 canary | `free_provider_backfill_976b817861751227d290c782` | `600000.SH` 身份、协议请求和返回代码一致 |
| Baostock turnover v2 canary | `free_provider_backfill_416ab1d145ba2cfa43fbd63e` | `600000.SH` 1,967 行，返回证券身份一致 |

39 只退市证券原先表现为“多一天”，根因是内部把供应商 `delist_date` 错当成首个
无效日；实际合同现统一为包含最后上市日。Admission、PIT、universe 和 matrix 的
生命周期语义已一起修正。剩余差异是：

- `001872.SZ`、`001914.SZ`、`302132.SZ` 均缺 `2012-09-10`；本地旧行情有该日，
  Baostock 长区间响应没有，须用有回执的窄窗复查及代码变更链裁决；
- `600680.SH` 在主表退市日 `2019-05-23` 后又返回 `2019-05-24`，不得自动扩展
  生命周期，须由终止上市公告/交易所日历裁决。

因此网络活动成功而研究产出仍可为零；当前数据准入没有被强行改成通过。
