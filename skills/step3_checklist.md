# Step 3 Execution Checklist

Quick reference for Step 3 Text Pre-Scan. Produce all four outputs before proceeding to Step 3.5.

---

## Four Required Outputs

### Output 1 — Pages requiring visual verification
List page numbers where text is ambiguous, strategic, or valuation-relevant.
Priority targets: growth bridges · margin trend charts · segment tables · peer comp tables · product demos · legal footnotes

### Output 2 — Claims requiring external evidence
List specific claims that need verification against external data.

| Claim | Type | Best-fit source |
|---|---|---|
| e.g. "PMI recovery supports demand" | Chinese macro | `akshare_fetcher.py --mode macro` (§11.6) |
| e.g. "beef prices surging" | Commodity | `cme_fetcher.py` (§11.2) |
| e.g. "revenue RMB 50bn" | Company filing | `hkex_fetcher.py` / `edgar_fetcher.py` (§11.1) |
| e.g. "CPI at 3%" | Global macro | `fred_fetcher.py` (§11.2) |

### Output 3 — Independent falsification agenda
Items to investigate REGARDLESS of whether the sell-side discloses them.

Default recent-period lookback: past 12 months unless thesis requires longer.

Minimum scope by company type:
- Product / consumer → safety events, recalls, regulatory enforcement
- Financial → credit events, regulatory sanctions, asset quality disputes
- All → material litigation, government investigations, major customer/contract losses

Agenda must be self-generated — not solely derived from risks the sell-side chose to disclose.

### Output 4 — Intra-document quantitative conflicts
Flag pairs where two numbers within the same source conflict.
Trigger: >15% relative difference, OR smaller difference material to valuation or PM conclusion.
Auto-route all flagged items to Step 3.5 as (b)-class — resolve before Step 6.

---

## §2.3 Forensic Gate — Trigger Check

Ask at Step 3: does the source material contain any of the following?

| Trigger | Condition |
|---|---|
| Accounting policy change | Any shift affecting period-over-period comparability: depreciation/amortization, revenue recognition, capitalization policy, consolidation scope |
| Accruals pattern | Earnings materially outpacing operating CF |
| Working capital deterioration | DSO or DIO expanding, DPO compressing |
| Earnings quality screen | 2+ years of primary filing data available AND quality is thesis-relevant |

If any trigger is present, route to `python3 skills/forensic_accounting.py [TICKER]` at Step 5.

---

## Source Routing Quick Reference (for (b)-class items at Step 3.5)

| Claim type | Best-fit source | Fallback |
|---|---|---|
| Company financials / segment data / capex | `hkex_fetcher.py` / `edgar_fetcher.py` | Tavily L2 on filing URL only if direct filing retrieval succeeded but local extraction is insufficient |
| Chinese macro (PMI, PPI, CPI, M2) | `akshare_fetcher.py --mode macro` | NBS/PBoC primary release |
| Global macro (CPI, PCE, rates, unemployment) | `fred_fetcher.py` | — |
| Commodity prices | `cme_fetcher.py` | — |
| News / events / public info | Tavily L1 | L2 if URL identified |
| SEC cross-filing keyword discovery | `edgar_fulltext_search.py` | — |
| A-share ST status / HK Connect flow | `akshare_fetcher.py` | — |
