# Data Layer Guide

> **这份文档从主 CLAUDE.md 抽取,只保留数据层相关规则。**
> 原文是为 buy-side 投研 pipeline 服务的,但数据层的分层逻辑和冲突仲裁机制是通用的,任何数据聚合产品都能复用。
>
> 所有条款都标注了原文出处(§x.y),方便回溯上下文。

---

## 目录

1. [核心哲学](#1-核心哲学)
2. [五层数据分层](#2-五层数据分层)
3. [冲突仲裁规则](#3-冲突仲裁规则)
4. [主流程调度:缺口门与 primary filing 默认规则](#4-主流程调度)
5. [多 Agent 并行采集](#5-多-agent-并行采集)
6. [Skill 角色速查表](#6-skill-角色速查表)
7. [已知限制与缺口](#7-已知限制与缺口)

---

## 1. 核心哲学

### 1.1 数据不是结论 (§1 · §9)

> "Hard extraction > paraphrase. Verified data > narrative. Structured output > free-form. Report failures; never invent metrics."
>
> "Opus = judgment (PM, Bull, Bear, Pre-Scan, Visual); Sonnet = data collection."

**含义**:
- 数据层(Sonnet agents + fetcher 脚本)只负责**按结构交货**,不能产出投资观点/stance/目标价。
- 所有判断留给上层模型(Opus)。
- 抓不到的数据必须**透明声明缺失**,永远不能编。

### 1.2 证据不平等:Falsification 对称律 (§2.1)

原文(非常关键,直接引用):

> **Falsification symmetry rule:** Secondary-source claims (Tavily, media, broker excerpts, web abstracts) are **falsification candidates, not falsification evidence**, until verified against a primary source. A claim may enter PM Verdict only after verification at the same or higher standard as the bull evidence it challenges. Only **primary filings, official company disclosures, verified market data, or regulator/exchange disclosures are high-confidence sources by default**. Unverified secondary claims may be flagged as *risk to monitor* but must not anchor a falsification argument or PM Verdict.

**含义**:
- 媒体/搜索/研报摘要 ≠ 证据,它们是"候选线索"。
- 只有**一手文件、官方披露、经核验的市场数据、监管/交易所披露**默认是高置信度源。
- 数据产品的设计必须在字段级别区分"primary"和"secondary",不能混为一谈。

### 1.3 三线证据收敛 (§2.2)

所有结论必须同时有三条证据线支撑:

1. **Text Evidence Line** — 业务模式、增长逻辑、管理层指引的文字审计
2. **Visual Evidence Line** — 图表、表格、截图的视觉审计
3. **Valuation Evidence Line** — 纪律化估值重定价

> **Rule**: All three lines must mutually confirm or produce explainable conflicts before final adjudication.

**对数据层的要求**:每条数据必须能追溯到它属于哪条证据线,以及它的来源层级。

---

## 2. 五层数据分层

虽然原 CLAUDE.md 没有用"L1–L5"的命名,但 §3 Data Recon Layer + §5 Step 5 Group A–E 实际上就是这个分层。我把它显式化如下:

```
┌─────────────────────────────────────────────────────┐
│  L1 · 市场事实层 (Base Data, 最高权威)               │  ← live_market_fetcher.py (yfinance)
│     价格、市值、EV、股本、当前倍数                    │
│     §3.1: "Highest authority on market facts"        │
├─────────────────────────────────────────────────────┤
│  L2 · 一手文件层 (Primary Filing, 叙事终审)          │  ← hkex_fetcher.py / edgar_fetcher.py
│     年报/季报/招股书/8-K/Form 4                       │     filing_extractor.py (封装)
│     §5 Step 3.5: "Primary filing default rule"       │     edgar_fulltext_search.py
├─────────────────────────────────────────────────────┤
│  L3 · 量化质量层 (Advanced Quant, 屏幕/风控)         │  ← [不在本包]
│     ROIC / Z-Score / Beneish M / Accruals             │     (forensic_accounting / health_checker)
│     §3.2: "Cannot override Base Data on market facts" │
├─────────────────────────────────────────────────────┤
│  L4 · 宏观/可比层 (Macro & Peer Context)             │  ← fred_fetcher.py / cme_fetcher.py
│     利率、收益率曲线、商品现货、A 股通                 │     akshare_fetcher.py
│     同行估值倍数                                       │
├─────────────────────────────────────────────────────┤
│  L5 · 线索层 (Recon & Falsification Leads)           │  ← tavily_search.py (L1/L2/L3)
│     新闻、诉讼、内部人、做空、OSS 活跃度               │     insider_tracker.py / short_interest.py
│     §3.4: "Retrieval only, never valuation input"    │     court_fetcher.py / earnings_transcript_fetcher.py
│     §2.1: "Falsification candidates, not evidence"   │     github_repo_scraper.py
└─────────────────────────────────────────────────────┘
```

### 2.1 L1 市场事实层 —— `live_market_fetcher.py` + yfinance

**原文 §3.1**:
> **Base Data Agent** (`yfinance`): real-time price/cap/EV/shares/multiples. **Highest authority on market facts**.

**职责**:
- 价格、市值、EV、股本、当前 P/S/P/E/EV/GP
- 美股、大部分港股、部分 A 股的实时数据入口

**权威性**:
- **市场事实最高权威**。任何其他源(包括 L2 一手文件)在市场事实上冲突时,都以 L1 为准。
- 例外:股本数、流通股数如果 L1 与 L2 最新披露不一致,以 L2 为准(因为 L1 的 yfinance 可能滞后)。

**已知限制**:
- yfinance 对港股数据有偶发不稳定(延迟、缺段)。
- 部分 A 股 ticker 需要用 `.SS` / `.SZ` 后缀,由 `data_utils.detect_exchange()` 自动处理。

### 2.2 L2 一手文件层 —— `hkex_fetcher.py` / `edgar_fetcher.py` / `filing_extractor.py`

**原文 §5 Step 3.5** (核心规则,直接引用):
> **Primary filing default rule (non-negotiable):** 卖方研报定义"待查点",不能定义"主输入"。All key financials (segment profitability, capex, risk, guidance) are **unverified secondary** until cross-checked against HKEx/EDGAR. **Blocking:** Step 6 blocked until primary filing attempted, OR PM labels figures `[unverified secondary]`. Contradictory primary filing evidence always overrides proxies.

**含义**:
- 分部盈利、资本开支、风险披露、管理层指引 —— 这些"关键财务字段"在没和 HKEx/EDGAR 交叉核验之前,**一律是 `[unverified secondary]`**。
- 即使数据从路透、彭博、Wind 来,只要没对照一手文件,都是 secondary。
- 估值步骤(原框架 §6)在 primary filing 尝试之前被**阻塞**。

**职责划分**:
| 脚本 | 覆盖范围 |
|---|---|
| `hkex_fetcher.py` | 港交所披露易:年报/中报/季报/公告 |
| `edgar_fetcher.py` | SEC EDGAR:10-K/10-Q/8-K/Form 4/S-1 |
| `edgar_fulltext_search.py` | 跨公司/跨年份的 SEC 全文检索 |
| `filing_extractor.py` | 一键封装,自动根据 ticker 路由 HK vs US |

**扇区报告豁免 (§5 Step 3.5)**:
> **Sector report exemption (≥5 cos):** Primary filing required for 1–2 most central companies only; rest labeled `[unverified secondary]`. Exception: sell-side forward NP diverging >50% from actual → primary filing required.

—— 即研究 5 家以上公司时,不必每家都拉一手文件,中心公司 1–2 家即可,但剩下的必须显式打标。

**文档内冲突规则 (§5 Step 3.5)**:
> **Intra-doc conflict rule:** Flag within-document number conflicts (>15% relative or material). Auto-classify as (b); resolve via primary filing before Step 6.

—— 同一份报告里数字自相矛盾(>15% 或实质性),自动归类为"缺口",必须在估值前解决。

### 2.3 L3 量化质量层 —— **本包未包含**

原框架里的 `forensic_accounting.py` / `health_checker.py` 属于这一层。它们**消费** L1+L2 数据,产出风险 flag 和质量评分。本包不包含,因为同事要的是数据层不是判断层。

**原文 §3.2**:
> **Advanced Quant Agent** (`FinanceToolkit`): ROIC, Z-Score, capital efficiency. Returns `unavailable` if unreliable. **Cannot override Base Data on market facts.**

一个关键设计原则:**质量层不能推翻市场事实**。即使 FinanceToolkit 算出来的数跟 yfinance 不一样,也以 yfinance 为准。

### 2.4 L4 宏观/可比层 —— `fred_fetcher.py` / `cme_fetcher.py` / `akshare_fetcher.py`

**职责**:
| 脚本 | 覆盖 |
|---|---|
| `fred_fetcher.py` | 美国宏观:利率、通胀、就业、M2 等(FRED API) |
| `cme_fetcher.py` | CME 商品期货:WTI/Brent/黄金/铜 等 |
| `akshare_fetcher.py` | 中国宏观 + **A 股行情** + 港股通北向/南向持仓 |

**权威性**:
- 不仲裁单公司数据,但作为 **sensitivity 假设的上下文**。
- 例如 DCF 里的 WACC、周期股的商品价格预测,要引用 L4 的数字而不是拍脑袋。

**A 股专用说明**:
- `akshare_fetcher.py` 是 A 股数据的**主入口**。yfinance 覆盖 A 股不稳定。
- 中国证监会(CSRC)**没有官方 API**,监管披露只能靠 `tavily_search.py` + `site:csrc.gov.cn` 手工检索。这是已知缺口。

### 2.5 L5 线索层 —— Tavily + 辅助 fetcher

**原文 §3.4**:
> **Public Web Recon (Tavily)** L1=search, L2=single-URL extract, L3=crawl (max_depth≤2, breadth≤10). **Retrieval only — never valuation input.** Tag sources (`primary_filing`/`company_ir`/`reputable_media`/`other`); `other`-only = heightened skepticism.

**职责**:
| 脚本 | 用途 |
|---|---|
| `tavily_search.py` | 三级 Web recon(搜索 / 单 URL 抽取 / 爬取) |
| `insider_tracker.py` | SEC Form 4 + HKEx 董监高增减持(2 天滞后,只能看方向) |
| `short_interest.py` | 做空兴趣、大股东结构(~2 周滞后) |
| `court_fetcher.py` | 美国法院 PACER + DOJ 公告 |
| `earnings_transcript_fetcher.py` | 业绩电话会纪要(FMP=secondary / 8-K=primary) |
| `github_repo_scraper.py` | 开源项目 star/issue/commit 活跃度 |

**关键规则** (再强调一次 §2.1):
- L5 的数据**永远不能作为估值输入**。
- L5 只能做 "falsification lead" —— 指向应该去 L2 一手文件核验的点。
- 单靠 L5 得出的结论,最多打"risk to monitor"标签,不能进入最终判断。

**源标签 (§3.4)**:
Tavily 每条结果必须标注来源等级:
- `primary_filing` —— 来自 SEC/HKEx/交易所官方
- `company_ir` —— 来自公司 IR 官网
- `reputable_media` —— 来自彭博、路透、WSJ、FT 等
- `other` —— 其他,**重度怀疑**

只有 `other` 一档的 claim 必须显式打问号,不能直接传递。

---

## 3. 冲突仲裁规则

数据冲突不是"平均",有明确的优先级。原文 §2.2 Evidence Conflict Resolution + §3.4 SOURCE_CONFLICT 处理流程:

### 3.1 冲突优先级表

| 冲突类型 | 胜出方 | 出处 |
|---|---|---|
| 市场事实(价格/市值/股本)vs 任何其他源 | **L1 Base Data** | §2.2 |
| 估值 vs 叙事 | **估值**(除非 PM 引用高置信度新证据) | §2.2 |
| 一手文件 vs 卖方研报/媒体 | **L2 一手文件** | §5 Step 3.5 |
| 文字 vs 图像(同一 claim) | **图像**(仅限该 claim,不能推广) | §2.2 |
| Tavily/媒体 vs 一手文件 | **一手文件** | §2.1 对称律 |
| 两个同级源互相矛盾 | **SOURCE_CONFLICT** 挂起 | §3.4 |

### 3.2 SOURCE_CONFLICT 处理流程 (§3.4 · §4.8)

原文:
> **SOURCE_CONFLICT:** Two sources disagree on material figure → mark unresolved, use sensitivity ranges, log both values. PM acknowledges before proceeding.
>
> **SOURCE_CONFLICT action:** (1) acknowledge conflict and both values; (2) suspend single-point use; (3) substitute sensitivity range; (4) state resolution condition.

**四步硬约束**:
1. **Acknowledge** —— 在输出里显式承认冲突,列出两个值。
2. **Suspend** —— 不能在任何下游计算里用单点值。
3. **Substitute** —— 用 sensitivity range (low/high) 替代。
4. **Resolution condition** —— 陈述什么样的新证据能解除冲突。

### 3.3 图像触发审计 (§2.2)

原文:
> **Triggered Visual Audit:** `health_checker.py` HIGH/ANOMALY/STRESS → mandatory visual audit before Verdict: BURN/RUNWAY→cash flow; MARGIN_FRAGILITY→GM bridge; LEVERAGE→capital adequacy/asset quality. PDF escalation: `pdftotext`-only insufficient — use `pdfimages -j` or `docling_extractor.py`.

**对数据层的要求**:
- 当 L3 健康度检查(本包不含,但可替换为任何风险触发器)报出 HIGH/ANOMALY/STRESS,数据层必须**主动升级**,触发 PDF 图像/表格抽取。
- `pdftotext` 不够用时,必须 fallback 到 `pdfimages -j` 或 `docling_extractor.py`。
- **数据层有否决权** —— 它可以强制下游步骤暂停,直到图像审计完成。

---

## 4. 主流程调度

### 4.1 Verification Gap Gate (§5 Step 3.5)

这是整个数据层最关键的调度机制。原文:

> **Verification Gap Gate.** Classify each item as **(a) covered** → proceed, or **(b) gap** → resolve before Step 6 via: (1) `hkex_fetcher.py`/`edgar_fetcher.py`; (2) CME/FRED/AKShare; (3) Tavily L1. Unresolved (b) items = *risk to monitor* only, not valuation/Verdict inputs.

**操作流程**:
```
Pre-scan 输出 falsification 议程项
        │
        ├─ (a) 已覆盖 → 继续
        │
        └─ (b) 缺口 → 必须按以下优先级解决:
                     1st: hkex_fetcher / edgar_fetcher   (L2 一手文件)
                     2nd: CME / FRED / AKShare           (L4 官方宏观)
                     3rd: Tavily L1                      (L5 Web)
                     │
                     ├─ 解决 → 升级为 (a)
                     └─ 仍未解决 → 只能标 "risk to monitor"
                                  不能进入估值或 Verdict
```

### 4.2 Primary Filing 默认规则 (§5 Step 3.5)

重复一遍,因为这是**不可协商**的:

> **Blocking:** Step 6 blocked until primary filing attempted, OR PM labels figures `[unverified secondary]`.

**含义**:
- 数据 agent 不能默认信任任何二手源。
- 如果一手文件抓不到,必须显式打 `[unverified secondary]` 标签,而不是偷偷当一手用。
- 下游估值步骤在这一步被硬阻塞。

---

## 5. 多 Agent 并行采集

原文 §5 Step 5 的数据采集调度(我只保留数据相关的 A/B/D 三组,因为 C=Forensic 和 E=Sell-Side 不在本包范围):

| Group | Agent prompt | 层 | Scripts |
|---|---|---|---|
| **A** Market & Risk | `agents/data_market_risk.md` | L1 + L3 触发器 | `live_market_fetcher.py` + (health_checker,本包无) |
| **B** Primary Filing | `agents/data_primary_filing.md` | L2 | `hkex_fetcher.py` / `edgar_fetcher.py` (§5 Step 3.5 阻塞规则) |
| **D** Falsification Recon | `agents/data_falsification_recon.md` | L5 | `tavily_search.py` + 可选 `court_fetcher` / `insider_tracker` |

### 5.1 信息隔离原则 (§8 · `agents/context_map.md`)

原文:
> Bull/Bear information-isolated, parallel at Step 8.

这是对抗层的隔离,数据层本身不隔离,但**数据 agent 看不到任何判断结果**(PM Verdict/Bull thesis/Bear thesis 都不能回流到数据 agent)。这避免了"数据迎合结论"的污染。

**对产品的启发**:
如果做数据平台,**下游的判断/观点不应该作为上游数据采集的 context**。否则会出现 "我知道 PM 想得出 bull 结论,所以我优先搜正面数据" 的偏差。

### 5.2 合并规则 (§5 Step 5)

> **After all groups complete:** PM (Opus) consolidates into `data_recon_packet`. If Group A health_checker raises HIGH/ANOMALY/STRESS → execute §2.2 Triggered Visual Audit before Step 6.

**流程**:
1. 所有 Group 并行完成。
2. PM(上层 Opus,本包不含)把各组输出合并成一个统一的 `data_recon_packet`。
3. 如果 Group A 的风险体检报出 HIGH/ANOMALY/STRESS,**先触发图像审计,再往下走**。

---

## 6. Skill 角色速查表

从原 §10 Skill Reference 抽取,仅保留数据抓取相关:

| Skill | Role | 所属层 | Key constraint |
|---|---|---|---|
| `live_market_fetcher.py` | Real-time market facts | **L1** | yfinance,市场事实最高权威 |
| `hkex_fetcher.py` / `edgar_fetcher.py` | Primary filings | **L2** | §5 Step 3.5 阻塞规则,supersedes secondary data |
| `filing_extractor.py` | Filing fetch+extract | **L2** | 一键封装,自动路由 HK vs US |
| `edgar_fulltext_search.py` | Cross-filing search | **L2** | Phrase-only;`cik_filter_exhausted` → drop ticker |
| `docling_extractor.py` | Complex PDF tables | **L2 支持** | 只用于复杂布局;简单表用 pdftotext |
| `fred_fetcher.py` / `cme_fetcher.py` | Macro / commodity | **L4** | 用于校准假设,不作为估值单点输入 |
| `akshare_fetcher.py` | CN macro, HK Connect, **A 股** | **L4** | CSRC 无 API,需 Tavily+site:csrc.gov.cn |
| `tavily_search.py` | Web recon L1/L2/L3 | **L5** | §3.4 Retrieval only, **never valuation input** |
| `insider_tracker.py` | Form 4 + HKEx insider | **L5** | 2 天滞后,directional only |
| `short_interest.py` | SI / ownership | **L5** | ~2 周滞后 |
| `court_fetcher.py` | Court + DOJ | **L5** | Leads only;older DOJ → Tavily `site:justice.gov` |
| `earnings_transcript_fetcher.py` | Transcripts | **L5** | FMP=secondary, 8-K=primary |
| `github_repo_scraper.py` | OSS vitality | **L5** | 仅用作 falsification lead |
| `data_utils.py` | 共享工具 | — | ticker 归一化 / 交易所判断 / yf 封装 / retry |

---

## 7. 已知限制与缺口

这些是原框架**已经踩过坑**的限制,同事直接继承,不用再试一次:

| 缺口 | 影响 | 解决方向 |
|---|---|---|
| **港股 yfinance 不稳定** | L1 港股数据偶发延迟/缺段 | 交叉验证 HKEx 披露易;关键字段以 L2 为准 |
| **中国证监会无 API** | L5 CN 监管线索只能靠 Tavily + `site:csrc.gov.cn` | 暂时无解,手动检索 |
| **Form 4 滞后 2 天** | L5 内部人数据不能打 timing | 只用 directional 信号 |
| **短线做空数据滞后 ~2 周** | L5 拥挤度判断有延迟 | 结合 L1 成交量观察 |
| **A 股分钟级数据** | akshare 的 A 股分钟数据可能不全 | 日频是安全的 |
| **跨文件 SEC 全文检索 CIK 限制** | `cik_filter_exhausted` 错误 | 降级为单文档检索 |
| **PDF 图像抽取** | `pdftotext` 对扫描件失效 | 必须 fallback 到 `pdfimages -j` 或 `docling_extractor.py` |

---

## 8. 如果要扩展这个数据层

当同事想往这个框架里**加新数据源**时,必须回答三个问题:

1. **这个源属于哪一层?** L1/L2/L4/L5 必选一个。(L3 通常是计算出来的,不是抓取的。)
2. **它和现有源会冲突吗?** 如果会,按 §3.1 的优先级表仲裁;如果不会,就是 "additive context"。
3. **它是 primary 还是 secondary?** 按 §2.1 对称律分类。Secondary 的东西永远进不了 Verdict,只能做 falsification lead。

回答不了这三个问题的数据源,不要加进来 —— 它会污染整个仲裁链。

---

## 附录:原文索引

本 guide 引用的 CLAUDE.md 章节:

- **§1** Core Identity
- **§2.1** Falsification First(对称律)
- **§2.2** Three-Line Evidence Convergence(三证据线 + 冲突仲裁)
- **§2.3** Forensic Gate(L3 触发,本包不含)
- **§3.1–3.3** Data Agents & Governance
- **§3.4** Public Web Recon (Tavily)
- **§4.8** PM Valuation Obligation(SOURCE_CONFLICT 四步法)
- **§5 Step 3.5** Verification Gap Gate(缺口门 + primary filing 阻塞规则)
- **§5 Step 5** Data Recon Packet(并行 agent 调度)
- **§9** Tooling & Multi-Agent Rules
- **§10.1** Skills Reference

需要看原文完整上下文的话,参考本仓库根目录的 `CLAUDE.md`。
