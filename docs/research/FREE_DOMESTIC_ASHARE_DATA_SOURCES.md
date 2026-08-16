# 免费国内 A 股数据源对首期准入缺口的适配调研

调研日期：2026-08-16

适用范围：首期 `2012-01-01` 至 `2019-12-31`、PIT CSI300、期间生命周期相交的全部 A 股

裁决依据：[Governed A-share Data Admission Contract](../DATA_ADMISSION_CONTRACT.md)

## 结论

没有发现一个**长期免费、单一调用、同时提供历史版本语义**的数据 API，能够独立满足当前 Data Admission Contract。免费路线可行，但必须是多源组合：

1. **Baostock 作为免费主补采源**：补历史逐日 `isST`、`tradestatus`，并提供 CSI300 历史快照、分红和复权因子交叉校验。它是当前最值得先接入并做受治理探测的免费来源。
2. **JQData 三个月免费试用作为一次性增强源**：补 `is_st`、`paused`、涨跌停、复权因子和历史指数成分，文档和字段语义比网页抓取清楚；但它不是永久免费的生产来源。
3. **中证指数官网作为 CSI300 公布时间的权威源**：公告同时给出发布日期、生效日和调入调出名单，可修复 Baostock/JQData 只有“某日成分”而没有“何时公开”的 PIT 缺口。
4. **巨潮资讯为公司行为与 ST 事件版本的权威档案源**，上交所、深交所公告页用于交叉校验。公告 ID、秒级公告时间、原文或 PDF 必须原样归档。
5. **AkShare 只作为适配器和侦察/交叉校验工具**，不能把 AkShare 本身当成数据权威或稳定供应合同。
6. **没有发现免费来源能返回 2012–2019 复权因子的历史 `revision/vintage` 快照**。正式路线应当从公告版本按锁定公式重建 PIT 公司行为与复权链，再用 Baostock、JQData、Tushare 当前因子交叉校验；如果当前 Profile 不允许这种派生权威，需要人工批准一个新 Profile，而不是伪造历史版本。

因此，推荐的最低成本组合是：

```text
Baostock（日状态与交叉值）
  + 中证官网（CSI300 公告日/生效日/变更名单）
  + 巨潮资讯（公司行为、ST/退市、更正公告版本）
  + 上交所/深交所（公告交叉核验）
  + 可选 JQData 三个月试用（第二独立日状态源）
```

这套组合有机会修复当前 blocker，但在 bounded probe 通过、Provider Acquisition Contract 经人工激活、逐请求回执生成之前，仍不能宣称正式准入。

## 数据源比较

