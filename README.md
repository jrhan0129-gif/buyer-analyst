# Buyer Analyst

**An evidence-driven investment research toolkit combining financial-data collection, deterministic analysis, and multi-agent research prompts.**

Buyer Analyst is a personal research project by **Jieran (Alex) Han**. It brings together public-source data collection, financial risk checks, valuation tools, and a structured bull/bear review process. Product and workflow design were developed with AI-assisted coding.

This repository is a **sanitized source snapshot**, not a hosted application or a production-ready autonomous investment system. The separate employer repository, client documents, broker research PDFs, credentials, personal watchlists, and historical research outputs are excluded. No investment performance or accuracy claim is made.

**Known test issue:** 24 regression tests pass and 8 fail because the valuation test fixture is out of sync with its data class. See [validation details](docs/VALIDATION.md). The snapshot is shared as-is, not as a fully validated release.

## What the project demonstrates

- **Source-aware research:** distinguish primary filings and market facts from unverified web leads, and explicitly track missing or conflicting evidence.
- **Modular data collection:** retrieve market, filing, macroeconomic, corporate-disclosure and public-web inputs through separate Python tools.
- **Deterministic financial checks:** sector-aware margin checks, accounting-risk screens, peer comparisons and valuation calculations.
- **Multi-agent workflow design:** separate market-data, filings, forensic review, bull thesis, bear thesis and verdict validation into defined roles with context boundaries.
- **Research continuity:** load locally stored prior verdicts and a user-created watchlist; optionally integrate a separately supplied benchmark repository.

The original data-layer package contains **16 Python modules**, including a shared utility module and PDF extraction. This is not a claim of 16 independent live APIs.

## Workflow

```text
Research question / ticker
           |
           v
Market facts + primary filings + supporting public sources
           |
           v
Data reconciliation packet + risk flags + missing-evidence notes
           |
           v
Financial checks / peer comparisons / valuation scenarios
           |
           v
Isolated bull and bear analyses (run in an LLM-capable host)
           |
           v
Human-reviewed verdict + validation + local research memory
```

**Important distinction:** `skills/pipeline_runner.py` collects and assembles research inputs. It does not independently run the full LLM debate or deliver a completed investment judgment. `CLAUDE.md` and `agents/` describe the host-assisted workflow; copying the prompts is not equivalent to executing it.

## Repository map

| Path | Purpose |
|---|---|
| `CLAUDE.md` | Research framework, evidence gates and host instructions |
| `agents/` | Prompts defining researcher roles, context boundaries and review criteria |
| `skills/` | Python collection, risk, valuation, screening and tracking tools |
| `scripts/load_prior_context.py` | Local research-memory loader |
| `scripts/verdict_to_markdown.py` | Render a structured verdict as Markdown |
| `spec/PM_VERDICT_SCHEMA.md` | Structured verdict specification |
| `docs/DATA_LAYER_GUIDE.md` | Source tiers and data-reconciliation design |
| `docs/PUBLIC_RELEASE.md` | Publication exclusions and portability notes |
| `tests/` | Offline regression tests for risk and valuation logic |

## Quick start

Use Python **3.10+**; Python 3.12 is a practical baseline for this snapshot. Run commands from the repository root.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest -q
```

The regression tests use constructed inputs rather than live market calls. They cover selected risk-routing and valuation calculations, **not** the correctness of every data source or the investment quality of the overall system.

Inspect a tool's arguments before requesting live data:

```bash
python skills/pipeline_runner.py --help
python skills/valuation_matrix.py --help
python scripts/load_prior_context.py NVDA --pretty
```

Optional live-data examples, subject to provider access, availability and usage terms:

```bash
python skills/live_market_fetcher.py NVDA
python skills/pipeline_runner.py NVDA --skip-filing --skip-forensic
```

Some simple scripts accept positional inputs rather than an argparse-style `--help`; consult their source docstrings. Network-dependent adapters may require additional configuration or maintenance as provider interfaces change.

## Optional dependencies and access

- **AKShare:** install `akshare` for the corresponding regional-data adapter.
- **Complex PDF extraction:** install `docling`; some document paths also use external tools such as `pdftotext`. No broker PDFs are supplied here.
- **Advanced metrics:** `financetoolkit` is optional and may require provider access.
- **Web search:** install `tavily-python` and set your own `TAVILY_API_KEY` in your shell when using the Tavily adapter.
- **Macro data:** set your own `FRED_API_KEY` when using the FRED adapter.
- **Transcript provider:** set your own `FMP_API_KEY` where that adapter requires it.
- **SEC:** review and configure the adapters' `User-Agent` headers with your own valid contact information before requesting SEC data. The original generic header strings are not your registration or authorization.

Do not put real API keys into source files or commit them to Git. The tools read the relevant keys from environment variables. Provider fees, rate limits and data-redistribution rights remain the user's responsibility.

## Optional host and benchmark integrations

The prompts were written for a Claude-style multi-agent host. Model names, agent spawning, scheduling and some auxiliary skill references are host conventions, not bundled implementations. Scheduled shell jobs, local assistant settings, third-party skill collections and vendored FinRobot code are excluded from this release.

The Research Report Benchmark bridge is also **not included**. If you independently have a compatible copy, set `RRB_ROOT` to its root directory. Otherwise, local verdict/watchlist memory still works, while external benchmark lineage data is absent. Do not interpret missing historical data as validated prediction performance.

## Limitations and responsible use

- This is an experimental research aid, **not investment advice** or an execution system.
- Model judgments can be wrong even when calculations pass tests. Review source freshness, units, currencies, accounting periods and evidence provenance.
- Source-access changes, data gaps and extraction failures can affect results. An assembled packet is not proof that every source succeeded.
- Supply only data you are authorized to access and process; do not upload confidential client or employer material to an LLM service.
- No project-wide open-source license has been selected for this snapshot. Public visibility alone does not grant a reuse license; third-party dependencies retain their own licenses.

## 中文简介

这是一个个人买方投研研究工具：把公开数据采集、来源可信度判断、财务风险检查、估值计算，以及多 agent 的多空论证和审阅流程组合起来。

公开版只展示代码与框架，不包含券商研报、公司或客户资料、个人观察清单、运行记录和密钥。部分能力依赖外部数据服务或 LLM 宿主；并非下载即可全自动完成投资决策的产品。
