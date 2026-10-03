# Structured PM Verdict Schema (v0.1, 2026-04-27)

**Purpose**: Replace free-form markdown PM Verdicts with structured JSON so that:
1. Verdict-to-verdict deltas can be computed (addresses critique 弱项 1 — time dimension)
2. Each verdict has a stable identity (`verdict_id`, `supersedes`) for decision-chain auditing (弱项 3)
3. Break conditions are machine-checkable, not free-text (弱项 2)
4. Pricing scenarios carry probabilities so downstream RRB can score expected value (bridge to RRB)
5. Evidence chain is explicit, addressing the 3-line evidence convergence requirement (CLAUDE.md §6.4)

**Where verdicts live**: `Buyer_Analyst/verdicts/<ticker>_<YYYY-MM-DD>_v<N>.json`

**Lifecycle**: Each `/分析` produces a new verdict file. The `supersedes` field links it to the previous verdict on the same ticker (if any). No verdict is ever modified after writing — frozen.

---

## Schema

The following is a **synthetic format example**, not a completed or validated investment verdict. No figures refer to an actual company. It replaces the original personal research example for publication.

```json
{
  "schema_version": "v0.1",
  "verdict_id": "buyer_analyst_EXAMPLE_2026-01-01_v1",
  "supersedes": null,
  "ticker": "EXAMPLE",
  "subject": "buyer_analyst",
  "subject_version": "illustrative-only",
  "as_of_timestamp": "2026-01-01T00:00:00Z",
  "company_name": "Fictional Example Company",
  "sector": "Illustrative",
  "addenda_applied": [],
  "stance": "watch",
  "current_market_state": {
    "price": 100,
    "currency": "USD",
    "market_cap_local": 100000000,
    "as_of": "2026-01-01"
  },
  "pricing_anchor": {
    "primary_method": "EV/GP",
    "secondary_method": "DCF",
    "deprioritized_method": null,
    "scenarios": [
      {
        "name": "bear",
        "probability": 0.25,
        "valuation_multiple": {
          "type": "EV/GP",
          "value": 10
        },
        "fair_value_per_share": 60,
        "currency": "USD",
        "rationale": "Synthetic downside assumptions",
        "implied_return_pct": -40
      },
      {
        "name": "base",
        "probability": 0.5,
        "valuation_multiple": {
          "type": "EV/GP",
          "value": 15
        },
        "fair_value_per_share": 100,
        "currency": "USD",
        "rationale": "Synthetic central assumptions",
        "implied_return_pct": 0
      },
      {
        "name": "bull",
        "probability": 0.25,
        "valuation_multiple": {
          "type": "EV/GP",
          "value": 20
        },
        "fair_value_per_share": 140,
        "currency": "USD",
        "rationale": "Synthetic upside assumptions",
        "implied_return_pct": 40
      }
    ],
    "expected_value": 100,
    "current_implied_multiple": {
      "type": "EV/GP",
      "value": 15
    }
  },
  "entry_zone": {
    "low": 80,
    "high": 90,
    "currency": "USD",
    "preconditions": [
      "Illustrative thesis has been independently reviewed"
    ]
  },
  "thesis_break_conditions": [
    {
      "id": "margin_break",
      "description": "Fictional gross margin below 40% for two quarters",
      "metric": "gross_margin_pct",
      "operator": "<",
      "threshold": 40,
      "consecutive_periods": 2,
      "current_value": 50,
      "distance_to_break_abs": 10,
      "distance_to_break_pct": 20,
      "data_source": "synthetic_fixture",
      "horizon_months": 12,
      "status": "intact",
      "implied_probability_within_horizon": 0.25,
      "links_to_scenario": "bear",
      "trajectory_signal": "stable",
      "prior_period_value": 50
    }
  ],
  "re_underwrite_triggers": [
    {
      "id": "margin_stabilization",
      "description": "Fictional margin remains above the review threshold",
      "is_required": true,
      "horizon_months": 6,
      "implied_probability": 0.5
    }
  ],
  "evidence_chain": [
    {
      "line": "text",
      "source_type": "synthetic_fixture",
      "source_ref": "Illustrative schema example; not a real filing",
      "claim": "All figures are invented to demonstrate the format",
      "tier": "T3"
    }
  ],
  "execution_friction": [
    {
      "item": "No live sources supplied",
      "priority": "P1",
      "verdict_impact": "Not suitable for an investment decision"
    }
  ],
  "rrb_export": {
    "ready": false,
    "lineage_strategy": "stable_per_leaf",
    "leaves_emitted": [],
    "predictions_exported_count": null
  },
  "narrative": {
    "executive_summary_md": "Synthetic schema example only.",
    "variant_perception_md": "Not applicable.",
    "long_thesis_md": "Illustrative only.",
    "falsification_case_md": "Illustrative only.",
    "vs_prior_verdict_md": "No prior verdict."
  },
  "prior_context_loaded": {
    "loaded_at": "2026-01-01T00:00:00Z",
    "prior_verdict_id": null,
    "rrb_lineage_members_seen": 0,
    "watchlist_state_at_load": null
  }
}
```

---

## Field semantics

