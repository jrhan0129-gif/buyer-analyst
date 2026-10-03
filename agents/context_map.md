# Multi-Agent Context Isolation Map

This file defines which CLAUDE.md sections each agent role needs. The PM (main thread) loads full CLAUDE.md. Sub-agents receive only the sections listed below, injected into their prompt at spawn time.

## Model Assignment

| Role | Model | Rationale |
|---|---|---|
| PM (main thread) | **Opus** | Synthesis, judgment, final verdict |
| Text Pre-Scan (Step 3) | **Opus** | Detecting narrative manipulation requires nuanced language understanding |
| Bull Thesis Agent | **Opus** | Constructing compelling investment argument requires deep reasoning |
| Bear / Falsification Agent | **Opus** | Adversarial hypothesis testing requires independent critical thinking |
| Visual Audit judgment | **Opus** | Interpreting charts vs text contradictions |
| Data Market & Risk (Group A) | **Sonnet** | Script execution + JSON parsing |
| Data Primary Filing (Group B) | **Sonnet** | Filing download + text extraction |
| Data Forensic (Group C) | **Sonnet** | Script execution + flag reporting |
| Data Falsification Recon (Group D) | **Sonnet** | Search execution + result classification |
| Data Sell-Side Extract (Group E) | **Sonnet** | Structured data extraction from sell-side PDF |
| Valuation Execution (Step 6) | **Sonnet** | Peer comps + valuation_matrix.py assembly + SOTP/WACC/FCF |
| Narrative Integrity (Step 7) | **Sonnet** | Conflict-of-interest scan in disclaimers |

## Context Isolation Rules

### Sonnet Data Agents (Groups A-D)
**Load:** Only the agent-specific prompt file (`agents/data_*.md`).
**Do NOT load:** Any CLAUDE.md sections. These agents follow their own prompt only.
**Rationale:** Data collection is mechanical; framework rules add noise and waste context.

### Opus Bull Thesis Agent — "Market's Best Advocate"
**Identity:** Synthesizes sell-side + public fund manager consensus into the strongest evidence-backed bull case. Filters sell-side claims through primary filing scrutiny.
**Data inputs:** primary filing (PRIMARY), data_recon_packet, valuation output, visual audit. Sell-side data points only if PM explicitly provides as `[sell-side — unverified]`.
**Load from CLAUDE.md:**
- §4.3 Approved Valuation Methods (to select primary anchor)
- §4.7 Capital Accessibility Framework (for Precondition requirements)
- §6.5 Target Price Closure Rule (Precondition requirements)
- Relevant §8.x sector addendum (one only, based on company type)

**Do NOT load:** §2.1 Falsification rules, §6.2 Falsification Case, falsification recon data, bear arguments.

### Opus Bear / Falsification Agent — "Buy-Side PM with Capital at Risk"
**Identity:** Thinks like a portfolio manager writing a real check. Asks: what's priced in? What's my downside? Would I buy this private? Show me the cash, not the story. What's missing?
**Data inputs:** data_recon_packet, primary filing, text prescan, visual audit, valuation output, falsification recon (full Group D output including insider_tracker).
**Load from CLAUDE.md:**
- §2.1 Falsification First (source standards)
- §2.3 Forensic Gate (flag interpretation)
- §4.7 Capital Accessibility Framework (for assessing financing risk severity)
- Relevant §8.x sector addendum falsification triggers only

**Do NOT load:** §6.1 Long Thesis, sell-side report, bull arguments.

### Opus PM (main thread)
**Load:** Full CLAUDE.md (all sections).
**Role:** Receives both bull and bear reports, applies §6.3 PM Verdict, §6.4 Adjudication Rule, §6.5 Target Price Closure. Produces final output per §7.

## Execution Flow Summary

```
Step 1-2: PM (Opus) — PDF extraction
Step 3:   PM (Opus) — Text Pre-Scan, build falsification agenda
Step 3.5 + 5: Spawn Sonnet agents in parallel: A+B+D always; C if §2.3 triggered; E only if PM explicitly needs sell-side data points
              PM waits for all to complete
Step 4:   PM (Opus) — Visual audit: (a) on-demand per Step 3 text prescan pages; (b) mandatory if Group A health_checker raised HIGH/ANOMALY/STRESS (§2.2)
Step 6:   Sonnet (`data_valuation.md`) — Peer comps + valuation_matrix.py with all flags from A/B/E
Step 7:   Sonnet (`narrative_integrity.md`) — Conflict scan (if sell-side report exists)
Post-8:   **[BLOCKING]** Sonnet (`verdict_validator.md`) — Framework compliance check. Analysis is DRAFT until validator returns PUBLISHABLE. Fix all FAILs before presenting to user.
Post-val: Sonnet — `watchlist_manager.py add` with stance, breaks, entry-zone, sector
Step 8:   Spawn 2× Opus agents in parallel (Bull + Bear)
          PM receives both, adjudicates per §6.3-6.5
          PM writes final output per §7
```

## Error Recovery Rules
1. API 529 (overloaded) → wait 30 seconds and retry once. If still failing, return partial results with `[API_OVERLOAD]` tag.
2. TLS/SSL timeout → retry once with 60s timeout. If still failing, use yfinance fallback where available.
3. Tavily SSL error → retry once. If persistent, skip that search and note `[TAVILY_UNAVAILABLE]` in output.
4. Permission denied → return the data you have with `[PERMISSION_BLOCKED: {tool}]` tag. PM will handle manually.

## Anti-Contamination Rules
1. Bull agent NEVER sees falsification recon data or bear arguments.
2. Bear agent NEVER sees bull arguments.
3. PM sees BOTH only after both are complete — no iterative back-and-forth.
4. Sonnet data agents see NO framework rules — only their task-specific prompt.
5. If PM needs to re-run an agent (e.g., new evidence emerged), spawn a fresh agent — do not send follow-up to existing one.
