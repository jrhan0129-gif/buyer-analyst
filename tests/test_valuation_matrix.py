"""
Regression tests for skills/valuation_matrix.py

Coverage:
  1. share_scale inferred as exactly 1000 (billions revenue / millions shares)
  2. DCF per-share value ~$177.56 — deterministic, tight tolerance ±0.2
  3. UNIT_INFERENCE_FAILED (ambiguous unit_ratio) → share_scale=0.0, per-share=None
  4. UNIT_INFERENCE_FAILED (multiple P/S candidates via monkeypatch) → same
  5. UNIT INFERENCE SKIPPED (no live_mc) → share_scale=1.0, per-share not None
  6. stderr: FAILED path emits "UNIT_INFERENCE_FAILED"
  7. stderr: SKIPPED path emits "UNIT INFERENCE SKIPPED"
  8. stderr: successful inference emits neither FAILED nor SKIPPED

All tests construct DataReconPacket directly — no yfinance network calls.
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skills"))

import valuation_matrix as vm
from valuation_matrix import (
    ValuationInputs,
    ValuationEngine,
    DataReconPacket,
    BaseMarketData,
    AdvancedQuantData,
    AnthropicStyleDCFEngine,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _adv() -> AdvancedQuantData:
    return AdvancedQuantData(
        ticker=None, roic=None, altman_z_score=None,
        cash_flow_warning=None, source="test", status="skipped",
        notes=[], raw_metrics={},
    )


def _packet(market_cap, current_price) -> DataReconPacket:
    return DataReconPacket(
        base_data_agent=BaseMarketData(
            ticker="TEST",
            current_price=current_price,
            market_cap=market_cap,
            enterprise_value=None,
            shares_outstanding=None,
            currency="USD",
            source="test",
            status="ok",
            notes=[],
        ),
        advanced_quant_agent=_adv(),
        peer_snapshots=[],
        conflict_check={},
    )


def _pdd_inputs(**overrides) -> ValuationInputs:
    """PDD-like inputs: revenue and FCF in billions USD, shares in millions."""
    base = dict(
        revenue=59.97,
        gross_margin=0.563,
        fcf=14.25,
        growth_rate=0.097,
        shares_outstanding=1483.0,
        current_price=102.61,
        discount_rate=0.12,
        terminal_growth_rate=0.03,
        fcf_growth_rate=0.15,
        projection_years=5,
    )
    base.update(overrides)
    return ValuationInputs(**base)


# ---------------------------------------------------------------------------
# Test 1: share_scale inferred as exactly 1000
# ---------------------------------------------------------------------------

def test_share_scale_inferred_correctly():
    """
    Revenue in billions (59.97), shares in millions (1483).
    live_mc=145.67B, live_price=102.61 → implied_abs_shares≈1.42B
    unit_ratio≈957k ≈ 1e6 → shares_unit_scale=1e6
    P/S at 1e9: 2.43x ∈ [0.3, 60] → value_unit_scale=1e9
    share_scale = 1e9 / 1e6 = 1000.0 exactly.
    """
    inputs = _pdd_inputs()
    ValuationEngine(inputs=inputs, data_packet=_packet(145_669_924_332.0, 102.61))
    assert inputs.share_scale == 1000.0


# ---------------------------------------------------------------------------
# Test 2: DCF per-share is ~$177.56 — tight deterministic tolerance
# ---------------------------------------------------------------------------

def test_dcf_per_share_correct_value():
    """
    Fixed DCF inputs → equity_value ≈ 263.32B → 263320M / 1483M shares = $177.56.
    Tolerance ±0.2 catches regressions while accommodating float rounding.
    If this breaks, the share_scale or DCF formula has regressed.
    """
    inputs = _pdd_inputs()
    ValuationEngine(inputs=inputs, data_packet=_packet(145_669_924_332.0, 102.61))

    result = AnthropicStyleDCFEngine(inputs).run()

    assert result is not None
    assert result.implied_share_price is not None, "Per-share suppressed unexpectedly"
    assert abs(result.implied_share_price - 177.56) < 0.2, (
        f"Expected ~$177.56, got ${result.implied_share_price:.4f}"
    )


# ---------------------------------------------------------------------------
# Test 3: FAILED (ambiguous unit_ratio) → share_scale=0.0, per-share=None
# ---------------------------------------------------------------------------

def test_unit_inference_failed_ambiguous_shares():
    """
    live_mc=1_000_000, live_price=10 → implied_abs_shares=100_000.
    shares_input=500 → unit_ratio=200.
    200 is outside all bands (< 10, [5e4,5e7], [5e7,5e10]) → ambiguous → FAILED.
    """
    inputs = _pdd_inputs(shares_outstanding=500.0, current_price=10.0)
    ValuationEngine(inputs=inputs, data_packet=_packet(1_000_000.0, 10.0))

    assert inputs.share_scale == 0.0

    result = AnthropicStyleDCFEngine(inputs).run()
    assert result is not None
    assert result.implied_share_price is None, (
        "Per-share must be suppressed when share_scale=0.0"
    )


# ---------------------------------------------------------------------------
# Test 4: FAILED (multiple P/S candidates via monkeypatch) → same suppression
# ---------------------------------------------------------------------------

def test_unit_inference_failed_multiple_ps_candidates(monkeypatch):
    """
    Production band [0.3, 60] prevents adjacent candidates from simultaneously
    matching (hi=60 < lo=0.3×1000=300 — mathematical guarantee).
    We monkeypatch to [0.001, 60000] to force the defensive multiple-matched
    branch. This is NOT a production scenario; monkeypatch restores after test.

    At wide band: 1e9→P/S=2.43 ✓, 1e6→P/S=2430 ✓ → multiple matched → FAILED.
    """
    monkeypatch.setattr(vm, "_PS_PLAUSIBILITY_LO", 0.001)
    monkeypatch.setattr(vm, "_PS_PLAUSIBILITY_HI", 60_000.0)

    inputs = _pdd_inputs()
    ValuationEngine(inputs=inputs, data_packet=_packet(145_669_924_332.0, 102.61))

    assert inputs.share_scale == 0.0

    result = AnthropicStyleDCFEngine(inputs).run()
    assert result is not None
    assert result.implied_share_price is None, (
        "Per-share must be suppressed on multiple-matched FAILED"
    )


# ---------------------------------------------------------------------------
# Test 5: SKIPPED (no live_mc) → share_scale=1.0, per-share not None
# ---------------------------------------------------------------------------

def test_unit_inference_skipped_no_live_mc():
    """
    SKIPPED ≠ FAILED. When live_mc is None, inference is skipped:
    share_scale remains 1.0 (no suppression). Per-share is still computed.
    """
    inputs = _pdd_inputs()
    ValuationEngine(inputs=inputs, data_packet=_packet(None, 102.61))

    assert inputs.share_scale == 1.0

    result = AnthropicStyleDCFEngine(inputs).run()
    assert result is not None
    assert result.implied_share_price is not None, "SKIPPED must not suppress per-share"


# ---------------------------------------------------------------------------
# Test 6: stderr — FAILED path emits "UNIT_INFERENCE_FAILED"
# ---------------------------------------------------------------------------

def test_stderr_failed_path(capsys):
    """FAILED path must write 'UNIT INFERENCE FAILED' to stderr."""
    inputs = _pdd_inputs(shares_outstanding=500.0, current_price=10.0)
    ValuationEngine(inputs=inputs, data_packet=_packet(1_000_000.0, 10.0))

    err = capsys.readouterr().err
    assert "UNIT INFERENCE FAILED" in err, (
        f"Expected 'UNIT INFERENCE FAILED' in stderr.\nGot: {err}"
    )


# ---------------------------------------------------------------------------
# Test 7: stderr — SKIPPED path emits "UNIT INFERENCE SKIPPED"
# ---------------------------------------------------------------------------

def test_stderr_skipped_path(capsys):
    """SKIPPED path must write 'UNIT INFERENCE SKIPPED' to stderr."""
    inputs = _pdd_inputs()
    ValuationEngine(inputs=inputs, data_packet=_packet(None, 102.61))

    err = capsys.readouterr().err
    assert "UNIT INFERENCE SKIPPED" in err, (
        f"Expected 'UNIT INFERENCE SKIPPED' in stderr.\nGot: {err}"
    )


# ---------------------------------------------------------------------------
# Test 8: stderr — successful inference emits neither FAILED nor SKIPPED
# ---------------------------------------------------------------------------

def test_stderr_clean_on_successful_inference(capsys):
    """
    Successful inference must not pollute stderr with FAILED or SKIPPED.
    Prevents false-positive noise in normal analysis runs.
    """
    inputs = _pdd_inputs()
    ValuationEngine(inputs=inputs, data_packet=_packet(145_669_924_332.0, 102.61))

    err = capsys.readouterr().err
    assert "UNIT INFERENCE FAILED" not in err, "Successful inference must not emit FAILED"
    assert "UNIT INFERENCE SKIPPED" not in err, "Successful inference must not emit SKIPPED"