| 来源 | 免费边界与登录 | 2012–2019 能力 | 稳定性与治理风险 | 当前合同定位 |
| --- | --- | --- | --- | --- |
| Baostock | 官方称免费、开源；官方 Python 客户端默认匿名登录。2026-07-10 发布 `0.9.3`，客户端协议版本 `00.9.30`。[官网](https://www.baostock.com/) / [PyPI 官方发布页](https://pypi.org/project/baostock/0.9.3/) | [`query_history_k_data_plus`](https://www.baostock.com/mainContent?file=stockKData.md) 有逐日 `isST`、`tradestatus`；[`query_hs300_stocks(date)`](https://www.baostock.com/mainContent?file=hs300Stock.md) 可查历史成分；另有 [dividend](https://www.baostock.com/mainContent?file=dividInfo.md)、[adjust factor](https://www.baostock.com/mainContent?file=factorInfo.md) | 没有公开 SLA、请求频率、历史修订政策或 provider-side request ID；每页默认 2,000，终止主要靠页长推断。历史 `code_name` 不能当 PIT 身份 | **首选免费补采源**，但必须锁客户端、限速、保存原始 socket 响应并独立验证覆盖 |
| JQData | 注册申请后普通试用三个月；基础数据日流量 50 万条、一个连接；到期或扩容收费。[官方 JQData 文档](https://www.joinquant.com/help/api/help?name=JQData) | `get_extras('is_st')`、`get_price(..., fields=['paused','high_limit','low_limit','factor'])`、`get_index_stocks(index,date)`；`STK_XR_XD` 自 2005 年 | 免费仅为试用；响应没有公司行为旧行版本，指数接口没有公告时间，复权因子没有 vintage | **一次性增强/交叉源**；不能作为长期免费自主更新唯一来源 |
| AkShare | MIT 开源、无需统一账号；但官方风险提示称接口及数据主要用于学术研究并提醒商业风险。[项目说明](https://akshare.akfamily.xyz/introduction.html) | 有当前 ST、东财停复牌、当前中证成分、分红和复权行情适配器 | 上游是网页或未承诺的 Web API，页面变更会导致接口修复；供应商权限、空响应、分页和历史修订语义不由 AkShare 保证 | **开发适配器/交叉校验**，不能独立满足受治理准入 |
| 中证指数官网 | 公共网站和公开公告，无需登录即可查询当前探测范围。[新闻与公告](https://www.csindex.com.cn/#/about/newsCenter) / [沪深300 页面](https://www.csindex.com.cn/#/indices/family/detail?indexCode=000300) | 历史调样公告可给发布日期、生效日、完整调入调出名单 | 公共 Web 接口不是承诺的商业 API；需要证明 2011 年末种子、全部定期及临时调整公告无遗漏 | **CSI300 PIT 公布权威** |
| 巨潮资讯 | 基础公告检索和公告原文公开；数据平台部分接口可能有额外使用条件。[公告检索](https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search) / [深证信数据 API](https://webapi.cninfo.com.cn/#/apiDoc) | 可按证券、类别和日期检索，返回公告 ID、时间和原文链接；适合权益分派、特别处理/退市、补充更正等事件版本 | Web 查询接口有分页和反自动化机制，未公开稳定限流；必须限速并验证退市证券、空结果和分页终止 | **公司行为/ST 公告版本权威** |
| 上交所、深交所 | 公告页公开；无需登录完成当前页面检索。[上交所公告](https://www.sse.com.cn/disclosure/listedinfo/announcement/) / [深交所公告](https://www.szse.cn/disclosure/listed/notice/index.html) | 可查历史上市公司公告，包含发布时间和文件 | 两站接口和检索窗口不同；适合作为巨潮的独立交叉源，不宜先做全市场三套重复抓取 | **权威交叉核验源** |

### 免费不等于可直接投入正式采集

“接口能返回数据”不等于满足准入：Profile 还必须锁定供应商、客户端/API/schema 版本、许可上下文、字段、请求几何、分页终止、限速与重试规则。每次调用要在传输前写 `attempt_started`，传输后写 `post_transport_receipt`，并保留请求参数、原始响应、失败、零行、分页及哈希。网页或 SDK 当前可访问也不能替代人工激活的 Provider Acquisition Contract。

## 对五个关键缺口的判断

| 缺口 | Baostock | JQData 试用 | AkShare | 官方公告源 | 组合后判断 |
| --- | --- | --- | --- | --- | --- |
| 历史 ST 日状态 | `isST` 可覆盖逐日正/负状态 | `get_extras('is_st')` 明确支持历史日期 | `stock_zh_a_st_em` 只返回当前风险警示板，[官方文档](https://akshare.akfamily.xyz/data/stock/stock.html#id49) | 巨潮“特别处理和退市”及交易所公告可证明状态变更与发布时间 | **可修复**：Baostock 主值，JQData/公告交叉；状态类型粒度和空覆盖须探测 |
| 停复牌 | `tradestatus` 给每个交易日状态；可保守派生 S/R 转换 | `paused` 为明确布尔字段，停牌时量额为 0，[官方文档](https://www.joinquant.com/help/api/help?name=JQData) | `stock_tfp_em(date)` 依赖东财网页接口；[文档](https://akshare.akfamily.xyz/data/stock/stock.html#id136)不能证明精确按日语义 | 交易所/巨潮公告补停复牌原因和发布时间 | **可修复**：逐日状态 + 公告；缺少盘中 timing 时整日不可交易 |
| CSI300 历史成分及公布/生效时间 | `query_hs300_stocks(date)` 有历史成分和 `updateDate`，但它不是公告时间 | `get_index_stocks('000300.XSHG', date)` 从 2005 年可用，但无公告时间；[官方文档](https://www.joinquant.com/help/api/help?name=JQData) | 中证适配器只下载覆盖式的当前 `000300cons.xls`，[官方文档](https://akshare.akfamily.xyz/data/index/index.html#id22) / [固定版本源码](https://github.com/akfamily/akshare/blob/1248fdd05a2dda92937d4cd39c0957825f2f7f6e/akshare/index/index_cons.py#L126-L190) | 中证公告提供 publish/effective/change list | **可修复**：中证公告建事件链，Baostock/JQData 逐日快照校验；必须包含 span 前种子和临时调整 |
| 公司行为历史版本 | dividend 返回预案、股东会、实施、登记、除权、支付日期，但只见当前聚合结果 | `STK_XR_XD` 分阶段字段丰富，但官方说明同一行会随进度修改，最新比例会覆盖前值 | 东财分红接口有方案进度和最新公告日期，但仍是当前聚合表，[固定版本源码](https://github.com/akfamily/akshare/blob/1248fdd05a2dda92937d4cd39c0957825f2f7f6e/akshare/stock_feature/stock_fhps_em.py#L15-L139) | 巨潮公告 ID、公告时间、原文/PDF 可组成不可变 proposal → shareholder → implementation → correction 版本链 | **有条件可修复**：公告版本为权威，聚合表只作索引和核对；需验证公告档案 exact cover |
| 复权因子 revision/vintage | 只返回当前事件日因子序列，无 `revision_id/as_of` | 当前 factor，无历史 vintage；JQData 也说明复权为数据商计算 | 官方文档明确前复权历史价格会随新除权除息事件变化，[行情文档](https://akshare.akfamily.xyz/data/stock/stock.html#id23) | 交易所不发布供应商复权因子；公司行为公告可提供经济事实版本 | **不能直接下载补齐**：应从 PIT 事件版本确定性重建；否则保持 blocked |

## 2026-08-16 本机 bounded probe

以下只是能力探测，不是准入证据，也没有启动批量回填：

### Baostock 0.9.3 / client 00.9.30

- 匿名 `login()` 成功。
- `query_history_k_data_plus` 对 2012 年能返回逐交易日 `isST` 和 `tradestatus`。样本 `sh.600145` 返回 243 行，其中 159 日 `isST=1`、63 日 `tradestatus=0`；说明免费源能表达正状态和负状态，而不只是事件列表。
- `query_hs300_stocks('2012-06-29')` 返回 300 行，返回字段中的 `updateDate=2012-06-25`。它可用来重建/核对成分，但 `updateDate` 不能替代真实发布日期。
- `query_dividend_data('sh.600000', year='2012')` 与 `query_adjust_factor('sh.600000', 2012–2019)` 成功；响应没有 `revision_id` 或 `as_of`。
- 官方 sdist SHA-256：`16699d82d05037a8c133577fcdeb9ac0d5a7f31edc2432c4e883004e0a95e3f7`。正式适配器必须锁这个字节身份或后续经批准的新版本。

### AkShare 1.18.91 与其上游

- 官方版本为 `1.18.91`，2026-08-13 发布；sdist SHA-256：`340b8a490513fd3a1fd329bd0ec645e667faf5052f9b8c65aef14e06580e9846`。
- `stock_tfp_em` 能触达 2012 数据，但底层东财查询对请求日返回的是从该日延伸到当前的集合，不是严格“指定日事件集合”；它不能直接映射 `security_day` 覆盖义务。
- `stock_zh_a_disclosure_report_cninfo` 对 `600000`、2012 年、类别“权益分派”返回 4 条，包含秒级公告时间和公告链接。这证明巨潮档案有构建事件版本链的能力，但全市场、退市证券、零事件和分页仍待验证。
- AkShare 当前 ST 适配器没有日期参数；中证成分适配器直接读取覆盖式的当前 XLS。这两项不能补历史 PIT。

### 中证指数官网

- 官方检索接口 `POST /csindex-home/announcement/queryAnnouncementByVo` 可以按“沪深300 + 2012”检索历史公告。
- [官方公告详情 `id=6699`](https://www.csindex.com.cn/csindex-home/announcement/queryAnnouncementById?id=6699) 返回发布日期 `2012-06-11`，正文明确该批调整于 `2012-07-02` 生效，并包含沪深300完整 18 只调出和 18 只调入名单。
- 以主题 `index_rebalance` 检索 2012–2019 得到 1,061 条全指数调样公告；[另一条 2012 年公告 `id=3242`](https://www.csindex.com.cn/csindex-home/announcement/queryAnnouncementById?id=3242) 同样含发布日期、次年首个交易日生效信息和沪深300调入调出代码。定期调整和多次临时调整均能在官方档案找到，部分正文还链接官方 XLS/XLSX。
- 只按 `indexCode=000300` 或标题关键词过滤会漏掉部分 2012–2014 公告。正确几何是抓取全部 `index_rebalance` 公告，解析正文/附件后再筛 000300。
- [官方当前样本数据接口](https://www.csindex.com.cn/csindex-home/indexInfo/index-details-data?fileLang=1&indexCode=000300) 可取得 `000300cons.xls`，但它不是历史快照 API。必须采用“历史官方锚点向前重放”，或“当前锚点 + 完整后续/历史调样反向重放”，并证明每天恰好 300 只。
- 这些证据说明免费官方档案能填补“快照日期不是公布日期”的核心 PIT seam；仍需用 exact-cover 检查证明没有漏掉临时调整和分页。

### 巨潮、上交所和深交所

- 巨潮 `POST /new/hisAnnouncement/query` 的“特别处理和退市”类别 `category_tbclts_szsh` 在 2012 年返回 483 条沪深公告。它不是结构化逐日 ST feed，但可用 2011 年末 seed + 实施/撤销风险警示事件构建状态机。
- 监管停复牌类别 `category_jgjg_tfp` 在 2012 年实测深市 1,003 条、沪市 259 条；[2012-12-31 官方 HTML 日报](https://static.cninfo.com.cn/finalpage/2012-12-31/cninfo61971005.html) 展示了连续停牌起止和临时停牌时段。它比东财 `stock_tfp_em` 更适合补正式 suspension 事件。
- 巨潮“权益分派”类别 `category_qyfpxzcs_szsh` 在 2012 年返回 7,794 条，包含提案、实施、补充和更正公告。前端最多展示 100 页，单年结果超过 3,000 条，因此正式采集必须按月或证券确定性分片，不能接受页面显示数量为完整覆盖。
- [上交所公告类型表](https://www.sse.com.cn/xhtml/disclosure/listedinfo/announcement/json/announce_type.json) 明列 `12=利润分配/转增`、`31=风险警示`、`36=停复牌`；上交所历史公告接口可作巨潮的独立核验。深交所历史接口当前探测稳定性弱于巨潮，建议只作交叉验证。

## 推荐采集合同

### 方案 A：永久免费优先

| Canonical 数据 | 主来源 | 校验来源 | 规范化方式 |
| --- | --- | --- | --- |
| `st_status_daily` | Baostock `query_history_k_data_plus.isST` | 巨潮特别处理/退市公告；可选 JQData `is_st` | 每个生命周期交易日生成 `known/is_st`；同日值不早于收盘可见；不使用历史查询返回的当前证券简称 |
| `suspensions` | Baostock `tradestatus` | 巨潮及交易所停复牌公告；可选 JQData `paused` | 由 0/1 状态转换派生 S/R；timing 未知则事件日整日不可交易；读取 2011 年末 seed |
| `index_members` | 中证官网公告事件链 | Baostock `query_hs300_stocks(date)`；可选 JQData | 公告日为 `known_at`，正文日期为 `effective_at`；由变更事件持续展开到每个 open day |
| `corporate_actions` | 巨潮公告原文/PDF | Baostock dividend、现有 Tushare、本所公告 | 每个公告 ID 形成独立 PIT version；只在各自公告时点暴露阶段字段 |
| `adjustment_factors` | 本地锁定算法从 PIT corporate actions 重建 | Baostock/JQData/Tushare 当前因子 | 每个跳变绑定可见且已生效的公司行为版本；算法、舍入、基准日和 axis hash 入合同 |

该方案没有长期账号费用，但需要实现公告解析、覆盖证明和复权重建，工程量高于购买带 PIT/vintage 的商业数据。

### 方案 B：用 JQData 免费试用缩短首期回填

普通试用为三个月、每日 50 万条。首期约 `3,869 × 1,945 = 7,525,205` 个证券日：一套逐日矩阵理论下限约 16 个满配额日，两套约 31 日，仍可能在三个月试用窗内完成有界回填。实际调用前必须确认“条”的计费口径和所需接口权限。

建议在试用窗内只取准入缺口和独立校验：

```python
get_extras("is_st", securities, start_date, end_date)
get_price(
    securities,
    start_date=start_date,
    end_date=end_date,
    fq="none",
    fields=["pre_close", "paused", "high_limit", "low_limit", "factor"],
)
get_index_stocks("000300.XSHG", date)
```

JQData 不能替代中证公告的 `known_at`，也不能提供历史 factor vintage。试用到期后，日常免费更新仍应回到 Baostock + 官方公告组合。

## 批量下载前的 bounded probe 清单

### 1. 固定软件、权限和响应语义

- 锁 Baostock `0.9.3`/client `00.9.30`、JQData SDK 版本、AkShare `1.18.91` 和官方网页/API adapter 版本。
- 记录匿名或账号权限上下文、源 IP/连接数、官方使用条款、限频和可重试错误；没有公开限频时采用单连接低速策略，不能把“目前没封禁”写成授权。
- 对成功、零行、错误、超时、断连和恰好触及 cap 的响应分别建立探测样本。

### 2. Baostock：30 只证券 × 三个边界窗口

- 样本覆盖沪/深、正常、ST、长期停牌、退市、代码变更、上市前/退市后和无事件证券。
- 窗口包含 2011 年末 seed、2012 开端、2015 中段和 2019 末端。
- 校验交易日日历行数、`isST`、`tradestatus`、量额为零、身份映射、空结果和重复调用哈希。
- 验证一只证券整个 2012–2019 span 是否稳定低于 2,000 行 cap；若等于 cap，必须按年份确定性拆分，不能把 cap 页当终止页。

### 3. JQData：先验证免费权限再消耗流量

- 对同一 30 只样本验证 `is_st`、`paused`、limits、factor 和未复权价格。
- 明确返回条数计费、每次标的数/总行数上限、空证券日、退市证券和分页/拆分语义。
- 与 Baostock 做 `(code,date)` 全连接；差异不自动投票，进入有来源证据的 conflict queue。

### 4. 中证 CSI300：完整公告链探测

- 从 2011 年最后一次有效成分种子开始，枚举到 2019-12-31 的所有定期和临时调整公告。
- 每条保存搜索响应、详情响应、公告 ID、发布日期、正文/PDF、有效日期、调入/调出代码及 SHA-256。
- 用事件链展开 1,945 个 open day，再和 Baostock/JQData 的选定日前后快照核对；任何一天不是恰好 300 个已知成员都阻断。
- 检查检索分页 total、末页、重复 ID 和搜索关键词漏召回；抓全 `index_rebalance` 后解析内容筛 000300，不能仅依赖标题或不完整的早年 `indexCode` 标签。

### 5. 巨潮/交易所：公司行为与 ST 事件版本探测

- 对 30 只样本查询“权益分派、配股、股权变动、补充更正、特别处理和退市、风险提示”。
- 样本必须含有四种终态：多阶段实施、取消、后来更正、全 span 无事件。
- 校验公告时间到秒、公告 ID、原文/PDF 可下载、总条数、分页末端和 observed-empty；确认退市证券仍能通过代码或 org ID 检索。
- 对大于前端 100 页/3,000 条显示边界的类别，按月或证券生成无缝、无重叠的 split tree，并将每个分片的 `totalAnnouncement` 纳入回执。
- 对同一经济事件构建 proposal、shareholder approval、implementation、correction/cancellation 版本，证明后期字段未提前暴露。

### 6. 每个 probe 的唯一结论

每个接口只允许落入一个终态：

- `bounded_backfill_eligible`
- `reconciliation_only`
- `permission_missing`
- `provider_cannot_prove`
- `schema_or_pit_conflict`

只有 `bounded_backfill_eligible` 能进入人工待激活的 Provider Acquisition Contract。其余状态保留证据并继续阻断，不用降低 Profile 门槛来迁就免费源。

## 对用户的实际建议

优先顺序不是“再找一个免费全家桶”，而是：

1. 先接 Baostock，只探测 `isST`、`tradestatus`、CSI300、dividend、adjust factor 五类接口；
2. 同时补中证官网 2011 年末至 2019 年末的沪深300调样公告；
3. 用巨潮公告补 ST/退市和公司行为版本；
4. 如果能申请 JQData 三个月试用，用它做第二套逐日控制矩阵，显著降低误判风险；
5. 复权 revision 不再寻找不存在的免费快照 API，改成“官方事件版本 → 锁定算法 → 因果复权因子”，经人工批准后实施；
6. 任一公告链、退市证券、空结果或分页证据不完整，就保持正式准入 blocked，但本地 development replay 可以继续。
