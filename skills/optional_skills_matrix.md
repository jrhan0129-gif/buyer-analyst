# Optional Skills Activation Matrix

Quick reference for §11 Optional External Data Skills.
Activate only when thesis requires that evidence type AND core pipeline tools are insufficient.

---

| Skill | Activate when | Do NOT activate when | §11 ref |
|---|---|---|---|
| `hkex_fetcher.py` | Sell-side report present (all key financials unverified until cross-checked); no sell-side → mandatory first step | Already have primary filing data from current session | §11.1 |
| `edgar_fetcher.py` | Same as above, for SEC-listed companies | Already have primary filing data from current session | §11.1 |
| `fred_fetcher.py` | Analysis relies on global macro figure (CPI, PCE, rates, unemployment) from sell-side or secondary source | Macro data already verified against primary release | §11.2 |
| `cme_fetcher.py` | Analysis relies on commodity price claim from sell-side or secondary source (per §2.1 falsification symmetry) | Commodity claim is not thesis-relevant | §11.2 |
| `peer_comps.py` | Sell-side comp table is sole relative valuation source, OR peer selection appears selective/cherry-picked | Independent peer data not needed | §11.3 |
| `short_interest.py` | Short-bias/Avoid stance where crowding affects entry risk; OR squeeze is a bull catalyst; OR management credibility under scrutiny | Positioning not relevant to thesis | §11.4 |
| `edgar_fulltext_search.py` | Need cross-filing keyword discovery (risk language history, going-concern, litigation) that edgar_fetcher + Tavily can't efficiently provide | Single-filing review sufficient | §11.7 |
| `insider_tracker.py` | Short-bias/Avoid where insider selling is thesis-relevant; Watch/Buy where cluster buying is catalyst | Management conviction not in scope | §11.8 |
| `akshare_fetcher.py` | Analysis requires Chinese macro indicators (PMI, PPI, CPI, M2), CSRC enforcement proxy, or HK Connect flow data | General Chinese company analysis — hkex_fetcher + Tavily usually sufficient | §11.6 |

---

## Activation Decision Rule

Before activating any optional skill, answer two questions:
1. Is this evidence type required by the current thesis or falsification agenda?
2. Can the core pipeline (hkex/edgar/health_checker/valuation_matrix) provide it?

Activate only if (1) YES and (2) NO.
