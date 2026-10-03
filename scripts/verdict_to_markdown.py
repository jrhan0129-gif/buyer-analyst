#!/usr/bin/env python3
"""
verdict_to_markdown.py — Render structured PM Verdict JSON to human-readable markdown.

Anti-trap-2: enforces structured-first principle. Markdown is generated FROM structured,
never the source of truth. Use this when PM needs to share verdict with humans.

Usage:
    python3 scripts/verdict_to_markdown.py verdicts/EXAMPLE_2026-01-01_v1.json
    python3 scripts/verdict_to_markdown.py <verdict.json> -o report.md
"""
import argparse
import json
import sys
from pathlib import Path

SUPPORTED_VERDICT_VERSIONS = ["v0.1"]


def render(verdict):
    sv = verdict.get("schema_version")
    if sv not in SUPPORTED_VERDICT_VERSIONS:
        print(f"[WARN] verdict schema_version={sv} not in {SUPPORTED_VERDICT_VERSIONS}", file=sys.stderr)

    cmkt = verdict.get("current_market_state", {})
    pa = verdict.get("pricing_anchor", {})
    scenarios = pa.get("scenarios", [])
    breaks = verdict.get("thesis_break_conditions", [])
    triggers = verdict.get("re_underwrite_triggers", [])
    evidence = verdict.get("evidence_chain", [])
    friction = verdict.get("execution_friction", [])
    narrative = verdict.get("narrative", {})
    prior_ctx = verdict.get("prior_context_loaded", {})

    lines = []
    lines.append(f"# PM Verdict — {verdict.get('company_name', verdict['ticker'])} ({verdict['ticker']})")
    lines.append("")
    lines.append(f"`{verdict['verdict_id']}` · stance **{verdict.get('stance','?').upper()}** · as_of {verdict['as_of_timestamp'][:10]} · schema {sv}")
    if verdict.get("supersedes"):
        lines.append(f"Supersedes: `{verdict['supersedes']}`")
    lines.append("")

    # Current market
    if cmkt:
        lines.append(f"**Current**: {cmkt.get('currency','')}${cmkt.get('price')} · MktCap {cmkt.get('market_cap_local','?'):,} · as_of {cmkt.get('as_of','?')}")
        lines.append("")

    # PM Verdict line — pricing scenarios
    lines.append("## 1. Pricing Anchor (定价情景)")
    lines.append("")
    lines.append(f"Primary method: **{pa.get('primary_method','?')}** · Secondary: {pa.get('secondary_method','—')} · Deprioritized: {pa.get('deprioritized_method','—')}")
    if pa.get("current_implied_multiple"):
        cim = pa["current_implied_multiple"]
        lines.append(f"Current implied: **{cim['type']} = {cim['value']}×**")
    lines.append("")
    if scenarios:
        lines.append("| Scenario | P | Multiple | Fair Value | vs Current | Rationale |")
        lines.append("|---|---|---|---|---|---|")
        for s in scenarios:
            mult = s.get("valuation_multiple", {})
            mult_str = f"{mult.get('type','?')} {mult.get('value','?')}×"
            ret = s.get("implied_return_pct")
            ret_str = f"{ret:+.1f}%" if ret is not None else "—"
            lines.append(f"| **{s.get('name','?')}** | {s.get('probability',0):.2f} | {mult_str} | "
                         f"{cmkt.get('currency','')}${s.get('fair_value_per_share','?')} | {ret_str} | {s.get('rationale','')[:100]} |")
        lines.append("")
        if pa.get("expected_value") is not None:
            lines.append(f"**Expected value**: {cmkt.get('currency','')}${pa['expected_value']:.1f}  ·  vs current ${cmkt.get('price','?')}")
            lines.append("")

    # Entry zone
    ez = verdict.get("entry_zone", {})
    if ez:
        lines.append(f"**Entry zone**: {ez.get('currency','')}${ez.get('low','?')}-${ez.get('high','?')}")
        if ez.get("preconditions"):
            lines.append(f"Preconditions: {', '.join(ez['preconditions'])}")
        lines.append("")

    # Break conditions
    if breaks:
        lines.append("## 2. Thesis Break Conditions (机器可校)")
        lines.append("")
        lines.append("| ID | Description | Metric | Op | Threshold | Current | Distance | P(horizon) | Status |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for b in breaks:
            cv = b.get("current_value")
            cv_str = f"{cv}" if cv is not None else "—"
            dist = b.get("distance_to_break_abs") or b.get("distance_to_break_pct")
            dist_str = f"{dist}" if dist is not None else "—"
            lines.append(f"| `{b.get('id','?')}` | {b.get('description','')[:80]} | {b.get('metric','?')} | "
                         f"{b.get('operator','?')} | {b.get('threshold','?')} | {cv_str} | {dist_str} | "
                         f"{b.get('implied_probability_within_horizon',0):.2f} | {b.get('status','?')} |")
        lines.append("")

    # Re-underwrite triggers
    if triggers:
        lines.append("## 3. Re-Underwrite Triggers")
        lines.append("")
        for t in triggers:
            req = "REQUIRED" if t.get("is_required") else "optional"
            lines.append(f"- **`{t.get('id')}`** ({req}, P={t.get('implied_probability',0):.2f} within {t.get('horizon_months','?')}m): {t.get('description','')}")
        lines.append("")

    # Evidence chain
    if evidence:
        lines.append("## 4. Evidence Chain (3-line convergence per CLAUDE.md §6.4)")
        lines.append("")
        for e in evidence:
            lines.append(f"- **[{e.get('tier','?')}]** [{e.get('line','?')}] {e.get('claim','')} — *{e.get('source_ref') or e.get('source_type','?')}*")
        lines.append("")

    # Prior context (anti-trap-1 marker)
    if prior_ctx and prior_ctx.get("prior_verdict_id"):
        lines.append("## 5. vs Prior Verdict (reverse-flow loaded)")
        lines.append("")
        lines.append(f"Prior verdict: `{prior_ctx['prior_verdict_id']}`")
        wls = prior_ctx.get("watchlist_state_at_load", {})
        if wls:
            lines.append(f"Prior stance: **{wls.get('stance','?')}** · breaks intact {wls.get('breaks_intact_count',0)} / broken {wls.get('breaks_broken_count',0)}")
        if narrative.get("vs_prior_verdict_md"):
            lines.append("")
            lines.append(narrative["vs_prior_verdict_md"])
        lines.append("")

    # Narrative slots
    for slot, header in [("executive_summary_md", "Executive Summary"),
                         ("variant_perception_md", "Variant Perception"),
                         ("long_thesis_md", "Long Thesis (steel-manned)"),
                         ("falsification_case_md", "Falsification Case")]:
        body = narrative.get(slot)
        if body:
            lines.append(f"## {header}")
            lines.append("")
            lines.append(body)
            lines.append("")

    # Friction log
    if friction:
        lines.append("## Execution Friction Log")
        lines.append("")
        for fr in friction:
            lines.append(f"- **[{fr.get('priority','?')}]** {fr.get('item','')} — *{fr.get('verdict_impact','')}*")
        lines.append("")

    # Footer
    lines.append("---")
    lines.append(f"*Rendered from structured verdict by `verdict_to_markdown.py`. Schema: v{sv}. Source-of-truth is the JSON file, not this markdown.*")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description="Render structured PM Verdict to markdown")
    p.add_argument("verdict_path")
    p.add_argument("--output", "-o", help="Output .md path (default: stdout)")
    args = p.parse_args()

    verdict = json.load(open(args.verdict_path))
    md = render(verdict)
    if args.output:
        Path(args.output).write_text(md)
        print(f"wrote {args.output}")
    else:
        print(md)


if __name__ == "__main__":
    main()