### `verdict_id`
Globally unique. Format: `buyer_analyst_<TICKER>_<YYYY-MM-DD>_v<N>` where `<N>` is the version on that day (start at v1; increment if same-day re-run).

### `supersedes`
Points to previous verdict on same ticker. `null` if first. This is the chain that lets us trace stance evolution.

### `pricing_anchor.scenarios[].probability`
**Must sum to 1.0 across scenarios.** Validated. These probabilities are the PM's NATIVE probability assertion — they go directly into RRB as `report_implied_probability` (no Opus inference needed).

### `thesis_break_conditions[]`
Each break is a machine-checkable triple `(metric, operator, threshold)` plus horizon. `current_value` is the latest observed; `distance_to_break_*` is auto-computed. `status` ∈ `{intact, warning, broken, data_unavailable, scheduled}`.

### `implied_probability_within_horizon`
PM's assessment of P(this break fires within horizon). Used as `report_implied_probability` when bridge converts to RRB prediction.

### `evidence_chain[]`
Three lines (text/visual/valuation/falsification) per CLAUDE.md §6.4 + §2.2. Each item carries a tier (T1/T2/T3) per §6.2.

### `rrb_export.ready`
Set true when verdict is finalized and ready to bridge. Bridge script ignores verdicts with `ready: false`.

---

## Backwards compat

Old watchlist entries (free-text break conditions) can be **back-filled** by hand using this schema. The bridge can ingest them as **T0 lineage members** retroactively, even if they came from the old format — provided someone hand-structures them once.

For example, a legacy condition such as "gross margin below threshold for two quarters" can be converted into a structured metric, operator, threshold and horizon. Historical personal watchlist examples are excluded from this public snapshot.

---

## Schema versioning + drift control (anti-trap-3)

**Truth source**: this file (`spec/PM_VERDICT_SCHEMA.md`) is the single authoritative spec. Code that reads/writes verdicts MUST declare which `schema_version` it supports.

**Versioning rules**:
- `schema_version` field on every verdict file is **MANDATORY**. Bridge halts on missing/unknown versions.
- Backward-incompatible changes (rename/remove a field, change semantics) require version bump (`v0.1 → v0.2`).
- Backward-compatible additions (new optional field) keep the version, but consumers that depend on the new field must declare a `min_required_schema_version`.
- Each version bump requires a migration script at `migrations/v<old>_to_v<new>.py` that converts old verdicts in-place (writes new file with new version, original preserved with `.v0_1.bak` suffix).

**Bridge code requirement**: `scripts/buyer_analyst_to_rrb.py` declares `SUPPORTED_VERDICT_VERSIONS = ["v0.1"]` at module top. Encountering a verdict with version not in this list → exit 1 with explicit error pointing to migration script.

**Compatibility table** (initial):
| Verdict version | Bridge version | RRB schema version |
|---|---|---|
| v0.1 | bridge-0.1 | rrb-prediction v0.3 |

---

## Reverse flow — verdict feeds back to next /分析 (anti-trap-1)

A new /分析 on a ticker that already has prior verdicts MUST:

1. Call `scripts/load_prior_context.py <ticker>` BEFORE generating new verdict
2. Receive structured prior context: most recent verdict + lineage trajectory from RRB + watchlist current state
3. PM (Opus, main thread) explicitly addresses in the new verdict:
   - **vs prior verdict**: which `thesis_break_conditions` fired / are still intact / are now warning
   - **vs RRB lineage**: latest probability vs prior; |Δp|; whether change is warranted by info-delta or unwarranted overshoot
   - **stance evolution**: Buy → Watch → Re-underwrite → Avoid trajectory across verdict chain
4. New verdict's `supersedes` field MUST equal the immediately prior `verdict_id` for that ticker

This closes the loop: production no longer starts from zero.

---

## Markdown is rendered FROM structured (anti-trap-2)

**Structured verdict JSON is canonical**. Markdown PM Verdicts are rendered from it via `scripts/verdict_to_markdown.py`. PM (Opus) authors structured first, then renders for human reading.

Free-form prose (e.g., the Long Thesis steel-man, evidence narrative) lives in dedicated `narrative.long_thesis_md`, `narrative.falsification_case_md` fields within the structured verdict — they're still text, but they're **named slots** so they're machine-locatable for trajectory comparison.

**Old markdown verdicts** (pre-2026-04-27) are not auto-convertible. They must be hand-authored into structured form once (one-time back-fill cost). After that, all writes go through structured-first.

---

## What this addresses from the critique

| Critique 弱项 | This schema's answer |
|---|---|
| 弱项 1 (时间维度 — current/previous/delta) | `verdict_id` + `supersedes` chain; bridge to RRB lineage gives full trajectory + Δp |
| 弱项 2 (thesis_break_conditions 结构化) | `thesis_break_conditions[]` with metric/operator/threshold triple + status + distance |
| 弱项 3 (pm_verdict 入 schema + verdict_id + evidence_chain) | This entire schema is the answer |
| 弱项 4 (falsification_recon T1/T2/T3 自动判定) | `evidence_chain[].tier` field; bridge enforces "T3 anchor" rule |
| 弱项 5 (跨 ticker relationships) | NOT addressed in v0.1 — separate `relationships.json` is future work |
